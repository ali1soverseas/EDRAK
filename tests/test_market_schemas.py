from edrak.agents.market_intelligence.schemas import TaskPlan


def test_task_plan_schema_names_its_fields():
    schema = TaskPlan.model_json_schema()
    assert "tasks" in schema["properties"]
