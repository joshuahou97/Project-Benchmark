import argparse
import json
import random
import sqlite3
import string
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Sequence, Tuple

from employee_dataset import DB_PATH, EMPLOYEE_ROWS
from multi_agent_agents import (
    AgentMessage,
    LLMClient,
    LLMPythonAnalysisAgent,
    LLMPlannerAgent,
    LLMReporterAgent,
    LLMSQLAgent,
    LLMVerifierAgent,
    PlannerAgent,
    PythonAnalysisAgent,
    ReporterAgent,
    TemplateSQLAgent,
    VerifierAgent,
    close_enough,
)
from test_cases_multi_agent import MULTI_AGENT_TEST_CASES, MultiAgentCase


def run_sql_raw_with_cols(db_path: str, sql: str) -> Tuple[List[Tuple[Any, ...]], List[str]]:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in (cur.description or [])]
    conn.close()
    return rows, cols


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (
            name   TEXT NOT NULL,
            dept   TEXT NOT NULL,
            title  TEXT NOT NULL,
            salary INTEGER NOT NULL
        )
        """
    )
    cur.execute("DELETE FROM employees")
    cur.executemany(
        "INSERT INTO employees (name, dept, title, salary) VALUES (?, ?, ?, ?)",
        EMPLOYEE_ROWS,
    )
    conn.commit()
    conn.close()


def normalize_value(value: Any, float_ndigits: int = 6) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, float):
        return round(value, float_ndigits)
    return value


def normalize_rows(rows: Sequence[Tuple[Any, ...]], float_ndigits: int = 6) -> List[Tuple[Any, ...]]:
    return [tuple(normalize_value(value, float_ndigits) for value in row) for row in rows]


def rows_equal(actual: Sequence[Tuple[Any, ...]], expected: Sequence[Tuple[Any, ...]]) -> bool:
    return Counter(normalize_rows(actual)) == Counter(normalize_rows(expected))


def add_noise(question: str) -> str:
    noise_type = random.choice(["delete_char", "swap_char", "replace_char", "keyboard_typo"])
    chars = list(question)
    if not chars:
        return question

    if noise_type == "delete_char" and len(chars) > 4:
        del chars[random.randint(0, len(chars) - 1)]
        return "".join(chars)

    if noise_type == "swap_char" and len(chars) > 4:
        idx = random.randint(0, len(chars) - 2)
        chars[idx], chars[idx + 1] = chars[idx + 1], chars[idx]
        return "".join(chars)

    if noise_type == "replace_char":
        chars[random.randint(0, len(chars) - 1)] = random.choice(string.ascii_lowercase)
        return "".join(chars)

    keyboard_neighbors = {
        "a": "sqwz",
        "b": "vghn",
        "c": "xdfv",
        "d": "erfcxs",
        "e": "rdsw",
        "f": "rtgvcd",
        "g": "tyhbvf",
        "h": "yujnbg",
        "i": "okju",
        "j": "uikmnh",
        "k": "iolmj",
        "l": "opk",
        "m": "njk",
        "n": "bhjm",
        "o": "pikl",
        "p": "ol",
        "q": "wa",
        "r": "tfde",
        "s": "wedxza",
        "t": "ygfr",
        "u": "yihj",
        "v": "cfgb",
        "w": "qase",
        "x": "zsdc",
        "y": "uhtg",
        "z": "asx",
    }
    idx = random.randint(0, len(chars) - 1)
    ch = chars[idx].lower()
    if ch in keyboard_neighbors:
        chars[idx] = random.choice(keyboard_neighbors[ch])
    return "".join(chars)


class MultiAgentOrchestrator:
    def __init__(self, agent_mode: str = "deterministic", llm_verifier: bool = False, llm_reporter: bool = False):
        self.agent_mode = agent_mode
        self.llm_client = LLMClient() if agent_mode == "llm" else None

        if self.llm_client:
            self.planner = LLMPlannerAgent(self.llm_client)
            self.sql_agent = LLMSQLAgent(self.llm_client)
            self.python_agent = LLMPythonAnalysisAgent(self.llm_client)
            self.verifier = LLMVerifierAgent(self.llm_client if llm_verifier else None)
            self.reporter = LLMReporterAgent(self.llm_client if llm_reporter else None)
        else:
            self.planner = PlannerAgent()
            self.sql_agent = TemplateSQLAgent()
            self.python_agent = PythonAnalysisAgent()
            self.verifier = VerifierAgent()
            self.reporter = ReporterAgent()

    def run(self, question: str) -> Dict[str, Any]:
        trace: List[AgentMessage] = []
        llm_start = len(self.llm_client.calls) if self.llm_client else 0

        plan = self.planner.plan(question)
        trace.append(AgentMessage("planner", {"route": plan.route, "steps": plan.steps, "rationale": plan.rationale}))

        sql = self.sql_agent.generate_sql(question, plan)
        trace.append(AgentMessage("sql_agent", {"sql": sql}))

        sql_error = None
        try:
            sql_rows, sql_columns = run_sql_raw_with_cols(DB_PATH, sql)
        except Exception as exc:
            sql_rows, sql_columns = [], []
            sql_error = str(exc)
        trace.append(AgentMessage("sql_executor", {"columns": sql_columns, "rows": sql_rows, "error": sql_error}))

        python_result: Dict[str, Any] = {}
        python_error = None
        if "python" in plan.route and sql_error is None:
            try:
                python_result = self.python_agent.analyze(question, sql_columns, sql_rows)
            except Exception as exc:
                python_error = str(exc)
                python_result = {"analysis": "error", "error": python_error}
            trace.append(AgentMessage("python_agent", python_result))

        verifier_result = self.verifier.verify(plan, sql_rows, python_result)
        if sql_error:
            verifier_result["passed"] = False
            verifier_result["issues"].append(f"SQL execution failed: {sql_error}")
            verifier_result["issue_count"] = len(verifier_result["issues"])
        if python_error:
            verifier_result["passed"] = False
            verifier_result["issues"].append(f"Python analysis failed: {python_error}")
            verifier_result["issue_count"] = len(verifier_result["issues"])
        trace.append(AgentMessage("verifier", verifier_result))

        final_answer = self.reporter.report(question, plan, sql_rows, python_result)
        trace.append(AgentMessage("reporter", {"final_answer": final_answer}))
        llm_calls = []
        if self.llm_client:
            llm_calls = [
                {
                    "agent": call.agent,
                    "output": call.output,
                    "token_usage": call.token_usage,
                }
                for call in self.llm_client.calls[llm_start:]
            ]

        return {
            "plan": plan,
            "sql": sql,
            "sql_rows": sql_rows,
            "sql_columns": sql_columns,
            "python_result": python_result,
            "verifier": verifier_result,
            "final_answer": final_answer,
            "trace": trace,
            "llm_calls": llm_calls,
        }


def evaluate_sql_correctness(case: MultiAgentCase, actual_rows: Sequence[Tuple[Any, ...]]) -> bool:
    if case.answer_type != "sql_rows":
        return True
    return rows_equal(actual_rows, case.expected["rows"])


def evaluate_python_correctness(case: MultiAgentCase, result: Dict[str, Any]) -> bool:
    if case.answer_type == "sql_rows":
        return True

    expected = case.expected
    if result.get("analysis") != expected.get("analysis"):
        return False

    tolerance = expected.get("tolerance", 1e-6)

    if "label" in expected and result.get("label") != expected["label"]:
        return False
    if "value" in expected and not close_enough(result.get("value"), expected["value"], tolerance):
        return False

    if "rows" in expected:
        actual_rows = result.get("rows", [])
        if len(actual_rows) != len(expected["rows"]):
            return False
        for actual, exp in zip(actual_rows, expected["rows"]):
            if actual[0] != exp[0] or not close_enough(actual[1], exp[1], tolerance):
                return False

    for key in ["name", "dept", "salary", "dept_average", "difference", "chart_type"]:
        if key in expected and not close_enough(result.get(key), expected[key], tolerance):
            return False

    return True


def required_fact_completeness(answer: str, required_facts: Sequence[str]) -> float:
    if not required_facts:
        return 1.0
    answer_lower = answer.lower()
    matched = sum(1 for fact in required_facts if any(part in answer_lower for part in fact.lower().split()))
    return matched / len(required_facts)


def evaluate_case(case: MultiAgentCase, orchestrator: MultiAgentOrchestrator, noisy: bool = False) -> Dict[str, Any]:
    question = add_noise(case.question) if noisy else case.question
    t0 = time.perf_counter()
    run = orchestrator.run(question)
    latency = time.perf_counter() - t0

    route = tuple(run["plan"].route)
    route_correct = route == case.gold_route
    sql_correct = evaluate_sql_correctness(case, run["sql_rows"])
    python_correct = evaluate_python_correctness(case, run["python_result"])
    final_correct = route_correct and sql_correct and python_correct and run["verifier"]["passed"]
    completeness = required_fact_completeness(run["final_answer"], case.required_facts)

    return {
        "id": case.id,
        "question": case.question,
        "run_question": question,
        "gold_route": case.gold_route,
        "planned_route": route,
        "route_correct": route_correct,
        "gold_sql": case.gold_sql,
        "executed_sql": run["sql"],
        "sql_rows": normalize_rows(run["sql_rows"]),
        "sql_correct": sql_correct,
        "python_result": run["python_result"],
        "python_correct": python_correct,
        "verifier_passed": run["verifier"]["passed"],
        "verifier_issues": run["verifier"]["issues"],
        "final_answer": run["final_answer"],
        "final_correct": final_correct,
        "completeness": round(completeness, 4),
        "latency_sec": round(latency, 4),
        "round_count": len(run["trace"]),
        "sql_query_count": 1,
        "python_execution_count": 1 if "python" in route else 0,
        "llm_call_count": len(run.get("llm_calls", [])),
        "llm_calls": run.get("llm_calls", []),
        "trace": [
            {"agent": message.agent, "content": message.content}
            for message in run["trace"]
        ],
        "notes": case.notes,
    }


def summarize(results: Sequence[Dict[str, Any]], robust_results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    if total == 0:
        return {}

    final_correct = sum(1 for item in results if item["final_correct"])
    route_correct = sum(1 for item in results if item["route_correct"])
    sql_correct = sum(1 for item in results if item["sql_correct"])
    python_cases = [item for item in results if "python" in item["gold_route"]]
    python_correct = sum(1 for item in python_cases if item["python_correct"])
    verifier_passed = sum(1 for item in results if item["verifier_passed"])
    robust_correct = sum(1 for item in robust_results if item["final_correct"])
    llm_call_count = sum(item.get("llm_call_count", 0) for item in results)

    return {
        "total": total,
        "final_accuracy": round(final_correct / total, 4),
        "tool_routing_accuracy": round(route_correct / total, 4),
        "sql_correctness": round(sql_correct / total, 4),
        "python_correctness": round(python_correct / len(python_cases), 4) if python_cases else 1.0,
        "verifier_pass_rate": round(verifier_passed / total, 4),
        "result_completeness": round(sum(item["completeness"] for item in results) / total, 4),
        "robust_final_accuracy": round(robust_correct / len(robust_results), 4) if robust_results else None,
        "robust_drop": round((final_correct / total) - (robust_correct / len(robust_results)), 4)
        if robust_results
        else None,
        "avg_latency_sec": round(sum(item["latency_sec"] for item in results) / total, 4),
        "avg_round_count": round(sum(item["round_count"] for item in results) / total, 4),
        "avg_sql_query_count": round(sum(item["sql_query_count"] for item in results) / total, 4),
        "avg_python_execution_count": round(sum(item["python_execution_count"] for item in results) / total, 4),
        "avg_llm_call_count": round(llm_call_count / total, 4),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the multi-agent SQL + Python benchmark.")
    parser.add_argument(
        "--agent-mode",
        choices=["deterministic", "llm"],
        default="deterministic",
        help="Use deterministic reference agents or LLM-backed agents.",
    )
    parser.add_argument("--llm-verifier", action="store_true", help="Use an LLM verifier in LLM mode.")
    parser.add_argument("--llm-reporter", action="store_true", help="Use an LLM reporter in LLM mode.")
    parser.add_argument("--skip-robust", action="store_true", help="Skip noisy robustness runs to reduce LLM cost.")
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N cases.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for noisy robustness cases.")
    parser.add_argument("--output", default=None, help="Output JSON path.")
    return parser.parse_args()


def main():
    args = parse_args()
    random.seed(args.seed)
    init_db()
    orchestrator = MultiAgentOrchestrator(
        agent_mode=args.agent_mode,
        llm_verifier=args.llm_verifier,
        llm_reporter=args.llm_reporter,
    )

    cases = MULTI_AGENT_TEST_CASES[: args.limit] if args.limit else MULTI_AGENT_TEST_CASES
    results = [evaluate_case(case, orchestrator, noisy=False) for case in cases]
    robust_results = [] if args.skip_robust else [evaluate_case(case, orchestrator, noisy=True) for case in cases]
    summary = summarize(results, robust_results)

    output = {
        "agent_mode": args.agent_mode,
        "llm_verifier": args.llm_verifier,
        "llm_reporter": args.llm_reporter,
        "summary": summary,
        "results": results,
        "robust_results": robust_results,
    }

    print("=== MULTI-AGENT SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    output_path = args.output or (
        "benchmark_results_multi_agent_llm.json"
        if args.agent_mode == "llm"
        else "benchmark_results_multi_agent.json"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Saved results to {output_path}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
