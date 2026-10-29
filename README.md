# OSRS GE Dip Scanner

Finds the deep, short instasell **dumps** on your watchlist, the kind where someone sells
an item far below its price and it bounces right back, so you know where to leave lowball
offers. It ignores small wiggles and slow price drifts.

## Setup (once)

1. Install Python 3 from python.org (tick **"Add python.exe to PATH"** in the installer).
2. On GitHub, click **Code → Download ZIP**, and unzip it somewhere, e.g. `Documents\osrs-dip-scanner`.
3. Optional: right-click **GE Dip Scanner.bat** → **Send to → Desktop (create shortcut)**.

That's the only download you ever need. **It updates itself:** every time you start it, it fetches the
latest version from GitHub and restarts on it if anything changed. Your `watchlist.txt` and settings
are never overwritten.

## Use

Double-click **GE Dip Scanner.bat**. Your browser opens **http://localhost:8765**:

- The first time, it asks for your cash stack, then scans (about 30 seconds, with a progress bar).
  After that it remembers your stack and scans as soon as it opens.
- **Scan now** rescans any time. Leave the tab open and it rescans every 10 minutes on its own.
- **Cash stack**: see below.
- Already running? Double-clicking the shortcut again just reopens the page. You can also bookmark it.
- A small minimized window titled "GE Dip Scanner" keeps it running. Close it to stop, or it stops
  on its own after 3 hours with the page closed.

### Let your cash stack pick the items

Type your cash stack into the page (e.g. `15m`). Every scan then watches items that fit it, so the list
moves with the market and with your stack:

- **Priced from 5% to about 29% of your stack.** At 15m: about 750k to 4.3m.
- **25+ trades a day**, top 80 by daily gp traded.
- Items in your `watchlist.txt` are always scanned too, on top of these.
- One item per offer: buying more of the same item just competes with your own resale.

Leave the cash stack empty to scan only your `watchlist.txt`.

What the page shows: the **best 8 items worth a bid**, ranked by estimated profit per day. A short list
just means a quiet market.

- **Normal now**: what it sells for today, with its 7-day average below.
- **Deepest**: the biggest dump in the last day.
- **Suggested bid**: where to leave your buy offer for one.
- **Est. profit/day**: what that bid would have made in the last 24h.
- Tags: **New** (wasn't on the list last time you looked) and **Choppy price** (jumps around a lot, ranked lower).

## How it works

A **dump** is a 5-minute instasell at least 9% below both the price around it and the 7-day average,
lasting 30 minutes or less and bouncing back within an hour. Slow price drops don't count, and an item
only shows if it dumped in the last 12 hours.

The **suggested bid** is whichever bid would have made the most gp over the last 24 hours. A dump only
counts as filling you if it went 3% below your bid, because dumps fill the highest bids first and other
lowballers sit just above the bottom. Bids land just above round numbers (1,004,111, not 1,000,000).

It's an estimate from one day: check back daily and move offers that stopped filling.

## Tuning

The page covers the cash stack. For anything else, create `config.json` next to the scanner, e.g.
`{"max_shown": 10}`. Updates never touch it.

| Setting | Default | Meaning |
| --- | --- | --- |
| `max_shown` | 8 | How many items to show. |
| `beat_by_pct` | 3 | How far a dump must go past your bid to count as a fill. Higher = safer bids, less profit. |
| `min_profit_pct` | 4 | Least profit to suggest, after tax. |
| `min_daily_volume` | 25 | Trades per day an item needs to be picked. |
| `min_item_pct` / `max_offer_pct` | 5 / 29 | Cheapest and priciest items to watch, as % of your stack. |

Edit `watchlist.txt` to always include specific items (names exactly as on the prices site).

## Data

OSRS Wiki real-time prices API (`prices.runescape.wiki`), the same data the prices site shows.
The scanner follows the wiki's API guidance: it sends a descriptive User-Agent, and after the first
scan of a session it only fetches what's new, using bulk requests for all items at once
(about 3 requests per rescan instead of one per item). Slow-moving data (7-day history, item list,
daily volumes) refreshes hourly.
Requests identify the tool and its owner's Discord, as the wiki asks.
