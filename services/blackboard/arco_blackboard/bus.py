"""Blackboard event bus.

Design
------
* Every event is written to `bus_outbox` **inside the writer's transaction** and a
  `pg_notify('blackboard_events', <outbox id>)` is queued; Postgres delivers the NOTIFY
  only when the transaction commits, so subscribers never see an event before the data.
* If Redis is configured and reachable, the full `BusEnvelope` is additionally published
  on channel `blackboard.<topic>` right after commit (fast path, lower latency, fan-out
  without hammering Postgres). Redis is optional: when it is missing or dies, nothing is
  lost because the outbox + NOTIFY path is always on.
* `seq` on every message equals the outbox row id, so consumers can detect gaps and
  WebSocket clients can catch up with `since_seq`.
* Subscribers are async iterators of `BusEnvelope`: `RedisSubscriber` (if Redis is up)
  or `NotifySubscriber` (LISTEN + hydrate from the outbox).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from typing import Any, Protocol

import psycopg
import redis
import redis.asyncio as aioredis
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.orm import Session

from arco_blackboard.logging import get_logger
from arco_blackboard.models import BusOutboxRow
from arco_blackboard.settings import Settings, get_settings
from arco_schemas import AnyMessage, BusEnvelope

NOTIFY_CHANNEL = "blackboard_events"
REDIS_PREFIX = "blackboard."
_PENDING_KEY = "arco_pending_redis"
_HOOK_KEY = "arco_redis_hook_installed"

log = get_logger("blackboard.bus")


def topic_for(message: AnyMessage) -> str:
    """`hazard.created` -> `hazard`, `routes.updated` -> `routes`, `detections` -> `detections`."""
    return str(message.type).split(".", 1)[0]


def sqlalchemy_url_to_psycopg(url: str) -> str:
    """`postgresql+psycopg://...` -> `postgresql://...` for a raw psycopg connection."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


# ---------------------------------------------------------------------------
# Publisher (sync; used inside request handlers and agent loops)
# ---------------------------------------------------------------------------


class Publisher:
    """Writes outbox + NOTIFY in the caller's session; mirrors to Redis after commit."""

    def __init__(self, redis_client: redis.Redis | None, publisher_name: str) -> None:
        self._redis = redis_client
        self.publisher_name = publisher_name

    @property
    def redis_enabled(self) -> bool:
        return self._redis is not None

    @property
    def mode(self) -> str:
        return "redis+notify" if self._redis is not None else "notify"

    def publish(self, session: Session, message: AnyMessage) -> BusEnvelope:
        """Assign `seq`, write the outbox row, queue NOTIFY, schedule Redis mirror."""
        seq = int(session.scalar(select(func.nextval("bus_outbox_id_seq"))) or 0)
        message = message.model_copy(update={"seq": seq})
        env = BusEnvelope(
            topic=topic_for(message),
            published_at=datetime.now(UTC),
            publisher=self.publisher_name,
            message=message,
        )
        payload = json.loads(env.model_dump_json())
        session.add(
            BusOutboxRow(
                id=seq,
                topic=env.topic,
                publisher=env.publisher,
                published_at=env.published_at,
                payload=payload,
            )
        )
        session.flush()
        session.execute(text("SELECT pg_notify(:ch, :id)"), {"ch": NOTIFY_CHANNEL, "id": str(seq)})
        if self._redis is not None:
            self._queue_redis(session, env)
        return env

    # -- redis after-commit mirroring ---------------------------------------

    def _queue_redis(self, session: Session, env: BusEnvelope) -> None:
        pending: list[BusEnvelope] = session.info.setdefault(_PENDING_KEY, [])
        pending.append(env)
        if not session.info.get(_HOOK_KEY):
            session.info[_HOOK_KEY] = True
            event.listen(session, "after_commit", self._on_commit)
            event.listen(session, "after_rollback", self._on_rollback)

    def _on_commit(self, session: Session) -> None:
        pending: list[BusEnvelope] = session.info.pop(_PENDING_KEY, [])
        self.publish_to_redis(pending)

    def _on_rollback(self, session: Session) -> None:
        session.info.pop(_PENDING_KEY, None)

    def publish_to_redis(self, envelopes: Iterable[BusEnvelope]) -> None:
        if self._redis is None:
            return
        try:
            pipe = self._redis.pipeline(transaction=False)
            for env in envelopes:
                pipe.publish(REDIS_PREFIX + env.topic, env.model_dump_json())
            pipe.execute()
        except (redis.RedisError, OSError) as exc:  # Redis died mid-run: outbox still has it
            log.warning("redis publish failed; NOTIFY path still delivered", error=str(exc))

    def close(self) -> None:
        if self._redis is not None:
            with contextlib.suppress(Exception):
                self._redis.close()


