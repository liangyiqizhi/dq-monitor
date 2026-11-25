"""离线演示源：不依赖网络，用于本地跑通链路、CI 回归、以及给看板造历史趋势。"""

from __future__ import annotations

import random
from datetime import timedelta

from .base import BaseCollector, register

STATUS_POOL = ["paid", "paid", "paid", "pending", "refunded"]
CHANNEL_POOL = ["app", "miniapp", "web"]

# anomaly_level: 0 干净数据 / 1 值级异常（空值、越界、非法枚举、重复） / 2 结构级异常（字段漂移）
CLEAN, VALUE_ANOMALY, SCHEMA_DRIFT = 0, 1, 2


@register
class DemoCollector(BaseCollector):
    type_name = "demo"

    def fetch(self) -> list[dict]:
        opts = self.cfg.options
        rows = int(opts.get("rows", 60))
        seed = f"{opts.get('seed', 0)}-{self.now.date().isoformat()}-{self.anomaly_level}"
        rnd = random.Random(seed)

        records: list[dict] = []
        for idx in range(rows):
            created_at = self.now - timedelta(minutes=rnd.randint(0, 60 * 20))
            records.append(
                {
                    "order_id": f"ORD{rnd.randint(100000, 999999)}{idx:03d}",
                    "amount": round(rnd.uniform(9.9, 3000), 2),
                    "status": rnd.choice(STATUS_POOL),
                    "channel": rnd.choice(CHANNEL_POOL),
                    "created_at": created_at.replace(microsecond=0).isoformat(),
                }
            )

        if self.anomaly_level == VALUE_ANOMALY:
            records.append(dict(records[0]))          # 重复订单号
            records[1]["amount"] = None               # 金额缺失
            records[2]["amount"] = 999999.0           # 金额越界
            records[3]["status"] = "void"             # 非法枚举
        elif self.anomaly_level == SCHEMA_DRIFT:
            for rec in records:
                rec.pop("status", None)               # 上游删字段

        return records
