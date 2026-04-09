from fastapi import FastAPI

from pipeline_engine import __version__
from pipeline_engine.engine.run_store import RunStore
from pipeline_engine.steps.registry import StepRegistry


def create_app() -> FastAPI:
    app = FastAPI(
        title="Pipeline Engine",
        version=__version__,
        description="Standalone pipeline execution engine with plugin-based step registry",
    )

    registry = StepRegistry()
    registry.discover()
    app.state.registry = registry

    run_store = RunStore()
    app.state.run_store = run_store

    from pipeline_engine.routers.pipelines import router as pipelines_router
    from pipeline_engine.routers.catalog import router as catalog_router

    app.include_router(pipelines_router, prefix="/api")
    app.include_router(catalog_router, prefix="/api")

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "version": __version__,
            "registered_steps": list(registry.list_types()),
        }

    return app
