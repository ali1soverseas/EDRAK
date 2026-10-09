"""A/B the two structured-output methods against this worker's real schemas.

Arm A is what ships, and it is measured by calling the shipping code itself:
`structured_call(..., repair=False)`. Arm B binds the same schema the same way
with `function_calling` and drops the schema-as-text instruction, on the theory
that a prompt describing the expected JSON suppresses the tool call.

Both arms use one model, built the way the worker builds it, so the method is
the only variable. `repair=False` means these are first-attempt numbers and a
retry cannot mask a failure. The conclusion is recorded in
docs/customer-trends-agent/README.md; re-run this if the model, the schemas or
langchain-ollama change, because a method inherited from another worker is not
evidence about this one.

    PYTHONPATH=backend/src python -m edrak.agents.customer_trends.llm.ab_structured_output

It spends 80 real model calls, so run it deliberately.
"""

import asyncio

from edrak.agents.customer_trends.llm.client import get_chat_model, structured_call
from edrak.agents.customer_trends.nodes import PlanDraft
from edrak.agents.customer_trends.schemas.findings import FindingsDraft, HeadlineDraft
from edrak.agents.customer_trends.tools.analyze_text import ChunkAnalysis
from langchain_core.messages import HumanMessage

RUNS = 10
ROLE = "planner"

CASES = [
    (
        "HeadlineDraft",
        HeadlineDraft,
        (
            "Two developers say GitLab Duo suggestions are accurate and fast. "
            "Write a one-line headline for a competitive brief."
        ),
    ),
    (
        "FindingsDraft",
        FindingsDraft,
        (
            "Evidence: Duo Agent Platform reached GA on 15 Jan 2026. "
            "Duo Code Suggestions ships in 14 IDEs. "
            "Draft the findings an executive would act on."
        ),
    ),
    (
        "PlanDraft",
        PlanDraft,
        (
            "Assess GitLab Duo against GitHub Copilot on AI code review. "
            "Draft a social query plan a research analyst would run."
        ),
    ),
    (
        "ChunkAnalysis",
        ChunkAnalysis,
        (
            "Chunk text: 'GitLab Duo supports 14 IDEs and is bundled with Ultimate "
            "seats. Code Suggestions was GA in 2025.' "
            "Label each item and propose candidate themes."
        ),
    ),
]


async def arm_a(llm, schema, prompt) -> bool:
    """The shipping path: json_schema plus the schema instruction, no repair."""
    try:
        await structured_call(llm, schema, [HumanMessage(content=prompt)], repair=False)
    except Exception:  # noqa: BLE001 - any failure is a failed trial
        return False
    return True


async def arm_b(llm, schema, prompt) -> bool:
    """function_calling, same binding, without the schema instruction."""
    runnable = llm.with_structured_output(
        schema, method="function_calling", include_raw=True
    )
    try:
        out = await runnable.ainvoke([HumanMessage(content=prompt)])
    except Exception:  # noqa: BLE001 - a refused tool call is a failed trial
        return False
    return out.get("parsed") is not None


async def main() -> None:
    llm = get_chat_model(ROLE)
    print(f"first-attempt success, {RUNS} runs per arm, role={ROLE}, model={llm.model}\n")
    print(f"{'schema':16} {'A: json_schema+instr':>22} {'B: function_calling':>21}")
    print("-" * 62)
    totals = {"A": 0, "B": 0}
    for name, schema, prompt in CASES:
        row = {}
        for arm, runner in (("A", arm_a), ("B", arm_b)):
            ok = sum([await runner(llm, schema, prompt) for _ in range(RUNS)])
            row[arm] = ok
            totals[arm] += ok
        print(f"{name:16} {row['A']:>10}/{RUNS}      {row['B']:>9}/{RUNS}")
    print("-" * 62)
    print(f"{'TOTAL':16} {totals['A']:>10}/{RUNS * len(CASES)}      "
          f"{totals['B']:>9}/{RUNS * len(CASES)}")


if __name__ == "__main__":
    asyncio.run(main())
