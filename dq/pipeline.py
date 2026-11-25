"""流水线编排：采集 -> 幂等落库 -> 规则校验 -> 告警。所有入口（CLI / CI / 定时任务）都走这里。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .alerting.notifier import Notifier
from .collectors import build_collector
from .config import Settings, SourceConfig
from .rules.engine import FAIL, RuleEngine, RuleResult
from .storage.db import Database, compute_metrics

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    source: str
    batch_id: int
    status: str            # ok / failed
    row_count: int = 0
    results: list[RuleResult] = field(default_factory=list)
    sent_alerts: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def failed(self) -> list[RuleResult]:
        return [r for r in self.results if r.status == FAIL]

    @property
    def warned(self) -> list[RuleResult]:
        return [r for r in self.results if r.status == "warn"]

    @property
    def passed(self) -> list[RuleResult]:
        return [r for r in self.results if r.status == "pass"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def run_source(
    settings: Settings,
    source: SourceConfig,
    *,
    now: datetime | None = None,
    anomaly_level: int = 0,
    db: Database | None = None,
    notify: bool = True,
) -> RunResult:
    now = now or utcnow()
    db = db or Database(settings.db_path)
    db.init_schema()

    batch_id = db.start_batch(source.name, now)
    logger.info("批次 %s 开始：source=%s", batch_id, source.name)

    try:
        collector = build_collector(source, now, anomaly_level)
        records = collector.fetch()
        if not records:
            raise RuntimeError("采集结果为空，疑似上游无数据或解析失败")

        key_field = source.key_field
        rows = [(str(rec[key_field]), rec) for rec in records if rec.get(key_field) is not None]
        if not rows:
            raise RuntimeError(f"记录里没有主键字段 {key_field}")

        db.upsert_records(source.name, rows, now)
        metrics = compute_metrics(records)
        db.finish_batch(batch_id, status="ok", row_count=len(records), metrics=metrics, finished_at=utcnow())

        results = RuleEngine(settings, db).evaluate(source.name, records, batch_id, now)
        db.save_rule_results(batch_id, results, now)

        sent: list[str] = []
        if notify:
            sent = Notifier(settings, db).notify(source.name, results, now)

        return RunResult(
            source=source.name,
            batch_id=batch_id,
            status="ok",
            row_count=len(records),
            results=results,
            sent_alerts=sent,
        )

    except Exception as err:
        logger.exception("数据源 %s 采集失败", source.name)
        db.finish_batch(batch_id, status="failed", error=str(err), finished_at=utcnow())
        return RunResult(source=source.name, batch_id=batch_id, status="failed", error=str(err))


def run_all(
    settings: Settings,
    *,
    sources: list[str] | None = None,
    now: datetime | None = None,
    anomaly_level: int = 0,
    notify: bool = True,
) -> list[RunResult]:
    targets = [settings.source(name) for name in sources] if sources else settings.sources
    return [
        run_source(settings, cfg, now=now, anomaly_level=anomaly_level, notify=notify) for cfg in targets
    ]
