import sqlite3
from pathlib import Path
from typing import Iterable, Tuple


DB_PATH = "enterprise_company.db"


CUSTOMERS = [
    (1, "Acme Manufacturing", "Enterprise", "North America", "active", 62000, 0),
    (2, "Bright Retail", "Mid-Market", "North America", "active", 18000, 0),
    (3, "Cobalt Finance", "Enterprise", "Europe", "active", 54000, 0),
    (4, "Delta Health", "Enterprise", "Europe", "at_risk", 47000, 0),
    (5, "Evergreen Foods", "SMB", "North America", "active", 9000, 0),
    (6, "Futura Logistics", "Mid-Market", "APAC", "active", 23000, 0),
    (7, "Globex Internal Sandbox", "Enterprise", "North America", "active", 999999, 1),
    (8, "Helios Energy", "Enterprise", "APAC", "churned", 42000, 0),
]


ORDERS = [
    (101, 1, "2026-01-15", 15000, "closed"),
    (102, 1, "2026-03-12", 21000, "closed"),
    (103, 1, "2026-05-09", 26000, "closed"),
    (104, 2, "2026-02-02", 8000, "closed"),
    (105, 2, "2026-05-20", 10000, "closed"),
    (106, 3, "2026-01-30", 18000, "closed"),
    (107, 3, "2026-04-18", 36000, "closed"),
    (108, 4, "2026-02-15", 27000, "closed"),
    (109, 4, "2026-06-10", 20000, "closed"),
    (110, 5, "2026-03-05", 4000, "closed"),
    (111, 5, "2026-06-12", 5000, "closed"),
    (112, 6, "2026-01-21", 11000, "closed"),
    (113, 6, "2026-04-25", 12000, "closed"),
    (114, 7, "2026-02-01", 999999, "closed"),
    (115, 8, "2026-01-10", 42000, "closed"),
]


SUPPORT_TICKETS = [
    (1001, 1, "2026-05-01", "low", "closed", 6),
    (1002, 2, "2026-05-11", "medium", "closed", 18),
    (1003, 3, "2026-05-16", "high", "closed", 30),
    (1004, 4, "2026-06-14", "critical", "open", 72),
    (1005, 4, "2026-06-18", "high", "open", 54),
    (1006, 5, "2026-06-02", "low", "closed", 10),
    (1007, 6, "2026-06-08", "medium", "closed", 16),
    (1008, 8, "2026-04-02", "critical", "closed", 96),
]


ACCOUNT_MANAGERS = [
    (1, "Maya Chen", "Strategic"),
    (2, "Noah Patel", "Commercial"),
    (3, "Maya Chen", "Strategic"),
    (4, "Lena Ortiz", "Strategic"),
    (5, "Noah Patel", "Commercial"),
    (6, "Aiko Tanaka", "Commercial"),
    (7, "Maya Chen", "Strategic"),
    (8, "Aiko Tanaka", "Commercial"),
]


