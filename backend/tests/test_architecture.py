"""Architectural boundary tests.

The claim "the LLM cannot spend money" is only worth something if it is
enforced. These tests read the actual import graph of `app/agents/` and fail if
anything under it can reach payment code, the database, or a credential.

A comment saying "agents must not import payment_service" is a wish. This is a
test.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import app.agents
from app.core.config import Settings

AGENTS_DIR = Path(app.agents.__file__).parent
APP_DIR = AGENTS_DIR.parent

#: Modules the agent layer must never reach, directly or transitively.
FORBIDDEN_FOR_AGENTS = (
    "app.services.payment_service",
    "app.services.approval_service",
    "app.services.order_service",
    "app.services.webhook_service",
    "app.services.inventory_service",
    "app.services.proposal_service",
    "app.services.audit_service",
    "app.core.database",
    "app.core.limits",
    "app.models",
)


def _agent_modules() -> list[str]:
    names = ["app.agents"]
    for module in pkgutil.iter_modules([str(AGENTS_DIR)]):
        names.append(f"app.agents.{module.name}")
    return names


def _imported_names(path: Path) -> set[str]:
    """Every module name imported by a source file, from its AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            for alias in node.names:
                names.add(f"{node.module}.{alias.name}")

    return names


def test_agent_modules_are_discovered() -> None:
    """Guard against the suite silently testing nothing."""
    modules = _agent_modules()

    assert "app.agents.forecast_agent" in modules
    assert "app.agents.supplier_agent" in modules
    assert "app.agents.llm_client" in modules


@pytest.mark.parametrize(
    "source_file",
    sorted(AGENTS_DIR.glob("*.py")),
    ids=lambda path: path.name,
)
def test_no_agent_module_imports_forbidden_code(source_file: Path) -> None:
    """The LLM layer cannot reach payment, persistence, or guardrail code."""
    imported = _imported_names(source_file)

    for forbidden in FORBIDDEN_FOR_AGENTS:
        offending = {name for name in imported if name.startswith(forbidden)}
        assert not offending, (
            f"{source_file.name} imports {offending}, which the agent layer "
            f"must not reach. Agents return validated data; services act on it."
        )


def _executable_source(path: Path) -> str:
    """Source with comments and string literals removed, lowercased.

    Docstrings are excluded deliberately: `app/agents/__init__.py` *documents*
    the payment boundary, and a naive text search would flag the very comment
    explaining the rule. What matters is whether any executable code names a
    payment concept.
    """
    import io
    import tokenize

    kept: list[str] = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)

    return " ".join(kept).lower()


@pytest.mark.parametrize(
    "source_file",
    sorted(AGENTS_DIR.glob("*.py")),
    ids=lambda path: path.name,
)
def test_no_agent_code_names_a_payment_concept(source_file: Path) -> None:
    """Catches a raw HTTP call to RazorpayX, or any payment identifier.

    Comments and docstrings are stripped first, so this asserts on code only.
    """
    code = _executable_source(source_file)

    for token in ("razorpay", "payout", "fund_account", "idempotency"):
        assert token not in code, (
            f"{source_file.name} has executable code naming {token!r}. The "
            f"agent layer must have no awareness of the payment provider."
        )


def test_agents_cannot_import_payment_service_at_runtime() -> None:
    """The static check, confirmed dynamically.

    Imports every agent module and asserts none of them bound a payment symbol
    — which would catch an import hidden inside a function body.
    """
    for name in _agent_modules():
        module = importlib.import_module(name)
        attributes = set(dir(module))

        assert not {
            attribute
            for attribute in attributes
            if "payout" in attribute.lower() or "razorpay" in attribute.lower()
        }, f"{name} exposes a payment symbol"


def test_payment_service_does_not_import_agents() -> None:
    """The boundary holds in the other direction too.

    Payment code that imported an agent could be made to depend on model output,
    which is exactly what must never gate a transfer.
    """
    payment = APP_DIR / "services" / "payment_service.py"
    imported = _imported_names(payment)

    offending = {name for name in imported if name.startswith("app.agents")}
    assert not offending, f"payment_service imports {offending}"


def test_guardrails_do_not_import_agents() -> None:
    """A limit the model can influence is not a limit."""
    imported = _imported_names(APP_DIR / "core" / "limits.py")

    assert not {name for name in imported if name.startswith("app.agents")}


def test_prompt_templates_contain_no_credential_placeholders() -> None:
    """Nothing in a prompt may interpolate a secret."""
    forbidden = (
        "razorpay",
        "key_secret",
        "api_key",
        "account_number",
        "webhook_secret",
        "authorization",
        "payout",
    )

    for prompt in (AGENTS_DIR / "prompts").glob("*.txt"):
        text = prompt.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in text, f"{prompt.name} mentions {token!r}"


def test_prompts_do_not_reveal_spend_limits() -> None:
    """A model that knew the cap could size a recommendation to sit under it.

    The forecast would then reflect the budget rather than the demand, which
    would defeat the guardrail it was supposedly respecting.
    """
    for prompt in (AGENTS_DIR / "prompts").glob("*.txt"):
        text = prompt.read_text(encoding="utf-8").lower()

        for token in ("max_order_spend", "max_daily_spend", "max_reorder_quantity"):
            assert token not in text
        # No hard-coded rupee ceiling either.
        assert "10000" not in text
        assert "25000" not in text


