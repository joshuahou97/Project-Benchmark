import argparse
import json
import random
import sqlite3
import string
import sys
import time
from collections import Counter
from typing import Any, Dict, Sequence, Tuple

from multi_agent_agents import (
    AgentMessage,
    AnalysisAgent,
    DataDiscoveryAgent,
    InsightAgent,
    LLMClient,
    LLMDataDiscoveryAgent,
    LLMMetricAgent,
    LLMQueryAgent,
    LLMTaskManagerAgent,
    MetricAgent,
    QAAgent,
    QueryAgent,
    TaskManagerAgent,
)
from multi_agent_dataset import DB_PATH, init_enterprise_db
from test_cases_multi_agent import MULTI_AGENT_TEST_CASES, MultiAgentCase


def run_query(db_path: str, query: str) -> Tuple[list[Tuple[Any, ...]], list[str]]:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(query)
    rows = cur.fetchall()
    columns = [d[0] for d in (cur.description or [])]
    conn.close()
    return rows, columns


def normalize_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, str):
        return value.strip()
    return value


def normalize_rows(rows: Sequence[Tuple[Any, ...]]) -> list[Tuple[Any, ...]]:
    return [tuple(normalize_value(v) for v in row) for row in rows]


def rows_equal(a: Sequence[Tuple[Any, ...]], b: Sequence[Tuple[Any, ...]]) -> bool:
    return Counter(normalize_rows(a)) == Counter(normalize_rows(b))


def column_matches(expected: str, actual: str) -> bool:
    expected_norm = expected.lower()
    actual_norm = actual.lower()
    if expected_norm == actual_norm:
        return True
    revenue_aliases = {"amount", "revenue", "total_revenue", "closed_revenue", "booked_revenue", "churned_revenue"}
    if expected_norm in {"amount", "revenue"} and actual_norm in revenue_aliases:
        return True
    if expected_norm in {"amount", "revenue"} and actual_norm.endswith("revenue"):
        return True
    return False


def find_column_indices(expected: Sequence[str], actual: Sequence[str]) -> Tuple[int, ...] | None:
    indices = []
    used = set()
    for expected_column in expected:
        match = None
        for idx, actual_column in enumerate(actual):
            if idx not in used and column_matches(expected_column, actual_column):
                match = idx
                break
        if match is None:
            return None
        indices.append(match)
        used.add(match)
    return tuple(indices)


def project_rows(rows: Sequence[Tuple[Any, ...]], indices: Sequence[int]) -> list[Tuple[Any, ...]]:
    return [tuple(row[idx] for idx in indices) for row in rows]


def add_noise(question: str) -> str:
    rewrites = [
        ("high-value", "strategic"),
        ("support exposure", "service risk"),
        ("account manager", "account owner"),
        ("churned customers", "lost customers"),
        ("at-risk", "risk flagged"),
        ("high-severity", "urgent"),
        ("active customers", "currently live customers"),
        ("region", "geo"),
        ("customer segment", "market segment"),
        ("closed revenue", "booked revenue"),
    ]
    for src, dst in rewrites:
        if src in question:
            return question.replace(src, dst)

    words = question.split()
    if not words:
        return question
    idx = random.randrange(len(words))
    word = words[idx]
    if len(word) > 5:
        chars = list(word)
        i = random.randrange(1, len(chars) - 2)
        chars[i], chars[i + 1] = chars[i + 1], chars[i]
        words[idx] = "".join(chars)
    else:
        words[idx] = word + random.choice(string.ascii_lowercase)
    return " ".join(words)


