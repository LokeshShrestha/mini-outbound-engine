# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "beautifulsoup4"]
# ///
"""Shared plumbing: scrape, call the LLM, parse scores, classify replies, update HubSpot.

Logic lives here so it self-checks without an API key:  uv run engine.py
"""

from __future__ import annotations

import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
HUBSPOT_URL = "https://api.hubspot.com/crm/v3/objects"
MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
MAX_SITE_TEXT = 6000
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (compatible; mini-outbound-engine/1.0)"}

LABELS = ("interested", "not_now", "wrong_person", "unsubscribe", "out_of_office")

# Default HubSpot free-pipeline stage ids. Edit to match your portal.
DEAL_STAGE = {
    "interested": "qualifiedtobuy",
    "not_now": "appointmentscheduled",
    "wrong_person": "appointmentscheduled",
    "unsubscribe": "closedlost",
    "out_of_office": "appointmentscheduled",
}
LIFECYCLE = {"interested": "opportunity", "unsubscribe": "other"}

SCORE_SYSTEM = """You are a BDR qualifying leads against an ICP. Score 0-100 for ICP fit.
Be harsh: 80+ only for an obvious, urgent fit; 40-70 is a maybe; under 40 is a pass.
Do not narrate your reasoning. Reply with EXACTLY two lines and nothing else:

SCORE: <0-100>
REASON: <one line, max 15 words, grounded only in the company text given>

If the company text is empty or useless, output SCORE: 0 and a reason saying why.
Never invent a fact, a metric, a news item, or a person."""

DRAFT_SYSTEM = """You write one short cold email. Hard rules:
- 60-100 words, plain text, no subject line, no placeholders like [Name] or {Company}.
- The FIRST LINE must be the sentence in the HOOK block. If the block is marked
  NO_HOOK, use that sentence verbatim and do not add any other personal claim.
- Only claim things literally present in CONTEXT. Invent nothing: no metrics,
  no news, no "I saw you are hiring", no mutual connections.
- One soft ask at the end: open to a 15-minute call?
- If a sender signature is supplied, put it on the last line. If not, end after the ask."""

CLASSIFY_SYSTEM = """Classify this inbound email reply. Reply with ONE label only, nothing else:
interested | not_now | wrong_person | unsubscribe | out_of_office
- interested: wants to talk, asks for pricing/call/details
- not_now: polite no, budget/timing/other priority
- wrong_person: wrong human, asks to be redirected to someone
- unsubscribe: wants off the list (this wins over everything)
- out_of_office: automatic or "away" reply"""

REPLY_SYSTEM = """Draft a reply to an inbound email. 40-70 words, plain text, no subject line,
no placeholders. Be direct and human. Do not invent facts, dates, prices or names
that are not in the message you were given. Do not promise anything specific."""

TEMPLATES = {
    "unsubscribe": (
        "Understood - you are off the list and I will not email you again. "
        "No reply needed."
    ),
    "out_of_office": (
        "Thanks for the auto-reply - I will pick this up when you are back. "
        "Nothing needed from you now."
    ),
}

_UNSUB = re.compile(
    r"\bunsubscribe|opt[\s-]?out|stop (sending|emailing|contacting|writing)|"
    r"remove me|take me off|do not (contact|email) me",
    re.I,
)
_OOO = re.compile(
    r"\bout of (the )?office|away from (my|the) (desk|office)|on leave|on vacation|"
    r"on holiday|auto-?matic(ally)? (reply|response)|I am currently (out|unavailable)",
    re.I,
)


def fetch(url: str) -> str:
    """Return readable page text, or "" on any failure. Never raises."""
    url = (url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = requests.get(url, headers=BROWSER_UA, timeout=15, allow_redirects=True)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! fetch failed {url}: {e}", file=sys.stderr)
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())[:MAX_SITE_TEXT]


def llm(system: str, user: str, max_tokens: int = 700) -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY is not set.  set OPENROUTER_API_KEY=sk-or-v1-...")
    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    h = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
    r = None
    for attempt in range(3):
        r = requests.post(OPENROUTER_URL, json=body, headers=h, timeout=90)
        if r.status_code in (429, 500, 502, 503) and attempt < 2:
            time.sleep(3 * (attempt + 1))  # free-tier rate limits
            continue
        break
    if r.status_code != 200:
        raise RuntimeError(f"openrouter {r.status_code}: {r.text[:300]}")
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


def parse_score(text: str) -> tuple[int, str]:
    m = re.search(r"SCORE:\s*(\d{1,3})", text, re.I)
    r = re.search(r"REASON:\s*(.+)", text, re.I)
    score = min(int(m.group(1)), 100) if m else 0
    reason = r.group(1).strip().strip("*#").strip() if r else ""
    if not reason:
        reason = re.sub(r"\s+", " ", text).strip()[:140] or "no usable response"
    return score, reason


