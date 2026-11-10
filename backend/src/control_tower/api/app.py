"""Application factory."""

from fastapi import APIRouter, FastAPI

from control_tower import __version__
from control_tower.api.errors import install_error_handlers
from control_tower.api.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from control_tower.api.routes import auth, executions, gateway, health, registry
from control_tower.core.operations import OperationCatalog
from control_tower.logging import configure_logging
from control_tower.platforms.registry import AdapterRegistry, build_adapters, build_catalog
from control_tower.security.throttle import LoginThrottle
from control_tower.settings import Settings, get_settings

API_PREFIX = "/api/v1"


def create_app(
    settings: Settings | None = None,
    catalog: OperationCatalog | None = None,
    adapters: AdapterRegistry | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="Agent Control Tower",
        version=__version__,
        description=(
            "Policy gateway, audit trail and management API for AI agents acting on "
            "enterprise systems."
        ),
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.catalog = catalog or build_catalog()
    app.state.adapters = adapters or build_adapters()
    app.state.login_throttle = LoginThrottle()

    install_error_handlers(app)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(health.router)
    api.include_router(auth.router)
    api.include_router(registry.router)
    api.include_router(executions.router)
    api.include_router(gateway.router)
    app.include_router(api)

    # Outermost first: correlation id and access log wrap everything, including 413s.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(RequestContextMiddleware)
    return app
