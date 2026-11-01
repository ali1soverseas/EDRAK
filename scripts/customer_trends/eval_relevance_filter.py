"""Measure the comment relevance filter against comments you labeled.

Run from backend/:
    uv run python ../scripts/customer_trends/eval_relevance_filter.py labeled.csv \
        --brief evals/customer_trends/briefs/competitive_intelligence.json \
        --alias "GitLab Inc." --product Duo

The CSV has the columns id, platform, parent_text, comment_text, label (relevant or irrelevant); see
evals/customer_trends/relevance/template.csv. Keep real labeled files in
evals/customer_trends/relevance/data/, which git ignores. The script prints precision and recall of
the relevant class, the drop rate and the estimated token reduction. It calls the model unless you
pass --no-llm, which counts every comment the free stages cannot decide as kept.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from edrak.agents.customer_trends.llm.client import LLMConfigError, get_chat_model
from edrak.agents.customer_trends.relevance.evaluation import (
    EvaluationError,
    evaluate,
    format_report,
    read_rows,
)
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.settings import get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("csv", type=Path, help="labeled comments")
    parser.add_argument(
        "--brief",
        type=Path,
        required=True,
        help="a task brief (JSON): company, competitors, question",
    )
    parser.add_argument(
        "--alias", action="append", default=[], help="another name for the company (repeatable)"
    )
    parser.add_argument(
        "--product", action="append", default=[], help="a product name (repeatable)"
    )
    parser.add_argument(
        "--focus-term", action="append", default=[], help="a focus keyword (repeatable)"
    )
    parser.add_argument("--no-llm", action="store_true", help="free stages only; no model call")
    parser.add_argument(
        "--show", type=int, default=10, help="how many missed and leaked comments to list"
    )
    return parser


async def run(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    try:
        rows = read_rows(args.csv)
        brief = TaskBrief.model_validate_json(args.brief.read_text(encoding="utf-8"))
    except (EvaluationError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    brief = brief.model_copy(
        update={
            "aliases": [*brief.aliases, *args.alias],
            "products": [*brief.products, *args.product],
            "focus_terms": [*brief.focus_terms, *args.focus_term],
        }
    )
    model_for = None if args.no_llm else (lambda: get_chat_model("relevance", settings=settings))
    if model_for is not None:
        try:
            model_for()  # fail now, with the setting named, not inside the first batch
        except LLMConfigError as exc:
            print(f"error: {exc} (or pass --no-llm)", file=sys.stderr)
            return 2
    report = await evaluate(rows, brief, settings, model_for=model_for)
    print(format_report(report, show=args.show))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
