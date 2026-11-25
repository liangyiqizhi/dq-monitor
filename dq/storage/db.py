"""SQLite 访问层。换 Postgres 时只需要改这一个文件（SQL 都是标准语法）。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def compute_metrics(records: list[dict]) -> dict[str, Any]:
    """算批次级汇总，给 volatility 规则当上一批基线用（明细表只存最新快照，历史基线靠这里）。"""
    metrics: dict[str, Any] = {"row_count": len(records), "mean": {}, "sum": {}, "count": {}}
    for name in {k for r in records for k in r}:
        nums: list[float] = []
        for rec in records:
            val = rec.get(name)
            if isinstance(val, bool) or val is None:
                continue
            if isinstance(val, (int, float)):
                nums.append(float(val))
            elif isinstance(val, str):
                try:
                    nums.append(float(val.strip()))
                except ValueError:
                    continue
        if nums:
            metrics["mean"][name] = round(sum(nums) / len(nums), 6)
            metrics["sum"][name] = round(sum(nums), 6)
            metrics["count"][name] = len(nums)
    return metrics


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))

    # ---------------- batches ----------------
    def start_batch(self, source: str, started_at: datetime) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO batches (source, status, started_at) VALUES (?, 'running', ?)",
                (source, started_at.isoformat()),
            )
            return int(cur.lastrowid)

    def finish_batch(
        self,
        batch_id: int,
        *,
        status: str,
        row_count: int = 0,
        metrics: dict | None = None,
        error: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE batches
                      SET status = ?, row_count = ?, metrics = ?, error = ?, finished_at = ?
                    WHERE id = ?""",
                (
                    status,
                    row_count,
                    json.dumps(metrics or {}, ensure_ascii=False),
                    error,
                    (finished_at or datetime.now()).isoformat(),
                    batch_id,
                ),
            )

    def prev_batch_metrics(self, source: str, before: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT metrics FROM batches
                    WHERE source = ? AND status = 'ok' AND started_at < ?
                    ORDER BY started_at DESC LIMIT 1""",
                (source, before),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["metrics"])
        except json.JSONDecodeError:
            return None

    # ---------------- records ----------------
    def upsert_records(self, source: str, rows: Iterable[tuple[str, dict]], fetched_at: datetime) -> int:
        payload_rows = [(source, key, json.dumps(rec, ensure_ascii=False), fetched_at.isoformat()) for key, rec in rows]
        if not payload_rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """INSERT INTO records (source, biz_key, payload, fetched_at)
                        VALUES (?, ?, ?, ?)
                   ON CONFLICT (source, biz_key)
                   DO UPDATE SET payload = excluded.payload, fetched_at = excluded.fetched_at""",
                payload_rows,
            )
        return len(payload_rows)

    def count_records(self, source: str | None = None) -> int:
        with self.connect() as conn:
            if source:
                row = conn.execute("SELECT COUNT(*) AS c FROM records WHERE source = ?", (source,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS c FROM records").fetchone()
        return int(row["c"])

    # ---------------- rule results ----------------
    def save_rule_results(self, batch_id: int, results: Iterable[Any], created_at: datetime) -> int:
        rows = [
            (
                batch_id,
                r.source,
                r.rule_id,
                r.rule_type,
                r.status,
                r.observed,
                r.threshold,
                r.detail,
                created_at.isoformat(),
            )
            for r in results
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """INSERT INTO rule_results
                       (batch_id, source, rule_id, rule_type, status, observed, threshold, detail, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        return len(rows)

    # ---------------- alert state ----------------
    def alert_last_sent(self, key: str) -> datetime | None:
        with self.connect() as conn:
            row = conn.execute("SELECT last_sent_at FROM alert_state WHERE alert_key = ?", (key,)).fetchone()
        if not row:
            return None
        try:
            return datetime.fromisoformat(row["last_sent_at"])
        except ValueError:
            return None

    def mark_alert_sent(self, key: str, sent_at: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO alert_state (alert_key, last_sent_at, hit_count) VALUES (?, ?, 1)
                   ON CONFLICT (alert_key)
                   DO UPDATE SET last_sent_at = excluded.last_sent_at, hit_count = hit_count + 1""",
                (key, sent_at.isoformat()),
            )
