"""One-time selector — pick 10 real Bitext rows, one per SaaS-mappable intent.

Bitext is an e-commerce/order support dataset (27 intents). This project is a
SaaS support agent. We pick the 10 intents that map cleanly to SaaS support and
take one real customer message per intent.

Output: data/bitext_eval_10.csv — the real eval data file. Reproducible:
selection is deterministic (first clean row, in dataset order).

Run once:  python -m eval.select_bitext
"""

from __future__ import annotations

import csv
from pathlib import Path

from datasets import load_dataset

# Bitext intent -> (project intent label, expected outcome, one-line rationale).
# These mappings are hand-authored judgement calls — disclosed in the README.
SELECTED: dict[str, tuple[str, str, str]] = {
    "recover_password": ("FAQ", "auto_send", "password reset — safe self-service FAQ"),
    "newsletter_subscription": ("FAQ", "auto_send", "newsletter signup — safe FAQ"),
    "check_payment_methods": ("FAQ", "auto_send", "which payment methods — informational FAQ"),
    "create_account": ("basic_technical", "auto_send", "how to create an account — basic how-to"),
    "get_refund": ("refund", "escalated", "refund request — money, Gate 1 escalates"),
    "check_refund_policy": ("refund", "escalated", "refund-policy question — refund mention escalates"),
    "track_refund": ("refund", "escalated", "refund status — refund mention escalates"),
    "payment_issue": ("billing", "escalated", "payment problem — billing dispute escalates"),
    "complaint": ("complaint", "escalated", "complaint — angry/edge-case escalates"),
    "contact_human_agent": ("other", "escalated", "explicit human request — escalate by design"),
}

_REPO_ROOT = Path(__file__).parent.parent
_OUT = _REPO_ROOT / "data" / "bitext_eval_10.csv"


def _is_clean(text: str) -> bool:
    """A usable row: no template placeholders, reasonable length."""
    return "{{" not in text and "}}" not in text and 20 <= len(text) <= 220


def main() -> None:
    ds = load_dataset(
        "bitext/Bitext-customer-support-llm-chatbot-training-dataset", split="train"
    )
    picked: dict[str, dict[str, str]] = {}
    for row in ds:
        intent = row["intent"]
        if intent in SELECTED and intent not in picked and _is_clean(row["instruction"]):
            picked[intent] = {
                "bitext_intent": intent,
                "category": row["category"],
                "instruction": row["instruction"].strip(),
            }
        if len(picked) == len(SELECTED):
            break

    missing = set(SELECTED) - set(picked)
    if missing:
        raise SystemExit(f"No clean row found for intents: {sorted(missing)}")

    with open(_OUT, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["bitext_intent", "category", "instruction",
             "project_intent", "expected_outcome", "mapping_rationale"]
        )
        for intent in SELECTED:  # stable order
            rec = picked[intent]
            proj, outcome, rationale = SELECTED[intent]
            writer.writerow(
                [rec["bitext_intent"], rec["category"], rec["instruction"],
                 proj, outcome, rationale]
            )
    print(f"Wrote {len(picked)} rows -> {_OUT}")
    for intent in SELECTED:
        print(f"  {intent:26} | {picked[intent]['instruction']}")


if __name__ == "__main__":
    main()
