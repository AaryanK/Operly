-- REVIEW ONLY. Operator must have independently verified an active,
-- individually approved DragonZpyder subject and working login.
-- No workspace membership or local credential fields are changed.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='7s';
SELECT set_config('operly.handoff_subject', :'handoff_subject', true);
DO $$
DECLARE sid text:=current_setting('operly.handoff_subject');
BEGIN
 PERFORM 1 FROM public.app_users WHERE id=sid AND active FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Active source projection required'; END IF;
 UPDATE public.federated_identity_maps SET status='active',activated_at=timezone('UTC',now())
 WHERE issuer='https://api.dragonzpyder.xyz' AND subject=sid
 AND operly_user_id=sid AND status='pending' AND activated_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'Pending exact mapping required'; END IF;
END $$;
COMMIT;
SELECT 'FEDERATED_MAPPING_ACTIVATED' AS result;
