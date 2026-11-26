"""Every authenticated Personal user must use the DragonZpyder harness."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from apps.api.agent_runtime_router import _use_dragonzpyder_personal_harness


class PersonalHarnessCanaryRoutingTests(unittest.TestCase):
    def test_old_canary_and_owner_flags_cannot_reenable_operly(self):
        with patch.dict("os.environ", {
            "OPERLY_PERSONAL_HARNESS_OWNER": "operly",
            "OPERLY_DRAGONZPYDER_PERSONAL_CANARY_USER_IDS": "",
            "OPERLY_PERSONAL_DRAGONZPYDER_ALL_USERS_ENABLED": "0",
        }, clear=True):
            self.assertTrue(_use_dragonzpyder_personal_harness(
                SimpleNamespace(is_personal=True, user_id="alice")))
            self.assertTrue(_use_dragonzpyder_personal_harness(
                SimpleNamespace(is_personal=True, user_id="bob")))
            self.assertFalse(_use_dragonzpyder_personal_harness(
                SimpleNamespace(is_personal=False, user_id="alice")))
            self.assertFalse(_use_dragonzpyder_personal_harness(
                SimpleNamespace(is_personal=True, user_id=None)))


if __name__ == "__main__":
    unittest.main()
