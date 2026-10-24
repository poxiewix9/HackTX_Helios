"""Taxonomy must match PROJECT_SPEC.md section 4.1 exactly."""

from arco_schemas import (
    HAZARD_CLASSES,
    MACRO_CLASSES,
    MICRO_CLASSES,
    HazardClass,
    classes_for_stream,
)

SPEC_MACRO = {
    "fire_front",
    "smoke",
    "flood_water",
    "collapsed_building",
    "tornado_swath",
    "blocked_road",
}
SPEC_MICRO = {
    "debris",
    "flooded_road",
    "fire",
    "smoke",
    "crowd_bottleneck",
    "vehicle_blockage",
    "downed_line",
}


def test_every_enum_member_has_info() -> None:
    assert set(HAZARD_CLASSES) == set(HazardClass)


def test_macro_classes_match_spec() -> None:
    assert {c.value for c in MACRO_CLASSES} == SPEC_MACRO


def test_micro_classes_match_spec() -> None:
    assert {c.value for c in MICRO_CLASSES} == SPEC_MICRO


def test_smoke_is_in_both_streams() -> None:
    assert HAZARD_CLASSES[HazardClass.SMOKE].streams == ["macro", "micro"]


def test_classes_for_stream() -> None:
    assert classes_for_stream("macro") == MACRO_CLASSES
    assert classes_for_stream("micro") == MICRO_CLASSES


def test_expanding_hazards_are_fire_or_water() -> None:
    expanding = {c.value for c, i in HAZARD_CLASSES.items() if i.expands}
    assert expanding == {"fire_front", "smoke", "flood_water", "flooded_road", "fire"}


def test_actions_are_valid_and_crowd_is_capacity() -> None:
    for info in HAZARD_CLASSES.values():
        assert info.default_action in {"sever", "penalize", "capacity"}
    assert HAZARD_CLASSES[HazardClass.CROWD_BOTTLENECK].default_action == "capacity"
