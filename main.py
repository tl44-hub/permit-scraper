import asyncio, json, hashlib, logging, sys, yaml, httpx
from scraper.adapters import ADAPTERS
from scraper.db import open_db, upsert, export_csv, now

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scraper")
CONCURRENCY = asyncio.BoundedSemaphore(4)


def pick(raw, spec):
    if not spec:
        return ""
    if isinstance(spec, list):
        return " ".join(str(raw[k]).strip() for k in spec if raw.get(k)).strip()
    return str(raw.get(spec, "")).strip()


def normalize(raw, s):
    m = s.get("map", {})
    rec = {
        "source_id": s["id"], "category": s["category"], "jurisdiction": s.get("jurisdiction", ""),
        "record_id": pick(raw, m.get("record_id", "record_id")),
        "address": pick(raw, m.get("address", "address")),
        "event_date": pick(raw, m.get("event_date", "event_date"))[:10],
        "status": pick(raw, m.get("status", "status")),
        "description": pick(raw, m.get("description", "description"))[:1000],
        "party": pick(raw, m.get("party", "party")),
        "amount": pick(raw, m.get("amount", "amount")),
        "url": pick(raw, m.get("url", "url")),
        "raw_json": json.dumps(raw, sort_keys=True, default=str),
    }
    if not rec["record_id"]:
        rec["record_id"] = hashlib.sha1(
            "|".join([rec["address"], rec["event_date"], rec["description"]]).encode()).hexdigest()[:16]
    return rec


async def run_source(client, db, s):
    async with CONCURRENCY:
        try:
            raws = await ADAPTERS[s["adapter"]](client, s)
        except Exception as e:
            log.error("[%s] FAILED: %s", s["id"], e)
            return None
    counts = {"new": 0, "changed": 0, "same": 0}
    for raw in raws:
        counts[await upsert(db, normalize(raw, s))] += 1
    await db.commit()
    log.info("[%s] %d fetched: %s", s["id"], len(raws), counts)
    return counts


async def main():
    started = now()
    cfg = yaml.safe_load(open("config/sources.yaml"))
    sources = [s for s in cfg["sources"] if s.get("enabled", True)]
    db = await open_db("data/records.db")
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(run_source(client, db, s) for s in sources))
    await export_csv(db, "data/exports", started)
    await db.close()
    if sources and all(r is None for r in results):
        sys.exit(1)


asyncio.run(main())
