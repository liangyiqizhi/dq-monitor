"""命令行入口：

    python -m dq.cli init                 初始化数据库
    python -m dq.cli run [--source X ...]  巡检（默认跑全部数据源）
    python -m dq.cli seed --days 14        造 14 天历史数据（只用离线 demo 源）
    python -m dq.cli rules                 打印规则清单
    python -m dq.cli serve                 本地起看板
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from .collectors.demo import SCHEMA_DRIFT, VALUE_ANOMALY
from .config import ROOT, load_settings
from .pipeline import RunResult, run_all, utcnow
from .storage.db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
logging.getLogger("urllib3").setLevel(logging.WARNING)

STATUS_ICON = {"ok": "[OK]  ", "failed": "[FAIL]", "pass": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}


def _print_result(res: RunResult) -> None:
    if res.status != "ok":
        print(f"{STATUS_ICON['failed']} {res.source:<16} batch={res.batch_id} 采集失败：{res.error}")
        return
    print(
        f"{STATUS_ICON['ok']} {res.source:<16} batch={res.batch_id} 行数={res.row_count:<5} "
        f"规则 {len(res.results)} 条：通过 {len(res.passed)} / 警告 {len(res.warned)} / 失败 {len(res.failed)}"
    )
    for r in res.failed + res.warned:
        print(f"         {STATUS_ICON[r.status]} {r.rule_id:<26} {r.detail}")
    if res.sent_alerts:
        print(f"         已推送告警：{res.sent_alerts}")


def cmd_init(args) -> int:
    settings = load_settings()
    Database(settings.db_path).init_schema()
    print(f"数据库已初始化：{settings.db_path}")
    print(f"数据源 {len(settings.sources)} 个，规则 {len(settings.rules)} 条")
    return 0


def cmd_run(args) -> int:
    settings = load_settings()
    results = run_all(settings, sources=args.source or None, anomaly_level=args.anomaly)
    for res in results:
        _print_result(res)
    bad = [r for r in results if r.status != "ok" or r.failed]
    print(f"\n巡检完成：{len(results)} 个源，{len(bad)} 个存在问题")
    return 1 if bad else 0


def cmd_seed(args) -> int:
    settings = load_settings()
    demo_sources = [s.name for s in settings.sources if s.type == "demo"]
    if not demo_sources:
        print("配置里没有 type: demo 的数据源，无法造历史数据")
        return 1

    print(f"开始造 {args.days} 天历史数据（源：{demo_sources}）...")
    total = 0
    for offset in range(args.days, -1, -1):
        day_now = utcnow() - timedelta(days=offset)
        # 每 4 天来一次值级异常，每 7 天来一次字段漂移，让趋势图有红有黄
        level = 0
        if offset % 7 == 3:
            level = SCHEMA_DRIFT
        elif offset % 4 == 1:
            level = VALUE_ANOMALY
        results = run_all(settings, sources=demo_sources, now=day_now, anomaly_level=level, notify=False)
        total += len(results)
        for res in results:
            _print_result(res)
    print(f"\n完成，共写入 {total} 个批次。看板启动：python -m dq.cli serve")
    return 0


def cmd_rules(args) -> int:
    settings = load_settings()
    print(f"{'规则 ID':<28}{'类型':<12}{'数据源':<16}{'级别':<6}说明")
    print("-" * 100)
    for r in settings.rules:
        print(f"{r.id:<28}{r.type:<12}{r.source:<16}{r.level:<6}{r.desc}")
    print(f"\n共 {len(settings.rules)} 条规则，覆盖 {len({r.type for r in settings.rules})} 种类型")
    return 0


def cmd_serve(args) -> int:
    app = ROOT / "dq" / "dashboard" / "app.py"
    if not app.exists():
        print(f"看板文件不存在：{app}")
        return 1
    cmd = [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port)]
    print("启动看板：", " ".join(cmd))
    return subprocess.call(cmd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dq", description="数据质量监控平台")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化数据库").set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="执行一次巡检")
    p_run.add_argument("--source", action="append", help="只跑指定数据源，可重复")
    p_run.add_argument("--anomaly", type=int, default=0, choices=[0, 1, 2], help="注入异常等级（仅 demo 源生效）")
    p_run.set_defaults(func=cmd_run)

    p_seed = sub.add_parser("seed", help="造历史数据，用于看板演示")
    p_seed.add_argument("--days", type=int, default=14, help="回溯天数，默认 14")
    p_seed.set_defaults(func=cmd_seed)

    sub.add_parser("rules", help="打印规则清单").set_defaults(func=cmd_rules)

    p_serve = sub.add_parser("serve", help="启动 Streamlit 看板")
    p_serve.add_argument("--port", type=int, default=8501)
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
