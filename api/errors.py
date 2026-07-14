"""Consistent HTTP error semantics for every FastAPI route."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


logger = logging.getLogger(__name__)


@dataclass
class ApiError(Exception):
    status_code: int
    code: str
    message: str
    retryable: bool = False
    details: Any = None

    def __post_init__(self):
        super().__init__(self.message)


def error_response(error: ApiError) -> JSONResponse:
    payload: dict[str, Any] = {
        "result": "error",
        "error": {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
        },
        # Temporary compatibility for callers that still read response.detail.
        "detail": error.message,
    }
    if error.details is not None:
        payload["error"]["details"] = error.details
    return JSONResponse(payload, status_code=error.status_code)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        if exc.status_code >= 500:
            logger.error(
                "API error %s on %s %s",
                exc.code,
                request.method,
                request.url.path,
                exc_info=exc.__cause__ or exc,
            )
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        return error_response(ApiError(
            status_code=422,
            code="request_validation_error",
            message="请求参数格式不正确",
            details=exc.errors(),
        ))

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException):
        message = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return error_response(ApiError(
            status_code=exc.status_code,
            code=f"http_{exc.status_code}",
            message=message,
            retryable=exc.status_code >= 500,
            details=None if isinstance(exc.detail, str) else exc.detail,
        ))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        logger.exception(
            "Unhandled API error on %s %s",
            request.method,
            request.url.path,
            exc_info=exc,
        )
        return error_response(ApiError(
            status_code=500,
            code="internal_error",
            message="系统异常，请稍后重试",
            retryable=True,
        ))
