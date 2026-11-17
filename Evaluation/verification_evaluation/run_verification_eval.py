
#!/usr/bin/env python3
"""
Evaluate Edrak Verification output against verification_golden.json.

LLM Judge:
    OpenAI through the official chat completions endpoint.

Install:
    pip install deepeval requests

Environment:
    OPENAI_API_KEY is read from the repository .env file.
    OPENAI_MODEL=gpt-4o-mini

Usage:
    python run_verification_eval.py \
        --golden verification_golden.json \
        --predictions actual_verification_trace.json \
        --output evaluation_report.json

If --predictions is omitted, the script evaluates the original
verification statuses stored in the golden dataset.
"""

import argparse
import json
import sys
from pathlib import Path


def _make_openai_judge():
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from Evaluation.generic.evaluator import DEFAULT_CONFIG, _make_openai_judge as make_judge

    return make_judge(DEFAULT_CONFIG)


# ============================================================
# JSON UTILITIES
# ============================================================

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_assessments(obj):

    if isinstance(obj, list):
        return obj

    if isinstance(obj, dict) and isinstance(obj.get("assessments"), list):
        return obj["assessments"]

    if isinstance(obj, dict) and isinstance(obj.get("spans"), list):

        for span in obj["spans"]:

            if span.get("name") == "assess_findings":

                output = span.get("output", {})

                if isinstance(output, dict):
                    return output.get("assessments", [])

    if isinstance(obj, dict) and isinstance(obj.get("result"), dict):

        findings = obj["result"].get("findings")

        if isinstance(findings, list):
            return findings

    raise ValueError(
        "Could not find an assessments array in predictions JSON."
    )


def normalize_status(value):

    value = str(value or "").strip().lower()

    aliases = {
        "verified": "verified",
        "insufficient": "insufficient",
    }

    if value not in aliases:
        raise ValueError(
            f"Unsupported verification status: {value!r}. "
            "Expected verified or insufficient."
        )

    return aliases[value]


def make_context(case):

    context = []

    for ev in case["input"].get("evidence", []):

        text = ev.get("excerpt") or ev.get("extracted_fact") or ""

        if text:

            label = (
                ev.get("source_title")
                or ev.get("publisher")
                or ev.get("source_url")
                or ev["evidence_id"]
            )

            context.append(
                f"Evidence {ev['evidence_id']} ({label}): {text}"
            )

    return context


# ============================================================
# DETERMINISTIC METRICS
# ============================================================

def deterministic_metrics(cases, predictions):

    tp = fp = fn = tn = 0

    rows = []

    for case in cases:

        fid = case["finding_id"]

        expected = normalize_status(
            case["golden_reference"]["verification_status"]
        )

        actual_obj = predictions.get(fid, {})

        predicted = normalize_status(
            actual_obj.get("verification_status")
        )

        if expected == "verified" and predicted == "verified":
            tp += 1

        elif expected == "insufficient" and predicted == "verified":
            fp += 1

        elif expected == "verified" and predicted == "insufficient":
            fn += 1

        else:
            tn += 1

        rows.append({
            "test_case_id": case["test_case_id"],
            "finding_id": fid,
            "worker": case["worker"],
            "expected_status": expected,
            "predicted_status": predicted,
            "correct": expected == predicted,
            "review_reason": case["golden_reference"]["review_reason"]
        })

    n = len(rows)

    accuracy = (tp + tn) / n if n else 0.0

    precision = tp / (tp + fp) if tp + fp else 0.0

    recall = tp / (tp + fn) if tp + fn else 0.0

    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall else 0.0
    )

    return {
        "count": n,

        "confusion_matrix_positive_class_verified": {
            "TP": tp,
            "FP": fp,
            "FN": fn,
            "TN": tn
        },

        "accuracy": accuracy,
        "precision_verified": precision,
        "recall_verified": recall,
        "f1_verified": f1,

        "false_verification_rate_among_agent_verified": (
            fp / (tp + fp) if tp + fp else 0.0
        ),

        "false_positive_rate_among_expected_insufficient": (
            fp / (fp + tn) if fp + tn else 0.0
        ),

        "per_case": rows
    }


# ============================================================
# DEEPEVAL METRICS
# ============================================================

