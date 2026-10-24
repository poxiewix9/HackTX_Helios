"""The exporter is deterministic and the committed artefacts match the Python models."""

from __future__ import annotations

import json
import re
from pathlib import Path

from arco_schemas import HAZARD_CLASSES, SCHEMA_VERSION
from arco_schemas.export import NAMED_UNIONS, TOP_LEVEL_MODELS, build_outputs, main

PKG_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = PKG_ROOT / "schema"
GENERATED_TS = PKG_ROOT / "ts" / "src" / "generated" / "arco.ts"


def test_export_is_deterministic() -> None:
    assert build_outputs() == build_outputs()


def test_bundle_contains_every_model_and_union() -> None:
    bundle = json.loads(build_outputs()["arco.schema.json"])
    defs = bundle["$defs"]
    for model in TOP_LEVEL_MODELS:
        assert model.__name__ in defs, model.__name__
    for name in NAMED_UNIONS:
        assert name in defs, name
    assert bundle["version"] == SCHEMA_VERSION


def test_committed_schema_files_are_current() -> None:
    """Equivalent to `python -m arco_schemas.export --check`; fails if someone edited a
    model without running `make schemas`."""
    assert main(["--out", str(SCHEMA_DIR), "--check"]) == 0


def test_hazard_classes_json_matches_python() -> None:
    data = json.loads((SCHEMA_DIR / "hazard_classes.json").read_text())
    ids = [c["id"] for c in data["classes"]]
    assert ids == [c.value for c in HAZARD_CLASSES]
    for c in data["classes"]:
        info = HAZARD_CLASSES[c["id"]]  # type: ignore[index]
        assert c["color"] == info.color
        assert c["default_action"] == info.default_action


def test_generated_typescript_declares_every_model() -> None:
    """Cheap cross-language check: every Python model has a TS declaration with the
    same name. The TS test-suite validates the other direction with Ajv."""
    assert GENERATED_TS.exists(), "run `make schemas`"
    ts = GENERATED_TS.read_text()
    declared = set(re.findall(r"^export (?:interface|type) (\w+)", ts, flags=re.MULTILINE))
    for model in TOP_LEVEL_MODELS:
        assert model.__name__ in declared, model.__name__
    for name in NAMED_UNIONS:
        assert name in declared, name
    assert f"schema version {SCHEMA_VERSION}" in ts
