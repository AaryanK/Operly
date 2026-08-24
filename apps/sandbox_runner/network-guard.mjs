import { Sandbox } from "railway";

const GENERATED_UID = 10001;
const TABLE = "operly";
const CHAIN = "output";
let installed = false;

const ADD_BLOCK_RULES = [
  `nft add rule inet ${TABLE} ${CHAIN} meta skuid ${GENERATED_UID} ip daddr != 127.0.0.0/8 reject`,
  `nft add rule inet ${TABLE} ${CHAIN} meta skuid ${GENERATED_UID} ip6 daddr != ::1 reject`,
].join("; ");

const SETUP = [
  `nft delete table inet ${TABLE} 2>/dev/null || true`,
  `nft add table inet ${TABLE}`,
  `nft 'add chain inet ${TABLE} ${CHAIN} { type filter hook output priority 0; policy accept; }'`,
  ADD_BLOCK_RULES,
  `nft list chain inet ${TABLE} ${CHAIN} | grep -q 'skuid ${GENERATED_UID}'`,
].join("; ");

function dependencyCommand(command) {
  return [
    `nft flush chain inet ${TABLE} ${CHAIN}`,
    command,
    "status=$?",
    ADD_BLOCK_RULES,
    `nft list chain inet ${TABLE} ${CHAIN} | grep -q 'skuid ${GENERATED_UID}' || exit 97`,
    "exit $status",
  ].join("; ");
}

function verificationCommand() {
  return [
    `nft list chain inet ${TABLE} ${CHAIN} | grep -q 'skuid ${GENERATED_UID}'`,
    // A direct public-IP TCP attempt avoids DNS ambiguity. Native nftables
    // should reject it immediately for the generated uid.
    `! su -s /bin/bash operly -c "python3 -c 'import socket; s=socket.socket(); s.settimeout(2); s.connect((\"1.1.1.1\",443))'" >/dev/null 2>&1`,
  ].join(" && ");
}

export function installSandboxNetworkGuard() {
  if (installed) return;
  installed = true;

  const originalTemplate = Sandbox.template.bind(Sandbox);
  Sandbox.template = function guardedTemplate() {
    // nftables is injected into the trusted base template. Generated source
    // cannot choose or alter this package set.
    return originalTemplate().withPackages("nftables");
  };

  const originalCreate = Sandbox.create.bind(Sandbox);
  Sandbox.create = async function guardedCreate(...args) {
    const box = await originalCreate(...args);
    try {
      const result = await box.exec(SETUP, { timeoutSec: 30 });
      if (result.exitCode !== 0 || result.timedOut) {
        throw new Error(
          `Unable to install generated-user egress boundary: ${String(result.stderr || result.stdout || "").slice(-1000)}`,
        );
      }
      return box;
    } catch (error) {
      try { await box.destroy(); } catch {}
      throw error;
    }
  };

  const originalExec = Sandbox.prototype.exec;
  Sandbox.prototype.exec = function guardedExec(target, options = {}) {
    if (typeof target !== "string") return originalExec.call(this, target, options);

    // Dependency resolution is the only phase where uid 10001 may reach the
    // Internet. The runner authors these exact install commands, disables npm
    // scripts, and re-installs the nft boundary before returning control.
    if (target.includes("pip install --disable-pip-version-check") || target.includes("npm ci --ignore-scripts")) {
      return originalExec.call(this, dependencyCommand(target), options);
    }

    // server.mjs historically used iptables-owner as the final hardening gate.
    // On Railway Sandbox kernels that extension is unavailable. Convert the
    // gate into an evidence check of the nft boundary installed at VM creation.
    if (target.includes("iptables -C OUTPUT -m owner --uid-owner 10001")) {
      return originalExec.call(this, verificationCommand(), options);
    }

    return originalExec.call(this, target, options);
  };
}

export const sandboxNetworkPolicy = Object.freeze({
  implementation: "nftables_uid_egress_v1",
  generatedUid: GENERATED_UID,
  dependencyException: "runner_authored_install_only",
  runtimeEgress: "loopback_only",
});
