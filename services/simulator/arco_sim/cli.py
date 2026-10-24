"""`arco-sim` command line.

arco-sim validate [--dir data/scenarios]
    schema + semantic checks (what `make validate-scenarios` runs)
arco-sim play --scenario lahaina_2023 [--speed 20] [--until 900] [--check]
    headless playback against the blackboard (:8001) and agents (:8003)
arco-sim render-feed --scenario lahaina_2023 --camera cam-front-st --out clip.mp4
    [--t0 0 --duration 60]  write a SIMULATED street clip
arco-sim serve
    HTTP API on SIMULATOR_PORT
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

from arco_sim.clients import AgentsClient, BlackboardClient
from arco_sim.feeds import FeedSpec, write_clip
from arco_sim.logging import configure_logging
from arco_sim.player import Player
from arco_sim.scenario_io import check_all, load_scenario
from arco_sim.settings import get_settings


def cmd_validate(args: argparse.Namespace) -> int:
    settings = get_settings()
    directory = Path(args.dir) if args.dir else settings.arco_scenarios_dir
    checks = check_all(directory, data_dir=settings.arco_data_dir)
    if not checks:
        print(f"no scenarios in {directory}")
        return 1
    bad = 0
    for c in checks:
        status = "OK " if c.ok else "ERR"
        name = c.scenario.id if c.scenario else c.path.name
        extra = ""
        if c.scenario:
            sc = c.scenario
            extra = (
                f"  {sc.event:9s} {sc.event_date}  {sc.duration_s:6.0f}s  "
                f"{len(sc.timeline):3d} timeline  {len(sc.community_events):3d} community  "
                f"{len(sc.micro_feeds)} cams  sources={len(sc.data_sources)}"
            )
        print(f"{status} {name}{extra}")
        for e in c.errors:
            print(f"      error: {e}")
        for w in c.warnings:
            print(f"      warn:  {w}")
        bad += 0 if c.ok else 1
    print(f"{len(checks) - bad}/{len(checks)} scenarios valid")
    return 1 if bad else 0


def cmd_play(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging("simulator")
    sc = load_scenario(settings.arco_scenarios_dir / f"{args.scenario}.json")
    bb = BlackboardClient(settings.arco_blackboard_url, timeout_s=settings.arco_sim_http_timeout_s)
    agents = AgentsClient(settings.arco_agents_url, timeout_s=settings.arco_sim_http_timeout_s)
    player = Player(bb, agents)
    player.load(sc)
    player.set_speed(args.speed)
    until = sc.duration_s if args.until is None else min(args.until, sc.duration_s)
    player.play()
    stop = threading.Event()
    th = threading.Thread(target=player.run, args=(stop,), daemon=True)
    th.start()
    try:
        while player.playing and player.t < until:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        player.pause()
        stop.set()
        th.join(timeout=2)
    print(json.dumps(player.summary(), indent=2))
    if args.check:
        chk = player.check_expected()
        print(json.dumps(chk.model_dump(), indent=2))
        return 0 if chk.ok else 2
    return 0


def cmd_render_feed(args: argparse.Namespace) -> int:
    settings = get_settings()
    sc = load_scenario(settings.arco_scenarios_dir / f"{args.scenario}.json")
    feed = next((f for f in sc.micro_feeds if f.camera_id == args.camera), None)
    if feed is None:
        print(
            f"camera {args.camera!r} not in scenario {sc.id}; cameras: "
            f"{[f.camera_id for f in sc.micro_feeds]}"
        )
        return 1
    n = write_clip(
        feed,
        Path(args.out),
        t_start=args.t0,
        duration_s=args.duration,
        spec=FeedSpec(fps=args.fps),
    )
    overlays = ", ".join(f"{o.hazard_class}@{o.t}s" for o in feed.synthetic_overlays)
    print(f"wrote {n} frames to {args.out} (SIMULATED; overlays: {overlays or 'none'})")
    return 0


def cmd_serve(_: argparse.Namespace) -> int:
    from arco_sim.api import main as serve_main  # noqa: PLC0415 - avoid importing uvicorn for validate

    serve_main()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="arco-sim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--dir")
    v.set_defaults(fn=cmd_validate)
    p = sub.add_parser("play")
    p.add_argument("--scenario", required=True)
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--until", type=float, default=None, help="stop at this scenario second")
    p.add_argument("--check", action="store_true", help="exit 2 if expected_blocked_edges unmet")
    p.set_defaults(fn=cmd_play)
    r = sub.add_parser("render-feed")
    r.add_argument("--scenario", required=True)
    r.add_argument("--camera", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--t0", type=float, default=0.0)
    r.add_argument("--duration", type=float, default=30.0)
    r.add_argument("--fps", type=float, default=10.0)
    r.set_defaults(fn=cmd_render_feed)
    s = sub.add_parser("serve")
    s.set_defaults(fn=cmd_serve)
    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
