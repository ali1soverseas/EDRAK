"""Structure checks and schedule repair for an action plan. Deterministic."""

from __future__ import annotations

from typing import Optional

from edrak.action_plan.contracts import ActionPlan
from edrak.metrics.registry import get_registry


def _actions(plan: ActionPlan):
    return [a for w in plan.workstreams for a in w.actions]


def find_cycle(plan: ActionPlan) -> Optional[list[str]]:
    deps = {a.id: [d for d in a.depends_on if d] for a in _actions(plan)}
    state: dict[str, int] = {}
    path: list[str] = []

    def visit(n: str) -> Optional[list[str]]:
        state[n] = 1
        path.append(n)
        for d in deps.get(n, []):
            if d not in deps:
                continue
            if state.get(d) == 1:
                return path[path.index(d):] + [d]
            if state.get(d) is None and (c := visit(d)):
                return c
        state[n] = 2
        path.pop()
        return None

    for n in deps:
        if state.get(n) is None and (c := visit(n)):
            return c
    return None


def schedule(plan: ActionPlan) -> ActionPlan:
    """Move each action to start after the actions it depends on, then set the total length. Assumes no cycle."""
    by_id = {a.id: a for a in _actions(plan)}
    done: dict[str, int] = {}

    def end(a_id: str) -> int:
        if a_id in done:
            return done[a_id]
        a = by_id[a_id]
        start = max([a.start_week] + [end(d) + 1 for d in a.depends_on if d in by_id])
        a.start_week = start
        done[a_id] = start + a.duration_weeks - 1
        return done[a_id]

    for a_id in by_id:
        end(a_id)
    plan.total_weeks = max(done.values(), default=0)
    return plan


def check(plan: ActionPlan) -> tuple[list[str], list[str]]:
    """(errors, warnings). Errors block saving and approval; warnings are shown to the person."""
    errors: list[str] = []
    warnings: list[str] = []
    ids = [a.id for a in _actions(plan)]
    if len(ids) != len(set(ids)):
        errors.append("Two actions share the same id.")
    known = set(ids)
    for a in _actions(plan):
        for d in a.depends_on:
            if d not in known:
                errors.append(f"Action '{a.title}' depends on an unknown action ({d}).")
            if d == a.id:
                errors.append(f"Action '{a.title}' depends on itself.")
    cycle = find_cycle(plan)
    if cycle:
        errors.append("Actions depend on each other in a loop: " + " → ".join(cycle))
    if not _actions(plan):
        errors.append("The plan has no actions.")
    for r in plan.risks:
        if not (r.mitigation.strip() or r.mitigation_action_ids):
            errors.append(f"Risk '{r.text[:60]}' has no mitigation.")
        for a_id in r.mitigation_action_ids:
            if a_id not in known:
                errors.append(f"Risk '{r.text[:60]}' points to an unknown action ({a_id}).")
    reg = get_registry()
    for k in plan.kpis:
        if not k.new_measure and not (k.metric_id and reg.get(k.metric_id)):
            errors.append(f"KPI '{k.name}' must link to a known metric or be marked as a new measure.")
        if not k.target.strip():
            warnings.append(f"KPI '{k.name}' has no target yet.")
        if not k.new_measure and not k.baseline:
            warnings.append(f"KPI '{k.name}' has no stored baseline; mark it as a new measure or run more research.")
    end = plan.total_weeks or max((a.start_week + a.duration_weeks - 1 for a in _actions(plan)), default=0)
    for m in plan.milestones:
        if end and m.week > end + 4:
            warnings.append(f"Milestone '{m.title}' is well after the last action ends.")
        if not m.go_criteria.strip():
            warnings.append(f"Milestone '{m.title}' has no go / no-go criteria.")
    if not plan.kpis:
        warnings.append("The plan has no KPIs.")
    if not plan.risks:
        warnings.append("The plan has no risk register.")
    return errors, warnings
