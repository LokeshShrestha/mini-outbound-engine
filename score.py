# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "beautifulsoup4"]
# ///
"""1. Source + score: scrape each site, ask Claude for ICP fit, write ranked leads.csv."""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

from engine import build_score_prompt, fetch, llm, parse_score, SCORE_SYSTEM

ICP = Path(__file__).with_name("icp.md")
OUT = Path(__file__).with_name("leads.csv")
FIELDS = ["rank", "name", "website", "contact_email", "score", "reason", "context"]


def verified_context(row: dict) -> str:
    """Return only explicitly supplied evidence suitable for outreach hooks."""
    return "\n\n".join(
        f"{label}: {row.get(field, '').strip()}"
        for field, label in (("context", "CONTEXT"), ("job_post", "JOB POST"), ("news", "NEWS"))
        if row.get(field, "").strip()
    )


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if r.get("name") or r.get("website")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="companies.csv")
    ap.add_argument("--limit", type=int, default=0, help="stop after N new leads")
    ap.add_argument("--delay", type=float, default=0.2, help="seconds between requests")
    args = ap.parse_args()

    icp = ICP.read_text(encoding="utf-8")
    leads = read_csv(Path(__file__).with_name(args.input))
    done = {r["name"] for r in read_csv(OUT)}
    scored = read_csv(OUT)
    new = 0

    for i, row in enumerate(leads, 1):
        name, url = row.get("name", "").strip(), row.get("website", "").strip()
        if not name or name in done:
            continue
        print(f"[{i}/{len(leads)}] {name}", end=" ... ", flush=True)
        text = fetch(url)
        prompt = build_score_prompt(icp, name, url, text)
        if not prompt:
            score, reason = 0, "site unreachable - no text to score"
        else:
            # free models sometimes burn the whole token budget on reasoning and
            # never emit SCORE: - nudge once, then take whatever parses.
            out = ""
            for attempt in range(2):
                out = llm(SCORE_SYSTEM, prompt, max_tokens=1024)
                if attempt or re.search(r"(?m)^\s*SCORE:\s*\d", out):
                    break
                prompt = "Output ONLY the two lines. No thinking, no preamble.\n\n" + prompt
            score, reason = parse_score(out)
        scored.append(
            {
                "rank": "0",
                "name": name,
                "website": url,
                "contact_email": (row.get("contact_email") or "").strip(),
                "score": score,
                "reason": reason,
                "context": verified_context(row),
            }
        )
        done.add(name)
        print(f"{score}  {reason}")
        new += 1
        if args.limit and new >= args.limit:
            break
        time.sleep(args.delay)

    scored.sort(key=lambda r: int(r["score"]), reverse=True)
    for n, r in enumerate(scored, 1):
        r["rank"] = n
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(scored)
    print(f"\n{new} new, {len(scored)} total -> {OUT.name}")
    print("Open it, then File > Import > Upload to make it a Google Sheet.")


if __name__ == "__main__":
    sys.exit(main())
