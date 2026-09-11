import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from opspilot.config import get_settings
from opspilot.logging_config import configure_logging
from opspilot.routers import inspect

settings = get_settings()
configure_logging(settings.log_level)
log = logging.getLogger("opspilot")

app = FastAPI(title="OpsPilot", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)

app.include_router(inspect.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error(
        "unhandled exception",
        extra={"extra_fields": {"path": request.url.path, "error_type": type(exc).__name__}},
        exc_info=exc,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error."},
    )


@app.get("/health")
def health() -> dict:
    """Unauthenticated by design — a liveness probe should not need a secret."""
    return {"status": "ok"}
