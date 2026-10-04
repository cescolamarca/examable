"""Domain errors raised by services and translated to HTTP responses in `app.main`."""

from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    status_code = 400

    def __init__(self, detail: Any) -> None:
        super().__init__(detail if isinstance(detail, str) else repr(detail))
        self.detail = detail


class InvalidRequestError(ServiceError):
    status_code = 400


class NotFoundError(ServiceError):
    status_code = 404


class ConflictError(ServiceError):
    status_code = 409


class PayloadTooLargeError(ServiceError):
    status_code = 413


class UpstreamError(ServiceError):
    status_code = 502


class UnprocessableDocumentError(ServiceError):
    status_code = 422
