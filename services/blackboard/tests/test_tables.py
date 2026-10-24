"""2.1: tables, GiST indexes, spatial queries and round-trips through the repo layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from bb_helpers import make_hazard, square
from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session

from arco_blackboard import repo
from arco_blackboard.migrate import upgrade_head
from arco_schemas import AgentEvent, CommunityReport, LineString, Point, RoadEdge, Route, SafeZone
from arco_schemas.hazard_classes import HazardClass

# Metres per degree of latitude; used to size test geometries, not for production math.
M_PER_DEG = 111_320.0


def _edge(edge_id: int, coords: list[list[float]], **kw: object) -> RoadEdge:
    return RoadEdge(
        id=edge_id,
        geom=LineString(coordinates=coords),
        u=edge_id * 10,
        v=edge_id * 10 + 1,
        length_m=kw.get("length_m", 100.0),  # type: ignore[arg-type]
        base_cost=10.0,
        current_cost=10.0,
        capacity_estimate=500.0,
    )


@pytest.mark.integration
def test_alembic_head_is_0002(clean_db: Engine) -> None:
    upgrade_head()
    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0002"


@pytest.mark.integration
def test_all_tables_exist_with_gist_indexes(clean_db: Engine) -> None:
    insp = inspect(clean_db)
    tables = set(insp.get_table_names())
    for t in (
        "hazards",
        "road_edges",
        "edge_hazard_links",
        "community_reports",
        "routes",
        "safe_zones",
        "agent_events",
        "graph_state",
        "bus_outbox",
    ):
        assert t in tables, t
    with clean_db.connect() as conn:
        gist = {
            r[0]
            for r in conn.execute(
                text("SELECT tablename FROM pg_indexes WHERE indexdef ILIKE '%USING gist%'")
            )
        }
    assert {"hazards", "road_edges", "community_reports", "routes", "safe_zones"} <= gist


@pytest.mark.integration
def test_hazard_round_trip_and_update(clean_db: Engine) -> None:
    h = make_hazard(square(0.0, 0.0, 0.001))
    with Session(clean_db) as s:
        _, created = repo.upsert_hazard(s, h)
        s.commit()
        assert created
        got = repo.get_hazard(s, h.id)
        assert got is not None
        assert got.model_dump(exclude={"first_seen", "last_seen"}) == h.model_dump(
            exclude={"first_seen", "last_seen"}
        )
        assert abs((got.first_seen - h.first_seen).total_seconds()) < 1e-3

        h2 = h.model_copy(update={"confidence": 0.3, "status": "disputed"})
        _, created2 = repo.upsert_hazard(s, h2)
        s.commit()
        assert not created2
        got2 = repo.get_hazard(s, h.id)
        assert got2 is not None
        assert got2.confidence == 0.3
        assert got2.status == "disputed"


@pytest.mark.integration
def test_hazard_bbox_query_filters_spatially(clean_db: Engine) -> None:
    inside = make_hazard(square(0.0, 0.0, 0.001))
    outside = make_hazard(square(1.0, 1.0, 0.001))
    with Session(clean_db) as s:
        repo.upsert_hazard(s, inside)
        repo.upsert_hazard(s, outside)
        s.commit()
        found = repo.list_hazards(s, bbox=[-0.01, -0.01, 0.01, 0.01])
        assert [h.id for h in found] == [inside.id]
        assert repo.list_hazards(s, bbox=[10, 10, 11, 11]) == []
        assert len(repo.list_hazards(s)) == 2
        assert len(repo.list_hazards(s, status="disputed")) == 0


@pytest.mark.integration
def test_edges_intersecting_reports_metric_overlap_fraction(clean_db: Engine) -> None:
    # Edge A: 0.002 deg (~222 m) long along the equator; hazard square covers its first half.
    # Edge B: far away, must not appear.
    a = _edge(1, [[0.0, 0.0], [0.002, 0.0]], length_m=0.002 * M_PER_DEG)
    b = _edge(2, [[0.5, 0.5], [0.502, 0.5]])
    hazard_geom = square(0.0005, 0.0, 0.0005)  # x in [0, 0.001]
    with Session(clean_db) as s:
        repo.replace_edges(s, [a, b])
        s.commit()
        overlaps = repo.edges_intersecting(s, hazard_geom)
    assert [o.edge_id for o in overlaps] == [1]
    o = overlaps[0]
    assert o.overlap_fraction == pytest.approx(0.5, abs=0.02)
    assert o.edge_length_m == pytest.approx(0.002 * M_PER_DEG, rel=0.01)
    assert o.overlap_length_m == pytest.approx(0.001 * M_PER_DEG, rel=0.02)


@pytest.mark.integration
def test_update_edge_states_bumps_graph_version(clean_db: Engine) -> None:
    with Session(clean_db) as s:
        repo.replace_edges(s, [_edge(1, [[0, 0], [0.001, 0]]), _edge(2, [[0, 0], [0, 0.001]])])
        s.commit()
        v0 = repo.graph_version(s)
        changed = repo.update_edge_states(
            s, [(1, "severed", 10.0, None), (999, "severed", 1, None)]
        )
        s.commit()
        assert [e.id for e in changed] == [1]
        assert changed[0].status == "severed"
        assert repo.graph_version(s) == v0 + 1
        assert repo.list_edges(s, status="open") and all(
            e.id == 2 for e in repo.list_edges(s, status="open")
        )


@pytest.mark.integration
def test_expired_hazards_uses_ttl(clean_db: Engine) -> None:
    now = datetime.now(UTC)
    fresh = make_hazard(square(0, 0, 0.001), ttl_s=600, last_seen=now)
    stale = make_hazard(square(0, 0, 0.001), ttl_s=60, last_seen=now - timedelta(seconds=120))
    with Session(clean_db) as s:
        repo.upsert_hazard(s, fresh)
        repo.upsert_hazard(s, stale)
        s.commit()
        assert [h.id for h in repo.expired_hazards(s, now)] == [stale.id]


@pytest.mark.integration
def test_routes_replace_marks_previous_non_current(clean_db: Engine) -> None:
    zone = SafeZone(id="sz-1", name="School", geom=square(0.01, 0.01, 0.001), capacity=100)

    def route(version: int) -> Route:
        return Route(
            id=uuid4(),
            origin_cell_id="cell-a",
            safe_zone_id="sz-1",
            crowd_group_id="g1",
            group_size=50,
            path=LineString(coordinates=[[0, 0], [0.01, 0.01]]),
            edge_ids=[1, 2],
            cost=12.5,
            length_m=1500.0,
            computed_at=datetime.now(UTC),
            graph_version=1,
            version=version,
            instruction="Head north-east on Front St",
        )

    with Session(clean_db) as s:
        repo.upsert_safe_zone(s, zone)
        repo.replace_routes(s, [route(0)])
        s.commit()
        repo.replace_routes(s, [route(1)])
        s.commit()
        current = repo.list_routes(s, crowd_group_id="g1")
        assert len(current) == 1
        assert current[0].version == 1
        assert len(repo.list_routes(s, current_only=False)) == 2
        assert repo.latest_route_version(s, "g1") == 1
        assert repo.latest_route_version(s, "nope") == -1


@pytest.mark.integration
def test_agent_events_append_and_list(clean_db: Engine) -> None:
    ev = AgentEvent(
        id=uuid4(),
        agent="cartographer",
        kind="edge.severed",
        reasoning="fire_front conf 0.9 overlaps 62% of edge 1 (>50% threshold)",
        scenario_t=12.0,
        wall_t=datetime.now(UTC),
        edge_ids=[1],
        data={"overlap": 0.62},
    )
    with Session(clean_db) as s:
        repo.append_agent_event(s, ev)
        s.commit()
        got = repo.list_agent_events(s, agent="cartographer")
        assert len(got) == 1
        assert got[0].data == {"overlap": 0.62}
        assert repo.list_agent_events(s, agent="navigator") == []


@pytest.mark.integration
def test_reports_near_uses_geography_distance(clean_db: Engine) -> None:
    def rep(lon: float, lat: float) -> CommunityReport:
        return CommunityReport(
            id=uuid4(),
            geom=Point(coordinates=[lon, lat]),
            report_type="hazard_pin",
            hazard_class=HazardClass.SMOKE,
            reporter_hash="abcdef0123456789",
            device_pubkey="pk",
            signature="sig",
            created_at=datetime.now(UTC),
        )

    near = rep(0.0, 0.0)
    far = rep(0.01, 0.0)  # ~1.1 km east
    with Session(clean_db) as s:
        repo.insert_report(s, near)
        repo.insert_report(s, far)
        s.commit()
        assert [r.id for r in repo.reports_near(s, 0.0, 0.0, radius_m=200)] == [near.id]
        assert len(repo.reports_near(s, 0.0, 0.0, radius_m=2000)) == 2
