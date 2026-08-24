"""Test suite explicitly opts into deterministic non-network runtime backends."""
import os

os.environ.setdefault("OPERLY_PLANNING_MODE", "deterministic_test")
os.environ.setdefault("OPERLY_ENV", "test")
os.environ.setdefault("OPERLY_SEMANTIC_EMBEDDING_BACKEND", "hashing")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-that-is-long-enough")
os.environ.setdefault("AUTH_TOKEN_PEPPER", "test-token-pepper-that-is-long-enough")
