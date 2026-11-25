"""流水线与存储层的集成测试：用离线 demo 源，不依赖网络。"""

from __future__ import annotations

from dataclasses import replace

from dq.config import load_settings
from dq.pipeline import run_source
from dq.storage.db import Database, compute_metrics


def make_settings(tmp_path):
    return replace(load_settings(), db_path=tmp_path / "test.db", alert_webhook="", alert_dedup_minutes=60)


def test_compute_metrics_ignores_non_numeric():
    metrics = compute_metrics([{"a": 1, "b": "x"}, {"a": 3, "b": None}])
    assert metrics["row_count"] == 2
    assert metrics["mean"]["a"] == 2.0
    assert metrics["sum"]["a"] == 4.0
    assert metrics["count"]["a"] == 2
    assert "b" not in metrics["mean"]


def test_upsert_is_idempotent(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    now = load_settings_timestamp()
    rows = [("A", {"v": 1}), ("B", {"v": 2}), ("A", {"v": 9})]
    db.upsert_records("s", rows, now)
    db.upsert_records("s", rows, now)
    assert db.count_records("s") == 2  # 同主键只留一行，最新的值覆盖旧值


def load_settings_timestamp():
    from dq.pipeline import utcnow

    return utcnow()


def test_pipeline_clean_run(tmp_path):
    settings = make_settings(tmp_path)
    result = run_source(settings, settings.source("demo_order"), notify=False)

    assert result.status == "ok"
    assert result.row_count == 80
    assert len(result.results) == len(settings.rules_for("demo_order"))
    assert result.failed == []
    assert result.sent_alerts == []  # 未配置 webhook 时只记日志


def test_pipeline_injected_anomalies(tmp_path):
    settings = make_settings(tmp_path)
    result = run_source(settings, settings.source("demo_order"), anomaly_level=1, notify=False)

    failed_ids = {r.rule_id for r in result.failed}
    assert {"order.id.unique", "order.amount.not_null", "order.amount.range", "order.status.enum"} <= failed_ids
    assert "order.schema" not in failed_ids


def test_pipeline_schema_drift(tmp_path):
    settings = make_settings(tmp_path)
    result = run_source(settings, settings.source("demo_order"), anomaly_level=2, notify=False)

    failed_ids = {r.rule_id for r in result.failed}
    assert "order.schema" in failed_ids
    assert "order.status.enum" in failed_ids


def test_second_run_sees_previous_baseline(tmp_path):
    settings = make_settings(tmp_path)
    source = settings.source("demo_order")
    run_source(settings, source, notify=False)
    run_source(settings, source, notify=False)

    db = Database(settings.db_path)
    assert db.prev_batch_metrics("demo_order", before="2999-01-01T00:00:00+00:00") is not None


def test_results_persisted_for_dashboard(tmp_path):
    settings = make_settings(tmp_path)
    run_source(settings, settings.source("demo_order"), notify=False)

    db = Database(settings.db_path)
    with db.connect() as conn:
        batches = conn.execute("SELECT COUNT(*) AS c FROM batches").fetchone()["c"]
        results = conn.execute("SELECT COUNT(*) AS c FROM rule_results").fetchone()["c"]
    assert batches == 1
    assert results == len(settings.rules_for("demo_order"))
