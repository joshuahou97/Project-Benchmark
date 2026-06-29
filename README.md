
---

# LLM SQL and Multi-Agent Benchmark

A lightweight benchmark framework for evaluating **LLM-powered SQL agents** and
**multi-agent SQL + Python data analysis workflows**.

This project builds a SQL agent using a Large Language Model (LLM) via an OpenAI-compatible API (e.g. DeepSeek or Qwen) and evaluates its ability to translate natural language questions into executable SQL queries.

The extended multi-agent benchmark evaluates whether a Planner Agent can route a task to SQL only or SQL + Python analysis, and whether the full agent team can produce a correct final answer.

The benchmark measures multiple dimensions of performance including **accuracy, completeness, tool routing, SQL correctness, Python correctness, robustness, and latency**.

---

# Project Structure

```
.
├── llm_sql_agent.py          # SQL agent implementation
├── evaluation-metrics.py     # SQL benchmark evaluation pipeline
├── test_cases_sql.py         # benchmark test cases
├── multi_agent_agents.py     # planner, SQL, Python, verifier, reporter agents
├── multi_agent_benchmark.py  # multi-agent benchmark runner
├── test_cases_multi_agent.py # multi-agent benchmark test cases
├── employee_dataset.py       # SQLite dataset
├── config_local.py           # LLM API configuration
├── visualize_radar.py        # radar chart visualization
├── requirements.txt
└── README.md
```

---

# Features

This project provides:

* LLM-powered SQL agent (LangChain + OpenAI-compatible API)
* SQLite employee dataset
* Automatic benchmark evaluation
* Multiple evaluation metrics
* Robustness testing with noisy queries
* Radar chart visualization
* Planner-based multi-agent routing
* SQL + Python data analysis tasks
* Verifier and trace logging for multi-agent runs

---

# Dataset

The benchmark uses a simple employee database.

The schema is:

| Column | Description   |
| ------ | ------------- |
| name   | employee name |
| dept   | department    |
| title  | job title     |
| salary | salary        |

Example rows:

```
("John Smith", "Engineering", "ML Engineer", 32000)
("Sophia Martinez", "Finance", "Accountant", 20000)
("Alexander Lewis", "Engineering", "Software Architect", 40000)
```

The dataset is defined in:

```
employee_dataset.py
```

and inserted into the SQLite database `company.db` during initialization. 

---

# SQL Agent

The SQL agent is implemented in:

```
llm_sql_agent.py
```

The agent:

1. Connects to the SQLite database
2. Uses LangChain's SQL toolkit
3. Calls an LLM through an OpenAI-compatible interface

The LLM configuration is provided in:

```
config_local.py
```

Example configuration: 

```python
LLM_MODEL = "deepseek-chat"
LLM_API_KEY = "your_api_key"
LLM_BASE_URL = "https://api.deepseek.com/v1"
```

---

# SQL Benchmark Tasks

Benchmark tasks are defined in:

```
test_cases_sql.py
```

Each benchmark case includes:

```
Case(
    id="avg_salary_engineering",
    question="What is the average salary in Engineering?",
    gold_sql="SELECT AVG(salary) FROM employees WHERE dept='Engineering';"
)
```

The benchmark contains multiple types of SQL reasoning tasks:

### Basic SQL reasoning

* filtering
* ordering
* aggregation
* grouping

### Intermediate queries

* nested queries
* logical conditions
* department statistics

### Edge cases

* empty result sets

Example tasks include:

* highest salary employee
* employees with salary > X
* average salary per department
* departments above company average
* highest salary per department

---

# Multi-Agent Benchmark

The multi-agent benchmark extends the SQL benchmark into a small data analysis workflow.

The default local implementation is deterministic so it can run without an API key. It is designed as a reproducible reference pipeline that can later be replaced by real LLM agents.

The agent roles are:

* **Planner Agent**: decides whether a task should use SQL only or SQL + Python.
* **SQL Agent**: generates a SQL query for database retrieval.
* **Python Agent**: computes statistical analysis or chart-ready data from SQL rows.
* **Verifier Agent**: checks route and artifact consistency.
* **Reporter Agent**: produces the final answer.

Pipeline:

```
Natural Language Task
        │
        ▼
   Planner Agent
        │
        ├── SQL only
        │
        └── SQL + Python
        │
        ▼
   Verifier Agent
        │
        ▼
   Reporter Agent
        │
        ▼
 Evaluation Metrics
```

Multi-agent tasks are defined in:

```
test_cases_multi_agent.py
```

Each case includes:

```python
MultiAgentCase(
    id="salary_variance_department",
    question="Which department has the largest salary variance? Include the variance value.",
    gold_route=("sql", "python"),
    gold_sql="SELECT dept, salary FROM employees;",
    answer_type="python_result",
    expected={
        "analysis": "group_variance_max",
        "label": "Finance",
        "value": 37555555.55555555,
    },
)
```

