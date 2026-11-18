import asyncio, random, logging
import httpx

log = logging.getLogger("scraper")

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


async def get(client: httpx.AsyncClient, url, params=None, headers=None, retries=5):
    h = {"User-Agent": random.choice(UAS), **(headers or {})}
    for attempt in range(retries):
        try:
            r = await client.get(url, params=params, headers=h, timeout=40, follow_redirects=True)
            if r.status_code in (429, 503, 403):
                raise httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
            r.raise_for_status()
            return r
        except httpx.HTTPError as e:
            if attempt == retries - 1:
                raise
            wait = min(60, 2 ** attempt) + random.random()
            log.warning("retry %s in %.1fs (%s)", url, wait, e)
            await asyncio.sleep(wait)


async def render(url, wait_selector=None):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(user_agent=random.choice(UAS))
        await page.goto(url, wait_until="networkidle", timeout=60000)
        if wait_selector:
            await page.wait_for_selector(wait_selector, timeout=30000)
        html = await page.content()
        await browser.close()
        return html
