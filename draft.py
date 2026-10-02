# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "beautifulsoup4"]
# ///
"""2. Personalize: draft a first line + short email for each scored lead -> drafts.csv.

Nothing here sends. Every row lands as status=needs_approval for a human.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

from engine import DRAFT_SYSTEM, build_draft_prompt, llm, parse_draft

ROOT = Path(__file__).parent
FIELDS = ["rank", "name", "website", "contact_email", "score", "first_line", "email", "status"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="leads.csv")
    ap.add_argument("--min-score", type=int, default=60)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.2)
    args = ap.parse_args()

    sig = os.environ.get("SENDER_NAME", "").strip()
    out_path = ROOT / "drafts.csv"
    with (ROOT / args.input).open(newline="", encoding="utf-8-sig") as f:
        leads = [r for r in csv.DictReader(f) if (r.get("name") or "").strip()]
    already = set()
    if out_path.exists():
        with out_path.open(newline="", encoding="utf-8-sig") as f:
            already = {r["website"] for r in csv.DictReader(f)}

    rows, new = [], 0
    for lead in leads:
        if int(lead.get("score") or 0) < args.min_score or lead["website"] in already:
            continue
        print(f"  {lead['name']} ({lead['score']})", end=" ... ", flush=True)
        prompt = build_draft_prompt(
            lead["name"], lead["website"], lead.get("reason", ""), lead.get("context", "")
        )
        first, body = parse_draft(llm(DRAFT_SYSTEM, prompt, max_tokens=1200))
        if sig:
            body = f"{body}\n\n--\n{sig}"
        rows.append(
            {
                "rank": lead.get("rank", ""),
                "name": lead["name"],
                "website": lead["website"],
                "contact_email": lead.get("contact_email", ""),
                "score": lead.get("score", ""),
                "first_line": first,
                "email": body,
                "status": "needs_approval",
            }
        )
        print("ok")
        new += 1
        if args.limit and new >= args.limit:
            break
        time.sleep(args.delay)

    if not rows:
        print("nothing new to draft (check --min-score / leads.csv)")
        return
    exists = out_path.exists()
    with out_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        w.writerows(rows)
    print(f"\n{new} drafts -> {out_path.name}, all status=needs_approval")


if __name__ == "__main__":
    sys.exit(main())
