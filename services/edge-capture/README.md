# arco-edge-capture

The edge side of the split-inference pipeline. One process per camera / tile source:

```
frame source -> letterbox (640, deterministic) -> ONNX *head* (INT8) -> uint8 quantize
             -> zstd -> msgpack {h: FeaturePacketHeader, p: bytes} -> WebSocket -> base station
```

Raw frames never leave this process. The only outbound bytes are the cut tensor; the
`FeaturePacketHeader` carries no pixels; the only image metadata is the source size inside
`georef` (the letterbox is a deterministic function of it, so the base station can undo it).

## Modules

| Module | What it does |
|--------|--------------|
| `onnx_tools.py` | Model-agnostic split: `single_cut_points()` enumerates tensors where the graph is a single edge **after the last use of the image input** (privacy guard: the tail can never need the frame); `split_model()` cuts with `onnx.utils.Extractor`; `quantize_static_int8()` = ORT static QDQ (MinMax, per-channel int8 weights, uint8 activations); `compare_split()` measures full-vs-split error; `SplitManifest` records cut, shapes, hashes and the *measured* tolerance. |
| `packet.py` | Transport codec. Asymmetric uint8 quantisation of the cut tensor (error ≤ scale/2, tested), zstd level 3, msgpack. `encode()` / `decode()` both return measured ms. |
| `preprocess.py` | `Letterbox.for_size(w, h, imgsz)` — same math on both ends, no extra header fields. |
| `sources.py` | `CaptureSource` (OpenCV device / file / RTSP), `ImageDirSource`, `SyntheticSource` (**SIMULATED**, seeded, labelled). |
| `head.py` | `OnnxHead` (prefers `head_int8.onnx`), `SyntheticHead` (1-element tensor, for `ARCO_DETECTOR=synthetic`). |
| `client.py` | `WebSocketLink` with bounded drop-oldest queue and reconnect back-off; `assert_local_destination()` refuses anything that is not loopback / RFC1918 / link-local / `.local` / a dot-less compose hostname unless `ARCO_ALLOW_REMOTE_BASE=1`. |
| `main.py` | `EdgeNode` wiring + `arco-edge-run` CLI. |

## Run

```bash
# synthetic source + synthetic head against the compose base station (default settings)
make run-edge FRAMES=50
# real model: export first, then
make export-split WEIGHTS=models/yolov8n-seg.pt          # writes models/yolov8n-seg/*
ARCO_DETECTOR=onnx_split ARCO_EDGE_SOURCE=0 make run-edge    # webcam 0
# in compose (profile)
docker compose --profile vision up -d edge-capture
```

Settings are `ARCO_EDGE_*`, `ARCO_DETECTOR`, `ARCO_MODELS_DIR`, `ARCO_MODEL_ID`,
`ARCO_BASE_STATION_URL` (see `.env.example` and `arco_edge/settings.py`). Camera geometry
comes from a `CameraGeoref` / `TileGeoref` JSON (`ARCO_EDGE_GEOREF_PATH`); the sample is
`data/samples/vision/georef/camera_front_st.json`.

## Split export

```bash
uv sync --package arco-edge-capture --extra export   # torch + ultralytics, only for export
uv run python scripts/export_split.py --weights models/yolov8n-seg.pt --list-cuts
uv run python scripts/export_split.py --weights models/yolov8n-seg.pt --split-index 10 \
    --calib-dir data/calib/   # optional: real frames; otherwise seeded noise (labelled in manifest)
```

Outputs `full_fp32.onnx`, `head_fp32.onnx`, `head_int8.onnx`, `tail_fp32.onnx`,
`manifest.json`. The script measures the FP32 split error (must be ≤ 1e-4) and the INT8
head cosine similarity against FP32 (must be ≥ `--tolerance-cosine`, default 0.99) and
exits non-zero if either is exceeded. `scripts/finetune.py` wraps ultralytics training with
the ARCO hazard taxonomy and validates class names against the contract.

## Tests

```bash
make test-split          # cut analysis, privacy guard, FP32/INT8 split tolerance, codec, no-raw-frame
uv run pytest services/edge-capture/tests -q
```

`test_packet_and_privacy.py::test_no_raw_frame_leaves_the_edge` runs the full edge node with a
`RecordingLink` and asserts that no outbound frame contains any row of any captured image
(and that every frame decodes to a tensor of the cut shape, not an image).

## Known limitations

- INT8 calibration defaults to seeded noise when no `--calib-dir` is given; the manifest
  says so (`"calibration": "synthetic_noise"`). Calibrate on real frames before trusting
  accuracy numbers.
- The `network` stage on the base station is `received − captured − (head + serialize)` and
  relies on both clocks being in sync; skew is clamped and counted (`skew_clamped`).
- Capture latency is measured but not transmitted (not part of the header contract).
