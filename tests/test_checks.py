"""规则实现的单元测试：规则引擎是最该被覆盖的部分。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dq.rules.checks import CHECKS, CheckContext, parse_dt

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
CTX = CheckContext(now=NOW, source="unit_test")


def run(rule_type: str, records: list[dict], field: str | None = None, **params):
    return CHECKS[rule_type](records, field, params, CTX)


# ----------------------------- not_null -----------------------------
def test_not_null_threshold():
    records = [{"v": 1}, {"v": None}, {"v": ""}, {"v": 4}]
    outcome = run("not_null", records, "v", min_pass_rate=0.99)
    assert outcome.passed is False
    assert outcome.observed == 0.5
    assert "2/4 条非空" in outcome.detail


def test_not_null_all_present():
    assert run("not_null", [{"v": 1}, {"v": 2}], "v", min_pass_rate=1.0).passed is True


# ----------------------------- unique -----------------------------
def test_unique_detects_duplicate():
    records = [{"id": "A"}, {"id": "B"}, {"id": "A"}]
    outcome = run("unique", records, "id")
    assert outcome.passed is False
    assert outcome.observed == 1.0  # 一个重复键
    assert "重复 1 个" in outcome.detail


def test_unique_ignores_blank_keys():
    assert run("unique", [{"id": "A"}, {"id": None}], "id").passed is True


# ----------------------------- range -----------------------------
def test_range_flags_out_of_bounds_and_null():
    records = [{"v": 10}, {"v": -1}, {"v": None}]
    outcome = run("range", records, "v", min=0, max=100)
    assert outcome.passed is False
    assert outcome.observed == 2.0


def test_range_allow_null():
    assert run("range", [{"v": 10}, {"v": None}], "v", min=0, max=100, allow_null=True).passed is True


def test_range_rejects_non_numeric():
    assert run("range", [{"v": "abc"}], "v", min=0, max=100).passed is False


# ----------------------------- enum / pattern -----------------------------
def test_enum_whitelist():
    records = [{"s": "paid"}, {"s": "void"}]
    outcome = run("enum", records, "s", allowed=["paid", "pending"])
    assert outcome.passed is False
    assert "void" in outcome.detail


def test_pattern_fullmatch():
    assert run("pattern", [{"c": "home-decor"}], "c", regex="[a-z][a-z-]*").passed is True
    assert run("pattern", [{"c": "Home Decor"}], "c", regex="[a-z][a-z-]*").passed is False


# ----------------------------- schema -----------------------------
def test_schema_detects_missing_field():
    records = [{"a": 1, "b": 2}, {"a": 3}]
    outcome = run("schema", records, expected_fields=["a", "b", "c"])
    assert outcome.passed is False
    assert outcome.observed == 1.0
    assert "c" in outcome.detail


# ----------------------------- freshness -----------------------------
def test_freshness_within_window():
    records = [{"t": (NOW - timedelta(hours=2)).isoformat()}]
    assert run("freshness", records, "t", max_lag_hours=24).passed is True


def test_freshness_stale_data():
    records = [{"t": (NOW - timedelta(hours=30)).isoformat()}]
    outcome = run("freshness", records, "t", max_lag_hours=24)
    assert outcome.passed is False
    assert outcome.observed == pytest.approx(30.0, abs=0.01)


def test_parse_dt_handles_z_and_date_only():
    assert parse_dt("2026-01-01T08:00:00Z") == datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
    assert parse_dt("2025-12-31") == datetime(2025, 12, 31, tzinfo=timezone.utc)
    assert parse_dt("not-a-date") is None


def test_freshness_unparsable_field():
    assert run("freshness", [{"t": "??"}], "t", max_lag_hours=24).passed is False


# ----------------------------- volatility -----------------------------
def test_volatility_uses_previous_baseline():
    ctx = CheckContext(now=NOW, source="s", prev_metrics={"mean": {"price": 100.0}})
    outcome = CHECKS["volatility"]([{"price": 150}, {"price": 150}], "price", {"agg": "mean", "max_change_pct": 25}, ctx)
    assert outcome.passed is False
    assert outcome.observed == 50.0


def test_volatility_without_baseline_is_skipped():
    outcome = run("volatility", [{"price": 150}], "price", agg="mean", max_change_pct=10)
    assert outcome.passed is True
    assert "无历史批次基线" in outcome.detail


def test_volatility_row_count_metric():
    ctx = CheckContext(now=NOW, source="s", prev_metrics={"row_count": 100})
    outcome = CHECKS["volatility"]([{} for _ in range(50)], None, {"metric": "row_count", "max_change_pct": 20}, ctx)
    assert outcome.passed is False


# ----------------------------- row_count -----------------------------
def test_row_count_watermark():
    assert run("row_count", [{} for _ in range(3)], min_rows=5).passed is False
    assert run("row_count", [{} for _ in range(9)], min_rows=5, max_rows=10).passed is True


def test_all_rule_types_registered():
    expected = {"not_null", "unique", "range", "enum", "pattern", "schema", "freshness", "volatility", "row_count"}
    assert expected <= set(CHECKS)
