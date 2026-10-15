"""SQLite catalog table: one row per model per qualifying tier per refresh."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tokenrouter.catalog.models import CatalogRow

SCHEMA = """
CREATE TABLE IF NOT EXISTS refreshes (
    refresh_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    refreshed_at TEXT NOT NULL,
    n_models     INTEGER NOT NULL,
    n_unmatched  INTEGER NOT NULL,
    unmatched    TEXT NOT NULL DEFAULT '',
    applied      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS catalog (
    refresh_id       INTEGER NOT NULL REFERENCES refreshes(refresh_id),
    model_id         TEXT NOT NULL,
    provider         TEXT NOT NULL,
    litellm_name     TEXT NOT NULL,
    input_usd_per_m  REAL NOT NULL,
    output_usd_per_m REAL NOT NULL,
    context_window   INTEGER NOT NULL,
    supports_tools   INTEGER NOT NULL,
    supports_json    INTEGER NOT NULL,
    intelligence_idx REAL,
    coding_idx       REAL,
    agentic_idx      REAL,
    aa_slug          TEXT,
    blended_price    REAL NOT NULL,
    callable         INTEGER NOT NULL,
    native_key       INTEGER NOT NULL,
    tier             TEXT,
    rank_in_tier     INTEGER,
    refreshed_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS catalog_refresh_tier ON catalog(refresh_id, tier, rank_in_tier);
"""

_COLS = [
    "model_id", "provider", "litellm_name", "input_usd_per_m", "output_usd_per_m",
    "context_window", "supports_tools", "supports_json", "intelligence_idx", "coding_idx",
    "agentic_idx", "aa_slug", "blended_price", "callable", "native_key", "tier",
    "rank_in_tier", "refreshed_at",
]


class CatalogStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def save(self, rows: list[CatalogRow], unmatched: list[str], refreshed_at: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO refreshes (refreshed_at, n_models, n_unmatched, unmatched) VALUES (?, ?, ?, ?)",
                (refreshed_at, len({r.model_id for r in rows}), len(unmatched), "\n".join(unmatched)),
            )
            refresh_id = cur.lastrowid
            self.conn.executemany(
                f"INSERT INTO catalog (refresh_id, {', '.join(_COLS)}) VALUES (?, {', '.join('?' * len(_COLS))})",
                [(refresh_id, *(getattr(r, c) for c in _COLS)) for r in rows],
            )
        return refresh_id

    def mark_applied(self, refresh_id: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE refreshes SET applied = 1 WHERE refresh_id = ?", (refresh_id,))

    def latest_refresh_id(self, applied_only: bool = False, before: int | None = None) -> int | None:
        sql = "SELECT MAX(refresh_id) FROM refreshes WHERE 1=1"
        args: list = []
        if applied_only:
            sql += " AND applied = 1"
        if before is not None:
            sql += " AND refresh_id < ?"
            args.append(before)
        return self.conn.execute(sql, args).fetchone()[0]

    def load(self, refresh_id: int) -> list[CatalogRow]:
        cur = self.conn.execute(f"SELECT {', '.join(_COLS)} FROM catalog WHERE refresh_id = ?", (refresh_id,))
        out = []
        for rec in cur:
            d = dict(rec)
            for b in ("supports_tools", "supports_json", "callable", "native_key"):
                d[b] = bool(d[b])
            out.append(CatalogRow(**d))
        return out
