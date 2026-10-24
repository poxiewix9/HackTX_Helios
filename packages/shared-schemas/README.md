# @arco/schemas / arco-schemas

Single source of truth for every ARCO contract. Only the **Architect** role edits this
package; other roles file requests in `HANDOFFS.md`.

```
python/arco_schemas/     Pydantic v2 models (edit here)
  hazard_classes.py      unified taxonomy (12 classes, macro/micro streams, colours, actions)
  geo.py                 GeoJSON Point/LineString/Polygon/MultiPolygon, BBox
  blackboard.py          Hazard, RoadEdge, EdgeHazardLink, CommunityReport, SafeZone, Route, AgentEvent
  vision.py              CameraGeoref (homography), TileGeoref (affine), FeaturePacketHeader, Detection
  telemetry.py           TelemetrySample, BenchmarkResult, HardwareInfo (mode: live|recorded)
  scenario.py            Scenario file schema (+ SIMULATION_LABEL)
  messages.py            Typed WebSocket messages per channel, BusEnvelope
  export.py              `python -m arco_schemas.export --out schema [--check]`
schema/                  GENERATED JSON Schema bundle + per-model files + hazard_classes.json
ts/src/generated/        GENERATED TypeScript types
ts/src/{index,channels,hazardClasses,labels}.ts   hand-written TS helpers over the generated types
```

## Workflow

```bash
# edit python/arco_schemas/*.py, bump SCHEMA_VERSION if the wire changes
make schemas          # regenerate schema/ and ts/src/generated/
make test             # pytest + vitest (both validate against the same bundle)
make schemas-check    # CI drift guard
```

## Consuming

- Python: `from arco_schemas import Hazard, HazardEvent, Scenario, ...`
- TypeScript: `import type { Hazard } from "@arco/schemas"; import { HAZARD_CLASSES, isMessageOfType } from "@arco/schemas";`

## Versioning

`SCHEMA_VERSION` (semver) lives in `python/arco_schemas/__init__.py` and is written into
the bundle and `ts/src/generated/version.ts`. Breaking wire changes bump the major version
and come with a migration note in `HANDOFFS.md`.
