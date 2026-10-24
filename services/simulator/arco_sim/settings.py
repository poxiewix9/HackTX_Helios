"""Simulator settings (env / .env). No coordinates live here: scenarios do."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCENARIOS_DIR = REPO_ROOT / "data" / "scenarios"


class SimulatorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    simulator_port: int = Field(default=8004, ge=1, le=65535)
    arco_blackboard_url: str = "http://localhost:8001"
    arco_agents_url: str = "http://localhost:8003"
    arco_scenarios_dir: Path = DEFAULT_SCENARIOS_DIR
    # Paths inside scenario files are relative to data/ (see arco_schemas.scenario).
    arco_data_dir: Path = REPO_ROOT / "data"
    arco_default_scenario: str = "lahaina_2023"
    # Load the default scenario at startup; autoplay it at this speed (0 = load only).
    arco_sim_autoload: bool = True
    arco_sim_autoplay_speed: float = Field(default=0.0, ge=0)
    arco_sim_clock_hz: float = Field(default=2.0, gt=0)
    arco_sim_http_timeout_s: float = Field(default=120.0, gt=0)
    arco_log_level: str = "INFO"
    arco_log_json: bool = True


def get_settings() -> SimulatorSettings:
    return SimulatorSettings()
