# OSRS GE Dip Scanner

Finds the deep, short instasell **dips** on your watchlist, the kind where someone sells
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

- The first time, it asks for your cash stack, then scans (about 10 to 30 seconds, with a progress bar).
  After that it remembers your stack and scans as soon as it opens.
- **Scan now** rescans any time. Leave the tab open and it rescans every 10 minutes on its own.
- **Cash stack**: see below.
- Already running? Double-clicking the shortcut again just reopens the page. You can also bookmark it.
- A small minimized window titled "GE Dip Scanner" keeps it running. Close it to stop, or it stops
  on its own after 12 hours with the page closed.

### On your phone

The scanner runs on your PC, and your phone can open the same page:

1. Start the scanner on your PC. The first time, Windows may ask whether to allow Python on your
   network: tick **Private networks** and click **Allow**.
2. The scanner's window, and the top of the page on your PC, show an address like
   `http://192.168.1.23:8765`. Open that in your phone's browser while it's on the same Wi-Fi.
   Bookmark it or add it to your home screen.

**Away from home:** install [Tailscale](https://tailscale.com) (free) on your PC and your phone and
sign in to both with the same account. Then open `http://<your PC's name>:8765` on your phone from
anywhere. Your PC must be on with the scanner running.

### Let your cash stack pick the items

Type your cash stack into the page (e.g. `15m`). Every scan then watches items that fit it, so the list
moves with the market and with your stack:

- **Priced from 5% to about 29% of your stack.** At 15m: about 750k to 4.3m.
- **The 75 busiest items** in that range, by trades a day. Busy items get dumped on often, so
  bids on them actually fill. Rarely traded items are left out even if they had a big dip.
- **Not enough dips in that range?** It steps down to cheaper items (the range a 10x smaller
  stack would watch), up to twice, until it has enough dips to fill the page. With a very big
  stack you still see the biggest flips there are, even if they use only a small part of it.
- Items in your `watchlist.txt` are always scanned too, on top of these.
- One item per offer: buying more of the same item just competes with your own resale.

Leave the cash stack empty to scan only your `watchlist.txt`.

What the page shows: the **best 20 items worth a bid**, ranked by profit per flip. A short list
just means a quiet market.

- **Normal now**: what it sells for today, with its 7-day average below.
- **Deepest**: the biggest dip in the last day.
- **Suggested bid**: where to leave your buy offer for one.
- **Profit per flip**: what that bid makes on one item after tax, if you sell by listing it mid-range
  (halfway between the instasell and instabuy prices, the lower of today's and the 7-day one) rather
  than instaselling. The sale price is shown, and when the dip the bid is based on happened.
- Tags: **New** (wasn't on the list last time you looked) and **Choppy price** (jumps around a lot).

## How it works

A **dip** is a 5-minute trade (an instasell, or an instabuy: a seller who listed cheap with no buy offers above them) at least 5% below both the price around it and the 7-day average,
lasting 30 minutes or less and bouncing back within an hour. Slow price drops don't count, and an item
only shows if it dipped in the last 18 hours.

The **suggested bid** is just above the most recent dip (1% above its low). A dip means someone sold
through every normal buyer and reached a lowball offer at that price, which is now used up. Bidding
just above it puts you at the front of what's left of the lowball queue, so the next big dump fills you
first. Older dips are ignored: they say nothing about the queue now. Bids land just above round
numbers (1,004,111, not 1,000,000).

The list is sorted by profit per flip. Nothing is modelled or weighted: the dip count and how long ago it dipped are shown so you can judge them yourself.

It's an estimate from one day: check back daily and move offers that stopped filling.

## Dip log

Experimental, and nothing on the page uses it yet. Scans write each finished dip to `dip_log.csv`
next to the scanner, once per dip: the item, when, how deep, how long it lasted, how many sold, the item's daily volume,
and instasell or instabuy.

A dip is logged 6 hours after it ends, with the **lowest price in those 6 hours** (`next_low`):
what a bid placed off that dip would have needed to fill. A dip to 500k then a low of 600k two
hours later logs `next_low_vs_dip_pct` = +20. Lows later than 6 hours are treated as unrelated.
After a couple of weeks it shows how far above a dip's low to bid. Updates never touch it.
Open it in any spreadsheet app.

## Tuning

The page covers the cash stack. For anything else, create `config.json` next to the scanner, e.g.
`{"max_shown": 10}`. Updates never touch it.

| Setting | Default | Meaning |
| --- | --- | --- |
| `max_shown` | 20 | How many items to show. |
| `beat_by_pct` | 1 | How far above the latest dip's low to bid, to get ahead of other lowballers. Higher = fills a bit sooner, less profit. |
| `min_profit_pct` | 2 | Least profit to suggest, after tax. Also sets how deep a dip must be (this + 2% tax + `beat_by_pct`). |
| `max_auto_items` | 75 | How many of the busiest items to watch in each price range. |
| `min_daily_volume` | 25 | Trades per day an item needs to be picked at all. |
| `min_item_pct` / `max_offer_pct` | 5 / 29 | Cheapest and priciest items to watch, as % of your stack. |

Edit `watchlist.txt` to always include specific items (names exactly as on the prices site).

## Data

OSRS Wiki real-time prices API (`prices.runescape.wiki`), the same data the prices site shows.
The scanner follows the wiki's API guidance: it sends a descriptive User-Agent, and after the first
scan of a session it only fetches what's new, using bulk requests for all items at once
(about 3 requests per rescan instead of one per item). Slow-moving data (7-day history, item list,
daily volumes) refreshes hourly.
Requests identify the tool and its owner's Discord, as the wiki asks.
