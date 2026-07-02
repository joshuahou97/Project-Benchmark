import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from multi_agent_dataset import DATA_DISCOVERY_CATALOG, METRIC_GLOSSARY, SCHEMA_TEXT


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
        discovery = DATA_DISCOVERY_CATALOG[metric_context["metric_id"]]
        return {
            "tables": discovery["tables"],
            "columns": discovery["columns"],
            "join_keys": discovery["join_keys"],
            "filters": discovery["filters"],
            "grain": discovery["grain"],
            "data_contract": {
                "tables": discovery["tables"],
                "columns": discovery["columns"],
                "join_keys": discovery["join_keys"],
                "filters": discovery["filters"],
                "grain": discovery["grain"],
            },
        }


class LLMDataDiscoveryAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.fallback = DataDiscoveryAgent()

    def discover(self, metric_context: Dict[str, Any]) -> Dict[str, Any]:
        output = self.llm_client.invoke(
            "data_discovery_agent",
            "You are an enterprise data discovery agent. Map business metric rules to physical schema. Return JSON only.",
            f"""
Schema:
{SCHEMA_TEXT}

Metric context:
{json.dumps(metric_context, ensure_ascii=False)}

Select the physical data needed to compute the metric.
Do not invent columns outside the schema.
The selected columns must be sufficient for joins, filters, grouping, downstream analysis, and the final answer.
Do not return a minimal filter-only contract if the user needs labels, values, or explanatory fields in the answer.

Return:
{{
  "tables": [...],
  "columns": [...],
  "join_keys": [...],
  "filters": [...],
  "grain": "...",
  "data_contract": {{
    "tables": [...],
    "columns": [...],
    "join_keys": [...],
    "filters": [...],
    "grain": "..."
  }}
}}
""".strip(),
        )
        try:
            data = extract_json_object(output)
            if data.get("tables") and data.get("columns") and self._covers_catalog(metric_context["metric_id"], data):
                return data
        except Exception:
            pass
        return self.fallback.discover(metric_context)

    def _covers_catalog(self, metric_id: str, data: Dict[str, Any]) -> bool:
        expected = DATA_DISCOVERY_CATALOG[metric_id]
        return all(
            [
                self._contains_names(expected["tables"], data.get("tables", [])),
                self._contains_names(expected["columns"], data.get("columns", [])),
                self._contains_contract(expected["join_keys"], data.get("join_keys", [])),
                self._contains_contract(expected["filters"], data.get("filters", [])),
            ]
        )

    def _contains_names(self, expected: Sequence[str], actual: Sequence[str]) -> bool:
        actual_names = {item.lower() for item in actual}
        actual_names.update(item.lower().split(".")[-1] for item in actual)
        return {item.lower() for item in expected}.issubset(actual_names)

    def _contains_contract(self, expected: Sequence[str], actual: Sequence[str]) -> bool:
        actual_norm = {self._normalize_contract_item(item) for item in actual}
        return {self._normalize_contract_item(item) for item in expected}.issubset(actual_norm)

    def _normalize_contract_item(self, value: str) -> str:
        text = value.lower()
        text = re.sub(r"\b(customers|orders|support_tickets|account_managers)\.", "", text)
        text = text.replace("'", "").replace('"', "").replace("`", "")
        return re.sub(r"\s+", "", text)


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
Follow the data contract closely. Include output columns needed by the final answer or downstream analysis.
Use explicit AS aliases for computed columns, for example AS revenue or AS closed_revenue; do not leave raw expressions such as SUM(amount) unnamed.
""".strip(),
        )
        return clean_query(output)


class AnalysisAgent:
    def analyze(self, metric_id: str, columns: Sequence[str], rows: Sequence[Tuple[Any, ...]]) -> Dict[str, Any]:
        if metric_id == "manager_revenue_concentration":
            totals = self._group_sum(columns, rows, "manager_name")
            label, value = max(totals.items(), key=lambda item: item[1])
            return {"analysis": "group_sum_max", "label": label, "value": value, "all_values": totals}
        if metric_id in {"regional_active_customer_revenue", "segment_active_revenue_mix"}:
            label_column = "segment" if metric_id == "segment_active_revenue_mix" else "region"
            totals = self._group_sum(columns, rows, label_column)
            return {"analysis": "group_sum_chart", "chart_type": "bar", "rows": sorted(totals.items())}
        return {"analysis": "passthrough", "rows": list(rows)}

    def _group_sum(self, columns: Sequence[str], rows: Sequence[Tuple[Any, ...]], label_column: str) -> Dict[str, int]:
        label_idx = self._find_column(columns, label_column)
        value_idx = self._find_column(columns, "amount")
        if label_idx is None or value_idx is None:
            if columns and len(columns) != 2:
                raise ValueError(f"Cannot identify {label_column}/amount columns from {list(columns)}")
            label_idx, value_idx = 0, 1
        totals: Dict[str, int] = defaultdict(int)
        for row in rows:
            totals[row[label_idx]] += row[value_idx]
        return dict(totals)

    def _find_column(self, columns: Sequence[str], expected: str) -> Optional[int]:
        for idx, column in enumerate(columns):
            if self._canonical_column_name(column) == self._canonical_column_name(expected):
                return idx
        return None

    def _canonical_column_name(self, value: str) -> str:
        aliases = {
            "account_manager": "manager_name",
            "account_owner": "manager_name",
            "manager": "manager_name",
            "customer_segment": "segment",
            "market_segment": "segment",
            "closed_revenue": "amount",
            "booked_revenue": "amount",
            "total_revenue": "amount",
            "revenue": "amount",
        }
        name = value.lower().split(".")[-1]
        return aliases.get(name, name)


class QAAgent:
    REQUIRED_QUERY_COLUMNS = {
        "high_value_active_revenue": ("revenue",),
        "at_risk_open_support": ("customer_name", "severity", "resolution_hours"),
        "manager_revenue_concentration": ("manager_name", "amount"),
        "regional_active_customer_revenue": ("region", "amount"),
        "segment_active_revenue_mix": ("segment", "amount"),
        "churned_customer_revenue": ("revenue",),
    }

    EXPECTED_ANALYSIS = {
        "manager_revenue_concentration": "group_sum_max",
        "regional_active_customer_revenue": "group_sum_chart",
        "segment_active_revenue_mix": "group_sum_chart",
    }

    COLUMN_ALIASES = {
        "account_manager": "manager_name",
        "account_owner": "manager_name",
        "manager": "manager_name",
        "customer_segment": "segment",
        "market_segment": "segment",
        "closed_revenue": "amount",
        "booked_revenue": "amount",
        "total_revenue": "amount",
        "churned_revenue": "amount",
        "high_value_active_revenue": "revenue",
        "churned_customer_revenue": "revenue",
    }

    def check(
        self,
        plan: Plan,
        metric_context: Dict[str, Any],
        data_context: Dict[str, Any],
        query: str,
        query_columns: Sequence[str],
        query_rows: Sequence[Tuple[Any, ...]],
        analysis_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        issues = []
        if "query" not in plan.route:
            issues.append("Workflow did not include query.")
        if "analysis" in plan.route and not analysis_result:
            issues.append("Workflow required analysis but no analysis result was produced.")
        if query_rows is None:
            issues.append("Query did not return rows.")
        if not query.strip().lower().startswith("select"):
            issues.append("Query is not a SELECT statement.")

        metric_id = metric_context.get("metric_id")
        if metric_id not in DATA_DISCOVERY_CATALOG:
            issues.append(f"Unknown metric id: {metric_id}")
        else:
            expected_contract = DATA_DISCOVERY_CATALOG[metric_id]
            for key in ("tables", "columns", "join_keys", "filters", "grain"):
                if not data_context.get(key):
                    issues.append(f"Data discovery did not return {key}.")
            missing_columns = self._missing_columns(self.REQUIRED_QUERY_COLUMNS.get(metric_id, ()), query_columns)
            if missing_columns:
                issues.append(f"Query output is missing required columns: {', '.join(missing_columns)}.")
            missing_filters = self._missing_contract_items(expected_contract.get("filters", []), data_context.get("filters", []))
            if missing_filters:
                issues.append(f"Data contract is missing filters: {missing_filters}.")

        expected_analysis = self.EXPECTED_ANALYSIS.get(metric_id)
        if "analysis" in plan.route and expected_analysis and analysis_result.get("analysis") != expected_analysis:
            issues.append(f"Expected analysis type {expected_analysis}, got {analysis_result.get('analysis')}.")
        return {"passed": not issues, "issues": issues}

    def _missing_columns(self, expected: Sequence[str], actual: Sequence[str]) -> List[str]:
        actual_names = {self._canonical_column_name(item) for item in actual}
        missing = []
        for column in expected:
            canonical_column = self._canonical_column_name(column)
            if canonical_column in actual_names:
                continue
            if canonical_column in {"amount", "revenue"} and any(
                name.endswith("revenue") or name in {"amount", "total_revenue"} for name in actual_names
            ):
                continue
            missing.append(column)
        return missing

    def _canonical_column_name(self, value: str) -> str:
        name = value.lower().split(".")[-1]
        return self.COLUMN_ALIASES.get(name, name)

    def _missing_contract_items(self, expected: Sequence[str], actual: Sequence[str]) -> List[str]:
        actual_norm = {self._normalize_contract_item(item) for item in actual}
        return [item for item in expected if self._normalize_contract_item(item) not in actual_norm]

    def _normalize_contract_item(self, value: str) -> str:
        text = value.lower()
        text = re.sub(r"\b(customers|orders|support_tickets|account_managers)\.", "", text)
        text = text.replace("'", "").replace('"', "").replace("`", "")
        return re.sub(r"\s+", "", text)


class LLMQAAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.fallback = QAAgent()

    def check(
        self,
        plan: Plan,
        metric_context: Dict[str, Any],
        data_context: Dict[str, Any],
        query: str,
        query_columns: Sequence[str],
        query_rows: Sequence[Tuple[Any, ...]],
        analysis_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        rule_result = self.fallback.check(
            plan, metric_context, data_context, query, query_columns, query_rows, analysis_result
        )
        if not rule_result["passed"]:
            return rule_result

        output = self.llm_client.invoke(
            "qa_agent",
            "You are a data workflow QA agent. Return JSON only.",
            f"""
