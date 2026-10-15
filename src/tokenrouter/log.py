"""Decision + cost log, shadow eval pairs and the human-review queue (SQLite)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    project_id       TEXT NOT NULL,
    mode             TEXT NOT NULL,          -- live | shadow
    prompt_hash      TEXT NOT NULL,
    prompt_tokens    INTEGER,
    completion_tokens INTEGER,
    jev_version      TEXT,
    jev_choice       TEXT,
    jev_confidence   REAL,
    probabilities    TEXT,                   -- JSON
    flags            TEXT,                   -- JSON
    decision_source  TEXT,                   -- jev | default | rule
    reasons          TEXT,                   -- JSON list
    tier             TEXT,
    primary_model    TEXT,
    model            TEXT,                   -- model that actually answered
    fallback_used    INTEGER NOT NULL DEFAULT 0,
    usd              REAL,                   -- actual spend for this request
    decision_ms      REAL,
    llm_ms           REAL,
    latency_ms       REAL,
    baseline_model   TEXT,
    baseline_usd     REAL,                   -- what the fixed baseline cost / would cost
    routed_est_usd   REAL,                   -- shadow: what the routed tier would cost
    error            TEXT
);
CREATE INDEX IF NOT EXISTS requests_project_ts ON requests(project_id, ts);
CREATE TABLE IF NOT EXISTS eval_pairs (
    request_id       INTEGER PRIMARY KEY REFERENCES requests(id),
    prompt           TEXT NOT NULL,
    baseline_model   TEXT NOT NULL,
    baseline_output  TEXT NOT NULL,
    routed_tier      TEXT NOT NULL,
    routed_model     TEXT NOT NULL,
    routed_output    TEXT NOT NULL,
    grade            INTEGER,                -- 1 acceptable, 0 not, NULL ungraded
    grader_note      TEXT
);
CREATE TABLE IF NOT EXISTS review_queue (
    request_id       INTEGER PRIMARY KEY REFERENCES requests(id),
    ts               TEXT NOT NULL,
    project_id       TEXT NOT NULL,
    reasons          TEXT NOT NULL,
    messages         TEXT NOT NULL,          -- JSON, kept locally for the reviewer
    status           TEXT NOT NULL DEFAULT 'open'
);
"""


def prompt_hash(messages: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(messages, sort_keys=True, default=str).encode()).hexdigest()[:16]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class RequestRecord:
    project_id: str
    mode: str
    prompt_hash: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    jev_version: str | None = None
    jev_choice: str | None = None
    jev_confidence: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    flags: dict[str, float] = field(default_factory=dict)
    decision_source: str | None = None
    reasons: list[str] = field(default_factory=list)
    tier: str | None = None
    primary_model: str | None = None
    model: str | None = None
    fallback_used: bool = False
    usd: float | None = None
    decision_ms: float | None = None
    llm_ms: float | None = None
    latency_ms: float | None = None
    baseline_model: str | None = None
    baseline_usd: float | None = None
    routed_est_usd: float | None = None
    error: str | None = None
    ts: str = field(default_factory=utcnow)


class DecisionLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def write(self, rec: RequestRecord) -> int:
        d = dict(rec.__dict__)
        for k in ("probabilities", "flags", "reasons"):
            d[k] = json.dumps(d[k])
        d["fallback_used"] = int(d["fallback_used"])
        cols = list(d)
        with self._lock, self.conn:
            cur = self.conn.execute(
                f"INSERT INTO requests ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [d[c] for c in cols],
            )
            return cur.lastrowid

    def add_eval_pair(self, request_id: int, prompt: str, baseline_model: str, baseline_output: str,
                      routed_tier: str, routed_model: str, routed_output: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO eval_pairs (request_id, prompt, baseline_model, baseline_output,"
                " routed_tier, routed_model, routed_output) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (request_id, prompt, baseline_model, baseline_output, routed_tier, routed_model, routed_output),
            )

    def enqueue_review(self, request_id: int, project_id: str, reasons: list[str], messages: list[dict]) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO review_queue (request_id, ts, project_id, reasons, messages) VALUES (?, ?, ?, ?, ?)",
                (request_id, utcnow(), project_id, json.dumps(reasons), json.dumps(messages, default=str)),
            )

    def month_spend(self, project_id: str, now: datetime | None = None) -> float:
        month = (now or datetime.now(timezone.utc)).strftime("%Y-%m")
        row = self.conn.execute(
            "SELECT COALESCE(SUM(usd), 0) FROM requests WHERE project_id = ? AND substr(ts, 1, 7) = ?",
            (project_id, month),
        ).fetchone()
        return float(row[0])

    def grade(self, request_id: int, grade: int, note: str | None = None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE eval_pairs SET grade = ?, grader_note = ? WHERE request_id = ?",
                (grade, note, request_id),
            )
