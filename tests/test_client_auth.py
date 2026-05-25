import sys
import types
import unittest


# The client helper is pure stdlib, but the plugin module imports changedetection.io's
# hook decorator and loguru at module load time. Stub them so this regression test
# can run without a full changedetection.io install.
loguru = types.ModuleType("loguru")
setattr(loguru, "logger", types.SimpleNamespace(warning=lambda *_args, **_kwargs: None, debug=lambda *_args, **_kwargs: None))
sys.modules.setdefault("loguru", loguru)

pluggy = types.ModuleType("changedetectionio.pluggy_interface")
setattr(pluggy, "hookimpl", lambda fn=None, **_kwargs: fn if fn is not None else (lambda wrapped: wrapped))
changedetectionio = types.ModuleType("changedetectionio")
sys.modules.setdefault("changedetectionio", changedetectionio)
sys.modules.setdefault("changedetectionio.pluggy_interface", pluggy)

from changedetectionio_camofox_browser.fetcher import _CamofoxClient


class CamofoxClientAuthTests(unittest.TestCase):
    def test_payload_request_headers_include_bearer_when_api_key_is_set(self):
        client = _CamofoxClient("http://camofox:9377/", api_key=" secret-token ")

        self.assertEqual(
            client._headers(has_payload=True),
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
        )

    def test_request_headers_omit_authorization_when_api_key_is_unset(self):
        client = _CamofoxClient("http://camofox:9377/")

        self.assertEqual(client._headers(has_payload=True), {"Content-Type": "application/json"})
        self.assertEqual(client._headers(has_payload=False), {})


if __name__ == "__main__":
    unittest.main()
