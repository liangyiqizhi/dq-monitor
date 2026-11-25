"""采集器基类 + 注册表。新增一种数据源类型 = 加一个子类 + 注册一行。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from ..config import SourceConfig


class CollectorError(RuntimeError):
    """采集失败（网络、解析、结构不符），由流水线捕获并记为失败批次。"""


def resolve_path(obj: Any, path: str | None) -> Any:
    """按 "a.b.0.c" 取值，支持 dict 键与 list 下标；path 为空返回原对象。"""
    if not path:
        return obj
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


class BaseCollector(ABC):
    type_name = "base"

    def __init__(self, cfg: SourceConfig, now: datetime, anomaly_level: int = 0):
        self.cfg = cfg
        self.now = now
        self.anomaly_level = anomaly_level

    @abstractmethod
    def fetch(self) -> list[dict]:
        """返回记录列表；每条记录是 {字段名: 值}。"""


COLLECTORS: dict[str, type[BaseCollector]] = {}


def register(cls: type[BaseCollector]) -> type[BaseCollector]:
    COLLECTORS[cls.type_name] = cls
    return cls
