"""Minimal JEVStation client for JEV semantic evaluations.

JEVStation (https://jevstation.com/docs) exposes TypeSafe's JEV System One
model over ``POST /api/v1/systemone``. This module is a thin transport and
parsing layer only:

* builds ``state`` + typed-question payloads (noul / choice / score),
* authenticates with ``JEVSTATION_API_KEY`` (environment variable, never
  hardcoded or committed),
* parses the ``{code, message, data}`` envelope into typed answers,
* maps HTTP failures to typed exceptions, with no automatic retries.

Policy (thresholds, ALLOW/BLOCK/REVIEW decisions) lives in callers such as
the guardrails layer -- never here. Response shapes follow the official
JevStation ``jev-api`` skill doc; undocumented fields are treated as
optional and never invented.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

import httpx

BASE_URL = "https://jevstation.com"
ENDPOINT = "/api/v1/systemone"
DEFAULT_TIMEOUT_S = 15.0
API_KEY_ENV_VAR = "JEVSTATION_API_KEY"

QuestionType = Literal["noul", "choice", "score"]


# ------------------------------------------------------------------ builders


def noul_question(instructions: str | dict | list, criteria: dict | None = None) -> dict[str, Any]:
    """A yes/no question; answer is a probability that the answer is yes."""
    question: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if criteria is not None:
        question["criteria"] = criteria
    return question


def choice_question(
    instructions: str | dict | list,
    criteria: Mapping[str, str | dict | list | None],
) -> dict[str, Any]:
    """Pick-one-of question; answer is the top option plus a distribution."""
    return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}


def score_question(
    instructions: str | dict | list,
    criteria: list[str | dict | list],
) -> dict[str, Any]:
    """Ordered-rubric question; answer is a probability-weighted position."""
    return {"type": "score", "instructions": instructions, "criteria": list(criteria)}


# ------------------------------------------------------------------ answers


@dataclass(frozen=True)
class NoulAnswer:
    type: str = "noul"
    noul: float = 0.0


@dataclass(frozen=True)
class ChoiceAnswer:
    type: str = "choice"
    choice: str = ""
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass(frozen=True)
class ScoreAnswer:
    type: str = "score"
    score: float = 0.0
    legend: dict[str, str] = field(default_factory=dict)
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0


Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer


@dataclass(frozen=True)
class Evaluation:
    """One completed JEVStation evaluation (single round trip)."""

    model: str
    answers: dict[str, Answer]
    input_tokens: int = 0
    output_tokens: int = 0
    credits_charged: int | None = None
    credits_remaining: int | None = None
    latency_ms: int | None = None


# ------------------------------------------------------------------ errors


class JevStationError(Exception):
    """Base error; never carries the API key."""

    def __init__(self, message: str, *, status: int | None = None, code: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class JevStationAuthError(JevStationError):
    """401: missing or invalid API key."""


class JevStationCreditError(JevStationError):
    """402: not enough credits."""


class JevStationRateLimitError(JevStationError):
    """429: rate limited. Honour retry_after (may be None) with backoff."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class JevStationBadRequestError(JevStationError):
    """400/404/413/422: malformed body, unknown question set, state too large."""


class JevStationServerError(JevStationError):
    """502/503/529: evaluation failed (credits refunded) or at capacity."""


class JevStationNetworkError(JevStationError):
    """Timeouts and connection errors."""


class JevStationResponseError(JevStationError):
    """The envelope or an answer did not match the documented schema."""


# ------------------------------------------------------------------ client


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_questions(questions: Mapping[str, Mapping[str, Any]]) -> None:
    if not isinstance(questions, Mapping) or not questions:
        raise ValueError("questions must be a non-empty mapping of id to question.")
    for qid, question in questions.items():
        if not isinstance(question, Mapping):
            raise ValueError(f"question {qid!r} must be a mapping.")
        qtype = question.get("type")
        if qtype not in ("noul", "choice", "score"):
            raise ValueError(f"question {qid!r} has unknown type {qtype!r}.")
        if "instructions" not in question:
            raise ValueError(f"question {qid!r} is missing 'instructions'.")
        criteria = question.get("criteria")
        if qtype == "choice":
            if not isinstance(criteria, Mapping) or len(criteria) < 2:
                raise ValueError(f"choice question {qid!r} needs at least two options in 'criteria'.")
        elif qtype == "score":
            if not isinstance(criteria, (list, tuple)) or len(criteria) < 2:
                raise ValueError(f"score question {qid!r} needs at least two ordered levels in 'criteria'.")


