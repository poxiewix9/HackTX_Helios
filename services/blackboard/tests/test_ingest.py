"""2.3: OSMnx road-graph ingestion into `road_edges`.

Unit tests use a tiny synthetic MultiDiGraph; the integration test loads the committed
Lahaina sample (`data/samples/osm/lahaina_drive.graphml`, real OSM geometry) offline."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest
from shapely.geometry import LineString as ShapelyLine
from sqlalchemy import Engine

from arco_blackboard import bus, repo
from arco_blackboard.db import session_scope
from arco_blackboard.ingest import (
    IngestConfig,
    graph_bbox,
    graph_to_edges,
    ingest,
    parse_bbox_wsen,
)
from arco_blackboard.service import BlackboardService
from arco_schemas import GraphUpdatedEvent

# --- unit ---------------------------------------------------------------------


def test_config_loads_defaults_and_falls_back() -> None:
    cfg = IngestConfig.load()
    assert cfg.default_network_type == "drive"
    assert cfg.speed_for("motorway") > cfg.speed_for("residential") > 0
    assert cfg.speed_for("not_a_highway_tag") == cfg.speed_m_s["default"]
    assert cfg.speed_for(None) == cfg.speed_m_s["default"]
    assert cfg.capacity_for("primary") > cfg.capacity_for("service")


def test_parse_bbox_wsen() -> None:
    assert parse_bbox_wsen("-156.7,20.86,-156.66,20.9") == (-156.7, 20.86, -156.66, 20.9)
    with pytest.raises(ValueError, match="west < east"):
        parse_bbox_wsen("1,1,0,2")
    with pytest.raises(ValueError, match="west,south,east,north"):
        parse_bbox_wsen("1,2,3")


def _tiny_graph() -> nx.MultiDiGraph:
    g = nx.MultiDiGraph(crs="EPSG:4326")
    g.add_node(1, x=0.0, y=0.0)
    g.add_node(2, x=0.001, y=0.0)
    g.add_node(3, x=0.001, y=0.001)
    # explicit geometry, multi-valued tags (osmnx style lists)
    g.add_edge(
        1,
        2,
        key=0,
        osmid=[100, 101],
        highway=["primary", "residential"],
        name="Front Street",
        length=111.3,
        geometry=ShapelyLine([(0.0, 0.0), (0.0005, 0.0001), (0.001, 0.0)]),
    )
    # no geometry -> straight line from node coords; reverse direction
    g.add_edge(2, 1, key=0, osmid=100, highway="primary", length=111.3)
    # zero length edge must be skipped
    g.add_edge(2, 3, key=0, osmid=200, highway="service", length=0.0)
    # unknown highway falls back to default speed/capacity
    g.add_edge(3, 1, key=0, osmid=300, highway="busway", length=157.0)
    return g


def test_graph_to_edges_derives_cost_capacity_and_geometry() -> None:
    cfg = IngestConfig.load()
    edges = graph_to_edges(_tiny_graph(), cfg)
    assert [e.id for e in edges] == [1, 2, 3]  # zero-length edge dropped, ids dense
    first = edges[0]
    assert first.osm_way_id == 100 and first.highway == "primary"
    assert first.name == "Front Street"
    assert len(first.geom.coordinates) == 3
    assert first.base_cost == pytest.approx(111.3 / cfg.speed_for("primary"))
    assert first.current_cost == first.base_cost
    assert first.capacity_estimate == cfg.capacity_for("primary")
    assert first.status == "open" and first.current_load == 0.0

    reverse = edges[1]
    assert (reverse.u, reverse.v) == (2, 1)
    assert reverse.geom.coordinates == [[0.001, 0.0], [0.0, 0.0]]

    fallback = edges[2]
    assert fallback.base_cost == pytest.approx(157.0 / cfg.speed_m_s["default"])
    assert graph_bbox(_tiny_graph()) == [0.0, 0.0, 0.001, 0.001]


def test_sample_metadata_matches_graph(sample_graphml: Path) -> None:
    meta = json.loads(sample_graphml.with_suffix(".json").read_text())
    assert meta["source"]["name"].startswith("OpenStreetMap")
    assert meta["source"]["license"] == "ODbL 1.0"
    assert meta["network_type"] == "drive"
    parse_bbox_wsen(meta["bbox_wsen"])


# --- integration ----------------------------------------------------------------


@pytest.mark.integration
def test_ingest_sample_graphml_populates_road_edges(
    clean_db: Engine, service: BlackboardService, sample_graphml: Path
) -> None:
    meta = json.loads(sample_graphml.with_suffix(".json").read_text())
    result = ingest(
        "graphml_file", str(sample_graphml), engine=clean_db, publisher=service.publisher
    )
    assert result.edges == meta["edges"] == 637
    assert result.nodes == meta["nodes"]
    assert result.graph_version == 1

    west, south, east, north = parse_bbox_wsen(meta["bbox_wsen"])
    assert west <= result.bbox[0] and result.bbox[2] <= east
    assert south <= result.bbox[1] and result.bbox[3] <= north

    with session_scope(clean_db) as s:
        assert repo.graph_version(s) == 1
        edges = repo.list_edges(s, bbox=[west, south, east, north], limit=10_000)
        assert len(edges) == 637
        assert all(e.length_m > 0 and e.base_cost > 0 for e in edges)
        assert all(e.current_cost == e.base_cost and e.status == "open" for e in edges)
        assert any(e.highway in {"primary", "secondary", "residential"} for e in edges)
        # a sub-bbox must return a strict subset
        mid_lon, mid_lat = (west + east) / 2, (south + north) / 2
        part = repo.list_edges(s, bbox=[west, south, mid_lon, mid_lat], limit=10_000)
        assert 0 < len(part) < 637
        assert repo.list_edges(s, bbox=[10, 10, 11, 11]) == []

    events = bus.read_outbox(clean_db, since_seq=0, topics={"graph"})
    assert len(events) == 1
    msg = events[0].message
    assert isinstance(msg, GraphUpdatedEvent)
    assert msg.full_rebuild is True and msg.graph_version == 1

    # re-ingesting replaces (not appends) and bumps the version again
    again = ingest(
        "graphml_file", str(sample_graphml), engine=clean_db, publisher=service.publisher
    )
    assert again.edges == 637 and again.graph_version == 2
    with session_scope(clean_db) as s:
        assert repo.table_counts(s)["road_edges"] == 637
