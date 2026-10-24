"""OSM road-graph ingestion into `road_edges` via OSMnx.

Sources (mirror `arco_schemas.scenario.RoadGraphSource`):
  osmnx_bbox   "west,south,east,north"      -> Overpass download
  osmnx_place  "Lahaina, Hawaii, USA"       -> Overpass download
  graphml_file path/to/graph.graphml        -> offline (what tests and `make ingest-sample` use)

Each directed edge of the OSMnx MultiDiGraph becomes one row; `base_cost` and
`capacity_estimate` come from `config/ingest.yaml` keyed by the OSM `highway` tag.
A `graph.updated(full_rebuild=True)` event is published after the reload.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import networkx as nx
import osmnx as ox
import yaml
from shapely.geometry import LineString as ShapelyLine
from shapely.geometry import mapping
from sqlalchemy import Engine

from arco_blackboard import repo
from arco_blackboard.bus import Publisher, make_publisher
from arco_blackboard.db import get_engine, session_scope
from arco_blackboard.logging import configure_logging, get_logger
from arco_schemas import GraphUpdatedEvent, LineString, RoadEdge

log = get_logger("blackboard.ingest")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "ingest.yaml"
RoadGraphSource = Literal["osmnx_bbox", "osmnx_place", "graphml_file"]

# Overpass responses are cached under data/cache (git-ignored) instead of ./cache.
_REPO_ROOT = Path(__file__).resolve().parents[3]
ox.settings.cache_folder = str(_REPO_ROOT / "data" / "cache" / "osmnx")


@dataclass(frozen=True)
class IngestConfig:
    speed_m_s: dict[str, float]
    capacity_people_per_min: dict[str, float]
    default_network_type: str = "drive"

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> IngestConfig:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            speed_m_s={k: float(v) for k, v in raw["speed_m_s"].items()},
            capacity_people_per_min={
                k: float(v) for k, v in raw["capacity_people_per_min"].items()
            },
            default_network_type=str(raw.get("default_network_type", "drive")),
        )

    def speed_for(self, highway: str | None) -> float:
        return self.speed_m_s.get(highway or "default", self.speed_m_s["default"])

    def capacity_for(self, highway: str | None) -> float:
        return self.capacity_people_per_min.get(
            highway or "default", self.capacity_people_per_min["default"]
        )


@dataclass(frozen=True)
class IngestResult:
    source: RoadGraphSource
    ref: str
    nodes: int
    edges: int
    bbox: list[float]
    graph_version: int


# ---------------------------------------------------------------------------
# graph loading
# ---------------------------------------------------------------------------


def parse_bbox_wsen(value: str) -> tuple[float, float, float, float]:
    parts = [float(p) for p in value.split(",")]
    if len(parts) != 4:
        msg = "bbox must be 'west,south,east,north'"
        raise ValueError(msg)
    west, south, east, north = parts
    if west >= east or south >= north:
        msg = "bbox must satisfy west < east and south < north"
        raise ValueError(msg)
    return west, south, east, north


def load_graph(
    source: RoadGraphSource, ref: str, *, network_type: str = "drive"
) -> nx.MultiDiGraph:
    if source == "graphml_file":
        return ox.load_graphml(ref)
    if source == "osmnx_bbox":
        west, south, east, north = parse_bbox_wsen(ref)
        return ox.graph_from_bbox(bbox=(west, south, east, north), network_type=network_type)
    return ox.graph_from_place(ref, network_type=network_type)


def _first(value: Any) -> Any:
    """OSMnx stores multi-valued tags as lists; take the first for a scalar column."""
    if isinstance(value, list | tuple):
        return value[0] if value else None
    return value


def graph_to_edges(graph: nx.MultiDiGraph, config: IngestConfig) -> list[RoadEdge]:
    """Every directed (u, v, key) edge -> RoadEdge with derived cost and capacity."""
    edges: list[RoadEdge] = []
    next_id = 1
    for u, v, _key, data in graph.edges(keys=True, data=True):
        geom = data.get("geometry")
        if geom is None:
            nu, nv = graph.nodes[u], graph.nodes[v]
            geom = ShapelyLine([(nu["x"], nu["y"]), (nv["x"], nv["y"])])
        length_m = float(data.get("length") or 0.0)
        if length_m <= 0.0:
            continue  # degenerate zero-length edge; cannot be routed over
        highway = _first(data.get("highway"))
        highway_s = str(highway) if highway is not None else None
        osmid = _first(data.get("osmid"))
        name = _first(data.get("name"))
        speed = config.speed_for(highway_s)
        # base_cost in seconds at evacuation speed for this road class
        base_cost = length_m / speed
        edges.append(
            RoadEdge(
                id=next_id,
                geom=LineString.model_validate(mapping(geom)),
                osm_way_id=int(osmid) if osmid is not None else None,
                u=int(u),
                v=int(v),
                length_m=length_m,
                base_cost=base_cost,
                current_cost=base_cost,
                status="open",
                capacity_estimate=config.capacity_for(highway_s),
                current_load=0.0,
                highway=highway_s[:32] if highway_s else None,
                name=str(name)[:256] if name is not None else None,
            )
        )
        next_id += 1
    return edges


def graph_bbox(graph: nx.MultiDiGraph) -> list[float]:
    xs = [float(d["x"]) for _, d in graph.nodes(data=True)]
    ys = [float(d["y"]) for _, d in graph.nodes(data=True)]
    return [min(xs), min(ys), max(xs), max(ys)]


# ---------------------------------------------------------------------------
# ingestion
# ---------------------------------------------------------------------------


def ingest_graph(
    graph: nx.MultiDiGraph,
    *,
    engine: Engine,
    publisher: Publisher,
    config: IngestConfig | None = None,
    source: RoadGraphSource = "graphml_file",
    ref: str = "",
) -> IngestResult:
    config = config or IngestConfig.load()
    edges = graph_to_edges(graph, config)
    with session_scope(engine) as s:
        count = repo.replace_edges(s, edges)
        version = repo.graph_version(s)
        publisher.publish(
            s,
            GraphUpdatedEvent(
                type="graph.updated",
                scenario_t=-1.0,
                wall_t=datetime.now(UTC),
                seq=0,
                graph_version=version,
                changed_edge_ids=[],
                severed_count=0,
                penalized_count=0,
                full_rebuild=True,
            ),
        )
    result = IngestResult(
        source=source,
        ref=ref,
        nodes=graph.number_of_nodes(),
        edges=count,
        bbox=graph_bbox(graph),
        graph_version=version,
    )
    log.info("road graph ingested", **result.__dict__)
    return result


def ingest(
    source: RoadGraphSource,
    ref: str,
    *,
    engine: Engine | None = None,
    publisher: Publisher | None = None,
    network_type: str | None = None,
    save_graphml: Path | None = None,
) -> IngestResult:
    config = IngestConfig.load()
    graph = load_graph(source, ref, network_type=network_type or config.default_network_type)
    if save_graphml is not None:
        save_graphml.parent.mkdir(parents=True, exist_ok=True)
        ox.save_graphml(graph, save_graphml)
        log.info("graphml saved", path=str(save_graphml))
    return ingest_graph(
        graph,
        engine=engine or get_engine(),
        publisher=publisher or make_publisher(publisher_name="ingest"),
        config=config,
        source=source,
        ref=ref,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--bbox", help="west,south,east,north (Overpass download)")
    src.add_argument("--place", help='e.g. "Lahaina, Hawaii, USA" (Overpass download)')
    src.add_argument("--graphml", help="path to a saved .graphml (offline)")
    parser.add_argument(
        "--network-type", default=None, help="osmnx network_type (default from config)"
    )
    parser.add_argument(
        "--save-graphml", type=Path, default=None, help="also save the downloaded graph"
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)

    configure_logging("blackboard")
    if args.graphml:
        source, ref = "graphml_file", args.graphml
    elif args.bbox:
        source, ref = "osmnx_bbox", args.bbox
    else:
        source, ref = "osmnx_place", args.place
    result = ingest(source, ref, network_type=args.network_type, save_graphml=args.save_graphml)  # type: ignore[arg-type]
    if args.json:
        print(json.dumps(result.__dict__))
    else:
        print(
            f"ingested {result.edges} edges / {result.nodes} nodes; "
            f"graph_version={result.graph_version}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
