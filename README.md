# 数据质量监控平台（dq-monitor）

一个可上线的轻量数据质量巡检系统：定时采集多个公开数据源，用配置化的规则校验数据质量，异常结果进看板并通过企业微信 / Server酱告警。

面向数据开发、测试开发、数据运维方向的作品集项目，刻意做小、做完整，每一层都能在面试里被追问。

## 亮点

- **配置驱动**：数据源和规则全部写在 YAML 里，新增一个源 / 一条规则不改代码
- **9 种规则类型**：非空率、唯一性、数值区间、枚举白名单、格式正则、字段漂移、数据时效、环比波动、行数水位
- **幂等采集**：以业务主键 upsert，重复跑不会产生重复数据；每个批次单独记账，失败也留痕
- **告警可克制**：同一规则在抑制窗口内只推送一次，避免告警风暴
- **可验证**：28 个测试，覆盖每条规则的边界 + 流水线 + 看板渲染

## 架构

```mermaid
flowchart LR
    A[采集<br/>http_json / demo] --> B[入库<br/>SQLite 幂等 upsert]
    B --> C[规则引擎<br/>YAML 配置驱动]
    C --> D[看板<br/>Streamlit]
    C --> E[告警<br/>webhook + 抑制窗口]
    B --> F[(batches 批次表<br/>含批次汇总指标)]
    F --> C
```

四个表各司其职：`batches` 记批次与批次级汇总（环比规则的基线来源）、`records` 只留最新快照、`rule_results` 供看板查询、`alert_state` 做告警去重。

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m dq.cli init            # 建表
python -m dq.cli seed --days 14  # 造 14 天演示数据（离线源，不需要联网）
python -m dq.cli serve           # 浏览器打开 http://localhost:8501
```

真实数据源（需要外网）：

```bash
python -m dq.cli run                 # 跑全部数据源
python -m dq.cli run --source github_repo
python -m dq.cli run --source demo_order --anomaly 1   # 注入值级异常，看规则怎么报
python -m dq.cli run --source demo_order --anomaly 2   # 注入字段漂移
python -m dq.cli rules               # 打印规则清单
```

## 目录结构

```
dq-monitor/
├── config/
│   ├── sources.yaml        # 数据源定义（url / 字段映射 / 主键）
│   └── rules.yaml          # 规则定义（类型 / 字段 / 阈值 / 级别）
├── dq/
│   ├── config.py           # YAML -> 强类型对象，环境变量覆盖
│   ├── pipeline.py         # 采集 -> 落库 -> 校验 -> 告警 的编排
│   ├── cli.py              # 命令行入口
│   ├── collectors/         # 采集器（http_json / demo），注册表模式
│   ├── storage/            # 建表 SQL + SQLite 访问层
│   ├── rules/              # checks.py（规则实现）+ engine.py（调度）
│   ├── alerting/           # webhook 推送 + 去重
│   └── dashboard/          # Streamlit 看板
├── tests/                  # 规则单测 / 流水线集成测试 / 看板冒烟测试
└── .github/workflows/      # CI + 每日巡检
```

## 换成你自己的数据源

改 `config/sources.yaml`，三步：

```yaml
sources:
  - name: my_api
    type: http_json
    url: "https://your-api/xxx"
    list_path: data.items        # 列表在返回体里的路径，支持 a.b.c；根节点就是列表时留空
    key_field: id                # 业务主键，决定幂等粒度
    fields:                      # 左边是你的字段名，右边是接口返回的字段路径
      id: id
      amount: payload.amount
      created_at: createTime
```

如果返回结构特殊（XML、分页、需要签名），在 `dq/collectors/` 加一个子类，用 `@register` 注册即可，流水线无需改动。

## 加一条规则

```yaml
- id: order.amount.range
  type: range
  source: demo_order
  field: amount
  level: fail            # fail 触发告警，warn 只上板
  params: { min: 0.01, max: 100000 }
  desc: 订单金额需在合理区间
```

| 类型 | 用途 | params |
| --- | --- | --- |
| `not_null` | 非空率 | `min_pass_rate` |
| `unique` | 主键唯一 | — |
| `range` | 数值区间 | `min` / `max` / `allow_null` |
| `enum` | 枚举白名单 | `allowed[]` / `allow_null` |
| `pattern` | 格式正则 | `regex` |
| `schema` | 字段漂移 | `expected_fields[]` |
| `freshness` | 数据时效 | `max_lag_hours` |
| `volatility` | 环比波动 | `agg` / `metric` / `max_change_pct` |
| `row_count` | 行数水位 | `min_rows` / `max_rows` |


## 已知取舍

- 明细表只存最新快照，不做全量历史（历史靠批次汇总指标），这是为了控制体积；真要留历史，加一张 `records_history` 按批次存即可。
- 没有用户体系与权限，看板是只读的，公开链接即可访问。
- 调度依赖 GitHub Actions 的 cron，没有自建调度器；cron 有几分钟抖动，对日级巡检够用。
