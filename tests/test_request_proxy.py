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
    _request_proxy_for_watch,
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
            _proxy_override_to_request_proxy("http://user:%70%77@gw.dataimpulse.com:10000"),
            {"server": "http://gw.dataimpulse.com:10000", "username": "user", "password": "pw"},
        )

    @patch.dict(
        "os.environ",
        {
            "CAMOFOX_BROWSER_REQUEST_PROXY": "http://user:%70%77@gw.dataimpulse.com:10000",
            "CAMOFOX_BROWSER_REQUEST_PROXY_WATCH_UUIDS": "7ee5d178-ff9b-471a-bb56-61b7c6c955fa,95ab0402-c991-43ed-adc8-9517eb2a8e82",
        },
        clear=False,
    )
    def test_fallback_request_proxy_is_used_for_allowlisted_watch_uuid(self):
        self.assertEqual(
            _fallback_request_proxy_for_url(
                "https://www.bol.com/nl/nl/p/example/123/",
                watch_uuid="7ee5d178-ff9b-471a-bb56-61b7c6c955fa",
            ),
            {"server": "http://gw.dataimpulse.com:10000", "username": "user", "password": "pw"},
        )

    @patch.dict(
        "os.environ",
        {
            "CAMOFOX_BROWSER_REQUEST_PROXY": "http://user:%70%77@gw.dataimpulse.com:10000",
            "CAMOFOX_BROWSER_REQUEST_PROXY_WATCH_UUIDS": "7ee5d178-ff9b-471a-bb56-61b7c6c955fa",
        },
        clear=False,
    )
    def test_fallback_request_proxy_is_not_used_for_unlisted_hard_domain(self):
        self.assertIsNone(
            _fallback_request_proxy_for_url(
                "https://www.amazon.nl/dp/example",
                watch_uuid="0786f84d-37f4-4f08-8c22-f42922a44c23",
            )
        )

    @patch.dict("os.environ", {"CAMOFOX_BROWSER_REQUEST_PROXY": "http://user:%70%77@gw.dataimpulse.com:10000"}, clear=False)
    def test_fallback_request_proxy_is_not_used_without_allowlist(self):
        self.assertIsNone(
            _fallback_request_proxy_for_url(
                "https://www.bol.com/nl/nl/p/example/123/",
                watch_uuid="7ee5d178-ff9b-471a-bb56-61b7c6c955fa",
            )
        )
    @patch.dict(
        "os.environ",
        {
            "CAMOFOX_BROWSER_REQUEST_PROXY_WATCH_UUIDS": "7ee5d178-ff9b-471a-bb56-61b7c6c955fa",
        },
        clear=False,
    )
    def test_proxy_override_is_ignored_for_unlisted_watch_uuid(self):
        self.assertIsNone(
            _request_proxy_for_watch(
                proxy_override="http://user:%70%77@gw.dataimpulse.com:10000",
                url="https://www.amazon.nl/dp/example",
                watch_uuid="0786f84d-37f4-4f08-8c22-f42922a44c23",
            )
        )

    @patch.dict(
        "os.environ",
        {
            "CAMOFOX_BROWSER_REQUEST_PROXY_WATCH_UUIDS": "7ee5d178-ff9b-471a-bb56-61b7c6c955fa",
        },
        clear=False,
    )
    def test_proxy_override_is_used_for_allowlisted_watch_uuid(self):
        self.assertEqual(
            _request_proxy_for_watch(
                proxy_override="http://user:%70%77@gw.dataimpulse.com:10000",
                url="https://www.bol.com/nl/nl/p/example/123/",
                watch_uuid="7ee5d178-ff9b-471a-bb56-61b7c6c955fa",
            ),
            {"server": "http://gw.dataimpulse.com:10000", "username": "user", "password": "pw"},
        )


if __name__ == "__main__":
    unittest.main()
