"""Optional headless rendering for JS-heavy pages (``render: js`` in sources.yaml).

Runs only in the ``render`` Compose profile (separate image with Playwright + Chromium) so the API image
stays small. When Playwright is not installed the pipeline falls back to the static HTML and reports
the page as captured *without* rendering.
"""

from __future__ import annotations

from app.observability.logging import get_logger

log = get_logger(__name__)


def render_available() -> bool:
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return False
    return True


async def render_html(url: str, user_agent: str, timeout_ms: int = 30000) -> str | None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=["--no-sandbox"])
            page = await browser.new_page(user_agent=user_agent)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(5000)  # tracking scripts never go network-idle; give widgets time
            # Expand collapsed FAQ/accordion widgets so their answers are in the DOM.
            await page.evaluate(
                "document.querySelectorAll('details').forEach(d => d.open = true);"
                "document.querySelectorAll('[aria-expanded=\"false\"]').forEach(e => { try { e.click() } catch (_) {} });"
            )
            await page.wait_for_timeout(1500)
            html = await page.content()
            await browser.close()
            return html
    except Exception as exc:
        log.warning("render_failed", url=url, error=type(exc).__name__)
        return None
