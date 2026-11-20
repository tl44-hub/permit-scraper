import os, datetime, logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from .core import get, render

log = logging.getLogger("scraper")


async def socrata(client, s):
    """Socrata open-data API (used by hundreds of US cities/counties). Best source: structured + legal."""
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
    """Config-driven HTML list scraper using CSS selectors, with pagination."""
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


async def table(client, s):
    """Beginner-friendly HTML table scraper: finds the table by its column HEADER TEXT, no CSS selectors.
    columns: {field_name: "Header text"}   keep: {"Header": "text that must appear"}   static: {field: value}"""
    if s.get("render_javascript"):
        page_html = await render(s["url"], s.get("wait_selector"))
    else:
        page_html = (await get(client, s["url"])).text
    soup = BeautifulSoup(page_html, "lxml")
    cols = {f: h.strip().lower() for f, h in s["columns"].items()}
    keep = {h.strip().lower(): v.upper() for h, v in s.get("keep", {}).items()}
    needed = set(cols.values()) | set(keep)
    rows, seen = [], []
    for tbl in soup.find_all("table"):
        trs = tbl.find_all("tr")
        if not trs:
            continue
        header = [c.get_text(" ", strip=True).lower() for c in trs[0].find_all(["th", "td"])]
        seen.append(header)
        if not needed.issubset(header):
            continue
        for tr in trs[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) != len(header):
                continue
            row = dict(zip(header, cells))
            if any(v not in row[h].upper() for h, v in keep.items()):
                continue
            rec = {f: row[h] for f, h in cols.items()}
            rec.update(s.get("static", {}))
            rows.append(rec)
    if not rows:
        log.warning("[%s] no matching table rows. Wanted headers %s. Headers found on page: %s",
                    s["id"], sorted(needed), seen[:5])
    return rows


ADAPTERS = {"socrata": socrata, "html": html, "table": table}
