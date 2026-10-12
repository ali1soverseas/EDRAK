"""Run the competitor worker from the command line.

    python -m edrak.agents.competitor_intelligence "Nile Ledger" "How do we compare on SME invoicing?" \
        --competitors "Kanz Pay,Sada" --markets Egypt --offering "SME invoicing"

Leave out --competitors to let the worker find them itself.
"""

from __future__ import annotations

import argparse

from ...contracts import BusinessContext, CompanyProfile, CompetitorRef, ResearchTask, UseCase, WorkerType
from . import run_research


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("company")
    parser.add_argument("goal")
    parser.add_argument("--competitors", default="", help="Comma-separated names. Empty: discover them.")
    parser.add_argument("--markets", default="")
    parser.add_argument("--offering", default=None)
    parser.add_argument("--website", default=None, help="The company's own website.")
    parser.add_argument("--use-case", default=UseCase.COMPETITIVE_INTELLIGENCE.value, choices=[u.value for u in UseCase])
    args = parser.parse_args()

    names = [n.strip() for n in args.competitors.split(",") if n.strip()]
    context = BusinessContext(
        use_case=UseCase(args.use_case),
        targets=names,
        competitors=[CompetitorRef(name=n) for n in names],
        markets=[m.strip() for m in args.markets.split(",") if m.strip()],
        offering=args.offering,
    )
    task = ResearchTask(
        parent_request_id="cli",
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        goal=args.goal,
        focus="Features, pricing and positioning relevant to the goal.",
        company_profile=CompanyProfile(name=args.company, website=args.website),
        business_context=context,
    )
    run_research(task, print_result=True)


if __name__ == "__main__":
    main()
