"""告警通知：webhook 推送 + 抑制窗口去重（同一规则在窗口内只打扰一次）。"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from ..config import Settings
from ..rules.engine import FAIL, RuleResult
from ..storage.db import Database

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db

    def notify(self, source: str, results: list[RuleResult], now: datetime) -> list[str]:
        """返回本次实际发出的告警 key 列表（被抑制的不算）。"""
        failures = [r for r in results if r.status == FAIL]
        if not failures:
            return []

        dedup_seconds = self.settings.alert_dedup_minutes * 60
        pending: list[tuple[str, RuleResult]] = []
        for result in failures:
            key = f"{source}:{result.rule_id}"
            last = self.db.alert_last_sent(key)
            if last and (now - last).total_seconds() < dedup_seconds:
                logger.info("告警抑制：%s 在 %d 分钟窗口内已推送过", key, self.settings.alert_dedup_minutes)
                continue
            pending.append((key, result))

        if not pending:
            return []

        if not self.settings.alert_webhook:
            logger.warning("[dry-run] 未配置 DQ_ALERT_WEBHOOK，本应推送 %d 条告警：%s",
                           len(pending), [r.rule_id for _, r in pending])
            return []

        text = self._render(source, [r for _, r in pending], now)
        if not self._post(text):
            return []

        sent = []
        for key, _ in pending:
            self.db.mark_alert_sent(key, now)
            sent.append(key)
        return sent

    @staticmethod
    def _render(source: str, failures: list[RuleResult], now: datetime) -> str:
        lines = [
            f"### 数据质量告警 · {source}",
            f"> 巡检时间：{now.isoformat(timespec='seconds')}",
            f"> 未通过规则：{len(failures)} 条",
        ]
        for r in failures:
            lines.append(f"- **{r.rule_id}** {r.desc}\n  {r.detail}")
        return "\n".join(lines)

    def _post(self, text: str) -> bool:
        kind = self.settings.alert_kind
        url = self.settings.alert_webhook
        try:
            if kind == "wecom":
                payload = {"msgtype": "markdown", "markdown": {"content": text}}
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            elif kind == "serverchan":
                form = urllib.parse.urlencode({"title": "数据质量告警", "desp": text}).encode("utf-8")
                req = urllib.request.Request(url, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"})
            else:  # generic：自定义服务，收 {"text": "..."}
                data = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            logger.info("告警已推送（%s）：%s", kind, body[:200])
            return True
        except (urllib.error.URLError, OSError, TimeoutError) as err:
            logger.error("告警推送失败：%s", err)
            return False
