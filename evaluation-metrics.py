# benchmark_sql_agent.py
import time
import json
import sqlite3
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple

# import from your file
from llm_sql_agent_gemini import init_db, build_agent, DB_PATH

@dataclass
class Case:
    id: str
    question: str
    gold_sql: str
    # optional: postprocess / tolerance, etc.
    notes: str = ""

def run_sql_raw(db_path: str, sql: str) -> List[Tuple]:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    conn.close()
    return rows

def normalize_rows(rows: List[Tuple]) -> List[Tuple]:
    # make deterministic comparison: strip strings, keep tuples
    norm = []
    for r in rows:
        rr = []
        for x in r:
            if isinstance(x, str):
                rr.append(x.strip())
            else:
                rr.append(x)
        norm.append(tuple(rr))
    return norm

def main():
    init_db()
    agent = build_agent(verbose=False)

    # -------- Patch db.run to log SQL queries ----------
    # create_sql_agent uses tools which call SQLDatabase.run internally.
    # We can reach db through agent's tools by finding a tool that has .db
    sql_logs: List[str] = []

    # Try to locate the SQLDatabase instance from the agent
    db_obj = None
    for tool in getattr(agent, "tools", []) or []:
        # common: tool has .db
        if hasattr(tool, "db"):
            db_obj = tool.db
            break

    if db_obj is None:
        raise RuntimeError("Could not locate SQLDatabase from agent.tools. "
                           "Try agent.get_tools() or inspect agent structure.")

    original_run = db_obj.run

    def logged_run(command: str, *args, **kwargs):
        sql_logs.append(command)
        return original_run(command, *args, **kwargs)

    db_obj.run = logged_run  # monkey patch

    # -------- Benchmark cases ----------
    cases: List[Case] = [
        Case(
            id="avg_salary_engineering",
            question="What is the average salary in Engineering?",
            gold_sql="SELECT AVG(salary) FROM employees WHERE dept='Engineering';",
        ),
        Case(
            id="highest_salary",
            question="Who earns the highest salary?",
            gold_sql="SELECT name, salary FROM employees ORDER BY salary DESC LIMIT 1;",
        ),
        Case(
            id="salary_gt_25000_desc",
            question="List employees with salary > 25000 ordered by salary descending.",
            gold_sql="SELECT name, salary FROM employees WHERE salary > 25000 ORDER BY salary DESC;",
        ),
        Case(
            id="count_by_dept",
            question="How many employees are in each department?",
            gold_sql="SELECT dept, COUNT(*) FROM employees GROUP BY dept ORDER BY dept;",
        ),
        Case(
            id="top2_engineering",
            question="Top 2 highest paid people in Engineering (name + salary).",
            gold_sql=("SELECT name, salary FROM employees "
                      "WHERE dept='Engineering' ORDER BY salary DESC LIMIT 2;"),
        ),
        
        Case(
            id="min_salary",
            question="Who has the lowest salary?",
            gold_sql="SELECT name, salary FROM employees ORDER BY salary ASC LIMIT 1;",
        ),
        Case(
            id="dept_avg_salary_desc",
            question="Show average salary per department, from highest to lowest.",
            gold_sql=("SELECT dept, AVG(salary) AS avg_salary "
                      "FROM employees GROUP BY dept ORDER BY avg_salary DESC;"),
        ),
        Case(
            id="marketing_titles",
            question="List all job titles in Marketing.",
            gold_sql="SELECT title FROM employees WHERE dept='Marketing' ORDER BY title;",
        ),
        Case(
            id="people_named_ends_with_son",
            question="List employees whose name ends with 'son'.",
            gold_sql="SELECT name FROM employees WHERE name LIKE '%son' ORDER BY name;",
        ),
        Case(
            id="no_result_edge",
            question="List employees with salary > 100000.",
            gold_sql="SELECT name, salary FROM employees WHERE salary > 100000;",
            notes="edge: empty result",
        ),
    ]

    results: List[Dict[str, Any]] = []
    total_pass = 0

    for c in cases:
        sql_logs.clear()
        gold_rows = normalize_rows(run_sql_raw(DB_PATH, c.gold_sql))

        t0 = time.perf_counter()
        out = agent.invoke({"input": c.question})
        t1 = time.perf_counter()

        # We compare oracle rows with the *last SELECT-like* SQL the agent executed.
        # Agent might run schema/table listing too; we want final query.
        executed_sql = None
        for s in reversed(sql_logs):
            if isinstance(s, str) and "select" in s.lower():
                executed_sql = s
                break

        agent_rows = None
        error = None
        try:
            if executed_sql is not None:
                agent_rows = normalize_rows(run_sql_raw(DB_PATH, executed_sql))
        except Exception as e:
            error = f"Agent SQL failed: {e}"

        passed = (agent_rows == gold_rows) if (agent_rows is not None) else False
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

    # Optional: save to file for later analysis
    with open("benchmark_results.json", "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()