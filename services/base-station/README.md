# arco-base-station

Receives feature packets from edge nodes, finishes inference, turns masks into
geo-referenced hazard polygons, writes them to the blackboard and streams detections +
per-stage telemetry to the dashboard.

```
WS /ws/features (msgpack) -> decode -> tail ONNX (FP32) -> YOLOv8-seg decode + NMS + mask->polygon
  -> class map -> homography / affine -> Hazard -> POST blackboard /hazards
  -> DetectionsMessage on /ws/detections, TelemetrySample on /ws/telemetry, Prometheus /metrics
```

## Modules

| Module | What it does |
|--------|--------------|
| `detectors.py` | `SplitDetector` (tail of the exported model), `FullDetector` (unsplit FP32, in-process only — the benchmark's pipeline A baseline, not accepted over the wire), `SyntheticDetector` (**SIMULATED**, scripted from `config/synthetic.yaml`, emits `detector="synthetic"`). `select_providers()` prefers TensorRT > CUDA > CPU, override with `ARCO_ORT_PROVIDERS`. |
| `postprocess.py` | YOLOv8-seg output decode (`cx,cy,w,h + nc + 32 mask coeffs`), class-aware NMS, prototype masks → sigmoid → crop → threshold → `findContours` → `approxPolyDP` → Shapely polygon in original-image pixels. |
| `classmap.py` | `config/class_map.yaml`: identity map for fine-tuned hazard models, **COCO proxy map** for pretrained weights (`person×4 → crowd_bottleneck`, `car×2 → vehicle_blockage`, …) with `min_count` aggregation so one pedestrian is never a bottleneck. |
| `georef.py` | `CameraHomography` from a `CameraGeoref` (normalised DLT via SVD in local metres, sign-normalised, horizon rejection when projective depth ≤ 0, reprojection RMS reported), `TileAffine` from a `TileGeoref` (GDAL affine). Math is commented inline. |
| `hazards.py` | `HazardWriter`: stable ids `uuid5(NAMESPACE, "source_id:hazard_class")` so successive frames **update one row** instead of spamming; re-posts only when the footprint changes by `ARCO_HAZARD_CHANGE_FRACTION` or `ARCO_HAZARD_REPOST_S` elapsed; `source` is `simulated` for the synthetic detector, else `micro_vision` / `macro_vision`. |
| `telemetry.py` | `TelemetryHub`: all 9 stages per frame (`network = received − captured − head − serialize`, clock-skew clamped and counted), frame-id gap → drop count, rolling FPS, `psutil` CPU/RSS, Prometheus histograms `arco_stage_ms{stage,stream,pipeline}`, `arco_frame_total_ms`, counters. |
| `api.py` | FastAPI app: `GET /health`, `GET /metrics`, `POST /features` (one packet, returns `DetectionsMessage`), `WS /ws/features` (binary packets in, fire-and-forget; a bad packet is logged and the link stays up), `WS /ws/detections`, `WS /ws/telemetry`. Packets are processed one at a time under a lock in a worker thread (ordered, no ORT contention). |

## Run

```bash
docker compose up -d --wait base-station        # :8002, ARCO_DETECTOR=synthetic by default
make serve-base-station                          # host-side, same env
curl localhost:8002/health
websocat ws://localhost:8002/ws/telemetry
# real model
ARCO_DETECTOR=onnx_split ARCO_MODEL_ID=yolov8n-seg make serve-base-station   # needs models/<id>/manifest.json
```

In compose the `./models` directory is mounted read-only at `/app/models`; set
`ARCO_DETECTOR=onnx_split` in `.env` once `make export-split` has produced a manifest.

## Tests

```bash
make test-vision-e2e     # ARCO_REQUIRE_DB=1: edge -> WS -> base -> blackboard -> PostGIS row, telemetry, metrics
uv run pytest services/base-station/tests -q
```

- `test_georef_postprocess.py`: homography recovery from a pinhole camera (< 5 % range
  error), horizon / degenerate rejection, sample georef consistency, tile corners, seg
  decode + NMS + polygon bounds, class-map aggregation.
- `test_vision_e2e.py`: 12 synthetic frames → 2 hazard rows (`debris`, `smoke`) in PostGIS,
  detections labelled `synthetic`, telemetry with all stages, `hazard.*` events on the
  blackboard WS; plus the HTTP `/features` macro-tile path.
- `test_real_model.py`: runs only when `models/<id>/manifest.json` exists — real INT8 head →
  packet → real tail, FP32 baseline in the same process, and the E2E through the base
  station with measured timings printed (never asserted against a target).

## Measured so far (local CPU, M1 Pro, `CPUExecutionProvider`, yolov8n-seg @ 640)

| stage | ms |
|-------|----|
| head INT8 (edge) | ~6 |
| serialize (uint8 + zstd + msgpack) | ~3, packet ≈ 131 kB (21× smaller than raw BGR) |
| tail FP32 (base) | ~61 |
| full FP32 unsplit (baseline) | ~66–68 |

So on this CPU the split buys bandwidth and edge offload, **not** base-station compute
(tail ≈ full, because the cut after `/model.3` leaves most of the network in the tail).
Phase 5 measures this properly, per hardware, with the benchmark harness.

## Known limitations

- Pretrained COCO weights with a proxy class map are a mechanism test, not a hazard
  detector; fine-tune with `scripts/finetune.py` before reading detections as hazards.
- `config/synthetic.yaml` is a static schedule keyed on `scenario_t` / frame; it is labelled
  simulated everywhere it surfaces (`detector`, `source`, `model_id`).
- Telemetry `pipeline` is `int8_split` for both the real split and the synthetic
  detector (the contract has no `synthetic` kind yet; requested in `HANDOFFS.md`). The
  `detector` field on `DetectionsMessage` and `/health` is the authoritative label.
- `capture_ms` is reported as 0 on the base station (not carried in the header contract).
