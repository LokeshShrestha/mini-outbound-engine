from __future__ import annotations

from pathlib import Path

from app.db import (
    create_draft,
    create_reply,
    has_draft,
    list_leads,
    list_unscored_leads,
    update_lead_score,
)
from engine import (
    DRAFT_SYSTEM,
    SCORE_SYSTEM,
    build_draft_prompt,
    build_score_prompt,
    classify,
    crm_update,
    draft_reply,
    fetch,
    llm,
    parse_draft,
    parse_score,
)

ICP_PATH = Path(__file__).resolve().parent.parent / "icp.md"


class WorkflowError(RuntimeError):
    pass


def verified_context(row: dict) -> str:
    """Return only explicit evidence that is safe to use as an outreach hook."""
    return "\n\n".join(
        f"{label}: {row.get(field, '').strip()}"
        for field, label in (("context", "CONTEXT"), ("job_post", "JOB POST"), ("news", "NEWS"))
        if row.get(field, "").strip()
    )


def score_new_leads() -> int:
    icp = ICP_PATH.read_text(encoding="utf-8")
    processed = 0
    for lead in list_unscored_leads():
        website = lead["website"]
        site_text = fetch(website) if website.startswith(("http://", "https://")) else ""
        prompt = build_score_prompt(icp, lead["name"], website, site_text)
        if not prompt:
            update_lead_score(lead["id"], 0, "No reachable website text", site_text)
            processed += 1
            continue
        try:
            score, reason = parse_score(llm(SCORE_SYSTEM, prompt, max_tokens=1024))
        except SystemExit as error:
            raise WorkflowError(str(error)) from error
        update_lead_score(lead["id"], score, reason, site_text)
        processed += 1
    return processed


def generate_drafts(min_score: int = 60) -> int:
    created = 0
    for lead in list_leads():
        if lead["score"] < min_score or has_draft(lead["id"]):
            continue
        prompt = build_draft_prompt(
            lead["name"], lead["website"], lead["reason"], lead["context"]
        )
        try:
            first_line, email = parse_draft(llm(DRAFT_SYSTEM, prompt, max_tokens=1200))
        except SystemExit as error:
            raise WorkflowError(str(error)) from error
        create_draft(
            {
                "website": lead["website"],
                "first_line": first_line,
                "email": email,
                "status": "needs_approval",
            }
        )
        created += 1
    return created


def process_reply(
    email: str,
    name: str,
    reply_text: str,
    deal_id: str = "",
    dry_run: bool = True,
) -> str:
    label = classify(reply_text)
    response = draft_reply(label, reply_text, name)
    crm = "dry-run: not touched" if dry_run else crm_update(email, name, label, deal_id)
    create_reply(
        {
            "email": email,
            "name": name,
            "reply_text": reply_text,
            "deal_id": deal_id,
            "label": label,
            "response": response,
            "crm": crm,
            "status": "needs_approval",
        }
    )
    return label
