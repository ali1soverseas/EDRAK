import importlib.util

from tests.customer_trends.factories import BRIEFS

REPO = BRIEFS.parents[3]
SCRIPT = REPO / "scripts" / "customer_trends" / "print_routing.py"
ARCHITECTURE = REPO / "backend/src/edrak/agents/customer_trends/docs/ARCHITECTURE.md"


def load_script():  # type: ignore[no-untyped-def]  # a module loaded from a script path
    spec = importlib.util.spec_from_file_location("print_routing", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_routing_table_in_the_docs_is_what_the_script_prints() -> None:
    table = load_script().routing_table()
    assert table in ARCHITECTURE.read_text(encoding="utf-8"), (
        "docs/ARCHITECTURE.md is out of date: run scripts/customer_trends/print_routing.py"
    )


def test_every_capability_of_the_config_is_in_the_table() -> None:
    from edrak.agents.customer_trends.providers.config import load_providers_config

    config = load_providers_config()
    table = load_script().routing_table(config)
    for capability in config.routing:
        assert f"| `{capability}` |" in table
    assert "apify (off)" in table and table.count("(off)") == 1
