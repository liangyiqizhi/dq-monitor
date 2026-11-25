"""看板冒烟测试：用 Streamlit 的 AppTest 无头执行页面脚本，确保改完代码页面不会白屏。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from streamlit.testing.v1 import AppTest

from dq.config import load_settings
from dq.pipeline import run_source

APP = Path(__file__).resolve().parents[1] / "dq" / "dashboard" / "app.py"


def test_dashboard_renders_with_data(tmp_path, monkeypatch):
    settings = replace(load_settings(), db_path=tmp_path / "dash.db", alert_webhook="")
    source = settings.source("demo_order")
    run_source(settings, source, notify=False)
    run_source(settings, source, anomaly_level=1, notify=False)  # 制造红黄点，验证异常分支也能渲染

    monkeypatch.setenv("DQ_DB", str(settings.db_path))
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()

    assert not at.exception, [e.value for e in at.exception]
    assert len(at.metric) == 4
    assert len(at.tabs) == 4


def test_dashboard_handles_empty_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DQ_DB", str(tmp_path / "missing.db"))
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()

    assert not at.exception, [e.value for e in at.exception]
    assert at.warning  # 数据库不存在时给出引导，而不是报错
