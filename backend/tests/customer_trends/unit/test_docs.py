import re

from edrak.agents.customer_trends.settings import Settings
from tests.customer_trends.factories import BRIEFS

PACKAGE = BRIEFS.parents[3] / "backend/src/edrak/agents/customer_trends"
README = (PACKAGE / "README.md").read_text(encoding="utf-8")
ENV_EXAMPLE = (BRIEFS.parents[3] / ".env.example").read_text(encoding="utf-8")


def test_the_readme_configuration_table_lists_every_setting() -> None:
    documented = set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \|", README, flags=re.MULTILINE))
    expected = {name.upper() for name in Settings.model_fields}
    assert expected <= documented, f"not in the README table: {sorted(expected - documented)}"
    assert documented <= expected, (
        f"in the README table but not a setting: {sorted(documented - expected)}"
    )


def test_the_env_example_names_every_worker_setting() -> None:
    names = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", ENV_EXAMPLE, flags=re.MULTILINE))
    settings = {name.upper() for name in Settings.model_fields} - {"EDRAK_ENV"}
    assert settings <= names, sorted(settings - names)
    assert "ENV" in names, "the shared environment name"


def test_the_links_of_the_readme_point_at_files_that_exist() -> None:
    for target in re.findall(r"\]\((docs/[^)#]+|\.\./[^)#]+)\)", README):
        assert (PACKAGE / target).resolve().is_file(), target


def test_the_documented_commands_exist() -> None:
    from edrak.agents.customer_trends import cli

    parser = cli.build_parser()
    for command in re.findall(r"customer-trends (run|show|runs)\b", README):
        assert command in {"run", "show", "runs"}
    assert parser.parse_args(["runs"]).command == "runs"
    for brief in re.findall(r"evals/customer_trends/briefs/(\w+\.json)", README):
        assert (BRIEFS / brief).is_file()
    assert (BRIEFS.parent / "checks.py").is_file()
    assert "customer-trends = " in (BRIEFS.parents[2] / "pyproject.toml").read_text(
        encoding="utf-8"
    )


def test_the_runbook_covers_the_failures_the_batch_names() -> None:
    runbook = (PACKAGE / "docs/RUNBOOK.md").read_text(encoding="utf-8")
    for heading in (
        "## Missing key",
        "## Quota or credits used up",
        "## An Apify actor changed its input",
        "## GDELT rate limit",
        "## YouTube quota or search cap",
    ):
        assert heading in runbook, heading
    assert "To be written" not in runbook


def test_no_doc_uses_an_em_dash() -> None:
    for path in [PACKAGE / "README.md", *(PACKAGE / "docs").glob("*.md")]:
        if path.name == "SPEC.md":
            continue
        assert chr(0x2014) not in path.read_text(encoding="utf-8"), path.name
