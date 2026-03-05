# benchmark_sql_agent.py
import time
import json
import sqlite3
import itertools
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter

from llm_sql_agent_deepseek import init_db, build_agent, DB_PATH
from test_cases_sql import TEST_CASES, Case  # 从外部文件导入


# ... 你的 run_sql_raw_with_cols / normalize / best_match_executed_sql 等函数保持不变 ...


def main():
    init_db()
    agent = build_agent(verbose=False)

    # -------- Patch db.run to log SQL queries ----------
    sql_logs: List[str] = []

    db_obj = None
    for tool in getattr(agent, "tools", []) or []:
        if hasattr(tool, "db"):
            db_obj = tool.db
            break

    if db_obj is None:
        raise RuntimeError(
            "Could not locate SQLDatabase from agent.tools. "
            "Try agent.get_tools() or inspect agent structure."
        )

    original_run = db_obj.run

    def logged_run(command: str, *args, **kwargs):
        sql_logs.append(command)
        return original_run(command, *args, **kwargs)

    db_obj.run = logged_run  # monkey patch

    # -------- Benchmark cases ----------
    cases: List[Case] = TEST_CASES  # ✅ 用外部文件的用例

    results: List[Dict[str, Any]] = []
    total_pass = 0

    for c in cases:
        sql_logs.clear()

        t0 = time.perf_counter()
        out = agent.invoke({"input": c.question})
        t1 = time.perf_counter()

        executed_sql, agent_rows, passed, match_err = best_match_executed_sql(
            DB_PATH,
            sql_logs,
            c.gold_sql,
            ignore_order=True,
            float_ndigits=6,
        )

        gold_rows_raw, _ = run_sql_raw_with_cols(DB_PATH, c.gold_sql)
        gold_rows = normalize_rows(gold_rows_raw)

        error = match_err
        if executed_sql is None and error is None:
            error = "No matching SELECT found in agent SQL logs."

        total_pass += int(passed)

        results.append({
            "id": c.id,
            "question": c.question,
            "gold_sql": c.gold_sql,
            "gold_rows": gold_rows,
            "executed_sql": executed_sql,
            "agent_rows": agent_rows,
            "passed": passed,
            "latency_sec": round(t1 - t0, 4),
            "sql_query_count": len(sql_logs),
            "sql_logs": sql_logs.copy(),
            "agent_output": out.get("output", out),
            "notes": c.notes,
            "error": error,
        })

    summary = {
        "total": len(cases),
        "passed": total_pass,
        "accuracy": round(total_pass / len(cases), 4),
    }

    print("=== SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\n=== DETAILS ===")
    print(json.dumps(results, indent=2, ensure_ascii=False))

    with open("benchmark_results.json", "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()