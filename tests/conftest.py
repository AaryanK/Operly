"""Test suite explicitly opts into deterministic non-network runtime backends."""
import os
import sys
from pathlib import Path

# Pytest console entry points can set sys.path[0] to the environment's Scripts
# directory instead of the repository root (notably with some uv/Windows setups).
# Make direct imports such as apps.* and packages.* deterministic for local and CI
# runs without requiring callers to set PYTHONPATH manually.
REPO_ROOT = Path(__file__).resolve().parents[1]
repo_root = str(REPO_ROOT)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

os.environ.setdefault("OPERLY_PLANNING_MODE", "deterministic_test")
os.environ.setdefault("OPERLY_ENV", "test")
os.environ.setdefault("OPERLY_SEMANTIC_EMBEDDING_BACKEND", "hashing")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-that-is-long-enough")
os.environ.setdefault("AUTH_TOKEN_PEPPER", "test-token-pepper-that-is-long-enough")
