"""Run real Operly business provider tests in a credential-free staging cron.

No real database, customer account, HTTP broker, LLM or network connection.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone


def main():
    environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "")
    if (environment != "staging"
            or os.getenv("OPERLY_REAL_KERNEL_CANARY_ENABLED") != "1"
            or os.getenv("DATABASE_URL")
            or os.getenv("OPERLY_DRAGONZPYDER_DELEGATION_KEY")
            or os.getenv("OPERLY_DRAGONZPYDER_BROKER_TOKEN")):
        print(json.dumps({"event": "operly_real_business_kernel_canary",
                          "status": "blocked", "error": "staging_or_credentials"}), flush=True)
        raise SystemExit(3)
    root = Path(__file__).resolve().parents[3]
    args = [sys.executable, "-m", "unittest", "discover", "-s", "tests",
            "-p", "test_real_operly_kernel_business_canary.py", "-v"]
    try:
        result = subprocess.run(
            args, cwd=root, capture_output=True, text=True, timeout=90,
            env={"PATH": os.getenv("PATH", ""),
                 "HOME": os.getenv("HOME", "/tmp"),
                 "PYTHONPATH": str(root), "OPERLY_ENV": "development",
                 "DATABASE_URL": "sqlite+aiosqlite:///:memory:"},
        )
        ok = result.returncode == 0
    except subprocess.TimeoutExpired:
        ok = False
    print(json.dumps({
        "event": "operly_real_business_kernel_canary",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if ok else "failed",
        "actual_provider_code": True,
        "database": "disposable_sqlite",
        "customer_data_used": False,
        "mutations_executed": 0,
        "paid_inference": False,
    }, sort_keys=True), flush=True)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
