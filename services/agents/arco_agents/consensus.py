"""Consensus agent: community reports -> trust -> clusters -> overrides.

Inputs: `report.created` events (signed `CommunityReport`s) and `hazard.*` events from
vision (to corroborate earlier reports). Outputs go through the blackboard service only:
`upsert_hazard` / `set_hazard_status` (so the Cartographer sees them exactly like vision
hazards) and one `AgentEvent` per decision with the reasoning spelled out.

The policy numbers are in `config/consensus.yaml`; the rules are:

  * a report with a bad signature, a wrong fingerprint, or over the rate limit is
    rejected (logged, trust penalised for rate abuse) and never counted;
  * a `hazard_pin` near an ACTIVE vision hazard of the same class corroborates it
    (confidence up a little per independent trusted reporter, reporter trust up);
  * otherwise pins are clustered (spatial-temporal DBSCAN per class). A cluster with
    `k_independent` distinct reporters above `min_to_count` whose trust sums to
    `min_weighted`, at a location where vision sees nothing of that class
    ("vision-negative"), becomes a COMMUNITY hazard; a vision hazard of that class that
    was resolved recently there is marked `disputed` (vision said clear, people say not);
  * `deny` / `all_clear` against a hazard lower its confidence once an independent
    trusted quorum agrees; a community hazard can be resolved that way, a vision hazard
    is only ever marked `disputed` — never deleted without the vision side resolving it.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import yaml
from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry import MultiPoint
from shapely.geometry import Point as ShapelyPoint

from arco_agents.clustering import Cluster, dbscan
from arco_agents.geo import buffer_m, haversine_m, to_hazard_geometry, to_shape
from arco_agents.logging import get_logger
from arco_agents.reports import verify_report
from arco_blackboard.service import NO_SCENARIO, BlackboardService
from arco_schemas import AgentEvent, BBox, CommunityReport, Hazard, HazardClass, HazardEvent
from arco_schemas.blackboard import HazardStatus

log = get_logger("agents.consensus")

COMMUNITY_NS = UUID("7d1b7c2e-0d3a-4f55-9c0e-2b9a1f4c6e21")
VISION_SOURCES = ("macro_vision", "micro_vision", "simulated")


# ----------------------------------------------------------------------------- policy


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrustCfg(_Cfg):
    prior: float = Field(ge=0, le=1)
    min_to_count: float = Field(ge=0, le=1)
    corroborated: float
    contradicted: float
    rate_limited: float
    floor: float = Field(ge=0, le=1)
    cap: float = Field(ge=0, le=1)


class RateLimitCfg(_Cfg):
    max_reports: int = Field(ge=1)
    window_s: float = Field(gt=0)


class ClusterCfg(_Cfg):
    eps_m: float = Field(gt=0)
    eps_s: float = Field(gt=0)
    min_reports: int = Field(ge=1)


class QuorumCfg(_Cfg):
    k_independent: int = Field(ge=1)
    min_weighted: float = Field(ge=0)
    vision_negative_radius_m: float = Field(gt=0)
    recently_resolved_s: float = Field(ge=0)


class CommunityHazardCfg(_Cfg):
    pin_radius_m: float = Field(gt=0)
    base_confidence: float = Field(ge=0, le=1)
    per_extra_reporter: float = Field(ge=0)
    max_confidence: float = Field(ge=0, le=1)
    severity: float = Field(ge=0, le=1)
    ttl_s: float = Field(gt=0)


class CorroborationCfg(_Cfg):
    radius_m: float = Field(gt=0)
    confidence_boost: float = Field(ge=0)
    max_confidence: float = Field(ge=0, le=1)


class DenialCfg(_Cfg):
    radius_m: float = Field(gt=0)
    k_independent: int = Field(ge=1)
    min_weighted: float = Field(ge=0)
    confidence_factor: float = Field(gt=0, le=1)
    resolve_community_below: float = Field(ge=0, le=1)


class ConsensusPolicy(_Cfg):
    trust: TrustCfg
    rate_limit: RateLimitCfg
    cluster: ClusterCfg
    quorum: QuorumCfg
    community_hazard: CommunityHazardCfg
    corroboration: CorroborationCfg
    denial: DenialCfg

    @classmethod
    def load(cls, path: Path) -> ConsensusPolicy:
        return cls.model_validate(yaml.safe_load(path.read_text()))


# ----------------------------------------------------------------------------- trust


class TrustLedger:
    """Per-reporter trust in [floor, cap] plus a sliding-window rate limiter. In memory:
    trust is an opinion of this agent instance, persisted per report as
    `community_reports.trust_score` when the agent touches a report."""

    def __init__(self, cfg: TrustCfg, rate: RateLimitCfg) -> None:
        self.cfg = cfg
        self.rate = rate
        self.scores: dict[str, float] = {}
        self.windows: dict[str, deque[datetime]] = defaultdict(deque)

    def get(self, reporter: str) -> float:
        return self.scores.get(reporter, self.cfg.prior)

    def adjust(self, reporter: str, delta: float) -> float:
        new = min(self.cfg.cap, max(self.cfg.floor, self.get(reporter) + delta))
        self.scores[reporter] = new
        return new

    def counts(self, reporter: str) -> bool:
        return self.get(reporter) >= self.cfg.min_to_count

    def allow(self, reporter: str, at: datetime) -> bool:
        """Sliding window: True if this report is within the reporter's budget."""
        w = self.windows[reporter]
        cutoff = at - timedelta(seconds=self.rate.window_s)
        while w and w[0] < cutoff:
            w.popleft()
        if len(w) >= self.rate.max_reports:
            return False
        w.append(at)
        return True