def _parse_answer(qid: str, qtype: str, raw: Any) -> Answer:
    if not isinstance(raw, Mapping):
        raise JevStationResponseError(f"answer {qid!r} is not an object.")
    if raw.get("type") != qtype:
        raise JevStationResponseError(f"answer {qid!r} has type {raw.get('type')!r}, expected {qtype!r}.")
    if qtype == "noul":
        value = raw.get("noul")
        if not _is_number(value) or not 0.0 <= float(value) <= 1.0:
            raise JevStationResponseError(f"answer {qid!r} has invalid 'noul' value {value!r}.")
        return NoulAnswer(noul=float(value))
    if qtype == "choice":
        choice, probs, conf = raw.get("choice"), raw.get("probabilities"), raw.get("confidence")
        if not isinstance(choice, str) or not choice:
            raise JevStationResponseError(f"answer {qid!r} has invalid 'choice' value {choice!r}.")
        if not isinstance(probs, Mapping) or not all(_is_number(v) for v in probs.values()):
            raise JevStationResponseError(f"answer {qid!r} has invalid 'probabilities'.")
        if not _is_number(conf):
            raise JevStationResponseError(f"answer {qid!r} has invalid 'confidence'.")
        return ChoiceAnswer(choice=choice, probabilities=dict(probs), confidence=float(conf))
    score, legend, probs, conf = raw.get("score"), raw.get("legend"), raw.get("probabilities"), raw.get("confidence")
    if not _is_number(score):
        raise JevStationResponseError(f"answer {qid!r} has invalid 'score' value {score!r}.")
    if not isinstance(legend, Mapping) or not isinstance(probs, Mapping):
        raise JevStationResponseError(f"answer {qid!r} has invalid 'legend' or 'probabilities'.")
    if not all(_is_number(v) for v in probs.values()) or not _is_number(conf):
        raise JevStationResponseError(f"answer {qid!r} has invalid 'probabilities' or 'confidence'.")
    return ScoreAnswer(score=float(score), legend=dict(legend), probabilities=dict(probs), confidence=float(conf))


class JevStationClient:
    """Synchronous JEVStation client. Reuse one instance per thread/process."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        key = (api_key if api_key is not None else os.environ.get(API_KEY_ENV_VAR, "")).strip()
        if not key:
            raise JevStationError(
                f"{API_KEY_ENV_VAR} is not set. Export it in the environment; "
                "it is never read from committed files."
            )
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers={"Authorization": f"Bearer {key}"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> JevStationClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- evaluation --------------------------------------------------------

    def evaluate(
        self,
        state: str | dict | list,
        questions: Mapping[str, Mapping[str, Any]],
        *,
        timeout_s: float | None = None,
    ) -> Evaluation:
        """Evaluate every question against the same state in one round trip."""
        if not isinstance(state, (str, dict, list)) or (isinstance(state, str) and not state.strip()):
            raise ValueError("state must be a non-empty string, object, or array.")
        _validate_questions(questions)
        try:
            response = self._http.post(
                ENDPOINT,
                json={"state": state, "questions": {k: dict(v) for k, v in questions.items()}},
                **({"timeout": timeout_s} if timeout_s is not None else {}),
            )
        except httpx.TimeoutException as exc:
            raise JevStationNetworkError(f"JEVStation request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise JevStationNetworkError(f"JEVStation connection failed: {exc}") from exc
        self._raise_for_status(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise JevStationResponseError("JEVStation returned a non-JSON response.") from exc
        return self._parse_body(body, questions)

    # -- free balance check --------------------------------------------------

    def get_credits(self) -> dict[str, Any]:
        """Free key/balance check (GET). Shape beyond the skill doc is
        unverified, so the ``data`` payload is returned as-is."""
        try:
            response = self._http.get(ENDPOINT)
        except httpx.TimeoutException as exc:
            raise JevStationNetworkError(f"JEVStation request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise JevStationNetworkError(f"JEVStation connection failed: {exc}") from exc
        self._raise_for_status(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise JevStationResponseError("JEVStation returned a non-JSON response.") from exc
        if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
            raise JevStationResponseError("JEVStation balance response has no 'data' object.")
        return body["data"]

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status == 200:
            return
        try:
            message = response.json().get("message", response.text)
        except ValueError:
            message = response.text
        message = str(message)[:300]
        if status == 401:
            raise JevStationAuthError(f"unauthorized (401): {message}", status=status)
        if status == 402:
            raise JevStationCreditError(f"not enough credits (402): {message}", status=status)
        if status == 429:
            retry_after: float | None = None
            raw = response.headers.get("retry-after")
            if raw is not None:
                try:
                    retry_after = float(raw)
                except ValueError:
                    retry_after = None
            raise JevStationRateLimitError(
                f"rate limited (429): {message}", status=status, retry_after=retry_after
            )
        if status in (400, 404, 413, 422):
            raise JevStationBadRequestError(f"rejected ({status}): {message}", status=status)
        if status in (502, 503, 529):
            raise JevStationServerError(
                f"evaluation unavailable ({status}); failed runs are refunded: {message}", status=status
            )
        raise JevStationError(f"unexpected status ({status}): {message}", status=status)

    @staticmethod
    def _parse_body(body: Any, questions: Mapping[str, Mapping[str, Any]]) -> Evaluation:
        if not isinstance(body, dict):
            raise JevStationResponseError("JEVStation response is not an object.")
        if body.get("code") != 0:
            raise JevStationResponseError(f"JEVStation error {body.get('code')}: {body.get('message')}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise JevStationResponseError("JEVStation response has no 'data' object.")
        raw_answers = data.get("answers")
        if not isinstance(raw_answers, dict):
            raise JevStationResponseError("JEVStation response has no 'answers' object.")
        answers: dict[str, Answer] = {}
        for qid, question in questions.items():
            if qid not in raw_answers:
                raise JevStationResponseError(f"JEVStation response is missing answer {qid!r}.")
            answers[qid] = _parse_answer(qid, question["type"], raw_answers[qid])
        usage = data.get("usage") or {}
        credits = data.get("credits") or {}
        return Evaluation(
            model=str(data.get("model", "")),
            answers=answers,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            credits_charged=credits.get("charged"),
            credits_remaining=credits.get("remaining"),
            latency_ms=data.get("latency_ms"),
        )
