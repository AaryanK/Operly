"""Test suite explicitly opts into the non-production deterministic planner."""
import os

os.environ.setdefault("OPERLY_PLANNING_MODE", "deterministic_test")
