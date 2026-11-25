"""规则实现。每个函数签名统一为 (records, field, params, ctx) -> Outcome，方便注册与单测。

新增一种规则类型：写一个函数 + 在 CHECKS 里注册一行，配置里就能用。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass
class CheckContext:
    """规则执行时的上下文：当前时间、数据源、上一批次汇总指标。"""

    now: datetime
    source: str
    prev_metrics: dict[str, Any] | None = None


@dataclass
class Outcome:
    passed: bool
    observed: float | None = None
    threshold: float | None = None
    detail: str = ""


# ----------------------------- 工具 -----------------------------
def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, str):
        try:
            num = float(value.strip())
        except ValueError:
            return None
        return num if math.isfinite(num) else None
    return None


def parse_dt(value: Any) -> datetime | None:
    """兼容 ISO8601（含 Z）、时间戳、常见日期格式。"""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        dt = None
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return None
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _sample(values: list[Any], limit: int = 3) -> str:
    return str(values[:limit]) if values else ""


# ----------------------------- 规则 -----------------------------
def check_not_null(records: list[dict], field: str | None, params: dict, ctx: CheckContext) -> Outcome:
    min_rate = float(params.get("min_pass_rate", 1.0))
    total = len(records)
    if total == 0:
        return Outcome(False, 0.0, min_rate, "批次为空，无法校验非空")
    blanks = [r.get(field) for r in records if _is_blank(r.get(field))]
    rate = 1 - len(blanks) / total
    detail = f"{total - len(blanks)}/{total} 条非空，非空率 {rate:.2%}（阈值 {min_rate:.2%}）"
    return Outcome(rate >= min_rate, round(rate, 4), min_rate, detail)


def check_unique(records: list[dict], field: str | None, params: dict, ctx: CheckContext) -> Outcome:
    keys = [r.get(field) for r in records if not _is_blank(r.get(field))]
    if not keys:
        return Outcome(False, None, 0.0, f"字段 {field} 无有效取值，无法校验唯一性")
    dups = [k for k, cnt in Counter(keys).items() if cnt > 1]
    detail = f"{len(keys)} 个有效主键，重复 {len(dups)} 个"
    if dups:
        detail += f"，示例 {_sample(dups)}"
    return Outcome(not dups, float(len(dups)), 0.0, detail)


def check_range(records: list[dict], field: str | None, params: dict, ctx: CheckContext) -> Outcome:
    low, high = params.get("min"), params.get("max")
    allow_null = bool(params.get("allow_null", False))
    bad: list[Any] = []
    for rec in records:
        raw = rec.get(field)
        if _is_blank(raw):
            if not allow_null:
                bad.append(raw)
            continue
        num = _to_float(raw)
        if num is None or (low is not None and num < low) or (high is not None and num > high):
            bad.append(raw)
    detail = f"越界/非法 {len(bad)} 条，区间 [{low}, {high}]"
    if bad:
        detail += f"，示例 {_sample(bad)}"
    return Outcome(not bad, float(len(bad)), 0.0, detail)


def check_enum(records: list[dict], field: str | None, params: dict, ctx: CheckContext) -> Outcome:
    allowed = set(params.get("allowed") or [])
    allow_null = bool(params.get("allow_null", False))
    if not allowed:
        return Outcome(True, None, None, "未配置 allowed，跳过")
    bad: list[Any] = []
    for rec in records:
        raw = rec.get(field)
        if _is_blank(raw):
            if not allow_null:
                bad.append(raw)
            continue
        if raw not in allowed:
            bad.append(raw)
    detail = f"非法取值 {len(bad)} 条，白名单 {sorted(allowed)}"
    if bad:
        detail += f"，示例 {_sample(bad)}"
    return Outcome(not bad, float(len(bad)), 0.0, detail)


def check_pattern(records: list[dict], field: str | None, params: dict, ctx: CheckContext) -> Outcome:
    regex = params.get("regex")
    if not regex:
        return Outcome(True, None, None, "未配置 regex，跳过")
    pattern = re.compile(regex)
    bad = [r.get(field) for r in records if not _is_blank(r.get(field)) and not pattern.fullmatch(str(r.get(field)))]
    detail = f"格式不符 {len(bad)} 条"
    if bad:
        detail += f"，示例 {_sample(bad)}"
    return Outcome(not bad, float(len(bad)), 0.0, detail)


def check_schema(records: list[dict], field: str | None, params: dict, ctx: CheckContext) -> Outcome:
    expected = set(params.get("expected_fields") or [])
    if not expected:
        return Outcome(True, None, None, "未配置 expected_fields，跳过")
    present = {key for rec in records for key in rec}
    missing = sorted(expected - present)
    detail = "字段齐全" if not missing else f"缺失字段 {missing}"
    return Outcome(not missing, float(len(missing)), 0.0, detail)


def check_freshness(records: list[dict], field: str | None, params: dict, ctx: CheckContext) -> Outcome:
    max_lag = float(params.get("max_lag_hours", 24))
    times = [t for t in (parse_dt(r.get(field)) for r in records) if t is not None]
    if not times:
        return Outcome(False, None, max_lag, f"字段 {field} 无可用时间值")
    latest = max(times)
    lag = (ctx.now - latest).total_seconds() / 3600
    detail = f"最新数据 {latest.isoformat(timespec='minutes')}，滞后 {lag:.1f}h（阈值 {max_lag}h）"
    return Outcome(lag <= max_lag, round(lag, 2), max_lag, detail)


def check_volatility(records: list[dict], field: str | None, params: dict, ctx: CheckContext) -> Outcome:
    agg = params.get("agg", "mean")
    max_change = float(params.get("max_change_pct", 30))
    prev = ctx.prev_metrics or {}
    metric = params.get("metric")

    if metric == "row_count":
        current = float(len(records))
        baseline = prev.get("row_count")
        label = "行数"
    else:
        nums = [n for n in (_to_float(r.get(field)) for r in records) if n is not None]
        if not nums:
            return Outcome(False, None, max_change, f"字段 {field} 无可计算数值")
        current = sum(nums) / len(nums) if agg == "mean" else float(sum(nums))
        baseline = (prev.get(agg) or {}).get(field)
        label = f"{field}({agg})"

    if not baseline:
        return Outcome(True, None, max_change, f"无历史批次基线，{label} 跳过首轮")

    change = abs(current - baseline) / abs(baseline) * 100
    detail = f"{label} 本批 {current:.2f}，上批 {float(baseline):.2f}，波动 {change:.2f}%（阈值 {max_change}%）"
    return Outcome(change <= max_change, round(change, 2), max_change, detail)


def check_row_count(records: list[dict], field: str | None, params: dict, ctx: CheckContext) -> Outcome:
    min_rows = int(params.get("min_rows", 1))
    max_rows = params.get("max_rows")
    count = len(records)
    passed = count >= min_rows and (max_rows is None or count <= max_rows)
    detail = f"本批 {count} 行，要求 ≥{min_rows}" + (f" 且 ≤{max_rows}" if max_rows else "")
    return Outcome(passed, float(count), float(min_rows), detail)


CHECKS: dict[str, Callable[[list[dict], str | None, dict, CheckContext], Outcome]] = {
    "not_null": check_not_null,
    "unique": check_unique,
    "range": check_range,
    "enum": check_enum,
    "pattern": check_pattern,
    "schema": check_schema,
    "freshness": check_freshness,
    "volatility": check_volatility,
    "row_count": check_row_count,
}
