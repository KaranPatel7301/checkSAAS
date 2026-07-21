# SaaS Scout — Engineering Handoff

Handoff notes for the next agent/developer. This describes exactly what was
built, how it works, what to watch out for, and how it maps to the owner's
acquisition playbook. Read this together with `README.md` (user-facing) and
`saas_scout.py` (the implementation).

- **Repo:** https://github.com/KaranPatel7301/checkSAAS (branch `main`)
- **Owner email (digest recipient):** karanpatel7301@gmail.com
- **Playbook source of truth:** `~/Documents/Acquisition Criteria/Acquistion Critera.md`
- **Status:** Live. Verified via multiple successful GitHub Actions runs.

---

## 1. What this is

An automated scanner that hunts for small SaaS businesses for sale, filters
them against the owner's acquisition criteria, and emails a digest of only the
new passing candidates. Runs entirely in the cloud on a cron schedule.

**Pipeline:** `fetch each source → normalize → filter → dedupe (seen.json) →
email only new passers`.

---

## 2. Where it runs (IMPORTANT)

**It runs ONLY on GitHub Actions (cloud). It does NOT run on the owner's
laptop.** The laptop can be off; runs happen on GitHub's servers.

- Schedule file: `.github/workflows/scout.yml`
- Cadence: **3x/day** — `0 11 * * *`, `0 17 * * *`, `0 23 * * *` (UTC) ≈
  7am / 1pm / 7pm US Eastern.
- Manual trigger: repo → Actions → *saas-scout* → Run workflow
  (`workflow_dispatch` is enabled).
- A previous local macOS `launchd` job was created during setup and then
  **removed** to avoid duplicate emails. The old `.plist` has been deleted
  from the repo. There is no local scheduler anymore.

---

## 3. File map

| File | Purpose |
|---|---|
| `saas_scout.py` | Entire scanner: sources, normalizers, filters, email, main() |
| `.github/workflows/scout.yml` | Cloud cron schedule + secrets wiring + heartbeat/seen.json commit-back |
| `.github/last_run.txt` | Heartbeat timestamp; rewritten & committed EVERY run (dormancy protection) |
| `requirements.txt` | `requests`, `beautifulsoup4`, `python-dotenv` |
| `seen.json` | Dedup memory; auto-updated and committed back by the workflow |
| `README.md` | User-facing overview |
| `HANDOFF.md` | This file |
| `.env` | Local-only secrets (gitignored; NOT on GitHub) |
| `.gitignore` | Ignores `.env`, `.venv/`, `__pycache__/`, `scout.log` |

---

## 4. Sources (how each works + gotchas)

### Flippa  (`fetch_flippa` / `normalize_flippa` / `passes_flippa`)
- Public JSON: `https://flippa.com/v3/listings?filter[property_type]=saas&filter[status]=open&filter[sale_method]=classified&page[size]=50`, paged up to `MAX_FLIPPA_PAGES=4`.
- No auth currently required. `FLIPPA_COOKIE` env is supported as a fallback
  header if Flippa ever puts this behind Cloudflare/login.
