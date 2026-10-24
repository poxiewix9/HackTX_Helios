"""Re-export shared builders so test modules keep a short import."""

from arco_blackboard.testing import make_hazard, square

__all__ = ["make_hazard", "square"]
