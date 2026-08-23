"""Small durable state store for runner jobs.

The runner is intentionally independent from Operly's business database. A restart
must not turn a running build into an invented success, so in-flight jobs are
reconciled as interrupted and their Docker resources are cleaned before reuse.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class JobRecord:
    id: str
    idempotency_key: str
    state: str
    request_path: str
    response: dict
    resources: dict
    preview_id: str | None
    preview_token: str | None
    preview_upstream: str | None
    cancel_requested: bool


class RunnerStore:
    def __init__(self, root: str | None = None):
        self.root = Path(root or os.getenv("OPERLY_RUNNER_STATE_DIR", "/var/lib/operly-runner"))
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "jobs").mkdir(parents=True, exist_ok=True)
        self.path = self.root / "runner.sqlite3"
        self._lock = threading.RLock()
        self._init()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    request_path TEXT NOT NULL,
                    response_json TEXT NOT NULL DEFAULT '{}',
                    resources_json TEXT NOT NULL DEFAULT '{}',
                    preview_id TEXT,
                    preview_token TEXT,
                    preview_upstream TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_runner_jobs_state ON jobs(state);
                CREATE UNIQUE INDEX IF NOT EXISTS ux_runner_preview_id
                    ON jobs(preview_id) WHERE preview_id IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS ux_runner_preview_token
                    ON jobs(preview_token) WHERE preview_token IS NOT NULL;
                """
            )

    def request_file(self, job_id: str) -> Path:
        return self.root / "jobs" / f"{job_id}.json"

    def create(self, job_id: str, idempotency_key: str, request_payload: dict) -> JobRecord:
        path = self.request_file(job_id)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(request_payload, sort_keys=True, separators=(",", ":")))
        os.replace(temp, path)
        now = self._now()
        with self._lock, self._connect() as db:
            try:
                db.execute(
                    """
                    INSERT INTO jobs(id,idempotency_key,state,request_path,created_at,updated_at)
                    VALUES(?,?,?,?,?,?)
                    """,
                    (job_id, idempotency_key, "queued", str(path), now, now),
                )
            except sqlite3.IntegrityError:
                path.unlink(missing_ok=True)
                existing = self.by_idempotency(idempotency_key)
                if existing is None:
                    raise
                return existing
        return self.get(job_id)

    def _record(self, row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            idempotency_key=row["idempotency_key"],
            state=row["state"],
            request_path=row["request_path"],
            response=json.loads(row["response_json"] or "{}"),
            resources=json.loads(row["resources_json"] or "{}"),
            preview_id=row["preview_id"],
            preview_token=row["preview_token"],
            preview_upstream=row["preview_upstream"],
            cancel_requested=bool(row["cancel_requested"]),
        )

    def get(self, job_id: str) -> JobRecord:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._record(row)

    def by_idempotency(self, key: str) -> JobRecord | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE idempotency_key=?", (key,)
            ).fetchone()
        return self._record(row) if row else None

    def by_preview_id(self, preview_id: str) -> JobRecord | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE preview_id=?", (preview_id,)
            ).fetchone()
        return self._record(row) if row else None

    def by_preview_token(self, token: str) -> JobRecord | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE preview_token=?", (token,)
            ).fetchone()
        return self._record(row) if row else None

    def update(
        self,
        job_id: str,
        *,
        state: str | None = None,
        response: dict | None = None,
        resources: dict | None = None,
        preview_id: str | None = None,
        preview_token: str | None = None,
        preview_upstream: str | None = None,
        cancel_requested: bool | None = None,
    ) -> JobRecord:
        current = self.get(job_id)
        values = {
            "state": state if state is not None else current.state,
            "response_json": json.dumps(response if response is not None else current.response),
            "resources_json": json.dumps(resources if resources is not None else current.resources),
            "preview_id": preview_id if preview_id is not None else current.preview_id,
            "preview_token": preview_token if preview_token is not None else current.preview_token,
            "preview_upstream": preview_upstream if preview_upstream is not None else current.preview_upstream,
            "cancel_requested": int(cancel_requested if cancel_requested is not None else current.cancel_requested),
            "updated_at": self._now(),
        }
        with self._lock, self._connect() as db:
            db.execute(
                """
                UPDATE jobs SET
                    state=:state,
                    response_json=:response_json,
                    resources_json=:resources_json,
                    preview_id=:preview_id,
                    preview_token=:preview_token,
                    preview_upstream=:preview_upstream,
                    cancel_requested=:cancel_requested,
                    updated_at=:updated_at
                WHERE id=:id
                """,
                {**values, "id": job_id},
            )
        return self.get(job_id)

    def request_cancel(self, job_id: str) -> JobRecord:
        return self.update(job_id, cancel_requested=True)

    def in_flight(self) -> list[JobRecord]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE state NOT IN ('preview_ready','failed','cancelled','cleaned')"
            ).fetchall()
        return [self._record(row) for row in rows]

    def remove_request(self, job_id: str) -> None:
        try:
            record = self.get(job_id)
        except KeyError:
            return
        Path(record.request_path).unlink(missing_ok=True)


__all__ = ["JobRecord", "RunnerStore"]
