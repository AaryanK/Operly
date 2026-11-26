# Staged identity authority handoff (review only)

DragonZpyder owns human identity and credentials. Operly retains the existing
AppUser ID as a workspace/membership projection. Never map by email.

Deploy the reviewed gate on every source API replica before creating any
mapping. Keep global OPERLY_IDENTITY_OWNER unchanged during the pilot and
enable OPERLY_IDENTITY_HANDOFF_ENABLED only after verifying the mapping table
and all credential writers. With the staged gate enabled, pending, active and
disabled mappings deny local credential/session issuance for that subject.
Unmapped accounts continue using the existing flow. Local password recovery
and Google/Discord linking must not bypass the fence. Existing tenant
membership checks remain unchanged.

For an individually selected pilot:
1. Verify source and target identity details with the existing reconciliation
   process, backups and an agreed rollback procedure. Stop on drift.
2. Verify that all source credential writers honor the gate; quiesce any
   other writers (including admin/background jobs) for the pilot. The gate is
   application-level, not a database-enforced prohibition on every writer.
3. Run identity_handoff_prepare_manual.sql for that exact immutable UUID.
   It locks the source user, creates a pending mapping and revokes local
   sessions and unused challenges. It does not grant federated access.
   Expect temporary sign-in interruption for this pilot only.
4. Reconcile the now-fenced source again. In DragonZpyder collect fresh
   credential proof and individually approve activation. For a verified-email
   pending user without a password, explicitly authorize native recovery
   only after the source handoff is verified.
5. Run identity_handoff_activate_manual.sql only after verifying the exact
   subject is active and can sign in to the trusted issuer. Enable only
   reviewed SSO configuration and verify real workspace permissions.
6. Test a second account and check both directions of cross-account denial.

Rollback is a coordinated operation: disable federation for the subject and
revoke its relying-party sessions, fence or disable the destination identity,
then explicitly review restoration of source authority. A disabled mapping
does NOT silently restore local login. Do not delete mappings or globally
disable the staged gate as a shortcut. Source hashes are retained for a
reviewed rollback only; no source credential deletion is part of this PR.

The mapping is not a session provenance record: already-issued relying-party
sessions need explicit revocation upon mapping disable or destination account
revocation. Continuous revocation propagation, new native-user projection,
active-account recovery, and true PostgreSQL concurrent handoff testing
remain release gates. Do not enable global identity retirement until these
and browser acceptance are complete. No production configuration is changed
by this branch.
