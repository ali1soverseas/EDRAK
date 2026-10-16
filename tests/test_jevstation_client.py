"""Mocked tests for the JEVStation client. No live calls, no credits spent."""

import json

import httpx
import pytest

from edrak.eval.jevstation import (
    ChoiceAnswer,
    JevStationAuthError,
    JevStationBadRequestError,
    JevStationClient,
    JevStationCreditError,
    JevStationError,
    JevStationNetworkError,
    JevStationRateLimitError,
    JevStationResponseError,
    JevStationServerError,
    NoulAnswer,
    ScoreAnswer,
    choice_question,
    noul_question,
    score_question,
)

ENVELOPE = {
    "code": 0,
    "message": "ok",
    "data": {
        "model": "jev-1.13.0",
        "answers": {
            "is_urgent": {"type": "noul", "noul": 0.99},
            "team": {
                "type": "choice",
                "choice": "billing",
                "probabilities": {"billing": 0.88, "technical": 0.12},
                "confidence": 0.81,
            },
            "severity": {
                "type": "score",
                "score": 1.05,
                "legend": {"0": "Calm", "1": "Frustrated", "2": "Very angry"},
                "probabilities": {"0": 0.0, "1": 0.95, "2": 0.05},
                "confidence": 0.92,
            },
        },
        "usage": {"input_tokens": 300, "output_tokens": 40},
        "credits": {"charged": 1, "remaining": 49},
        "latency_ms": 320,
    },
}


def _client(handler, api_key="test-key"):
    return JevStationClient(api_key=api_key, transport=httpx.MockTransport(handler))


def test_request_shape_and_auth_header():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ENVELOPE)

    with _client(handler) as client:
        result = client.evaluate(
            "Payouts have been failing for 3 days.",
            {
                "is_urgent": noul_question("Does this convey urgency?"),
                "team": choice_question("Which team?", {"billing": "Payments", "technical": "Bugs"}),
                "severity": score_question("How severe?", ["Low", "High", "Critical"]),
            },
        )
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/systemone"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["state"] == "Payouts have been failing for 3 days."
    assert set(seen["body"]["questions"]) == {"is_urgent", "team", "severity"}
    assert seen["body"]["questions"]["team"]["criteria"] == {"billing": "Payments", "technical": "Bugs"}
    assert isinstance(result.answers["is_urgent"], NoulAnswer)
    assert result.answers["is_urgent"].noul == 0.99
    assert isinstance(result.answers["team"], ChoiceAnswer)
    assert result.answers["team"].choice == "billing"
    assert result.answers["team"].probabilities == {"billing": 0.88, "technical": 0.12}
    assert isinstance(result.answers["severity"], ScoreAnswer)
    assert result.answers["severity"].score == 1.05
    assert result.answers["severity"].legend["1"] == "Frustrated"
    assert result.model == "jev-1.13.0"
    assert (result.input_tokens, result.output_tokens) == (300, 40)
    assert (result.credits_charged, result.credits_remaining) == (1, 49)
    assert result.latency_ms == 320


def test_missing_api_key_raises_without_secret(monkeypatch):
    monkeypatch.delenv("JEVSTATION_API_KEY", raising=False)
    with pytest.raises(JevStationError, match="JEVSTATION_API_KEY is not set"):
        JevStationClient()


def test_error_envelope_is_rejected():
    def handler(request):
        return httpx.Response(200, json={"code": -1, "message": "boom"})

    with _client(handler) as client, pytest.raises(JevStationResponseError):
        client.evaluate("x", {"a": noul_question("q?")})


def test_missing_and_mistyped_answers_are_rejected():
    bad = json.loads(json.dumps(ENVELOPE))
    del bad["data"]["answers"]["team"]

    def handler(request):
        return httpx.Response(200, json=bad)

    with _client(handler) as client, pytest.raises(JevStationResponseError, match="missing answer"):
        client.evaluate("x", {
            "is_urgent": noul_question("q?"),
            "team": choice_question("q?", {"a": None, "b": None}),
            "severity": score_question("q?", ["a", "b"]),
        })

    bad2 = json.loads(json.dumps(ENVELOPE))
    bad2["data"]["answers"]["is_urgent"] = {"type": "noul", "noul": "high"}

    def handler2(request):
        return httpx.Response(200, json=bad2)

    with _client(handler2) as client, pytest.raises(JevStationResponseError):
        client.evaluate("x", {"is_urgent": noul_question("q?")})


def test_http_status_mapping():
    cases = [
        (401, JevStationAuthError),
        (402, JevStationCreditError),
        (400, JevStationBadRequestError),
        (404, JevStationBadRequestError),
        (413, JevStationBadRequestError),
        (502, JevStationServerError),
        (503, JevStationServerError),
        (529, JevStationServerError),
    ]
    for status, exc in cases:
        def handler(request, _status=status):
            return httpx.Response(_status, json={"code": -1, "message": "err"})

        with _client(handler) as client, pytest.raises(exc):
            client.evaluate("x", {"a": noul_question("q?")})


def test_rate_limit_carries_retry_after():
    def handler(request):
        return httpx.Response(429, headers={"retry-after": "2"}, json={"code": -1, "message": "slow"})

    with _client(handler) as client, pytest.raises(JevStationRateLimitError) as info:
        client.evaluate("x", {"a": noul_question("q?")})
    assert info.value.retry_after == 2.0


def test_timeout_and_connection_errors():
    def slow(request):
        raise httpx.ConnectTimeout("timed out")

    def down(request):
        raise httpx.ConnectError("refused")

    with _client(slow) as client, pytest.raises(JevStationNetworkError, match="timed out"):
        client.evaluate("x", {"a": noul_question("q?")})
    with _client(down) as client, pytest.raises(JevStationNetworkError, match="failed"):
        client.evaluate("x", {"a": noul_question("q?")})


def test_input_validation():
    with _client(lambda request: httpx.Response(200, json=ENVELOPE)) as client:
        with pytest.raises(ValueError):
            client.evaluate("x", {})
        with pytest.raises(ValueError):
            client.evaluate("x", {"a": {"type": "mystery", "instructions": "q"}})
        with pytest.raises(ValueError):
            client.evaluate("x", {"a": choice_question("q?", {"only": "one"})})
        with pytest.raises(ValueError):
            client.evaluate("x", {"a": score_question("q?", ["only"])})
        with pytest.raises(ValueError):
            client.evaluate("   ", {"a": noul_question("q?")})


def test_get_credits_returns_data_object():
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"code": 0, "message": "ok",
                                         "data": {"remaining": 49, "plan": "free"}})

    with _client(handler) as client:
        assert client.get_credits() == {"remaining": 49, "plan": "free"}
