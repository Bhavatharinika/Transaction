"""Structured JSON logging with request / transaction / worker correlation via contextvars."""
import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def bind_context(**values: Any) -> None:
    """Attach fields (request_id, transaction_id, worker_id) to every subsequent log line in this task."""
    _context.set({**_context.get(), **{k: v for k, v in values.items() if v is not None}})


def get_context_value(key: str) -> Any:
    return _context.get().get(key)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_context.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