class MultiAgentSystem:
    def __init__(self, agent_mode: str = "deterministic"):
        self.llm_client = LLMClient() if agent_mode == "llm" else None
        if self.llm_client:
            self.task_manager = LLMTaskManagerAgent(self.llm_client)
            self.metric_agent = LLMMetricAgent(self.llm_client)
            self.discovery_agent = LLMDataDiscoveryAgent(self.llm_client)
            self.query_agent = LLMQueryAgent(self.llm_client)
        else:
            self.task_manager = TaskManagerAgent()
            self.metric_agent = MetricAgent()
            self.discovery_agent = DataDiscoveryAgent()
            self.query_agent = QueryAgent()
        self.analysis_agent = AnalysisAgent()
        self.qa_agent = QAAgent()
        self.insight_agent = InsightAgent()

    def run(self, question: str) -> Dict[str, Any]:
        llm_start = len(self.llm_client.calls) if self.llm_client else 0
        trace = []

        plan = self.task_manager.plan(question)
        trace.append(AgentMessage("task_manager", {"route": plan.route, "rationale": plan.rationale}))

        metric_context = self.metric_agent.resolve(question)
        trace.append(AgentMessage("metric_agent", metric_context))

        data_context = self.discovery_agent.discover(metric_context)
        trace.append(AgentMessage("data_discovery_agent", data_context))

        if self.llm_client:
            query = self.query_agent.generate_query(metric_context, data_context)
        else:
            query = self.query_agent.generate_query(metric_context)
        trace.append(AgentMessage("query_agent", {"query": query}))

        query_error = None
        try:
            query_rows, query_columns = run_query(DB_PATH, query)
        except Exception as exc:
            query_rows, query_columns = [], []
            query_error = str(exc)
        trace.append(AgentMessage("query_executor", {"columns": query_columns, "rows": query_rows, "error": query_error}))

        analysis_result = {}
        analysis_error = None
        if "analysis" in plan.route and query_error is None:
            try:
                analysis_result = self.analysis_agent.analyze(metric_context["metric_id"], query_columns, query_rows)
            except Exception as exc:
                analysis_error = str(exc)
                analysis_result = {"analysis": "error", "error": analysis_error}
            trace.append(AgentMessage("analysis_agent", analysis_result))

        qa_result = self.qa_agent.check(plan, query_rows, analysis_result)
        if query_error:
            qa_result["passed"] = False
            qa_result["issues"].append(f"Query execution failed: {query_error}")
        if analysis_error:
            qa_result["passed"] = False
            qa_result["issues"].append(f"Analysis failed: {analysis_error}")
        trace.append(AgentMessage("qa_agent", qa_result))

        final_answer = self.insight_agent.report(plan, query_rows, analysis_result)
        trace.append(AgentMessage("insight_agent", {"final_answer": final_answer}))

        llm_calls = []
        if self.llm_client:
            llm_calls = [
                {"agent": call.agent, "output": call.output, "token_usage": call.token_usage}
                for call in self.llm_client.calls[llm_start:]
            ]

        return {
            "plan": plan,
            "metric_context": metric_context,
            "data_context": data_context,
            "query": query,
            "query_rows": query_rows,
            "query_columns": query_columns,
            "analysis_result": analysis_result,
            "qa": qa_result,
            "final_answer": final_answer,
            "trace": [{"agent": item.agent, "content": item.content} for item in trace],
            "llm_calls": llm_calls,
        }


def set_contains(expected: Sequence[str], actual: Sequence[str]) -> bool:
    actual_names = {x.lower() for x in actual}
    actual_names.update(x.lower().split(".")[-1] for x in actual)
    return {x.lower() for x in expected}.issubset(actual_names)


def columns_contain(expected: Sequence[str], actual: Sequence[str]) -> bool:
    return find_column_indices(expected, actual) is not None


def route_covers(expected: Sequence[str], planned: Sequence[str]) -> bool:
    cursor = 0
    for expected_step in expected:
        while cursor < len(planned) and planned[cursor] != expected_step:
            cursor += 1
        if cursor >= len(planned):
            return False
        cursor += 1
    return True


