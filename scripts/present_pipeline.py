"""Audience view of a pipeline run.

Captures every line the agents print (and the verification log the
orchestrator redirects off stdout), then restates it for a technical
reader who does not know EDRAK internals.

This file does not research. It only watches and explains.

The formatted terminal output is also written to
``artifacts/runs/<request_id>.briefing.txt``.

    python scripts/present_pipeline.py --query "Should GitLab enter automotive compliance?" --target "Azure DevOps"

Replay a saved transcript without running the agents::

    python scripts/present_pipeline.py --from-transcript artifacts/runs/<id>.transcript.txt
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib.util
import json
import re
import sys
import textwrap
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TextIO

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
SCRIPTS_DIR = Path(__file__).resolve().parent
LOGS_DIR = REPO_ROOT / "artifacts" / "logs"
TRANSCRIPTS_DIR = REPO_ROOT / "artifacts" / "runs"

if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

_WIDTH = 72

_KNOWN_AGENTS = (
    "competitor_intelligence",
    "internal_intelligence",
    "market_intelligence",
    "customer_trends",
    "cross_signal",
    "verification",
    "orchestrator",
)

_AGENT_GUIDE = {
    "orchestrator": (
        "Orchestrator",
        "Control plane. Splits the question, dispatches specialists, "
        "and finishes the run. It does not research.",
    ),
    "competitor_intelligence": (
        "Competitor intelligence",
        "Rivals' products, pricing, positioning, and launches.",
    ),
    "market_intelligence": (
        "Market intelligence",
        "Market size, demand, regulation, and economic signals. "
        "Not a competitor monitor.",
    ),
    "internal_intelligence": (
        "Internal intelligence",
        "The company's own products, capabilities, and constraints.",
    ),
    "customer_trends": (
        "Customer and trends intelligence",
        "Buyer needs, sentiment, and adoption.",
    ),
    "verification": (
        "Verification",
        "Independent source-check. Keeps a claim only if the saved "
        "evidence actually supports it.",
    ),
    "cross_signal": (
        "Cross-signal",
        "Finds relationships among claims that already passed verification. "
        "Observations, not recommendations.",
    ),
}

_NODE_PLAIN = {
    "plan": "Turn the question into one bounded task per specialist.",
    "dispatch": "Hand this specialist its task and start research.",
    "verify": "Check each claim against the source text that was saved.",
    "assess_findings": "Grade every claim: supported, or not enough evidence.",
    "decide": "Choose the run-level verification status from those grades.",
    "cross_signal": "Link verified claims that point at the same underlying fact.",
    "finalize": "Package plan, findings, and signals into one result object.",
    "exhausted": "Retry/replan budget used up; finishing with what we have.",
    "replan": "Ask the planner for a different split of the work.",
    "identify_requirements": "Decide which competitor questions to answer.",
    "generate_queries": "Write web-search queries for this stage.",
    "search": "Search public sources.",
    "synthesize": "Turn search hits into claims with quotes.",
    "task planner": "Break the market question into look-ups.",
    "task router": "Choose the next look-up, or move on to packaging.",
    "gap fill": "Retry look-ups that did not produce usable evidence.",
    "output": "Package this specialist's findings for the next stage.",
    "output state": "Full verification record (status, per-claim grades).",
    "worker result": "This specialist's finished contract (findings + evidence).",
    "running cross signal agent": "Start linking verified claims across domains.",
    "prepare_next_stage": "Move to the next research stage.",
    "check_requirements": "See which assigned questions still lack evidence.",
    "final_report": "Compile this specialist's findings into a result.",
}

_TAG_RE = re.compile(
    r"^\s*\[(" + "|".join(_KNOWN_AGENTS) + r")\]\s*(.*)$",
    re.IGNORECASE,
)
_RULER_RE = re.compile(r"^[\s=\-─—]*$")
_LOG_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}  ")
_NOISE_EXACT = {
    "dynamic research requirements:",
    "generated queries:",
}


@dataclass
class Event:
    agent: str
    kind: str  # node | text | json | note | skip
    text: str
    annotation: str = ""
    payload: Any = None


@dataclass
class TranscriptParser:
    """Turn a raw print stream into tagged events."""

    default_agent: str = "orchestrator"
    on_event: Callable[[Event], None] | None = None
    events: list[Event] = field(default_factory=list)
    agent: str = "orchestrator"
    _json_lines: list[str] = field(default_factory=list)
    _json_depth: int = 0
    _in_json: bool = False

    def feed_text(self, text: str) -> None:
        for line in text.splitlines():
            self.feed_line(line)
        self.flush()

    def feed_line(self, line: str) -> None:
        if self._in_json:
            self._json_lines.append(line)
            self._json_depth += _brace_delta(line)
            if self._json_depth <= 0:
                self._emit_json()
            return

        stripped = line.strip()
        if not stripped or _RULER_RE.match(stripped):
            return

        tagged = _TAG_RE.match(stripped)
        if tagged:
            self.agent = tagged.group(1).lower()
            rest = tagged.group(2).strip()
            if not rest:
                return
        else:
            rest = stripped

        if rest[:1] in "{[":
            delta = _brace_delta(rest)
            if delta <= 0:
                compact = _compact_struct(rest)
                if compact:
                    self._emit(
                        Event(
                            agent=self.agent or self.default_agent,
                            kind="text",
                            text=compact,
                        )
                    )
                return
            self._in_json = True
            self._json_lines = [rest]
            self._json_depth = delta
            return

        event = _event_from_text(self.agent or self.default_agent, rest)
        self._emit(event)

    def flush(self) -> None:
        if self._in_json:
            self._emit_json()

    def _emit_json(self) -> None:
        raw = "\n".join(self._json_lines)
        self._in_json = False
        self._json_lines = []
        self._json_depth = 0
        payload: Any = None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        text = _summarize_payload(payload) if payload is not None else (
            f"Structured dump ({len(raw.splitlines())} lines; not valid JSON, "
            "so the recap shows a short excerpt)."
        )
        excerpt = raw if payload is None else ""
        event = Event(
            agent=self.agent or self.default_agent,
            kind="json",
            text=text,
            annotation=excerpt[:1200],
            payload=payload,
        )
        self._emit(event)

    def _emit(self, event: Event) -> None:
        self.events.append(event)
        if self.on_event is not None:
            self.on_event(event)


def _brace_delta(line: str) -> int:
    delta = 0
    in_str = False
    escape = False
    quote = ""
    for char in line:
        if in_str:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                in_str = False
            continue
        if char in "\"'":
            in_str = True
            quote = char
            continue
        if char in "{[":
            delta += 1
        elif char in "}]":
            delta -= 1
    return delta


_NOISY_STRUCT_KEYS = {
    "confidence",
    "evidence_quality",
    "url",
    "source",
    "status",
    "quote",
    "snippet",
}


def _compact_struct(rest: str) -> str:
    """Turn a one-line pprint/JSON object into a short detail, or '' to skip."""
    obj: Any
    try:
        obj = json.loads(rest)
    except json.JSONDecodeError:
        try:
            obj = ast.literal_eval(rest)
        except (ValueError, SyntaxError):
            return rest[:240]
    if isinstance(obj, dict):
        keys = {str(key).lower() for key in obj}
        if keys and keys <= _NOISY_STRUCT_KEYS:
            return ""
        parts = []
        for key, value in list(obj.items())[:5]:
            shown = " ".join(str(value).split())
            if len(shown) > 80:
                shown = shown[:77] + "..."
            parts.append(f"{key}: {shown}")
        extra = " ..." if len(obj) > 5 else ""
        return "; ".join(parts) + extra
    if isinstance(obj, list):
        return f"{len(obj)} item(s)"
    return rest[:240]


def _event_from_text(agent: str, rest: str) -> Event:
    lowered = rest.lower()
    annotation = _annotate(rest)
    kind = "text"
    if lowered.startswith("saved node output"):
        kind = "skip"
    elif lowered.startswith("node:") or lowered.startswith("node ") or _is_step(lowered):
        kind = "node"
    elif any(
        token in lowered for token in ("error", "failed", "warning", "no worker registered")
    ):
        kind = "note"
        if not annotation:
            annotation = _plain_error(rest)
    return Event(agent=agent, kind=kind, text=rest, annotation=annotation)


def _is_step(lowered: str) -> bool:
    if lowered.startswith("node:") or lowered.startswith("node "):
        return True
    for key in sorted(_NODE_PLAIN, key=len, reverse=True):
        if lowered == key or lowered.startswith(key + " ") or lowered.startswith(key + ":"):
            return True
    return False


def _annotate(rest: str) -> str:
    body = rest.strip()
    if body.lower().startswith("node:"):
        body = body[5:].strip()
    lowered = body.lower()
    for key, explanation in sorted(
        _NODE_PLAIN.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if lowered == key or lowered.startswith(key + " ") or lowered.startswith(key + ":"):
            return explanation
        if f"node: {key}" in lowered or f"node {key}" in lowered:
            return explanation
    if lowered.startswith("claim"):
        return "A statement this specialist made from evidence it saved."
    if lowered.startswith("quote"):
        return "Excerpt copied from a source page to back the claim."
    if "no worker registered" in lowered:
        return "This specialist is not wired into the pipeline yet."
    if lowered.startswith("evaluated: useful"):
        return "The page looked relevant enough to keep."
    if lowered.startswith("not useful"):
        return "The page was discarded; the specialist will try another query or tool."
    if lowered.startswith("scraping:"):
        return "Fetching the page text so a claim can be quoted from it."
    if "run artifact saved" in lowered:
        return "Complete machine-readable record of this run."
    return ""


def _plain_error(text: str) -> str:
    lowered = text.lower()
    if "no worker registered" in lowered:
        return "This specialist is not wired into the pipeline yet."
    if "conn error" in lowered or "connection error" in lowered:
        return "A web tool was unreachable; the specialist retries or falls back."
    if "llm error" in lowered:
        return "The language-model call failed; the specialist retries."
    if "tool error" in lowered:
        return "A research tool returned an error; the specialist tries a fallback."
    return ""


def _summarize_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        if "decision" in payload and "findings" in payload:
            return _summarize_verification(payload)
        if "signals" in payload:
            return _summarize_cross_signal(payload)
        if "findings" in payload and "worker" in payload:
            worker = payload.get("worker") or "specialist"
            status = payload.get("status") or "unknown"
            n_findings = len(payload.get("findings") or [])
            n_evidence = len(payload.get("evidence") or [])
            return (
                f"Specialist contract for {worker}: {status}, "
                f"{n_findings} claim(s), {n_evidence} source(s)."
            )
        keys = ", ".join(str(key) for key in list(payload.keys())[:12])
        extra = " ..." if len(payload) > 12 else ""
        return f"Structured object with keys: {keys}{extra}."
    if isinstance(payload, list):
        return f"Structured list of {len(payload)} item(s)."
    return "Structured payload."


def _summarize_verification(payload: dict[str, Any]) -> str:
    decision = payload.get("decision") or {}
    status = decision.get("status") or "unknown"
    summary = (decision.get("summary") or "").strip()
    findings = payload.get("findings") or []
    verified = sum(
        1
        for item in findings
        if str(item.get("verification_status", "")).lower() == "verified"
    )
    insufficient = len(findings) - verified
    parts = [
        f"Verification finished as {status}: {verified} claim(s) matched "
        f"their sources, {insufficient} did not."
    ]
    if summary:
        parts.append(summary)
    return " ".join(parts)


def _summarize_cross_signal(payload: dict[str, Any]) -> str:
    signals = payload.get("signals") or []
    status = payload.get("status") or "unknown"
    return (
        f"Cross-signal finished as {status} with {len(signals)} "
        f"relationship(s) among verified claims."
    )


def _rule(title: str, out: TextIO) -> None:
    out.write("\n" + "=" * _WIDTH + "\n")
    out.write(title + "\n")
    out.write("=" * _WIDTH + "\n")


def _wrap(text: str, indent: str = "    ") -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return ""
    return textwrap.fill(
        cleaned,
        width=_WIDTH,
        initial_indent=indent,
        subsequent_indent=indent,
    )


def _agent_heading(agent: str) -> tuple[str, str]:
    name, role = _AGENT_GUIDE.get(agent, (agent, "Pipeline component."))
    return name, role


_STAGE_RE = re.compile(
    r"\s+STAGE\s+(\d+)\s*:?\s*(.*)$",
    re.IGNORECASE,
)


def _step_display(text: str) -> tuple[str, str]:
    """Split a NODE line into a short title and an optional stage note."""
    body = text.strip()
    if body.lower().startswith("node:"):
        body = body[5:].strip()
    stage = ""
    match = _STAGE_RE.search(body)
    if match:
        stage = f"Stage {match.group(1)}"
        extra = match.group(2).strip(" :")
        body = body[: match.start()].strip()
        if extra:
            stage += "  /  " + extra.replace("_", " ").lower()
    else:
        parts = body.split()
        if len(parts) > 1 and parts[-1].isupper() and "_" in parts[-1]:
            stage = parts[-1].replace("_", " ").lower()
            body = " ".join(parts[:-1])
    title = body.replace("_", " ").strip()
    if title:
        title = title[0].upper() + title[1:]
    else:
        title = "Step"
    return title, stage


def _write_agent_banner(agent: str, out: TextIO, *, recap: bool = False) -> None:
    name, role = _agent_heading(agent)
    label = "AGENT (recap)" if recap else "AGENT"
    out.write("\n\n")
    out.write("=" * _WIDTH + "\n")
    out.write(f"  {label}:  {name}\n")
    out.write(f"  id:     [{agent}]\n")
    out.write(_wrap(role, "  ") + "\n")
    out.write("=" * _WIDTH + "\n")


def _write_step(number: int, event: Event, out: TextIO) -> None:
    title, stage = _step_display(event.text)
    out.write("\n")
    out.write("  " + "-" * (_WIDTH - 2) + "\n")
    out.write(f"  Step {number}   {title}\n")
    if stage:
        out.write(_wrap(stage, "           ") + "\n")
    if event.annotation:
        out.write(_wrap(event.annotation, "           ") + "\n")
    out.write("  " + "-" * (_WIDTH - 2) + "\n")


def _write_detail(event: Event, out: TextIO) -> None:
    indent = "      "
    if event.kind == "json":
        out.write(_wrap(event.text, indent) + "\n")
        if isinstance(event.payload, dict) and event.payload.get("signals"):
            for index, signal in enumerate(event.payload.get("signals") or [], start=1):
                title = signal.get("title") or f"Signal {index}"
                kind = signal.get("signal_type") or ""
                suffix = f"  [{kind}]" if kind else ""
                out.write(f"\n      {index}. {title}{suffix}\n")
                claim = signal.get("signal") or ""
                interpretation = signal.get("interpretation") or ""
                if claim:
                    out.write(_wrap("Claim: " + claim, "         ") + "\n")
                if interpretation:
                    out.write(_wrap("Why it matters: " + interpretation, "         ") + "\n")
        elif (
            isinstance(event.payload, dict)
            and event.payload.get("findings")
            and "decision" in event.payload
        ):
            for item in (event.payload.get("findings") or [])[:8]:
                status = item.get("verification_status") or ""
                statement = item.get("statement") or ""
                if statement:
                    out.write(_wrap(f"[{status}] {statement}", "         ") + "\n")
            extra = len(event.payload.get("findings") or []) - 8
            if extra > 0:
                out.write(f"         ... {extra} more graded claim(s) in the artifact\n")
        elif event.annotation:
            for line in event.annotation.splitlines()[:6]:
                out.write(_wrap(line, "         ") + "\n")
        return
    if event.kind == "note":
        out.write(_wrap("Note: " + event.text, indent) + "\n")
        if event.annotation:
            out.write(_wrap(event.annotation, "           ") + "\n")
        return
    if event.text.lower() in _NOISE_EXACT:
        out.write(f"{indent}{event.text}\n")
        return
    out.write(_wrap(event.text, indent) + "\n")


class LiveView:
    """Numbered steps, boxed agent switches, details nested under the current step."""

    def __init__(self, out: TextIO) -> None:
        self.out = out
        self.last_agent = ""
        self.steps: dict[str, int] = {}

    def __call__(self, event: Event) -> None:
        if event.kind == "skip":
            return
        if event.agent != self.last_agent:
            self.last_agent = event.agent
            _write_agent_banner(event.agent, self.out)
        if event.kind == "node":
            self.steps[event.agent] = self.steps.get(event.agent, 0) + 1
            _write_step(self.steps[event.agent], event, self.out)
        else:
            _write_detail(event, self.out)
        self.out.flush()


def print_intro(out: TextIO) -> None:
    _rule("EDRAK  |  Live research, explained", out)
    out.write("\n")
    out.write(
        "  A business question is split across specialist agents.\n"
        "  Each agent prints as it works. This presenter captures those\n"
        "  prints, hides decorative banners and raw JSON dumps, and restates\n"
        "  what happened. The system supports a human decision; it does not\n"
        "  make the decision.\n"
        "\n"
        "  Stages you will see:\n"
        "    1. Orchestrator assigns bounded tasks\n"
        "    2. Specialists research in parallel (competitor / market /\n"
        "       internal / customer)\n"
        "    3. Verification checks each claim against saved source text\n"
        "    4. Cross-signal links claims that survived that check\n"
        "\n"
    )
    out.flush()


def _group_by_agent(events: list[Event]) -> list[tuple[str, list[Event]]]:
    order: list[str] = []
    buckets: dict[str, list[Event]] = {}
    for event in events:
        if event.kind == "skip":
            continue
        if event.agent not in buckets:
            order.append(event.agent)
            buckets[event.agent] = []
        buckets[event.agent].append(event)
    return [(agent, buckets[agent]) for agent in order]


def render_recap(
    events: list[Event],
    *,
    artifact_path: Path | None,
    transcript_path: Path | None,
    briefing_path: Path | None = None,
    out: TextIO,
) -> None:
    _rule("Run complete  |  Each agent's work, in order", out)
    out.write("\n")
    out.write(
        "  Below, every agent's prints are gathered into one section even if\n"
        "  they were interleaved live. Each Step is a workflow node; the\n"
        "  indented lines under it are what that node printed.\n"
    )

    grouped = _group_by_agent(events)
    if not grouped:
        out.write("\n  No agent output was captured.\n")
        _print_paths(
            artifact_path,
            transcript_path,
            briefing_path,
            out,
        )
        return

    for agent, group in grouped:
        _write_agent_banner(agent, out, recap=True)
        step_n = 0
        for event in group:
            if event.kind == "node":
                step_n += 1
                _write_step(step_n, event, out)
            else:
                _write_detail(event, out)

    _print_paths(artifact_path, transcript_path, briefing_path, out)
    out.flush()


def _print_paths(
    artifact_path: Path | None,
    transcript_path: Path | None,
    briefing_path: Path | None,
    out: TextIO,
) -> None:
    _rule("Where to look next", out)
    out.write("\n")
    out.write(
        "  The JSON artifact is the complete machine record (plan, claims,\n"
        "  source URLs, verification grades, cross-signals). The transcript\n"
        "  is every captured print. The briefing is this formatted view.\n\n"
    )
    if artifact_path:
        out.write(f"  artifact   : {artifact_path}\n")
    else:
        out.write("  artifact   : not written (--no-out)\n")
    if transcript_path:
        out.write(f"  transcript : {transcript_path}\n")
    else:
        out.write("  transcript : not written\n")
    if briefing_path:
        out.write(f"  briefing   : {briefing_path}\n")
    else:
        out.write("  briefing   : not written (--no-out)\n")


class TeeWriter:
    """Write the same formatted text to the terminal and a briefing file."""

    def __init__(self, *targets: TextIO | None) -> None:
        self.targets = tuple(target for target in targets if target is not None)
        self.encoding = "utf-8"
        self.errors = "replace"

    def write(self, data: str) -> int:
        text = data if isinstance(data, str) else str(data)
        for target in self.targets:
            target.write(text)
        return len(text)

    def flush(self) -> None:
        for target in self.targets:
            try:
                target.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        return False


class CaptureStream:
    """Thread-safe stdout stand-in that records lines and optionally explains them live."""

    def __init__(
        self,
        real: TextIO,
        parser: TranscriptParser,
        *,
        live: bool,
    ) -> None:
        self.real = real
        self.parser = parser
        self.live = live
        self.encoding = getattr(real, "encoding", "utf-8") or "utf-8"
        self.errors = "replace"
        self._lock = threading.Lock()
        self._buf = ""
        self.raw_chunks: list[str] = []

        if live:
            parser.on_event = LiveView(self.real)

    def write(self, data: str) -> int:
        if not data:
            return 0
        text = data if isinstance(data, str) else str(data)
        with self._lock:
            self.raw_chunks.append(text)
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self.parser.feed_line(line)
        return len(text)

    def flush(self) -> None:
        with self._lock:
            leftover = self._buf
            self._buf = ""
        if leftover:
            self.parser.feed_line(leftover)
        try:
            self.real.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return False

    def reconfigure(self, **kwargs: Any) -> None:
        return None


def _snapshot_logs() -> dict[Path, int]:
    if not LOGS_DIR.exists():
        return {}
    return {path: path.stat().st_size for path in LOGS_DIR.glob("*.log")}


def _read_log_deltas(snapshot: dict[Path, int]) -> dict[str, str]:
    if not LOGS_DIR.exists():
        return {}
    deltas: dict[str, str] = {}
    for path in LOGS_DIR.glob("*.log"):
        start = snapshot.get(path, 0)
        try:
            data = path.read_bytes()[start:].decode("utf-8", errors="replace")
        except OSError:
            continue
        if data.strip():
            deltas[path.stem] = data
    return deltas


def _strip_log_stamps(text: str) -> str:
    lines = []
    for line in text.splitlines():
        lines.append(_LOG_STAMP_RE.sub("", line))
    return "\n".join(lines)


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location(
        "run_pipeline",
        SCRIPTS_DIR / "run_pipeline.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load scripts/run_pipeline.py")
    module = importlib.util.module_from_spec(spec)
    # Pydantic resolves ParsedIntent.NonBlankStr from this module's namespace.
    # Without the sys.modules entry, that lookup fails and validate_json raises
    # "ParsedIntent is not fully defined".
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.ParsedIntent.model_rebuild()
    return module


def _write_transcript(request_id: str, text: str) -> Path | None:
    path = TRANSCRIPTS_DIR / f"{request_id}.transcript.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"  warning: could not write {path}: {exc}", file=sys.stderr)
        return None
    return path


def _briefing_path_for(request_id: str) -> Path:
    return TRANSCRIPTS_DIR / f"{request_id}.briefing.txt"


def _open_briefing(path: Path) -> TextIO | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("w", encoding="utf-8")
    except OSError as exc:
        print(f"  warning: could not write {path}: {exc}", file=sys.stderr)
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="present_pipeline.py",
        description=(
            "Run the EDRAK pipeline (or replay a transcript) and present "
            "every agent print for a technical reader who does not know "
            "the project."
        ),
    )
    parser.add_argument("--query", help="What you want to find out.")
    parser.add_argument("--company", help="Override the inferred company.")
    parser.add_argument(
        "--target",
        action="append",
        help="Competitor or product to compare against. Repeatable.",
    )
    parser.add_argument(
        "--focus", action="append", help="Comparison dimension. Repeatable."
    )
    parser.add_argument(
        "--constraint", action="append", help="Explicit limit. Repeatable."
    )
    parser.add_argument(
        "--time-window", type=int, help="Research recency window in days."
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Stop after planning. Needs no workers.",
    )
    parser.add_argument(
        "--no-out", action="store_true", help="Do not write artifacts/runs output."
    )
    parser.add_argument(
        "--from-transcript",
        metavar="PATH",
        help="Replay a saved transcript instead of running the pipeline.",
    )
    parser.add_argument(
        "--no-live",
        action="store_true",
        help="Capture silently and print only the end-of-run recap.",
    )
    return parser


def present_transcript(
    text: str,
    *,
    extra_logs: dict[str, str] | None = None,
    artifact_path: Path | None = None,
    transcript_path: Path | None = None,
    briefing_path: Path | None = None,
    live: bool = False,
    out: TextIO | None = None,
) -> list[Event]:
    stream = out or sys.stdout
    parser = TranscriptParser()
    if live:
        parser.on_event = LiveView(stream)
    parser.feed_text(text)
    for agent, blob in (extra_logs or {}).items():
        extra = TranscriptParser(default_agent=agent)
        extra.agent = agent
        extra.feed_text(_strip_log_stamps(blob))
        parser.events.extend(extra.events)
    render_recap(
        parser.events,
        artifact_path=artifact_path,
        transcript_path=transcript_path,
        briefing_path=briefing_path,
        out=stream,
    )
    return parser.events


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.from_transcript:
        _force_utf8()
        path = Path(args.from_transcript)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"could not read transcript: {exc}", file=sys.stderr)
            return 2
        briefing_path = None
        briefing_file = None
        if not args.no_out:
            if path.name.endswith(".transcript.txt"):
                briefing_path = path.with_name(
                    path.name[: -len(".transcript.txt")] + ".briefing.txt"
                )
            else:
                briefing_path = path.with_name(path.stem + ".briefing.txt")
            briefing_file = _open_briefing(briefing_path)
            if briefing_file is None:
                briefing_path = None
        audience = TeeWriter(sys.stdout, briefing_file) if briefing_file else sys.stdout
        try:
            print_intro(audience)
            present_transcript(
                text,
                transcript_path=path,
                briefing_path=briefing_path if briefing_file else None,
                live=not args.no_live,
                out=audience,
            )
        finally:
            if briefing_file is not None:
                briefing_file.close()
        return 0

    runner = _load_runner()
    runner.force_utf8_output()

    if not args.query or not args.query.strip():
        print("provide --query, or --from-transcript PATH", file=sys.stderr)
        return runner.EXIT_FAILED

    if not runner.resolve_api_key():
        print(
            "LLM_API_KEY is not set. Copy .env.example to .env and fill it in.\n"
            "Create a key at https://ollama.com/settings/keys",
            file=sys.stderr,
        )
        return runner.EXIT_FAILED

    from edrak.orchestration.planner import PlanningError

    try:
        request = runner.build_request(args.query, args)
    except runner.RequestBuildError as exc:
        print(f"request build failed: {exc}", file=sys.stderr)
        return runner.EXIT_FAILED
    except PlanningError as exc:
        print(f"request build failed: {exc.reason}", file=sys.stderr)
        if exc.raw_output:
            print(f"raw output: {exc.raw_output[:400]}", file=sys.stderr)
        return runner.EXIT_FAILED

    briefing_path = None if args.no_out else _briefing_path_for(request.request_id)
    briefing_file = None if briefing_path is None else _open_briefing(briefing_path)
    if briefing_file is None:
        briefing_path = None
    audience = TeeWriter(sys.stdout, briefing_file) if briefing_file else sys.stdout

    try:
        print_intro(audience)
        audience.write(f"\n  Company  : {request.company_profile.name}\n")
        audience.write(f"  Question : {request.goal}\n")
        audience.write(f"  Run id   : {request.request_id}\n\n")
        audience.flush()

        snapshot = _snapshot_logs()
        parser = TranscriptParser()
        real_out = sys.stdout
        real_err = sys.stderr
        cap = CaptureStream(audience, parser, live=not args.no_live)
        sys.stdout = cap
        sys.stderr = cap
        out = None
        exit_code = runner.EXIT_FAILED
        fail_message = ""
        try:
            if args.plan_only:
                from edrak.orchestration.planner import LlmPlanner

                print("[orchestrator] NODE: plan")
                try:
                    plan = LlmPlanner().plan(request)
                except PlanningError as exc:
                    fail_message = f"planning failed: {exc.reason}"
                else:
                    print(f"[orchestrator] plan_id={plan.plan_id} tasks={len(plan.tasks)}")
                    for task in plan.tasks:
                        print(f"[{task.worker.value}] goal: {task.goal}")
                        print(f"[{task.worker.value}] focus: {task.focus}")
                    out = (
                        None
                        if args.no_out
                        else runner.write_artifact_plan(plan, request.request_id)
                    )
                    exit_code = runner.EXIT_COMPLETED
            else:
                from edrak.orchestration.graph import build_graph

                state = asyncio.run(
                    build_graph(runner.build_default_registry()).ainvoke(
                        {"request": request}
                    )
                )
                result = state["orchestration_result"]
                out = None if args.no_out else runner.write_artifact(result)
                if result.status.value == "completed":
                    exit_code = runner.EXIT_COMPLETED
                elif result.status.value == "partial":
                    exit_code = runner.EXIT_PARTIAL
                else:
                    exit_code = runner.EXIT_FAILED
        finally:
            cap.flush()
            parser.flush()
            sys.stdout = real_out
            sys.stderr = real_err

        if fail_message:
            print(fail_message, file=sys.stderr)
            return runner.EXIT_FAILED

        raw = "".join(cap.raw_chunks)
        transcript_path = None if args.no_out else _write_transcript(request.request_id, raw)
        log_deltas = _read_log_deltas(snapshot)
        extra_logs = {
            agent: blob
            for agent, blob in log_deltas.items()
            if agent == "verification"
        }
        extra_parser = TranscriptParser()
        for agent, blob in extra_logs.items():
            extra = TranscriptParser(default_agent=agent)
            extra.agent = agent
            extra.feed_text(_strip_log_stamps(blob))
            extra_parser.events.extend(extra.events)
        all_events = parser.events + extra_parser.events
        render_recap(
            all_events,
            artifact_path=out,
            transcript_path=transcript_path,
            briefing_path=briefing_path,
            out=audience,
        )
        return exit_code
    finally:
        if briefing_file is not None:
            briefing_file.close()


if __name__ == "__main__":
    raise SystemExit(main())
