"""
Backtest for the GE Dip Scanner: replays the scanner's own rules over the last few weeks of real
GE prices, and reports how often its suggested bids would have filled and what they'd have made.

Run: double-click "GE Dip Backtest.bat" (or: python backtest.py [days], default 30).
The first run downloads the prices, one request a second as the wiki asks: about 2.5 hours for
30 days. Later runs only download days they don't have yet. The first 7 days are only used as
history (the scanner compares against a 7-day average), so 30 days gives about 23 days of results.

Items: the price range the scanner uses for your cash stack, with enough trades a day.
Results: backtest_results.txt (summary) and backtest_dips.csv (one line per dip).
Limits: prices are 5-minute averages of the trades RuneLite sees, and it can't know whether
another lowballer was ahead of you in line. Good for comparing rules, rough on exact odds.
"""
import bisect
import csv
import gzip
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

import dip_scanner as ds

DATA = os.path.join(ds.HERE, "backtest_data")
RESULTS = os.path.join(ds.HERE, "backtest_results.txt")
DIPS_CSV = os.path.join(ds.HERE, "backtest_dips.csv")
MIN_STORED_PRICE = 50_000       # cheaper items aren't saved at all
WARMUP_DAYS = 7                 # history needed before the first replayed scan
SCAN_EVERY = 3600               # replay a scan once an hour
WINDOWS = (6, 24, 48)           # hours a bid is left in to fill
DEPTHS = ((5, 10), (10, 15), (15, 25), (25, 35), (35, 101))
PRICES = ((0, 500e3), (500e3, 1e6), (1e6, 2e6), (2e6, 5e6), (5e6, 1e12))
RULES = (                       # (name, % above the dip, never more than this % under normal)
    ("1% above the dip (now)", 1, None),
    ("2% above the dip", 2, None),
    ("3% above the dip", 3, None),
    ("1%, at most 25% under normal", 1, 25),
    ("1%, at most 20% under normal", 1, 20),
    ("1%, at most 15% under normal", 1, 15),
)


# ----------------------------------------------------------------------------- download

def day_path(day):
    return os.path.join(DATA, datetime.fromtimestamp(day, timezone.utc).strftime("%Y-%m-%d") + ".json.gz")


def download(days, cfg):
    """Saves each whole UTC day of 5-minute prices (all items at least MIN_STORED_PRICE)."""
    os.makedirs(DATA, exist_ok=True)
    today = int(time.time()) // 86400 * 86400
    all_days = [today - 86400 * k for k in range(days, 0, -1)]
    todo = [d for d in all_days if not os.path.exists(day_path(d))]
    if todo:
        print(f"Downloading {len(todo)} day(s) of prices: about {len(todo) * 288 / 3600:.1f} hours. "
              "You can stop (close the window) and rerun later; finished days are kept.")
    for n, day in enumerate(todo, 1):
        buckets = {}
        for ts in range(day, day + 86400, 300):
            started = time.time()
            data = ds.fetch("5m", cfg, {"timestamp": ts}, retries=6).get("data", {})
            buckets[ts] = {i: [v.get("avgLowPrice"), v.get("avgHighPrice"),
                               v.get("lowPriceVolume") or 0, v.get("highPriceVolume") or 0]
                           for i, v in data.items()
                           if max(v.get("avgLowPrice") or 0, v.get("avgHighPrice") or 0) >= MIN_STORED_PRICE}
            time.sleep(max(0.0, 1 - (time.time() - started)))
        tmp = day_path(day) + ".part"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(buckets, f)
        os.replace(tmp, day_path(day))
        print(f"  day {n}/{len(todo)} saved")
    return all_days


def load(all_days, cfg):
    """Price points per item for the items worth checking: busy enough and pricey enough."""
    raw = {}
    for day in all_days:
        with gzip.open(day_path(day), "rt", encoding="utf-8") as f:
            for ts, items in json.load(f).items():
                for i, (lo, hi, lv, hv) in items.items():
                    raw.setdefault(int(i), []).append((int(ts), lo, hi, lv, hv))
    days = len(all_days)
    # The same price range the scanner uses for your cash stack (100k to 50m if none is set).
    lo_price, hi_price = ds.price_band(cfg) or (100_000, 50_000_000)
    series = {}
    for i, pts in raw.items():
        lows = [p[1] for p in pts if p[1]]
        if not lows or not lo_price <= statistics.median(lows) <= hi_price:
            continue
        if sum(p[3] + p[4] for p in pts) / days < cfg["min_daily_volume"]:
            continue
        pts.sort()
        series[i] = [{"timestamp": t, "avgLowPrice": lo, "avgHighPrice": hi,
                      "lowPriceVolume": lv, "highPriceVolume": hv} for t, lo, hi, lv, hv in pts]
    return series


