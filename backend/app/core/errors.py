"""Uniform error envelope: {"error": {"code", "message", "requestId"}} and exception handlers."""
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_context_value

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}


def error_response(status: int, code: str, message: str, details: Any = None, headers: dict[str, str] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message, "requestId": get_context_value("request_id")}
    if details is not None:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        return error_response(422, "VALIDATION_ERROR", "Request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return error_response(exc.status_code, code, str(exc.detail))

    async def _db_down(_: Request, exc: Exception) -> JSONResponse:
        logger.error("database unavailable", extra={"error": type(exc).__name__})
        return error_response(
            503, "DATABASE_UNAVAILABLE", "Storage is temporarily unavailable; retry shortly", headers={"Retry-After": "5"}
        )

    for exc_type in (OperationalError, InterfaceError, SATimeoutError, ConnectionError):
        app.add_exception_handler(exc_type, _db_down)

    @app.exception_handler(DBAPIError)
    async def _dbapi(_: Request, exc: DBAPIError) -> JSONResponse:
        logger.error("database error", extra={"error": type(exc.orig).__name__})
        return error_response(500, "INTERNAL_ERROR", "An internal error occurred")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception")  # details stay in logs, never in the response
        return error_response(500, "INTERNAL_ERROR", "An internal error occurred")