METRIC_GLOSSARY = {
    "high_value_active_revenue": {
        "definition": (
            "Total closed order revenue for active, non-internal customers whose annual contract value "
            "is at least 20000."
        ),
        "business_rules": [
            "Exclude internal test accounts.",
            "Only include customers with status = active.",
            "High-value means annual_contract_value >= 20000.",
            "Revenue is the sum of closed order amounts.",
        ],
        "required_tables": ["customers", "orders"],
        "required_columns": ["customer_id", "customer_name", "status", "is_internal", "annual_contract_value", "amount", "order_status"],
    },
    "at_risk_open_support": {
        "definition": "Open high or critical support exposure for non-internal customers marked at risk.",
        "business_rules": [
            "Exclude internal test accounts.",
            "Only include customers with status = at_risk.",
            "Only include support tickets with status = open and severity in high or critical.",
        ],
        "required_tables": ["customers", "support_tickets"],
        "required_columns": ["customer_id", "customer_name", "status", "is_internal", "severity", "ticket_status", "resolution_hours"],
    },
    "manager_revenue_concentration": {
        "definition": "Closed revenue by account manager, excluding internal accounts.",
        "business_rules": [
            "Exclude internal test accounts.",
            "Revenue is the sum of closed order amounts.",
            "Group customers by account manager.",
        ],
        "required_tables": ["customers", "orders", "account_managers"],
        "required_columns": ["customer_id", "manager_name", "is_internal", "amount", "order_status"],
    },
    "regional_active_customer_revenue": {
        "definition": "Closed revenue by region for active, non-internal customers.",
        "business_rules": [
            "Exclude internal test accounts.",
            "Only include customers with status = active.",
            "Revenue is the sum of closed order amounts grouped by region.",
        ],
        "required_tables": ["customers", "orders"],
        "required_columns": ["customer_id", "region", "status", "is_internal", "amount", "order_status"],
    },
    "segment_active_revenue_mix": {
        "definition": "Closed revenue by customer segment for active, non-internal customers.",
        "business_rules": [
            "Exclude internal test accounts.",
            "Only include customers with status = active.",
            "Revenue is the sum of closed order amounts grouped by segment.",
        ],
        "required_tables": ["customers", "orders"],
        "required_columns": ["customer_id", "segment", "status", "is_internal", "amount", "order_status"],
    },
    "churned_customer_revenue": {
        "definition": "Closed revenue from churned, non-internal customers.",
        "business_rules": [
            "Exclude internal test accounts.",
            "Only include customers with status = churned.",
            "Revenue is the sum of closed order amounts.",
        ],
        "required_tables": ["customers", "orders"],
        "required_columns": ["customer_id", "customer_name", "status", "is_internal", "amount", "order_status"],
    },
}


SCHEMA_TEXT = """
Table: customers
- customer_id INTEGER PRIMARY KEY
- customer_name TEXT
- segment TEXT
- region TEXT
- status TEXT
- annual_contract_value INTEGER
- is_internal INTEGER

Table: orders
- order_id INTEGER PRIMARY KEY
- customer_id INTEGER
- order_date TEXT
- amount INTEGER
- order_status TEXT

Table: support_tickets
- ticket_id INTEGER PRIMARY KEY
- customer_id INTEGER
- created_at TEXT
- severity TEXT
- ticket_status TEXT
- resolution_hours INTEGER

Table: account_managers
- customer_id INTEGER
- manager_name TEXT
- team TEXT
""".strip()


def _insert_many(cur: sqlite3.Cursor, sql: str, rows: Iterable[Tuple]) -> None:
    cur.executemany(sql, rows)


def init_enterprise_db(db_path: str = DB_PATH) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.executescript(
        """
        DROP TABLE IF EXISTS account_managers;
        DROP TABLE IF EXISTS support_tickets;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS customers;

        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            customer_name TEXT NOT NULL,
            segment TEXT NOT NULL,
            region TEXT NOT NULL,
            status TEXT NOT NULL,
            annual_contract_value INTEGER NOT NULL,
            is_internal INTEGER NOT NULL
        );

        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            order_date TEXT NOT NULL,
            amount INTEGER NOT NULL,
            order_status TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );

        CREATE TABLE support_tickets (
            ticket_id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            severity TEXT NOT NULL,
            ticket_status TEXT NOT NULL,
            resolution_hours INTEGER NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );

        CREATE TABLE account_managers (
            customer_id INTEGER NOT NULL,
            manager_name TEXT NOT NULL,
            team TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );
        """
    )

    _insert_many(cur, "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)", CUSTOMERS)
    _insert_many(cur, "INSERT INTO orders VALUES (?, ?, ?, ?, ?)", ORDERS)
    _insert_many(cur, "INSERT INTO support_tickets VALUES (?, ?, ?, ?, ?, ?)", SUPPORT_TICKETS)
    _insert_many(cur, "INSERT INTO account_managers VALUES (?, ?, ?)", ACCOUNT_MANAGERS)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_enterprise_db()
    print(f"Initialized {DB_PATH}")
