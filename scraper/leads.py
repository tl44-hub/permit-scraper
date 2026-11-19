import csv, os, re, datetime, yaml

COLS = ["source_id", "record_id", "category", "jurisdiction", "address", "event_date",
        "status", "description", "party", "amount", "url", "first_seen"]


def money(s):
    try:
        return float(re.sub(r"[^0-9.]", "", s or "") or 0)
    except ValueError:
        return 0.0


def iso_days_ago(n):
    d = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=n)
    return d.isoformat(timespec="seconds")


def date_days_ago(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


def write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


async def fetch(db, sql, args=()):
    cur = await db.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in await cur.fetchall()]


async def build_leads(db, out_dir, cfg_path="config/leads.yaml"):
    cfg = yaml.safe_load(open(cfg_path)) if os.path.exists(cfg_path) else {}
    p, v, t = cfg.get("permits", {}), cfg.get("violations", {}), cfg.get("top_leads", {})
    min_cost = p.get("min_cost", 100000)
    types = [x.upper() for x in p.get("types", ["NEW CONSTRUCTION", "RENOVATION", "ALTERATION", "WRECKING", "DEMOLITION"])]
    open_statuses = [x.upper() for x in v.get("open_statuses", ["OPEN"])]
    sel = f"SELECT {','.join(COLS)} FROM records"
    os.makedirs(out_dir, exist_ok=True)

    permits = await fetch(db, f"{sel} WHERE category='permit' AND event_date>=?", (date_days_ago(p.get("days", 30)),))
    big = [r for r in permits
           if money(r["amount"]) >= min_cost and any(k in (r["party"] or "").upper() for k in types)]
    big.sort(key=lambda r: money(r["amount"]), reverse=True)
    write(os.path.join(out_dir, "permit_big_projects.csv"), COLS, [[r[c] for c in COLS] for r in big])

    viol = await fetch(db, f"{sel} WHERE category='code_violation' AND event_date>=?", (date_days_ago(v.get("days", 30)),))
    open_v = [r for r in viol if (r["status"] or "").upper() in open_statuses]
    open_v.sort(key=lambda r: r["event_date"] or "", reverse=True)
    write(os.path.join(out_dir, "code_violation_open.csv"), COLS, [[r[c] for c in COLS] for r in open_v])

    cutoff = iso_days_ago(t.get("days", 7))
    rows = []
    new_big = [r for r in big if r["first_seen"] >= cutoff][: t.get("max_permits", 50)]
    for i, r in enumerate(new_big, 1):
        rows.append(["big_permit", i, r["address"], r["event_date"],
                     f"{r['party']} - {(r['description'] or '')[:200]}", money(r["amount"]),
                     r["jurisdiction"], r["record_id"]])

    by_addr = {}
    for r in open_v:
        key = (r["address"] or "").strip().upper()
        if key:
            by_addr.setdefault(key, []).append(r)
    min_open = t.get("min_open_violations", 2)
    distressed = sorted((g for g in by_addr.values() if len(g) >= min_open),
                        key=lambda g: (len(g), g[0]["event_date"] or ""), reverse=True)
    for i, g in enumerate(distressed[: t.get("max_distressed", 50)], 1):
        latest = g[0]
        rows.append(["distressed_property", i, latest["address"], latest["event_date"],
                     f"{len(g)} open violations; latest: {(latest['description'] or '')[:150]}", len(g),
                     latest["jurisdiction"], latest["record_id"]])

    write(os.path.join(out_dir, "top_leads_this_week.csv"),
          ["lead_type", "rank", "address", "latest_date", "detail", "value", "jurisdiction", "source_record"], rows)
