# ✈️ Flight Finder — Tokyo → Montreal and Tokyo → anywhere in Asia

A personal cheap-flight finder that runs for free on GitHub. Twice a day it checks
fares for every date over the next 6 months, builds trips that a normal search box
won't show you, and posts the results to a phone-friendly page. It can also send
a push alert when a deal shows up.

Everything it uses is free: **Travelpayouts** (fare data), **GitHub** (runs it
and hosts the page) and, optionally, **ntfy** (push alerts, no account needed).

## What it finds

**Tokyo ⇄ Montreal**
- Normal round-trip tickets, for every departure date at once
- **Two one-ways**: outbound and return on different airlines, when that's cheaper
- **Self-transfers**: e.g. Tokyo → Vancouver on ZIPAIR, then Vancouver → Montreal
  on Flair, as separate tickets. It only pairs flights with a 4–26 hour gap.
  The return works the same way. Hubs checked: Vancouver, Calgary, Toronto,
  Seattle, San Francisco, Los Angeles, Honolulu, New York, Chicago, Boston,
  Seoul, Taipei, Hong Kong, Manila, Beijing and Shanghai.
- **Nearby airports**: Ottawa, Toronto, Plattsburgh, Burlington, Boston and New York,
  with a note on the ground trip (bus or train cost isn't included)

**Tokyo ⇄ anywhere in Asia**
- Every destination at once: East, Southeast, South and Central Asia, plus the
  Middle East. Japan domestic is off by default.
- **Open-jaw**: fly into one city and home from another nearby
  (e.g. into Bangkok, out of Chiang Mai)
- **2-city trips**: Tokyo → A → B → Tokyo, with 2–7 days in each place

The page tracks the lowest price seen for each destination, so you can tell
whether today's price is actually a good one.

## Setup (about 15 minutes, all free)

### 1. Get a free Travelpayouts API token
1. Sign up at <https://www.travelpayouts.com> (free).
2. When it asks for a project or website, enter your GitHub Pages address:
   `https://davepomerlo.github.io/flight-finder/`
3. Find your **API token** in your account's API section and copy it.

### 2. Put the code on GitHub
1. Create a **public** repository called `flight-finder`. GitHub Pages is free for public repos.
2. Upload everything in this folder, including the hidden `.github` folder.

### 3. Add the token as a secret
Repository → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `TRAVELPAYOUTS_TOKEN`
- Value: your token

### 4. Turn on the page
Repository → **Settings → Pages** → Source: *Deploy from a branch* → Branch: `main`, folder: `/docs` → Save.

### 5. Run it
Repository → **Actions → Update fares → Run workflow**. A run takes 5–10 minutes,
then the page at `https://davepomerlo.github.io/flight-finder/` shows real fares.
Until then it shows made-up demo data, marked with a yellow banner.

### 6. Optional: push alerts on your phone
1. Install the **ntfy** app from Google Play. It's free and needs no account.
2. Subscribe to a topic name that's hard to guess, e.g. `dave-flights-8k2q7`.
   Anyone who knows the name can read it.
3. Add a second repository secret: `NTFY_TOPIC` = that topic name.

You'll get an alert when Tokyo ⇄ Montreal drops below ¥140,000, or when an Asia
round trip drops below ¥30,000. Each alert fires again only if the price falls a
further 5%.

## Changing what it searches

Edit `config.json` on GitHub (pencil icon):

| Setting | What it does |
|---|---|
| `months_ahead` | How far ahead to search (default 6) |
| `montreal.stay_days` | Shortest and longest Montreal trip |
| `montreal.alert_below` | Montreal alert price in yen |
| `montreal.self_transfer_layover_hours` | Minimum and maximum gap between separate tickets |
| `asia.stay_days`, `asia.alert_below` | The same, for Asia |
| `asia.include_japan_domestic` | `true` to include Okinawa, Sapporo, etc. |
| `asia.open_jaw_max_km` | How far apart the arrival and departure cities can be |
| `asia.hop.days_per_stop` | Days in each city on 2-city trips |

## Things to know

- **Prices are recent, not live.** The data is the cheapest fares Aviasales users
  found in roughly the last 48 hours. Tap **Check** to open the live search and
  confirm the price before you book. Some fares will have gone up or sold out.
- **Self-transfer and multi-ticket trips are separate bookings.** If your first
  flight is late, the next airline doesn't have to rebook you. You collect and
  re-check your bags, and you may need to pass immigration (for Canada and the
  US, check whether you need an eTA or ESTA to transit). Leave long gaps.
- Coverage depends on what people have searched for. Popular routes are well
  covered; rare dates on unusual routes may be missing.
- GitHub pauses scheduled runs if a repository has no activity for 60 days. The
  bot's own commits normally count as activity; if runs stop, click **Run workflow**.

## Running it on your PC (optional)

```
python finder.py --fake      # demo data, no token needed
set TRAVELPAYOUTS_TOKEN=xxxx
python finder.py             # real fares
python -m unittest discover tests
```
Then open `docs/index.html` through a local server: `python -m http.server -d docs`.
