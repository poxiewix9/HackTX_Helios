"""`arco-bench run | show | replay | serve`."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from arco_benchmark.bench import BenchConfig, run_benchmark
from arco_benchmark.report import format_table, load_run, write_run
from arco_benchmark.serve import SERVICE_ROOT, BenchmarkSettings


def _cmd_run(args: argparse.Namespace) -> int:
    cfg = BenchConfig(
        models_dir=Path(args.models_dir),
        model_id=args.model_id,
        frames=args.frames,
        warmup=args.warmup,
        pace_fps=None if args.pace_fps == 0 else args.pace_fps,
        streams=tuple(args.streams),
        source=args.source,
        providers=args.providers.split(",") if args.providers else None,
        target_ms=args.target_ms,
        target_fps=args.target_fps,
    )
    try:
        run = run_benchmark(cfg, log=lambda s: print(s, file=sys.stderr))
    except FileNotFoundError as exc:
        print(f"benchmark: {exc}", file=sys.stderr)
        return 2
    out_dir = Path(args.out) / run.run_id
    paths = write_run(run, out_dir)
    print(format_table(run))
    print(f"\nwrote {paths['run']}, {paths['summary']}, {paths['samples']}")
    if args.record:
        rec_dir = Path(args.recorded_dir)
        rec_dir.mkdir(parents=True, exist_ok=True)
        dst = rec_dir / f"{run.run_id}.json"
        shutil.copyfile(paths["run"], dst)
        print(f"recorded -> {dst}")
    missed = [r for r in run.results if not r.meets_target]
    if missed and args.strict:
        return 3
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    run = load_run(Path(args.file))
    print(format_table(run))
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    from arco_benchmark.replay import iter_replay  # noqa: PLC0415 - keep CLI import light

    run = load_run(Path(args.file))
    for n, msg in enumerate(iter_replay(run, speed=args.speed, loop=False), start=1):
        print(msg.model_dump_json())
        if args.limit and n >= args.limit:
            break
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn  # noqa: PLC0415 - server-only dependency path

    from arco_benchmark.serve import create_app  # noqa: PLC0415

    settings = BenchmarkSettings()
    if args.port:
        settings = settings.model_copy(update={"benchmark_port": args.port})
    uvicorn.run(create_app(settings), host=args.host, port=settings.benchmark_port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arco-bench", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="measure fp32_full vs int8_split on identical frames")
    r.add_argument("--models-dir", default="models")
    r.add_argument("--model-id", default="yolov8n-seg")
    r.add_argument("--frames", type=int, default=60)
    r.add_argument("--warmup", type=int, default=3)
    r.add_argument(
        "--pace-fps", type=float, default=30.0, help="input frame rate; 0 = unpaced throughput"
    )
    r.add_argument("--streams", nargs="+", default=["micro", "macro"], choices=["micro", "macro"])
    r.add_argument(
        "--source",
        default="synthetic:0",
        help="synthetic[:seed] (SIMULATED frames), an image directory, or a video file",
    )
    r.add_argument("--providers", default=None, help="comma list of ORT providers to force")
    r.add_argument("--target-ms", type=float, default=80.0)
    r.add_argument("--target-fps", type=float, default=30.0)
    r.add_argument("--out", default=str(SERVICE_ROOT / "results"))
    r.add_argument("--record", action="store_true", help="also store under recorded/")
    r.add_argument("--recorded-dir", default=str(SERVICE_ROOT / "recorded"))
    r.add_argument("--strict", action="store_true", help="exit 3 if any pipeline misses target")
    r.set_defaults(fn=_cmd_run)

    s = sub.add_parser("show", help="print a stored run")
    s.add_argument("file")
    s.set_defaults(fn=_cmd_show)

    rp = sub.add_parser("replay", help="print recorded TelemetryMessages (mode=recorded)")
    rp.add_argument("file")
    rp.add_argument("--speed", type=float, default=1000.0)
    rp.add_argument("--limit", type=int, default=0)
    rp.set_defaults(fn=_cmd_replay)

    sv = sub.add_parser("serve", help="telemetry WS for the dashboard (replay + live runs)")
    sv.add_argument("--host", default="0.0.0.0")
    sv.add_argument("--port", type=int, default=0)
    sv.set_defaults(fn=_cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fn = args.fn
    result: int = fn(args)
    return result


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