# ----------------------------------------------------------------------------- results


@dataclass
class ConsensusResult:
    kind: str
    reasoning: str
    report_id: UUID | None = None
    hazard_event: HazardEvent | None = None
    hazard_ids: list[UUID] = field(default_factory=list)
    report_ids: list[UUID] = field(default_factory=list)
    data: dict[str, float | int | str | bool | None] = field(default_factory=dict)
    agent_event: AgentEvent | None = None
    compute_ms: float = 0.0


# ----------------------------------------------------------------------------- agent


class Consensus:
    def __init__(self, service: BlackboardService, policy: ConsensusPolicy) -> None:
        self.service = service
        self.policy = policy
        self.trust = TrustLedger(policy.trust, policy.rate_limit)
        # accepted pins not yet consumed by a quorum, per class
        self.pending: dict[HazardClass, list[CommunityReport]] = defaultdict(list)
        # hazard id -> reporters who denied it (hash -> trust at the time)
        self.denials: dict[UUID, dict[str, float]] = defaultdict(dict)
        # hazard id -> reporters who confirmed it (so one device cannot pump confidence)
        self.confirmers: dict[UUID, set[str]] = defaultdict(set)
        # community hazard id -> reporters behind it
        self.community: dict[UUID, set[str]] = {}
        self.seen_reports: set[UUID] = set()

    # ------------------------------------------------------------------ entry points

    def on_report(
        self, report: CommunityReport, *, scenario_t: float = NO_SCENARIO
    ) -> ConsensusResult:
        t0 = time.perf_counter()
        if report.id in self.seen_reports:
            res = ConsensusResult("report.duplicate", "report id already processed", report.id)
        else:
            self.seen_reports.add(report.id)
            res = self._decide(report, scenario_t)
        res.compute_ms = (time.perf_counter() - t0) * 1000.0
        self._record(res, scenario_t)
        return res

    def observe_hazard(
        self, event: HazardEvent, *, scenario_t: float = NO_SCENARIO
    ) -> ConsensusResult | None:
        """A vision hazard appeared: reporters who pinned it earlier were right."""
        h = event.hazard
        if h.source not in VISION_SOURCES or h.status != "active":
            return None
        pins = self.pending.get(h.hazard_class, [])
        if not pins:
            return None
        lon, lat = _centroid(h)
        near = [
            r
            for r in pins
            if haversine_m(r.geom.coordinates[0], r.geom.coordinates[1], lon, lat)
            <= self.policy.corroboration.radius_m
        ]
        if not near:
            return None
        reporters = {r.reporter_hash for r in near}
        for rep in reporters:
            self.trust.adjust(rep, self.policy.trust.corroborated)
        self.pending[h.hazard_class] = [r for r in pins if r not in near]
        for r in near:
            self.service.update_report(r.id, trust_score=self.trust.get(r.reporter_hash))
        res = ConsensusResult(
            "report.corroborated_by_vision",
            f"vision reported {h.hazard_class} ({h.source}) within "
            f"{self.policy.corroboration.radius_m:.0f} m of {len(near)} earlier community pin(s) "
            f"from {len(reporters)} reporter(s); their trust +{self.policy.trust.corroborated}",
            hazard_ids=[h.id],
            report_ids=[r.id for r in near],
            data={"reports": len(near), "reporters": len(reporters)},
        )
        self._record(res, scenario_t)
        return res

    # ------------------------------------------------------------------ decisions

    def _decide(self, report: CommunityReport, scenario_t: float) -> ConsensusResult:
        ok, why = verify_report(report)
        if not ok:
            return ConsensusResult(
                "report.rejected",
                f"signature check failed: {why}",
                report.id,
                report_ids=[report.id],
                data={"reason": "signature"},
            )
        rep = report.reporter_hash
        if not self.trust.allow(rep, report.created_at):
            new = self.trust.adjust(rep, self.policy.trust.rate_limited)
            self.service.update_report(report.id, trust_score=new)
            return ConsensusResult(
                "report.rejected",
                f"rate limit: more than {self.policy.rate_limit.max_reports} reports in "
                f"{self.policy.rate_limit.window_s:.0f} s from {rep[:8]}…; trust -> {new:.2f}",
                report.id,
                report_ids=[report.id],
                data={"reason": "rate_limit", "trust": new},
            )
        self.service.update_report(report.id, trust_score=self.trust.get(rep))
        if report.report_type == "hazard_pin":
            if report.hazard_class is None:
                return ConsensusResult(
                    "report.rejected",
                    "hazard_pin without hazard_class",
                    report.id,
                    report_ids=[report.id],
                    data={"reason": "no_class"},
                )
            return self._on_pin(report, report.hazard_class, scenario_t)
        if report.report_type == "confirm":
            return self._on_confirm(report, scenario_t)
        return self._on_denial(report, scenario_t)

    # ---- pins -------------------------------------------------------------------------

    def _on_pin(
        self, report: CommunityReport, cls: HazardClass, scenario_t: float
    ) -> ConsensusResult:
        lon, lat = report.geom.coordinates[0], report.geom.coordinates[1]
        active_vision = self._hazards_near(
            lon,
            lat,
            radius_m=self.policy.corroboration.radius_m,
            cls=cls,
            status="active",
            sources=VISION_SOURCES,
        )
        if active_vision:
            return self._corroborate(report, active_vision[0], scenario_t)
        self.pending[cls].append(report)
        self._prune_pending(cls, report.created_at)
        clusters = dbscan(
            self.pending[cls],
            eps_m=self.policy.cluster.eps_m,
            eps_s=self.policy.cluster.eps_s,
            min_pts=self.policy.cluster.min_reports,
        )
        mine = next((c for c in clusters if any(r.id == report.id for r in c.reports)), None)
        if mine is None:
            return ConsensusResult(
                "report.pending",
                f"{cls} pin from {report.reporter_hash[:8]}… "
                f"(trust {self.trust.get(report.reporter_hash):.2f}) has no neighbour within "
                f"{self.policy.cluster.eps_m:.0f} m / {self.policy.cluster.eps_s:.0f} s; "
                f"waiting for {self.policy.quorum.k_independent} independent reporters",
                report.id,
                report_ids=[report.id],
                data={"cluster_size": 1},
            )
        trusted = sorted(
            {r.reporter_hash for r in mine.reports if self.trust.counts(r.reporter_hash)}
        )
        weighted = sum(self.trust.get(h) for h in trusted)
        q = self.policy.quorum
        if len(trusted) < q.k_independent or weighted < q.min_weighted:
            return ConsensusResult(
                "report.pending",
                f"{cls} cluster has {len(mine.reports)} pin(s) from {len(trusted)} independent "
                f"trusted reporter(s) (weight {weighted:.2f}); need {q.k_independent} and "
                f"{q.min_weighted:.2f}",
                report.id,
                report_ids=[r.id for r in mine.reports],
                data={
                    "cluster_size": len(mine.reports),
                    "independent": len(trusted),
                    "weight": round(weighted, 3),
                },
            )
        return self._override(
            cluster=mine,
            cls=cls,
            trusted=trusted,
            weighted=weighted,
            scenario_t=scenario_t,
            report=report,
        )

    def _override(
        self,
        *,
        cluster: Cluster,
        cls: HazardClass,
        trusted: list[str],
        weighted: float,
        scenario_t: float,
        report: CommunityReport,
    ) -> ConsensusResult:
        q, ch = self.policy.quorum, self.policy.community_hazard
        lon, lat = cluster.centroid
        # vision-negative check is at the cluster centroid, not just the last pin
        if self._hazards_near(
            lon,
            lat,
            radius_m=q.vision_negative_radius_m,
            cls=cls,
            status="active",
            sources=VISION_SOURCES,
        ):
            return ConsensusResult(
                "report.pending",
                "an active vision hazard of this class appeared at the cluster; "
                "corroborating instead of overriding",
                report.id,
                report_ids=[r.id for r in cluster.reports],
            )
        first = min(r.id for r in cluster.reports)
        hid = uuid5(COMMUNITY_NS, f"community:{cls}:{first}")
        hull = MultiPoint(
            [(r.geom.coordinates[0], r.geom.coordinates[1]) for r in cluster.reports]
        ).convex_hull
        geom = to_hazard_geometry(buffer_m(hull, ch.pin_radius_m))
        extra = max(0, len(trusted) - q.k_independent)
        confidence = min(ch.max_confidence, ch.base_confidence + extra * ch.per_extra_reporter)
        now = datetime.now(UTC)
        existing = self.service.get_hazard(hid)
        hazard = Hazard(
            id=hid,
            geom=geom,
            hazard_class=cls,
            confidence=confidence,
            source="community",
            stream=None,
            camera_id=None,
            first_seen=existing.first_seen if existing else now,
            last_seen=now,
            ttl_s=ch.ttl_s,
            status="active",
            severity=ch.severity,
            velocity_m_s=None,
        )
        ev = self.service.upsert_hazard(hazard, scenario_t=scenario_t)
        self.community[hid] = set(trusted)
        self.pending[cls] = [r for r in self.pending[cls] if r not in cluster.reports]
        for h in trusted:  # independent agreement is evidence for each of them
            self.trust.adjust(h, self.policy.trust.corroborated)
        for r in cluster.reports:
            self.service.update_report(
                r.id, trust_score=self.trust.get(r.reporter_hash), confirms=len(trusted)
            )
        disputed = self._dispute_recently_resolved(lon, lat, cls, scenario_t, hid)
        kind = "community.hazard.created" if existing is None else "community.hazard.updated"
        reasoning = (
            f"{len(trusted)} independent trusted reporters (weight {weighted:.2f} ≥ "
            f"{q.min_weighted}) pinned {cls} within {self.policy.cluster.eps_m:.0f} m / "
            f"{self.policy.cluster.eps_s:.0f} s; no active vision hazard of that class within "
            f"{q.vision_negative_radius_m:.0f} m (vision-negative) → community hazard "
            f"{'created' if existing is None else 'updated'} with confidence {confidence:.2f}, "
            f"ttl {ch.ttl_s:.0f} s"
            + (
                f"; {len(disputed)} recently-resolved vision hazard(s) marked disputed"
                if disputed
                else ""
            )
        )
        return ConsensusResult(
            kind,
            reasoning,
            report.id,
            hazard_event=ev,
            hazard_ids=[hid, *disputed],
            report_ids=[r.id for r in cluster.reports],
            data={
                "independent": len(trusted),
                "weight": round(weighted, 3),
                "confidence": confidence,
                "pins": len(cluster.reports),
                "disputed": len(disputed),
            },
        )

    def _dispute_recently_resolved(
        self, lon: float, lat: float, cls: HazardClass, scenario_t: float, community_id: UUID
    ) -> list[UUID]:
        q = self.policy.quorum
        out: list[UUID] = []
        cutoff = datetime.now(UTC) - timedelta(seconds=q.recently_resolved_s)
        resolved_near = self._hazards_near(
            lon,
            lat,
            radius_m=q.vision_negative_radius_m,
            cls=cls,
            status="resolved",
            sources=VISION_SOURCES,
        )
        for h in resolved_near:
            if h.last_seen.astimezone(UTC) >= cutoff:
                age_s = (datetime.now(UTC) - h.last_seen.astimezone(UTC)).total_seconds()
                self.service.set_hazard_status(h.id, "disputed", scenario_t=scenario_t)
                out.append(h.id)
                self._record(
                    ConsensusResult(
                        "hazard.disputed",
                        f"vision ({h.source}, {h.camera_id}) resolved {cls} here "
                        f"{age_s:.0f} s ago but an independent community quorum reports it; "
                        f"vision hazard marked disputed, community "
                        f"hazard {community_id} is authoritative until vision re-detects",
                        hazard_ids=[h.id, community_id],
                    ),
                    scenario_t,
                )
        return out

    # ---- corroboration ----------------------------------------------------------------

    def _corroborate(
        self, report: CommunityReport, hazard: Hazard, scenario_t: float
    ) -> ConsensusResult:
        rep = report.reporter_hash
        if rep in self.confirmers[hazard.id]:
            return ConsensusResult(
                "report.ignored",
                "this device already confirmed this hazard",
                report.id,
                hazard_ids=[hazard.id],
                report_ids=[report.id],
            )
        self.confirmers[hazard.id].add(rep)
        boost = self.policy.corroboration.confidence_boost if self.trust.counts(rep) else 0.0
        new_conf = min(self.policy.corroboration.max_confidence, hazard.confidence + boost)
        self.trust.adjust(rep, self.policy.trust.corroborated)
        self.service.update_report(report.id, trust_score=self.trust.get(rep), confirms=1)
        ev = None
        if new_conf != hazard.confidence:
            ev = self.service.upsert_hazard(
                hazard.model_copy(update={"confidence": new_conf, "last_seen": datetime.now(UTC)}),
                scenario_t=scenario_t,
            )
        return ConsensusResult(
            "hazard.corroborated",
            f"community {report.report_type} of {hazard.hazard_class} agrees with active "
            f"{hazard.source} hazard "
            f"{hazard.id} ({len(self.confirmers[hazard.id])} independent confirmer(s)); confidence "
            f"{hazard.confidence:.2f} → {new_conf:.2f}, reporter trust "
            f"+{self.policy.trust.corroborated}",
            report.id,
            hazard_event=ev,
            hazard_ids=[hazard.id],
            report_ids=[report.id],
            data={"confidence": new_conf, "confirmers": len(self.confirmers[hazard.id])},
        )

    def _on_confirm(self, report: CommunityReport, scenario_t: float) -> ConsensusResult:
        target = self._target(report, self.policy.denial.radius_m)
        if target is None:
            return ConsensusResult(
                "report.ignored",
                "confirm with no active hazard nearby",
                report.id,
                report_ids=[report.id],
            )
        return self._corroborate(report, target, scenario_t)

    # ---- denials ----------------------------------------------------------------------

    def _on_denial(self, report: CommunityReport, scenario_t: float) -> ConsensusResult:
        d = self.policy.denial
        target = self._target(report, d.radius_m)
        if target is None:
            return ConsensusResult(
                "report.ignored",
                f"{report.report_type} with no active hazard within {d.radius_m:.0f} m",
                report.id,
                report_ids=[report.id],
            )
        rep = report.reporter_hash
        self.denials[target.id][rep] = self.trust.get(rep)
        trusted = {
            h: t for h, t in self.denials[target.id].items() if t >= self.policy.trust.min_to_count
        }
        weighted = sum(trusted.values())
        if len(trusted) < d.k_independent or weighted < d.min_weighted:
            return ConsensusResult(
                "report.pending",
                f"{report.report_type} on {target.source} hazard {target.id}: {len(trusted)} "
                f"independent trusted denier(s) (weight {weighted:.2f}); need {d.k_independent} / "
                f"{d.min_weighted:.2f} — a single "
                f"denial never lowers a hazard",
                report.id,
                hazard_ids=[target.id],
                report_ids=[report.id],
                data={"deniers": len(trusted), "weight": round(weighted, 3)},
            )
        new_conf = round(target.confidence * d.confidence_factor, 4)
        for h in trusted:
            self.trust.adjust(h, self.policy.trust.corroborated)
        self.service.update_report(report.id, trust_score=self.trust.get(rep), denies=len(trusted))
        if target.source == "community" and new_conf < d.resolve_community_below:
            ev = self.service.set_hazard_status(target.id, "resolved", scenario_t=scenario_t)
            for h in self.community.get(target.id, set()):
                self.trust.adjust(h, self.policy.trust.contradicted)
            self.denials.pop(target.id, None)
            return ConsensusResult(
                "hazard.resolved_by_community",
                f"{len(trusted)} independent trusted reporters (weight {weighted:.2f}) deny "
                f"community hazard {target.id}; confidence {target.confidence:.2f} → "
                f"{new_conf:.2f} < {d.resolve_community_below} → "
                f"resolved; original reporters' trust {self.policy.trust.contradicted}",
                report.id,
                hazard_event=ev,
                hazard_ids=[target.id],
                report_ids=[report.id],
                data={
                    "deniers": len(trusted),
                    "weight": round(weighted, 3),
                    "confidence": new_conf,
                },
            )
        updated = target.model_copy(
            update={"confidence": new_conf, "status": "disputed", "last_seen": datetime.now(UTC)}
        )
        ev = self.service.upsert_hazard(updated, scenario_t=scenario_t)
        return ConsensusResult(
            "hazard.disputed",
            f"{len(trusted)} independent trusted reporters (weight {weighted:.2f}) deny "
            f"{target.source} hazard {target.id}; confidence {target.confidence:.2f} → "
            f"{new_conf:.2f}, status disputed. Not deleted: a "
            f"vision hazard is only removed when vision resolves it",
            report.id,
            hazard_event=ev,
            hazard_ids=[target.id],
            report_ids=[report.id],
            data={"deniers": len(trusted), "weight": round(weighted, 3), "confidence": new_conf},
        )

    # ------------------------------------------------------------------ helpers

    def _target(self, report: CommunityReport, radius_m: float) -> Hazard | None:
        if report.target_hazard_id is not None:
            h = self.service.get_hazard(report.target_hazard_id)
            if h is not None and h.status == "active":
                return h
        lon, lat = report.geom.coordinates[0], report.geom.coordinates[1]
        near = self._hazards_near(
            lon, lat, radius_m=radius_m, cls=report.hazard_class, status="active", sources=None
        )
        return near[0] if near else None

    def _hazards_near(
        self,
        lon: float,
        lat: float,
        *,
        radius_m: float,
        cls: HazardClass | None,
        status: HazardStatus,
        sources: tuple[str, ...] | None,
    ) -> list[Hazard]:
        d = radius_m / 111_000.0 * 1.5  # generous bbox, exact distance below
        bbox: BBox = [lon - d, lat - d, lon + d, lat + d]
        out: list[tuple[float, Hazard]] = []
        for h in self.service.list_hazards(bbox=bbox, status=status):
            if cls is not None and h.hazard_class != cls:
                continue
            if sources is not None and h.source not in sources:
                continue
            hl, ht = _centroid(h)
            dist = haversine_m(lon, lat, hl, ht)
            # distance to the footprint, not only its centroid, for large polygons
            dist = min(dist, _edge_distance_m(h, lon, lat))
            if dist <= radius_m:
                out.append((dist, h))
        return [h for _, h in sorted(out, key=lambda x: x[0])]

    def _prune_pending(self, cls: HazardClass, now: datetime) -> None:
        horizon = timedelta(seconds=2 * self.policy.cluster.eps_s)
        self.pending[cls] = [r for r in self.pending[cls] if now - r.created_at <= horizon]

    def _record(self, res: ConsensusResult, scenario_t: float) -> None:
        ev = AgentEvent(
            id=uuid4(),
            agent="consensus",
            kind=res.kind,
            reasoning=res.reasoning,
            scenario_t=scenario_t,
            wall_t=datetime.now(UTC),
            hazard_ids=res.hazard_ids,
            report_ids=res.report_ids,
            data={**res.data, "compute_ms": round(res.compute_ms, 2)},
        )
        self.service.append_agent_event(ev)
        res.agent_event = ev
        log.info(
            "consensus", kind=res.kind, reports=len(res.report_ids), hazards=len(res.hazard_ids)
        )


def _centroid(h: Hazard) -> tuple[float, float]:
    c = to_shape(h.geom).centroid
    return float(c.x), float(c.y)


def _edge_distance_m(h: Hazard, lon: float, lat: float) -> float:
    """Metres from (lon, lat) to the hazard footprint (0 inside)."""
    shape = to_shape(h.geom)
    pt = ShapelyPoint(lon, lat)
    if shape.contains(pt):
        return 0.0
    boundary = shape.boundary
    nearest = boundary.interpolate(boundary.project(pt))
    return haversine_m(lon, lat, float(nearest.x), float(nearest.y))
