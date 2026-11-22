"""All authenticated Personal users delegate to DragonZpyder."""
from types import SimpleNamespace
from unittest.mock import patch

from apps.api.agent_runtime_router import _use_dragonzpyder_personal_harness


def test_only_personal_scope_is_eligible_and_old_canary_flags_are_ignored():
    with patch.dict("os.environ", {
        "OPERLY_PERSONAL_HARNESS_OWNER": "operly",
        "OPERLY_DRAGONZPYDER_PERSONAL_CANARY_USER_IDS": "",
        "OPERLY_PERSONAL_DRAGONZPYDER_ALL_USERS_ENABLED": "0",
    }, clear=True):
        assert _use_dragonzpyder_personal_harness(SimpleNamespace(is_personal=True, user_id="alice"))
        assert _use_dragonzpyder_personal_harness(SimpleNamespace(is_personal=True, user_id="bob"))
        assert not _use_dragonzpyder_personal_harness(SimpleNamespace(is_personal=False, user_id="alice"))
        assert not _use_dragonzpyder_personal_harness(SimpleNamespace(is_personal=True, user_id=None))
