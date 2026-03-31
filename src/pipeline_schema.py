"""
InfraForge Pipeline Definition Schema — ``infraforge.pipeline.v1``

Canonical, machine-readable format for defining multi-step pipelines.
AI agents produce these definitions; the ``PipelineRunner`` executes them;
the frontend renders them as Logic-Apps-style flowcharts.

Three core models
=================

PipelineStep
    A single action within a stage (e.g. "generate ARM template").

PipelineStage
    A named group of sequential steps (e.g. "Generate", "Validate").
    Stages appear as collapsible sections in the UI.

PipelineDefinition
    The top-level envelope: metadata, trigger, and an ordered list of
    stages.  Stored as JSON in the ``pipeline_definitions`` DB table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


SCHEMA_VERSION = "infraforge.pipeline.v1"


# ══════════════════════════════════════════════════════════════
# STEP
# ══════════════════════════════════════════════════════════════

class PipelineStep(BaseModel):
    """A single executable step inside a stage."""

    id: str = Field(description="Unique step identifier (e.g. 'generate_arm')")
    name: str = Field(description="Human-readable step name")
    action: str = Field(
        description=(
            "Handler action key — maps to a registered PipelineRunner handler. "
            "Example: 'generate_arm', 'validate_arm_deploy', 'promote_service'."
        )
    )
    icon: str = Field(default="▸", description="Emoji icon for UI display")
    description: str = Field(default="", description="What this step does")
    on_success: str = Field(
        default="next",
        description="Routing on success: 'next' | 'skip_to:<step_id>' | 'end'",
    )
    on_failure: str = Field(
        default="abort",
        description=(
            "Routing on failure: 'abort' | 'heal_and_retry' | "
            "'retry_with_llm' | 'skip' | 'action_required'"
        ),
    )
    healable: bool = Field(
        default=False,
        description="Whether LLM-based healing should be attempted on failure",
    )
    max_heal_attempts: int = Field(
        default=5,
        description="Maximum number of heal-and-retry cycles",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Step-specific configuration (passed to the handler)",
    )


# ══════════════════════════════════════════════════════════════
# STAGE
# ══════════════════════════════════════════════════════════════

class PipelineStage(BaseModel):
    """A named group of sequential steps.

    Stages are the primary visual grouping unit in the pipeline UI.
    Each stage appears as a collapsible section header with its steps
    rendered as action cards beneath it.
    """

    id: str = Field(description="Unique stage identifier (e.g. 'generate')")
    name: str = Field(description="Display name (e.g. 'Generate Infrastructure')")
    icon: str = Field(default="📦", description="Emoji icon for the stage header")
    color: str = Field(
        default="blue",
        description=(
            "Accent color for the stage header: "
            "blue | purple | amber | teal | green | red"
        ),
    )
    steps: list[PipelineStep] = Field(
        default_factory=list,
        description="Ordered list of steps within this stage",
    )


# ══════════════════════════════════════════════════════════════
# TRIGGER
# ══════════════════════════════════════════════════════════════

class PipelineTrigger(BaseModel):
    """How the pipeline is started."""

    type: Literal["manual", "event", "schedule"] = Field(
        default="manual",
        description="Trigger type",
    )
    event: Optional[str] = Field(
        default=None,
        description="Event name that triggers execution (when type='event')",
    )
    schedule: Optional[str] = Field(
        default=None,
        description="Cron expression (when type='schedule')",
    )


# ══════════════════════════════════════════════════════════════
# METADATA
# ══════════════════════════════════════════════════════════════

class PipelineMetadata(BaseModel):
    """Provenance information about who/what created the definition."""

    author: str = Field(default="infraforge", description="Creator identifier")
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO datetime when the definition was created",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Searchable tags (e.g. ['onboarding', 'arm', 'governance'])",
    )


# ══════════════════════════════════════════════════════════════
# PIPELINE DEFINITION (top-level)
# ══════════════════════════════════════════════════════════════

class PipelineDefinition(BaseModel):
    """Complete pipeline definition — the canonical wire format.

    This is the JSON that gets stored in ``pipeline_definitions.definition_json``,
    returned from REST endpoints, and rendered by the frontend blueprint viewer.
    """

    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Schema version string",
    )
    id: str = Field(description="Unique pipeline identifier (e.g. 'service_onboarding')")
    name: str = Field(description="Human-readable pipeline name")
    version: str = Field(default="1.0.0", description="Semantic version of this definition")
    icon: str = Field(default="🚀", description="Pipeline icon for UI display")
    description: str = Field(default="", description="What this pipeline does")
    trigger: PipelineTrigger = Field(
        default_factory=PipelineTrigger,
        description="How this pipeline is triggered",
    )
    stages: list[PipelineStage] = Field(
        default_factory=list,
        description="Ordered list of stages",
    )
    metadata: PipelineMetadata = Field(
        default_factory=PipelineMetadata,
        description="Provenance and tagging",
    )

    # ── Helpers ───────────────────────────────────────────────

    def to_step_sequence(self) -> list[tuple[PipelineStage, PipelineStep]]:
        """Flatten stages into an ordered list of (stage, step) tuples.

        Used by ``PipelineRunner.execute_definition()`` to walk the
        pipeline in execution order.
        """
        result: list[tuple[PipelineStage, PipelineStep]] = []
        for stage in self.stages:
            for step in stage.steps:
                result.append((stage, step))
        return result

    def total_steps(self) -> int:
        """Total number of steps across all stages."""
        return sum(len(stage.steps) for stage in self.stages)

    def stage_ids(self) -> list[str]:
        """Return ordered list of stage IDs."""
        return [s.id for s in self.stages]

    def get_stage(self, stage_id: str) -> PipelineStage | None:
        """Look up a stage by ID."""
        for s in self.stages:
            if s.id == stage_id:
                return s
        return None

    def to_preview(self) -> dict:
        """Return a frontend-friendly preview of the pipeline structure.

        Used by the ``/api/pipelines/definitions/{id}/preview`` endpoint
        and the ``_renderPipelineBlueprint()`` JS function.
        """
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "icon": self.icon,
            "description": self.description,
            "total_steps": self.total_steps(),
            "stages": [
                {
                    "id": stage.id,
                    "name": stage.name,
                    "icon": stage.icon,
                    "color": stage.color,
                    "step_count": len(stage.steps),
                    "steps": [
                        {
                            "id": step.id,
                            "name": step.name,
                            "icon": step.icon,
                            "action": step.action,
                            "healable": step.healable,
                        }
                        for step in stage.steps
                    ],
                }
                for stage in self.stages
            ],
            "trigger": self.trigger.model_dump(),
            "metadata": self.metadata.model_dump(),
        }


# ══════════════════════════════════════════════════════════════
# BUILT-IN PIPELINE DEFINITIONS
# ══════════════════════════════════════════════════════════════

def _builtin_service_onboarding() -> PipelineDefinition:
    """Built-in: Service Onboarding Pipeline (12 steps across 6 stages)."""
    return PipelineDefinition(
        id="service_onboarding",
        name="Service Onboarding Pipeline",
        version="1.0.0",
        icon="🚀",
        description=(
            "End-to-end service onboarding: generates ARM templates via "
            "Copilot SDK, validates against governance policies, deploys "
            "to Azure for verification, runs infrastructure tests, and "
            "promotes the service to approved status."
        ),
        trigger=PipelineTrigger(type="manual"),
        stages=[
            PipelineStage(
                id="prepare",
                name="Prepare",
                icon="⚙️",
                color="blue",
                steps=[
                    PipelineStep(
                        id="initialize",
                        name="Initialize Pipeline",
                        action="initialize",
                        icon="⚙️",
                        description="Set up model routing, clean stale drafts",
                    ),
                    PipelineStep(
                        id="check_dependency_gates",
                        name="Dependency Validation Gate",
                        action="check_dependency_gates",
                        icon="🔗",
                        description="Validate required dependencies are fully onboarded",
                    ),
                    PipelineStep(
                        id="analyze_standards",
                        name="Analyze Standards",
                        action="analyze_standards",
                        icon="📋",
                        description="Fetch and analyze organization governance standards",
                    ),
                ],
            ),
            PipelineStage(
                id="generate",
                name="Generate",
                icon="⚡",
                color="purple",
                steps=[
                    PipelineStep(
                        id="plan_architecture",
                        name="Plan Architecture",
                        action="plan_architecture",
                        icon="🧠",
                        description="LLM planning call for ARM template structure",
                    ),
                    PipelineStep(
                        id="generate_arm",
                        name="Generate ARM Template",
                        action="generate_arm",
                        icon="⚡",
                        description="Generate ARM template via Copilot SDK",
                    ),
                    PipelineStep(
                        id="generate_policy",
                        name="Generate Azure Policy",
                        action="generate_policy",
                        icon="🛡️",
                        description="Generate governance policy for the ARM template",
                    ),
                ],
            ),
            PipelineStage(
                id="govern",
                name="Governance",
                icon="🏛️",
                color="amber",
                steps=[
                    PipelineStep(
                        id="governance_review",
                        name="Governance Review",
                        action="governance_review",
                        icon="🏛️",
                        description="CISO + CTO structured review gate",
                    ),
                ],
            ),
            PipelineStage(
                id="validate",
                name="Validate & Deploy",
                icon="🔍",
                color="teal",
                steps=[
                    PipelineStep(
                        id="validate_arm_deploy",
                        name="Validate ARM Deployment",
                        action="validate_arm_deploy",
                        icon="🚀",
                        description="Static policy checks → What-If → Deploy → Resource verification",
                        on_failure="heal_and_retry",
                        healable=True,
                        max_heal_attempts=5,
                    ),
                    PipelineStep(
                        id="infra_testing",
                        name="Infrastructure Testing",
                        action="infra_testing",
                        icon="🧪",
                        description="AI-generated infrastructure smoke tests",
                    ),
                    PipelineStep(
                        id="deploy_policy",
                        name="Deploy Azure Policy",
                        action="deploy_policy",
                        icon="📜",
                        description="Deploy generated policy to Azure",
                    ),
                ],
            ),
            PipelineStage(
                id="finalize",
                name="Finalize",
                icon="🏆",
                color="green",
                steps=[
                    PipelineStep(
                        id="cleanup",
                        name="Cleanup",
                        action="cleanup",
                        icon="🧹",
                        description="Delete temporary resource group and policy",
                    ),
                    PipelineStep(
                        id="promote_service",
                        name="Promote Service",
                        action="promote_service",
                        icon="🏆",
                        description="Mark service as approved, set active version",
                    ),
                ],
            ),
        ],
        metadata=PipelineMetadata(
            author="infraforge",
            tags=["onboarding", "arm", "governance", "deployment", "testing"],
        ),
    )


def _builtin_template_validation() -> PipelineDefinition:
    """Built-in: Template Validation Pipeline (10 steps across 4 stages)."""
    return PipelineDefinition(
        id="template_validation",
        name="Template Validation Pipeline",
        version="1.0.0",
        icon="🧪",
        description=(
            "Validates a composed infrastructure template: structural tests, "
            "ARM deployment to a temporary resource group with self-healing, "
            "compliance scanning, and version promotion."
        ),
        trigger=PipelineTrigger(type="manual"),
        stages=[
            PipelineStage(
                id="prepare",
                name="Prepare",
                icon="⚙️",
                color="blue",
                steps=[
                    PipelineStep(
                        id="init",
                        name="Initialize",
                        action="initialize",
                        icon="⚙️",
                        description="Set up model routing and pipeline context",
                    ),
                    PipelineStep(
                        id="structural_tests",
                        name="Structural Tests",
                        action="structural_tests",
                        icon="🧪",
                        description="Run JSON schema and dependency checks",
                    ),
                ],
            ),
            PipelineStage(
                id="deploy",
                name="Deploy & Heal",
                icon="🚀",
                color="teal",
                steps=[
                    PipelineStep(
                        id="sanitize",
                        name="Sanitize Template",
                        action="sanitize_template",
                        icon="🔧",
                        description="Parameter defaults, DNS names, placeholder GUIDs",
                    ),
                    PipelineStep(
                        id="what_if",
                        name="ARM What-If",
                        action="what_if",
                        icon="🔍",
                        description="Preview deployment changes",
                    ),
                    PipelineStep(
                        id="deploy",
                        name="Deploy to Azure",
                        action="deploy_to_azure",
                        icon="🚀",
                        description="Deploy ARM template to temporary resource group",
                        on_failure="heal_and_retry",
                        healable=True,
                        max_heal_attempts=5,
                    ),
                ],
            ),
            PipelineStage(
                id="verify",
                name="Verify",
                icon="🛡️",
                color="amber",
                steps=[
                    PipelineStep(
                        id="compliance_scan",
                        name="Compliance Scan",
                        action="compliance_scan",
                        icon="🛡️",
                        description="Scan deployed resources against org standards",
                    ),
                    PipelineStep(
                        id="cleanup",
                        name="Cleanup",
                        action="cleanup",
                        icon="🧹",
                        description="Delete temporary resource group",
                    ),
                ],
            ),
            PipelineStage(
                id="publish",
                name="Publish",
                icon="🏆",
                color="green",
                steps=[
                    PipelineStep(
                        id="promote",
                        name="Promote Version",
                        action="promote_version",
                        icon="🏆",
                        description="Mark template version as approved",
                    ),
                ],
            ),
        ],
        metadata=PipelineMetadata(
            author="infraforge",
            tags=["template", "validation", "deployment", "compliance"],
        ),
    )


def _builtin_api_version_update() -> PipelineDefinition:
    """Built-in: API Version Update Pipeline."""
    return PipelineDefinition(
        id="api_version_update",
        name="API Version Update Pipeline",
        version="1.0.0",
        icon="⬆",
        description=(
            "Updates a service's ARM template to a newer Azure API version: "
            "checks out the current template, AI-rewrites it, validates via "
            "governance and deployment, then promotes the new version."
        ),
        trigger=PipelineTrigger(type="manual"),
        stages=[
            PipelineStage(
                id="prepare",
                name="Prepare",
                icon="⚙️",
                color="blue",
                steps=[
                    PipelineStep(
                        id="init",
                        name="Initialize",
                        action="initialize",
                        icon="⚙️",
                        description="Model routing and pipeline context setup",
                    ),
                    PipelineStep(
                        id="checkout",
                        name="Checkout Template",
                        action="checkout_template",
                        icon="📥",
                        description="Load the current active ARM template version",
                    ),
                ],
            ),
            PipelineStage(
                id="rewrite",
                name="Rewrite",
                icon="⚡",
                color="purple",
                steps=[
                    PipelineStep(
                        id="plan",
                        name="Plan Changes",
                        action="plan_api_update",
                        icon="🧠",
                        description="AI analysis of API version differences",
                    ),
                    PipelineStep(
                        id="rewrite",
                        name="Rewrite Template",
                        action="rewrite_template",
                        icon="⚡",
                        description="AI rewrites template for new API version",
                    ),
                ],
            ),
            PipelineStage(
                id="govern",
                name="Governance",
                icon="🏛️",
                color="amber",
                steps=[
                    PipelineStep(
                        id="governance",
                        name="Governance Review",
                        action="governance_review",
                        icon="🏛️",
                        description="CISO + CTO review of updated template",
                    ),
                ],
            ),
            PipelineStage(
                id="validate",
                name="Validate",
                icon="🔍",
                color="teal",
                steps=[
                    PipelineStep(
                        id="validate_deploy",
                        name="Validate Deployment",
                        action="validate_arm_deploy",
                        icon="🚀",
                        description="Policy checks → What-If → Deploy → Verify",
                        on_failure="heal_and_retry",
                        healable=True,
                        max_heal_attempts=5,
                    ),
                    PipelineStep(
                        id="compliance",
                        name="Compliance Test",
                        action="compliance_test",
                        icon="🛡️",
                        description="Runtime policy compliance verification",
                    ),
                    PipelineStep(
                        id="cleanup",
                        name="Cleanup",
                        action="cleanup",
                        icon="🧹",
                        description="Delete temporary resources",
                    ),
                ],
            ),
            PipelineStage(
                id="finalize",
                name="Finalize",
                icon="🏆",
                color="green",
                steps=[
                    PipelineStep(
                        id="promote",
                        name="Publish Version",
                        action="promote_service",
                        icon="🏆",
                        description="Promote updated version to active",
                    ),
                ],
            ),
        ],
        metadata=PipelineMetadata(
            author="infraforge",
            tags=["api-version", "update", "arm", "governance"],
        ),
    )


def _builtin_deployment() -> PipelineDefinition:
    """Built-in: Deployment Pipeline (3-step)."""
    return PipelineDefinition(
        id="deployment",
        name="Deployment Pipeline",
        version="1.0.0",
        icon="🚀",
        description=(
            "Deploy an approved ARM template to a target Azure resource group: "
            "sanitize parameters, run What-If preview, deploy."
        ),
        trigger=PipelineTrigger(type="manual"),
        stages=[
            PipelineStage(
                id="deploy",
                name="Deploy",
                icon="🚀",
                color="teal",
                steps=[
                    PipelineStep(
                        id="sanitize",
                        name="Sanitize Template",
                        action="sanitize_template",
                        icon="🔧",
                        description="Ensure parameter defaults and clean placeholders",
                    ),
                    PipelineStep(
                        id="what_if",
                        name="What-If Preview",
                        action="what_if",
                        icon="🔍",
                        description="Preview deployment changes before applying",
                    ),
                    PipelineStep(
                        id="deploy",
                        name="Deploy",
                        action="deploy_to_azure",
                        icon="🚀",
                        description="Execute ARM deployment to target resource group",
                        on_failure="heal_and_retry",
                        healable=True,
                        max_heal_attempts=3,
                    ),
                ],
            ),
        ],
        metadata=PipelineMetadata(
            author="infraforge",
            tags=["deployment", "arm"],
        ),
    )


def get_builtin_definitions() -> list[PipelineDefinition]:
    """Return all built-in pipeline definitions."""
    return [
        _builtin_service_onboarding(),
        _builtin_template_validation(),
        _builtin_api_version_update(),
        _builtin_deployment(),
    ]
