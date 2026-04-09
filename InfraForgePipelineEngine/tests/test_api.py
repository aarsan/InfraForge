"""Test API endpoints — integration tests via TestClient."""

import json
import pytest
from httpx import ASGITransport, AsyncClient

from pipeline_engine.app import create_app
from pipeline_engine.steps.noop_step import NoopStepHandler
from pipeline_engine.steps.gate_step import GateStepHandler
from tests.conftest import OutputHandler


@pytest.fixture
def app():
    application = create_app()
    # Ensure built-in types are registered even without pip install -e
    if not application.state.registry.get("noop"):
        application.state.registry.register("noop", NoopStepHandler())
    if not application.state.registry.get("output"):
        application.state.registry.register("output", OutputHandler())
    if not application.state.registry.get("gate"):
        application.state.registry.register("gate", GateStepHandler())
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "registered_steps" in data


class TestCatalogEndpoint:
    @pytest.mark.asyncio
    async def test_list_steps(self, client):
        resp = await client.get("/api/catalog/steps")
        assert resp.status_code == 200
        data = resp.json()
        assert "steps" in data
        assert isinstance(data["steps"], list)


class TestPipelineRunEndpoint:
    @pytest.mark.asyncio
    async def test_run_simple_pipeline(self, client):
        payload = {
            "name": "test",
            "context": {"greeting": "hello"},
            "options": {"timeout": 30},
            "stages": [
                {
                    "id": "s1",
                    "steps": [
                        {"id": "step1", "type": "noop", "config": {"message": "hi"}}
                    ],
                }
            ],
        }
        resp = await client.post(
            "/api/pipelines/run",
            json=payload,
            headers={"Accept": "application/x-ndjson"},
        )
        assert resp.status_code == 200
        assert "application/x-ndjson" in resp.headers["content-type"]

        lines = resp.text.strip().split("\n")
        events = [json.loads(line) for line in lines if line.strip()]

        types = [e["type"] for e in events]
        assert "pipeline_start" in types
        assert "pipeline_done" in types

        done_event = next(e for e in events if e["type"] == "pipeline_done")
        assert done_event["status"] == "success"

    @pytest.mark.asyncio
    async def test_run_unknown_step_type_returns_422(self, client):
        payload = {
            "name": "test",
            "stages": [
                {
                    "id": "s1",
                    "steps": [{"id": "x", "type": "nonexistent_type", "config": {}}],
                }
            ],
        }
        resp = await client.post("/api/pipelines/run", json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_run_invalid_body_returns_422(self, client):
        resp = await client.post("/api/pipelines/run", json={"name": "x"})
        assert resp.status_code == 422


class TestRunStatusEndpoint:
    @pytest.mark.asyncio
    async def test_unknown_run_returns_404(self, client):
        resp = await client.get("/api/pipelines/nonexistent")
        assert resp.status_code == 404


class TestGatePauseResume:
    """Test the full pause → resume flow for gate steps."""

    @pytest.mark.asyncio
    async def test_pipeline_pauses_at_gate(self, client):
        """Pipeline with a gate step should pause and return step_waiting + pipeline_paused events."""
        payload = {
            "name": "gate-test",
            "context": {"data": "before-gate"},
            "options": {"timeout": 30},
            "stages": [
                {
                    "id": "pre",
                    "steps": [{"id": "setup", "type": "noop", "config": {"message": "setup"}}],
                },
                {
                    "id": "review",
                    "steps": [{
                        "id": "approval",
                        "type": "gate",
                        "config": {
                            "gate_type": "approval",
                            "assignee": "team-lead",
                            "instructions": "Please approve this change",
                            "required_inputs": [
                                {"name": "verdict", "type": "enum", "options": ["approved", "rejected"], "required": True},
                                {"name": "comments", "type": "text", "required": False},
                            ],
                        },
                        "inputs": {"data": "ctx.data"},
                        "outputs": {"verdict": "ctx.verdict", "comments": "ctx.comments"},
                    }],
                },
                {
                    "id": "post",
                    "steps": [{"id": "finish", "type": "noop", "config": {"message": "done"}}],
                },
            ],
        }
        resp = await client.post(
            "/api/pipelines/run",
            json=payload,
            headers={"Accept": "application/x-ndjson"},
        )
        assert resp.status_code == 200

        lines = resp.text.strip().split("\n")
        events = [json.loads(line) for line in lines if line.strip()]
        types = [e["type"] for e in events]

        # Should have step_waiting and pipeline_paused but NOT pipeline_done
        assert "step_waiting" in types
        assert "pipeline_paused" in types
        assert "pipeline_done" not in types

        # Extract run_id for resume
        paused = next(e for e in events if e["type"] == "pipeline_paused")
        run_id = paused["run_id"]
        assert paused["waiting_step_id"] == "approval"

        # Check run status is paused
        status_resp = await client.get(f"/api/pipelines/{run_id}")
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "paused"

        return run_id  # For use by resume test

    @pytest.mark.asyncio
    async def test_full_pause_resume_flow(self, client):
        """Full flow: run → pause at gate → resume with outputs → complete."""
        payload = {
            "name": "pause-resume-test",
            "context": {"value": "original"},
            "options": {"timeout": 30},
            "stages": [
                {
                    "id": "s1",
                    "steps": [
                        {
                            "id": "before",
                            "type": "noop",
                            "config": {"message": "before gate", "outputs": {"pre_result": "done"}},
                            "outputs": {"pre_result": "ctx.pre_result"},
                        },
                    ],
                },
                {
                    "id": "s2",
                    "steps": [{
                        "id": "the_gate",
                        "type": "gate",
                        "config": {
                            "gate_type": "approval",
                            "assignee": "tester",
                            "instructions": "Approve this",
                            "required_inputs": [
                                {"name": "approved", "type": "boolean", "required": True},
                            ],
                        },
                        "inputs": {"pre_result": "ctx.pre_result"},
                        "outputs": {"approved": "ctx.approved"},
                    }],
                },
                {
                    "id": "s3",
                    "steps": [{
                        "id": "after",
                        "type": "noop",
                        "config": {"message": "after gate"},
                        "inputs": {"approved": "ctx.approved"},
                    }],
                },
            ],
        }

        # Step 1: Run — should pause at the gate
        resp = await client.post(
            "/api/pipelines/run",
            json=payload,
            headers={"Accept": "application/x-ndjson"},
        )
        assert resp.status_code == 200
        events = [json.loads(l) for l in resp.text.strip().split("\n") if l.strip()]
        types = [e["type"] for e in events]

        assert "pipeline_paused" in types
        paused = next(e for e in events if e["type"] == "pipeline_paused")
        run_id = paused["run_id"]

        # Step 2: Resume with human-provided outputs
        resume_resp = await client.post(
            f"/api/pipelines/{run_id}/steps/the_gate/complete",
            json={"outputs": {"approved": True}},
            headers={"Accept": "application/x-ndjson"},
        )
        assert resume_resp.status_code == 200

        resume_events = [json.loads(l) for l in resume_resp.text.strip().split("\n") if l.strip()]
        resume_types = [e["type"] for e in resume_events]

        # Should see pipeline_resumed, then remaining steps, then pipeline_done
        assert "pipeline_resumed" in resume_types
        assert "pipeline_done" in resume_types

        done = next(e for e in resume_events if e["type"] == "pipeline_done")
        assert done["status"] == "success"

    @pytest.mark.asyncio
    async def test_resume_wrong_step_returns_409(self, client):
        """Resuming with wrong step_id should return 409."""
        # First, create a paused pipeline
        payload = {
            "name": "wrong-step-test",
            "stages": [{
                "id": "s1",
                "steps": [{
                    "id": "gate1",
                    "type": "gate",
                    "config": {
                        "gate_type": "approval",
                        "required_inputs": [{"name": "ok", "type": "boolean", "required": True}],
                    },
                    "outputs": {"ok": "ctx.ok"},
                }],
            }],
        }
        resp = await client.post("/api/pipelines/run", json=payload)
        events = [json.loads(l) for l in resp.text.strip().split("\n") if l.strip()]
        paused = next(e for e in events if e["type"] == "pipeline_paused")
        run_id = paused["run_id"]

        # Try to resume with wrong step ID
        resume_resp = await client.post(
            f"/api/pipelines/{run_id}/steps/WRONG_ID/complete",
            json={"outputs": {"ok": True}},
        )
        assert resume_resp.status_code == 409

    @pytest.mark.asyncio
    async def test_list_runs(self, client):
        """GET /api/pipelines should list runs."""
        resp = await client.get("/api/pipelines")
        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
