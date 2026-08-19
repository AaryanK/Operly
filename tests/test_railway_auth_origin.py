import os
import unittest
from unittest.mock import patch

from starlette.requests import Request

from apps.api.csrf import _cross_site


def request_for(origin: str, host: str, *, proto: str = "https", fetch_site: str = "same-origin") -> Request:
    headers = [
        (b"host", host.encode()),
        (b"origin", origin.encode()),
        (b"x-forwarded-proto", proto.encode()),
        (b"sec-fetch-site", fetch_site.encode()),
    ]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/auth/login",
            "raw_path": b"/api/auth/login",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": (host, 443),
        }
    )


class RailwayAuthOriginTests(unittest.TestCase):
    def test_railway_copy_is_same_origin_even_when_public_base_url_is_original_domain(self):
        with patch.dict(
            os.environ,
            {"PUBLIC_BASE_URL": "https://operly.dragonzpyder.xyz"},
            clear=False,
        ):
            request = request_for(
                "https://operly-copy-production-886c.up.railway.app",
                "operly-copy-production-886c.up.railway.app",
            )
            self.assertFalse(_cross_site(request))

    def test_real_cross_site_origin_remains_rejected(self):
        with patch.dict(
            os.environ,
            {"PUBLIC_BASE_URL": "https://operly.dragonzpyder.xyz"},
            clear=False,
        ):
            request = request_for(
                "https://attacker.example",
                "operly-copy-production-886c.up.railway.app",
                fetch_site="cross-site",
            )
            self.assertTrue(_cross_site(request))

    def test_configured_custom_domain_is_still_accepted(self):
        with patch.dict(
            os.environ,
            {"PUBLIC_BASE_URL": "https://operly.dragonzpyder.xyz"},
            clear=False,
        ):
            request = request_for(
                "https://operly.dragonzpyder.xyz",
                "internal-proxy.example",
            )
            self.assertFalse(_cross_site(request))


if __name__ == "__main__":
    unittest.main()