def run_deepeval(cases, predictions):

    try:

        from deepeval.metrics import (
            GEval,
            FaithfulnessMetric,
            ContextualRelevancyMetric
        )

        from deepeval.test_case import (
            LLMTestCase,
            LLMTestCaseParams
        )

    except ImportError as exc:

        raise RuntimeError(
            "DeepEval is not installed. "
            "Run: pip install deepeval requests"
        ) from exc

    # One shared OpenAI judge for all metrics.
    model = _make_openai_judge()

    test_cases = []

    for case in cases:

        fid = case["finding_id"]

        pred = predictions[fid]

        context = make_context(case)

        expected = case["golden_reference"]

        actual_output = json.dumps(
            pred,
            ensure_ascii=False
        )

        expected_output = json.dumps(
            {
                "verification_status": expected["verification_status"],
                "review_reason": expected["review_reason"]
            },
            ensure_ascii=False
        )

        test_cases.append(
            LLMTestCase(
                input=case["input"]["finding_statement"],
                actual_output=actual_output,
                expected_output=expected_output,
                retrieval_context=context
            )
        )

    # --------------------------------------------------------
    # 1. Verification Decision Correctness
    # --------------------------------------------------------

    decision_metric = GEval(
        name="Verification Decision Correctness",

        criteria=(
            "Judge whether the predicted verification_status is correct "
            "for the finding, using the attached evidence and expected "
            "human-reviewed status. A finding is verified only if the "
            "evidence supports the complete claim as worded. If any "
            "material part is unsupported, outdated, misattributed, "
            "contradicted, or inferred beyond the evidence, the correct "
            "status is insufficient."
        ),

        evaluation_steps=[
            "Read the finding statement and attached evidence excerpts.",
            "Identify every material component of the finding, including "
            "company, product, geography, metric, and time period.",
            "Check whether the evidence directly supports all material "
            "components.",
            "Compare the predicted verification_status with the expected "
            "human-reviewed status.",
            "Penalize a verified decision when evidence supports only part "
            "of the finding or belongs to another company, period, or claim."
        ],

        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
            LLMTestCaseParams.RETRIEVAL_CONTEXT
        ],

        model=model,
        threshold=0.8
    )

    # --------------------------------------------------------
    # 2. Faithfulness
    # --------------------------------------------------------

    faithfulness_metric = FaithfulnessMetric(
        threshold=0.8,
        model=model,
        include_reason=True,
        async_mode=False
    )

    # --------------------------------------------------------
    # 3. Contextual Relevancy
    # --------------------------------------------------------

    contextual_relevancy_metric = ContextualRelevancyMetric(
        threshold=0.8,
        model=model,
        include_reason=True,
        async_mode=False
    )

    # --------------------------------------------------------
    # Run metrics
    # --------------------------------------------------------

    results = []

    metrics = [
        ("decision_correctness", decision_metric),
        ("faithfulness", faithfulness_metric),
        ("contextual_relevancy", contextual_relevancy_metric)
    ]

    for case, test_case in zip(cases, test_cases):

        fid = case["finding_id"]

        item = {
            "test_case_id": case["test_case_id"],
            "finding_id": fid
        }

        for metric_name, metric in metrics:

            try:

                metric.measure(test_case)

                item[metric_name] = {
                    "score": metric.score,
                    "passed": metric.is_successful(),
                    "reason": getattr(metric, "reason", None)
                }

            except Exception as exc:

                item[metric_name] = {
                    "error": str(exc)
                }

        results.append(item)

    return {
        "provider": "OpenAI",
        "model": model.get_model_name(),
        "endpoint": model.endpoint,
        "results": results
    }


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--golden",
        default="verification_golden.json"
    )

    parser.add_argument(
        "--predictions",
        default=None
    )

    parser.add_argument(
        "--output",
        default="verification_evaluation_report.json"
    )

    args = parser.parse_args()

    golden = load_json(args.golden)

    cases = golden["test_cases"]

    # --------------------------------------------------------
    # Load predictions
    # --------------------------------------------------------

    if args.predictions:

        raw = load_json(args.predictions)

        assessments = extract_assessments(raw)

        predictions = {
            a["finding_id"]: a
            for a in assessments
            if a.get("finding_id")
            and a.get("verification_status")
        }

    else:

        predictions = {
            c["finding_id"]: c["actual_verification"]
            for c in cases
        }

    missing = [
        c["finding_id"]
        for c in cases
        if c["finding_id"] not in predictions
    ]

    if missing:

        raise ValueError(
            f"Predictions are missing {len(missing)} finding IDs: {missing}"
        )

    # --------------------------------------------------------
    # Deterministic evaluation
    # --------------------------------------------------------

    deterministic = deterministic_metrics(
        cases,
        predictions
    )

    report = {
        "dataset": golden.get("dataset_name"),
        "trace_id": golden.get("trace_id"),

        "prediction_source": (
            args.predictions
            or "original trace baseline stored in golden dataset"
        ),

        "deterministic_metrics": deterministic
    }

    # --------------------------------------------------------
    # DeepEval evaluation
    # --------------------------------------------------------

    try:

        report["deepeval"] = run_deepeval(
            cases,
            predictions
        )

        report["deepeval"]["status"] = "completed"

    except Exception as exc:

        report["deepeval"] = {
            "status": "not_run",
            "reason": str(exc),
            "setup": (
                "Configure OPENAI_API_KEY in .env and install "
                "deepeval and requests."
            )
        }

    # --------------------------------------------------------
    # Save report
    # --------------------------------------------------------

    Path(args.output).write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "output": args.output,

                "deterministic_metrics": {
                    k: v
                    for k, v in deterministic.items()
                    if k != "per_case"
                },

                "deepeval_status": report["deepeval"].get(
                    "status",
                    "completed"
                ),

                "judge_model": report["deepeval"].get(
                    "model",
                    "Not initialized"
                )
            },
            indent=2
        )
    )


if __name__ == "__main__":
    main()
