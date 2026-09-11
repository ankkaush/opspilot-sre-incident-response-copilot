import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from opspilot.config import get_settings
from opspilot.logging_config import configure_logging
from opspilot.routers import incidents, inspect

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


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject oversized request bodies by their declared Content-Length,
    before FastAPI ever parses them. A cheap, deterministic guard against a
    client sending an absurdly large payload at a small JSON API."""

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > settings.max_request_body_bytes:
                return JSONResponse(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content={"detail": "Request body too large."},
                )
        return await call_next(request)


app.add_middleware(MaxBodySizeMiddleware)

app.include_router(inspect.router)
app.include_router(incidents.router)


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
