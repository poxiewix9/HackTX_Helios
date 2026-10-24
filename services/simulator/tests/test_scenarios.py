"""7.2: the three starter scenarios validate (schema + semantic checks) and each tells the
truth about where it came from."""

from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import shape

from arco_agents.geo import haversine_m
from arco_schemas import SCHEMA_VERSION, Scenario
from arco_schemas.scenario import SIMULATION_LABEL
from arco_sim.scenario_io import check_all, check_scenario, list_scenarios, load_scenario

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
SCENARIOS_DIR = DATA_DIR / "scenarios"

EXPECTED = {"lahaina_2023", "joplin_2011", "camp_fire_2018"}


def test_three_starter_scenarios_exist_and_validate() -> None:
    checks = check_all(SCENARIOS_DIR, data_dir=DATA_DIR)
    ids = {c.scenario.id for c in checks if c.scenario}
    assert ids >= EXPECTED
    for c in checks:
        assert c.ok, (c.path.name, c.errors)
        sc = c.scenario
        assert sc is not None
        assert sc.label == SIMULATION_LABEL
        assert sc.schema_version == SCHEMA_VERSION
        low = sc.notes.lower()
        assert "approximat" in low and "openstreetmap" in low
        assert all(ds.license for ds in sc.data_sources)
        assert any("OpenStreetMap" in ds.name for ds in sc.data_sources)
        # the road graph file the scenario names is the committed OSM sample
        assert (DATA_DIR.parent / sc.road_graph_ref).exists()
        meta = json.loads((DATA_DIR.parent / sc.road_graph_ref).with_suffix(".json").read_text())
        assert meta["source"]["license"].startswith("ODbL")
        # every camera is calibrated for itself and inside the bbox
        for feed in sc.micro_feeds:
            assert feed.calibration.camera_id == feed.camera_id
            assert len(feed.calibration.image_points) >= 4


def test_lahaina_scripts_the_smoke_false_negative() -> None:
    """Two *different* phones pin smoke where no vision smoke hazard exists at that time —
    the setup the Consensus override is built for."""
    sc = load_scenario(SCENARIOS_DIR / "lahaina_2023.json")
    pins = [e for e in sc.community_events if e.type == "hazard_pin" and e.hazard_class == "smoke"]
    assert len({p.reporter for p in pins}) >= 2
    t_pin = max(p.t for p in pins)
    assert max(haversine_m(pins[0].lon, pins[0].lat, p.lon, p.lat) for p in pins) < 60
    assert max(p.t for p in pins) - min(p.t for p in pins) < 300
    # vision-negative: no active smoke hazard polygon within 80 m of the pins at t_pin
    for ev in sc.timeline:
        if ev.t <= t_pin and ev.hazard_class == "smoke" and ev.hazard_polygon_geojson is not None:
            poly = shape(ev.hazard_polygon_geojson.model_dump())
            c = poly.centroid
            assert haversine_m(c.x, c.y, pins[0].lon, pins[0].lat) > 300
    # and the camera that would have seen it is scripted as obscured first
    assert any(e.type == "camera_obscured" and e.t < t_pin for e in sc.timeline)


def test_joplin_swath_moves_west_to_east_and_camp_fire_has_a_denial() -> None:
    joplin = load_scenario(SCENARIOS_DIR / "joplin_2011.json")
    swath = [e for e in joplin.timeline if e.hazard_id == "swath" and e.hazard_polygon_geojson]
    easts = [
        max(float(pt[0]) for pt in e.hazard_polygon_geojson.coordinates[0])  # type: ignore[union-attr,arg-type]
        for e in swath
    ]
    assert easts == sorted(easts) and easts[0] < easts[-1]
    assert swath[-1].velocity_m_s == 0.0  # static after passage
    camp = load_scenario(SCENARIOS_DIR / "camp_fire_2018.json")
    denies = [e for e in camp.community_events if e.type == "deny"]
    assert len({d.reporter for d in denies}) >= 2
    target = denies[0].target_hazard_id
    fp = next(e for e in camp.timeline if e.hazard_id == target)
    assert (
        fp.confidence is not None and fp.confidence < 0.6
    )  # scripted low-confidence false positive
    assert any(e.hazard_class == "crowd_bottleneck" for e in camp.timeline)


def test_checker_catches_bad_scenarios(tmp_path: Path) -> None:
    good = json.loads((SCENARIOS_DIR / "lahaina_2023.json").read_text())
    bad = dict(good)
    bad["id"] = "bad_one"
    bad["notes"] = "Real footage of the event, exact polygons from the official survey record."
    bad["timeline"] = [dict(e) for e in good["timeline"]]
    bad["timeline"][1]["hazard_polygon_geojson"] = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]],  # outside the bbox
    }
    bad["community_events"] = [dict(good["community_events"][0])]
    bad["community_events"][0]["lat"] = 0.0
    p = tmp_path / "bad_one.json"
    p.write_text(json.dumps(bad))
    chk = check_scenario(p, data_dir=DATA_DIR)
    assert not chk.ok
    joined = "\n".join(chk.errors)
    assert "approximations" in joined and "outside bbox" in joined
    # schema failures are reported, not raised
    p2 = tmp_path / "broken.json"
    p2.write_text(json.dumps({"id": "broken"}))
    chk2 = check_scenario(p2, data_dir=DATA_DIR)
    assert chk2.scenario is None and chk2.errors and chk2.errors[0].startswith("schema:")
    assert list_scenarios(tmp_path) == [p, p2]
    assert isinstance(Scenario.model_validate(good), Scenario)
