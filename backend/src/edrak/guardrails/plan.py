"""Guardrails for action plans: what a person types into a plan is checked like any other input."""

from __future__ import annotations

from typing import Any, Optional

from edrak.guardrails import patterns


def check_free_text(text: str) -> Optional[str]:
    """A reason the text cannot be used, or None."""
    if patterns.injection_hits(text) or patterns.hidden_char_count(text) > 3:
        return "That text looks like an instruction aimed at the AI, so it was not accepted. Describe the change you want in plain words."
    return None


def _strings(plan: Any) -> list[str]:
    out: list[str] = [plan.objective, plan.chosen_option.why, *plan.resources, *plan.notes]
    out += [n.why_not for n in plan.alternatives_not_chosen]
    out += [a.text for a in plan.assumptions] + [q.text for q in plan.open_questions]
    for w in plan.workstreams:
        out += [w.name, w.objective]
        for a in w.actions:
            out += [a.title, a.description, a.deliverable, a.success_measure, a.owner_role]
    out += [m.title for m in plan.milestones] + [m.go_criteria for m in plan.milestones] + [m.kill_criteria for m in plan.milestones]
    out += [k.name + " " + k.target for k in plan.kpis]
    out += [r.text + " " + r.mitigation + " " + r.early_warning for r in plan.risks]
    out += [o.text + " " + o.how_to_capture for o in plan.opportunities]
    return out


def check_plan(plan: Any) -> list[str]:
    """Problems that stop a plan from being saved: injection text and personal data in anything a person edited."""
    problems: list[str] = []
    for text in _strings(plan):
        if not text:
            continue
        if check_free_text(text):
            problems.append(f"'{text[:50]}…' looks like an instruction to the AI.")
        if patterns.find_pii(text):
            problems.append(f"'{text[:50]}…' contains personal data (email, phone or ID); remove it before saving.")
    return list(dict.fromkeys(problems))[:8]
