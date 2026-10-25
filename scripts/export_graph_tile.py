"""Export a scenario's road graph as a compact JSON tile for the mobile on-device router.

    uv run python scripts/export_graph_tile.py --scenario lahaina_2023 \
        --out apps/mobile/assets/tiles/lahaina_2023.json

The tile carries nodes (lon/lat), directed edges with geometry + length + highway class,
the scenario's safe zones (polygon centroid + capacity), its population cells (start
points) and the data-source / licence note from the OSM sample descriptor. The phone's A*
(`apps/mobile/src/core/router.ts`) runs on this file when the agents are unreachable; it
is a *fallback*, not the Navigator.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import osmnx as ox
from shapely.geometry import LineString, shape

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="lahaina_2023")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    scenario_path = REPO / "data" / "scenarios" / f"{args.scenario}.json"
    scenario = json.loads(scenario_path.read_text())
    if scenario["road_graph_source"] != "graphml_file":
        raise SystemExit(f"{args.scenario}: only graphml_file scenarios can be exported offline")
    graph_ref = scenario["road_graph_ref"]
    descriptor_path = REPO / Path(graph_ref).with_suffix(".json")
    descriptor: dict[str, Any] = (
        json.loads(descriptor_path.read_text()) if descriptor_path.exists() else {}
    )
    graph = ox.load_graphml(REPO / graph_ref)

    nodes: dict[str, list[float]] = {}
    for nid, data in graph.nodes(data=True):
        nodes[str(nid)] = [round(float(data["x"]), 6), round(float(data["y"]), 6)]

    edges: list[dict[str, Any]] = []
    next_id = 1
    for u, v, _key, data in graph.edges(keys=True, data=True):
        length_m = float(data.get("length") or 0.0)
        if length_m <= 0.0:
            continue
        geom = data.get("geometry")
        if geom is None:
            nu, nv = graph.nodes[u], graph.nodes[v]
            geom = LineString([(nu["x"], nu["y"]), (nv["x"], nv["y"])])
        highway = data.get("highway")
        if isinstance(highway, list):
            highway = highway[0] if highway else None
        name = data.get("name")
        if isinstance(name, list):
            name = name[0] if name else None
        edges.append(
            {
                "id": next_id,
                "u": str(u),
                "v": str(v),
                "length_m": round(length_m, 1),
                "highway": str(highway) if highway is not None else None,
                "name": str(name) if name is not None else None,
                "geom": [[round(x, 6), round(y, 6)] for x, y in geom.coords],
            }
        )
        next_id += 1

    safe_zones = []
    for sz in scenario.get("safe_zones", []):
        c = shape(sz["geom"]).centroid
        safe_zones.append(
            {
                "id": sz["id"],
                "name": sz.get("name", sz["id"]),
                "center": [round(c.x, 6), round(c.y, 6)],
                "capacity": sz.get("capacity"),
            }
        )

    cells = [
        {
            "id": c["id"],
            "center": [round(float(x), 6) for x in c["geom"]["coordinates"][:2]],
            "population": c.get("population"),
        }
        for c in scenario.get("population_cells", [])
    ]

    tile = {
        "format": "arco.tile/1",
        "scenario_id": scenario["id"],
        "label": scenario.get("label", "Simulated on real event geometry"),
        "bbox": scenario["bbox"],
        "source": descriptor.get(
            "source", {"name": "OpenStreetMap contributors", "license": "ODbL 1.0"}
        ),
        "note": "Present-day OSM drive network exported for the on-device fallback router; "
        "roads may differ from the event date. Not the Navigator's output.",
        "nodes": nodes,
        "edges": edges,
        "safe_zones": safe_zones,
        "population_cells": cells,
    }
    default_out = REPO / "apps" / "mobile" / "assets" / "tiles" / f"{scenario['id']}.json"
    out = Path(args.out) if args.out else default_out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(tile, separators=(",", ":")) + "\n")
    size_kib = out.stat().st_size / 1024
    print(
        f"{out}: {len(nodes)} nodes, {len(edges)} edges, {len(safe_zones)} safe zones, "
        f"{size_kib:.0f} KiB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