def evaluate_query_rows(case: MultiAgentCase, rows: Sequence[Tuple[Any, ...]], columns: Sequence[str]) -> bool:
    expected_rows = case.expected.get("rows", [])
    if not case.expected_result_columns:
        return rows_equal(rows, expected_rows)
    indices = find_column_indices(case.expected_result_columns, columns)
    if indices is None:
        return False
    return rows_equal(project_rows(rows, indices), expected_rows)


def evaluate_analysis(case: MultiAgentCase, result: Dict[str, Any]) -> bool:
    if case.expected_output_type == "query_rows":
        return True
    expected = case.expected
    if result.get("analysis") != expected.get("analysis"):
        return False
    if "label" in expected and result.get("label") != expected["label"]:
        return False
    if "value" in expected and result.get("value") != expected["value"]:
        return False
    if "rows" in expected and normalize_rows(result.get("rows", [])) != normalize_rows(expected["rows"]):
        return False
    return True


def required_query_output_columns(case: MultiAgentCase) -> Tuple[str, ...]:
    if case.expected_result_columns:
        return case.expected_result_columns
    if case.expected_output_type == "query_rows":
        return ()
    analysis = case.expected.get("analysis")
    if analysis in {"group_sum_max", "group_sum_chart"}:
        if case.expected_metric == "manager_revenue_concentration":
            return ("manager_name", "amount")
        if case.expected_metric == "segment_active_revenue_mix":
            return ("segment", "amount")
        return ("region", "amount")
    return ()


def evaluate_case(case: MultiAgentCase, system: MultiAgentSystem, noisy: bool = False) -> Dict[str, Any]:
    question = add_noise(case.question) if noisy else case.question
    t0 = time.perf_counter()
    run = system.run(question)
    latency = time.perf_counter() - t0

    route_exact_match = tuple(run["plan"].route) == case.expected_route
    route_covered = route_covers(case.expected_route, run["plan"].route)
    unnecessary_steps = [step for step in run["plan"].route if step not in case.expected_route]
    metric_correct = run["metric_context"].get("metric_id") == case.expected_metric
    discovery_correct = set_contains(case.expected_tables, run["data_context"].get("tables", [])) and set_contains(
        case.expected_columns, run["data_context"].get("columns", [])
    )
    query_correct = True if case.expected_output_type != "query_rows" else evaluate_query_rows(
        case, run["query_rows"], run["query_columns"]
    )
    required_output_columns = required_query_output_columns(case)
    query_sufficient = query_correct if not required_output_columns else columns_contain(
        required_output_columns, run["query_columns"]
    )
    analysis_correct = evaluate_analysis(case, run["analysis_result"])
    completeness = sum(str(f).lower() in run["final_answer"].lower() for f in case.required_facts) / max(
        len(case.required_facts), 1
    )
    final_correct = all(
        [
            route_covered,
            metric_correct,
            discovery_correct,
            query_sufficient,
            query_correct,
            analysis_correct,
            run["qa"]["passed"],
        ]
    )

    return {
        "id": case.id,
        "question": case.question,
        "run_question": question,
        "expected_route": case.expected_route,
        "planned_route": run["plan"].route,
        "route_correct": route_exact_match,
        "route_exact_match": route_exact_match,
        "route_covered": route_covered,
        "unnecessary_steps": unnecessary_steps,
        "unnecessary_step_count": len(unnecessary_steps),
        "expected_metric": case.expected_metric,
        "metric_context": run["metric_context"],
        "metric_correct": metric_correct,
        "expected_tables": case.expected_tables,
        "expected_columns": case.expected_columns,
        "data_context": run["data_context"],
        "data_discovery_correct": discovery_correct,
        "expected_query": case.expected_query,
        "executed_query": run["query"],
        "query_columns": run["query_columns"],
        "query_rows": normalize_rows(run["query_rows"]),
        "query_contract": case.query_contract,
        "query_sufficient": query_sufficient,
        "query_correct": query_correct,
        "analysis_result": run["analysis_result"],
        "analysis_correct": analysis_correct,
        "qa_passed": run["qa"]["passed"],
        "qa_issues": run["qa"]["issues"],
        "final_answer": run["final_answer"],
        "final_correct": final_correct,
        "completeness": round(completeness, 4),
        "latency_sec": round(latency, 4),
        "round_count": len(run["trace"]),
        "query_count": 1,
        "analysis_execution_count": 1 if "analysis" in run["plan"].route else 0,
        "llm_call_count": len(run["llm_calls"]),
        "llm_calls": run["llm_calls"],
        "trace": run["trace"],
        "notes": case.notes,
    }


