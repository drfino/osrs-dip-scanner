# OSRS Spike Scanner

Finds the deep, short instasell **dumps** on your watchlist, the kind where someone sells
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

- **The best 8 items worth a bid right now**, ranked by **est. profit/day**: enough to fill your GE
  slots with a few spares. Items that aren't worth a bid fold into one line at the bottom.
  A short list just means a quiet market.
- **Normal now**: what the item sells for today (median of the last 3 hours), with its 7-day average below.
- **Dumps (24h)**: how many qualifying dumps in the last 24h. Red dots on the chart.
- **Deepest**: the lowest dump, how far under normal, and how long ago.
- **Suggested bid**: where to leave your offer, how far under normal it is, and how many to buy.
- **Est. profit/day**: what that bid would have made over the last 24h, profit each, and how many
  times it would have filled.
- **Choppy price**: this item normally jumps around a lot, so it's ranked lower.

## What counts as a dump

A 5-minute instasell that is:
- **deep enough to profit from**: 9%+ below BOTH the price around it (median of the 90 minutes
  before and after) **and** the item's 7-day average. 9% = a bid 3% above it still clears 4% profit
  after the 2% tax. Whichever price is lower is the bar, so:
  - an item drifting down all day is judged against today's lower price, and
  - an item that has risen isn't credited for a dip that only fell back to last week's price.
- **short**: 30 minutes or less,
- **bounces back** within an hour. A price that drops and stays down is a crash, not a dump.

An item is listed only if it dumped **in the last 12 hours** (dead items drop off), but the bid uses
the whole last 24 hours as evidence.

## How the suggested bid works

The goal is the most profit per day, not the deepest discount. A bid that catches one huge dump a
week loses to one that fills on every ordinary dump.

So the scanner replays the last 24 hours. For every bid from 4% profit up to 30% under normal, it asks:
- **Would it have filled?** Dumps fill the highest bids first, and other lowballers sit just above the
  bottom. A dump only counts if it went **3% past your bid**, so you'd have been ahead of that pack.
- **How many?** Your offer size, or the units that dump actually sold, whichever is smaller.
- **For how much?** Normal price after tax, minus your bid.

The bid that would have made the most is the suggestion, landed **just above a round number**
(1,004,111 rather than 1,000,000), since that's where other offers cluster. Items are ranked by that
estimated profit/day, so a reliable −9% dumper can outrank a one-off −20% crash.

It's an estimate from one day. Re-check daily and move offers that stopped filling.

## Tuning

The page covers the cash stack. Anything else goes in `config.json` (create it if it doesn't exist,
e.g. `{"spike_max_minutes": 20}`); it's yours and updates never touch it.

| Setting | Default | Meaning |
| --- | --- | --- |
| `spike_max_age_hours` | 12 | Only items that dumped this recently are listed. |
| `long_baseline_days` | 7 | The long-run average a dip must also be below. |
| `cash_stack` | 0 | Set on the page. |
| `min_item_pct` | 5 | Cheapest item to watch, as % of your stack. |
| `max_offer_pct` | 29 | Most one offer may use and priciest item to watch, as % of your stack. |
| `min_daily_volume` | 25 | Trades per day an item needs to be auto-picked. |
| `max_auto_items` | 80 | Most items to auto-pick (more = slower scans). |
| `max_shown` | 8 | How many items to show. |
| `beat_by_pct` | 3 | How far a dump must go past your bid to count as a fill. Higher = safer bids, less profit. |
| `min_profit_pct` | 4 | Never suggest a bid with less profit than this, after tax. Also sets how deep a dump must be. |
| `spike_max_minutes` | 30 | Longer than this = a price drop, not a dump |
| `spike_recover_minutes` | 60 | Must bounce back within this |
| `local_window_minutes` | 90 | How much surrounding price defines "normal" |
| `choppy_noise_pct` | 3 | Items that normally move more than this every 5 min are marked choppy |

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
Requests identify the tool and its owner's Discord, as the wiki asks.
