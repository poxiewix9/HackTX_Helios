"""Model classes -> hazard classes (config/class_map.yaml), with per-frame aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from shapely.geometry import Polygon
from shapely.ops import unary_union

from arco_base.postprocess import Instance
from arco_schemas import HazardClass
from arco_schemas.hazard_classes import StreamKind


@dataclass(frozen=True)
class Rule:
    to: HazardClass
    min_count: int = 1
    min_conf: float | None = None


@dataclass
class ClassMap:
    mapping: str  # "hazards" | "coco" | custom key
    rules: dict[StreamKind, dict[str, Rule]]

    @classmethod
    def load(cls, path: Path, mapping: str) -> ClassMap:
        raw = yaml.safe_load(path.read_text())
        if mapping not in raw:
            raise KeyError(f"class map {mapping!r} not in {path}")
        rules: dict[StreamKind, dict[str, Rule]] = {"macro": {}, "micro": {}}
        for stream in ("macro", "micro"):
            for name, spec in (raw[mapping].get(stream) or {}).items():
                rules[stream][name] = Rule(
                    to=HazardClass(spec["to"]),
                    min_count=int(spec.get("min_count", 1)),
                    min_conf=spec.get("min_conf"),
                )
        return cls(mapping, rules)

    @classmethod
    def for_model(cls, path: Path, class_names: list[str]) -> ClassMap:
        """`hazards` if the model's class names are hazard classes, else `coco`."""
        taxonomy = {c.value for c in HazardClass}
        key = "hazards" if class_names and all(n in taxonomy for n in class_names) else "coco"
        return cls.load(path, key)


@dataclass
class Grouped:
    hazard_class: HazardClass
    confidence: float
    bbox_px: list[float]
    polygon_px: list[list[float]]
    count: int


def apply(
    cmap: ClassMap,
    stream: StreamKind,
    class_names: list[str],
    instances: list[Instance],
    *,
    default_min_conf: float,
) -> list[Grouped]:
    """Map + aggregate: all instances of one source class that pass `min_conf` are unioned
    when there are at least `min_count` of them (one footprint per class per frame)."""
    rules = cmap.rules.get(stream, {})
    buckets: dict[str, list[Instance]] = {}
    for inst in instances:
        name = class_names[inst.class_index] if inst.class_index < len(class_names) else None
        if name is None or name not in rules:
            continue
        rule = rules[name]
        if inst.confidence < (rule.min_conf if rule.min_conf is not None else default_min_conf):
            continue
        buckets.setdefault(name, []).append(inst)
    out: list[Grouped] = []
    for name, insts in buckets.items():
        rule = rules[name]
        if len(insts) < rule.min_count:
            continue
        polys = [Polygon(i.polygon_px) for i in insts if i.polygon_px and len(i.polygon_px) >= 4]
        polys = [p if p.is_valid else p.buffer(0) for p in polys]
        union: Any = (
            unary_union(polys).convex_hull if len(polys) > 1 else (polys[0] if polys else None)
        )
        if union is None or union.is_empty:
            continue
        ring = [[float(x), float(y)] for x, y in union.exterior.coords]
        xs, ys = np.asarray(ring)[:, 0], np.asarray(ring)[:, 1]
        out.append(
            Grouped(
                hazard_class=rule.to,
                confidence=float(np.mean([i.confidence for i in insts])),
                bbox_px=[float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())],
                polygon_px=ring,
                count=len(insts),
            )
        )
    return out
