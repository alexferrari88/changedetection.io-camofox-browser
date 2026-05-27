import asyncio
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger
from changedetectionio.pluggy_interface import hookimpl


FETCHER_NAME = "extra_browser_camofox_browser"
DEFAULT_USER_ID = os.getenv("CAMOFOX_BROWSER_USER_ID", "changedetectionio")


def _env_truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def _proxy_override_to_request_proxy(proxy_override) -> dict[str, str] | None:
    if not proxy_override:
        return None
    if isinstance(proxy_override, dict):
        if "server" in proxy_override:
            server = str(proxy_override.get("server") or "").strip()
            username = str(proxy_override.get("username") or "").strip()
            password = str(proxy_override.get("password") or "").strip()
            return {"server": server, "username": username, "password": password} if server else None
        proxy_url = proxy_override.get("https") or proxy_override.get("http") or proxy_override.get("all")
    else:
        proxy_url = str(proxy_override).strip()
    if not proxy_url:
        return None
    parsed = urllib.parse.urlsplit(str(proxy_url))
    if not (parsed.scheme and parsed.hostname):
        return None
    netloc = parsed.hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    server = urllib.parse.urlunsplit((parsed.scheme, netloc, "", "", ""))
    username = urllib.parse.unquote(parsed.username or "")
    password = urllib.parse.unquote(parsed.password or "")
    return {"server": server, "username": username, "password": password}


def _proxy_scoped_user_id(base_user_id: str, request_proxy: dict[str, str] | None) -> str:
    if not request_proxy:
        return base_user_id
    fingerprint = json.dumps(request_proxy, sort_keys=True, separators=(",", ":"))
    suffix = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:12]
    return f"{base_user_id}-proxy-{suffix}"


def _fallback_request_proxy_for_url(url: str | None) -> dict[str, str] | None:
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    hard_domain = host.endswith("bol.com") or "amazon." in host or "idventure-shop" in host
    if not hard_domain:
        return None
    proxy_url = os.getenv("CAMOFOX_BROWSER_REQUEST_PROXY") or os.getenv("DATAIMPULSE_PROXY") or ""
    return _proxy_override_to_request_proxy(proxy_url)


@hookimpl
def register_content_fetcher():
    """Register a changedetection.io fetcher backed by jo-inc/camofox-browser.

    Imports from ``changedetectionio.content_fetchers`` are intentionally deferred
    until this hook runs. changedetection.io discovers entry points while
    ``content_fetchers`` is partially initialised, so importing it at module load
    time creates a circular import.
    """
    return (FETCHER_NAME, _build_fetcher_class())


@dataclass
class _Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict[str, Any]:
        return json.loads(self.body.decode("utf-8"))


