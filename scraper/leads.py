import csv, os, re, datetime, yaml

COLS = ["source_id", "record_id", "category", "jurisdiction", "address", "event_date",
        "status", "description", "party", "amount", "url", "first_seen"]


def money(s):
    try:
        return float(re.sub(r"[^0-9.]", "", s or "") or 0)
    except ValueError:
        return 0.0


CITY_SUFFIXES = ["CHICAGO"]     # city names to strip from the END of addresses so records for one property match


def norm_addr(a):
    a = re.sub(r"[.,#]", " ", (a or "").upper())
    a = re.sub(r"\s+", " ", a).strip()
    a = re.sub(r"\s+\d{5}(-\d{4})?$", "", a)                 # trailing ZIP
    for c in CITY_SUFFIXES:
        if a.endswith(" " + c):
            a = a[: -len(c) - 1]
    return a


def good_addr(a):
    """Junk filter: a usable lead needs a street number and a street name."""
    return bool(re.match(r"^\d+\w*\s+\S+", a))


def date_days_ago(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


def write(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig keeps Excel happy
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


async def fetch(db, category):
    cur = await db.execute(f"SELECT {','.join(COLS)} FROM records WHERE category=?", (category,))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in await cur.fetchall()]


def up(items):
    return [str(x).upper() for x in items]


async def build_leads(db, out_dir, cfg_path="config/leads.yaml"):
    cfg = yaml.safe_load(open(cfg_path)) if os.path.exists(cfg_path) else {}
    pc, vc, wc = cfg.get("permits", {}), cfg.get("violations", {}), cfg.get("weekly", {})
    today = datetime.date.today().isoformat()
    global CITY_SUFFIXES
    CITY_SUFFIXES = up(wc.get("strip_city_suffixes", ["CHICAGO"]))
    os.makedirs(out_dir, exist_ok=True)

    # ---------- 1) Big permits: ONE row per property ----------
    types = up(pc.get("types", ["NEW CONSTRUCTION", "RENOVATION", "ALTERATION", "WRECKING", "DEMOLITION"]))
    skip = up(pc.get("exclude_statuses", ["CANCEL", "REVOK", "VOID", "WITHDRAWN", "EXPIRED", "DISAPPROV", "COMPLETE"]))
    min_cost, since = pc.get("min_cost", 100000), date_days_ago(pc.get("days", 30))
    groups = {}
    for r in await fetch(db, "permit"):
        a = norm_addr(r["address"])
        if not good_addr(a) or (r["event_date"] or "") < since:
            continue
        if money(r["amount"]) < min_cost:
            continue
        if not any(k in (r["party"] or "").upper() for k in types):
            continue
        if any(k in (r["status"] or "").upper() for k in skip):
            continue
        groups.setdefault(a, []).append(r)
    big = []
    for a, g in groups.items():
        best = max(g, key=lambda r: money(r["amount"]))     # repeats at one address collapse to the biggest
        big.append((a, best, len(g)))
    big.sort(key=lambda t: money(t[1]["amount"]), reverse=True)
    write(os.path.join(out_dir, "permit_big_projects.csv"),
          ["address", "jurisdiction", "permit_type", "estimated_cost", "permit_date", "status",
           "description", "permits_at_address", "source_record", "first_seen"],
          [[a, r["jurisdiction"], r["party"], money(r["amount"]), r["event_date"], r["status"],
            r["description"], n, r["record_id"], r["first_seen"]] for a, r, n in big])

    # ---------- 2) Open code violations: ONE row per property ----------
    open_words = up(vc.get("open_statuses", ["OPEN"]))
    vsince = date_days_ago(vc.get("days", 30))
    vg = {}
    for r in await fetch(db, "code_violation"):
        a = norm_addr(r["address"])
        if not good_addr(a) or (r["event_date"] or "") < vsince:
            continue
        if not any(k in (r["status"] or "").upper() for k in open_words):
            continue
        vg.setdefault(a, []).append(r)
    viol = []
    for a, g in vg.items():
        g.sort(key=lambda r: r["event_date"] or "", reverse=True)
        descs = []
        for r in g:
            d = (r["description"] or "").strip()
            if d and d not in descs:            # identical descriptions count once
                descs.append(d)
        viol.append((a, g, descs, max(len(descs), 1)))
    viol.sort(key=lambda t: (t[3], t[1][0]["event_date"] or ""), reverse=True)
    write(os.path.join(out_dir, "code_violation_open.csv"),
          ["address", "jurisdiction", "open_violations", "latest_date", "latest_description",
           "other_descriptions", "source_record"],
          [[a, g[0]["jurisdiction"], n, g[0]["event_date"], (d[0] if d else ""), " | ".join(d[1:4]),
            g[0]["record_id"]] for a, g, d, n in viol])

    # ---------- 3) Upcoming foreclosure auctions: ONE row per property ----------
    pf = {}
    for r in await fetch(db, "preforeclosure"):
        a = norm_addr(r["address"])
        if not good_addr(a) or (r["event_date"] or "") < today:     # past sales are useless
            continue
        if a not in pf or r["event_date"] < pf[a]["event_date"]:
            pf[a] = r
    write(os.path.join(out_dir, "preforeclosure_upcoming.csv"),
          ["address", "jurisdiction", "sale_date", "opening_bid", "source", "source_record"],
          [[a, r["jurisdiction"], r["event_date"], r["amount"], r["source_id"], r["record_id"]]
           for a, r in sorted(pf.items(), key=lambda t: t[1]["event_date"])])

    # ---------- 4) Weekly leads: one row per property, nothing sent twice ----------
    await db.execute("CREATE TABLE IF NOT EXISTS delivered (lead_key TEXT PRIMARY KEY, delivered_at TEXT)")
    cur = await db.execute("SELECT lead_key FROM delivered")
    done = {r[0] for r in await cur.fetchall()}
    leads = {}

    def add(addr, jur, key, audience, signal, score, detail, rec):
        if key in done:
            return
        L = leads.setdefault(addr, {"jur": jur, "aud": [], "sig": [], "score": 0, "det": [], "keys": [], "rec": []})
        if audience not in L["aud"]:
            L["aud"].append(audience)
        L["sig"].append(signal); L["score"] += score; L["det"].append(detail)
        L["keys"].append(key); L["rec"].append(rec)

    for a, r, n in big:
        c = money(r["amount"])
        add(a, r["jurisdiction"], f"permit|{r['source_id']}|{r['record_id']}", "builders/contractors",
            f"big permit ${c:,.0f}", 2 + (2 if c >= 1_000_000 else 1 if c >= 500_000 else 0),
            f"{r['party']}: {(r['description'] or '')[:150]}", r["record_id"])
    min_open = wc.get("min_open_violations", 2)
    for a, g, d, n in viol:
        if n >= min_open:
            add(a, g[0]["jurisdiction"], f"distress|{a}", "wholesalers/investors",
                f"{n} open violations", min(n, 5), f"latest: {(d[0] if d else '')[:150]}", g[0]["record_id"])
    for a, r in pf.items():
        add(a, r["jurisdiction"], f"foreclosure|{a}", "wholesalers/investors",
            f"foreclosure auction {r['event_date']}", 4, f"opening bid {r['amount']}", r["record_id"])

    ranked = sorted(leads.items(), key=lambda t: (-t[1]["score"], t[0]))[: wc.get("max_leads", 200)]
    header = ["rank", "address", "jurisdiction", "good_for", "signals", "score", "details", "source_records"]
    rows = [[i, a, L["jur"], "; ".join(L["aud"]), "; ".join(L["sig"]), L["score"], " | ".join(L["det"]),
             ",".join(L["rec"])] for i, (a, L) in enumerate(ranked, 1)]
    if rows:   # only write a file when there is something NEW to send
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        write(os.path.join(out_dir, "weekly", f"weekly_leads_{stamp}.csv"), header, rows)
        write(os.path.join(out_dir, "weekly_leads_latest.csv"), header, rows)
        if wc.get("mark_delivered", True):
            ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            for a, L in ranked:
                for k in L["keys"]:
                    await db.execute("INSERT OR IGNORE INTO delivered VALUES (?,?)", (k, ts))
            await db.commit()
