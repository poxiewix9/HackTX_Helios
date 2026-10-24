"""LangGraph pipeline: one blackboard event in, agent actions out.

    [route] --report--> [consensus] --hazard written--> [cartographer] --> [navigator] --> END
            --hazard--> [cartographer] --graph changed--> [navigator] --> END
            --graph---> [navigator] --> END
            --other---> END

Shared state carries the event, the scenario clock and each agent's summary. Every node
is wrapped so an exception in one agent is recorded in `errors` and the rest of the
graph still runs (graceful degradation): a Consensus crash never stops the Cartographer.
A hazard the Consensus wrote is applied in the same run; when the bus later delivers that
same `hazard.*` event (same seq) the Cartographer is not run twice.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from arco_agents.cartographer import Cartographer
from arco_agents.consensus import Consensus
from arco_agents.logging import get_logger
from arco_agents.navigator import Navigator
from arco_schemas import BusEnvelope, EdgeEvent, GraphUpdatedEvent, HazardEvent, ReportEvent

log = get_logger("agents.pipeline")


class AgentState(TypedDict, total=False):
    envelope: BusEnvelope
    scenario_t: float
    hazard_event: HazardEvent | None
    graph_event: GraphUpdatedEvent | None
    consensus: dict[str, Any] | None
    cartographer: dict[str, Any] | None
    navigator: dict[str, Any] | None
    errors: list[str]
    handled_graph_versions: list[int]
    handled_hazard_seqs: list[int]


def _guard(
    name: str, fn: Callable[[AgentState], dict[str, Any]]
) -> Callable[[AgentState], dict[str, Any]]:
    def wrapped(state: AgentState) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            out = fn(state)
        except Exception as exc:
            log.error("agent node failed", node=name, error=repr(exc))
            errors = list(state.get("errors", []))
            errors.append(f"{name}: {exc!r}")
            return {"errors": errors, name: None}
        out.setdefault("errors", list(state.get("errors", [])))
        log.debug("agent node ok", node=name, ms=round((time.perf_counter() - t0) * 1000, 1))
        return out

    return wrapped


def build_pipeline(
    cartographer: Cartographer, navigator: Navigator, consensus: Consensus | None = None
) -> Any:
    def route(state: AgentState) -> dict[str, Any]:
        return {"scenario_t": state["envelope"].message.scenario_t, "errors": []}

    def route_next(state: AgentState) -> str:
        msg = state["envelope"].message
        if isinstance(msg, ReportEvent):
            return "consensus" if consensus is not None else str(END)
        if isinstance(msg, HazardEvent):
            if msg.seq in state.get("handled_hazard_seqs", []):
                return str(END)  # already applied in the run that wrote it
            if consensus is not None:
                try:
                    consensus.observe_hazard(msg, scenario_t=msg.scenario_t)
                except Exception as exc:  # consensus must never block the cartographer
                    log.error("consensus observe failed", error=repr(exc))
            return "cartographer"
        if isinstance(msg, GraphUpdatedEvent):
            return "navigator"
        if isinstance(msg, EdgeEvent):
            navigator.on_edge_event(msg)  # keep the mirror warm; no reroute until graph.updated
        return str(END)

    def consensus_node(state: AgentState) -> dict[str, Any]:
        assert consensus is not None
        msg = state["envelope"].message
        assert isinstance(msg, ReportEvent)
        res = consensus.on_report(msg.report, scenario_t=msg.scenario_t)
        handled = list(state.get("handled_hazard_seqs", []))
        if res.hazard_event is not None:
            handled.append(res.hazard_event.seq)
        return {
            "hazard_event": res.hazard_event,
            "handled_hazard_seqs": handled,
            "consensus": {
                "kind": res.kind,
                "hazards": len(res.hazard_ids),
                "reports": len(res.report_ids),
                "compute_ms": round(res.compute_ms, 2),
            },
        }

    def after_consensus(state: AgentState) -> str:
        return "cartographer" if state.get("hazard_event") is not None else str(END)

    def cartographer_node(state: AgentState) -> dict[str, Any]:
        msg: Any = state.get("hazard_event") or state["envelope"].message
        assert isinstance(msg, HazardEvent)
        res = cartographer.on_hazard_event(msg)
        return {
            "graph_event": res.graph_event,
            "cartographer": {
                "kind": res.kind,
                "severed": len(res.severed),
                "penalized": len(res.penalized),
                "restored": len(res.restored),
                "ignored": res.ignored,
                "compute_ms": round(res.compute_ms, 2),
                "graph_version": res.graph_event.graph_version if res.graph_event else None,
            },
        }

    def after_cartographer(state: AgentState) -> str:
        return "navigator" if state.get("graph_event") is not None else str(END)

    def navigator_node(state: AgentState) -> dict[str, Any]:
        ev = state.get("graph_event")
        if ev is None:
            msg = state["envelope"].message
            assert isinstance(msg, GraphUpdatedEvent)
            ev = msg
        handled = list(state.get("handled_graph_versions", []))
        if ev.graph_version in handled:
            return {"navigator": {"skipped": "already rerouted for this graph version"}}
        res = navigator.on_graph_updated(ev)
        handled.append(ev.graph_version)
        if res is None:
            return {
                "navigator": {"skipped": "no population loaded"},
                "handled_graph_versions": handled,
            }
        return {
            "navigator": {
                "routes": len(res.routes),
                "compute_ms": round(res.compute_ms, 2),
                "max_edge_load_ratio": (
                    round(res.assignment.max_edge_load_ratio, 3) if res.assignment else None
                ),
                "graph_version": res.graph_version,
                "unreachable": res.unreachable_cells,
            },
            "handled_graph_versions": handled,
        }

    g: Any = StateGraph(AgentState)
    g.add_node("route", route)
    g.add_node("consensus", _guard("consensus", consensus_node))
    g.add_node("cartographer", _guard("cartographer", cartographer_node))
    g.add_node("navigator", _guard("navigator", navigator_node))
    g.set_entry_point("route")
    g.add_conditional_edges(
        "route",
        route_next,
        {
            "consensus": "consensus",
            "cartographer": "cartographer",
            "navigator": "navigator",
            END: END,
        },
    )
    g.add_conditional_edges(
        "consensus", after_consensus, {"cartographer": "cartographer", END: END}
    )
    g.add_conditional_edges(
        "cartographer", after_cartographer, {"navigator": "navigator", END: END}
    )
    g.add_edge("navigator", END)
    return g.compile()


class Pipeline:
    """Thin wrapper that remembers which graph versions already produced a reroute, so
    the `graph.updated` the Cartographer itself emitted does not trigger a second one."""

    def __init__(
        self, cartographer: Cartographer, navigator: Navigator, consensus: Consensus | None = None
    ) -> None:
        self.cartographer = cartographer
        self.navigator = navigator
        self.consensus = consensus
        self.compiled = build_pipeline(cartographer, navigator, consensus)
        self.handled_graph_versions: list[int] = []
        self.handled_hazard_seqs: list[int] = []
        self.processed = 0
        self.errors: list[str] = []

    def run(self, envelope: BusEnvelope) -> AgentState:
        state: AgentState = {
            "envelope": envelope,
            "handled_graph_versions": self.handled_graph_versions[-50:],
            "handled_hazard_seqs": self.handled_hazard_seqs[-50:],
        }
        out: AgentState = self.compiled.invoke(state)
        self.processed += 1
        self.handled_graph_versions = list(
            out.get("handled_graph_versions", self.handled_graph_versions)
        )
        self.handled_hazard_seqs = list(out.get("handled_hazard_seqs", self.handled_hazard_seqs))
        if out.get("errors"):
            self.errors.extend(out["errors"])
            self.errors = self.errors[-100:]
        return out
