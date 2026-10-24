"""Thin HTTP clients. The simulator is a *client* of the blackboard: every hazard,
report, clock tick and network change goes through `POST :8001/...` so the agents see
exactly what they would see from real inference. Both clients accept any `httpx.Client`
(a FastAPI `TestClient` in tests)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import httpx

from arco_schemas import (
    ClockEvent,
    CommunityReport,
    Hazard,
    HazardEvent,
    HazardStatus,
    NetworkEvent,
    ReportEvent,
    RoadEdge,
    Route,
)
from arco_schemas.scenario import RoadGraphSource


def _json(model: Any) -> Any:
    return json.loads(model.model_dump_json())


class BlackboardClient:
    def __init__(
        self, base_url: str, *, timeout_s: float = 120.0, http: httpx.Client | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.Client(base_url=self.base_url, timeout=timeout_s)

    def close(self) -> None:
        self.http.close()

    def health(self) -> dict[str, Any]:
        r = self.http.get("/health")
        r.raise_for_status()
        out: dict[str, Any] = r.json()
        return out

    def ingest_graph(
        self, source: RoadGraphSource, ref: str, *, network_type: str | None = None
    ) -> dict[str, Any]:
        r = self.http.post(
            "/graph/ingest", json={"source": source, "ref": ref, "network_type": network_type}
        )
        r.raise_for_status()
        out: dict[str, Any] = r.json()
        return out

    def graph_version(self) -> int:
        r = self.http.get("/graph/version")
        r.raise_for_status()
        return int(r.json()["graph_version"])

    def upsert_hazard(self, hazard: Hazard, *, scenario_t: float) -> HazardEvent:
        r = self.http.post("/hazards", params={"scenario_t": scenario_t}, json=_json(hazard))
        r.raise_for_status()
        return HazardEvent.model_validate(r.json())

    def set_hazard_status(
        self, hazard_id: UUID, status: HazardStatus, *, scenario_t: float
    ) -> HazardEvent | None:
        r = self.http.post(
            f"/hazards/{hazard_id}/status", params={"status": status, "scenario_t": scenario_t}
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return HazardEvent.model_validate(r.json())

    def list_hazards(self, *, status: HazardStatus | None = None) -> list[Hazard]:
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = status
        r = self.http.get("/hazards", params=params)
        r.raise_for_status()
        return [Hazard.model_validate(h) for h in r.json()]

    def post_report(self, report: CommunityReport, *, scenario_t: float) -> ReportEvent:
        r = self.http.post("/reports", params={"scenario_t": scenario_t}, json=_json(report))
        r.raise_for_status()
        return ReportEvent.model_validate(r.json())

    def post_clock(
        self,
        *,
        scenario_id: str | None,
        scenario_t: float,
        playing: bool,
        speed: float,
        duration_s: float | None,
    ) -> ClockEvent:
        r = self.http.post(
            "/clock",
            json={
                "scenario_id": scenario_id,
                "scenario_t": scenario_t,
                "playing": playing,
                "speed": speed,
                "duration_s": duration_s,
            },
        )
        r.raise_for_status()
        return ClockEvent.model_validate(r.json())

    def post_network(
        self, *, cellular_up: bool, reason: str | None, scenario_t: float
    ) -> NetworkEvent:
        r = self.http.post(
            "/network",
            params={"scenario_t": scenario_t},
            json={"cellular_up": cellular_up, "reason": reason},
        )
        r.raise_for_status()
        return NetworkEvent.model_validate(r.json())

    def list_edges(self, *, status: str | None = None) -> list[RoadEdge]:
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = status
        r = self.http.get("/edges", params=params)
        r.raise_for_status()
        return [RoadEdge.model_validate(e) for e in r.json()]

    def list_routes(self) -> list[Route]:
        r = self.http.get("/routes")
        r.raise_for_status()
        return [Route.model_validate(x) for x in r.json()]


class AgentsClient:
    """Optional: tells the agents service which population to route. When the agents
    service is unreachable the player still runs (hazards still flow via the bus)."""

    def __init__(
        self, base_url: str, *, timeout_s: float = 120.0, http: httpx.Client | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.Client(base_url=self.base_url, timeout=timeout_s)

    def close(self) -> None:
        self.http.close()

    def set_population(self, population: dict[str, Any]) -> dict[str, Any] | None:
        try:
            r = self.http.post("/navigator/population", json=population)
            r.raise_for_status()
        except httpx.HTTPError:
            return None
        out: dict[str, Any] = r.json()
        return out

    def health(self) -> dict[str, Any] | None:
        try:
            r = self.http.get("/health")
            r.raise_for_status()
        except httpx.HTTPError:
            return None
        out: dict[str, Any] = r.json()
        return out
