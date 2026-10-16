"""Export the shared contracts as one JSON Schema file for the frontend.

The frontend must not hand-write types for contract objects. This script reads the
Pydantic models in ``backend/src/edrak/contracts`` and writes
``frontend/src/types/contracts.schema.json``. ``npm run gen:types`` in ``frontend/``
then turns that file into ``frontend/src/types/contracts.ts``.

Run from the repository root::

    .venv/bin/python scripts/export_contract_schemas.py

Schemas are generated in serialization mode, which is what the API returns: fields
that have a default are always present, so the TypeScript types mark them required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

OUTPUT = REPO_ROOT / "frontend" / "src" / "types" / "contracts.schema.json"

from pydantic.json_schema import models_json_schema  # noqa: E402

from edrak.contracts import (  # noqa: E402
    BusinessRequest,
    OrchestrationResult,
    ResearchPlan,
    ResearchTask,
    VerificationResult,
    WorkerResult,
)

# Every model the UI reads or sends. Nested models and enums are pulled in as $defs.
EXPORTED_MODELS = [
    BusinessRequest,
    ResearchPlan,
    ResearchTask,
    WorkerResult,
    OrchestrationResult,
    VerificationResult,
]


def build_schema() -> dict:
    _, schema = models_json_schema(
        [(model, "serialization") for model in EXPORTED_MODELS],
        title="EdrakContracts",
    )
    # Pydantic leaves defaulted fields out of ``required`` even in serialization mode.
    # The API serializes every field (defaults included), so a response object always
    # has them all. Mark them required so the UI does not need `?? default` everywhere.
    for definition in schema.get("$defs", {}).values():
        properties = definition.get("properties")
        if properties:
            definition["required"] = sorted(properties)
    return schema


def main() -> int:
    schema = build_schema()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)} ({len(schema.get('$defs', {}))} definitions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
