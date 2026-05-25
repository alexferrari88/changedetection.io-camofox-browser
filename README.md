# changedetection.io Camofox Browser plugin

A small [changedetection.io](https://github.com/dgtlmoon/changedetection.io) content-fetcher plugin that uses the [jo-inc/camofox-browser](https://github.com/jo-inc/camofox-browser) REST API as the browser.

Goal: use Camoufox/Camofox with changedetection.io **without patching changedetection.io source** and without maintaining a custom changedetection image.

## Why this shape

changedetection.io already has a plugin system:

- Docker entrypoint installs `EXTRA_PACKAGES` at startup.
- Plugins are discovered through Python entry point group `changedetectionio`.
- Content fetchers register via `register_content_fetcher()`.
- Fetcher names beginning with `extra_browser_` are accepted by changedetection.io's API schema in 0.55.x.

So the simplest deployable design is:

1. Run stock `ghcr.io/dgtlmoon/changedetection.io`.
2. Run stock `ghcr.io/jo-inc/camofox-browser` beside it.
3. Install this plugin via `EXTRA_PACKAGES`.
4. Select **Camofox Browser REST** per watch.

No fork, no source patch, no rebuilt changedetection image.

## Docker Compose

After this is published to PyPI:

```yaml
services:
  changedetection:
    image: ghcr.io/dgtlmoon/changedetection.io:latest
    container_name: changedetection
    restart: unless-stopped
    volumes:
      - ./datastore:/datastore
    environment:
      - EXTRA_PACKAGES=--upgrade changedetection.io-camofox-browser
      - CAMOFOX_BROWSER_URL=http://camofox-browser:9377
      # Optional: required when camofox-browser runs with CAMOFOX_API_KEY.
      # Prefer a changedetection-local env var name so the browser service key
      # is not confused with changedetection.io's own API key.
      - CAMOFOX_BROWSER_API_KEY=${CAMOFOX_BROWSER_API_KEY}
      - WEBDRIVER_DELAY_BEFORE_CONTENT_READY=5
    depends_on:
      - camofox-browser

  camofox-browser:
    image: ghcr.io/jo-inc/camofox-browser:latest
    container_name: camofox-browser
    restart: unless-stopped
    environment:
      - CAMOFOX_PORT=9377
```

For development directly from GitHub before PyPI publishing:

```yaml
environment:
  - EXTRA_PACKAGES=--upgrade git+https://github.com/alexferrari88/changedetection.io-camofox-browser.git
  - CAMOFOX_BROWSER_URL=http://camofox-browser:9377
```

## Auto-update model

For a low-maintenance deployment:

- Let Watchtower update the two registry images:
  - `ghcr.io/dgtlmoon/changedetection.io:latest`
  - `ghcr.io/jo-inc/camofox-browser:latest`
- Keep `EXTRA_PACKAGES=--upgrade changedetection.io-camofox-browser` so the Python plugin is upgraded whenever the changedetection container starts/recreates.
- Avoid local Docker builds unless you need to test a branch.

This keeps the operational surface to normal Docker image updates instead of a custom changedetection image rebuild.

## Test result on Alex's failing Amazon watches

The plugin was installed into the running `changedetection` container and tested against the six watches whose latest snapshots were `Amazon Sign-In` with blank prices.

Result: the underlying `camofox-browser` REST service loaded all six Amazon product pages and extracted non-empty prices when the existing Amazon interstitial step was guarded first.

Root cause found during testing: the old browser step blindly clicked any element matching `/Doorgaan met winkelen|Continue shopping|Verder|Continue/`. On real product pages that can navigate to Amazon Sign-In. The safe first step is:

```js
(() => {
  if (document.querySelector('#productTitle,#corePrice_feature_div,.a-price .a-offscreen')) return 'already product page';
  const candidates = Array.from(document.querySelectorAll('button,input[type=submit],a')).filter(el => /Doorgaan met winkelen|Continue shopping|Verder|Continue/i.test((el.innerText || el.value || el.getAttribute('alt') || '').trim()));
  if (candidates[0]) { candidates[0].click(); return 'clicked interstitial'; }
  const f = document.querySelector('form[action*="validateCaptcha"]');
  if (f) { f.submit(); return 'submitted interstitial'; }
  return 'no interstitial';
})()
```

## Current feature coverage

Implemented fetcher: `extra_browser_camofox_browser`

Supported:

- Initial page navigation through camofox-browser `/tabs`.
- HTML capture via `/evaluate` (`document.documentElement.outerHTML`).
- Screenshots via `/screenshot`.
- Common browser steps:
  - `Goto site`, `Goto URL`
  - `Wait for seconds`, `Wait for text`, `Wait for text in element`
  - `Click element`, `Click element if exists`
  - `Click element containing text`, `Click element containing text if exists`
  - `Enter text in field`
  - `Press Enter`
  - `Scroll down`
  - `Execute JS`
  - `Remove elements`
  - `Make all child elements visible`

Known limitations:

- camofox-browser does not currently expose the original navigation HTTP status, so this plugin reports `200` if the browser interaction succeeded.
- Per-watch changedetection proxy settings are not passed through yet; configure proxy at the camofox-browser service layer.
- Visual selector/xpath metadata is best-effort via changedetection's existing JavaScript extractor.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `CAMOFOX_BROWSER_URL` | `http://camofox-browser:9377` | Base URL for the REST browser service |
| `CAMOFOX_BROWSER_API_KEY` | unset | Bearer token for protected camofox-browser routes such as `/tabs/:id/evaluate`; falls back to `CAMOFOX_API_KEY` if set |
| `CAMOFOX_BROWSER_USER_ID` | `changedetectionio` | camofox-browser user/session namespace |
| `CAMOFOX_BROWSER_TIMEOUT` | `120` | HTTP timeout in seconds |
| `CAMOFOX_BROWSER_CAPTURE_XPATH` | `false` | Capture changedetection visual-selector metadata; keep off for price watches |
| `CAMOFOX_BROWSER_CAPTURE_INSTOCK` | `false` | Run changedetection's generic in-stock extractor; keep off when watch JS emits availability |
| `CAMOFOX_BROWSER_CAPTURE_SCREENSHOT` | `false` | Capture final screenshot; keep off for faster price watches |
| `WEBDRIVER_DELAY_BEFORE_CONTENT_READY` | `5` | Reused changedetection delay before capture |

## Research notes

Confirmed from upstream changedetection.io:

- `docker-entrypoint.sh` runs `pip3 install --no-cache-dir $EXTRA_PACKAGES` on startup.
- `pluggy_interface.py` calls `plugin_manager.load_setuptools_entrypoints("changedetectionio")`.
- `content_fetchers.__init__.py` calls plugin hooks and adds returned fetchers to `available_fetchers()`.

This means this plugin can be distributed as a normal Python package.
