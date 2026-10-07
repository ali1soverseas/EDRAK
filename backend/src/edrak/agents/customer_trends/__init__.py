"""The Customer and Trends worker.

The entry points are exported lazily, so importing a submodule (settings, schemas) does not load
the graph and its dependencies.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from edrak.agents.customer_trends.runner import arun_worker, run_task, run_worker, stream_task
    from edrak.agents.customer_trends.worker import CustomerTrendsWorker, register

__version__ = "0.1.0"

_EXPORTS = {
    "run_worker": "runner",
    "arun_worker": "runner",
    "run_task": "runner",
    "stream_task": "runner",
    "CustomerTrendsWorker": "worker",
    "register": "worker",
}

__all__ = [
    "CustomerTrendsWorker",
    "__version__",
    "arun_worker",
    "register",
    "run_task",
    "run_worker",
    "stream_task",
]


def __getattr__(name: str) -> Any:
    if name in _EXPORTS:
        return getattr(import_module(f"{__name__}.{_EXPORTS[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