# ----------------------------------------------------------------------------- replay

def hourly(pts):
    """1-hour averages, like the API's 1h timeseries."""
    hours = {}
    for p in pts:
        h = hours.setdefault(p["timestamp"] // 3600 * 3600, ([], []))
        if p["avgLowPrice"]:
            h[0].append(p["avgLowPrice"])
        if p["avgHighPrice"]:
            h[1].append(p["avgHighPrice"])
    return [{"timestamp": t, "avgLowPrice": statistics.mean(lo) if lo else None,
             "avgHighPrice": statistics.mean(hi) if hi else None} for t, (lo, hi) in sorted(hours.items())]


def bid_for(dip, beat, cap):
    bid = ds.price_bid(dip["low"] / (1 - beat / 100))
    if cap:
        normal = dip["low"] / (1 - dip["pct"] / 100)
        bid = max(bid, ds.price_bid(normal * (1 - cap / 100)))
    return bid


def outcome(pts, times, start, bid, cfg):
    """Hours until a bid placed at `start` fills (None if not within 48h), and what it then sold for."""
    for k in range(bisect.bisect_right(times, start), len(pts)):
        p = pts[k]
        if p["timestamp"] > start + max(WINDOWS) * 3600:
            break
        low = ds.dip_price(p)[0]
        if low and low <= bid:
            fill = p["timestamp"]
            after = pts[k + 1:bisect.bisect_right(times, fill + 6 * 3600)]
            highs = [q["avgHighPrice"] for q in after if q["avgHighPrice"]]
            sold = statistics.median(highs) * (1 - cfg["undercut_pct"] / 100) if highs else None
            return (fill - start) / 3600, sold
    return None, None


def replay(series, names, cfg):
    """Every dip the scanner would have listed, the first hour it would have listed it."""
    first = min(p["timestamp"] for pts in series.values() for p in pts) // 86400 * 86400
    last = max(p["timestamp"] for pts in series.values() for p in pts)
    out = []
    for n, (item_id, pts) in enumerate(series.items(), 1):
        if n % 25 == 0:
            print(f"  replaying item {n}/{len(series)}")
        hours = hourly(pts)
        times = [p["timestamp"] for p in pts]
        seen = set()
        lo_i = hi_i = 0
        for t in range(first + WARMUP_DAYS * 86400, last - max(WINDOWS) * 3600, SCAN_EVERY):
            while lo_i < len(pts) and pts[lo_i]["timestamp"] <= t - 86400:
                lo_i += 1
            while hi_i < len(pts) and pts[hi_i]["timestamp"] <= t:
                hi_i += 1
            window = pts[lo_i:hi_i]
            # Quick skip: nothing in the last hour came close to a dip.
            recent = [x for x in (ds.dip_price(p)[0] for p in window if p["timestamp"] > t - SCAN_EVERY) if x]
            lows = [p["avgLowPrice"] for p in window if p["avgLowPrice"]]
            if not recent or len(lows) < 12 or min(recent) > statistics.median(lows) * 0.96:
                continue
            past = [h for h in hours if t - cfg["long_baseline_days"] * 86400 <= h["timestamp"] <= t - 3600]
            r = ds.analyze({"id": item_id, "name": names.get(item_id, str(item_id))}, window, cfg, t, past)
            if not r or not r["pick"] or (item_id, r["pick"]["dip"]["ts"]) in seen:
                continue
            dip = r["pick"]["dip"]
            seen.add((item_id, dip["ts"]))
            row = {"item": names.get(item_id, str(item_id)), "listed_at": t, "dip_time": dip["ts"],
                   "depth_pct": dip["pct"], "dip_low": dip["low"], "normal": dip["low"] / (1 - dip["pct"] / 100),
                   "est_sale": r["sell_at"], "est_profit": r["pick"]["profit"]}
            for name, beat, cap in RULES:
                bid = bid_for(dip, beat, cap)
                hours_to_fill, sold = outcome(pts, times, t, bid, cfg)
                row[name] = (bid, hours_to_fill, sold, ds.sell_after_tax(sold) - bid if sold else None)
            out.append(row)
    return out


# ----------------------------------------------------------------------------- report

def pct(a, b):
    return f"{a / b * 100:3.0f}%" if b else "   -"


def summary(rows, all_days, n_items):
    now_rule = RULES[0][0]
    span = f"{datetime.fromtimestamp(all_days[WARMUP_DAYS], timezone.utc):%d %b} to " \
           f"{datetime.fromtimestamp(all_days[-1] + 86400, timezone.utc):%d %b %Y}"
    lines = [f"GE Dip Scanner backtest, {span}", f"{n_items} items checked, {len(rows)} dips the scanner would have listed.", ""]

    def filled(r, rule, hours):
        h = r[rule][1]
        return h is not None and h <= hours

    def odds_table(title, groups):
        lines.append(title)
        lines.append(f"  {'':16}{'dips':>6}" + "".join(f"{f'in {w}h':>9}" for w in WINDOWS))
        for label, rs in groups:
            lines.append(f"  {label:16}{len(rs):>6}" + "".join(f"{pct(sum(filled(r, now_rule, w) for r in rs), len(rs)):>9}" for w in WINDOWS))
        lines.append("")

    odds_table("Chance the current bid (1% above the dip) fills, by how deep the dip was:",
               [(f"{a}-{b}%" if b < 101 else f"{a}%+", [r for r in rows if a <= r["depth_pct"] < b]) for a, b in DEPTHS])
    odds_table("Same, by the item's normal price:",
               [(f"{ds.gp(a)}-{ds.gp(b)}" if b < 1e12 else f"{ds.gp(a)}+", [r for r in rows if a <= r["normal"] < b])
                for a, b in PRICES if any(a <= r["normal"] < b for r in rows)])

    lines.append("Bid rules compared (bid left in for 48h; profit is what it actually sold for after, minus tax):")
    lines.append(f"  {'':32}{'filled':>8}{'made money':>12}{'median/flip':>13}{'avg per dip listed':>20}")
    for name, _, _ in RULES:
        profits = [r[name][3] for r in rows if r[name][3] is not None]
        lines.append(f"  {name:32}{pct(len(profits), len(rows)):>8}"
                     f"{pct(sum(p > 0 for p in profits), len(profits)):>12}"
                     f"{ds.gp(statistics.median(profits)) if profits else '-':>13}"
                     f"{ds.gp(sum(profits) / len(rows)) if rows else '-':>20}")
    lines.append("")

    errs = [(r[now_rule][2] - r["est_sale"]) / r["est_sale"] * 100 for r in rows if r[now_rule][2]]
    if errs:
        lines.append(f"Est. sale vs what it actually sold for after a fill: median {statistics.median(errs):+.1f}% "
                     f"(negative means Est. sale was too high), over {len(errs)} fills.")
    return "\n".join(lines) + "\n"


def write_csv(rows):
    with open(DIPS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["item", "listed_at", "dip_time", "depth_pct", "dip_low", "normal", "est_sale", "est_profit"]
                   + [f"{name}: {k}" for name, _, _ in RULES for k in ("bid", "hours_to_fill", "sold_for", "profit")])
        for r in rows:
            w.writerow([r["item"], datetime.fromtimestamp(r["listed_at"]).isoformat(timespec="minutes"),
                        datetime.fromtimestamp(r["dip_time"]).isoformat(timespec="minutes"),
                        round(r["depth_pct"], 1), round(r["dip_low"]), round(r["normal"]), round(r["est_sale"]),
                        round(r["est_profit"])]
                       + [round(x, 1) if isinstance(x, float) else x for name, _, _ in RULES for x in r[name]])


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    if days <= WARMUP_DAYS:
        sys.exit(f"Use more than {WARMUP_DAYS} days.")
    cfg = ds.load_config()
    all_days = download(days, cfg)
    print("Loading prices...")
    series = load(all_days, cfg)
    names = {m["id"]: m["name"] for m in ds.fetch("mapping", cfg)}
    print(f"Replaying the scanner over {len(series)} items...")
    rows = replay(series, names, cfg)
    text = summary(rows, all_days, len(series))
    with open(RESULTS, "w", encoding="utf-8") as f:
        f.write(text)
    write_csv(rows)
    print("\n" + text)
    print(f"Saved to {os.path.basename(RESULTS)} and {os.path.basename(DIPS_CSV)}.")


if __name__ == "__main__":
    main()
