import pytest
from langfuse.experiment import Evaluation

from edrak.core.observability import _safe_format, get_versioned_prompt, is_observability_enabled
from edrak.evaluation.langfuse_runner import get_git_context, verification_accuracy_evaluator


def test_safe_format():
    template = "Hello {name}, your role is {{role}}."
    result = _safe_format(template, name="Agent", role="Analyst")
    assert result == "Hello Agent, your role is Analyst."


def test_versioned_prompt_fallback():
    template = "Evaluate claim: {claim}"
    out = get_versioned_prompt("non_existent_prompt_test", template, claim="Competitor A launched X")
    assert "Competitor A launched X" in out
    assert "Evaluate claim:" in out


def test_git_context():
    ctx = get_git_context()
    assert "git_branch" in ctx
    assert "git_commit" in ctx
    assert len(ctx["git_branch"]) > 0
    assert len(ctx["git_commit"]) > 0


def test_verification_accuracy_evaluator_match():
    inp = {"statement": "Test claim"}
    out = {"predicted_status": "verified"}
    expected = {"status": "verified"}

    evals = verification_accuracy_evaluator(input=inp, output=out, expected_output=expected)
    assert len(evals) >= 1
    acc_eval = next(e for e in evals if e.name == "accuracy")
    assert acc_eval.value == 1.0


def test_verification_accuracy_evaluator_mismatch():
    inp = {"statement": "Test claim"}
    out = {"predicted_status": "insufficient"}
    expected = {"status": "verified"}

    evals = verification_accuracy_evaluator(input=inp, output=out, expected_output=expected)
    acc_eval = next(e for e in evals if e.name == "accuracy")
    assert acc_eval.value == 0.0
