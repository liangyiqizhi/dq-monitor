"""Streamlit 看板：只用 SQL 查询 + 图表，不写业务逻辑（业务都在 pipeline 里）。

本地启动：python -m dq.cli serve
云端部署：streamlit run dq/dashboard/app.py（Streamlit Cloud / Docker 均可用）
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="数据质量监控平台", layout="wide")

DEFAULT_DB = os.getenv("DQ_DB") or str(Path(__file__).resolve().parents[2] / "data" / "dq.db")


@st.cache_data(ttl=30, show_spinner=False)
def query(db_path: str, sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


st.title("数据质量监控平台")
st.caption("采集 → 幂等落库 → 规则校验 → 看板与告警。规则与数据源均由 YAML 配置驱动。")

with st.sidebar:
    st.header("设置")
    db_path = st.text_input("SQLite 数据库", DEFAULT_DB)
    days = st.selectbox("统计窗口", [7, 14, 30, 90], index=1, format_func=lambda d: f"最近 {d} 天")
    if st.button("刷新数据", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.caption("命令行：`python -m dq.cli run` 执行一次巡检")

if not Path(db_path).exists():
    st.warning("数据库不存在。先跑一次巡检或造历史数据：")
    st.code("python -m dq.cli init\npython -m dq.cli seed --days 14\npython -m dq.cli serve", language="bash")
    st.stop()

batches = query(db_path, "SELECT * FROM batches ORDER BY started_at DESC")
if batches.empty:
    st.warning("还没有巡检批次。执行 `python -m dq.cli seed --days 14` 生成演示数据。")
    st.stop()

results = query(db_path, "SELECT * FROM rule_results WHERE created_at >= ? ORDER BY created_at DESC", (since(days),))
if results.empty:
    st.info("统计窗口内没有规则结果，试试放大窗口或重新巡检。")
    st.stop()

latest_batch = batches.iloc[0]
window_total = len(results)
pass_rate = (results["status"] == "pass").mean()
fail_rules = results.loc[results["status"] == "fail", "rule_id"].nunique()
sources = sorted(results["source"].unique())

col1, col2, col3, col4 = st.columns(4)
col1.metric("最近巡检", str(latest_batch["started_at"])[5:16].replace("T", " "), f"{latest_batch['source']}")
col2.metric("规则通过率", f"{pass_rate:.1%}", f"{window_total} 次校验")
col3.metric("存在失败的规则数", f"{fail_rules}", f"{len(sources)} 个数据源")
col4.metric("最新批次行数", f"{int(latest_batch['row_count'])}", str(latest_batch["status"]))

tab_overview, tab_health, tab_rules, tab_batches = st.tabs(["全局概览", "数据源健康度", "规则明细", "批次记录"])

with tab_overview:
    trend = results.copy()
    trend["日期"] = trend["created_at"].str[:10]
    daily = (
        trend.pivot_table(index="日期", columns="status", values="id", aggfunc="count")
        .fillna(0)
        .rename(columns={"pass": "通过", "warn": "警告", "fail": "失败"})
        .sort_index()
    )
    for col in ("通过", "警告", "失败"):
        if col not in daily:
            daily[col] = 0
    st.subheader("每日异常趋势")
    st.line_chart(daily[["失败", "警告"]], height=260)
    st.caption("失败 = 阻断级规则未通过（触发告警）；警告 = 只上板不打扰的规则未通过。")

    st.subheader("规则通过率排行")
    rank = (
        results.groupby(["rule_id", "source"])["status"]
        .apply(lambda s: (s == "pass").mean())
        .reset_index(name="通过率")
        .sort_values("通过率")
        .set_index("rule_id")
    )
    st.bar_chart(rank["通过率"], height=300, color="#E24B4A")
    st.caption("柱越短说明这条规则越常不通过，优先去查它对应的上游数据。")

with tab_health:
    st.subheader("各数据源最新状态")
    latest = (
        batches.sort_values("started_at")
        .groupby("source", as_index=False)
        .last()
        .loc[:, ["source", "status", "row_count", "started_at", "finished_at", "error"]]
    )
    latest["耗时(秒)"] = latest.apply(
        lambda r: round(
            (pd.to_datetime(r["finished_at"]) - pd.to_datetime(r["started_at"])).total_seconds(), 2
        )
        if pd.notna(r["finished_at"])
        else None,
        axis=1,
    )
    latest = latest.rename(
        columns={"source": "数据源", "status": "状态", "row_count": "行数", "started_at": "开始时间", "error": "错误"}
    )
    st.dataframe(latest, width="stretch", hide_index=True)

    st.subheader("各源行数趋势")
    rows = batches[batches["status"] == "ok"].copy()
    rows["日期"] = rows["started_at"].str[:10]
    volume = rows.pivot_table(index="日期", columns="source", values="row_count", aggfunc="last").sort_index()
    st.line_chart(volume, height=260)
    st.caption("行数突然掉一截，通常意味着上游截断或接口分页变化，看板比报表更早发现。")

with tab_rules:
    st.subheader("规则维度统计")
    stats = (
        results.groupby(["rule_id", "source", "rule_type"])
        .agg(校验次数=("id", "count"), 失败=("status", lambda s: int((s == "fail").sum())), 警告=("status", lambda s: int((s == "warn").sum())))
        .reset_index()
    )
    stats["通过率"] = (1 - (stats["失败"] + stats["警告"]) / stats["校验次数"]).map(lambda v: f"{v:.0%}")
    st.dataframe(stats.sort_values("失败", ascending=False), width="stretch", hide_index=True)

    st.subheader("最近异常明细")
    abnormal = results[results["status"] != "pass"].head(200)
    if abnormal.empty:
        st.success("窗口内没有异常，全部规则通过。")
    else:
        st.dataframe(
            abnormal[["created_at", "source", "rule_id", "status", "observed", "threshold", "detail"]].rename(
                columns={
                    "created_at": "时间",
                    "source": "数据源",
                    "rule_id": "规则",
                    "status": "状态",
                    "observed": "实测值",
                    "threshold": "阈值",
                    "detail": "说明",
                }
            ),
            width="stretch",
            hide_index=True,
        )

with tab_batches:
    st.subheader("批次记录（最近 100 次）")
    st.dataframe(
        batches.head(100)[["id", "source", "status", "row_count", "started_at", "finished_at", "error"]].rename(
            columns={
                "id": "批次",
                "source": "数据源",
                "status": "状态",
                "row_count": "行数",
                "started_at": "开始",
                "finished_at": "结束",
                "error": "错误",
            }
        ),
        width="stretch",
        hide_index=True,
    )
    st.caption(f"数据库：{db_path}")