class _CamofoxClient:
    def __init__(
        self,
        base_url: str,
        user_id: str = DEFAULT_USER_ID,
        timeout: int = 120,
        api_key: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.timeout = timeout
        self.api_key = (api_key or "").strip()

    def _headers(self, has_payload: bool) -> dict[str, str]:
        headers: dict[str, str] = {}
        if has_payload:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, method: str, path: str, payload: Optional[dict[str, Any]] = None) -> _Response:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers=self._headers(payload is not None),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return _Response(
                    status=resp.status,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    body=resp.read(),
                )
        except urllib.error.HTTPError as e:
            body = e.read()
            raise RuntimeError(f"camofox-browser HTTP {e.code} {method} {path}: {body[:500]!r}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Could not reach camofox-browser at {self.base_url}: {e}") from e

    def create_tab(self, url: str, session_key: str, proxy: dict[str, str] | None = None) -> str:
        payload: dict[str, Any] = {"userId": self.user_id, "sessionKey": session_key, "url": url}
        if proxy:
            payload["proxy"] = proxy
        response = self._request("POST", "/tabs", payload).json()
        tab_id = response.get("tabId")
        if not tab_id:
            raise RuntimeError(f"camofox-browser did not return tabId: {response!r}")
        return tab_id

    def close_tab(self, tab_id: str) -> None:
        quoted = urllib.parse.quote(tab_id, safe="")
        try:
            self._request("DELETE", f"/tabs/{quoted}?userId={urllib.parse.quote(self.user_id)}")
        except Exception as e:
            logger.warning(f"Could not close camofox-browser tab {tab_id}: {e}")

    def navigate(self, tab_id: str, url: str) -> None:
        self._request("POST", f"/tabs/{tab_id}/navigate", {"userId": self.user_id, "url": url})

    def evaluate(self, tab_id: str, expression: str) -> Any:
        response = self._request("POST", f"/tabs/{tab_id}/evaluate", {
            "userId": self.user_id,
            "expression": expression,
        }).json()
        if not response.get("ok", True):
            raise RuntimeError(f"camofox-browser evaluate failed: {response!r}")
        return response.get("result")

    def screenshot(self, tab_id: str) -> bytes:
        return self._request("GET", f"/tabs/{tab_id}/screenshot?userId={urllib.parse.quote(self.user_id)}").body

    def click(self, tab_id: str, selector: str) -> None:
        self._request("POST", f"/tabs/{tab_id}/click", {"userId": self.user_id, "selector": selector})

    def type_text(self, tab_id: str, selector: str, text: str) -> None:
        self._request("POST", f"/tabs/{tab_id}/type", {"userId": self.user_id, "selector": selector, "text": text})

    def press(self, tab_id: str, key: str) -> None:
        self._request("POST", f"/tabs/{tab_id}/press", {"userId": self.user_id, "key": key})

    def scroll(self, tab_id: str, direction: str = "down", amount: int = 700) -> None:
        self._request("POST", f"/tabs/{tab_id}/scroll", {"userId": self.user_id, "direction": direction, "amount": amount})

    def wait(self, tab_id: str, ms: int) -> None:
        self._request("POST", f"/tabs/{tab_id}/wait", {"userId": self.user_id, "timeout": ms})


