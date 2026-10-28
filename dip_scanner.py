#!/usr/bin/env python3
"""
OSRS instasell spike scanner.

  python dip_scanner.py serve   Easiest: opens a local page (http://localhost:8765) with a Scan button,
                                a depth slider and auto-refresh. "Spike Scanner.bat" runs this.
  python dip_scanner.py scan    One-click audit: finds deep, short instasell spikes in the last
                                24h, suggests a lowball bid per item, and opens an HTML report
                                with a chart for every item.
  python dip_scanner.py watch   Leaves a loop running. Every few minutes it checks the latest
                                instasell price of every watched item and pushes a phone
                                notification (ntfy app) when one spikes far below its last-hour price.
  python dip_scanner.py test-alert   Sends one test notification to your phone.

A "spike" is a short, sharp dump that bounces back, measured against the price AROUND it
(the median instasell of the surrounding 90 minutes), so slow drifts and noise don't count:
  - the 5-minute average instasell is at least spike_min_pct below that local price,
  - it lasts no longer than spike_max_minutes,
  - and the price recovers to within half that depth within spike_recover_minutes.
Items whose price normally jumps around a lot are flagged "choppy" and ranked lower.

Data: OSRS Wiki real-time prices API (prices.runescape.wiki), the same data as the site.
No installs needed, Python 3.8+ standard library only.
"""
import io
import json
import shutil
import subprocess
import zipfile
import threading
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ZIP = "https://github.com/drfino/osrs-dip-scanner/archive/refs/heads/master.zip"
# Only these files are ever replaced by an update. Your config.json and watchlist.txt are yours.
UPDATABLE = ("dip_scanner.py", "README.md", "watchlist.example.txt")
API = "https://prices.runescape.wiki/api/v1/osrs"
ITEM_PAGE = "https://prices.runescape.wiki/osrs/item/{}"
GE_TAX = 0.02          # 2% sell tax
GE_TAX_CAP = 5_000_000

DEFAULT_CONFIG = {
    "contact": "osrs-dip-scanner personal watchlist",
    "spike_min_pct": 10,
    "local_window_minutes": 90,
    "long_baseline_days": 7,
    "spike_max_age_hours": 12,
    "choppy_noise_pct": 3,
    "spike_max_minutes": 30,
    "spike_recover_minutes": 60,
    "spike_bid_levels_pct": [10, 15, 20, 25, 30, 40],
    "spike_min_fills": 1,
    "watch_every_minutes": 3,
    "alert_spike_pct": 15,
    "alert_max_age_minutes": 10,
    "ntfy_topic": "",
    # Auto watchlist from your cash stack (0 = only use watchlist.txt).
    "cash_stack": 0,
    "min_item_pct": 5,         # cheapest item to watch, as % of your stack
    "max_offer_pct": 29,       # most one offer may use, as % of your stack (also the priciest item)
    "min_daily_volume": 25,    # trades per day, so offers can actually fill
    "max_auto_items": 80,      # keep scans quick: top items by daily gp traded
}


# ----------------------------------------------------------------------------- plumbing

