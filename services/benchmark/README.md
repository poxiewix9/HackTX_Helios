# arco-benchmark

Measures pipeline **A** (`fp32_full`: ship the frame as JPEG, run the unsplit FP32 model)
against pipeline **B** (`int8_split`: INT8 head → feature packet → FP32 tail) on
**identical frames** in one process, and writes `BenchmarkResult` / `TelemetrySample` rows
(shared schemas) as JSON + CSV. A recorded run replays over WebSocket labelled `recorded`.

```bash
make bench                       # 60 frames, paced at 30 fps input, both streams -> results/<run_id>/
make bench FRAMES=200 PACE_FPS=0 # unpaced = pure throughput
make bench RECORD=1              # also copy to recorded/ (committed evidence)
make bench-show                  # table for the newest recorded run
make test-benchmark
uv run arco-bench serve          # :8005  /health /runs /results /ws/telemetry  POST /run  POST /replay/{id}
docker compose up -d benchmark   # same, replaying the newest recorded run
```

## What is measured, and how

| field | source |
|-------|--------|
| `stage_ms` (all nine contract stages) | `perf_counter` around each stage; stages a pipeline does not have are `0.0` so rows line up |
| `total_ms` | wall time from "frame taken" to "detections georeferenced" |
| `fps` | processed frames / measured wall time (after warm-up) |
| `dropped_frames` | **paced mode only** — input frames arrive at `t0 + i/pace_fps`; the pipeline has no input queue, so every arrival instant that passes while it is busy is a drop (an edge node that always grabs the newest frame) |
| `bytes_sent` | A: JPEG q90 bytes of the frame; B: the feature packet. `network` is 0 for both (same process) — this is a compute + codec comparison |
| `cpu_percent`, `rss_mb`, `peak_rss_mb` | `psutil` on this process |
| `hardware` | `HardwareInfo` read from the OS: hostname, platform, CPU brand string (`sysctl` / `/proc/cpuinfo`), core count, RAM, GPU only if an ORT GPU provider is active, ORT providers actually used, Python |
| `meets_target` | `mean_ms ≤ target_ms` **and** `fps ≥ target_fps`; when missed, `bottleneck_stage` names the slowest mean stage |

Frames default to the seeded synthetic source (`synthetic:0`, **SIMULATED**, identical for
both pipelines); `--source <dir|video>` benchmarks real footage. Detections on synthetic
frames are meaningless and are not reported as accuracy.

## Recorded run on this development machine

`recorded/20261024T212059Z-b8ae37.json` — Apple M1 Pro (8 cores, 16 GB), `CPUExecutionProvider`,
yolov8n-seg @ 640, cut after `/model.3`, 60 frames per cell, 30 fps paced input:

| pipeline | stream | mean ms | p95 ms | fps | dropped | kB/frame | verdict |
|----------|--------|--------:|-------:|----:|--------:|---------:|---------|
| fp32_full | micro | 69.2 | 78.1 | 9.9 | 120 | 108.0 | MISSED (tail) |
| int8_split | micro | 68.3 | 83.1 | 10.0 | 119 | 127.6 | MISSED (tail) |
| fp32_full | macro | 69.6 | 80.4 | 10.1 | 116 | 123.1 | MISSED (tail) |
| int8_split | macro | 68.7 | 75.8 | 10.0 | 118 | 209.7 | MISSED (tail) |

Stage means (micro): A `serialize 2.2 / deserialize 2.3 / tail 60.9`; B `head 4.5 /
serialize 2.3 / deserialize 0.7 / tail 56.8`. Unpaced throughput: ~13–14 fps for both.

**Honest reading.** On this CPU the split is ~1 % faster per frame, both pipelines miss
the 80 ms / 30 fps target, and the bottleneck is the FP32 tail in both cases. The feature
packet (128–210 kB) is also *larger* than a JPEG of the same frame (108–123 kB) at this
early cut, because a 64×80×80 activation carries more entropy than a q90 JPEG. What the
split buys here is privacy (no reconstructable pixels leave the edge) and the option to
move compute to the edge by cutting deeper — not speed, not bandwidth. Expected to change
with a GPU/TensorRT tail (the tail is 90 % of the frame) and a deeper cut; the harness
reports whatever it finds. The 10 fps under 30 fps pacing is the no-queue arrival model:
a 68 ms frame spans three 33 ms slots, so two of three inputs are dropped.

## Recorded replay

`arco-bench serve` (or the `benchmark` compose service) streams the newest `recorded/*.json`
on `/ws/telemetry`: first one `telemetry.benchmark` per result, then `telemetry.sample`
frames paced by the original gaps, all with `mode="recorded"` and the original hardware
block. `POST /run` starts a **live** run (requires `models/<id>/manifest.json`, otherwise
`409`), whose samples stream as `mode="live"` and replace the replay until it ends. The
run file on disk keeps `mode="live"` — it is the record of how the numbers were measured;
the re-labelling happens on the way out.

## Tests

- `test_runner_report.py` — fake clock: unpaced run (no drops, exact FPS), paced run
  (70 ms work @ 30 fps → exactly 2 drops per frame, 10 fps), fast pipeline sleeps and
  drops nothing, `summarize` names the bottleneck only when the target is missed,
  `HardwareInfo` comes from the machine, JSON/CSV round-trip, replay relabels to
  `recorded` without touching the file.
- `test_serve.py` — server replays `recorded`, refuses a live run without models (409).
- `test_real_bench.py` — only with model files: a short real run; asserts mechanics,
  prints numbers, never asserts a performance target.
