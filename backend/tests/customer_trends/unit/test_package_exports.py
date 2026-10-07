import subprocess
import sys

import pytest

import edrak.agents.customer_trends as worker


def test_the_entry_points_are_exported_from_the_package() -> None:
    from edrak.agents.customer_trends import runner
    from edrak.agents.customer_trends import worker as worker_module

    assert worker.run_worker is runner.run_worker
    assert worker.arun_worker is runner.arun_worker
    assert worker.run_task is runner.run_task
    assert worker.stream_task is runner.stream_task
    assert worker.CustomerTrendsWorker is worker_module.CustomerTrendsWorker
    assert worker.register is worker_module.register
    assert set(worker.__all__) >= {"run_worker", "CustomerTrendsWorker", "__version__"}


def test_an_unknown_name_is_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'nothing'"):
        _ = worker.nothing  # type: ignore[attr-defined]  # the point of the test


def test_importing_the_package_does_not_load_the_graph() -> None:
    code = (
        "import sys, edrak.agents.customer_trends as w; "
        "print('edrak.agents.customer_trends.runner' in sys.modules, "
        "'langgraph' in sys.modules, w.__version__)"
    )
    out = subprocess.run(  # noqa: S603  # the interpreter running the tests and a fixed snippet
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.split() == ["False", "False", "0.1.0"]
