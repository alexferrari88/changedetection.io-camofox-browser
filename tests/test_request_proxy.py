import sys
import types
import unittest
from unittest.mock import patch


loguru = types.ModuleType("loguru")
setattr(loguru, "logger", types.SimpleNamespace(warning=lambda *_args, **_kwargs: None, debug=lambda *_args, **_kwargs: None))
sys.modules.setdefault("loguru", loguru)

pluggy = types.ModuleType("changedetectionio.pluggy_interface")
setattr(pluggy, "hookimpl", lambda fn=None, **_kwargs: fn if fn is not None else (lambda wrapped: wrapped))
changedetectionio = types.ModuleType("changedetectionio")
sys.modules.setdefault("changedetectionio", changedetectionio)
sys.modules.setdefault("changedetectionio.pluggy_interface", pluggy)

from changedetectionio_camofox_browser.fetcher import (
    _CamofoxClient,
    _fallback_request_proxy_for_url,
    _proxy_override_to_request_proxy,
)


class CamofoxClientRequestProxyTests(unittest.TestCase):
    def test_create_tab_sends_proxy_payload_when_supplied(self):
        client = _CamofoxClient("http://camofox:9377/")
        captured = {}

        def fake_request(method, path, payload=None):
            captured.update({"method": method, "path": path, "payload": payload})
            return types.SimpleNamespace(json=lambda: {"tabId": "tab-123"})

        with patch.object(client, "_request", side_effect=fake_request):
            tab_id = client.create_tab(
                "https://example.com",
                "watch-1",
                proxy={"server": "http://gw.dataimpulse.com:10000", "username": "user", "password": "pass"},
            )

        self.assertEqual(tab_id, "tab-123")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/tabs")
        self.assertEqual(captured["payload"]["proxy"]["server"], "http://gw.dataimpulse.com:10000")
        self.assertEqual(captured["payload"]["proxy"]["username"], "user")
        self.assertEqual(captured["payload"]["proxy"]["password"], "pass")

    def test_proxy_override_url_is_converted_to_request_proxy(self):
        self.assertEqual(
            _proxy_override_to_request_proxy("http://user:secret@gw.dataimpulse.com:10000"),
            {"server": "http://gw.dataimpulse.com:10000", "username": "user", "password": "secret"},
        )

    @patch.dict("os.environ", {"CAMOFOX_BROWSER_REQUEST_PROXY": "http://user:secret@gw.dataimpulse.com:10000"}, clear=False)
    def test_fallback_request_proxy_is_used_for_hard_domains(self):
        self.assertEqual(
            _fallback_request_proxy_for_url("https://www.bol.com/nl/nl/p/example/123/"),
            {"server": "http://gw.dataimpulse.com:10000", "username": "user", "password": "secret"},
        )

    @patch.dict("os.environ", {"CAMOFOX_BROWSER_REQUEST_PROXY": "http://user:secret@gw.dataimpulse.com:10000"}, clear=False)
    def test_fallback_request_proxy_is_not_used_for_generic_domains(self):
        self.assertIsNone(_fallback_request_proxy_for_url("https://example.com/product"))


if __name__ == "__main__":
    unittest.main()
