import crypto from "node:crypto";
import { Pool } from "pg";
import { Sandbox } from "railway";

const INFLIGHT_SQL = "'queued','provisioning','building','testing','starting'";
const LEASE_MS = 180_000;
const HEARTBEAT_MS = 30_000;
const INSTANCE_ID = `${process.env.RAILWAY_DEPLOYMENT_ID || process.env.RAILWAY_SERVICE_ID || "runner"}:${crypto.randomUUID()}`;
const ORIGINAL_QUERY = Pool.prototype.query;
let installed = false;
let maintenanceStarted = false;

function leaseExpiry() {
  return new Date(Date.now() + LEASE_MS);
}

function isInitInflightSelect(sql) {
  const normalized = sql.replace(/\s+/g, " ").trim();
  return normalized.startsWith("SELECT id, sandbox_id FROM operly_sandbox_runner_jobs WHERE state IN ('queued','provisioning','building','testing','starting')");
}

function isQueuedInsert(sql) {
  const normalized = sql.replace(/\s+/g, " ").trim();
  return normalized.includes("INSERT INTO operly_sandbox_runner_jobs(id,idempotency_key,state,source_digest,response_json)") &&
    normalized.includes("VALUES($1,$2,'queued',$3,$4::jsonb)");
}

export function installRunnerLeaseGuard() {
  if (installed) return INSTANCE_ID;
  installed = true;

  Pool.prototype.query = async function guardedQuery(text, values, ...rest) {
    const sql = typeof text === "string" ? text : "";

    if (sql.includes("CREATE TABLE IF NOT EXISTS operly_sandbox_runner_jobs")) {
      const result = await ORIGINAL_QUERY.call(this, text, values, ...rest);
      await ORIGINAL_QUERY.call(
        this,
        "ALTER TABLE operly_sandbox_runner_jobs ADD COLUMN IF NOT EXISTS owner_id TEXT",
      );
      await ORIGINAL_QUERY.call(
        this,
        "ALTER TABLE operly_sandbox_runner_jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ",
      );
      await ORIGINAL_QUERY.call(
        this,
        "CREATE INDEX IF NOT EXISTS ix_operly_sandbox_runner_jobs_lease ON operly_sandbox_runner_jobs(lease_expires_at)",
      );
      return result;
    }

    if (isInitInflightSelect(sql)) {
      return ORIGINAL_QUERY.call(
        this,
        `SELECT id, sandbox_id FROM operly_sandbox_runner_jobs
          WHERE state IN (${INFLIGHT_SQL})
            AND (lease_expires_at IS NULL OR lease_expires_at <= NOW())`,
      );
    }

    if (isQueuedInsert(sql)) {
      return ORIGINAL_QUERY.call(
        this,
        `INSERT INTO operly_sandbox_runner_jobs(
           id,idempotency_key,state,source_digest,response_json,owner_id,lease_expires_at
         ) VALUES($1,$2,'queued',$3,$4::jsonb,$5,$6)`,
        [...(values || []), INSTANCE_ID, leaseExpiry()],
      );
    }

    return ORIGINAL_QUERY.call(this, text, values, ...rest);
  };

  return INSTANCE_ID;
}

function failurePayload(jobId) {
  const message = "Runner instance lease expired while the Sandbox job was in flight";
  return {
    jobId,
    state: "failed",
    result: {
      buildSuccess: false,
      testSuccess: false,
      processStartSuccess: false,
      healthCheckSuccess: false,
      acceptanceCheckSuccess: false,
      previewAvailable: false,
      artifacts: [],
      testReport: {},
      staticAnalysisReport: {},
      dependencyReport: {},
      resourceUsage: {},
      failureEvidence: { classification: "runner_lease_expired", message },
    },
    events: [{ state: "failed", message }],
  };
}

export function startRunnerLeaseMaintenance() {
  if (maintenanceStarted) return;
  maintenanceStarted = true;
  const databaseUrl = String(process.env.DATABASE_URL || "");
  const environmentId = String(process.env.RAILWAY_ENVIRONMENT_ID || "");
  if (!databaseUrl || !environmentId) throw new Error("Runner lease maintenance requires DATABASE_URL and RAILWAY_ENVIRONMENT_ID");
  const pool = new Pool({ connectionString: databaseUrl, max: 2 });

  const heartbeat = async () => {
    await ORIGINAL_QUERY.call(
      pool,
      `UPDATE operly_sandbox_runner_jobs
          SET lease_expires_at=$2
        WHERE owner_id=$1
          AND state IN (${INFLIGHT_SQL})`,
      [INSTANCE_ID, leaseExpiry()],
    );
  };

  const reap = async () => {
    const result = await ORIGINAL_QUERY.call(
      pool,
      `SELECT id, sandbox_id FROM operly_sandbox_runner_jobs
        WHERE state IN (${INFLIGHT_SQL})
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at <= NOW()
        LIMIT 20`,
    );
    for (const row of result.rows) {
      const claimed = await ORIGINAL_QUERY.call(
        pool,
        `UPDATE operly_sandbox_runner_jobs
            SET owner_id=$2, lease_expires_at=$3
          WHERE id=$1
            AND state IN (${INFLIGHT_SQL})
            AND lease_expires_at <= NOW()
          RETURNING sandbox_id`,
        [row.id, INSTANCE_ID, leaseExpiry()],
      );
      if (!claimed.rowCount) continue;
      const sandboxId = claimed.rows[0]?.sandbox_id;
      if (sandboxId) {
        try {
          const box = await Sandbox.connect(sandboxId, { environmentId });
          await box.destroy();
        } catch {}
      }
      const response = failurePayload(row.id);
      await ORIGINAL_QUERY.call(
        pool,
        `UPDATE operly_sandbox_runner_jobs
            SET state='failed', response_json=$2::jsonb, owner_id=NULL,
                lease_expires_at=NULL, updated_at=NOW()
          WHERE id=$1 AND owner_id=$3`,
        [row.id, JSON.stringify(response), INSTANCE_ID],
      );
      console.error(`OPERLY_RUNNER_REAPED_EXPIRED_JOB job=${row.id}`);
    }
  };

  heartbeat().catch((error) => console.error("runner lease heartbeat failed", error?.message || error));
  const heartbeatTimer = setInterval(() => {
    heartbeat().catch((error) => console.error("runner lease heartbeat failed", error?.message || error));
  }, HEARTBEAT_MS);
  heartbeatTimer.unref();

  const reapTimer = setInterval(() => {
    reap().catch((error) => console.error("runner lease reaper failed", error?.message || error));
  }, HEARTBEAT_MS);
  reapTimer.unref();
}

export const runnerInstanceId = INSTANCE_ID;
