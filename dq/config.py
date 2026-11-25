"""配置加载：把 YAML 变成强类型对象，环境变量可覆盖数据库路径和告警设置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = ROOT / "config"
DEFAULT_DB = ROOT / "data" / "dq.db"


@dataclass
class SourceConfig:
    name: str
    type: str
    desc: str = ""
    options: dict[str, Any] = dc_field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.desc or self.name

    @property
    def key_field(self) -> str:
        return self.options.get("key_field") or "id"


@dataclass
class RuleConfig:
    id: str
    type: str
    source: str
    field: str | None = None
    level: str = "fail"
    params: dict[str, Any] = dc_field(default_factory=dict)
    desc: str = ""


@dataclass
class Settings:
    db_path: Path
    sources: list[SourceConfig]
    rules: list[RuleConfig]
    alert_webhook: str = ""
    alert_kind: str = "wecom"
    alert_dedup_minutes: int = 60

    def source(self, name: str) -> SourceConfig:
        for s in self.sources:
            if s.name == name:
                return s
        raise KeyError(f"配置里没有数据源：{name}")

    def rules_for(self, source_name: str) -> list[RuleConfig]:
        return [r for r in self.rules if r.source == source_name]


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"缺少配置文件：{path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings(config_dir: str | Path | None = None, db_path: str | Path | None = None) -> Settings:
    cdir = Path(config_dir or os.getenv("DQ_CONFIG_DIR") or DEFAULT_CONFIG_DIR)

    sources: list[SourceConfig] = []
    for raw in _read_yaml(cdir / "sources.yaml").get("sources", []):
        # 顶层键 + 可选的 options 子块，统一摊平到一个 dict
        options = {k: v for k, v in raw.items() if k not in {"name", "type", "desc", "options"}}
        options.update(raw.get("options") or {})
        sources.append(
            SourceConfig(
                name=raw["name"],
                type=raw["type"],
                desc=raw.get("desc", ""),
                options=options,
            )
        )

    rules: list[RuleConfig] = []
    for raw in _read_yaml(cdir / "rules.yaml").get("rules", []):
        rules.append(
            RuleConfig(
                id=raw["id"],
                type=raw["type"],
                source=raw["source"],
                field=raw.get("field"),
                level=raw.get("level", "fail"),
                params=raw.get("params", {}) or {},
                desc=raw.get("desc", ""),
            )
        )

    db = Path(db_path or os.getenv("DQ_DB") or DEFAULT_DB)
    return Settings(
        db_path=db,
        sources=sources,
        rules=rules,
        alert_webhook=os.getenv("DQ_ALERT_WEBHOOK", ""),
        alert_kind=os.getenv("DQ_ALERT_KIND", "wecom"),
        alert_dedup_minutes=int(os.getenv("DQ_ALERT_DEDUP_MINUTES", "60")),
    )
