"""Edge node settings (env / .env)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DetectorSetting = Literal["onnx_split", "onnx_full", "synthetic"]


class EdgeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    arco_detector: DetectorSetting = "synthetic"
    arco_models_dir: Path = Path("./models")
    arco_model_id: str = Field(
        default="yolov8n-seg", description="Sub-folder of ARCO_MODELS_DIR holding manifest.json"
    )
    arco_head_quantized: bool = Field(default=True, description="Use head_int8 when present")

    arco_edge_stream: Literal["macro", "micro"] = "micro"
    arco_edge_source: str = Field(
        default="synthetic",
        description="`synthetic[:seed]`, image folder, video file, device index or RTSP URL",
    )
    arco_edge_georef_path: Path = Field(
        default=Path("data/samples/vision/georef/camera_front_st.json"),
        description="CameraGeoref or TileGeoref JSON for this source",
    )
    arco_edge_width: int = Field(default=640, gt=0, description="Synthetic frame size")
    arco_edge_height: int = Field(default=480, gt=0)
    arco_edge_fps: float = Field(default=10.0, gt=0)
    arco_edge_realtime: bool = Field(default=True, description="Pace frames at ARCO_EDGE_FPS")
    arco_edge_loop: bool = True
    arco_edge_frames: int | None = Field(default=None, description="Stop after N frames")
    arco_edge_queue: int = Field(default=4, ge=1)

    arco_base_station_url: str = "ws://localhost:8002/ws/features"
    arco_allow_remote_base: bool = False

    arco_log_level: str = "INFO"
    arco_log_json: bool = True


def get_settings() -> EdgeSettings:
    return EdgeSettings()