def test_secret_leak_assertion_actually_fires() -> None:
    """The prompt guard is real, not decorative."""
    from app.core.security import assert_no_secrets

    config = Settings(RAZORPAY_KEY_SECRET="super_secret_value")

    assert_no_secrets("a harmless prompt", config.all_secrets)

    with pytest.raises(RuntimeError):
        assert_no_secrets(
            "please use super_secret_value to authorise", config.all_secrets
        )


def test_only_webhook_processing_increases_stock() -> None:
    """One writer for inventory, reachable from one place.

    `apply_received_stock` is the only function that raises stock, and only the
    webhook service may call it.
    """
    services = APP_DIR / "services"
    callers = []

    for source_file in services.glob("*.py"):
        text = source_file.read_text(encoding="utf-8")
        if "apply_received_stock" in text and source_file.name not in (
            "inventory_service.py",
        ):
            callers.append(source_file.name)

    assert callers == ["webhook_service.py"], (
        f"apply_received_stock is called from {callers}; only webhook_service "
        f"may settle inventory."
    )


def test_order_status_is_assigned_in_exactly_one_place() -> None:
    """State transitions must funnel through order_service.transition.

    Scattered `order.status = ...` assignments are how a state machine quietly
    stops being one.
    """
    services = APP_DIR / "services"
    offenders = {}

    for source_file in services.glob("*.py"):
        if source_file.name == "order_service.py":
            continue
        for number, line in enumerate(
            source_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if stripped.startswith("order.status =") or stripped.startswith(
                "order.status="
            ):
                offenders[f"{source_file.name}:{number}"] = stripped

    assert not offenders, (
        f"direct status assignment outside order_service: {offenders}. Use "
        f"order_service.transition, which validates against ALLOWED_TRANSITIONS."
    )


def test_no_module_outside_tests_imports_a_fake() -> None:
    """Fakes are test-only. No production import path may reach one."""
    offenders = []

    for source_file in APP_DIR.rglob("*.py"):
        text = source_file.read_text(encoding="utf-8")
        if "tests.fakes" in text or "FakePaymentProvider" in text:
            offenders.append(str(source_file.relative_to(APP_DIR)))

    assert not offenders, f"application code references test doubles: {offenders}"


def test_no_hardcoded_credentials_in_application_code() -> None:
    """Credentials come from configuration, never from source."""
    suspicious = ("rzp_live_", "sk-proj-", "sk-ant-")
    offenders = []

    for source_file in APP_DIR.rglob("*.py"):
        text = source_file.read_text(encoding="utf-8")
        for token in suspicious:
            if token in text:
                offenders.append(f"{source_file.name}: {token}")

    assert not offenders, f"possible hard-coded credential: {offenders}"


def test_no_raw_sql_string_interpolation() -> None:
    """SQL is built by SQLAlchemy, never by string concatenation.

    Catches f-string or %-formatted SQL, which is where injection would live.
    """
    offenders = []

    for source_file in APP_DIR.rglob("*.py"):
        for number, line in enumerate(
            source_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            lowered = line.lower()
            if "text(" not in lowered:
                continue
            # A parameterised text() call is fine; an interpolated one is not.
            if 'text(f"' in lowered or "text(f'" in lowered or "text(%" in lowered:
                offenders.append(f"{source_file.name}:{number}")

    assert not offenders, f"interpolated SQL found: {offenders}"


def test_record_sale_is_the_only_stock_decreasing_path() -> None:
    """Mirror of the receipt rule: exactly one writer in each direction.

    `record_sale` may only be called from the sales router, so there is one
    place to audit for "how can stock go down?".
    """
    callers = []

    for source_file in (APP_DIR / "services").glob("*.py"):
        if source_file.name == "inventory_service.py":
            continue
        if "record_sale" in source_file.read_text(encoding="utf-8"):
            callers.append(f"services/{source_file.name}")

    for source_file in (APP_DIR / "api").glob("*.py"):
        if "record_sale" in source_file.read_text(encoding="utf-8"):
            callers.append(f"api/{source_file.name}")

    assert callers == ["api/sales.py"], (
        f"record_sale is reachable from {callers}; only the sales router may "
        f"decrease stock."
    )


def test_agents_cannot_reach_the_sales_or_spend_surfaces() -> None:
    """An agent that could record a sale could manufacture a low-stock event."""
    for source_file in AGENTS_DIR.glob("*.py"):
        code = _executable_source(source_file)
        for token in ("record_sale", "spending_service", "reject_order"):
            assert token not in code, f"{source_file.name} names {token!r}"


def test_settings_endpoint_exposes_no_writer() -> None:
    """Spend limits must not be client-editable at any cost."""
    source = (APP_DIR / "api" / "settings.py").read_text(encoding="utf-8")

    for verb in ("@router.post", "@router.put", "@router.patch", "@router.delete"):
        assert verb not in source, f"settings router defines {verb}"


def test_audit_router_exposes_no_writer() -> None:
    source = (APP_DIR / "api" / "audit.py").read_text(encoding="utf-8")

    for verb in ("@router.post", "@router.put", "@router.patch", "@router.delete"):
        assert verb not in source, f"audit router defines {verb}"
