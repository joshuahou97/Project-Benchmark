import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple


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


def close_enough(actual: Any, expected: Any, tolerance: float = 1e-6) -> bool:
    if isinstance(actual, float) or isinstance(expected, float):
        return math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=tolerance)
    return actual == expected
