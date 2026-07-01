import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from multi_agent_dataset import METRIC_GLOSSARY, SCHEMA_TEXT


@dataclass
class Plan:
    route: Tuple[str, ...]
    rationale: str


@dataclass
class AgentMessage:
    agent: str
    content: Dict[str, Any]


@dataclass
class LLMCall:
    agent: str
    output: str
    token_usage: Dict[str, Any]


def extract_json_object(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"No JSON object found in LLM output: {text}")
        return json.loads(match.group(0))


def clean_query(text: str) -> str:
    query = text.strip()
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", query, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        query = fenced.group(1).strip()
    query = query.strip().strip("`")
    query = re.sub(r"^\s*SQL\s*:\s*", "", query, flags=re.IGNORECASE)
    query = query.split(";")[0].strip() + ";"
    if not query.lower().startswith("select"):
        raise ValueError(f"Only SELECT queries are allowed. Got: {query}")
    return query


class LLMClient:
    def __init__(self):
        try:
            import config_local
            from langchain_openai import ChatOpenAI
        except Exception as exc:
            raise RuntimeError("LLM mode requires langchain-openai and config_local.py.") from exc

        placeholders = {"Your LLM Model", "Your API Key", "Your Base URL", ""}
        if (
            config_local.LLM_MODEL in placeholders
            or config_local.LLM_API_KEY in placeholders
            or config_local.LLM_BASE_URL in placeholders
        ):
            raise RuntimeError("config_local.py still contains placeholder values.")

        self.llm = ChatOpenAI(
            model=config_local.LLM_MODEL,
            temperature=0,
            api_key=config_local.LLM_API_KEY,
            base_url=config_local.LLM_BASE_URL,
        )
        self.calls: List[LLMCall] = []

    def invoke(self, agent: str, system_prompt: str, user_prompt: str) -> str:
        response = self.llm.invoke([("system", system_prompt), ("human", user_prompt)])
        output = response.content if hasattr(response, "content") else str(response)
        usage = getattr(response, "usage_metadata", None) or {}
        self.calls.append(LLMCall(agent=agent, output=output, token_usage=dict(usage)))
        return output


class TaskManagerAgent:
    def plan(self, question: str) -> Plan:
        q = question.lower()
        needs_analysis = any(term in q for term in ["identify", "chart", "concentration", "highest"])
        if "which at-risk" in q:
            needs_analysis = False
        route = ("metric", "discovery", "query", "analysis") if needs_analysis else ("metric", "discovery", "query")
        return Plan(route=route, rationale="Route selected from enterprise task intent.")


class LLMTaskManagerAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def plan(self, question: str) -> Plan:
        output = self.llm_client.invoke(
            "task_manager",
            "You are an enterprise data task manager. Return JSON only.",
            f"""
Choose the workflow route for this business question.

Allowed routes:
- ["metric", "discovery", "query"]
- ["metric", "discovery", "query", "analysis"]

Use analysis when the task asks for chart-ready output, ranking, identifying a maximum, concentration, or post-query computation.

Question: {question}

Return: {{"route": [...], "rationale": "..."}}
""".strip(),
        )
        data = extract_json_object(output)
        route = tuple(data.get("route", ("metric", "discovery", "query")))
        if route not in {
            ("metric", "discovery", "query"),
            ("metric", "discovery", "query", "analysis"),
        }:
            route = ("metric", "discovery", "query")
        return Plan(route=route, rationale=data.get("rationale", "LLM route."))


class MetricAgent:
    def resolve(self, question: str) -> Dict[str, Any]:
        q = question.lower()
        if "high-value" in q or "high value" in q:
            metric_id = "high_value_active_revenue"
        elif "at-risk" in q or "support exposure" in q:
            metric_id = "at_risk_open_support"
        elif "account manager" in q or "concentration" in q:
            metric_id = "manager_revenue_concentration"
        elif "segment" in q:
            metric_id = "segment_active_revenue_mix"
        elif "churned" in q:
            metric_id = "churned_customer_revenue"
        else:
            metric_id = "regional_active_customer_revenue"
        return {"metric_id": metric_id, **METRIC_GLOSSARY[metric_id]}


class LLMMetricAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.fallback = MetricAgent()

    def resolve(self, question: str) -> Dict[str, Any]:
        output = self.llm_client.invoke(
            "metric_agent",
            "You map business questions to metric glossary ids. Return JSON only.",
            f"""
Metric glossary:
{json.dumps(METRIC_GLOSSARY, ensure_ascii=False, indent=2)}

Question: {question}

Return: {{"metric_id": "..."}}.
""".strip(),
        )
        try:
            metric_id = extract_json_object(output).get("metric_id")
            if metric_id in METRIC_GLOSSARY:
                return {"metric_id": metric_id, **METRIC_GLOSSARY[metric_id]}
        except Exception:
            pass
        return self.fallback.resolve(question)


class DataDiscoveryAgent:
    def discover(self, metric_context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "tables": metric_context["required_tables"],
            "columns": metric_context["required_columns"],
            "data_contract": {
                "required_tables": metric_context["required_tables"],
                "required_columns": metric_context["required_columns"],
            },
        }


class LLMDataDiscoveryAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.fallback = DataDiscoveryAgent()

    def discover(self, metric_context: Dict[str, Any]) -> Dict[str, Any]:
        output = self.llm_client.invoke(
            "data_discovery_agent",
            "You select enterprise tables and columns from a schema. Return JSON only.",
            f"""
Schema:
{SCHEMA_TEXT}

Metric context:
{json.dumps(metric_context, ensure_ascii=False)}

Return: {{"tables": [...], "columns": [...], "data_contract": {{"required_tables": [...], "required_columns": [...]}}}}
""".strip(),
        )
        try:
            data = extract_json_object(output)
            if data.get("tables") and data.get("columns"):
                return data
        except Exception:
            pass
        return self.fallback.discover(metric_context)


class QueryAgent:
    def generate_query(self, metric_context: Dict[str, Any]) -> str:
        metric_id = metric_context["metric_id"]
        if metric_id == "high_value_active_revenue":
            return """
            SELECT SUM(o.amount) AS revenue
            FROM customers c
            JOIN orders o ON c.customer_id = o.customer_id
            WHERE c.status='active'
              AND c.is_internal=0
              AND c.annual_contract_value >= 20000
              AND o.order_status='closed';
            """
        if metric_id == "at_risk_open_support":
            return """
            SELECT c.customer_name, t.severity, t.resolution_hours
            FROM customers c
            JOIN support_tickets t ON c.customer_id = t.customer_id
            WHERE c.status='at_risk'
              AND c.is_internal=0
              AND t.ticket_status='open'
              AND t.severity IN ('high', 'critical')
            ORDER BY t.resolution_hours DESC;
            """
        if metric_id == "manager_revenue_concentration":
            return """
            SELECT am.manager_name, o.amount
            FROM customers c
            JOIN orders o ON c.customer_id = o.customer_id
            JOIN account_managers am ON c.customer_id = am.customer_id
            WHERE c.is_internal=0
              AND o.order_status='closed';
            """
        if metric_id == "segment_active_revenue_mix":
            return """
            SELECT c.segment, o.amount
            FROM customers c
            JOIN orders o ON c.customer_id = o.customer_id
            WHERE c.status='active'
              AND c.is_internal=0
              AND o.order_status='closed';
            """
        if metric_id == "churned_customer_revenue":
            return """
            SELECT SUM(o.amount) AS revenue
            FROM customers c
            JOIN orders o ON c.customer_id = o.customer_id
            WHERE c.status='churned'
              AND c.is_internal=0
              AND o.order_status='closed';
            """
        return """
        SELECT c.region, o.amount
        FROM customers c
        JOIN orders o ON c.customer_id = o.customer_id
        WHERE c.status='active'
          AND c.is_internal=0
          AND o.order_status='closed';
        """


class LLMQueryAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def generate_query(self, metric_context: Dict[str, Any], data_context: Dict[str, Any]) -> str:
        output = self.llm_client.invoke(
            "query_agent",
            "You are a SQLite query agent. Return one SELECT query only.",
            f"""
Schema:
{SCHEMA_TEXT}

Metric context:
{json.dumps(metric_context, ensure_ascii=False)}

Data context:
{json.dumps(data_context, ensure_ascii=False)}

Return only a SELECT query that satisfies the metric definition and business rules.
""".strip(),
        )
        return clean_query(output)


class AnalysisAgent:
    def analyze(self, metric_id: str, columns: Sequence[str], rows: Sequence[Tuple[Any, ...]]) -> Dict[str, Any]:
        if metric_id == "manager_revenue_concentration":
            totals = self._group_sum(rows)
            label, value = max(totals.items(), key=lambda item: item[1])
            return {"analysis": "group_sum_max", "label": label, "value": value, "all_values": totals}
        if metric_id in {"regional_active_customer_revenue", "segment_active_revenue_mix"}:
            totals = self._group_sum(rows)
            return {"analysis": "group_sum_chart", "chart_type": "bar", "rows": sorted(totals.items())}
        return {"analysis": "passthrough", "rows": list(rows)}

    def _group_sum(self, rows: Sequence[Tuple[Any, ...]]) -> Dict[str, int]:
        totals: Dict[str, int] = defaultdict(int)
        for label, value in rows:
            totals[label] += value
        return dict(totals)


class QAAgent:
    def check(self, plan: Plan, query_rows: Sequence[Tuple[Any, ...]], analysis_result: Dict[str, Any]) -> Dict[str, Any]:
        issues = []
        if "query" not in plan.route:
            issues.append("Workflow did not include query.")
        if "analysis" in plan.route and not analysis_result:
            issues.append("Workflow required analysis but no analysis result was produced.")
        if query_rows is None:
            issues.append("Query did not return rows.")
        return {"passed": not issues, "issues": issues}


class InsightAgent:
    def report(self, plan: Plan, query_rows: Sequence[Tuple[Any, ...]], analysis_result: Dict[str, Any]) -> str:
        if "analysis" in plan.route:
            return f"Analysis result: {analysis_result}"
        return f"Query result: {list(query_rows)}"