- Real field names (confirmed live): `profit_per_month`, `current_price` /
  `display_price`, `revenue_per_month`, `has_verified_revenue`, `html_url`.
  (An earlier version mapped the wrong keys → profit was always 0 → nothing
  passed. Don't regress this.)
- Full playbook filter applies.

### Empire Flippers  (`fetch_empireflippers` / `normalize_ef` / `passes_ef`)
- HTML scrape of `https://empireflippers.com/marketplace/?monetization=saas`
  (the `monetization=saas` query returns only SaaS/subscription listings).
- Cards: `.listing-item`; fields via `.metric-item.price/.profit/.revenue/
  .monetization .value` and `.listing-number`. Business name is hidden by EF
  until unlock, so `title` = `"<niche> — <monetization>"`.
- Full playbook filter applies. EF deals skew large, so most exceed the
  price filter is off (asks are negotiable); the few that also clear the
  2.5x multiple are the point.
- Only page 1 is scraped (~9 SaaS cards). No pagination — see risks.

### TrustMRR  (`fetch_trustmrr` / `normalize_trustmrr` / `passes_trustmrr`)
- HTML scrape of the homepage `https://trustmrr.com/`. The "for sale" tiles are
  currently in the HTML as `<a href="/startup/...">` cards whose flat text reads
  e.g. `FOR SALE ReddGrow AI Marketing Revenue $17k Price $1M Multiple 4.7x`.
  (The dedicated `/acquire` marketplace page is JS-rendered → 0 links to a bot,
  so we parse the homepage cards, which include a good recent set, ~26.)
- **JS-SPA warning:** if a future fetch returns 0 FOR SALE cards, do NOT assume
  the selectors are wrong first — the homepage may have gone fully JS-rendered
  (like `/acquire` already is). Tell: raw HTML has no `/startup/` FOR SALE
  links. Fix: find a JSON endpoint in DevTools Network (like Flippa) or fall
  back to a headless browser. Microns worked with plain `requests` only because
  it is server-rendered Webflow; that is not guaranteed for TrustMRR.
- Only cards containing `FOR SALE` are kept; deduped by href within the fetch.
- **Revenue shown is MONTHLY and third-party VERIFIED** (Stripe/LemonSqueezy/
  RevenueCat) — the reason this source is worth trusting. Cards show revenue not
  profit, so `passes_trustmrr` gates on annual revenue (`>= min_monthly_profit*12`)
  like Microns and the email says to confirm profit on the listing page. If a
  card ever exposes profit, the profit floor is applied directly.
- Price cap + `exclude_keywords` + a light non-SaaS drop (`TRUSTMRR_NON_SAAS`,
  e-commerce) apply. The card's "Multiple" is a revenue multiple (not the
  playbook's profit multiple), so it is NOT filtered on.
- Money parsing (`_money`) requires the k/m unit to be ADJACENT to the number
  with a `(?![a-z])` lookahead — otherwise `$67 Multiple` misreads the "M" of
  "Multiple" as millions. Don't regress this.

### Microns  (`fetch_microns` / `normalize_microns` / `passes_microns`)
- HTML scrape of the homepage `https://www.microns.io/` (Webflow).
  NOTE: `/categories/saas` only shows SOLD items — do not use it.
- Cards: `.w-dyn-item` containing a `/startup-listings/` link. Category from
  `.listing-card-tag`. Only categories in `MICRONS_ALLOWED_CATEGORIES`
  (`micro-saas`, `saas`, `web app`) are kept.
- Profit is paywalled on Microns, so we filter on **annual revenue** as a
  necessary condition (`MICRONS_MIN_ANNUAL_REVENUE = min_monthly_profit*12`)
  and the email tells the owner to confirm profit on the listing page.

### Acquire.com  (`fetch_acquire` / `_acquire_financials`)
- Full listings are behind a free login, BUT the public sitemap
  `https://app.acquire.com/sitemap.xml` exposes ~1000+ listing URLs. The slug
  is `<10charID>-<title-with-metrics>`, and for a good chunk the title embeds
  real financials (ARR/MRR/TTM profit, e.g. `...-400k-arr`, `...-137k-ttm-profit`).
- We keep only listings whose slug reveals a revenue/profit signal >= the
  monthly floor, drop non-SaaS (ecommerce/agency/etc via `ACQUIRE_NON_SAAS`)
  and `exclude_keywords`. Financial parse requires a k/m suffix (avoids
  matching "profit-margin %").
- The whole catalog carries today's date (sitemap restamps daily), so date
  can't detect "new". Dedup via seen.json does. To avoid a first-run flood,
  `main()` surfaces at most `ACQUIRE_MAX_PER_RUN=25`, **smallest-revenue
  first** (most likely in the $20-150K budget), and only marks the ones it
  emails as seen -- so the backlog drips out over several runs, then
  steady-state new-only.
- Eyeball source: exact PRICE is not in the sitemap (it's on the login-gated
  page), so no price/multiple filter -- the email says to confirm on Acquire.

### Buy Sell Startups  (`fetch_buysellstartups` / `normalize_bss` / `passes_buysellstartups`)
- HTML scrape of the homepage `https://buysellstartups.com/`. Listing tiles are
  `<a href="/listings/...">` cards reading e.g.
  `SaaS Featured Mentioned ... Asking Sign in -> MRR $1.0k Multiple 0.8x`.
  (`/browse` and `/listings` are already client-side → 404 to a bot, so we use
  the ~6 homepage cards.)
- **JS-SPA warning:** same as TrustMRR. If the homepage fetch returns 0
  `/listings/` cards, treat that as a JS-render regression first, not a bad
  selector. Fix: JSON endpoint from the Network tab (like Flippa) or a headless
  browser.
- Thin, low-value source in practice: asking price is usually behind a login
  ("Sign in"), and some listings are "Confidential" with masked MRR (`••••`).
  We gate on MRR (annual `>= min_monthly_profit*12`); listings with no numeric
  MRR are dropped (can't verify the floor). Most cards are tiny and won't pass —
  that's expected and honest. Included per the brief for completeness.
- Same adjacent-unit money parsing fix as TrustMRR.

### Reddit  (`fetch_reddit`)
- **No API keys / no Reddit app.** Reddit gates app creation and blocks
  anonymous `.json`, but the RSS feed works with a browser UA.
- Key trick: ALL subs are combined into ONE request via
  `https://www.reddit.com/r/a+b+c+.../new.rss?limit=100`. Hitting subs one at
  a time gets you 429'd fast; the combined single request avoids it.
- Retries 4x with backoff on empty/429, then skips gracefully.
- Freeform posts → no numeric filter; kept only if title/body contains a
  sale-intent phrase from `REDDIT_SIGNALS`. Surfaced for manual eyeballing.
- 15 subs in `REDDIT_SUBS` (see README for the list).
- Posting date is captured for free from the RSS `<published>` and shown in the
  digest (display only — never filtered on).

### Digest extras (in `build_body` / `main`)
- **Source-tally line** at the top of every digest: raw counts fetched per
  source BEFORE dedup/filter, collected in `raw_counts` in `main()` and passed
  to `build_body`. A `0` where a number is expected = a silently broken scraper
  (this is exactly how the old Flippa profit-key regression would have been
  caught). Also printed to stdout on no-hit runs.
- **Business age (Flippa):** `established_at` (business creation date) →
  `business_age_months` via `_months_since()`. Shown in the digest with a
  "young, scrutinize churn/durability" flag under 12 months. Display only —
  NOT hard-filtered (a young product claiming strong revenue is a "scrutinize
  harder" flag, not an auto-no). Microns cards don't expose age, so it's skipped
  there rather than fetching every detail page.
- **Listing/posting date:** Flippa `starts_at` and Reddit RSS `<published>`,
  both shown, both display-only. No scraper fragility added for dates.

---

## 5. Criteria mapping (matches the playbook)

Defined in the `CRITERIA` dict in `saas_scout.py`:

| Playbook rule | Code | Enforced on |
|---|---|---|
| $2K+/mo profit, prefer $3K+ | `min_monthly_profit=2000`, `prefer_monthly_profit=3000` (starred) | Flippa, EF (direct profit); TrustMRR/Microns/BSS/Acquire via annual-revenue necessary-condition gate |
| Asking price / capital range | `max_price=None` (disabled) | Not machine-filtered — sellers pad asks for negotiation; owner judges affordability |
| Capital efficiency / ROIC | `max_multiple=2.5` (price ÷ annual profit; top of playbook's own examples) | Flippa, EF (profit-multiple sources only) |
| Recurring subscription revenue | SaaS category filters per source | all |
| Avoid "Bad Problems" (AI toys, novelty, games, entertainment) | `exclude_keywords` | all |

**Judgment-only criteria that are NOT machine-checkable** (not in listing
data) — the email explicitly reminds the owner to verify these by hand:
organic growth / low paid marketing, simple codebase / low surface area,
churn (operational vs structural), competition & market inefficiency, and the
**panic test** (would users scramble to replace it?).

If tightening further toward the playbook's cheaper "SaaS B" example
($30K/$3K-mo ≈ 0.83x), lower `max_multiple`. Current 2.5x = the playbook's
most expensive acceptable example.

---

## 6. Secrets / config

Stored as **GitHub repo Secrets** (Settings → Secrets and variables →
Actions), injected as env vars by `scout.yml`. Also readable from a local
`.env` (gitignored) when running locally for tests.

| Var | Required | Value |
|---|---|---|
| `SMTP_HOST` | yes | `smtp.gmail.com` |
| `SMTP_PORT` | yes | `587` |
| `SMTP_USER` | yes | `karanpatel7301@gmail.com` |
| `SMTP_PASS` | yes | Gmail **App Password** (16 chars) |
| `DIGEST_TO` | no | defaults to `karanpatel7301@gmail.com` |
| `SAAS_SCOUT_SEEN` | set in CI | `seen.json` (dedup path) |
| `SAAS_SCOUT_ALWAYS_EMAIL` | no | `1` forces an email even with no new hits (testing) |
| `FLIPPA_COOKIE` | no | fallback if Flippa ever needs auth |

All 5 required SMTP secrets are already set on the repo. With no SMTP set, the
script prints the digest instead of sending (useful for local dry-runs).

---

## 7. Known issues & fallbacks to consider

1. **Scraper fragility (EF & Microns).** Both parse HTML/CSS selectors. A site
   redesign will silently break parsing. Each `fetch_*` prints a diagnostic to
   stderr if it finds zero cards. Fallback: update selectors. Consider adding
   a health check that emails if a source returns 0 for N consecutive runs.
2. **EF / Microns have no pagination.** EF scrapes page 1 only (~9 SaaS);
   Microns scrapes the homepage set (~22). New listings appear at the top, so
   this is fine at 3x/day cadence, but a burst of new listings could push an
   in-budget SaaS off page 1 before a run catches it. Add pagination if misses
   are observed.
3. **Reddit rate limiting.** The combined single request is the mitigation. If
   Reddit kills RSS too, the fallback is OAuth (app-only client_credentials) —
   code for that existed earlier in git history. High-volume subs
   (`Entrepreneur`) can crowd the 100-item window; if the for-sale subs get
   starved, split Reddit into two combined requests (for-sale subs vs operator
   subs).
4. **Gmail App Password.** If revoked or if Gmail flags the login, sends fail
   (logged in the run). Regenerate at myaccount.google.com/apppasswords and
   update the `SMTP_PASS` secret. Consider a transactional provider (Resend/
   SendGrid) if deliverability becomes an issue.
5. **GitHub Actions scheduled-workflow dormancy — SOLVED.** GitHub disables
   scheduled workflows after ~60 days of NO repository activity. Previously the
   workflow only committed `seen.json` on runs with hits, so a dry spell (or a
   silently broken scraper returning 0 everywhere) meant no commits → eventual
   dormancy with no warning. **Fixed:** the `Heartbeat + persist dedup memory`
   step in `scout.yml` rewrites `.github/last_run.txt` with a UTC timestamp and
   commits it on EVERY run, regardless of hits — guaranteeing a commit 3x/day.
   **One commit per run:** `last_run.txt` and `seen.json` are folded into the
   SAME commit and pushed once. Do not split into two pushes — that can race
   the concurrency guard (`group: saas-scout`). Keep the concurrency group.
6. **seen.json growth.** Grows unbounded (one id per listing ever seen). Fine
   for years at this volume, but could be pruned by date if it ever bloats.
7. **Concurrency.** `scout.yml` uses a concurrency group so overlapping runs
   don't race on the seen.json commit. Keep it.
8. **`max_multiple` strictness.** Requires profit > 0, so pre-revenue
   "starter" listings are dropped (intended). 2.5x may be strict for listings
   that understate profit; loosen if too few pass.
9. **Spam filtering.** First digests may hit Gmail spam; owner should mark
   "Not spam" once.

---

## 8. Local testing (for development only — not how it runs in prod)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# print digest without sending (SMTP unset):
SMTP_HOST="" SAAS_SCOUT_SEEN=/tmp/t.json SAAS_SCOUT_ALWAYS_EMAIL=1 .venv/bin/python saas_scout.py
```

Test a single source, e.g.:
```bash
.venv/bin/python -c "import saas_scout as s; print(len(s.fetch_empireflippers()))"
```

---

## 9. Not included (evaluated and rejected)

| Marketplace | Why not |
|---|---|
| SideProjectors | Cloudflare challenge; needs a headless browser (too heavy for CI). |
| IndieMaker | Site unreliable / times out. |
| BizBuySell | Blocks bots (403). |
| Dwarf / TinySoko / Transferslot / Borderline.biz / FundNAcquire | Long-tail micro sites: heavy cross-listing overlap with covered sources, no revenue verification, more breakage surface. Revisit only if real misses observed. |
| Shopify Exchange Marketplace | Shut down. |

---

## 10. Quick verification checklist for the next agent

- [ ] `git log origin/main` shows the latest code commit + periodic
      `heartbeat ... [skip ci]` bot commits on EVERY run (proves runs are firing
      AND dormancy protection works, even with zero hits).
- [ ] Actions tab shows green runs at ~7am/1pm/7pm ET.
- [ ] A dry run (`SMTP_HOST="" ... python saas_scout.py`) prints the
      `Sources fetched:` tally line plus FLIPPA / EMPIRE FLIPPERS / TRUSTMRR /
      ACQUIRE.COM / MICRONS / BUY SELL STARTUPS / REDDIT sections with clickable
      links (Acquire capped at 25).
- [ ] Flippa entries show business age (+ "young" flag under 12 mo) and listing
      date; Reddit entries show the post date.
- [ ] `CRITERIA` still matches `Acquistion Critera.md`.