def summarize(results: Sequence[Dict[str, Any]], robust_results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    analysis_cases = [item for item in results if "analysis" in item["expected_route"]]
    robust_total = len(robust_results)
    clean_accuracy = sum(item["final_correct"] for item in results) / total
    robust_accuracy = sum(item["final_correct"] for item in robust_results) / robust_total if robust_total else None
    return {
        "total": total,
        "final_accuracy": round(clean_accuracy, 4),
        "tool_routing_accuracy": round(sum(item["route_exact_match"] for item in results) / total, 4),
        "route_exact_match_accuracy": round(sum(item["route_exact_match"] for item in results) / total, 4),
        "route_coverage_accuracy": round(sum(item["route_covered"] for item in results) / total, 4),
        "avg_unnecessary_step_count": round(sum(item["unnecessary_step_count"] for item in results) / total, 4),
        "metric_grounding_accuracy": round(sum(item["metric_correct"] for item in results) / total, 4),
        "data_discovery_accuracy": round(sum(item["data_discovery_correct"] for item in results) / total, 4),
        "query_sufficiency": round(sum(item["query_sufficient"] for item in results) / total, 4),
        "query_correctness": round(sum(item["query_correct"] for item in results) / total, 4),
        "analysis_correctness": round(sum(item["analysis_correct"] for item in analysis_cases) / len(analysis_cases), 4)
        if analysis_cases
        else 1.0,
        "qa_pass_rate": round(sum(item["qa_passed"] for item in results) / total, 4),
        "result_completeness": round(sum(item["completeness"] for item in results) / total, 4),
        "robust_final_accuracy": round(robust_accuracy, 4) if robust_accuracy is not None else None,
        "robust_drop": round(clean_accuracy - robust_accuracy, 4) if robust_accuracy is not None else None,
        "avg_latency_sec": round(sum(item["latency_sec"] for item in results) / total, 4),
        "avg_round_count": round(sum(item["round_count"] for item in results) / total, 4),
        "avg_query_count": round(sum(item["query_count"] for item in results) / total, 4),
        "avg_analysis_execution_count": round(sum(item["analysis_execution_count"] for item in results) / total, 4),
        "avg_llm_call_count": round(sum(item["llm_call_count"] for item in results) / total, 4),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the enterprise multi-agent data benchmark.")
    parser.add_argument("--agent-mode", choices=["deterministic", "llm"], default="deterministic")
    parser.add_argument("--skip-robust", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    init_enterprise_db()
    system = MultiAgentSystem(agent_mode=args.agent_mode)
    cases = MULTI_AGENT_TEST_CASES[: args.limit] if args.limit else MULTI_AGENT_TEST_CASES
    results = [evaluate_case(case, system, noisy=False) for case in cases]
    robust_results = [] if args.skip_robust else [evaluate_case(case, system, noisy=True) for case in cases]
    summary = summarize(results, robust_results)
    output = {
        "agent_mode": args.agent_mode,
        "summary": summary,
        "results": results,
        "robust_results": robust_results,
    }
    output_path = args.output or (
        "benchmark_results_multi_agent_llm.json" if args.agent_mode == "llm" else "benchmark_results_multi_agent.json"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print("=== ENTERPRISE MULTI-AGENT SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Saved results to {output_path}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
