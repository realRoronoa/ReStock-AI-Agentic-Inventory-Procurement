"""Settings endpoint.

Read-only. There is deliberately no PUT/PATCH: spend limits come from the server
environment, and a client that could raise its own cap would make the guardrails
decorative.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings as app_settings
from app.schemas.settings import SettingsRead

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get(
    "",
    response_model=SettingsRead,
    summary="Read server configuration",
    description=(
        "The active spend guardrails, which integrations are usable, and the "
        "forecast window.\n\n"
        "**Read-only.** `editable` is always false. These values are set by "
        "environment variables on the server; there is no endpoint to change "
        "them, because the frontend is untrusted and a limit a client can raise "
        "is not a limit.\n\n"
        "Integration state is reported as booleans only — no key, secret, "
        "account number or connection string is ever exposed."
    ),
)
def get_settings() -> SettingsRead:
    # Imported here rather than at module scope to avoid a circular import:
    # main imports the router package, and __version__ lives in main.
    from app.main import __version__

    return SettingsRead.from_config(app_settings, version=__version__)
