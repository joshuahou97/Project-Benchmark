from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


@dataclass
class MultiAgentCase:
    id: str
    question: str
    expected_route: Tuple[str, ...]
    expected_metric: str
    expected_tables: Tuple[str, ...]
    expected_columns: Tuple[str, ...]
    expected_query: str
    expected_output_type: str
    expected: Dict[str, Any]
    expected_result_columns: Tuple[str, ...] = ()
    query_contract: str = "exact_result"
    required_facts: List[str] = field(default_factory=list)
    notes: str = ""


MULTI_AGENT_TEST_CASES = [
    MultiAgentCase(
        id="high_value_active_revenue",
        question="How much closed revenue came from high-value active customers? Exclude internal accounts.",
        expected_route=("metric", "discovery", "query"),
        expected_metric="high_value_active_revenue",
        expected_tables=("customers", "orders"),
        expected_columns=("customer_id", "customer_name", "status", "is_internal", "annual_contract_value", "amount", "order_status"),
        expected_query="""
        SELECT SUM(o.amount) AS revenue
        FROM customers c
        JOIN orders o ON c.customer_id = o.customer_id
        WHERE c.status='active'
          AND c.is_internal=0
          AND c.annual_contract_value >= 20000
          AND o.order_status='closed';
        """,
        expected_output_type="query_rows",
        expected={"rows": [(139000,)]},
        expected_result_columns=("revenue",),
        required_facts=["139000"],
    ),
    MultiAgentCase(
        id="at_risk_open_support",
        question="Which at-risk customers currently have open high-severity support exposure?",
        expected_route=("metric", "discovery", "query"),
        expected_metric="at_risk_open_support",
        expected_tables=("customers", "support_tickets"),
        expected_columns=("customer_id", "customer_name", "status", "is_internal", "severity", "ticket_status", "resolution_hours"),
        expected_query="""
        SELECT c.customer_name, t.severity, t.resolution_hours
        FROM customers c
        JOIN support_tickets t ON c.customer_id = t.customer_id
        WHERE c.status='at_risk'
          AND c.is_internal=0
          AND t.ticket_status='open'
          AND t.severity IN ('high', 'critical')
        ORDER BY t.resolution_hours DESC;
        """,
        expected_output_type="query_rows",
        expected={"rows": [("Delta Health", "critical", 72), ("Delta Health", "high", 54)]},
        expected_result_columns=("customer_name", "severity", "resolution_hours"),
        required_facts=["Delta Health", "critical", "72"],
    ),
    MultiAgentCase(
        id="manager_revenue_concentration",
        question="Show closed revenue by account manager and identify the manager with the highest concentration.",
        expected_route=("metric", "discovery", "query", "analysis"),
        expected_metric="manager_revenue_concentration",
        expected_tables=("customers", "orders", "account_managers"),
        expected_columns=("customer_id", "manager_name", "is_internal", "amount", "order_status"),
        expected_query="""
        SELECT am.manager_name, o.amount
        FROM customers c
        JOIN orders o ON c.customer_id = o.customer_id
        JOIN account_managers am ON c.customer_id = am.customer_id
        WHERE c.is_internal=0
          AND o.order_status='closed';
        """,
        expected_output_type="analysis_result",
        expected={
            "analysis": "group_sum_max",
            "label": "Maya Chen",
            "value": 116000,
            "all_values": {
                "Maya Chen": 116000,
                "Noah Patel": 27000,
                "Lena Ortiz": 47000,
                "Aiko Tanaka": 65000,
            },
        },
        expected_result_columns=("manager_name", "amount"),
        query_contract="aggregate_allowed",
        required_facts=["Maya Chen", "116000"],
    ),
    MultiAgentCase(
        id="regional_active_revenue_chart",
        question="Prepare chart-ready data for closed revenue by region among active customers.",
        expected_route=("metric", "discovery", "query", "analysis"),
        expected_metric="regional_active_customer_revenue",
        expected_tables=("customers", "orders"),
        expected_columns=("customer_id", "region", "status", "is_internal", "amount", "order_status"),
        expected_query="""
        SELECT c.region, o.amount
        FROM customers c
        JOIN orders o ON c.customer_id = o.customer_id
        WHERE c.status='active'
          AND c.is_internal=0
          AND o.order_status='closed';
        """,
        expected_output_type="analysis_result",
        expected={
            "analysis": "group_sum_chart",
            "chart_type": "bar",
            "rows": [
                ("APAC", 23000),
                ("Europe", 54000),
                ("North America", 89000),
            ],
        },
        expected_result_columns=("region", "amount"),
        query_contract="aggregate_allowed",
        required_facts=["bar", "North America", "89000"],
    ),
    MultiAgentCase(
        id="segment_active_revenue_mix",
        question="Prepare chart-ready data for closed revenue by customer segment among active customers.",
        expected_route=("metric", "discovery", "query", "analysis"),
        expected_metric="segment_active_revenue_mix",
        expected_tables=("customers", "orders"),
        expected_columns=("customer_id", "segment", "status", "is_internal", "amount", "order_status"),
        expected_query="""
        SELECT c.segment, o.amount
        FROM customers c
        JOIN orders o ON c.customer_id = o.customer_id
        WHERE c.status='active'
          AND c.is_internal=0
          AND o.order_status='closed';
        """,
        expected_output_type="analysis_result",
        expected={
            "analysis": "group_sum_chart",
            "chart_type": "bar",
            "rows": [
                ("Enterprise", 116000),
                ("Mid-Market", 41000),
                ("SMB", 9000),
            ],
        },
        expected_result_columns=("segment", "amount"),
        query_contract="aggregate_allowed",
        required_facts=["bar", "Enterprise", "116000"],
    ),
    MultiAgentCase(
        id="churned_customer_revenue",
        question="How much closed revenue came from churned customers? Exclude internal accounts.",
        expected_route=("metric", "discovery", "query"),
        expected_metric="churned_customer_revenue",
        expected_tables=("customers", "orders"),
        expected_columns=("customer_id", "customer_name", "status", "is_internal", "amount", "order_status"),
        expected_query="""
        SELECT SUM(o.amount) AS revenue
        FROM customers c
        JOIN orders o ON c.customer_id = o.customer_id
        WHERE c.status='churned'
          AND c.is_internal=0
          AND o.order_status='closed';
        """,
        expected_output_type="query_rows",
        expected={"rows": [(42000,)]},
        expected_result_columns=("revenue",),
        required_facts=["42000"],
    ),
]
