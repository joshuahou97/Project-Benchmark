import sqlite3
from sqlalchemy import create_engine

from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import create_sql_agent
from langchain_openai import ChatOpenAI   # ✅ 改成 OpenAI 兼容接口

import config_local

DB_PATH = "company.db"

SEED_ROWS = [
    ("John Smith", "Engineering", "ML Engineer", 32000),
    ("Alice Johnson", "Engineering", "Backend Engineer", 26000),
    ("Michael Brown", "Product", "Product Manager", 28000),
    ("Emily Davis", "Sales", "Sales Manager", 24000),
    ("David Wilson", "HR", "HR Business Partner", 18000),
    ("Sophia Martinez", "Finance", "Accountant", 20000),
    ("Daniel Anderson", "Engineering", "Tech Lead", 38000),
    ("Olivia Taylor", "Sales", "Sales Representative", 16000),
    ("James Thomas", "Engineering", "Data Engineer", 30000),
    ("Isabella Moore", "Marketing", "Marketing Specialist", 22000),
    ("William Jackson", "Finance", "Financial Analyst", 27000),
    ("Mia White", "HR", "Recruiter", 19000),
    ("Benjamin Harris", "Engineering", "DevOps Engineer", 31000),
    ("Charlotte Martin", "Product", "UX Designer", 25000),
    ("Lucas Thompson", "Sales", "Account Executive", 23000),
    ("Amelia Garcia", "Marketing", "Content Strategist", 21000),
    ("Henry Clark", "Engineering", "Frontend Engineer", 29000),
    ("Evelyn Rodriguez", "Finance", "Controller", 35000),
    ("Alexander Lewis", "Engineering", "Software Architect", 40000),
    ("Harper Lee", "HR", "HR Manager", 26000),
]


def init_db():
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
        SEED_ROWS,
    )
    conn.commit()
    conn.close()


def build_agent(verbose: bool = False):
    # Connect to database
    engine = create_engine(f"sqlite:///{DB_PATH}")
    db = SQLDatabase(engine)

    # ✅ DeepSeek LLM（OpenAI 兼容 API）
    llm = ChatOpenAI(
        model=config_local.DEEPSEEK_MODEL,      # 例如 "deepseek-chat"
        temperature=0,
        api_key=config_local.DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com/v1",
    )

    # Create SQL Agent
    agent = create_sql_agent(
        llm=llm,
        db=db,
        verbose=verbose,
        agent_type="tool-calling",
    )
    return agent


def main():
    init_db()
    agent = build_agent(verbose=True)

    print("✅ DeepSeek SQL Agent ready. Type your question, or 'exit' to quit.\n")
    print("Examples:")
    print(" - What is the average salary in Engineering?")
    print(" - Who earns the highest salary?")
    print(" - List employees with salary > 25000 ordered by salary descending.\n")

    while True:
        q = input("You> ").strip()
        if q.lower() in {"exit", "quit"}:
            break
        out = agent.invoke({"input": q})
        print("\nAnswer>\n", out.get("output", out), "\n")


if __name__ == "__main__":
    main()