# OSRS Spike Scanner

Finds the deep, short instasell **spikes** on your watchlist, the kind where someone dumps
an item far below its price and it bounces right back, so you know where to leave lowball
offers. It ignores small wiggles and slow price drifts.

## Setup (once)

1. Install Python 3 from python.org (tick **"Add python.exe to PATH"** in the installer).
2. On GitHub, click **Code → Download ZIP**, and unzip it somewhere, e.g. `Documents\osrs-dip-scanner`.
3. Optional: right-click **Spike Scanner.bat** → **Send to → Desktop (create shortcut)**.

That's the only download you ever need. **It updates itself:** every time you start it, it fetches the
latest version from GitHub and restarts on it if anything changed. Your `watchlist.txt` and settings
are never overwritten.

## Use

Double-click **Spike Scanner.bat**. Your browser opens **http://localhost:8765**:

- The first time, it asks for your cash stack, then scans (about 30 seconds, with a progress bar).
  After that it remembers your stack and scans as soon as it opens.
- **Scan now** rescans any time. Leave the tab open and it rescans every 10 minutes on its own.
- **Cash stack**: see below.
- Already running? Double-clicking the shortcut again just reopens the page. You can also bookmark it.
- A small minimized window titled "Spike Scanner" keeps it running. Close it to stop, or it stops
  on its own after 3 hours with the page closed.

### Let your cash stack pick the items

Type your cash stack into the page (e.g. `15m`). Every scan then watches items that fit it, so the list
moves with the market and with your stack:

- **Priced from 5% to about 29% of your stack.** At 15m: about 750k to 4.3m.
- **25+ trades a day**, top 80 by daily gp traded.
- Items in your `watchlist.txt` are always scanned too, on top of these.
- Each suggested bid shows **how many to buy** with one offer (up to ~29% of your stack, capped at the GE limit).

Leave the cash stack empty to scan only your `watchlist.txt`.

What the page shows:

- **The best 8 spikes right now**, best opportunity first: enough to fill your GE slots with a few spares.
  More than 8? The rest fold into "+ N more". Items with no spikes fold into one line at the bottom.
  A short list just means a quiet market.
- **Normal now**: what the item sells for today (median of the last 3 hours), with its 7-day average below.
- **Spikes**: how many deep spikes in the last 24h. Red dots on the chart.
- **Suggested bid**: the deepest standing buy offer that would have caught them.
- **Profit/ea**: if you resell at today's normal price, after the 2% GE tax.
- **Choppy price**: this item normally jumps around a lot, so treat its spikes with caution.

## What counts as a spike

A 5-minute instasell, **in the last 12 hours**, that is:
- **10%+ below BOTH** the price around it (median of the 90 minutes before and after) **and** the
  item's 7-day average. Whichever is lower is the bar, so:
  - an item drifting down all day is judged against today's lower price, and
  - an item that has risen isn't credited for a dip that only fell back to last week's price.
- **short**: 30 minutes or less,
- **bounces back** within an hour. A price that drops and stays down is a crash, not a spike.

Suggested bids and profit use the lower of today's price and the 7-day average too.

## Tuning

The page covers the cash stack. Anything else goes in `config.json` (create it if it doesn't exist,
e.g. `{"spike_max_minutes": 20}`); it's yours and updates never touch it.

| Setting | Default | Meaning |
| --- | --- | --- |
| `spike_min_pct` | 10 | How deep a spike must be. |
| `spike_max_age_hours` | 12 | Only spikes this recent count. |
| `long_baseline_days` | 7 | The long-run average a dip must also be below. |
| `cash_stack` | 0 | Set on the page. |
| `min_item_pct` | 5 | Cheapest item to watch, as % of your stack. |
| `max_offer_pct` | 29 | Most one offer may use and priciest item to watch, as % of your stack. |
| `min_daily_volume` | 25 | Trades per day an item needs to be auto-picked. |
| `max_auto_items` | 80 | Most items to auto-pick (more = slower scans). |
| `max_shown` | 8 | How many spikes to show before folding the rest away. |
| `spike_max_minutes` | 30 | Longer than this = a price drop, not a spike |
| `spike_recover_minutes` | 60 | Must bounce back within this |
| `local_window_minutes` | 90 | How much surrounding price defines "normal" |
| `choppy_noise_pct` | 3 | Items that normally move more than this every 5 min are marked choppy |
| `spike_bid_levels_pct` | 10 … 40 | The bid depths shown in the ladder |

Edit `watchlist.txt` to add or remove items (names exactly as on the prices site). It's created
from `watchlist.example.txt` the first time you run the scanner.

## Phone alerts (optional)

**Watch for dips.bat** checks every 3 minutes while your PC is on and pushes to your phone
(free **ntfy** app) when an item instasells 15%+ under its last-hour average. Set
`ntfy_topic` in `config.json` to a long random name, subscribe to the same name in the app,
then run **Test phone alert.bat**.

## Data

OSRS Wiki real-time prices API (`prices.runescape.wiki`), the same data the prices site shows.
The scanner follows the wiki's API guidance: it sends a descriptive User-Agent, and after the first
scan of a session it only fetches what's new, using bulk requests for all items at once
(about 3 requests per rescan instead of one per item). Slow-moving data (7-day history, item list,
daily volumes) refreshes hourly.
Put your Discord name in `"contact"` in config.json; the wiki asks tools to identify themselves.
