"""FastAPI application entry point.

Run locally from the `backend/` directory:

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api import api_router
from app.core.config import settings
from app.core.database import get_db
from app.core.errors import AppError

__version__ = "1.0.0"

logger = logging.getLogger("restock")

#: Correlates a client-visible error with the server-side log line that has the
#: real cause. Returned in the `X-Request-ID` header and in error details.
REQUEST_ID_HEADER = "X-Request-ID"


def configure_logging() -> None:
    """Structured-ish application logging.

    Secrets never reach the logger: `app/core/security.py` redacts headers,
    `app/services/audit_service.py` scrubs metadata, and the payment provider
    logs identifiers and outcomes only — never request bodies or credentials.
    """
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
    )


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    request_id: str | None = None,
    details: dict | None = None,
) -> JSONResponse:
    payload: dict = {"code": code, "message": message}
    if details:
        payload["details"] = details
    if request_id:
        payload["request_id"] = request_id

    headers = {REQUEST_ID_HEADER: request_id} if request_id else None
    return JSONResponse(
        status_code=status_code, content={"error": payload}, headers=headers
    )


def register_exception_handlers(app: FastAPI) -> None:
    """One error envelope for every failure, whatever its origin.

    Four handlers cover the whole surface so no endpoint can return an
    inconsistent shape: domain errors, FastAPI's own HTTPException, request
    validation failures, and anything genuinely unexpected.
    """

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        # Expected business outcomes: log at info/warning, not as an incident.
        logger.info(
            "app_error code=%s status=%s path=%s",
            exc.code,
            exc.status_code,
            request.url.path,
        )
        return _error_response(
            exc.status_code, exc.code, exc.message, details=exc.details or None
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        code = {
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "CONFLICT",
        }.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _error_response(exc.status_code, code, message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Pydantic's error list is genuinely useful to a client and contains no
        # server internals, so it is passed through under `details.fields`.
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "VALIDATION_ERROR",
            "Request validation failed.",
            details={"fields": exc.errors()},
        )

    @app.exception_handler(SQLAlchemyError)
    async def handle_database_error(
        request: Request, exc: SQLAlchemyError
    ) -> JSONResponse:
        request_id = str(uuid.uuid4())
        # The exception text can contain SQL and column values; it is logged,
        # never returned.
        logger.exception(
            "database_error request_id=%s path=%s", request_id, request.url.path
        )
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "DATABASE_ERROR",
            "A database error prevented this operation from completing.",
            request_id=request_id,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request, exc: Exception
    ) -> JSONResponse:
        request_id = str(uuid.uuid4())
        logger.exception(
            "unhandled_error request_id=%s path=%s", request_id, request.url.path
        )
        # No exception message, no stack trace: an unexpected error is exactly
        # the case where the text is most likely to leak internals.
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_ERROR",
            "An unexpected error occurred. Quote the request_id when reporting it.",
            request_id=request_id,
        )


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(
        title=settings.APP_NAME,
        version=__version__,
        summary="Agentic inventory procurement with a human approval gate.",
        description=(
            "## Design contract\n\n"
            "**The LLM reasons and recommends. Deterministic backend code "
            "validates and executes. A human authorises spending.**\n\n"
            "Consequences a client can rely on:\n\n"
            "* Money amounts are computed by the backend from database prices. "
            "No endpoint accepts an amount, a price, or a payment status from a "
            "client.\n"
            "* No order is ever paid without an explicit call to "
            "`POST /api/orders/{id}/approve`. There is no auto-approval path at "
            "any amount.\n"
            "* An order becomes `paid` only when a signature-verified "
            "`payout.processed` webhook arrives — never because a payout "
            "request was accepted.\n"
            "* Inventory increases only as a result of that webhook, exactly "
            "once per order.\n\n"
            "## Money\n\n"
            "All amounts are integer **paise** in fields suffixed `_paise`. "
            "Rupee fields are `Decimal` serialised as JSON strings; never parse "
            "them as floats.\n\n"
            "## Errors\n\n"
            "Every error returns `{\"error\": {\"code\", \"message\", "
            "\"details?\"}}`. Branch on `code`, not on the message text."
        ),
    )

    register_exception_handlers(app)

    # CORS configuration
    origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
    if not origins:
        origins = ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True if origins != ["*"] else False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get(
        "/health",
        tags=["meta"],
        summary="Liveness, database and integration-readiness check",
        description=(
            "Reports whether the database is reachable and which external "
            "integrations are configured. Booleans only — no credential values, "
            "no connection strings."
        ),
    )
    def health(
        response: Response,
        db: Session = Depends(get_db),
    ) -> dict[str, object]:
        # Goes through get_db rather than the module-level engine so the check
        # reflects the session the request handlers would actually get.
        database_ok = True
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            # Log the cause server-side; never leak a DSN or driver detail.
            logger.exception("Health check failed: database unreachable")
            database_ok = False

        if not database_ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        return {
            "status": "ok" if database_ok else "degraded",
            "app": settings.APP_NAME,
            "environment": settings.ENVIRONMENT,
            "version": __version__,
            "database": "up" if database_ok else "down",
            # Booleans, deliberately. Tells an operator whether a real payout or
            # a real forecast is possible without revealing anything secret.
            "integrations": {
                "razorpayx_payouts_configured": settings.razorpayx_configured,
                "razorpayx_webhooks_configured": (
                    settings.webhook_verification_configured
                ),
                "llm_configured": settings.llm_configured,
            },
        }

    app.include_router(api_router)
    return app


app = create_app()
