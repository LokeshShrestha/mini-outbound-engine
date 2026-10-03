# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "beautifulsoup4"]
# ///
"""3. Replies: classify, draft a response, move the CRM record -> replies_out.csv.

CRM updates run here (they are bookkeeping, not outreach). The drafted response
never sends: it is written out as status=needs_approval.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

from app.db import create_reply, init_db
from engine import classify, crm_update, draft_reply

ROOT = Path(__file__).parent
FIELDS = ["email", "name", "reply_text", "deal_id", "label", "response", "crm", "status"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="replies.csv")
    ap.add_argument("--dry-run", action="store_true", help="classify + draft, skip HubSpot")
    args = ap.parse_args()

    init_db()
    path = ROOT / args.input
    if not path.exists():
        sys.exit(f"{path.name} not found. Expected columns: email,name,reply_text[,deal_id]")
    with path.open(newline="", encoding="utf-8-sig") as f:
        inbox = [r for r in csv.DictReader(f) if (r.get("reply_text") or "").strip()]

    out = []
    for r in inbox:
        email, text = r.get("email", "").strip(), r["reply_text"]
        label = classify(text)
        name = (r.get("name") or "").strip()
        print(f"  {email or '?'} -> {label}", end=" ... ", flush=True)
        if not email:
            crm = "skipped: no email"
        elif args.dry_run:
            crm = "dry-run: not touched"
        else:
            crm = crm_update(email, name, label, (r.get("deal_id") or "").strip())
        out.append(
            {
                "email": email,
                "name": name,
                "reply_text": text,
                "deal_id": (r.get("deal_id") or "").strip(),
                "label": label,
                "response": draft_reply(label, text, name),
                "crm": crm,
                "status": "needs_approval",
            }
        )
        create_reply(out[-1])
        print(crm)

    dest = ROOT / "replies_out.csv"
    with dest.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out)
    print(f"\n{len(out)} replies -> {dest.name}  (CRM updates: {'off' if args.dry_run else 'on'})")


if __name__ == "__main__":
    sys.exit(main())