def connect_redis(url: str | None, timeout_s: float = 1.0) -> redis.Redis | None:
    """Return a live sync Redis client or None (unset / unreachable)."""
    if not url:
        return None
    try:
        client = redis.Redis.from_url(
            url, socket_connect_timeout=timeout_s, socket_timeout=timeout_s
        )
        client.ping()
        return client
    except (redis.RedisError, OSError, ValueError) as exc:
        log.warning(
            "redis unavailable; falling back to LISTEN/NOTIFY only", url=url, error=str(exc)
        )
        return None


def make_publisher(
    settings: Settings | None = None, publisher_name: str = "blackboard"
) -> Publisher:
    settings = settings or get_settings()
    client = connect_redis(settings.redis_url)
    pub = Publisher(client, publisher_name)
    log.info("event bus publisher ready", mode=pub.mode)
    return pub


# ---------------------------------------------------------------------------
# Outbox catch-up (sync)
# ---------------------------------------------------------------------------


def read_outbox(
    engine: Engine, *, since_seq: int, topics: set[str] | None = None, limit: int = 5000
) -> list[BusEnvelope]:
    stmt = (
        select(BusOutboxRow)
        .where(BusOutboxRow.id > since_seq)
        .order_by(BusOutboxRow.id)
        .limit(limit)
    )
    if topics:
        stmt = stmt.where(BusOutboxRow.topic.in_(sorted(topics)))
    with Session(engine) as s:
        return [BusEnvelope.model_validate(r.payload) for r in s.scalars(stmt)]


def latest_seq(engine: Engine) -> int:
    with Session(engine) as s:
        v = s.scalar(select(func.max(BusOutboxRow.id)))
    return int(v or 0)


# ---------------------------------------------------------------------------
# Subscribers (async)
# ---------------------------------------------------------------------------


class Subscriber(Protocol):
    name: str

    def events(self, topics: set[str] | None = None) -> AsyncIterator[BusEnvelope]: ...

    async def close(self) -> None: ...


class NotifySubscriber:
    """LISTEN on `blackboard_events`; each payload is an outbox id to hydrate."""

    name = "notify"

    def __init__(self, database_url: str) -> None:
        self._dsn = sqlalchemy_url_to_psycopg(database_url)
        self._listen_conn: psycopg.AsyncConnection[Any] | None = None
        self._hydrate_conn: psycopg.AsyncConnection[Any] | None = None

    async def _connect(self) -> None:
        self._listen_conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
        self._hydrate_conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
        await self._listen_conn.execute(f"LISTEN {NOTIFY_CHANNEL}")

    async def _hydrate(self, outbox_id: int) -> BusEnvelope | None:
        assert self._hydrate_conn is not None
        cur = await self._hydrate_conn.execute(
            "SELECT payload FROM bus_outbox WHERE id = %s", (outbox_id,)
        )
        row = await cur.fetchone()
        return BusEnvelope.model_validate(row[0]) if row else None

    async def events(self, topics: set[str] | None = None) -> AsyncIterator[BusEnvelope]:
        if self._listen_conn is None:
            await self._connect()
        assert self._listen_conn is not None
        async for n in self._listen_conn.notifies():
            try:
                outbox_id = int(n.payload)
            except ValueError:
                continue
            env = await self._hydrate(outbox_id)
            if env is None or (topics and env.topic not in topics):
                continue
            yield env

    async def close(self) -> None:
        for conn in (self._listen_conn, self._hydrate_conn):
            if conn is not None:
                with contextlib.suppress(Exception):
                    await conn.close()
        self._listen_conn = self._hydrate_conn = None


class RedisSubscriber:
    """PSUBSCRIBE `blackboard.*`; payloads are full envelopes."""

    name = "redis"

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: aioredis.Redis | None = None
        self._pubsub: Any = None

    async def events(self, topics: set[str] | None = None) -> AsyncIterator[BusEnvelope]:
        self._client = aioredis.Redis.from_url(self._url)
        self._pubsub = self._client.pubsub()
        if topics:
            await self._pubsub.subscribe(*(REDIS_PREFIX + t for t in sorted(topics)))
        else:
            await self._pubsub.psubscribe(REDIS_PREFIX + "*")
        while True:
            msg = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                await asyncio.sleep(0)  # let cancellation through
                continue
            data = msg.get("data")
            if isinstance(data, bytes | str):
                yield BusEnvelope.model_validate_json(data)

    async def close(self) -> None:
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
        self._pubsub = self._client = None


def make_subscriber(settings: Settings | None = None, *, prefer: str | None = None) -> Subscriber:
    """Redis if reachable (or `prefer="notify"` to force the fallback)."""
    settings = settings or get_settings()
    if prefer != "notify" and settings.redis_url:
        probe = connect_redis(settings.redis_url)
        if probe is not None:
            probe.close()
            return RedisSubscriber(settings.redis_url)
    return NotifySubscriber(settings.database_url)
