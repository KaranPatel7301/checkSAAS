# SaaS Scout

Automated deal scanner for the **SaaS Acquisition Playbook**. It scans
multiple marketplaces + Reddit several times a day, filters listings against
your acquisition criteria, dedupes what it has already seen, and emails you a
digest of **only the new candidates** that pass.

> The opportunity is not building software. It's buying software that already
> works and operating it better.

The full playbook lives at
`~/Documents/Acquisition Criteria/Acquistion Critera.md`. The hard, numeric
rules from that doc are enforced automatically in `saas_scout.py` (see
`CRITERIA`); the judgment calls are flagged in the email for you to verify by
hand.

---

## What it checks (all free, no API keys)

| Source | How it filters | Notes |
|---|---|---|
| **Flippa** | Full playbook filter on real profit / price / multiple | Public JSON endpoint |
| **Empire Flippers** | SaaS-only (`?monetization=saas`) + full playbook filter | Vetted, larger deals; business name hidden until you unlock |
| **Acquire.com** | SaaS whose public sitemap slug reveals ARR/MRR/profit ≥ floor | Exact price on the listing page (free login); smallest-first, capped 25/run |
| **Microns** | SaaS/Web-app categories, annual-revenue floor | Profit is paywalled — confirm on the listing page |
| **Reddit** | 15 subreddits, sale-intent keyword match | Freeform posts, surfaced for you to eyeball |

**Not included and why:**
- **SideProjectors** — protected by a Cloudflare challenge (needs a headless
  browser, too heavy/fragile for a cron job).
- **IndieMaker** — site is unreliable / times out.
- **BizBuySell** — blocks bots (403).

---

## How the playbook is enforced

Enforced automatically (in `CRITERIA`):

- **Profit floor:** $2K/mo minimum; $3K+/mo gets a `*` in the email (your
  "prefer $3K+").
- **Capital range:** price capped at $200K (your $20K–$150K+ range, with a
  little headroom for the "+").
- **Capital efficiency:** multiple capped at **2.5x** annual profit — the top
  of your playbook's own worked examples ($150K / $5K-mo). Anything richer is
  dropped.
- **Recurring revenue:** guaranteed by the SaaS category filters on each
  source.
- **Bad Problems:** trendy AI toys, novelty, one-time/entertainment
  (games, dating, astrology, etc.) are excluded by keyword.

Must still be verified BY HAND (not in any listing's structured data):

- Organic growth with little/no paid marketing
- Low software surface area / simple codebase
- Churn (and whether it's operational vs structural)
- Competition and market inefficiency
- The **panic test**: would users scramble to replace it if it vanished?

The email repeats these reminders so you never treat a machine-pass as a buy.

---

## Reddit: how it works

You don't supply subreddits — they're hardcoded in `REDDIT_SUBS`. Each run
pulls **all subs in a single combined RSS request**
(`reddit.com/r/a+b+c/new.rss`) so Reddit doesn't rate-limit us, then keeps
only posts whose title/body contain sale-intent phrases ("for sale",
"selling my", "acquire", "asking price", "open to offers", …). No Reddit app
or API keys are needed.

Currently watching 15 subs, led by the highest-signal ones:
`saasforsale`, `acquiresaas`, `AcquireStartup`, `microsaas`, `SaaS`,
`SaaSSales`, `AppBusiness`, `flipping`, `juststart`, `EntrepreneurRideAlong`,
`SideProject`, `Entrepreneur`, `Entrepreneurs`, `growmybusiness`,
`advancedentrepreneur`.

To add/remove subs, edit the `REDDIT_SUBS` list.

---

## Scheduling (the "cron")

This runs as a **cron schedule on GitHub Actions** — in the cloud, so it does
not depend on your laptop being awake. The schedule lives in
`.github/workflows/scout.yml`:

```yaml
on:
  schedule:
    - cron: '0 11 * * *'   # ~7am ET
    - cron: '0 17 * * *'   # ~1pm ET
    - cron: '0 23 * * *'   # ~7pm ET
  workflow_dispatch:        # manual "Run workflow" button
```

Each run installs deps, runs `saas_scout.py`, and commits the dedup file
(`seen.json`) back to the repo so it remembers what it already emailed.

Run it on demand anytime from the repo's **Actions** tab → *saas-scout* →
**Run workflow**.

---

## Setup

The only thing required is Gmail SMTP so it can email you. These are stored as
**GitHub repo Secrets** (Settings → Secrets and variables → Actions), never in
the code:

| Secret | Value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `karanpatel7301@gmail.com` |
| `SMTP_PASS` | Gmail **App Password** (16 chars, not your login password) |
| `DIGEST_TO` | optional; defaults to `karanpatel7301@gmail.com` |

Create the App Password at <https://myaccount.google.com/apppasswords>.

### Running locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python saas_scout.py
```

Locally, credentials are read from a `.env` file (gitignored). With no SMTP
set, the script just prints the digest instead of sending it — handy for
testing.

---

## Tuning

- **Criteria:** edit the `CRITERIA` dict in `saas_scout.py`
  (`min_monthly_profit`, `max_price`, `max_multiple`, `exclude_keywords`).
- **Subreddits:** edit `REDDIT_SUBS`.
- **Schedule:** edit the `cron` lines in `.github/workflows/scout.yml`.

## Files

| File | Purpose |
|---|---|
| `saas_scout.py` | The scanner (sources, filters, email) |
| `.github/workflows/scout.yml` | Cloud cron schedule |
| `requirements.txt` | Python deps (`requests`, `beautifulsoup4`, `python-dotenv`) |
| `seen.json` | Dedup memory (auto-updated) |
| `.env` | Local secrets (gitignored) |
