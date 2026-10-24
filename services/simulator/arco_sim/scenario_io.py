"""Load and validate scenario files (`data/scenarios/*.json`).

`arco_schemas.Scenario` enforces the shape; `check_scenario` adds the semantic checks the
simulator relies on: everything geographic inside the bbox, hazard ids consistent across
appear/update/resolve, referenced files present, notes naming sources + approximation,
and community quorums actually reachable (two distinct reporters for a pin cluster).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import shape

from arco_schemas import SCHEMA_VERSION, Scenario

APPROX_WORDS = ("approximat", "digitis", "digitiz", "hand-drawn", "hand drawn")


@dataclass
class ScenarioCheck:
    path: Path
    scenario: Scenario | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.scenario is not None and not self.errors


def load_scenario(path: Path) -> Scenario:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Scenario.model_validate(raw)


def list_scenarios(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.json") if not p.name.startswith("_"))


def _inside(bbox: list[float], lon: float, lat: float) -> bool:
    w, s, e, n = bbox
    return w <= lon <= e and s <= lat <= n


def check_scenario(path: Path, *, data_dir: Path) -> ScenarioCheck:
    try:
        sc = load_scenario(path)
    except Exception as exc:  # pydantic / json errors reported, not raised
        return ScenarioCheck(path=path, scenario=None, errors=[f"schema: {exc}"])
    chk = ScenarioCheck(path=path, scenario=sc)
    _check_meta(chk, sc, path, data_dir)
    _check_geography(chk, sc)
    known = _check_timeline(chk, sc)
    _check_community(chk, sc, known)
    _check_feeds(chk, sc, data_dir)
    return chk


def _check_meta(chk: ScenarioCheck, sc: Scenario, path: Path, data_dir: Path) -> None:
    if sc.id != path.stem:
        chk.errors.append(f"id {sc.id!r} must match file name {path.stem!r}")
    if sc.schema_version != SCHEMA_VERSION:
        chk.warnings.append(f"written for schema {sc.schema_version}, current {SCHEMA_VERSION}")
    notes = sc.notes.lower()
    if not any(w in notes for w in APPROX_WORDS):
        chk.errors.append("notes must state that polygons are digitized approximations")
    if "source" not in notes and "data" not in notes:
        chk.errors.append("notes must name the data source(s)")
    for ds in sc.data_sources:
        if not ds.license.strip():
            chk.errors.append(f"data source {ds.name!r} has no license")
    graph_path = data_dir.parent / sc.road_graph_ref
    if sc.road_graph_source == "graphml_file" and not graph_path.exists():
        chk.errors.append(f"road graph file missing: {sc.road_graph_ref}")


def _check_geography(chk: ScenarioCheck, sc: Scenario) -> None:
    for cell in sc.population_cells:
        c = shape(cell.geom.model_dump()).centroid
        if not _inside(sc.bbox, c.x, c.y):
            chk.errors.append(f"population cell {cell.id} outside bbox")
    for zone in sc.safe_zones:
        c = shape(zone.geom.model_dump()).centroid
        if not _inside(sc.bbox, c.x, c.y):
            chk.errors.append(f"safe zone {zone.id} outside bbox")


def _check_timeline(chk: ScenarioCheck, sc: Scenario) -> set[str]:
    seen: set[str] = set()
    for ev in sc.timeline:
        if ev.type == "hazard_appear":
            if ev.hazard_id in seen:
                chk.errors.append(f"hazard {ev.hazard_id} appears twice (use hazard_update)")
            seen.add(ev.hazard_id or "")
        elif ev.type in ("hazard_update", "hazard_resolve") and ev.hazard_id not in seen:
            chk.errors.append(f"{ev.type} at t={ev.t} references unknown hazard {ev.hazard_id}")
        if ev.hazard_polygon_geojson is not None:
            geom = shape(ev.hazard_polygon_geojson.model_dump())
            if not geom.is_valid:
                chk.errors.append(f"hazard {ev.hazard_id} at t={ev.t}: invalid polygon")
            c = geom.centroid
            if not _inside(sc.bbox, c.x, c.y):
                chk.errors.append(f"hazard {ev.hazard_id} at t={ev.t} outside bbox")
    return seen


def _check_community(chk: ScenarioCheck, sc: Scenario, known: set[str]) -> None:
    pins: dict[str, set[str]] = defaultdict(set)
    for ce in sc.community_events:
        if not _inside(sc.bbox, ce.lon, ce.lat):
            chk.errors.append(f"community event at t={ce.t} outside bbox")
        if ce.t > sc.duration_s:
            chk.errors.append(f"community event at t={ce.t} exceeds duration_s")
        if ce.type == "hazard_pin":
            if ce.hazard_class is None:
                chk.errors.append(f"hazard_pin at t={ce.t} needs hazard_class")
            else:
                pins[ce.hazard_class].add(ce.reporter)
        elif ce.target_hazard_id is not None and ce.target_hazard_id not in known:
            chk.errors.append(f"{ce.type} at t={ce.t} targets unknown hazard {ce.target_hazard_id}")
    for cls, reporters in pins.items():
        if len(reporters) < 2:
            chk.warnings.append(
                f"{cls} pins come from one reporter ({next(iter(reporters))}); no quorum"
            )


def _check_feeds(chk: ScenarioCheck, sc: Scenario, data_dir: Path) -> None:
    for feed in sc.micro_feeds:
        if not _inside(sc.bbox, feed.lon, feed.lat):
            chk.errors.append(f"camera {feed.camera_id} outside bbox")
        if feed.video_path is not None and not (data_dir / feed.video_path).exists():
            chk.errors.append(f"camera {feed.camera_id} video missing: {feed.video_path}")
        if feed.calibration.camera_id != feed.camera_id:
            chk.errors.append(
                f"camera {feed.camera_id}: calibration is for {feed.calibration.camera_id}"
            )
    for tile in sc.macro_feed.tiles:
        if not (data_dir / tile.path).exists():
            chk.warnings.append(
                f"macro tile {tile.tile_id} image missing: {tile.path} (synthetic tile used)"
            )


def check_all(directory: Path, *, data_dir: Path) -> list[ScenarioCheck]:
    return [check_scenario(p, data_dir=data_dir) for p in list_scenarios(directory)]
