import aiosqlite, datetime, hashlib, json, csv, os

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
  source_id TEXT, record_id TEXT, category TEXT, jurisdiction TEXT,
  address TEXT, event_date TEXT, status TEXT, description TEXT,
  party TEXT, amount TEXT, url TEXT, raw_json TEXT, content_hash TEXT,
  first_seen TEXT, last_seen TEXT, updated_at TEXT,
  PRIMARY KEY (source_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_cat ON records(category, first_seen);
"""

COLS = ["source_id", "record_id", "category", "jurisdiction", "address", "event_date",
        "status", "description", "party", "amount", "url"]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


async def open_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = await aiosqlite.connect(path)
    await db.executescript(SCHEMA)
    return db


async def upsert(db, rec):
    ts = now()
    h = hashlib.sha1(json.dumps([rec[c] for c in COLS], sort_keys=True).encode()).hexdigest()
    cur = await db.execute("SELECT content_hash FROM records WHERE source_id=? AND record_id=?",
                           (rec["source_id"], rec["record_id"]))
    row = await cur.fetchone()
    if row is None:
        await db.execute(
            f"INSERT INTO records ({','.join(COLS)},raw_json,content_hash,first_seen,last_seen,updated_at) "
            f"VALUES ({','.join('?'*len(COLS))},?,?,?,?,?)",
            [rec[c] for c in COLS] + [rec["raw_json"], h, ts, ts, ts])
        return "new"
    if row[0] != h:
        sets = ",".join(f"{c}=?" for c in COLS[2:])
        await db.execute(f"UPDATE records SET {sets},raw_json=?,content_hash=?,last_seen=?,updated_at=? "
                         "WHERE source_id=? AND record_id=?",
                         [rec[c] for c in COLS[2:]] + [rec["raw_json"], h, ts, ts, rec["source_id"], rec["record_id"]])
        return "changed"
    await db.execute("UPDATE records SET last_seen=? WHERE source_id=? AND record_id=?",
                     (ts, rec["source_id"], rec["record_id"]))
    return "same"


async def export_csv(db, out_dir, run_started):
    os.makedirs(out_dir, exist_ok=True)
    cur = await db.execute("SELECT DISTINCT category FROM records")
    cats = [r[0] for r in await cur.fetchall()]
    for cat in cats:
        for name, where, args in [
            (f"{cat}_all.csv", "category=?", (cat,)),
            (f"{cat}_new_this_run.csv", "category=? AND first_seen>=?", (cat, run_started)),
        ]:
            cur = await db.execute(
                f"SELECT {','.join(COLS)},first_seen FROM records WHERE {where} ORDER BY event_date DESC", args)
            rows = await cur.fetchall()
            with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(COLS + ["first_seen"])
                w.writerows(rows)
