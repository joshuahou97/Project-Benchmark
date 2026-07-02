# 企业数据多智能体 Benchmark

这是一个用于评估企业数据场景下 LLM Agent 的轻量级 Benchmark。

项目包含两条评测线：

* **SQL Baseline**：单智能体把自然语言问题转换成 SQL。
* **企业多智能体 Benchmark**：多个 Agent 协作完成业务问题理解、指标口径识别、数据发现、查询生成、分析计算、QA 和洞察输出。

当前版本是演示友好的 MVP：企业多智能体部分使用单独的多表 SQLite 数据集，覆盖客户、订单、支持工单和客户经理归属；同时保持实现足够小，方便演讲和代码检查。

---

# 项目结构

```
.
├── llm_sql_agent.py          # 单 SQL Agent baseline
├── evaluation-metrics.py     # SQL baseline 评测脚本
├── test_cases_sql.py         # SQL baseline 测试用例
├── multi_agent_agents.py     # 企业多智能体工作流角色
├── multi_agent_benchmark.py  # 企业多智能体评测入口
├── multi_agent_dataset.py    # 企业多表 benchmark 数据集
├── test_cases_multi_agent.py # 企业工作流测试用例
├── employee_dataset.py       # SQLite 员工数据集
├── config_local.py           # OpenAI-compatible LLM 配置
├── visualize_radar.py        # SQL baseline 雷达图
├── requirements.txt
└── README.md
```

---

# 数据集

SQL baseline 仍然使用原来的 `employee_dataset.py`。

企业多智能体 benchmark 使用 `multi_agent_dataset.py` 中定义的独立多表数据库：

```text
customers(customer_id, customer_name, segment, region, status, annual_contract_value, is_internal)
orders(order_id, customer_id, order_date, amount, order_status)
support_tickets(ticket_id, customer_id, created_at, severity, ticket_status, resolution_hours)
account_managers(customer_id, manager_name, team)
```

运行时会写入 `enterprise_company.db`。该数据集包含 internal test account、active/churned/at-risk 客户状态、closed revenue、支持工单暴露和客户经理归属等企业数据约束。

---

# SQL Baseline

SQL baseline 评估单个 LLM SQL Agent：

```text
自然语言问题
    ↓
LLM SQL Agent
    ↓
生成 SQL
    ↓
SQLite 执行
    ↓
外部 Evaluator 打分
```

运行：

```bash
python evaluation-metrics.py
```

这一部分主要复用旧项目，用于作为 Query Agent 能力的 baseline。

---

# 企业多智能体 Benchmark

企业多智能体 benchmark 评估的是完整数据工作流，而不是单条 SQL 生成。

MVP Agent 角色：

* **Task Manager Agent**：判断任务类型，选择 query only 或 query + analysis。
* **Metric Agent**：把业务表达映射到指标口径和业务规则。
* **Data Discovery Agent**：选择需要的数据表、字段、粒度和数据契约。
* **Query Agent**：生成满足数据契约的查询。
* **Analysis Agent**：基于查询结果计算统计值或图表数据。
* **QA Agent**：检查流程一致性、数据契约覆盖、查询输出充分性和分析结果形态。
* **Insight Agent**：把查询或分析结果转成有依据的业务结论。

Metric Agent 和 Data Discovery Agent 被刻意拆开：Metric Agent 负责回答“业务指标是什么意思”，Data Discovery Agent 负责回答“数据在哪里、如何取”，包括 join key、filter、grain 和下游查询契约。

工作流：

```text
业务问题
  ↓
Task Manager Agent
  ↓
Metric Agent
  ↓
Data Discovery Agent
  ↓
Query Agent
  ↓
Analysis Agent
  ↓
QA Agent
  ↓
Insight Agent
  ↓
外部 Evaluator
```

测试用例示例：

```python
MultiAgentCase(
    id="manager_revenue_concentration",
    question="Show closed revenue by account manager and identify the manager with the highest concentration.",
    expected_route=("query", "analysis"),
    expected_metric="manager_revenue_concentration",
    expected_tables=("customers", "orders", "account_managers"),
    expected_columns=("customer_id", "manager_name", "is_internal", "amount", "order_status"),
    expected_query="SELECT am.manager_name, o.amount FROM ...",
    expected_output_type="analysis_result",
    expected={
        "analysis": "group_sum_max",
        "label": "Maya Chen",
        "value": 116000,
    },
)
```

