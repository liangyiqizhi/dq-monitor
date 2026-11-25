from __future__ import annotations

from datetime import datetime

from ..config import SourceConfig
from .base import COLLECTORS, BaseCollector, CollectorError
from .demo import DemoCollector  # noqa: F401  导入即注册
from .http_json import HttpJsonCollector  # noqa: F401


def build_collector(cfg: SourceConfig, now: datetime, anomaly_level: int = 0) -> BaseCollector:
    cls = COLLECTORS.get(cfg.type)
    if cls is None:
        raise CollectorError(f"未知数据源类型：{cfg.type}（已注册：{sorted(COLLECTORS)}）")
    return cls(cfg, now, anomaly_level)


__all__ = ["COLLECTORS", "BaseCollector", "CollectorError", "build_collector"]
