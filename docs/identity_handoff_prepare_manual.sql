-- REVIEW ONLY. Run on Operly, with the staged gate deployed on all replicas.
-- psql -v handoff_subject=<reviewed immutable UUID> -f this_file
-- The operator must have independently verified the source/target mapping.
-- This freezes one existing subject; it does not activate federation.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='7s';
SET LOCAL statement_timeout='25s';
SELECT set_config('operly.handoff_subject', :'handoff_subject', true);
DO $$
DECLARE sid text:=current_setting('operly.handoff_subject');
BEGIN
 IF to_regclass('public.identity_accounts') IS NOT NULL
 OR to_regclass('public.app_users') IS NULL
 OR to_regclass('public.federated_identity_maps') IS NULL
 OR sid !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
 THEN RAISE EXCEPTION 'Unexpected handoff target'; END IF;
 PERFORM 1 FROM public.app_users WHERE id=sid AND active FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Active source account required'; END IF;
 IF EXISTS(SELECT 1 FROM public.federated_identity_maps WHERE operly_user_id=sid OR
     (issuer='https://api.dragonzpyder.xyz' AND subject=sid))
 THEN RAISE EXCEPTION 'Existing mapping requires review'; END IF;
 INSERT INTO public.federated_identity_maps(issuer,subject,operly_user_id,status,created_at)
 VALUES('https://api.dragonzpyder.xyz',sid,sid,'pending',timezone('UTC',now()));
 UPDATE public.auth_sessions SET revoked_at=timezone('UTC',now())
 WHERE user_id=sid AND revoked_at IS NULL;
 UPDATE public.auth_challenges SET consumed_at=timezone('UTC',now())
 WHERE user_id=sid AND consumed_at IS NULL;
END $$;
COMMIT;
-- No account data returned; preserve a private operator audit of the subject.
SELECT 'PENDING_HANDOFF_CREATED' AS result;
