"""Real Bitext eval tickets — 10 rows, one per SaaS-mappable Bitext intent.

Source: bitext/Bitext-customer-support-llm-chatbot-training-dataset (Hugging
Face). The 10 rows were selected by `eval/select_bitext.py` and frozen into
`data/bitext_eval_10.csv` so the eval is reproducible and offline-runnable.

HONEST SCOPE NOTES (read before trusting these numbers):
- Bitext is an e-commerce / order-support dataset. This project is a SaaS
  support agent. Only Bitext's SaaS-adjacent intents are used here — this is
  "10 of Bitext's SaaS-adjacent intents," NOT "a SaaS support benchmark."
- `expected_intent` and `expected_outcome` are hand-authored judgement calls
  (the Bitext->project mapping). Bitext itself does not label auto-send vs
  escalate. A couple of rows are deliberately borderline (e.g. create_account
  "I have a problem creating an account").
- Unlike `eval/dataset.py`, these tickets carry NO canned classification/draft.
  Bitext rows are unseen messages — hand-authoring canned LLM output for them
  would not be credible. They run in live-LLM mode ONLY (`--no-llm` is rejected
  for this dataset).
"""

from __future__ import annotations

import csv
from pathlib import Path

from eval.dataset import EvalTicket

_REPO_ROOT = Path(__file__).parent.parent
_DATA_DIR = _REPO_ROOT / "data"

# Project intent -> Slack channel the escalation router is expected to pick
# (3-channel build per adviserplan.md; billing/enterprise/legal route to the
# #support-technical catch-all).
_CHANNEL_BY_INTENT: dict[str, str] = {
    "refund": "#support-refunds",
    "billing": "#support-technical",
    "complaint": "#support-complaints",
    "other": "#support-technical",
}


def _subject_for(bitext_intent: str) -> str:
    """Humanize a Bitext intent slug into an email subject line."""
    return bitext_intent.replace("_", " ").capitalize()


def _load_bitext_tickets(csv_name: str, id_prefix: str) -> list[EvalTicket]:
    """Read a Bitext eval CSV -> EvalTicket objects (live-LLM only)."""
    csv_path = _DATA_DIR / csv_name
    if not csv_path.exists():
        raise FileNotFoundError(
            f"{csv_path} missing — run `python -m eval.select_bitext` first."
        )

    tickets: list[EvalTicket] = []
    with open(csv_path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    for i, row in enumerate(rows, start=1):
        ticket_id = f"{id_prefix}-t{i:02d}"
        email = f"{ticket_id}@example.com"
        outcome = row["expected_outcome"]
        proj_intent = row["project_intent"]
        channel = "" if outcome == "auto_send" else _CHANNEL_BY_INTENT.get(proj_intent, "")
        customer_message = (
            f"From: {email}\n"
            f"Subject: {_subject_for(row['bitext_intent'])}\n\n"
            f"{row['instruction']}"
        )
        tickets.append(
            EvalTicket(
                ticket_id=ticket_id,
                description=(
                    f"Bitext:{row['bitext_intent']} -> {proj_intent} "
                    f"({row['mapping_rationale']})"
                ),
                customer_message=customer_message,
                customer_email=email,
                expected_intent=proj_intent,
                expected_outcome=outcome,
                expected_channel=channel,
                expected_risk_flags=[],  # not asserted for live Bitext rows
                canned_classification={},  # live-LLM only — no canned output
                canned_draft={},
            )
        )
    return tickets


# 10 SaaS-mappable intents (focused first batch) and all 27 Bitext intents
# (breadth set — includes out-of-domain e-commerce intents; see select_bitext.py).
BITEXT_TICKETS: list[EvalTicket] = _load_bitext_tickets("bitext_eval_10.csv", "bitext")
BITEXT_TICKETS_27: list[EvalTicket] = _load_bitext_tickets("bitext_eval_27.csv", "bitext27")
BITEXT_TICKETS_BY_ID: dict[str, EvalTicket] = {t.ticket_id: t for t in BITEXT_TICKETS}

__all__ = ["BITEXT_TICKETS", "BITEXT_TICKETS_27", "BITEXT_TICKETS_BY_ID"]
