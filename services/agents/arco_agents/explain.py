"""Route explanations: deterministic templates always; a local SLM optionally.

The SLM never chooses routes. It only rewrites the deterministic facts into friendlier
text, and any failure (disabled, unreachable, timeout, bad output) falls back to the
template. Results are cached by route hash so an unchanged route is never re-explained.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from arco_agents.geo import bearing_deg, compass
from arco_agents.logging import get_logger
from arco_schemas import RoadEdge

log = get_logger("agents.explain")

ExplanationSource = Literal["slm", "deterministic"]


@dataclass(frozen=True)
class RouteFacts:
    """Everything an explanation may mention. Built from the computed route only."""

    cell_id: str
    zone_id: str
    zone_name: str
    group_size: int
    length_m: float
    cost_s: float
    edges: tuple[RoadEdge, ...]
    avoided_severed: int
    avoided_penalized: int
    load_ratio: float
    split_fraction: float

    def route_hash(self) -> str:
        payload = {
            "cell": self.cell_id,
            "zone": self.zone_id,
            "edges": [e.id for e in self.edges],
            "status": [e.status for e in self.edges],
            "severed": self.avoided_severed,
            "frac": round(self.split_fraction, 2),
        }
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Explanation:
    instruction: str
    rationale: str
    source: ExplanationSource


class Explainer(Protocol):
    def explain(self, facts: RouteFacts) -> Explanation: ...


def _road_legs(edges: tuple[RoadEdge, ...]) -> list[tuple[str, float, float]]:
    """Collapse consecutive edges with the same name into (name, metres, bearing)."""
    legs: list[tuple[str, float, float]] = []
    for e in edges:
        name = e.name or (e.highway or "unnamed road").replace("_", " ")
        c0, c1 = e.geom.coordinates[0], e.geom.coordinates[-1]
        b = bearing_deg(c0[0], c0[1], c1[0], c1[1])
        if legs and legs[-1][0] == name:
            prev = legs[-1]
            legs[-1] = (name, prev[1] + e.length_m, prev[2])
        else:
            legs.append((name, e.length_m, b))
    return legs


def _fmt_m(m: float) -> str:
    return f"{m / 1000:.1f} km" if m >= 950 else f"{int(round(m / 10.0) * 10)} m"


class DeterministicExplainer:
    """Template text from route facts. Always available; the fallback for the SLM."""

    def explain(self, facts: RouteFacts) -> Explanation:
        legs = _road_legs(facts.edges)
        if not legs:
            return Explanation(
                f"You are already at {facts.zone_name}.",
                "Origin is inside the safe zone.",
                "deterministic",
            )
        first = legs[0]
        parts = [f"Head {compass(first[2])} on {first[0]} for {_fmt_m(first[1])}"]
        for name, metres, _b in legs[1:3]:
            parts.append(f"continue onto {name} for {_fmt_m(metres)}")
        if len(legs) > 3:
            rest = len(legs) - 3
            parts.append(f"follow signs for {rest} more turn{'s' if rest != 1 else ''}")
        minutes = max(1, round(facts.cost_s / 60))
        instruction = (
            f"{'; '.join(parts)}; arrive at {facts.zone_name} ({_fmt_m(facts.length_m)}, "
            f"~{minutes} min)."
        )
        reasons: list[str] = []
        if facts.avoided_severed:
            reasons.append(f"avoids {facts.avoided_severed} closed road segment(s)")
        if facts.avoided_penalized:
            reasons.append(f"routes around {facts.avoided_penalized} degraded segment(s)")
        if facts.split_fraction < 0.999:
            reasons.append(
                f"carries {facts.split_fraction:.0%} of this area's people to spread crowds"
            )
        reasons.append(f"peak road load {facts.load_ratio:.0%} of capacity")
        return Explanation(instruction, "; ".join(reasons) + ".", "deterministic")


class SlmExplainer:
    """Ollama-backed rewrite of the deterministic explanation. Any problem -> fallback.

    Phase 3 ships the interface and a working minimal client; speculative decoding and
    tuning are Phase 10. With `ARCO_SLM_ENABLED=false` this class is never constructed."""

    def __init__(self, url: str, model: str, timeout_s: float) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.fallback = DeterministicExplainer()
        self._client = httpx.Client(timeout=timeout_s)

    def explain(self, facts: RouteFacts) -> Explanation:
        base = self.fallback.explain(facts)
        prompt = (
            "You write one-sentence evacuation instructions for the public. Rewrite the "
            "following route instruction so it is calm, concrete and under 40 words. Keep "
            "every road name and distance exactly. Do not add roads or advice.\n\n"
            f"Instruction: {base.instruction}\nWhy: {base.rationale}\n\nRewritten:"
        )
        try:
            r = self._client.post(
                f"{self.url}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False},
            )
            r.raise_for_status()
            text = str(r.json().get("response", "")).strip().split("\n")[0]
        except Exception as exc:
            log.warning("slm unavailable; deterministic fallback", error=str(exc))
            return base
        if not text or any(leg[0] not in text for leg in _road_legs(facts.edges)[:1]):
            return base  # the model dropped the first road name: untrusted, fall back
        return Explanation(text, base.rationale, "slm")


class CachedExplainer:
    def __init__(self, inner: Explainer) -> None:
        self.inner = inner
        self.cache: dict[str, Explanation] = {}
        self.hits = 0

    def explain(self, facts: RouteFacts) -> Explanation:
        key = facts.route_hash()
        if key in self.cache:
            self.hits += 1
            return self.cache[key]
        out = self.inner.explain(facts)
        self.cache[key] = out
        return out