---

# Benchmark Pipeline

The benchmark evaluates the ability of the agent to convert **natural language questions into correct SQL queries**.

Evaluation process:

1. Provide a natural language question to the SQL agent.
2. The LLM generates SQL queries.
3. The queries are executed on the SQLite database.
4. The result is compared with the ground truth SQL result.
5. Evaluation metrics are computed.

Pipeline:

```
Natural Language Question
        │
        ▼
   LLM SQL Agent
        │
        ▼
   Generated SQL
        │
        ▼
Execute on SQLite Database
        │
        ▼
   Agent Result
        │
        ▼
Compare with Ground Truth
        │
        ▼
 Evaluation Metrics
```

---

# Evaluation Metrics

The benchmark measures several aspects of agent performance.

### Accuracy

Whether the agent returns the correct SQL result compared with the ground truth.

---

### Result Completeness

Measures how much of the expected result set is returned.

```
matched_rows / total_gold_rows
```

---

### Query Efficiency

Measures how many SQL queries the agent executes.

Fewer queries indicate more efficient reasoning.

---

### Latency Efficiency

Measures response time for each query.

---

# Multi-Agent Metrics

The multi-agent benchmark reports:

* **Final Accuracy**: whether the end-to-end answer is correct.
* **Tool Routing Accuracy**: whether Planner selected the expected route, such as SQL only or SQL + Python.
* **SQL Correctness**: whether SQL retrieval returns the expected rows for SQL-only tasks.
* **Python Correctness**: whether statistical or chart-data analysis is correct.
* **Verifier Pass Rate**: whether the verifier finds the run internally consistent.
* **Result Completeness**: whether required facts appear in the final answer.
* **Robust Final Accuracy**: final accuracy on noisy versions of the same questions.
* **Robust Drop**: clean final accuracy minus noisy final accuracy.
* **Average Latency / Round Count / Tool Calls**: collaboration efficiency.

---

# Running the Benchmarks

Run the original SQL benchmark:

```
python evaluation-metrics.py
```

This requires a configured OpenAI-compatible LLM in:

```
config_local.py
```

Run the local reproducible multi-agent benchmark:

```
python multi_agent_benchmark.py
```

This creates:

```
benchmark_results_multi_agent.json
```

Example multi-agent summary:

```json
{
  "total": 8,
  "final_accuracy": 1.0,
  "tool_routing_accuracy": 1.0,
  "sql_correctness": 1.0,
  "python_correctness": 1.0,
  "verifier_pass_rate": 1.0,
  "result_completeness": 1.0,
  "robust_final_accuracy": 0.625,
  "robust_drop": 0.375
}
```

Lower latency indicates faster reasoning and tool usage.

---

### Token Efficiency

Measures the token usage of the LLM during inference.

Lower token consumption indicates more efficient prompts and reasoning.

---

### Robustness

Evaluates performance when the input question contains noise such as:

* character deletions
* character swaps
* keyboard typos
* word reordering

This simulates real-world user input errors.

---

# Installation

### Recommended Python version

```
Python 3.10 – 3.11
```

---

### Install dependencies

```
pip install -r requirements.txt
```

Main dependencies include: 

```
langchain
langchain-community
langchain-openai
sqlalchemy
```

---

# Configuration

Before running the benchmark, configure your LLM API in:

```
config_local.py
```

Example:

```python
LLM_MODEL = "deepseek-chat"
LLM_API_KEY = "your_api_key"
LLM_BASE_URL = "https://api.deepseek.com/v1"
```

This file should **not be committed to GitHub** because it contains API keys.

---

# Running the SQL Benchmark

Run the benchmark script:

```
python evaluation-metrics.py
```

The script will:

1. initialize the SQLite database
2. build the SQL agent
3. run all benchmark test cases
4. compute evaluation metrics
5. save results to:

```
benchmark_results.json
```

---

# Visualization

To visualize benchmark results, run:

```
python visualize_radar.py
```

This script reads:

```
benchmark_results.json
```

and generates a **radar chart** showing:

* Accuracy
* Completeness
* Query Efficiency
* Latency Efficiency
* Token Efficiency
* Robustness

---

# Example Benchmark Output

Example summary:

```
{
  "total": 15,
  "passed": 13,
  "accuracy": 0.867,
  "result_completeness": 0.91,
  "query_efficiency": 0.83,
  "latency_efficiency": 0.79,
  "token_efficiency": 0.81,
  "robust_accuracy": 0.72
}
```

The radar chart provides an intuitive overview of agent performance across these dimensions.

---

# Possible Extensions

This benchmark framework can be extended with:

* larger SQL datasets
* multi-table database schemas
* schema linking evaluation
* adversarial natural language queries
* cross-model comparison (GPT, Gemini, DeepSeek, Qwen)

---

# License

This project is intended for **research and educational purposes**.

---
