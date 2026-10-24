"""Base-station settings (env / .env)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CLASS_MAP = SERVICE_ROOT / "config" / "class_map.yaml"
DEFAULT_SYNTHETIC = SERVICE_ROOT / "config" / "synthetic.yaml"


class BaseStationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    arco_detector: Literal["onnx_split", "onnx_full", "synthetic"] = "synthetic"
    arco_models_dir: Path = Path("./models")
    arco_model_id: str = "yolov8n-seg"
    arco_ort_providers: str | None = Field(
        default=None, description="Comma list to force ORT providers; default TensorRT>CUDA>CPU"
    )
    arco_class_map_path: Path = DEFAULT_CLASS_MAP
    arco_synthetic_schedule_path: Path = DEFAULT_SYNTHETIC
    arco_conf_threshold: float = Field(default=0.3, ge=0, le=1)
    arco_iou_threshold: float = Field(default=0.5, ge=0, le=1)

    blackboard_url: str = Field(
        default="http://localhost:8001", description="Where hazards are written (REST)"
    )
    arco_hazard_ttl_s: float = Field(default=120.0, gt=0)
    arco_hazard_default_severity: float = Field(default=0.7, ge=0, le=1)
    arco_hazard_repost_s: float = Field(
        default=2.0, gt=0, description="Re-post an unchanged hazard at most this often"
    )
    arco_hazard_change_fraction: float = Field(
        default=0.05, ge=0, description="Area change that counts as 'changed' (fraction)"
    )
    arco_hazard_min_confidence: float = Field(default=0.3, ge=0, le=1)

    base_station_port: int = Field(default=8002, ge=1, le=65535)
    arco_telemetry_window: int = Field(default=30, ge=2, description="Rolling FPS window")
    arco_log_level: str = "INFO"
    arco_log_json: bool = True


def get_settings() -> BaseStationSettings:
    return BaseStationSettings()