def _build_fetcher_class():
    from changedetectionio.content_fetchers import (
        FAVICON_FETCHER_JS,
        INSTOCK_DATA_JS,
        SCREENSHOT_MAX_HEIGHT_DEFAULT,
        XPATH_ELEMENT_JS,
        visualselector_xpath_selectors,
    )
    from changedetectionio.content_fetchers.base import Fetcher
    from changedetectionio.content_fetchers.exceptions import EmptyReply, PageUnloadable

    class CamofoxBrowserFetcher(Fetcher):
        fetcher_description = "Camofox Browser REST (Camoufox/Firefox stealth)"
        supports_browser_steps = True
        supports_screenshots = True
        supports_xpath_element_data = True

        def __init__(self, proxy_override=None, custom_browser_connection_url=None, **kwargs):
            super().__init__(**kwargs)
            base_url = custom_browser_connection_url or os.getenv("CAMOFOX_BROWSER_URL", "http://camofox-browser:9377")
            self.request_proxy = _proxy_override_to_request_proxy(proxy_override)
            self.base_user_id = os.getenv("CAMOFOX_BROWSER_USER_ID", DEFAULT_USER_ID)
            self.client = _CamofoxClient(
                base_url=base_url,
                user_id=_proxy_scoped_user_id(self.base_user_id, self.request_proxy),
                timeout=int(os.getenv("CAMOFOX_BROWSER_TIMEOUT", "120")),
                api_key=os.getenv("CAMOFOX_BROWSER_API_KEY") or os.getenv("CAMOFOX_API_KEY"),
            )
            self.tab_id: Optional[str] = None
            self.watch_uuid: Optional[str] = None

            if proxy_override and not self.request_proxy:
                logger.warning("Could not convert changedetection proxy override for camofox-browser request proxy")

        def is_ready(self):
            return bool(self.client.base_url)

        def get_error(self):
            return self.error

        def get_last_status_code(self):
            return self.status_code

        async def quit(self, watch=None):
            if self.tab_id:
                await asyncio.to_thread(self.client.close_tab, self.tab_id)
                self.tab_id = None

        async def screenshot_step(self, step_n=""):
            super().screenshot_step(step_n=step_n)
            if not (self.tab_id and self.browser_steps_screenshot_path):
                return
            screenshot = await asyncio.to_thread(self.client.screenshot, self.tab_id)
            destination = os.path.join(self.browser_steps_screenshot_path, f"step_{step_n}.jpeg")
            with open(destination, "wb") as f:
                f.write(screenshot)

        async def save_step_html(self, step_n):
            super().save_step_html(step_n=step_n)
            if not (self.tab_id and self.browser_steps_screenshot_path):
                return
            html = await asyncio.to_thread(self.client.evaluate, self.tab_id, "document.documentElement.outerHTML")
            destination = os.path.join(self.browser_steps_screenshot_path, f"step_{step_n}.html")
            with open(destination, "w", encoding="utf-8") as f:
                f.write(html or "")

        async def _eval(self, expression: str) -> Any:
            return await asyncio.to_thread(self.client.evaluate, self.tab_id, expression)

        async def _run_browser_steps(self, start_url: str):
            from changedetectionio.browser_steps.browser_steps import browser_steps_get_valid_steps
            from changedetectionio.jinja2_custom import render as jinja_render

            if not self.browser_steps:
                return

            valid_steps = browser_steps_get_valid_steps(self.browser_steps)
            for step_n, step in enumerate(valid_steps, start=1):
                await self.screenshot_step("before-" + str(step_n))
                await self.save_step_html("before-" + str(step_n))

                selector = step.get("selector") or ""
                value = step.get("optional_value") or ""
                if ("{%" in selector) or ("{{" in selector):
                    selector = jinja_render(template_str=selector)
                if ("{%" in value) or ("{{" in value):
                    value = jinja_render(template_str=value)

                await self._run_step(step.get("operation"), selector, value, start_url=start_url)
                await asyncio.to_thread(self.client.wait, self.tab_id, 1500)

                await self.screenshot_step(str(step_n))
                await self.save_step_html(str(step_n))

        async def _run_step(self, operation: str, selector: str, value: str, start_url: str):
            op = re.sub(r"[^0-9a-zA-Z]+", "_", (operation or "").lower()).strip("_")
            if op in {"", "choose_one"}:
                return
            if selector.startswith("/") and not selector.startswith("//"):
                selector = "xpath=" + selector

            if op == "goto_site":
                await asyncio.to_thread(self.client.navigate, self.tab_id, re.sub(r"^source:", "", start_url, flags=re.I))
            elif op == "goto_url":
                await asyncio.to_thread(self.client.navigate, self.tab_id, value)
            elif op in {"wait_for_seconds"}:
                seconds = float(value.strip()) if value else 1.0
                await asyncio.to_thread(self.client.wait, self.tab_id, int(seconds * 1000))
            elif op == "wait_for_text":
                await self._wait_for_js(f"document.body && document.body.innerText.includes({json.dumps(value)})")
            elif op == "wait_for_text_in_element":
                await self._wait_for_js(
                    f"document.querySelector({json.dumps(selector)})?.innerText.includes({json.dumps(value)})"
                )
            elif op in {"click_element", "click_element_if_exists"}:
                if op.endswith("if_exists"):
                    exists = await self._eval(f"!!document.querySelector({json.dumps(selector)})")
                    if not exists:
                        return
                await asyncio.to_thread(self.client.click, self.tab_id, selector)
            elif op in {"click_element_containing_text", "click_element_containing_text_if_exists"}:
                expr = """
                (text) => {
                  const els = Array.from(document.querySelectorAll('button,a,input,summary,[role=button],div,span,p,li'));
                  const el = els.find(e => (e.innerText || e.value || '').includes(text));
                  if (!el) return false;
                  el.click();
                  return true;
                }
                """
                found = await self._eval(f"({expr})({json.dumps(value)})")
                if not found and not op.endswith("if_exists"):
                    raise RuntimeError(f"No element containing text {value!r}")
            elif op == "click_x_y":
                if not re.match(r"^\s?\d+\s?,\s?\d+\s?$", value or ""):
                    return
                x, y = [int(float(v.strip())) for v in value.split(",", 1)]
                await self._eval(f"document.elementFromPoint({x}, {y})?.click()")
            elif op == "enter_text_in_field":
                await asyncio.to_thread(self.client.type_text, self.tab_id, selector, value)
            elif op == "press_enter":
                await asyncio.to_thread(self.client.press, self.tab_id, "Enter")
            elif op == "scroll_down":
                await asyncio.to_thread(self.client.scroll, self.tab_id, "down", 700)
            elif op == "execute_js":
                await self._eval(value)
            elif op == "remove_elements":
                await self._eval(f"document.querySelectorAll({json.dumps(selector)}).forEach(el => el.remove())")
            elif op == "make_all_child_elements_visible":
                await self._eval(f"""
                document.querySelectorAll({json.dumps(selector)} + ' *').forEach(el => {{
                  el.style.display='block'; el.style.visibility='visible'; el.style.opacity='1';
                  el.style.position='relative'; el.style.height='auto'; el.style.width='auto';
                  el.removeAttribute('hidden'); el.classList.remove('hidden', 'd-none');
                }})
                """)
            else:
                logger.warning(f"Unsupported camofox-browser step {operation!r}; skipping")

        async def _wait_for_js(self, expression: str, timeout_seconds: int = 30):
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if await self._eval(expression):
                    return
                await asyncio.sleep(0.5)
            raise TimeoutError(f"Timed out waiting for JS expression: {expression}")

        async def run(
            self,
            fetch_favicon=True,
            current_include_filters=None,
            empty_pages_are_a_change=False,
            ignore_status_codes=False,
            is_binary=False,
            request_body=None,
            request_headers=None,
            request_method=None,
            screenshot_format=None,
            timeout=None,
            url=None,
            watch_uuid=None,
        ):
            self.delete_browser_steps_screenshots()
            self.watch_uuid = watch_uuid
            session_key = watch_uuid or "manual"
            self.status_code = 200  # camofox-browser REST does not expose initial navigation HTTP status yet.

            try:
                request_proxy = self.request_proxy or _fallback_request_proxy_for_url(url)
                self.client.user_id = _proxy_scoped_user_id(self.base_user_id, request_proxy)
                self.tab_id = await asyncio.to_thread(self.client.create_tab, url or "", session_key, request_proxy)
                extra_wait = int(os.getenv("WEBDRIVER_DELAY_BEFORE_CONTENT_READY", "5")) + self.render_extract_delay

                if self.webdriver_js_execute_code:
                    await self._eval(self.webdriver_js_execute_code)

                await asyncio.to_thread(self.client.wait, self.tab_id, extra_wait * 1000)
                await self._run_browser_steps(start_url=url)
                if self.browser_steps:
                    await asyncio.to_thread(self.client.wait, self.tab_id, extra_wait * 1000)

                content = await self._eval("document.documentElement.outerHTML")
                if not empty_pages_are_a_change and not (content or "").strip():
                    raise EmptyReply(url=url, status_code=self.status_code)

                if fetch_favicon:
                    try:
                        self.favicon_blob = await self._eval(FAVICON_FETCHER_JS)
                    except Exception as e:
                        logger.debug(f"Camofox favicon fetch failed, continuing: {e}")

                self.content = content

                if _env_truthy("CAMOFOX_BROWSER_CAPTURE_XPATH", default=False):
                    await self._eval("var include_filters=" + json.dumps(current_include_filters or ""))
                    xpath_options = {
                        "visualselector_xpath_selectors": visualselector_xpath_selectors,
                        "max_height": int(os.getenv("SCREENSHOT_MAX_HEIGHT", SCREENSHOT_MAX_HEIGHT_DEFAULT)),
                    }
                    self.xpath_data = await self._eval(f"({XPATH_ELEMENT_JS})({json.dumps(xpath_options)})")

                if _env_truthy("CAMOFOX_BROWSER_CAPTURE_INSTOCK", default=False):
                    self.instock_data = await self._eval(f"({INSTOCK_DATA_JS})()")

                if _env_truthy("CAMOFOX_BROWSER_CAPTURE_SCREENSHOT", default=False):
                    self.screenshot = await asyncio.to_thread(self.client.screenshot, self.tab_id)

            except Exception as e:
                self.error = str(e)
                raise PageUnloadable(url=url, status_code=self.status_code, message=str(e)) from e
            finally:
                await self.quit()

    return CamofoxBrowserFetcher
