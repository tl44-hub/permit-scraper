import os, datetime, logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from .core import get, render

log = logging.getLogger("scraper")


async def socrata(client, s):
    since = (datetime.date.today() - datetime.timedelta(days=s.get("days_back", 14))).isoformat()
    params = {
        "$where": f"{s['date_field']} >= '{since}T00:00:00'",
        "$order": f"{s['date_field']} DESC",
        "$limit": s.get("limit", 5000),
    }
    headers = {}
    if os.getenv("SOCRATA_APP_TOKEN"):
        headers["X-App-Token"] = os.environ["SOCRATA_APP_TOKEN"]
    url = f"https://{s['domain']}/resource/{s['dataset']}.json"
    r = await get(client, url, params=params, headers=headers)
    return r.json()


async def html(client, s):
    rows, url = [], s["url"]
    for _ in range(s.get("max_pages", 3)):
        if s.get("render_javascript"):
            page_html = await render(url, s.get("wait_selector"))
        else:
            page_html = (await get(client, url)).text
        soup = BeautifulSoup(page_html, "lxml")
        for el in soup.select(s["row_selector"]):
            rec = {}
            for field, sel in s["fields"].items():
                node = el.select_one(sel)
                if node is None:
                    rec[field] = ""
                elif field == "url" and node.get("href"):
                    rec[field] = urljoin(url, node["href"])
                else:
                    rec[field] = node.get_text(" ", strip=True)
            if any(rec.values()):
                rows.append(rec)
        nxt = soup.select_one(s["next_page_selector"]) if s.get("next_page_selector") else None
        if not nxt or not nxt.get("href"):
            break
        url = urljoin(url, nxt["href"])
    return rows


ADAPTERS = {"socrata": socrata, "html": html}
