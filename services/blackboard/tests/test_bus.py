"""2.2: event bus. A hazard insert must be observable on BOTH the Redis channel and the
Postgres NOTIFY path, seq must equal the outbox id, and Redis must be optional."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from bb_helpers import make_hazard, square
from sqlalchemy import Engine

from arco_blackboard import bus
from arco_blackboard.service import BlackboardService
from arco_blackboard.settings import Settings
from arco_schemas import BusEnvelope, HazardEvent, RoutesUpdatedMessage

# --- unit ---------------------------------------------------------------------


def test_topic_for_uses_prefix_before_dot() -> None:
    msg = RoutesUpdatedMessage(
        scenario_t=0,
        wall_t=datetime.now(UTC),
        seq=0,
        graph_version=0,
        routes=[],
        trigger="manual",
        compute_ms=0,
        max_edge_load_ratio=0,
    )
    assert bus.topic_for(msg) == "routes"


def test_sqlalchemy_url_to_psycopg() -> None:
    assert bus.sqlalchemy_url_to_psycopg("postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"


def test_connect_redis_returns_none_when_unreachable() -> None:
    assert bus.connect_redis("redis://127.0.0.1:1/0", timeout_s=0.2) is None
    assert bus.connect_redis(None) is None


# --- integration ----------------------------------------------------------------


async def _collect(events: AsyncIterator[BusEnvelope], n: int, timeout: float) -> list[BusEnvelope]:
    out: list[BusEnvelope] = []

    async def run() -> None:
        async for env in events:
            out.append(env)
            if len(out) >= n:
                return

    await asyncio.wait_for(run(), timeout)
    return out


@pytest.mark.integration
async def test_hazard_insert_is_observed_on_notify(
    service: BlackboardService, settings: Settings
) -> None:
    sub = bus.NotifySubscriber(settings.database_url)
    events = sub.events(topics={"hazard"})
    # prime the LISTEN before publishing
    task = asyncio.create_task(_collect(events, 1, timeout=10))
    await asyncio.sleep(0.3)
    h = make_hazard(square(0, 0, 0.001))
    published = await asyncio.to_thread(service.upsert_hazard, h, scenario_t=5.0)
    got = await task
    await sub.close()
    assert len(got) == 1
    env = got[0]
    assert env.topic == "hazard"
    assert isinstance(env.message, HazardEvent)
    assert env.message.type == "hazard.created"
    assert env.message.hazard.id == h.id
    assert env.message.seq == published.seq
    assert env.message.scenario_t == 5.0


@pytest.mark.integration
async def test_hazard_insert_is_observed_on_redis(
    service: BlackboardService, settings: Settings
) -> None:
    if not service.publisher.redis_enabled:
        pytest.skip("Redis not reachable; NOTIFY-only mode (covered by the notify test)")
    assert settings.redis_url is not None
    sub = bus.RedisSubscriber(settings.redis_url, channel_prefix=settings.arco_bus_prefix)
    task = asyncio.create_task(_collect(sub.events(topics={"hazard"}), 1, timeout=10))
    await asyncio.sleep(0.3)
    h = make_hazard(square(0, 0, 0.001))
    published = await asyncio.to_thread(service.upsert_hazard, h)
    got = await task
    await sub.close()
    assert isinstance(got[0].message, HazardEvent)
    assert got[0].message.hazard.id == h.id
    assert got[0].message.seq == published.seq


@pytest.mark.integration
def test_seq_is_monotonic_and_matches_outbox(service: BlackboardService, clean_db: Engine) -> None:
    e1 = service.upsert_hazard(make_hazard(square(0, 0, 0.001)))
    e2 = service.upsert_hazard(make_hazard(square(0, 0, 0.001)))
    assert e2.seq > e1.seq
    rows = bus.read_outbox(clean_db, since_seq=0)
    assert [r.message.seq for r in rows] == [e1.seq, e2.seq]
    assert bus.read_outbox(clean_db, since_seq=e1.seq)[0].message.seq == e2.seq
    assert bus.latest_seq(clean_db) == e2.seq
    assert bus.read_outbox(clean_db, since_seq=0, topics={"routes"}) == []


@pytest.mark.integration
def test_publisher_falls_back_when_redis_unreachable(clean_db: Engine, settings: Settings) -> None:
    broken = settings.model_copy(update={"redis_url": "redis://127.0.0.1:1/0"})
    pub = bus.make_publisher(broken, publisher_name="fallback-test")
    assert pub.mode == "notify"
    svc = BlackboardService(clean_db, pub)
    ev = svc.upsert_hazard(make_hazard(square(0, 0, 0.001)))
    assert bus.read_outbox(clean_db, since_seq=0)[0].message.seq == ev.seq
    sub = bus.make_subscriber(broken)
    assert sub.name == "notify"


@pytest.mark.integration
def test_update_and_resolve_emit_distinct_event_types(service: BlackboardService) -> None:
    h = make_hazard(square(0, 0, 0.001))
    assert service.upsert_hazard(h).type == "hazard.created"
    assert service.upsert_hazard(h.model_copy(update={"confidence": 0.5})).type == "hazard.updated"
    resolved = service.set_hazard_status(h.id, "resolved")
    assert resolved is not None and resolved.type == "hazard.resolved"
    assert service.set_hazard_status(h.id, "disputed") is not None
    stored = service.get_hazard(h.id)
    assert stored is not None and stored.status == "disputed"
