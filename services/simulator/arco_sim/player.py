"""Scenario player.

The scenario clock `t` (seconds since scenario start) is the single source of truth. The
player advances it from wall time x `speed` while playing, fires every timeline /
community event whose `t` has passed, and publishes `clock.tick` so every other service
shares the same clock. Scrubbing (`seek`) reconciles the blackboard to the state the
timeline prescribes at the new `t`.

All writes go through `BlackboardClient` (HTTP). Hazard ids are stable per scenario:
`uuid5(SIM_NS, "<scenario id>:<timeline hazard_id>")`, so appear/update/resolve of one
scripted hazard hit one blackboard row and re-loading a scenario is idempotent.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

from arco_agents.reports import DeviceKey
from arco_schemas import HAZARD_CLASSES, Hazard, HazardClass, HazardGeometry, Scenario
from arco_schemas.scenario import CommunityEvent, TimelineEvent
from arco_sim.clients import AgentsClient, BlackboardClient
from arco_sim.logging import get_logger

log = get_logger("simulator.player")

SIM_NS = uuid.UUID("7a3d1b6e-5c2f-4e8a-9f0b-2c1d3e4f5a6b")
SERVICE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLAYER_CONFIG = SERVICE_ROOT / "config" / "player.yaml"


class HazardsCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ttl_s: float = Field(gt=0)
    source: str = "simulated"


class CommunityCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key_seed_prefix: str


class ClockCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hz: float = Field(gt=0)


class SeekCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fire_skipped_community_events: bool = True


class PlayerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hazards: HazardsCfg
    community: CommunityCfg
    clock: ClockCfg
    seek: SeekCfg

    @classmethod
    def load(cls, path: Path = DEFAULT_PLAYER_CONFIG) -> PlayerConfig:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class LogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t: float
    wall_t: datetime
    kind: str
    detail: str


class ExpectedCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected: list[int]
    severed: list[int]
    missing: list[int]
    ok: bool


class PlayerState(BaseModel):
    """What `/state` returns; `label` is always the simulation label."""

    model_config = ConfigDict(extra="forbid")
    label: str
    scenario_id: str | None
    scenario_name: str | None
    loaded: bool
    playing: bool
    speed: float
    t: float
    duration_s: float | None
    timeline_cursor: int
    timeline_total: int
    community_cursor: int
    community_total: int
    active_hazards: list[str]
    cellular_up: bool
    graph_version: int | None
    events_fired: int
    reports_sent: int
    last_error: str | None
    log: list[LogEntry]


def _fold_timeline(
    timeline: list[TimelineEvent], t: float
) -> tuple[dict[str, TimelineEvent], bool, int]:
    """State the timeline prescribes at `t`: last event per hazard id, cellular state,
    and the cursor (index of the first event after `t`)."""
    desired: dict[str, TimelineEvent] = {}
    net_up = True
    cursor = 0
    for i, ev in enumerate(timeline):
        if ev.t > t:
            break
        cursor = i + 1
        if ev.hazard_id is not None and ev.type.startswith("hazard_"):
            desired[ev.hazard_id] = ev
        elif ev.type == "cell_network_down":
            net_up = False
        elif ev.type == "cell_network_up":
            net_up = True
    return desired, net_up, cursor


@dataclass
class _Hazard:
    scenario_id: str
    blackboard_id: uuid.UUID
    active: bool
    last_event: TimelineEvent


@dataclass
class Player:
    bb: BlackboardClient
    agents: AgentsClient | None = None
    config: PlayerConfig = field(default_factory=PlayerConfig.load)
    clock: Callable[[], float] = time.monotonic
    on_change: Callable[[], None] | None = None

    scenario: Scenario | None = None
    t: float = 0.0
    speed: float = 1.0
    playing: bool = False
    cellular_up: bool = True
    timeline_cursor: int = 0
    community_cursor: int = 0
    events_fired: int = 0
    reports_sent: int = 0
    graph_version: int | None = None
    last_error: str | None = None
    hazards: dict[str, _Hazard] = field(default_factory=dict)
    log: list[LogEntry] = field(default_factory=list)
    _last_wall: float | None = None
    _last_clock_pub: float = -1e9
    _keys: dict[str, DeviceKey] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    # ------------------------------------------------------------------ state

    def state(self) -> PlayerState:
        sc = self.scenario
        return PlayerState(
            label="Simulated on real event geometry",
            scenario_id=sc.id if sc else None,
            scenario_name=sc.name if sc else None,
            loaded=sc is not None,
            playing=self.playing,
            speed=self.speed,
            t=round(self.t, 3),
            duration_s=sc.duration_s if sc else None,
            timeline_cursor=self.timeline_cursor,
            timeline_total=len(sc.timeline) if sc else 0,
            community_cursor=self.community_cursor,
            community_total=len(sc.community_events) if sc else 0,
            active_hazards=sorted(k for k, h in self.hazards.items() if h.active),
            cellular_up=self.cellular_up,
            graph_version=self.graph_version,
            events_fired=self.events_fired,
            reports_sent=self.reports_sent,
            last_error=self.last_error,
            log=self.log[-50:],
        )

    def _note(self, kind: str, detail: str) -> None:
        self.log.append(LogEntry(t=self.t, wall_t=datetime.now(UTC), kind=kind, detail=detail))
        if len(self.log) > 500:
            del self.log[: len(self.log) - 500]
        log.info(kind, t=round(self.t, 1), detail=detail)

    def _changed(self) -> None:
        self._publish_clock(force=True)
        if self.on_change is not None:
            self.on_change()

    # ------------------------------------------------------------------- load

    def load(self, scenario: Scenario) -> None:
        """Reset the world to the scenario's t=0: resolve leftover hazards, (re)ingest the
        road graph, hand the population to the agents, publish a paused clock."""
        with self._lock:
            self.playing = False
            self._last_wall = None
            self._resolve_everything_active(reason="scenario load")
            self.scenario = scenario
            self.t = 0.0
            self.timeline_cursor = 0
            self.community_cursor = 0
            self.events_fired = 0
            self.reports_sent = 0
            self.hazards = {}
            self._keys = {}
            self.cellular_up = True
            self.last_error = None
            self.log = []
            # graphml refs are repo-root relative; the blackboard resolves them itself.
            res = self.bb.ingest_graph(scenario.road_graph_source, scenario.road_graph_ref)
            self.graph_version = int(res["graph_version"])
            self._note(
                "scenario.loaded",
                f"{scenario.id}: graph {res['nodes']} nodes / {res['edges']} edges "
                f"(v{self.graph_version}), {len(scenario.timeline)} timeline events, "
                f"{len(scenario.community_events)} community events",
            )
            if self.agents is not None:
                pop = {
                    "population_cells": [
                        c.model_dump(mode="json") for c in scenario.population_cells
                    ],
                    "safe_zones": [z.model_dump(mode="json") for z in scenario.safe_zones],
                    "label": scenario.label,
                    "notes": scenario.notes,
                }
                out = self.agents.set_population(pop)
                self._note(
                    "population.sent",
                    f"{len(scenario.population_cells)} cells / {len(scenario.safe_zones)} zones"
                    + (f" -> {out['routes']} routes" if out else " (agents unreachable)"),
                )
            self._changed()

    def _resolve_everything_active(self, *, reason: str) -> int:
        n = 0
        try:
            for h in self.bb.list_hazards(status="active"):
                self.bb.set_hazard_status(h.id, "resolved", scenario_t=self.t)
                n += 1
        except httpx.HTTPError as exc:
            self.last_error = f"reset: {exc}"
        if n:
            self._note("hazards.reset", f"resolved {n} active hazard(s) before {reason}")
        return n

    # --------------------------------------------------------------- controls

    def play(self) -> None:
        with self._lock:
            if self.scenario is None:
                raise RuntimeError("no scenario loaded")
            if self.scenario.duration_s <= self.t:
                self.t = 0.0  # replay from the start
                self.seek(0.0)
            self.playing = True
            self._last_wall = self.clock()
            self._note("play", f"speed x{self.speed}")
            self._changed()

    def pause(self) -> None:
        with self._lock:
            self.tick()
            self.playing = False
            self._last_wall = None
            self._note("pause", f"t={self.t:.1f}")
            self._changed()

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("speed must be > 0")
        with self._lock:
            self.tick()
            self.speed = speed
            self._note("speed", f"x{speed}")
            self._changed()

    def seek(self, t: float) -> None:
        """Scrub to `t`: reconcile hazards + network to the timeline state at `t`."""
        with self._lock:
            sc = self.scenario
            if sc is None:
                raise RuntimeError("no scenario loaded")
            t = max(0.0, min(float(t), sc.duration_s))
            forward = t >= self.t
            prev_community = self.community_cursor
            self.t = t
            self._last_wall = self.clock() if self.playing else None
            desired, net_up, self.timeline_cursor = _fold_timeline(sc.timeline, t)
            for hid, ev in desired.items():
                if ev.type == "hazard_resolve":
                    self._resolve(hid, ev)
                else:
                    self._upsert(hid, ev)
            for hid, h in list(self.hazards.items()):
                if hid not in desired and h.active:
                    self._resolve(hid, h.last_event)
            if net_up != self.cellular_up:
                self._set_cellular(net_up, reason="seek")
            # Community cursor: forward fires skipped events (configurable); backward rewinds.
            new_cursor = sum(1 for ce in sc.community_events if ce.t <= t)
            if forward and self.config.seek.fire_skipped_community_events:
                for ce in sc.community_events[prev_community:new_cursor]:
                    self.t = ce.t
                    self._fire_community(ce)
                self.t = t
            self.community_cursor = new_cursor
            self._note("seek", f"t={t:.1f} ({'forward' if forward else 'backward'})")
            self._changed()

    # ------------------------------------------------------------------- time

    def tick(self) -> int:
        """Advance from wall time; fire due events; publish the clock. Returns fired count."""
        with self._lock:
            if self.scenario is None:
                return 0
            now = self.clock()
            if self.playing and self._last_wall is not None:
                dt = (now - self._last_wall) * self.speed
                self._last_wall = now
                return self.advance(dt)
            self._last_wall = now if self.playing else None
            self._publish_clock(force=False)
            return 0

    def advance(self, dt: float) -> int:
        """Advance the scenario clock by `dt` scenario-seconds (no wall clock involved)."""
        with self._lock:
            sc = self.scenario
            if sc is None or dt <= 0:
                return 0
            target = min(sc.duration_s, self.t + dt)
            fired = 0
            # Merge timeline + community events in time order and stamp each with its own
            # scenario time, so a coarse tick (or a big seek) still produces an accurate
            # `scenario_t` on every message.
            while True:
                tl = (
                    sc.timeline[self.timeline_cursor]
                    if self.timeline_cursor < len(sc.timeline)
                    else None
                )
                ce = (
                    sc.community_events[self.community_cursor]
                    if self.community_cursor < len(sc.community_events)
                    else None
                )
                if tl is not None and tl.t <= target and (ce is None or tl.t <= ce.t):
                    self.t = max(self.t, tl.t)
                    self._fire_timeline(tl)
                    self.timeline_cursor += 1
                elif ce is not None and ce.t <= target:
                    self.t = max(self.t, ce.t)
                    self._fire_community(ce)
                    self.community_cursor += 1
                else:
                    break
                fired += 1
            self.t = target
            if self.t >= sc.duration_s and self.playing:
                self.playing = False
                self._last_wall = None
                self._note("end", f"scenario finished at t={self.t:.1f}")
                self._changed()
            else:
                self._publish_clock(force=fired > 0)
            return fired

    def run(self, stop: threading.Event, *, sleep: Callable[[float], None] = time.sleep) -> None:
        period = 1.0 / (self.config.clock.hz * 4)
        while not stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # keep the loop alive; surface the error in /state
                self.last_error = repr(exc)
                log.error("player tick failed", error=repr(exc))
            sleep(period)

    # ----------------------------------------------------------------- events

    def _fire_timeline(self, ev: TimelineEvent) -> None:
        self.events_fired += 1
        try:
            if ev.type in ("hazard_appear", "hazard_update"):
                assert ev.hazard_id is not None
                self._upsert(ev.hazard_id, ev)
            elif ev.type == "hazard_resolve":
                assert ev.hazard_id is not None
                self._resolve(ev.hazard_id, ev)
            elif ev.type == "cell_network_down":
                self._set_cellular(False, reason=ev.note or "scenario")
            elif ev.type == "cell_network_up":
                self._set_cellular(True, reason=ev.note or "scenario")
            elif ev.type == "camera_obscured":
                self._note("camera.obscured", f"{ev.camera_id}: {ev.note or ''}")
            else:
                self._note("note", ev.note or "")
        except httpx.HTTPError as exc:
            self.last_error = f"{ev.type}@{ev.t}: {exc}"
            self._note("error", self.last_error)

    def _hazard_from_event(self, hid: str, ev: TimelineEvent) -> Hazard:
        sc = self.scenario
        assert sc is not None and ev.hazard_polygon_geojson is not None
        assert ev.hazard_class is not None and ev.confidence is not None and ev.stream is not None
        now = datetime.now(UTC)
        bb_id = uuid.uuid5(SIM_NS, f"{sc.id}:{hid}")
        return Hazard(
            id=bb_id,
            geom=ev.hazard_polygon_geojson,
            hazard_class=ev.hazard_class,
            confidence=ev.confidence,
            source="simulated",
            stream=ev.stream,
            camera_id=ev.camera_id,
            first_seen=now,
            last_seen=now,
            ttl_s=self.config.hazards.ttl_s,
            status="active",
            severity=HAZARD_CLASSES[ev.hazard_class].default_severity,
            velocity_m_s=ev.velocity_m_s,
        )

    def _upsert(self, hid: str, ev: TimelineEvent) -> None:
        hz = self._hazard_from_event(hid, ev)
        out = self.bb.upsert_hazard(hz, scenario_t=self.t)
        self.hazards[hid] = _Hazard(hid, hz.id, True, ev)
        self._note(
            out.type,
            f"{hid} {ev.hazard_class} conf={ev.confidence} stream={ev.stream}"
            + (f" v={ev.velocity_m_s} m/s" if ev.velocity_m_s else "")
            + (f" — {ev.note}" if ev.note else ""),
        )

    def _resolve(self, hid: str, ev: TimelineEvent) -> None:
        sc = self.scenario
        assert sc is not None
        bb_id = uuid.uuid5(SIM_NS, f"{sc.id}:{hid}")
        res = self.bb.set_hazard_status(bb_id, "resolved", scenario_t=self.t)
        h = self.hazards.get(hid)
        if h is not None:
            h.active = False
            h.last_event = ev
        else:
            self.hazards[hid] = _Hazard(hid, bb_id, False, ev)
        self._note("hazard.resolved" if res else "hazard.resolve.noop", f"{hid}")

    def _set_cellular(self, up: bool, *, reason: str) -> None:
        self.cellular_up = up
        self.bb.post_network(cellular_up=up, reason=reason, scenario_t=self.t)
        self._note("network.cellular", f"{'UP' if up else 'DOWN'} ({reason})")

    def _key(self, reporter: str) -> DeviceKey:
        sc = self.scenario
        assert sc is not None
        key = self._keys.get(reporter)
        if key is None:
            seed = f"{self.config.community.key_seed_prefix}:{sc.id}:{reporter}".encode()
            key = self._keys[reporter] = DeviceKey.from_seed(seed)
        return key

    def _fire_community(self, ce: CommunityEvent) -> None:
        sc = self.scenario
        assert sc is not None
        self.events_fired += 1
        target = (
            uuid.uuid5(SIM_NS, f"{sc.id}:{ce.target_hazard_id}") if ce.target_hazard_id else None
        )
        report = self._key(ce.reporter).make_report(
            lon=ce.lon,
            lat=ce.lat,
            report_type=ce.type,
            hazard_class=ce.hazard_class,
            target_hazard_id=target,
            note=ce.note,
        )
        try:
            self.bb.post_report(report, scenario_t=self.t)
            self.reports_sent += 1
            self._note(
                "report.sent",
                f"{ce.reporter} {ce.type}"
                + (f" {ce.hazard_class}" if ce.hazard_class else "")
                + (f" on {ce.target_hazard_id}" if ce.target_hazard_id else "")
                + (f" — {ce.note}" if ce.note else ""),
            )
        except httpx.HTTPError as exc:
            self.last_error = f"report@{ce.t}: {exc}"
            self._note("error", self.last_error)

    def _publish_clock(self, *, force: bool) -> None:
        sc = self.scenario
        now = self.clock()
        if sc is None or (not force and now - self._last_clock_pub < 1.0 / self.config.clock.hz):
            return
        if not force and not self.playing:
            return
        try:
            self.bb.post_clock(
                scenario_id=sc.id,
                scenario_t=self.t,
                playing=self.playing,
                speed=self.speed,
                duration_s=sc.duration_s,
            )
            self._last_clock_pub = now
        except httpx.HTTPError as exc:
            self.last_error = f"clock: {exc}"

    # ------------------------------------------------------------------ chaos

    def inject_hazard(
        self,
        *,
        geom: HazardGeometry,
        hazard_class: HazardClass,
        confidence: float = 0.9,
        velocity_m_s: float | None = None,
        note: str | None = None,
    ) -> Hazard:
        """Dashboard chaos button: an ad-hoc simulated hazard through the same path."""
        with self._lock:
            now = datetime.now(UTC)
            hz = Hazard(
                id=uuid.uuid4(),
                geom=geom,
                hazard_class=hazard_class,
                confidence=confidence,
                source="simulated",
                stream=None,
                camera_id=None,
                first_seen=now,
                last_seen=now,
                ttl_s=self.config.hazards.ttl_s,
                status="active",
                severity=HAZARD_CLASSES[hazard_class].default_severity,
                velocity_m_s=velocity_m_s,
            )
            self.bb.upsert_hazard(hz, scenario_t=self.t)
            key = f"chaos-{str(hz.id)[:8]}"
            self.hazards[key] = _Hazard(
                key,
                hz.id,
                True,
                TimelineEvent(t=self.t, type="note", note=note or "chaos"),
            )
            self._note("chaos.hazard", f"{hazard_class} conf={confidence} {note or ''}".strip())
            self._changed()
            return hz

    def set_cellular(self, up: bool, *, reason: str = "chaos") -> None:
        with self._lock:
            self._set_cellular(up, reason=reason)
            self._changed()

    # ------------------------------------------------------------------ checks

    def check_expected(self) -> ExpectedCheck:
        sc = self.scenario
        expected = list(sc.expected_blocked_edges) if sc else []
        severed = sorted(e.id for e in self.bb.list_edges(status="severed"))
        missing = [e for e in expected if e not in severed]
        return ExpectedCheck(expected=expected, severed=severed, missing=missing, ok=not missing)

    def summary(self) -> dict[str, Any]:
        return self.state().model_dump(mode="json", exclude={"log"})
