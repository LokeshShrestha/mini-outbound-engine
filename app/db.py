from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "outbound.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    website TEXT NOT NULL,
    contact_email TEXT NOT NULL DEFAULT '',
    score INTEGER NOT NULL DEFAULT 0 CHECK(score BETWEEN 0 AND 100),
    reason TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT '',
    site_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(name, website)
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    first_line TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'needs_approval',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    email TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    reply_text TEXT NOT NULL,
    deal_id TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    response TEXT NOT NULL DEFAULT '',
    crm_result TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'needs_approval',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(path: Path = DEFAULT_DB) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)


def dashboard_counts(path: Path = DEFAULT_DB) -> dict[str, int]:
    init_db(path)
    with connect(path) as connection:
        return {
            "leads": connection.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
            "high_fit": connection.execute("SELECT COUNT(*) FROM leads WHERE score >= 80").fetchone()[0],
            "drafts": connection.execute("SELECT COUNT(*) FROM drafts WHERE status = 'needs_approval'").fetchone()[0],
            "replies": connection.execute("SELECT COUNT(*) FROM replies").fetchone()[0],
        }


def list_leads(path: Path = DEFAULT_DB) -> list[sqlite3.Row]:
    init_db(path)
    with connect(path) as connection:
        return connection.execute(
            "SELECT * FROM leads ORDER BY score DESC, name COLLATE NOCASE"
        ).fetchall()


def get_lead(lead_id: int, path: Path = DEFAULT_DB) -> sqlite3.Row | None:
    init_db(path)
    with connect(path) as connection:
        return connection.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()


def get_lead_by_website(website: str, path: Path = DEFAULT_DB) -> sqlite3.Row | None:
    init_db(path)
    with connect(path) as connection:
        return connection.execute("SELECT * FROM leads WHERE website = ?", (website,)).fetchone()


def list_drafts(path: Path = DEFAULT_DB) -> list[sqlite3.Row]:
    init_db(path)
    with connect(path) as connection:
        return connection.execute(
            """
            SELECT drafts.*, leads.name, leads.website
            FROM drafts JOIN leads ON leads.id = drafts.lead_id
            ORDER BY drafts.created_at DESC
            """
        ).fetchall()


def list_replies(path: Path = DEFAULT_DB) -> list[sqlite3.Row]:
    init_db(path)
    with connect(path) as connection:
        return connection.execute(
            "SELECT * FROM replies ORDER BY created_at DESC"
        ).fetchall()


def upsert_lead(row: dict[str, Any], path: Path = DEFAULT_DB) -> int:
    init_db(path)
    timestamp = now()
    with connect(path) as connection:
        connection.execute(
            """
            INSERT INTO leads (name, website, contact_email, score, reason, context, site_text,
                               status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
            ON CONFLICT(name, website) DO UPDATE SET
                contact_email = excluded.contact_email,
                score = excluded.score,
                reason = excluded.reason,
                context = excluded.context,
                site_text = excluded.site_text,
                updated_at = excluded.updated_at
            """,
            (
                row.get("name", "").strip(),
                row.get("website", "").strip(),
                row.get("contact_email", "").strip(),
                max(0, min(int(row.get("score") or 0), 100)),
                row.get("reason", "").strip(),
                row.get("context", "").strip(),
                row.get("site_text", "").strip(),
                timestamp,
                timestamp,
            ),
        )
        lead = connection.execute(
            "SELECT id FROM leads WHERE name = ? AND website = ?",
            (row.get("name", "").strip(), row.get("website", "").strip()),
        ).fetchone()
        return int(lead[0])


def create_draft(row: dict[str, Any], path: Path = DEFAULT_DB) -> int:
    lead = get_lead_by_website(row.get("website", "").strip(), path)
    if lead is None:
        raise ValueError("draft requires an existing lead")
    timestamp = now()
    with connect(path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO drafts (lead_id, first_line, email, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                lead["id"],
                row.get("first_line", "").strip(),
                row.get("email", "").strip(),
                row.get("status", "needs_approval"),
                timestamp,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)


def create_reply(row: dict[str, Any], path: Path = DEFAULT_DB) -> int:
    lead = get_lead_by_website(row.get("website", "").strip(), path) if row.get("website") else None
    timestamp = now()
    with connect(path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO replies (lead_id, email, name, reply_text, deal_id, label, response,
                                 crm_result, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lead["id"] if lead else None,
                row.get("email", "").strip(),
                row.get("name", "").strip(),
                row.get("reply_text", "").strip(),
                row.get("deal_id", "").strip(),
                row.get("label", "").strip(),
                row.get("response", "").strip(),
                row.get("crm", "").strip(),
                row.get("status", "needs_approval"),
                timestamp,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)


def update_status(table: str, record_id: int, status: str, path: Path = DEFAULT_DB) -> None:
    if table not in {"drafts", "replies", "leads"}:
        raise ValueError(f"unsupported table: {table}")
    init_db(path)
    with connect(path) as connection:
        connection.execute(
            f"UPDATE {table} SET status = ?, updated_at = ? WHERE id = ?",
            (status, now(), record_id),
        )


def record_event(kind: str, message: str, path: Path = DEFAULT_DB) -> None:
    init_db(path)
    with connect(path) as connection:
        connection.execute(
            "INSERT INTO events (kind, message, created_at) VALUES (?, ?, ?)",
            (kind, message, now()),
        )
