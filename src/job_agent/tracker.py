"""Local application tracker (SQLite). Records every job scored or applied to."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

STATUSES = ("scored", "letter_written", "applied", "interviewing", "rejected", "offer", "skipped")

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company     TEXT NOT NULL,
    role        TEXT NOT NULL,
    url         TEXT NOT NULL DEFAULT '',
    job_hash    TEXT NOT NULL UNIQUE,
    fit         INTEGER,
    similarity  REAL,
    summary     TEXT NOT NULL DEFAULT '',
    gaps        TEXT NOT NULL DEFAULT '',
    letter_path TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'scored',
    applied_on  TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    created     TEXT NOT NULL,
    updated     TEXT NOT NULL
);
"""


def job_hash(job_text: str) -> str:
    """Identity of a posting: whitespace/case-insensitive hash of its description."""
    norm = re.sub(r"\s+", " ", job_text).strip().lower()
    return hashlib.sha256(norm.encode()).hexdigest()


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class Tracker:
    def __init__(self, path: str | Path | None = None):
        if path is None:
            from .config import TRACKER_PATH
            path = TRACKER_PATH
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        if new:
            self.path.chmod(0o600)

    def close(self) -> None:
        self.db.close()

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def get(self, app_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        return dict(row) if row else None

    def find(self, job_text: str = "", company: str = "", role: str = "") -> dict | None:
        """Same posting (by description), or same company + role (case/punctuation-insensitive)."""
        if job_text:
            row = self.db.execute("SELECT * FROM applications WHERE job_hash = ?",
                                  (job_hash(job_text),)).fetchone()
            if row:
                return dict(row)
        if company and role:
            for row in self.db.execute("SELECT * FROM applications"):
                if _key(row["company"]) == _key(company) and _key(row["role"]) == _key(role):
                    return dict(row)
        return None

    def record_score(self, company: str, role: str, job_text: str, fit: dict,
                     url: str = "") -> int:
        """Insert a scored job, or refresh the score of an existing one. Returns its id.

        An existing entry's status is never moved backwards (e.g. 'applied' stays 'applied').
        """
        now = self._now()
        existing = self.find(job_text, company, role)
        values = (fit.get("fit"), fit.get("similarity"), fit.get("summary", ""),
                  "\n".join(fit.get("gaps", [])))
        if existing:
            self.db.execute(
                "UPDATE applications SET fit=?, similarity=?, summary=?, gaps=?, updated=? WHERE id=?",
                (*values, now, existing["id"]))
            self.db.commit()
            return existing["id"]
        cur = self.db.execute(
            "INSERT INTO applications (company, role, url, job_hash, fit, similarity, summary, gaps,"
            " status, created, updated) VALUES (?,?,?,?,?,?,?,?,'scored',?,?)",
            (company, role, url, job_hash(job_text), *values, now, now))
        self.db.commit()
        return cur.lastrowid

    def attach_letter(self, app_id: int, letter_path: str) -> None:
        app = self._require(app_id)
        status = "letter_written" if app["status"] == "scored" else app["status"]
        self.db.execute("UPDATE applications SET letter_path=?, status=?, updated=? WHERE id=?",
                        (letter_path, status, self._now(), app_id))
        self.db.commit()

    def set_status(self, app_id: int, status: str, note: str = "") -> None:
        if status not in STATUSES:
            raise ValueError(f"Unknown status {status!r}; use one of {', '.join(STATUSES)}")
        app = self._require(app_id)
        applied_on = app["applied_on"]
        if status == "applied" and not applied_on:
            applied_on = date.today().isoformat()
        notes = app["notes"]
        if note:
            notes = (notes + "\n" if notes else "") + f"{date.today().isoformat()}: {note}"
        self.db.execute("UPDATE applications SET status=?, applied_on=?, notes=?, updated=? WHERE id=?",
                        (status, applied_on, notes, self._now(), app_id))
        self.db.commit()

    def list(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self.db.execute("SELECT * FROM applications WHERE status=? ORDER BY updated DESC",
                                   (status,))
        else:
            rows = self.db.execute("SELECT * FROM applications ORDER BY updated DESC")
        return [dict(r) for r in rows]

    def counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT status, COUNT(*) AS n FROM applications GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def _require(self, app_id: int) -> dict:
        app = self.get(app_id)
        if not app:
            raise KeyError(f"No application with id {app_id}")
        return app
