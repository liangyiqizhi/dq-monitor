"""规则引擎：把配置里的规则套到一批数据上，产出可入库的结果。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from ..config import Settings
from ..storage.db import Database
from .checks import CHECKS, CheckContext, Outcome

logger = logging.getLogger(__name__)

PASS, WARN, FAIL = "pass", "warn", "fail"


@dataclass
class RuleResult:
    rule_id: str
    rule_type: str
    source: str
    status: str
    observed: float | None = None
    threshold: float | None = None
    detail: str = ""
    desc: str = ""


class RuleEngine:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db

    def evaluate(self, source: str, records: list[dict], batch_id: int, now: datetime) -> list[RuleResult]:
        rule_cfgs = self.settings.rules_for(source)
        if not rule_cfgs:
            logger.warning("数据源 %s 没有配置任何规则", source)
            return []

        ctx = CheckContext(
            now=now,
            source=source,
            prev_metrics=self.db.prev_batch_metrics(source, before=now.isoformat()),
        )

        results: list[RuleResult] = []
        for rc in rule_cfgs:
            checker = CHECKS.get(rc.type)
            if checker is None:
                results.append(
                    RuleResult(rc.id, rc.type, source, WARN, detail=f"未知规则类型 {rc.type}，已跳过", desc=rc.desc)
                )
                continue

            try:
                outcome: Outcome = checker(records, rc.field, rc.params or {}, ctx)
                status = PASS if outcome.passed else (WARN if rc.level == "warn" else FAIL)
            except Exception as err:  # 单条规则出错不能拖垮整批巡检
                logger.exception("规则 %s 执行异常", rc.id)
                outcome = Outcome(False, detail=f"规则执行异常：{err}")
                status = WARN

            results.append(
                RuleResult(
                    rule_id=rc.id,
                    rule_type=rc.type,
                    source=source,
                    status=status,
                    observed=outcome.observed,
                    threshold=outcome.threshold,
                    detail=outcome.detail,
                    desc=rc.desc,
                )
            )
        return results
