"""Measure the route-explanation SLM against a *real* local runtime.

    uv run python scripts/slm_bench.py --backend ollama --model qwen2.5:1.5b-instruct
    uv run python scripts/slm_bench.py --backend llamacpp --url http://localhost:8080

Prints per-call latency, tokens/s, whether the output passed the fact guard, and — for
llama.cpp started with a draft model (`llama-server -m target.gguf -md draft.gguf`) — the
real draft acceptance rate. To compare speculative vs plain decoding on llama.cpp, run
the server twice (with / without `-md`) and run this script against each.

No server reachable -> exit code 2 and no numbers. Nothing here is estimated.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import networkx as nx  # osmnx-free: the GraphML carries the attributes we need

from arco_agents.explain import RouteFacts, SlmExplainer, SlmTiming
from arco_agents.population import Population
from arco_schemas import RoadEdge

ROOT = Path(__file__).resolve().parents[1]


def sample_facts(n: int) -> list[RouteFacts]:
    """Route facts over the committed Lahaina sample graph: n distinct 2-4 edge legs."""
    g = nx.read_graphml(ROOT / "data/samples/osm/lahaina_drive.graphml")
    pop = Population.load(ROOT / "data/samples/lahaina/demo_population.json")
    zone = pop.safe_zones[0]
    edges: list[RoadEdge] = []
    for i, (u, v, data) in enumerate(g.edges(data=True)):
        if "geometry" in data or data.get("name"):
            coords = [
                [float(g.nodes[u]["x"]), float(g.nodes[u]["y"])],
                [float(g.nodes[v]["x"]), float(g.nodes[v]["y"])],
            ]
            name = data.get("name")
            edges.append(
                RoadEdge.model_validate(
                    {
                        "id": i,
                        "geom": {"type": "LineString", "coordinates": coords},
                        "u": int(float(u)),
                        "v": int(float(v)),
                        "length_m": float(data.get("length", 100.0)),
                        "base_cost": 10.0,
                        "current_cost": 10.0,
                        "status": "open",
                        "capacity_estimate": 100.0,
                        "name": name if isinstance(name, str) else None,
                        "highway": str(data.get("highway", "residential")),
                    }
                )
            )
        if len(edges) >= n * 4:
            break
    out: list[RouteFacts] = []
    for k in range(n):
        legs = tuple(edges[k * 4 : k * 4 + 2 + (k % 3)])
        out.append(
            RouteFacts(
                cell_id=f"cell-{k}",
                zone_id=zone.id,
                zone_name=zone.name,
                group_size=50 + k,
                length_m=sum(e.length_m for e in legs),
                cost_s=sum(e.length_m for e in legs) / 1.2,
                edges=legs,
                avoided_severed=k % 2,
                avoided_penalized=k % 3,
                load_ratio=0.3 + 0.05 * k,
                split_fraction=1.0 if k % 2 else 0.6,
            )
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--backend", choices=["ollama", "llamacpp"], default="ollama")
    ap.add_argument("--url", default=None)
    ap.add_argument("--model", default="qwen2.5:1.5b-instruct")
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--speculative-n-max", type=int, default=16)
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    a = ap.parse_args()
    url = a.url or (
        "http://localhost:8080" if a.backend == "llamacpp" else "http://localhost:11434"
    )

    ex = SlmExplainer(
        url,
        a.model,
        a.timeout,
        backend=a.backend,
        speculative_n_max=a.speculative_n_max,
    )
    facts = sample_facts(a.n)
    timings: list[SlmTiming] = []
    rows: list[dict[str, object]] = []
    for f in facts:
        e = ex.explain(f)
        rows.append(
            {
                "source": e.source,
                "latency_ms": round(e.timing.latency_ms, 1) if e.timing else None,
                "tokens_per_s": round(e.timing.tokens_per_s, 1)
                if e.timing and e.timing.tokens_per_s
                else None,
                "speculative": e.timing.speculative if e.timing else None,
                "draft_acceptance": e.timing.draft_acceptance if e.timing else None,
                "text": e.instruction,
            }
        )
        if e.timing:
            timings.append(e.timing)
    snap = ex.stats.snapshot()
    if snap["calls"] == snap["errors"]:
        print(
            f"no usable answer from {a.backend} at {url} ({snap['last_error']}); "
            "start the runtime first — nothing measured",
            file=sys.stderr,
        )
        return 2
    summary = {
        "backend": a.backend,
        "url": url,
        "model": a.model,
        "calls": snap["calls"],
        "accepted": snap["accepted"],
        "rejected": snap["rejected"],
        "errors": snap["errors"],
        "rejections": ex.stats.rejections,
        "latency_ms_median": round(statistics.median(t.latency_ms for t in timings), 1)
        if timings
        else None,
        "latency_ms_p90": round(
            sorted(t.latency_ms for t in timings)[int(0.9 * (len(timings) - 1))], 1
        )
        if timings
        else None,
        "tokens_per_s_median": round(
            statistics.median(t.tokens_per_s for t in timings if t.tokens_per_s), 1
        )
        if any(t.tokens_per_s for t in timings)
        else None,
        "speculative": snap["speculative"],
        "draft_acceptance": snap["draft_acceptance"],
    }
    if a.json:
        print(json.dumps({"summary": summary, "rows": rows}, indent=2))
    else:
        for r in rows:
            print(
                f"[{r['source']:13}] {r['latency_ms']!s:>8} ms  {r['tokens_per_s']!s:>6} tok/s  "
                f"spec={r['speculative']}  acc={r['draft_acceptance']}  {r['text']}"
            )
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
