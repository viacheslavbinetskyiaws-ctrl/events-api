import fastapi_swagger_dark as fsd
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.routers import admin, analytics, events, health
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.domain.exceptions import DomainError, EventNotFoundError, TenantAccountNotFoundError

settings = get_settings()
configure_logging(settings.log_level)

# docs_url=None hands the /docs route over to fastapi-swagger-dark instead
# of FastAPI's default (light-only) Swagger UI.
app = FastAPI(title=settings.app_name, docs_url=None)

docs_router = APIRouter()
fsd.install(docs_router)
app.include_router(docs_router)

app.include_router(health.router)
app.include_router(events.router)
app.include_router(analytics.router)
app.include_router(admin.router)

Instrumentator().instrument(app).expose(app)


@app.exception_handler(EventNotFoundError)
def handle_event_not_found(request: Request, exc: EventNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "event_not_found", "message": str(exc)}},
    )


@app.exception_handler(TenantAccountNotFoundError)
def handle_tenant_account_not_found(
    request: Request, exc: TenantAccountNotFoundError
) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "tenant_account_not_found", "message": str(exc)}},
    )


@app.exception_handler(DomainError)
def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": {"code": "domain_error", "message": str(exc)}},
    )
