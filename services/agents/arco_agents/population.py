"""Where evacuees start and where they may go. Loaded from JSON (never hardcoded).

Accepts either a full `Scenario` file or a minimal document:
  {"population_cells": [PopulationCell...], "safe_zones": [SafeZoneSpec...], "notes": "..."}
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from arco_schemas import SafeZone
from arco_schemas.scenario import PopulationCell, SafeZoneSpec, Scenario


class Population(BaseModel):
    model_config = ConfigDict(extra="forbid")

    population_cells: list[PopulationCell] = Field(min_length=1)
    safe_zones: list[SafeZoneSpec] = Field(min_length=1)
    label: str | None = None
    notes: str | None = None

    @classmethod
    def load(cls, path: Path) -> Population:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if "timeline" in raw:  # full scenario
            sc = Scenario.model_validate(raw)
            return cls(
                population_cells=sc.population_cells,
                safe_zones=sc.safe_zones,
                label=sc.label,
                notes=sc.notes,
            )
        return cls.model_validate(raw)

    def total_population(self) -> int:
        return sum(c.population for c in self.population_cells)

    def blackboard_zones(self) -> list[SafeZone]:
        return [
            SafeZone(id=z.id, name=z.name, geom=z.geom, capacity=z.capacity)
            for z in self.safe_zones
        ]
