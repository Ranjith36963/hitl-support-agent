"""MCP READ Server — capability-isolated to data retrieval only.

Implements spec.md §8 "MCP READ Server":
  - get_crm_profile(customer_email) → Salesforce-shape CRM record
  - get_customer_history(customer_email) → past 90-day interactions
  - get_kb_article(query) → ACME policy chunks with verbatim sentence quotes

Connected to: Enrich Context node and Revalidate Context node ONLY.
Cannot send email. Cannot post to Slack.

Data sources:
  - data/customers_seed.json  (mock CRM — Track C creates this)
  - data/acme_policies.md     (ACME policy corpus — Track C creates this)

If either file is absent, returns well-shaped stub data so the server runs
cleanly. Stubs are logged with a WARNING so Track C integration is obvious.

Run standalone (stdio):
    python mcp_server/support_read.py
"""

from __future__ import annotations

import json
import logging
import pathlib
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, stream=sys.stderr)

# ---------------------------------------------------------------------------
# Paths (Windows-friendly, relative to repo root)
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).parent.parent
_CUSTOMERS_PATH = _REPO_ROOT / "data" / "customers_seed.json"
_POLICIES_PATH = _REPO_ROOT / "data" / "acme_policies.md"

# ---------------------------------------------------------------------------
# Lazy-loaded data — loaded once on first access
# ---------------------------------------------------------------------------
_customers: dict[str, Any] | None = None
_policies_text: str | None = None


def _email_key(record: dict[str, Any]) -> str:
    """Extract email from record, supporting both 'email' and 'customer_email' keys."""
    return (record.get("customer_email") or record.get("email") or "").lower()


def _load_customers() -> dict[str, Any]:
    """Load customers_seed.json keyed by email (lowercase). Returns stubs if absent.

    Supports both field-name conventions:
      - "customer_email"  (Track C canonical schema)
      - "email"           (fallback / older shape)
    """
    global _customers
    if _customers is not None:
        return _customers

    if _CUSTOMERS_PATH.exists():
        with open(_CUSTOMERS_PATH, encoding="utf-8") as fh:
            raw: list[dict[str, Any]] = json.load(fh)
        _customers = {_email_key(r): r for r in raw if _email_key(r)}
        logger.info("Loaded %d customer records from %s", len(_customers), _CUSTOMERS_PATH)
    else:
        logger.warning(
            "customers_seed.json not found at %s — returning stub data (Track C pending)",
            _CUSTOMERS_PATH,
        )
        _customers = {}
    return _customers


def _load_policies() -> str:
    """Load acme_policies.md text. Returns stub text if absent."""
    global _policies_text
    if _policies_text is not None:
        return _policies_text

    if _POLICIES_PATH.exists():
        _policies_text = _POLICIES_PATH.read_text(encoding="utf-8")
        logger.info("Loaded ACME policy corpus from %s", _POLICIES_PATH)
    else:
        logger.warning(
            "acme_policies.md not found at %s — returning stub policy (Track C pending)",
            _POLICIES_PATH,
        )
        _policies_text = (
            "# ACME SaaS Co Policies (STUB)\n\n"
            "## Section 4.2.1 — Refund Policy\n"
            "Refunds above $100 require manager approval per Policy 4.2.1.\n\n"
            "## Section 7.1 — Cancellation Policy\n"
            "Cancellation requests must be submitted 30 days before renewal.\n\n"
            "## Section 2.3 — Billing Disputes\n"
            "Billing disputes must be raised within 60 days of the charge.\n"
        )
    return _policies_text


# ---------------------------------------------------------------------------
# Stub record shape (mirrors Salesforce-shape from spec §6 Enrich Context)
# Field names match customers_seed.json (Track C canonical schema).
# ---------------------------------------------------------------------------
_STUB_PROFILE: dict[str, Any] = {
    "customer_email": "unknown@example.com",
    "customer_tier": "Free",
    "contract_value": 0,
    "renewal_date": None,
    "billing_status": "current",
    "account_status": "Active",
    "open_tickets": 0,
    "is_stub": True,  # flag for caller to detect stub
}

_STUB_HISTORY: list[dict[str, Any]] = [
    {
        "ticket_id": "STUB-001",
        "date": "2025-01-01",
        "intent": "FAQ",
        "summary": "Stub history entry.",
        "resolution": "resolved",
        "sentiment": "neutral",
        "is_stub": True,
    }
]


