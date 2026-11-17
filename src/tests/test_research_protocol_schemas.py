"""Smoke tests for the versioned research governance JSON Schemas."""

import json
from pathlib import Path


SCHEMA_DIRECTORY = Path(__file__).resolve().parents[2] / "schemas"
SCHEMA_NAMES = (
    "research-protocol.schema.json",
    "research-run.schema.json",
    "research-event.schema.json",
    "research-decision.schema.json",
)


def test_all_research_governance_schemas_are_valid_json_draft_2020_12():
    for name in SCHEMA_NAMES:
        schema = json.loads((SCHEMA_DIRECTORY / name).read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["type"] == "object"
        assert "title" in schema
