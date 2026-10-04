"""Activity history: every pose reading, kept in SQLite on the Pi for the reports.

Only numbers are stored (in view, posture, movement), never camera pictures.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

from app.models import ActivitySample
from app.utils.logger import get_logger

logger = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pose_samples (
    ts REAL NOT NULL,          -- Unix time
    in_view INTEGER NOT NULL,
    posture TEXT,
    movement REAL,
    moved INTEGER
);
CREATE INDEX IF NOT EXISTS pose_samples_ts ON pose_samples (ts);
"""


class ActivityHistory:
    def __init__(self, path: Path, keep_days: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(SCHEMA)
            removed = self._db.execute("DELETE FROM pose_samples WHERE ts < ?",
                                       (time.time() - keep_days * 86400,)).rowcount
        logger.info("history: %s (keeping %d days, removed %d old readings)", path.name, keep_days, removed)

    def _insert(self, sample: ActivitySample) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO pose_samples VALUES (?, ?, ?, ?, ?)",
                             (sample.timestamp, int(sample.in_view), sample.posture, sample.movement,
                              None if sample.moved is None else int(sample.moved)))

    def _between(self, start: float, end: float) -> list[ActivitySample]:
        with self._lock:
            rows = self._db.execute("SELECT ts, in_view, posture, movement, moved FROM pose_samples "
                                    "WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end)).fetchall()
        return [ActivitySample(timestamp=ts, in_view=bool(v), posture=p, movement=m,
                               moved=None if moved is None else bool(moved)) for ts, v, p, m, moved in rows]

    async def record(self, sample: ActivitySample) -> None:
        await asyncio.to_thread(self._insert, sample)

    async def between(self, start: float, end: float) -> list[ActivitySample]:
        return await asyncio.to_thread(self._between, start, end)

    def close(self) -> None:
        with self._lock:
            self._db.close()