---

# 企业多智能体指标

当前评测指标包括：

* **Final Accuracy**：端到端结果是否正确。
* **Route Exact Match Accuracy**：Task Manager 是否选择了完全一致的流程。
* **Route Coverage Accuracy**：规划流程是否覆盖所有必要 Agent。
* **Average Unnecessary Step Count**：平均每个 case 多调用了多少不必要步骤。
* **Metric Grounding Accuracy**：Metric Agent 是否识别了正确业务指标。
* **Data Discovery Accuracy**：Data Discovery Agent 是否覆盖必要表、字段、join 和 filter。
* **Data Discovery Column Precision**：DataDiscovery 字段契约是否避免不必要字段。
* **Query Sufficiency**：Query Agent 是否取到了足够支持后续分析的数据。
* **Query Correctness**：查询结果是否能支持 expected answer。
* **Query Result Precision**：查询输出是否避免不必要行或列。
* **Analysis Correctness**：分析计算或图表数据是否正确。
* **Result Completeness**：最终回答是否覆盖必要事实。
* **Robust Final Accuracy**：噪声输入下的端到端准确率。
* **Robust Drop**：clean accuracy 与 noisy accuracy 的差距。
* **Average Latency / LLM Call Count**：协作成本。

示例 summary：

```json
{
  "total": 6,
  "final_accuracy": 1.0,
  "route_exact_match_accuracy": 1.0,
  "route_coverage_accuracy": 1.0,
  "avg_unnecessary_step_count": 0.0,
  "metric_grounding_accuracy": 1.0,
  "data_discovery_accuracy": 1.0,
  "data_discovery_column_precision": 1.0,
  "query_sufficiency": 1.0,
  "query_correctness": 1.0,
  "query_result_precision": 1.0,
  "analysis_correctness": 1.0,
  "result_completeness": 1.0,
  "robust_final_accuracy": 0.6667,
  "robust_drop": 0.3333,
  "avg_latency_sec": 0.0001,
  "avg_llm_call_count": 0.0
}
```

---

# 运行企业多智能体 Benchmark

运行 deterministic reference workflow：

```bash
python multi_agent_benchmark.py
```

运行 LLM-backed workflow：

```bash
python multi_agent_benchmark.py --agent-mode llm
```

减少 LLM 测试成本：

```bash
python multi_agent_benchmark.py --agent-mode llm --limit 2 --skip-robust
```

默认 LLM 模式使用：

* LLM Task Manager Agent
* LLM Metric Agent
* LLM Data Discovery Agent
* LLM Query Agent
* LLM QA Agent
* LLM Insight Agent
* deterministic Analysis executor

Analysis executor 默认是确定性工具，避免让模型凭记忆做统计计算。
QA 会先跑确定性 guardrail，再交给 LLM 做语义审查，因此明显的流程错误仍然能稳定捕捉。Insight 在 LLM 模式下由模型生成简洁的业务化回答，并保留确定性 fallback。

---

# 配置

安装依赖：

```bash
pip install -r requirements.txt
```

在 `config_local.py` 中配置 OpenAI-compatible API：

```python
LLM_MODEL = "deepseek-chat"
LLM_API_KEY = "your_api_key"
LLM_BASE_URL = "https://api.deepseek.com/v1"
```

不要提交真实 API key。

---

# 当前范围和后续优化

当前范围：

* 独立多表 SQLite 企业数据集
* 小型 metric glossary
* 6 个企业 workflow 测试用例
* 单企业 schema 下的数据发现
* query-only 与 query + analysis 任务
* 外部 evaluator 确定性打分

后续优化：

* 多表企业 schema
* 更丰富的指标文档和业务 glossary 检索
* 权限和治理约束
* QA fault-injection 评测
* QA 失败后的 self-correction loop
* 结构化 Insight 输出，包括证据、假设和限制
