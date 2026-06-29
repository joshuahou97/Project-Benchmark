import json
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


DATABASE_SCHEMA = """
Table: employees
Columns:
- name TEXT
- dept TEXT
- title TEXT
- salary INTEGER
""".strip()


@dataclass
class AgentMessage:
    agent: str
    content: Dict[str, Any]


@dataclass
class Plan:
    route: Tuple[str, ...]
    steps: List[Dict[str, str]]
    rationale: str


@dataclass
class LLMCall:
    agent: str
    prompt: str
    output: str
    token_usage: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MultiAgentRun:
    question: str
    plan: Plan
    sql: str
    sql_rows: List[Tuple[Any, ...]]
    sql_columns: List[str]
    python_result: Dict[str, Any]
    final_answer: str
    trace: List[AgentMessage] = field(default_factory=list)
    verifier: Dict[str, Any] = field(default_factory=dict)


def extract_json_object(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in LLM output: {text}")
    return json.loads(match.group(0))


def clean_sql(text: str) -> str:
    sql = text.strip()
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", sql, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        sql = fenced.group(1).strip()
    sql = sql.strip().strip("`")
    sql = re.sub(r"^\s*SQL\s*:\s*", "", sql, flags=re.IGNORECASE)
    sql = sql.split(";")[0].strip() + ";"
    if not sql.lower().startswith("select"):
        raise ValueError(f"Only SELECT statements are allowed. Got: {sql}")
    return sql


class LLMClient:
    """Small LangChain adapter that is imported only when LLM mode is used."""

    def __init__(self):
        try:
            import config_local
            from langchain_openai import ChatOpenAI
        except Exception as exc:
            raise RuntimeError(
                "LLM mode requires config_local.py plus langchain-openai. "
                "Install requirements and configure LLM_MODEL, LLM_API_KEY, and LLM_BASE_URL."
            ) from exc

        placeholders = {
            "Your LLM Model",
            "Your API Key",
            "Your Base URL",
            "",
        }
        if (
            config_local.LLM_MODEL in placeholders
            or config_local.LLM_API_KEY in placeholders
            or config_local.LLM_BASE_URL in placeholders
        ):
            raise RuntimeError(
                "LLM mode is enabled, but config_local.py still contains placeholder values."
            )

        self.llm = ChatOpenAI(
            model=config_local.LLM_MODEL,
            temperature=0,
            api_key=config_local.LLM_API_KEY,
            base_url=config_local.LLM_BASE_URL,
        )
        self.calls: List[LLMCall] = []

    def invoke(self, agent: str, system_prompt: str, user_prompt: str) -> str:
        response = self.llm.invoke(
            [
                ("system", system_prompt),
                ("human", user_prompt),
            ]
        )
        output = response.content if hasattr(response, "content") else str(response)
        token_usage = getattr(response, "usage_metadata", None) or {}
        self.calls.append(
            LLMCall(
                agent=agent,
                prompt=user_prompt,
                output=output,
                token_usage=dict(token_usage) if isinstance(token_usage, dict) else {},
            )
        )
        return output


class PlannerAgent:
    """Routes tasks to SQL only or SQL plus Python analysis."""

    PYTHON_KEYWORDS = {
        "variance",
        "standard deviation",
        "std",
        "gap",
        "range",
        "chart",
        "bar chart",
        "distribution",
        "compare",
        "difference",
    }

    def plan(self, question: str) -> Plan:
        q = question.lower()
        needs_python = any(keyword in q for keyword in self.PYTHON_KEYWORDS)
        route = ("sql", "python") if needs_python else ("sql",)

        steps = [
            {
                "agent": "sql",
                "goal": "Retrieve the database rows needed to answer the user question.",
            }
        ]
        if needs_python:
            steps.append(
                {
                    "agent": "python",
                    "goal": "Compute the statistical or chart-ready result from SQL rows.",
                }
            )
        steps.append(
            {
                "agent": "reporter",
                "goal": "Return a concise answer grounded in the executed result.",
            }
        )

        rationale = (
            "The task requires post-query computation."
            if needs_python
            else "The task can be answered directly with SQL."
        )
        return Plan(route=route, steps=steps, rationale=rationale)


class LLMPlannerAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def plan(self, question: str) -> Plan:
        system_prompt = (
            "You are a planner for a SQL + Python data analysis benchmark. "
            "Choose the minimal route needed to answer the task. "
            "Use route [\"sql\"] for direct retrieval, filtering, grouping, and simple SQL aggregates. "
            "Use route [\"sql\", \"python\"] for variance, standard deviation, ranges, chart data, "
            "distribution analysis, comparisons requiring post-processing, or multi-step numerical analysis. "
            "Return JSON only."
        )
        user_prompt = f"""
Database schema:
{DATABASE_SCHEMA}

Question:
{question}

Return this JSON shape:
{{
  "route": ["sql"] or ["sql", "python"],
  "steps": [{{"agent": "sql", "goal": "..."}}, ...],
  "rationale": "short reason"
}}
""".strip()
        data = extract_json_object(self.llm_client.invoke("planner", system_prompt, user_prompt))
        route = tuple(data.get("route", ["sql"]))
        route = tuple(item for item in route if item in {"sql", "python"})
        if not route or route[0] != "sql":
            route = ("sql",) + tuple(item for item in route if item != "sql")
        if route not in {("sql",), ("sql", "python")}:
            route = ("sql", "python") if "python" in route else ("sql",)
        steps = data.get("steps") or [{"agent": "sql", "goal": "Retrieve required data."}]
        return Plan(route=route, steps=steps, rationale=data.get("rationale", "LLM generated plan."))


class TemplateSQLAgent:
    """A deterministic SQL agent for local, reproducible benchmark runs."""

    def generate_sql(self, question: str, plan: Plan) -> str:
        q = question.lower()

        if "above 100000" in q or "> 100000" in q:
            return "SELECT name, salary FROM employees WHERE salary > 100000;"
        if "highest salary" in q or "highest paid employee" in q:
            if "department average" in q or "compare" in q:
                return "SELECT name, dept, salary FROM employees;"
            return "SELECT name, salary FROM employees ORDER BY salary DESC LIMIT 1;"
        if "average salary per department" in q or "average salary by department" in q:
            if "chart" in q or "bar chart" in q:
                return "SELECT dept, salary FROM employees;"
            return (
                "SELECT dept, AVG(salary) AS avg_salary "
                "FROM employees GROUP BY dept ORDER BY avg_salary DESC;"
            )
        if any(term in q for term in ["variance", "standard deviation", "gap", "range", "distribution"]):
            return "SELECT dept, salary FROM employees;"
        if "compare" in q and "department average" in q:
            return "SELECT name, dept, salary FROM employees;"

        return "SELECT name, dept, title, salary FROM employees;"


class LLMSQLAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def generate_sql(self, question: str, plan: Plan) -> str:
        system_prompt = (
            "You are a SQL agent for SQLite. Generate exactly one read-only SELECT statement. "
            "Do not include markdown, explanation, comments, INSERT, UPDATE, DELETE, DROP, or PRAGMA."
        )
        user_prompt = f"""
Database schema:
{DATABASE_SCHEMA}

Planner route: {list(plan.route)}
Planner steps: {json.dumps(plan.steps, ensure_ascii=False)}

Question:
{question}

Return only the SQL query. If Python will do later analysis, retrieve the raw columns needed for that analysis.
""".strip()
        return clean_sql(self.llm_client.invoke("sql_agent", system_prompt, user_prompt))


class PythonAnalysisAgent:
    """Runs deterministic analysis over SQL rows."""

    def analyze(
        self,
        question: str,
        columns: Sequence[str],
        rows: Sequence[Tuple[Any, ...]],
    ) -> Dict[str, Any]:
        q = question.lower()
        records = [dict(zip(columns, row)) for row in rows]

        if "variance" in q:
            return self._group_variance_max(records, "dept", "salary")
        if "standard deviation" in q or "std" in q:
            return self._group_std_desc(records, "dept", "salary")
        if "gap" in q or "range" in q:
            return self._group_range_max(records, "dept", "salary")
        if "chart" in q or "bar chart" in q:
            return self._group_mean_chart(records, "dept", "salary")
        if "compare" in q and "department average" in q:
            return self._top_employee_vs_group_mean(records, "name", "dept", "salary")

        return {"analysis": "passthrough", "rows": list(rows)}

    def _groups(self, records: Sequence[Dict[str, Any]], group_key: str, value_key: str):
        groups = defaultdict(list)
        for record in records:
            groups[record[group_key]].append(record[value_key])
        return groups

    def _group_variance_max(self, records, group_key, value_key):
        values = {
            group: statistics.pvariance(items)
            for group, items in self._groups(records, group_key, value_key).items()
        }
        label, value = max(values.items(), key=lambda item: item[1])
        return {"analysis": "group_variance_max", "label": label, "value": value, "all_values": values}

    def _group_std_desc(self, records, group_key, value_key):
        values = [
            (group, statistics.pstdev(items))
            for group, items in self._groups(records, group_key, value_key).items()
        ]
        rows = sorted(values, key=lambda item: item[1], reverse=True)
        return {"analysis": "group_std_desc", "rows": rows}

    def _group_range_max(self, records, group_key, value_key):
        values = {
            group: max(items) - min(items)
            for group, items in self._groups(records, group_key, value_key).items()
        }
        label, value = max(values.items(), key=lambda item: item[1])
        return {"analysis": "group_range_max", "label": label, "value": value, "all_values": values}

    def _group_mean_chart(self, records, group_key, value_key):
        values = [
            (group, statistics.mean(items))
            for group, items in self._groups(records, group_key, value_key).items()
        ]
        return {
            "analysis": "group_mean_chart",
            "chart_type": "bar",
            "rows": sorted(values, key=lambda item: item[0]),
        }

    def _top_employee_vs_group_mean(self, records, name_key, group_key, value_key):
        top = max(records, key=lambda record: record[value_key])
        group_values = self._groups(records, group_key, value_key)
        group_average = statistics.mean(group_values[top[group_key]])
        return {
            "analysis": "top_employee_vs_group_mean",
            "name": top[name_key],
            "dept": top[group_key],
            "salary": top[value_key],
            "dept_average": group_average,
            "difference": top[value_key] - group_average,
        }


class LLMPythonAnalysisAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def analyze(
        self,
        question: str,
        columns: Sequence[str],
        rows: Sequence[Tuple[Any, ...]],
    ) -> Dict[str, Any]:
        system_prompt = (
            "You are a Python-style data analysis agent. Analyze the provided SQL result rows. "
            "Return JSON only. Do not return Python code. Use numeric JSON values, not formatted strings."
        )
        user_prompt = f"""
Question:
{question}

Columns:
{list(columns)}

Rows:
{json.dumps(list(rows), ensure_ascii=False)}

Return a compact JSON object with an "analysis" field and the computed result.
Use these analysis names when appropriate:
- group_variance_max: {{"analysis":"group_variance_max","label":"...","value":0}}
- group_std_desc: {{"analysis":"group_std_desc","rows":[["...",0]]}}
- group_range_max: {{"analysis":"group_range_max","label":"...","value":0}}
- group_mean_chart: {{"analysis":"group_mean_chart","chart_type":"bar","rows":[["...",0]]}}
- top_employee_vs_group_mean: {{"analysis":"top_employee_vs_group_mean","name":"...","dept":"...","salary":0,"dept_average":0,"difference":0}}
""".strip()
        return extract_json_object(self.llm_client.invoke("python_agent", system_prompt, user_prompt))


class VerifierAgent:
    """Checks whether the planned route and produced artifacts are internally consistent."""

    def verify(self, plan: Plan, sql_rows: List[Tuple[Any, ...]], python_result: Dict[str, Any]) -> Dict[str, Any]:
        issues = []
        if "sql" not in plan.route:
            issues.append("Plan did not include SQL, but this benchmark requires database grounding.")
        if "python" in plan.route and not python_result:
            issues.append("Plan requested Python analysis, but no Python result was produced.")
        if "python" not in plan.route and python_result:
            issues.append("Python result was produced even though the plan did not request Python.")
        if sql_rows is None:
            issues.append("SQL execution did not return a row list.")

        return {
            "passed": not issues,
            "issues": issues,
            "issue_count": len(issues),
        }


class LLMVerifierAgent(VerifierAgent):
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client

    def verify(self, plan: Plan, sql_rows: List[Tuple[Any, ...]], python_result: Dict[str, Any]) -> Dict[str, Any]:
        base = super().verify(plan, sql_rows, python_result)
        if not self.llm_client:
            return base

        system_prompt = (
            "You are a verifier for a multi-agent benchmark. Check internal consistency only. "
            "Return JSON only with passed boolean and issues list."
        )
        user_prompt = f"""
Plan route: {list(plan.route)}
SQL row count: {len(sql_rows) if sql_rows is not None else "null"}
Python result: {json.dumps(python_result, ensure_ascii=False)}
Rule-based verifier result: {json.dumps(base, ensure_ascii=False)}
""".strip()
        try:
            data = extract_json_object(self.llm_client.invoke("verifier", system_prompt, user_prompt))
            return {
                "passed": bool(data.get("passed", base["passed"])) and base["passed"],
                "issues": list(base["issues"]) + list(data.get("issues", [])),
                "issue_count": len(base["issues"]) + len(data.get("issues", [])),
            }
        except Exception as exc:
            base["issues"].append(f"LLM verifier failed: {exc}")
            base["passed"] = False
            base["issue_count"] = len(base["issues"])
            return base


class ReporterAgent:
    def report(
        self,
        question: str,
        plan: Plan,
        sql_rows: Sequence[Tuple[Any, ...]],
        python_result: Dict[str, Any],
    ) -> str:
        if "python" not in plan.route:
            return f"SQL result: {list(sql_rows)}"

        analysis = python_result.get("analysis")
        if analysis in {"group_variance_max", "group_range_max"}:
            return f"{python_result['label']} with value {python_result['value']}"
        if analysis == "group_std_desc":
            return f"Standard deviation ranking: {python_result['rows']}"
        if analysis == "group_mean_chart":
            return f"Bar chart data: {python_result['rows']}"
        if analysis == "top_employee_vs_group_mean":
            return (
                f"{python_result['name']} earns {python_result['salary']} in "
                f"{python_result['dept']}; department average is "
                f"{python_result['dept_average']}; difference is {python_result['difference']}."
            )
        return str(python_result)


class LLMReporterAgent(ReporterAgent):
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client

    def report(
        self,
        question: str,
        plan: Plan,
        sql_rows: Sequence[Tuple[Any, ...]],
        python_result: Dict[str, Any],
    ) -> str:
        if not self.llm_client:
            return super().report(question, plan, sql_rows, python_result)

        system_prompt = (
            "You are a concise benchmark reporter. Answer using only the provided SQL/Python results. "
            "Include the key numeric facts. Do not invent data."
        )
        user_prompt = f"""
Question:
{question}

Route:
{list(plan.route)}

SQL rows:
{json.dumps(list(sql_rows), ensure_ascii=False)}

Python result:
{json.dumps(python_result, ensure_ascii=False)}
""".strip()
        return self.llm_client.invoke("reporter", system_prompt, user_prompt).strip()


def close_enough(actual: Any, expected: Any, tolerance: float = 1e-6) -> bool:
    if isinstance(actual, float) or isinstance(expected, float):
        return math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=tolerance)
    return actual == expected
