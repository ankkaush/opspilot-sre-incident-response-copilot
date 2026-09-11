import json
import logging
import sys
import time

_REDACT_KEYS = {"api_key", "authorization", "x-api-key", "password", "secret", "token"}


class JsonFormatter(logging.Formatter):
    """Minimal structured JSON log formatter.

    Deliberately dependency-free — this is a small enough need that pulling in
    python-json-logger would be one more thing to justify.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.get("extra_fields", {}).items():
            if key.lower() in _REDACT_KEYS:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "info") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)

    # Quiet the noisy third-party loggers down to warnings unless we're debugging.
    quiet_level = logging.WARNING if level.upper() != "DEBUG" else logging.INFO
    for noisy in ("uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(quiet_level)
