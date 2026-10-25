"""Runtime settings for the agents service (env / .env)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PENALTIES = SERVICE_ROOT / "config" / "penalties.yaml"
DEFAULT_ROUTING = SERVICE_ROOT / "config" / "routing.yaml"
DEFAULT_CONSENSUS = SERVICE_ROOT / "config" / "consensus.yaml"


class AgentsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://arco:arco_dev_password@localhost:5432/arco"
    )
    redis_url: str | None = None
    agents_port: int = Field(default=8003, ge=1, le=65535)
    arco_log_level: str = "INFO"
    arco_log_json: bool = True

    # Deterministic core always runs; the SLM only explains. False must be fully supported.
    arco_slm_enabled: bool = False
    # ollama -> OLLAMA_URL /api/generate ; llamacpp -> LLAMACPP_URL /completion (speculative
    # decoding when the server was started with a draft model: `llama-server -m X -md Y`).
    arco_slm_backend: Literal["ollama", "llamacpp"] = "ollama"
    ollama_url: str = "http://localhost:11434"
    llamacpp_url: str = "http://localhost:8080"
    arco_slm_model: str = "qwen2.5:1.5b-instruct"
    arco_slm_timeout_s: float = Field(default=4.0, gt=0)
    arco_slm_max_tokens: int = Field(default=80, ge=16, le=512)
    # llama.cpp per-request cap on drafted tokens per step (ignored without a draft model)
    arco_slm_speculative_n_max: int = Field(default=16, ge=0, le=64)

    arco_penalties_path: Path = DEFAULT_PENALTIES
    arco_routing_path: Path = DEFAULT_ROUTING
    arco_consensus_path: Path = DEFAULT_CONSENSUS
    # Population cells + safe zones the Navigator routes for. JSON file with
    # {"population_cells": [...], "safe_zones": [...]} (arco_schemas.scenario shapes) or a
    # full Scenario file. Empty -> wait for POST /navigator/population.
    arco_population_path: Path | None = None

    # Periodic tick (TTL expiry, buffer growth, periodic reroute) in wall seconds.
    arco_agents_tick_s: float = Field(default=5.0, gt=0)


def get_settings() -> AgentsSettings:
    return AgentsSettings()