def build_score_prompt(icp: str, name: str, url: str, text: str) -> str:
    if not text:
        return ""  # caller skips the API call entirely
    return f"ICP:\n{icp}\n\nCOMPANY: {name}\nWEBSITE: {url}\n\nCOMPANY TEXT:\n{text}"


def build_draft_prompt(name: str, url: str, reason: str, context: str) -> str:
    """Context that proves a hook -> model writes the first line.
    No context -> NO_HOOK fallback, first line used verbatim, nothing invented."""
    if context.strip():
        hook = (
            f"HOOK: write the first line using only these verified facts, "
            f"and nothing else:\n{context.strip()[:3000]}"
        )
    else:
        hook = (
            "HOOK (NO_HOOK fallback - use this sentence verbatim as the first line, "
            "add nothing personal to it):\n"
            f"Full disclosure: I do not have a specific trigger for reaching out "
            f"beyond our own read - {reason.strip()}."
        )
    return (
        f"COMPANY: {name}\nWEBSITE: {url}\nSCORE REASON: {reason}\n\n{hook}\n\n"
        "Write the email now as two lines:\n"
        "FIRST LINE: <the opening sentence>\n"
        "BODY: <the rest of the email>"
    )


def parse_draft(text: str) -> tuple[str, str]:
    body = re.split(r"^\s*BODY:\s*", text, flags=re.M | re.I)
    first = re.split(r"^\s*FIRST LINE:\s*", body[0], flags=re.M | re.I)[-1]
    rest = body[1] if len(body) > 1 else ""
    return first.strip(), (rest or body[0]).strip()


def classify(text: str, ask=llm) -> str:
    """Deterministic fast paths first, model only for the ambiguous middle."""
    if _UNSUB.search(text or ""):
        return "unsubscribe"
    if _OOO.search(text or ""):
        return "out_of_office"
    out = (ask(CLASSIFY_SYSTEM, f"REPLY:\n{(text or '')[:3000]}\n\nLABEL:") or "").lower()
    for label in LABELS:
        if label in out or label.replace("_", " ") in out:
            return label
    return "not_now"  # unknown -> hold, never drop


def draft_reply(label: str, text: str, name: str, ask=llm) -> str:
    if label in TEMPLATES:
        return TEMPLATES[label]
    return ask(REPLY_SYSTEM, f"THEIR REPLY:\n{(text or '')[:2000]}\nCONTACT: {name}")


def crm_update(email: str, name: str, label: str, deal_id: str = "") -> str:
    """Upsert the contact by email; move the deal stage when we have a deal id."""
    key = os.environ.get("HUBSPOT_API_KEY")
    if not key:
        return "skipped: no HUBSPOT_API_KEY"
    h = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
    props = {"lifecyclestage": LIFECYCLE.get(label, "lead")}
    if name.strip():
        props["firstname"] = name.strip()
    up = requests.post(
        f"{HUBSPOT_URL}/contacts/batch/upsert",
        json={"inputs": [{"id": email, "idProperty": "email", "properties": props}]},
        headers=h,
        timeout=30,
    )
    if not up.ok:
        return f"contact upsert {up.status_code}: {up.text[:200]}"
    done = f"contact {email} -> {props['lifecyclestage']}"
    if deal_id.strip():
        d = requests.patch(
            f"{HUBSPOT_URL}/deals/{deal_id.strip()}",
            json={"properties": {"dealstage": DEAL_STAGE[label]}},
            headers=h,
            timeout=30,
        )
        done += f"; deal {deal_id} -> {DEAL_STAGE[label]}" if d.ok else \
            f"; deal {d.status_code}: {d.text[:200]}"
    return done


def demo() -> None:
    assert parse_score("SCORE: 87\nREASON: hiring first engineer") == (
        87,
        "hiring first engineer",
    )
    assert parse_score("SCORE: 999\nREASON: x")[0] == 100
    assert parse_score("no structure at all")[0] == 0
    assert build_score_prompt("icp", "a", "b", "") == ""

    grounded = build_draft_prompt("Acme", "acme.com", "runs paid ads", "raised Series A")
    assert "NO_HOOK" not in grounded and "raised Series A" in grounded
    bare = build_draft_prompt("Acme", "acme.com", "runs paid ads", "   ")
    assert "NO_HOOK" in bare and "runs paid ads" in bare

    first, body = parse_draft("FIRST LINE: hello there\nBODY: rest of it")
    assert first == "hello there" and body == "rest of it"

    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("model called"))
    assert classify("please unsubscribe me now", ask=boom) == "unsubscribe"
    assert classify("I am out of office until Monday", ask=boom) == "out_of_office"
    assert classify("Interested - send pricing", ask=lambda *a, **k: "INTERESTED") == "interested"
    assert classify("something unclear", ask=lambda *a, **k: "gibberish") == "not_now"
    assert draft_reply("unsubscribe", "x", "Sam", ask=boom).isupper() is False
    assert all(l in DEAL_STAGE for l in LABELS)

    print(f"engine demo ok  (model={MODEL})")


if __name__ == "__main__":
    demo()
