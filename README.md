# Enterprise Data Multi-Agent Benchmark

A compact benchmark framework for evaluating enterprise-style data agents.

This repository contains two related tracks:

* **SQL baseline**: a single LLM SQL agent that converts natural language into SQL.
* **Enterprise multi-agent benchmark**: an agent workflow that grounds a business question into a metric, discovers required data, generates a sufficient query, runs analysis, performs QA, and returns a grounded insight.

The enterprise benchmark is intentionally small for presentation purposes. It uses a separate multi-table SQLite dataset for customer revenue, support exposure, and account ownership, while keeping the workflow compact enough to inspect and run.

---

# Project Structure

```
.
├── llm_sql_agent.py          # single-agent SQL baseline
├── evaluation-metrics.py     # SQL baseline evaluator
├── test_cases_sql.py         # SQL baseline test cases
├── multi_agent_agents.py     # enterprise multi-agent workflow roles
├── multi_agent_benchmark.py  # enterprise multi-agent benchmark runner
├── multi_agent_dataset.py    # enterprise multi-table benchmark dataset
├── test_cases_multi_agent.py # enterprise workflow test cases
├── employee_dataset.py       # SQLite employee dataset
├── config_local.py           # OpenAI-compatible LLM configuration
├── visualize_radar.py        # radar chart for SQL baseline results
├── requirements.txt
└── README.md
```

---

# Dataset

The SQL baseline still uses the original `employee_dataset.py`.

The enterprise multi-agent benchmark uses a separate SQLite database defined in `multi_agent_dataset.py`:

```text
customers(customer_id, customer_name, segment, region, status, annual_contract_value, is_internal)
orders(order_id, customer_id, order_date, amount, order_status)
support_tickets(ticket_id, customer_id, created_at, severity, ticket_status, resolution_hours)
account_managers(customer_id, manager_name, team)
```

It is loaded into `enterprise_company.db` during benchmark initialization. The dataset includes realistic enterprise constraints such as internal test accounts, active/churned/at-risk customer status, closed revenue, support exposure, and account manager ownership.

---

# SQL Baseline

The SQL baseline evaluates a single LLM SQL agent:

```text
Natural Language Question
        │
        ▼
   LLM SQL Agent
        │
        ▼
   Generated SQL
        │
        ▼
 Execute on SQLite
        │
        ▼
 External Evaluator
```

Run it with:

```bash
python evaluation-metrics.py
```

This track uses:

* `llm_sql_agent.py`
* `test_cases_sql.py`
* `evaluation-metrics.py`

It reports SQL-focused metrics such as accuracy, result completeness, query efficiency, latency efficiency, token efficiency, and robustness.

---

# Enterprise Multi-Agent Benchmark

The enterprise benchmark evaluates a workflow rather than a single SQL generation step.

MVP agent roles:

* **Task Manager Agent**: classifies the task and chooses a route such as query only or query + analysis.
* **Metric Agent**: maps business wording to a metric definition and business rules.
* **Data Discovery Agent**: identifies the table, columns, grain, and data contract required by the metric.
* **Query Agent**: generates a query that satisfies the data contract.
* **Analysis Agent**: computes statistical or chart-ready results from query rows.
* **QA & Insight Agent**: checks workflow consistency and produces a grounded answer.

Workflow:

```text
Natural Language Task
        │
        ▼
 Task Manager Agent
        │
        ▼
   Metric Agent
        │
        ▼
 Data Discovery Agent
        │
        ▼
    Query Agent
        │
        ▼
   Analysis Agent
        │
        ▼
 QA & Insight Agent
        │
        ▼
 External Evaluator
```

Example enterprise test case:

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

# Enterprise Metrics

The multi-agent benchmark reports:

* **Final Accuracy**: whether the end-to-end workflow result is correct.
* **Tool Routing Accuracy**: whether Task Manager selected the expected route.
* **Metric Grounding Accuracy**: whether Metric Agent selected the expected business metric.
* **Data Discovery Accuracy**: whether Data Discovery Agent selected the expected table and columns.
* **Query Sufficiency**: whether Query Agent retrieved enough data for downstream analysis.
* **Query Correctness**: whether direct query tasks return the expected rows.
* **Analysis Correctness**: whether statistical or chart-data analysis is correct.
* **QA Pass Rate**: whether QA finds the run internally consistent.
* **Result Completeness**: whether required facts appear in the final answer.
* **Robust Final Accuracy**: final accuracy on noisy versions of the same questions.
* **Robust Drop**: clean final accuracy minus noisy final accuracy.
* **Average Latency / Round Count / Tool Calls**: collaboration efficiency.

Example summary:

```json
{
  "total": 6,
  "final_accuracy": 1.0,
  "tool_routing_accuracy": 1.0,
  "metric_grounding_accuracy": 1.0,
  "data_discovery_accuracy": 1.0,
  "query_sufficiency": 1.0,
  "query_correctness": 1.0,
  "analysis_correctness": 1.0,
  "qa_pass_rate": 1.0,
  "result_completeness": 1.0,
  "robust_final_accuracy": 0.6667,
  "robust_drop": 0.3333
}
```

---

# Running The Enterprise Benchmark

Run the deterministic reference workflow:

```bash
python multi_agent_benchmark.py
```

This requires no API key and is useful for validating the benchmark harness.

Run the LLM-backed workflow:

```bash
python multi_agent_benchmark.py --agent-mode llm
```

LLM mode uses the OpenAI-compatible configuration in `config_local.py`.

To reduce cost during testing:

```bash
python multi_agent_benchmark.py --agent-mode llm --limit 2 --skip-robust
```

By default, LLM mode uses:

* LLM Task Manager Agent
* LLM Metric Agent
* LLM Data Discovery Agent
* LLM Query Agent
* deterministic Analysis executor
* rule-based QA Agent
* rule-based Insight Agent

The deterministic Analysis executor keeps statistical computation tool-grounded instead of relying on model memory.

Default outputs:

```text
benchmark_results_multi_agent.json
benchmark_results_multi_agent_llm.json
```

---

# Configuration

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure your OpenAI-compatible API in `config_local.py`:

```python
LLM_MODEL = "deepseek-chat"
LLM_API_KEY = "your_api_key"
LLM_BASE_URL = "https://api.deepseek.com/v1"
```

Do not commit real API keys.

---

# Visualization

The radar chart currently targets the SQL baseline output:

```bash
python visualize_radar.py
```

It reads `benchmark_results.json`.

---

# Current Scope And Future Work

Current scope:

* separate multi-table SQLite enterprise dataset
* compact metric glossary
* six enterprise workflow test cases
* table/column discovery over one enterprise schema
* query-only and query + analysis tasks
* external evaluator for deterministic scoring

Future work:

* multi-table enterprise schemas
* richer metric documentation and business glossary retrieval
* permission and governance constraints
* QA fault-injection evaluation
* self-correction loops after QA failures
* structured Insight Agent output with evidence and assumptions

---

# License

This project is intended for research and educational purposes.
