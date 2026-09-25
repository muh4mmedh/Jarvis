"""Persistent memory: conversation log + a fact store JARVIS curates itself.

SQLite, no server, no embeddings. Fact recall is keyword-scored, which is
plenty for a personal assistant holding a few hundred facts and keeps the
dependency list short.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from .config import cfg

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    role      TEXT NOT NULL,
    content   TEXT NOT NULL,
    meta      TEXT
);
CREATE TABLE IF NOT EXISTS facts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    topic     TEXT NOT NULL,
    fact      TEXT NOT NULL,
    hits      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts);
CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_topic ON facts(topic);
"""


class Memory:
    def __init__(self) -> None:
        path = cfg.resolve(cfg.get("memory.db_path", "data/memory.db"))
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.db.commit()
        self.enabled = bool(cfg.get("memory.enabled", True))

    # ── conversation ────────────────────────────────────────────────
    def log_turn(self, role: str, content: str, meta: dict | None = None) -> None:
        if not self.enabled or not content:
            return
        self.db.execute(
            "INSERT INTO turns (ts, role, content, meta) VALUES (?,?,?,?)",
            (time.time(), role, content, json.dumps(meta or {})),
        )
        self.db.commit()

    def recent_turns(self, limit: int | None = None) -> list[dict]:
        limit = limit or int(cfg.get("memory.context_turns", 24))
        rows = self.db.execute(
            "SELECT role, content FROM turns ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def clear_conversation(self) -> int:
        n = self.db.execute("SELECT COUNT(*) c FROM turns").fetchone()["c"]
        self.db.execute("DELETE FROM turns")
        self.db.commit()
        return n

    # ── long-term facts ─────────────────────────────────────────────
    def remember(self, topic: str, fact: str) -> str:
        topic = topic.strip().lower()[:80]
        existing = self.db.execute("SELECT fact FROM facts WHERE topic=?", (topic,)).fetchone()
        self.db.execute(
            "INSERT INTO facts (ts, topic, fact) VALUES (?,?,?) "
            "ON CONFLICT(topic) DO UPDATE SET fact=excluded.fact, ts=excluded.ts",
            (time.time(), topic, fact.strip()),
        )
        self.db.commit()
        return "updated" if existing else "stored"

    def forget(self, topic: str) -> bool:
        cur = self.db.execute("DELETE FROM facts WHERE topic=?", (topic.strip().lower(),))
        self.db.commit()
        return cur.rowcount > 0

    def all_facts(self) -> list[dict]:
        cap = int(cfg.get("memory.max_facts", 60))
        rows = self.db.execute(
            "SELECT topic, fact FROM facts ORDER BY hits DESC, ts DESC LIMIT ?", (cap,)
        ).fetchall()
        return [dict(r) for r in rows]

    def search_facts(self, query: str, limit: int = 8) -> list[dict]:
        """Cheap keyword overlap scoring — no embedding model required."""
        words = {w for w in re.findall(r"\w+", (query or "").lower()) if len(w) > 2}
        if not words:
            return []
        scored: list[tuple[int, dict]] = []
        for row in self.db.execute("SELECT id, topic, fact FROM facts").fetchall():
            blob = f"{row['topic']} {row['fact']}".lower()
            score = sum(1 for w in words if w in blob)
            if score:
                scored.append((score, dict(row)))
        scored.sort(key=lambda x: -x[0])
        top = [r for _, r in scored[:limit]]
        for r in top:
            self.db.execute("UPDATE facts SET hits = hits + 1 WHERE id=?", (r["id"],))
        self.db.commit()
        return [{"topic": r["topic"], "fact": r["fact"]} for r in top]

    def stats(self) -> dict[str, Any]:
        t = self.db.execute("SELECT COUNT(*) c FROM turns").fetchone()["c"]
        f = self.db.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]
        return {"turns": t, "facts": f}


memory = Memory()
