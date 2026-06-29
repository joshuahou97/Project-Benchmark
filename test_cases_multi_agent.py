from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class MultiAgentCase:
    id: str
    question: str
    gold_route: Tuple[str, ...]
    gold_sql: str
    answer_type: str
    expected: Dict[str, Any]
    notes: str = ""
    required_facts: List[str] = field(default_factory=list)


MULTI_AGENT_TEST_CASES: List[MultiAgentCase] = [
    MultiAgentCase(
        id="highest_salary_sql_only",
        question="Who earns the highest salary? Return the employee name and salary.",
        gold_route=("sql",),
        gold_sql="SELECT name, salary FROM employees ORDER BY salary DESC LIMIT 1;",
        answer_type="sql_rows",
        expected={"rows": [("Alexander Lewis", 40000)]},
        required_facts=["Alexander Lewis", "40000"],
    ),
    MultiAgentCase(
        id="avg_salary_by_dept_sql_only",
        question="Show average salary per department, from highest to lowest.",
        gold_route=("sql",),
        gold_sql=(
            "SELECT dept, AVG(salary) AS avg_salary "
            "FROM employees GROUP BY dept ORDER BY avg_salary DESC;"
        ),
        answer_type="sql_rows",
        expected={
            "rows": [
                ("Engineering", 32285.714285714286),
                ("Finance", 27333.333333333332),
                ("Product", 26500.0),
                ("Marketing", 21500.0),
                ("Sales", 21000.0),
                ("HR", 21000.0),
            ]
        },
        required_facts=["Engineering", "32285"],
    ),
    MultiAgentCase(
        id="salary_variance_department",
        question="Which department has the largest salary variance? Include the variance value.",
        gold_route=("sql", "python"),
        gold_sql="SELECT dept, salary FROM employees;",
        answer_type="python_result",
        expected={
            "analysis": "group_variance_max",
            "group_key": "dept",
            "value_key": "salary",
            "label": "Finance",
            "value": 37555555.55555555,
            "tolerance": 1e-6,
        },
        required_facts=["Finance", "37555555"],
    ),
    MultiAgentCase(
        id="salary_std_by_dept",
        question="Calculate each department's salary standard deviation and rank them from high to low.",
        gold_route=("sql", "python"),
        gold_sql="SELECT dept, salary FROM employees;",
        answer_type="python_result",
        expected={
            "analysis": "group_std_desc",
            "group_key": "dept",
            "value_key": "salary",
            "rows": [
                ("Finance", 6128.258770283412),
                ("Engineering", 4620.274751084637),
                ("Sales", 3559.026084010437),
                ("HR", 3559.026084010437),
                ("Product", 1500.0),
                ("Marketing", 500.0),
            ],
            "tolerance": 1e-6,
        },
        required_facts=["Finance", "Engineering", "6128"],
    ),
    MultiAgentCase(
        id="largest_salary_range",
        question="Which department has the largest gap between its highest and lowest salary?",
        gold_route=("sql", "python"),
        gold_sql="SELECT dept, salary FROM employees;",
        answer_type="python_result",
        expected={
            "analysis": "group_range_max",
            "group_key": "dept",
            "value_key": "salary",
            "label": "Finance",
            "value": 15000,
            "tolerance": 0,
        },
        required_facts=["Finance", "15000"],
    ),
    MultiAgentCase(
        id="salary_distribution_chart_data",
        question="Prepare chart data for a bar chart of average salary by department.",
        gold_route=("sql", "python"),
        gold_sql="SELECT dept, salary FROM employees;",
        answer_type="chart_data",
        expected={
            "analysis": "group_mean_chart",
            "group_key": "dept",
            "value_key": "salary",
            "chart_type": "bar",
            "rows": [
                ("Engineering", 32285.714285714286),
                ("Finance", 27333.333333333332),
                ("HR", 21000.0),
                ("Marketing", 21500.0),
                ("Product", 26500.0),
                ("Sales", 21000.0),
            ],
            "tolerance": 1e-6,
        },
        required_facts=["bar", "Engineering", "32285"],
    ),
    MultiAgentCase(
        id="highest_paid_vs_dept_average",
        question="For the highest paid employee, compare their salary with their department average.",
        gold_route=("sql", "python"),
        gold_sql="SELECT name, dept, salary FROM employees;",
        answer_type="python_result",
        expected={
            "analysis": "top_employee_vs_group_mean",
            "group_key": "dept",
            "value_key": "salary",
            "name_key": "name",
            "name": "Alexander Lewis",
            "dept": "Engineering",
            "salary": 40000,
            "dept_average": 32285.714285714286,
            "difference": 7714.285714285714,
            "tolerance": 1e-6,
        },
        required_facts=["Alexander Lewis", "40000", "32285", "7714"],
    ),
    MultiAgentCase(
        id="no_result_sql_only",
        question="List employees with salary above 100000.",
        gold_route=("sql",),
        gold_sql="SELECT name, salary FROM employees WHERE salary > 100000;",
        answer_type="sql_rows",
        expected={"rows": []},
        notes="edge: empty result",
        required_facts=["[]"],
    ),
]