Audit whether this completed workflow is internally consistent and grounded.
Do not recompute the whole answer; look for obvious mismatches between the metric rules, data contract, SQL output, and analysis.
Only fail the workflow for concrete issues visible in the provided artifacts.

Plan:
{json.dumps({"route": plan.route, "rationale": plan.rationale}, ensure_ascii=False)}

Metric context:
{json.dumps(metric_context, ensure_ascii=False)}

Data context:
{json.dumps(data_context, ensure_ascii=False)}

Query:
{query}

Query columns:
{json.dumps(list(query_columns), ensure_ascii=False)}

Query rows:
{json.dumps(list(query_rows), ensure_ascii=False)}

Analysis result:
{json.dumps(analysis_result, ensure_ascii=False)}

Return: {{"passed": true|false, "issues": [...]}}
""".strip(),
        )
        try:
            data = extract_json_object(output)
            return {"passed": self._parse_passed(data.get("passed")), "issues": list(data.get("issues", []))}
        except Exception:
            return rule_result

    def _parse_passed(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() == "true"
        return False


class InsightAgent:
    def report(
        self,
        question: str,
        metric_context: Dict[str, Any],
        plan: Plan,
        query_columns: Sequence[str],
        query_rows: Sequence[Tuple[Any, ...]],
        analysis_result: Dict[str, Any],
        qa_result: Dict[str, Any],
    ) -> str:
        if not qa_result.get("passed"):
            return f"Unable to produce a trusted answer. QA issues: {qa_result.get('issues', [])}"
        if "analysis" in plan.route:
            return self._analysis_answer(metric_context.get("metric_id"), analysis_result)
        return self._query_answer(metric_context.get("metric_id"), query_columns, query_rows)

    def _analysis_answer(self, metric_id: str, analysis_result: Dict[str, Any]) -> str:
        if metric_id == "manager_revenue_concentration":
            return (
                f"{analysis_result.get('label')} has the highest closed revenue concentration, "
                f"with {analysis_result.get('value')} in closed revenue."
            )
        if metric_id in {"regional_active_customer_revenue", "segment_active_revenue_mix"}:
            rows = analysis_result.get("rows", [])
            formatted = ", ".join(f"{label}: {value}" for label, value in rows)
            return f"Bar chart data: {formatted}."
        return f"Analysis result: {analysis_result}"

    def _query_answer(
        self, metric_id: str, query_columns: Sequence[str], query_rows: Sequence[Tuple[Any, ...]]
    ) -> str:
        rows = list(query_rows)
        if metric_id == "high_value_active_revenue" and rows:
            return f"High-value active customers generated {rows[0][0]} in closed revenue."
        if metric_id == "churned_customer_revenue" and rows:
            return f"Churned customers generated {rows[0][0]} in closed revenue."
        if metric_id == "at_risk_open_support":
            if not rows:
                return "No at-risk customers currently have open high-severity support exposure."
            details = ", ".join(f"{row[0]} ({row[1]}, {row[2]} resolution hours)" for row in rows)
            return f"Open high-severity support exposure: {details}."
        return f"Query result: {rows}"


class LLMInsightAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.fallback = InsightAgent()

    def report(
        self,
        question: str,
        metric_context: Dict[str, Any],
        plan: Plan,
        query_columns: Sequence[str],
        query_rows: Sequence[Tuple[Any, ...]],
        analysis_result: Dict[str, Any],
        qa_result: Dict[str, Any],
    ) -> str:
        if not qa_result.get("passed"):
            return self.fallback.report(question, metric_context, plan, query_columns, query_rows, analysis_result, qa_result)

        output = self.llm_client.invoke(
            "insight_agent",
            "You are an enterprise insight agent. Write concise grounded business answers.",
            f"""
Answer the user's question using only the provided query rows and analysis result.
Include the key numeric facts and labels. Do not mention internal implementation details.
If chart-ready data is present, summarize it as chart-ready data.
Preserve numeric values as plain digits without currency symbols, thousands separators, or rounding.
If analysis_result has chart_type, include that chart type word in the answer.

Question:
{question}

Metric context:
{json.dumps(metric_context, ensure_ascii=False)}

Query columns:
{json.dumps(list(query_columns), ensure_ascii=False)}

Query rows:
{json.dumps(list(query_rows), ensure_ascii=False)}

Analysis result:
{json.dumps(analysis_result, ensure_ascii=False)}
""".strip(),
        ).strip()
        return output or self.fallback.report(
            question, metric_context, plan, query_columns, query_rows, analysis_result, qa_result
        )