def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    path = os.path.join(HERE, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


def fetch(path, cfg, params=None, retries=3):
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    # The wiki asks every tool to send a descriptive User-Agent.
    req = urllib.request.Request(url, headers={"User-Agent": f"osrs-dip-scanner/1.1 ({cfg['contact']})"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # network hiccup or rate limit: back off and retry
            if attempt == retries - 1:
                raise RuntimeError(f"Could not reach the prices API ({url}): {e}")
            time.sleep(2 * (attempt + 1))


def gp(n):
    if n is None:
        return "-"
    n = float(n)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000:
        return f"{sign}{n / 1_000_000:.2f}m"
    if n >= 10_000:
        return f"{sign}{n / 1000:.0f}k"
    return f"{sign}{n:,.0f}"


def ago(ts, now=None):
    if not ts:
        return "-"
    s = (now or time.time()) - ts
    if s < 3600:
        return f"{int(s // 60)}m ago"
    if s < 86400:
        return f"{s / 3600:.1f}h ago"
    return f"{s / 86400:.1f}d ago"


def round_bid(p):
    """Round a bid down to a clean number people actually type."""
    step = 10_000 if p >= 10_000_000 else 1_000 if p >= 1_000_000 else 100 if p >= 10_000 else 1
    return int(p // step * step)


def sell_after_tax(price):
    return price - min(price * GE_TAX, GE_TAX_CAP)


# ----------------------------------------------------------------------------- watchlist

def offer_budget(cfg):
    """The most one lowball offer may use. None if no cash stack is set."""
    cash = cfg.get("cash_stack") or 0
    return cash * cfg["max_offer_pct"] / 100 if cash > 0 else None


def price_band(cfg):
    """Items worth watching for this stack: from min_item_pct to max_offer_pct of it."""
    cash = cfg.get("cash_stack") or 0
    if cash <= 0:
        return None
    return cash * cfg["min_item_pct"] / 100, cash * cfg["max_offer_pct"] / 100


def load_watchlist(cfg):
    """Your watchlist.txt items, plus (if a cash stack is set) every item that fits your budget."""
    mapping = fetch("mapping", cfg)
    by_name = {m["name"].lower(): m for m in mapping}
    by_id = {m["id"]: m for m in mapping}

    manual, missing = [], []
    path = os.path.join(HERE, "watchlist.txt")
    if not os.path.exists(path):
        shutil.copyfile(os.path.join(HERE, "watchlist.example.txt"), path)
    with open(path, encoding="utf-8") as f:
        for line in f:
            name = line.strip()
            if not name or name.startswith("#"):
                continue
            m = by_name.get(name.lower())
            (manual if m else missing).append(m or name)
    if missing:
        print("These watchlist names were not found (check spelling against the wiki):")
        for n in missing:
            print("   -", n)

    band = price_band(cfg)
    auto = []
    if band:
        lo, hi = band
        day = fetch("24h", cfg)["data"]
        for sid, d in day.items():
            m = by_id.get(int(sid))
            low = d.get("avgLowPrice")
            vol = (d.get("highPriceVolume") or 0) + (d.get("lowPriceVolume") or 0)
            if m and low and lo <= low <= hi and vol >= cfg["min_daily_volume"]:
                auto.append((low * vol, m))
        auto.sort(key=lambda x: x[0], reverse=True)
        auto = [m for _, m in auto[:cfg["max_auto_items"]]]

    seen, items = set(), []
    for m in auto + manual:
        if m["id"] not in seen:
            seen.add(m["id"])
            items.append(m)
    cfg["_watch_info"] = {
        "auto": len(auto), "manual": len([m for m in manual if m["id"] not in {a["id"] for a in auto}]),
        "band": band,
    }
    return items


# ----------------------------------------------------------------------------- analysis

def local_prices(pts, cfg):
    """
    For every 5-minute bucket, the 'normal' instasell price around it: the median of the
    surrounding local_window_minutes on each side, skipping the 15 minutes either side so a
    spike can't drag its own baseline down.
    """
    win = cfg["local_window_minutes"] * 60
    gap = 15 * 60
    lows = [(p["timestamp"], p["avgLowPrice"]) for p in pts if p.get("avgLowPrice")]
    out = {}
    for t, _ in lows:
        near = [v for (u, v) in lows if gap < abs(u - t) <= win]
        if len(near) >= 4:
            out[t] = statistics.median(near)
    return out


def find_spikes(pts, local, cfg, now, long_base=None):
    """
    Group 5-minute buckets that are spike_min_pct+ under their reference price into events, then
    keep only the ones that are recent, short and bounce back. The reference is the LOWER of the
    local price (around the dip) and the long-run average, so a dip that only falls back to where
    the price was days ago doesn't count. Returns (spikes, rejected_count, stale_count).
    """
    thr = cfg["spike_min_pct"] / 100
    max_len = cfg["spike_max_minutes"] * 60
    recover = cfg["spike_recover_minutes"] * 60

    events, cur = [], None
    for p in pts:
        low, t = p.get("avgLowPrice"), p["timestamp"]
        loc = local.get(t)
        if not low or not loc:
            continue
        base = min(loc, long_base) if long_base else loc
        if low > base * (1 - thr):
            continue
        pct = (base - low) / base * 100
        if cur and t - cur["end"] <= 900:
            cur["end"] = t
            if pct > cur["pct"]:
                cur.update(low=low, ts=t, pct=pct, base=base, local=loc)
        else:
            cur = {"ts": t, "start": t, "end": t, "low": low, "pct": pct, "base": base, "local": loc}
            events.append(cur)

    spikes, rejected, stale = [], 0, 0
    max_age = cfg["spike_max_age_hours"] * 3600
    for e in events:
        if now - e["ts"] > max_age:
            stale += 1               # too old to say much about the price right now
            continue
        if e["end"] - e["start"] > max_len:
            rejected += 1            # stayed low too long: a price drop, not a spike
            continue
        recovered_line = e["local"] * (1 - thr / 2)
        after = [p["avgLowPrice"] for p in pts
                 if e["end"] < p["timestamp"] <= e["end"] + recover and p.get("avgLowPrice")]
        if after and max(after) >= recovered_line:
            e["ongoing"] = False
        elif now - e["end"] <= recover:
            e["ongoing"] = True      # too recent to know if it recovers yet: keep it, flag it
        else:
            rejected += 1            # never bounced back: not a spike
            continue
        spikes.append(e)
    return spikes, rejected, stale


def long_run_price(hourly, cfg, now):
    """Median instasell over the last long_baseline_days, from 1-hour averages."""
    since = now - cfg["long_baseline_days"] * 86400
    lows = [p["avgLowPrice"] for p in hourly if p["timestamp"] >= since and p.get("avgLowPrice")]
    return statistics.median(lows) if len(lows) >= 24 else None


def analyze(item, recent, latest, cfg, now, hourly=()):
    pts = [p for p in recent if p["timestamp"] >= now - 86400]
    lows = [p["avgLowPrice"] for p in pts if p.get("avgLowPrice")]
    if len(lows) < 12:
        return None
    local = local_prices(pts, cfg)
    long_base = long_run_price(hourly, cfg, now)
    spikes, rejected, stale = find_spikes(pts, local, cfg, now, long_base)

    # Typical wiggle around the local price, ignoring the spikes themselves: is this item calm?
    spike_ts = {p["timestamp"] for e in spikes for p in pts if e["start"] - 300 <= p["timestamp"] <= e["end"] + 300}
    devs = [abs(p["avgLowPrice"] - local[p["timestamp"]]) / local[p["timestamp"]] * 100
            for p in pts if p.get("avgLowPrice") and p["timestamp"] in local and p["timestamp"] not in spike_ts]
    noise = statistics.median(devs) if devs else 0
    choppy = noise > cfg["choppy_noise_pct"]

    # "Normal now": what it actually sells for these days, the median of the last 3 hours.
    recent_lows = [p["avgLowPrice"] for p in pts if p["timestamp"] >= now - 3 * 3600 and p.get("avgLowPrice")]
    normal = statistics.median(recent_lows) if len(recent_lows) >= 4 else statistics.median(lows)

    # Bids and profit use the lower of today's price and the long-run average, so a temporary
    # high never makes a bid look better than it is.
    ref = min(normal, long_base) if long_base else normal

    # Bid ladder: a standing offer N% under the reference catches every spike at least N% deep.
    ladder = []
    for lvl in cfg["spike_bid_levels_pct"]:
        bid = round_bid(ref * (1 - lvl / 100))
        fills = sum(1 for e in spikes if e["pct"] >= lvl)
        profit = sell_after_tax(ref) - bid  # resell at the reference price, after tax
        ladder.append({"pct": lvl, "bid": bid, "fills": fills, "profit": profit})

    ok = [r for r in ladder if r["fills"] >= cfg["spike_min_fills"] and r["profit"] > 0]
    pick = ok[-1] if ok else None            # deepest bid that still caught enough spikes
    score = (pick["profit"] * pick["fills"] if pick else 0) * (0.3 if choppy else 1)
    budget = offer_budget(cfg)
    if pick and budget and pick["bid"] > budget:
        score *= 0.1                         # can't afford it in one offer: list it, but below the rest

    lt = latest.get(str(item["id"]), {}) if latest else {}
    now_low, now_low_ts = lt.get("low"), lt.get("lowTime")
    now_pct = (normal - now_low) / normal * 100 if now_low else None

    deepest = max(spikes, key=lambda e: e["pct"]) if spikes else None
    return {
        "item": item, "base": normal, "long_base": long_base, "ref": ref, "stale": stale,
        "spikes": spikes, "rejected": rejected, "deepest": deepest,
        "ladder": ladder, "pick": pick, "score": score, "now_low": now_low, "now_low_ts": now_low_ts,
        "now_pct": now_pct, "pts": pts, "noise": noise, "choppy": choppy,
    }


# ----------------------------------------------------------------------------- scan mode

def run_scan(cfg, progress=None):
    """Scan the whole watchlist. progress(done, total, name) is called per item."""
    items = load_watchlist(cfg)
    latest = fetch("latest", cfg)["data"]
    now = time.time()
    results = []
    for i, it in enumerate(items, 1):
        if progress:
            progress(i, len(items), it["name"])
        recent = fetch("timeseries", cfg, {"timestep": "5m", "id": it["id"]}).get("data", [])
        time.sleep(0.15)  # be polite to the free API
        hourly = fetch("timeseries", cfg, {"timestep": "1h", "id": it["id"]}).get("data", [])
        time.sleep(0.15)
        r = analyze(it, recent, latest, cfg, now, hourly)
        if r:
            results.append(r)
    results.sort(key=lambda r: (r["score"], r["deepest"]["pct"] if r["deepest"] else 0), reverse=True)
    return results, now


def scan(cfg, open_report=True):
    def show(i, n, name):
        print(f"\r  Checking {i}/{n}: {name[:40]:<40}", end="", flush=True)
    results, now = run_scan(cfg, show)
    print("\r" + " " * 70 + "\r", end="")
    if not results:
        print("Watchlist is empty.")
        return
    print_table(results, cfg, now)
    path = write_report(results, cfg, now)
    print(f"\nReport saved: {path}")
    if open_report:
        webbrowser.open("file://" + path)


def print_table(results, cfg, now):
    with_spikes = [r for r in results if r["spikes"]]
    print(f"\nSpikes of {cfg['spike_min_pct']}%+ below the surrounding price, last 24h, "
          f"that bounced back within {cfg['spike_recover_minutes']} min.\n")
    if not with_spikes:
        print("No spikes on any item. Quiet day, or lower spike_min_pct in config.json.")
        return
    hdr = f"{'Item':<32}{'Normal now':>12}{'Spikes':>8}{'Deepest':>18}{'Suggested bid':>15}{'Caught':>8}{'Profit/ea':>11}"
    print(hdr)
    print("-" * len(hdr))
    for r in with_spikes:
        d, p = r["deepest"], r["pick"]
        deep = f"-{d['pct']:.1f}% {ago(d['ts'], now)}"
        name = r['item']['name'][:28] + (" (choppy)" if r["choppy"] else "")
        print(f"{name[:31]:<32}{gp(r['base']):>12}{len(r['spikes']):>8}{deep:>18}"
              f"{gp(p['bid']) if p else '-':>15}{(p['fills'] if p else 0):>8}{gp(p['profit']) if p else '-':>11}")
    print(f"\n{len(results) - len(with_spikes)} other items had no spikes.")


# ----------------------------------------------------------------------------- HTML report

def sparkline(r, w=260, h=56):
    pts = r["pts"]
    if not pts:
        return ""
    t0, t1 = pts[0]["timestamp"], pts[-1]["timestamp"]
    vals = [p[k] for p in pts for k in ("avgLowPrice", "avgHighPrice") if p.get(k)] + [r["base"]]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad

    def y_of(v):
        return h - (v - lo) / (hi - lo) * h

    def line(k, cls):
        seg = " ".join(f"{(p['timestamp'] - t0) / max(t1 - t0, 1) * w:.1f},{y_of(p[k]):.1f}"
                       for p in pts if p.get(k))
        return f'<polyline class="{cls}" points="{seg}"/>'

    dots = ""
    for e in r["spikes"]:
        x = (e["ts"] - t0) / max(t1 - t0, 1) * w
        dots += (f'<circle cx="{x:.1f}" cy="{y_of(e["low"]):.1f}" r="3.5"><title>-{e["pct"]:.1f}% at '
                 f'{gp(e["low"])}</title></circle>')
    bid_line = ""
    if r["pick"]:
        by = y_of(r["pick"]["bid"])
        if 0 <= by <= h:
            bid_line = f'<line class="bid" x1="0" x2="{w}" y1="{by:.1f}" y2="{by:.1f}"/>'
    return (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" preserveAspectRatio="none" class="spark">'
            f'<line class="median" x1="0" x2="{w}" y1="{y_of(r["base"]):.1f}" y2="{y_of(r["base"]):.1f}"/>'
            f'{bid_line}{line("avgHighPrice", "hi")}{line("avgLowPrice", "lo")}{dots}</svg>')


def offer_qty(cfg, bid, limit):
    budget = offer_budget(cfg)
    if not budget or not bid:
        return None
    q = int(budget // bid)
    return min(q, limit) if limit else q


def row_html(r, cfg, now):
    it, d, p = r["item"], r["deepest"], r["pick"]
    qty = offer_qty(cfg, p["bid"], it.get("limit")) if p else None
    qty_html = ""
    if qty is not None:
        qty_html = (f"<div class='sub'>× {qty} = {gp(qty * p['bid'])}</div>" if qty > 0
                    else "<div class='sub over'>over one offer's budget</div>")
    live = ""
    if r["now_pct"] is not None and r["now_pct"] >= cfg["alert_spike_pct"] and \
            now - (r["now_low_ts"] or 0) <= cfg["alert_max_age_minutes"] * 60:
        live = f'<span class="chip live">Spiking now -{r["now_pct"]:.1f}%</span>'
    elif d and d.get("ongoing"):
        live = '<span class="chip warn">Recent, not recovered yet</span>'
    if r["choppy"]:
        live += f' <span class="chip warn" title="Normally moves about {r['noise']:.1f}% every 5 min">Choppy price</span>'
    ladder = "".join(
        f'<span class="rung{" on" if p and q["pct"] == p["pct"] else ""}" title="Bid {q["bid"]:,}: '
        f'caught {q["fills"]} spike(s), {gp(q["profit"])} profit each after tax">'
        f'-{q["pct"]}% <b>{q["fills"]}</b></span>'
        for q in r["ladder"])
    return f"""
<tr>
  <td class="name"><a href="{ITEM_PAGE.format(it['id'])}" target="_blank" rel="noopener">{escape(it['name'])}</a>
      <div class="sub">limit {it.get('limit') or '-'} · now sells {gp(r['now_low'])} {ago(r['now_low_ts'], now)}</div>{live}</td>
  <td>{sparkline(r)}</td>
  <td class="num">{gp(r['base'])}{f"<div class='sub'>7d avg {gp(r['long_base'])}</div>" if r.get('long_base') else ''}</td>
  <td class="num">{len(r['spikes'])}</td>
  <td class="num">{f"-{d['pct']:.1f}%<div class='sub'>{gp(d['low'])} · {ago(d['ts'], now)}</div>" if d else '<span class="sub">none</span>'}</td>
  <td class="num bidcell">{f"{p['bid']:,}{qty_html}<div class='sub'>caught {p['fills']}</div>" if p else '<span class="sub">-</span>'}</td>
  <td class="num">{gp(p['profit']) if p else '-'}</td>
  <td class="ladder">{ladder}</td>
</tr>"""


def write_report(results, cfg, now):
    path = os.path.join(HERE, "dip_report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_report(results, cfg, now))
    return path


def watch_text(cfg, n):
    info = cfg.get("_watch_info") or {}
    band = info.get("band")
    if not band:
        return f"{n} items from your watchlist"
    extra = f" + {info['manual']} from your list" if info.get("manual") else ""
    return (f"{n} items: {info.get('auto', 0)} priced {gp(band[0])}–{gp(band[1])} with "
            f"{cfg['min_daily_volume']}+ trades/day{extra}")


def build_report(results, cfg, now, toolbar=""):
    with_spikes = [r for r in results if r["spikes"]]
    quiet = [r for r in results if not r["spikes"]]
    rows = "".join(row_html(r, cfg, now) for r in with_spikes)
    if not rows:
        rows = ('<tr><td colspan="8" class="empty">No recent spikes on any item. '
                'Quiet market, or lower the spike depth.</td></tr>')
    quiet_list = ", ".join(escape(r["item"]["name"]) for r in quiet)

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spike Scanner</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=Libre+Franklin:wght@400;600;800&display=swap" rel="stylesheet">
<style>
:root {{
  --bg:#f3f1ec; --panel:#fffdf8; --fg:#24221d; --muted:#6f6a5e; --line:#ddd7ca;
  --buy:#1f9d55; --sell:#c4561b; --gold:#a57b12; --dip:#c0262d; --warn:#b07d10;
  --sans:"Libre Franklin",system-ui,sans-serif; --mono:"IBM Plex Mono",ui-monospace,monospace;
}}
@media (prefers-color-scheme: dark) {{ :root {{
  --bg:#1d1c19; --panel:#262420; --fg:#ece6d8; --muted:#a39c8b; --line:#3a372f;
  --buy:#3ccf74; --sell:#ee8243; --gold:#e3b341; --dip:#ff5a5f; --warn:#e3b341; color-scheme:dark; }} }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:14px/1.45 var(--sans);
  padding:28px max(16px, 3vw) 60px }}
header {{ display:flex; flex-wrap:wrap; gap:8px 24px; align-items:baseline; margin-bottom:6px }}
h1 {{ font-weight:800; font-size:26px; margin:0; letter-spacing:-.01em }}
.meta {{ color:var(--muted); font-size:13px }}
.legend {{ color:var(--muted); font-size:12.5px; margin:10px 0 18px; max-width:92ch }}
.legend i {{ display:inline-block; width:14px; height:2px; vertical-align:middle; margin:0 4px 0 10px }}
.wrap {{ overflow-x:auto; background:var(--panel); border:1px solid var(--line); border-radius:6px }}
table {{ border-collapse:collapse; width:100%; min-width:1050px }}
th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted);
  padding:10px 12px; border-bottom:1px solid var(--line); font-weight:600; white-space:nowrap }}
td {{ padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:middle }}
tr:last-child td {{ border-bottom:0 }}
td.empty {{ color:var(--muted); padding:24px 12px }}
.num {{ font-family:var(--mono); font-variant-numeric:tabular-nums; text-align:right; white-space:nowrap }}
th.num {{ text-align:right }}
.name {{ min-width:210px }}
.name a {{ color:var(--fg); font-weight:600; text-decoration:none }}
.name a:hover {{ text-decoration:underline }}
.sub {{ color:var(--muted); font-size:11.5px; font-family:var(--mono) }}
.bidcell {{ color:var(--gold); font-weight:600 }}
.sub.over {{ color:var(--warn) }}
.chip {{ display:inline-block; margin-top:4px; padding:1px 7px; border-radius:99px; font-size:11px; font-weight:600 }}
.chip.live {{ background:var(--dip); color:#fff }}
.chip.warn {{ border:1px solid var(--warn); color:var(--warn) }}
.spark {{ display:block; overflow:visible }}
.spark polyline {{ fill:none; stroke-width:1.3; stroke-linejoin:round }}
.spark .hi {{ stroke:var(--buy); opacity:.5 }}
.spark .lo {{ stroke:var(--sell) }}
.spark .median {{ stroke:var(--muted); stroke-dasharray:2 3; opacity:.7 }}
.spark .bid {{ stroke:var(--gold); stroke-dasharray:5 3 }}
.spark circle {{ fill:var(--dip) }}
.ladder {{ white-space:nowrap }}
.rung {{ display:inline-block; font:11px var(--mono); padding:2px 5px; margin-right:3px; border-radius:3px;
  border:1px solid var(--line); color:var(--muted) }}
.rung b {{ color:var(--fg) }}
.rung.on {{ border-color:var(--gold); color:var(--gold) }}
.quiet {{ color:var(--muted); font-size:12.5px; margin-top:16px; max-width:120ch }}
.bar {{ display:flex; flex-wrap:wrap; align-items:center; gap:10px 18px; margin:14px 0 4px;
  padding:10px 12px; background:var(--panel); border:1px solid var(--line); border-radius:6px }}
.bar button {{ font:600 14px var(--sans); padding:7px 16px; border-radius:5px; border:0; cursor:pointer;
  background:var(--gold); color:#1d1c19 }}
.bar button:disabled {{ opacity:.55; cursor:default }}
.bar label {{ display:flex; align-items:center; gap:8px; color:var(--muted); font-size:13px }}
.bar input[type=range] {{ width:140px; accent-color:var(--gold) }}
.bar .status {{ font:12.5px var(--mono); color:var(--muted) }}
.bar .status.err {{ color:var(--dip) }}
.bar progress {{ width:140px; accent-color:var(--gold) }}
</style></head><body>
<header><h1>Spike Scanner</h1>
<span class="meta">{datetime.fromtimestamp(now).strftime('%a %d %b, %H:%M')} · {watch_text(cfg, len(results))} · {len(with_spikes)} spiked {cfg['spike_min_pct']}%+ in the last {cfg['spike_max_age_hours']}h</span></header>
{toolbar}
<p class="legend">A <b>spike</b> is a 5-minute instasell in the last {cfg['spike_max_age_hours']} hours at least {cfg['spike_min_pct']}% under
<b>both</b> the price around it (the median of the {cfg['local_window_minutes']} minutes before and after) and the
item's {cfg['long_baseline_days']}-day average, lasting {cfg['spike_max_minutes']} min or less, that bounced back within
{cfg['spike_recover_minutes']} min. Slow price drifts and noise are ignored; items that normally jump around are marked <em>choppy</em> and ranked lower.
Chart: <i style="background:var(--sell)"></i>instasell <i style="background:var(--buy)"></i>instabuy
<i style="border-top:1px dashed var(--muted)"></i>normal price now <i style="border-top:2px dashed var(--gold)"></i>suggested bid,
red dots are spikes. <b>Suggested bid</b> is the deepest standing offer that would have caught at least
{cfg['spike_min_fills']} spike(s). Bids and profit use the lower of today's price and the {cfg['long_baseline_days']}-day average, after 2% tax.</p>
<div class="wrap"><table>
<thead><tr><th>Item</th><th>Last 24h</th><th class="num">Normal now</th><th class="num">Spikes</th>
<th class="num">Deepest</th><th class="num">Suggested bid</th><th class="num">Profit/ea</th><th>Bid ladder (spikes caught)</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p class="quiet"><b>No spikes:</b> {quiet_list or 'none'}</p>
</body></html>"""
    return html


# ----------------------------------------------------------------------------- serve mode (local page)

PORT = 8765
URL = f"http://localhost:{PORT}"
AUTO_SCAN_MINUTES = 10
IDLE_SHUTDOWN_HOURS = 3

TOOLBAR = """<div class="bar">
  <button id="scan">Scan now</button>
  <label>Spike depth <input type="range" id="depth" min="6" max="30" step="1" value="__DEPTH__"> <b id="dv">__DEPTH__%</b></label>
  <label>Cash stack <input id="cash" type="text" value="__CASH__" placeholder="e.g. 15m" size="7"></label>
  <progress id="prog" max="1" value="0" hidden></progress>
  <span class="status" id="st">__STATUS__</span>
</div>
<script>
(function () {
  const LAST = __LAST__;
  const btn = document.getElementById('scan'), st = document.getElementById('st'),
        prog = document.getElementById('prog'), depth = document.getElementById('depth'),
        dv = document.getElementById('dv');

  async function poll() {
    let s;
    try { s = await (await fetch('/status')).json(); }
    catch (e) { st.textContent = 'Scanner stopped. Double-click Spike Scanner.bat to start it again.'; st.className = 'status err'; btn.disabled = true; return; }
    if (s.running) {
      btn.disabled = true; prog.hidden = false; prog.max = s.total || 1; prog.value = s.done || 0;
      st.className = 'status'; st.textContent = 'Scanning ' + (s.done || 0) + '/' + (s.total || '?') + (s.name ? ' · ' + s.name : '');
      setTimeout(poll, 700);
    } else if (s.last && s.last !== LAST) {
      location.reload();
    } else {
      btn.disabled = false; prog.hidden = true;
      if (s.error) { st.textContent = 'Scan failed: ' + s.error; st.className = 'status err'; }
    }
  }
  function startScan() { fetch('/scan', { method: 'POST' }).then(poll); }
  btn.onclick = startScan;
  depth.oninput = () => { dv.textContent = depth.value + '%'; };
  depth.onchange = () => {
    fetch('/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spike_min_pct: Number(depth.value) }) }).then(poll);
  };
  function parseGp(t) {
    const m = String(t).trim().toLowerCase().replace(/,/g, '').match(/^([0-9]*[.]?[0-9]+) *([kmb]?)$/);
    if (!m) return null;
    return Math.round(parseFloat(m[1]) * ({ k: 1e3, m: 1e6, b: 1e9 }[m[2]] || 1));
  }
  function setting(obj) {
    fetch('/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(obj) }).then(poll);
  }
  const cash = document.getElementById('cash');
  cash.onchange = () => {
    const v = parseGp(cash.value);
    if (v === null) { st.textContent = 'Type your cash like 15m, 1.2b or 15000000'; st.className = 'status err'; return; }
    setting({ cash_stack: v });
  };
  setInterval(() => { if (!document.hidden) startScan(); }, __AUTO__ * 60 * 1000);
  poll();
})();
</script>"""

WAITING_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Spike Scanner</title>
<style>body{margin:0;display:grid;place-items:center;height:100vh;background:#1d1c19;color:#ece6d8;
font:15px system-ui,sans-serif}@media (prefers-color-scheme: light){body{background:#f3f1ec;color:#24221d}}
.box{text-align:center}progress{width:260px;accent-color:#e3b341}.err{color:#ff5a5f}</style></head><body>
<div class="box"><h2>Spike Scanner</h2><p id="st">Running the first scan…</p><progress id="p" max="1" value="0"></progress></div>
<script>
async function poll(){let s;try{s=await (await fetch('/status')).json()}catch(e){return}
if(s.last){location.reload();return}
if(s.error&&!s.running){document.getElementById('st').textContent='Scan failed: '+s.error;document.getElementById('st').className='err';return}
const p=document.getElementById('p');p.max=s.total||1;p.value=s.done||0;
document.getElementById('st').textContent='Scanning '+(s.done||0)+'/'+(s.total||'?')+'…';setTimeout(poll,700)}
poll();
</script></body></html>"""


class _State:
    lock = threading.Lock()
    running = False
    done = 0
    total = 0
    name = ""
    error = ""
    html = ""
    last = 0
    last_request = time.time()


def _start_scan():
    with _State.lock:
        if _State.running:
            return
        _State.running, _State.done, _State.total, _State.name, _State.error = True, 0, 0, "", ""

    def work():
        try:
            cfg = load_config()

            def progress(i, n, name):
                _State.done, _State.total, _State.name = i, n, name
            results, now = run_scan(cfg, progress)
            _State.html = build_report(results, cfg, now, toolbar="__TOOLBAR__")
            _State.last = int(now)
        except Exception as e:  # shown on the page
            _State.error = str(e)
        finally:
            _State.running = False

    threading.Thread(target=work, daemon=True).start()


def _save_setting(key, value):
    path = os.path.join(HERE, "config.json")
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    data[key] = value
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the console quiet

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj):
        self._send(200, json.dumps(obj), "application/json")

    def do_GET(self):
        _State.last_request = time.time()
        if self.path == "/status":
            self._json({"running": _State.running, "done": _State.done, "total": _State.total,
                        "name": _State.name, "error": _State.error, "last": _State.last})
        elif self.path in ("/", "/index.html"):
            if not _State.html:
                if not _State.running:
                    _start_scan()
                self._send(200, WAITING_PAGE)
                return
            cfg = load_config()
            status = (f"Last scan {datetime.fromtimestamp(_State.last):%H:%M} · rescans every "
                      f"{AUTO_SCAN_MINUTES} min" if _State.last else "")
            bar = (TOOLBAR.replace("__DEPTH__", str(cfg["spike_min_pct"]))
                   .replace("__CASH__", gp(cfg["cash_stack"]) if cfg.get("cash_stack") else "")
                   .replace("__AUTO__", str(AUTO_SCAN_MINUTES))
                   .replace("__STATUS__", escape(status))
                   .replace("__LAST__", str(_State.last)))
            self._send(200, _State.html.replace("__TOOLBAR__", bar))
        else:
            self._send(404, "Not found", "text/plain")

    def do_POST(self):
        _State.last_request = time.time()
        if self.path == "/scan":
            _start_scan()
            self._json({"ok": True})
        elif self.path == "/settings":
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            pct = body.get("spike_min_pct")
            age = body.get("spike_max_age_hours")
            if isinstance(pct, (int, float)) and 1 <= pct <= 50:
                _save_setting("spike_min_pct", int(pct))
                _start_scan()
            elif isinstance(age, (int, float)) and 1 <= age <= 24:
                _save_setting("spike_max_age_hours", int(age))
                _start_scan()
            elif isinstance(body.get("cash_stack"), (int, float)) and 0 <= body["cash_stack"] <= 1e11:
                _save_setting("cash_stack", int(body["cash_stack"]))
                _start_scan()
            self._json({"ok": True})
        else:
            self._send(404, "Not found", "text/plain")


def self_update():
    """
    Download the latest version from GitHub and replace the program files if they changed.
    Never touches config.json or watchlist.txt. Returns True if dip_scanner.py itself changed.
    Any problem (offline, GitHub down) is ignored and the current version keeps running.
    """
    try:
        req = urllib.request.Request(REPO_ZIP, headers={"User-Agent": "osrs-spike-scanner-updater"})
        with urllib.request.urlopen(req, timeout=15) as r:
            archive = zipfile.ZipFile(io.BytesIO(r.read()))
    except Exception as e:
        print(f"Update check skipped ({e}). Running the version you have.")
        return False

    code_changed = False
    for info in archive.infolist():
        name = info.filename.split("/", 1)[-1]
        if name not in UPDATABLE:
            continue
        new = archive.read(info)
        path = os.path.join(HERE, name)
        try:
            with open(path, "rb") as f:
                if f.read() == new:
                    continue
        except OSError:
            pass
        tmp = path + ".new"
        with open(tmp, "wb") as f:
            f.write(new)
        os.replace(tmp, path)
        print(f"Updated {name}")
        code_changed = code_changed or name == "dip_scanner.py"
    return code_changed


def serve(open_browser=True, update=True):
    if update and self_update():
        print("Restarting on the new version...")
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "serve", "--no-update"]
                         + (["--no-open"] if not open_browser else []))
        os._exit(0)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
    except OSError:
        # Already running (or the port is taken): just open the page.
        print(f"Spike Scanner is already running. Opening {URL}")
        if open_browser:
            webbrowser.open(URL)
        return

    def idle_watch():
        while True:
            time.sleep(60)
            if time.time() - _State.last_request > IDLE_SHUTDOWN_HOURS * 3600:
                print("No one has looked at the page for a while. Stopping.")
                os._exit(0)
    threading.Thread(target=idle_watch, daemon=True).start()

    print(f"Spike Scanner is running at {URL}")
    print("Close this window to stop it. It also stops by itself after "
          f"{IDLE_SHUTDOWN_HOURS} hours of nobody looking at the page.")
    _start_scan()
    if open_browser:
        webbrowser.open(URL)
    server.serve_forever()


# ----------------------------------------------------------------------------- watch mode

def notify(cfg, title, body, click=None, priority="high"):
    topic = cfg.get("ntfy_topic", "").strip()
    if not topic:
        return False
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Priority": priority, "Tags": "moneybag"}
    if click:
        headers["Click"] = click
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), headers=headers)
    try:
        urllib.request.urlopen(req, timeout=15).read()
        return True
    except Exception as e:
        print(f"  (push notification failed: {e})")
        return False


def watch(cfg):
    items = load_watchlist(cfg)
    ids = {str(m["id"]): m for m in items}
    state_path = os.path.join(HERE, ".alert_state.json")
    try:
        with open(state_path, encoding="utf-8") as f:
            alerted = json.load(f)
    except Exception:
        alerted = {}
    every = max(1, float(cfg["watch_every_minutes"])) * 60
    thr = cfg["alert_spike_pct"]
    if not cfg.get("ntfy_topic"):
        print("No ntfy_topic set in config.json, so alerts will only print here (no phone push).")
    print(f"Watching {len(items)} items. Alert when an instasell lands {thr}%+ below its "
          f"last-hour average. Checking every {cfg['watch_every_minutes']} min. Ctrl+C to stop.\n")

    bases, bases_fetched = {}, 0
    while True:
        try:
            now = time.time()
            if now - bases_fetched > 600:  # last-hour average instasell, all items in one call
                hour = fetch("1h", cfg)["data"]
                bases = {sid: d["avgLowPrice"] for sid, d in hour.items() if d.get("avgLowPrice")}
                bases_fetched = now
            latest = fetch("latest", cfg)["data"]
            hits = 0
            for sid, it in ids.items():
                lt, base = latest.get(sid, {}), bases.get(sid)
                low, ts = lt.get("low"), lt.get("lowTime")
                if not (low and ts and base) or now - ts > cfg["alert_max_age_minutes"] * 60:
                    continue
                pct = (base - low) / base * 100
                if pct < thr or alerted.get(sid) == ts:
                    continue
                alerted[sid] = ts
                hits += 1
                bid = round_bid(low * 1.01)
                msg = (f"{it['name']} instasold at {low:,} ({pct:.1f}% under its "
                       f"last-hour average {int(base):,}), {ago(ts, now)}. "
                       f"Try a buy offer around {bid:,}. Limit {it.get('limit') or '?'}.")
                print(f"[{datetime.now():%H:%M}] SPIKE  {msg}")
                notify(cfg, f"{it['name']} -{pct:.0f}%", msg, ITEM_PAGE.format(it["id"]),
                       priority="urgent" if pct >= thr * 1.5 else "high")
            if not hits:
                print(f"[{datetime.now():%H:%M}] no spikes", end="\r", flush=True)
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(alerted, f)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[{datetime.now():%H:%M}] check failed, will retry: {e}")
        time.sleep(every)


# ----------------------------------------------------------------------------- entry

def main():
    cfg = load_config()
    mode = (sys.argv[1] if len(sys.argv) > 1 else "serve").lower()
    try:
        if mode == "serve":
            serve(open_browser="--no-open" not in sys.argv, update="--no-update" not in sys.argv)
        elif mode == "scan":
            scan(cfg, open_report="--no-open" not in sys.argv)
        elif mode == "watch":
            watch(cfg)
        elif mode == "test-alert":
            ok = notify(cfg, "Spike Scanner test", "Phone alerts are working.", priority="default")
            print("Sent. Check your phone." if ok else "Not sent: set ntfy_topic in config.json first.")
        else:
            print(__doc__)
    except KeyboardInterrupt:
        print("\nStopped.")
    except RuntimeError as e:
        print(f"\n{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