def _normalize_profile(record: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized CRM profile dict with canonical key names.

    Translates the raw customers_seed.json schema to what the MCP client
    Pydantic models (CRMProfile) expect. Adds an 'email' alias for
    back-compat with any callers using that key.
    """
    email = record.get("customer_email") or record.get("email") or ""
    return {
        "email": email,
        "customer_email": email,
        "customer_tier": record.get("customer_tier", "Free"),
        "contract_value_usd": float(record.get("contract_value") or record.get("contract_value_usd") or 0),
        "renewal_date": record.get("renewal_date") or "",
        "billing_status": record.get("billing_status", "current"),
        "account_status": record.get("account_status", "Active"),
        "open_tickets": record.get("open_tickets", 0),
        "is_stub": record.get("is_stub", False),
    }


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "support-read",
    instructions=(
        "READ-ONLY server. Retrieves CRM profiles, customer history, and ACME KB articles. "
        "Has no ability to send email or post to Slack."
    ),
)


@mcp.tool()
def get_crm_profile(customer_email: str) -> dict[str, Any]:
    """Return Salesforce-shape CRM profile for *customer_email*.

    Returns: dict with keys:
      email, customer_tier (Free|SMB|Enterprise), contract_value_usd,
      renewal_date, billing_status, account_status, open_tickets.
    Falls back to stub record when customers_seed.json is absent or
    the email is not found.
    """
    customers = _load_customers()
    record = customers.get(customer_email.lower())
    if record is None:
        logger.warning("CRM: no profile for %s — returning stub", customer_email)
        stub = dict(_STUB_PROFILE)
        stub["customer_email"] = customer_email
        stub["email"] = customer_email
        return _normalize_profile(stub)
    return _normalize_profile(record)


@mcp.tool()
def get_customer_history(customer_email: str) -> list[dict[str, Any]]:
    """Return past-90-day ticket/interaction history for *customer_email*.

    Returns: list of dicts with keys:
      ticket_id, date, intent, summary, resolution, sentiment.
    Returns stub list when data file is absent or email not found.

    Note: customers_seed.json uses 'ticket_history' as the key.
    """
    customers = _load_customers()
    record = customers.get(customer_email.lower())
    # Support both "ticket_history" (Track C canonical) and "history" (fallback)
    history = None
    if record is not None:
        history = record.get("ticket_history") or record.get("history")
    if history is None:
        logger.warning("History: no data for %s — returning stub", customer_email)
        stubs = [dict(h) for h in _STUB_HISTORY]
        for h in stubs:
            h["customer_email"] = customer_email
        return stubs
    return list(history)


def search_kb(query: str) -> dict[str, Any]:
    """Keyword-overlap search of the ACME policy corpus for *query*.

    Plain function (no MCP wrapper) so non-MCP callers — notably the eval
    harness — can run the EXACT production KB retrieval logic instead of
    injecting canned policy matches. `get_kb_article` is the MCP tool that
    wraps this.

    Returns: dict with keys matched_sections / verbatim_quote /
    policy_references.
    """
    text = _load_policies()
    query_lower = query.lower()

    matched_sections: list[str] = []
    verbatim_quote: str = ""
    policy_references: list[str] = []

    current_heading = ""
    best_score = 0

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current_heading = stripped[3:]
        elif stripped.startswith("# "):
            current_heading = stripped[2:]
        elif stripped:
            # Score by keyword overlap
            words = set(query_lower.split())
            line_lower = stripped.lower()
            score = sum(1 for w in words if w in line_lower)
            if score > best_score:
                best_score = score
                verbatim_quote = stripped
                if current_heading and current_heading not in matched_sections:
                    matched_sections.append(current_heading)

    # Extract policy IDs from headings like "Section 4.2.1"
    import re
    for heading in matched_sections:
        refs = re.findall(r"\d+\.\d+(?:\.\d+)*", heading)
        for ref in refs:
            tag = f"ACME {ref}"
            if tag not in policy_references:
                policy_references.append(tag)

    if not verbatim_quote:
        verbatim_quote = "No directly matching policy found."

    return {
        "matched_sections": matched_sections,
        "verbatim_quote": verbatim_quote,
        "policy_references": policy_references,
    }


@mcp.tool()
def get_kb_article(query: str) -> dict[str, Any]:
    """Search ACME policy corpus for chunks relevant to *query*.

    Returns: dict with keys:
      matched_sections (list of section headings),
      verbatim_quote (the most relevant full sentence from the corpus),
      policy_references (list of policy section IDs, e.g. ["ACME 4.2.1"]).
    Used by Enrich Context to populate policy_matches in AgentState and
    to supply the KB justification quote rendered in Slack approval messages.
    """
    return search_kb(query)


if __name__ == "__main__":
    mcp.run(transport="stdio")
