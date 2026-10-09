import json

from scripts.pipeline_health_check import EXPECTED_WORKERS, check, report


def _result(worker, *, status="completed", findings=1, evidence=1, dangling=False):
    evidence_id = f"{worker}-evidence"
    finding = {
        "finding_id": f"{worker}-finding",
        "statement": "A claim.",
        "evidence_refs": [{"evidence_id": evidence_id}],
    }
    if dangling:
        finding["evidence_refs"] = [{"evidence_id": "not-a-real-id"}]
    return {
        "worker": worker,
        "status": status,
        "findings": [finding] * findings,
        "evidence": [{"evidence_id": evidence_id}] * evidence,
        "gaps": [],
    }


def _payload(results, *, status="completed", cross_signal=True):
    return {
        "request_id": "health-test",
        "status": status,
        "results": results,
        "cross_signal": {"status": "completed", "signals": [{}] * 3} if cross_signal else None,
    }


def _severities(payload):
    return {label: severity for severity, label, _ in check(payload)}


def test_healthy_run_passes_every_check():
    payload = _payload([_result(w) for w in EXPECTED_WORKERS])

    assert all(s == "PASS" for s in _severities(payload).values())
    assert report(payload) is True


def test_a_worker_that_found_nothing_fails_even_though_the_run_is_completed():
    """The blind spot this checker exists for.

    finalize_node marks the run completed because no worker *failed*, so a worker
    that collected nothing slips past the exit code.
    """
    results = [_result(w) for w in EXPECTED_WORKERS]
    results[-1] = _result(EXPECTED_WORKERS[-1], status="partial", findings=0, evidence=0)

    payload = _payload(results)
    severities = _severities(payload)

    assert severities["run status is completed"] == "PASS"
    assert severities["every worker is present in the results"] == "PASS"
    assert severities["every worker produced a finding"] == "FAIL"
    assert report(payload) is False


def test_partial_status_warns_but_does_not_fail():
    """partial with findings is a worker being honest about gaps, not a fault."""
    results = [_result(w) for w in EXPECTED_WORKERS]
    results[1] = _result(EXPECTED_WORKERS[1], status="partial", findings=26)

    severities = _severities(_payload(results))

    assert severities["every worker produced a finding"] == "PASS"
    assert severities["every worker finished as completed"] == "WARN"


def test_missing_worker_fails():
    payload = _payload([_result(EXPECTED_WORKERS[0])])

    assert _severities(payload)["every worker is present in the results"] == "FAIL"


def test_dangling_evidence_reference_fails():
    results = [_result(w) for w in EXPECTED_WORKERS]
    results[0] = _result(EXPECTED_WORKERS[0], dangling=True)

    assert _severities(_payload(results))["every evidence reference resolves"] == "FAIL"


def test_missing_cross_signal_fails():
    payload = _payload([_result(w) for w in EXPECTED_WORKERS], cross_signal=False)

    assert _severities(payload)["cross-signal completed"] == "FAIL"


def test_report_prints_gaps(capsys):
    results = [_result(w) for w in EXPECTED_WORKERS]
    results[-1]["gaps"] = ["the demand branch reported an error"]
    report(_payload(results))

    assert "the demand branch reported an error" in capsys.readouterr().out


def test_checker_reads_a_real_artifact_shape(tmp_path):
    """Guard against the checker and the writer drifting apart."""
    path = tmp_path / "run.json"
    path.write_text(json.dumps(_payload([_result(w) for w in EXPECTED_WORKERS])), encoding="utf-8")

    assert all(s == "PASS" for s in _severities(json.loads(path.read_text(encoding="utf-8"))).values())
