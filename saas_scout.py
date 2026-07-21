#!/usr/bin/env python3
"""
saas_scout.py  --  free multi-source SaaS deal scanner.

Sources it checks each run (all free, no paid API, no auth required):
  * Flippa  -- public /v3/listings JSON endpoint (full playbook filter:
               profit floor, price cap, multiple cap, bad-problem keywords)
  * Microns -- micro-SaaS marketplace homepage (annual-revenue floor;
               profit is paywalled so you confirm it on the listing page)
  * Empire Flippers -- public marketplace filtered to SaaS (full playbook
               filter; larger vetted businesses, name hidden until unlock)
  * Acquire.com -- public sitemap; surfaces SaaS whose slug reveals ARR/MRR/
               profit at/above your floor (exact price behind free login)
  * Reddit  -- free public RSS feed, no app/keys (keyword signal, eyeball)

Pipeline:
  fetch each source -> normalize -> filter -> dedupe across runs ->
  email ONLY the new passing listings to DEFAULT_DIGEST_TO.

Your acquisition criteria live in CRITERIA below; edit the numbers there.

--------------------------------------------------------------------
SETUP
--------------------------------------------------------------------
  pip install -r requirements.txt   (requests + beautifulsoup4)

  The ONLY thing you must supply is Gmail SMTP so it can email you.
  Create a Gmail App Password (myaccount.google.com -> Security ->
  2-Step Verification -> App Passwords), then export:

    export SMTP_HOST="smtp.gmail.com"
    export SMTP_PORT="587"
    export SMTP_USER="karanpatel7301@gmail.com"
    export SMTP_PASS="your_16_char_app_password"   # the App Password
    # DIGEST_TO is optional; defaults to karanpatel7301@gmail.com

  Put those exports in ~/.zshrc (local) or GitHub repo Secrets (cloud,
  see scout.yml). With no SMTP set, the script just PRINTS the digest so
  you can still test it end to end.
--------------------------------------------------------------------
"""

import os
import json
import pathlib
import datetime
import smtplib
import sys
from email.message import EmailMessage

import requests

# Load SMTP + optional keys from a local .env file if present, so you never
# have to export them by hand and cron/launchd picks them up automatically.
# .env is gitignored -- your App Password never gets committed. On GitHub
# Actions there's no .env; the creds come from repo Secrets instead.
try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).with_name(".env"))
except ImportError:
    pass


# ====================================================================
# YOUR PLAYBOOK  (applies to Flippa's structured listings)
# ====================================================================
CRITERIA = {
    # Hard floor from your playbook. You prefer $3K+, so listings at or
    # above prefer_monthly_profit get starred in the email.
    "min_monthly_profit": 2000,
    "prefer_monthly_profit": 3000,

    # Capital Efficiency Principle: maximize cash flow relative to price.
    # The playbook's own worked examples run 0.8x annual profit (the
    # $30k/$3k-mo deal) up to 2.5x (the $150k/$5k-mo deal). 2.5x is the
    # top of what your doc treats as acceptable, so we cap there -- any
    # richer than your most expensive example gets dropped.
    "max_multiple": 2.5,

    # Acquisition capital range from the playbook: $20K-$150K+. We allow a
    # little headroom above $150K for the "+", then hard-stop.
    "max_price": 200_000,

    "require_recurring": True,

    # Your "Bad Problems" column: trendy AI toys, novelty, one-time/entertainment.
    "exclude_keywords": [
        "ai humanizer", "humanize ai", "ai detector", "bypass ai",
        "gpt wrapper", "chatgpt wrapper", "ai girlfriend", "ai boyfriend",
        "faceless", "content generator", "ai content", "meme generator",
        "novelty", "quiz app", "soundboard", "wallpaper",
        # Entertainment / one-time-use = "Bad Problems" in your playbook.
        "game", "gaming", "dating", "astrology", "horoscope",
    ],
    "include_keywords": [],
}

# Reddit posts are freeform, so profit/multiple gates don't apply.
# Instead we surface posts that look like a sale and let you eyeball them.
# These subs have the highest hit-rate for people selling small SaaS /
# side projects. Add or remove freely.
REDDIT_SUBS = [
    # --- highest-signal "for sale" subs ---
    "saasforsale",            # literally SaaS businesses listed for sale
    "acquiresaas",            # SaaS acquisition marketplace sub
    "AcquireStartup",         # startups/SaaS put up for acquisition
    "microsaas",              # tiny SaaS, frequent sale posts
    "SaaS",                   # general SaaS, some "selling my app" posts
    "SaaSSales",              # SaaS-specific deal talk
    "AppBusiness",            # buying/selling apps & SaaS
    "flipping",               # asset flipping, incl. digital
    "juststart",              # website/app flipping community
    # --- founder/operator subs where exits get posted ---
    "EntrepreneurRideAlong",  # founders offloading side projects
    "SideProject",            # launches + occasional sales
    "Entrepreneur",           # large; keyword filter keeps it relevant
    "Entrepreneurs",          # sister sub, more sale/exit chatter
    "growmybusiness",         # owners looking to sell/partner
    "advancedentrepreneur",   # smaller, higher-quality operator posts
]
# Sale-intent phrases only, to keep the Reddit noise down. "acqui" covers
# acquire / acquisition / acquirer. Revenue mentions (mrr/arr) alone are too
# noisy, so they're intentionally excluded.
REDDIT_SIGNALS = [
    "for sale", "selling my", "sell my", "looking to sell", "open to offers",
    "make an offer", "up for grabs", "acqui", "for acquisition",
    "asking price", "flippa", "acquire.com", "microacquire",
]


# ====================================================================
# FLIPPA ENDPOINT + HEADERS  (paste from your browser)
# ====================================================================
FLIPPA_SEARCH_API_URL = (
    "https://flippa.com/v3/listings"
    "?filter[property_type]=saas"
    "&filter[status]=open"
    "&filter[sale_method]=classified"
    "&page[size]=50"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    # Pulled from the FLIPPA_COOKIE env var (a GitHub Secret) when set, so
    # it never gets committed. Falls back to whatever you paste here locally.
    "Cookie": os.environ.get("FLIPPA_COOKIE", ""),
}
MAX_FLIPPA_PAGES = 4

# Dedup memory. In GitHub Actions we point this at a file inside the repo
# (via SAAS_SCOUT_SEEN) so it survives between otherwise-ephemeral runs.
SEEN_FILE = pathlib.Path(
    os.environ.get("SAAS_SCOUT_SEEN", "~/.saas_scout_seen.json")
).expanduser()


# ====================================================================
# SOURCE 1: FLIPPA
# ====================================================================
def fetch_flippa() -> list[dict]:
    hits = []
    for page in range(1, MAX_FLIPPA_PAGES + 1):
        sep = "&" if "?" in FLIPPA_SEARCH_API_URL else "?"
        url = f"{FLIPPA_SEARCH_API_URL}{sep}page[number]={page}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            print(f"[flippa] request failed: {e}", file=sys.stderr)
            break
        if resp.status_code == 403:
            print("[flippa] 403 (Cloudflare). Refresh your Cookie or use "
                  "the Playwright fallback.", file=sys.stderr)
            break
        if not resp.ok:
            break
        raw = resp.json().get("data", [])
        if not raw:
            break
        hits.extend(normalize_flippa(r) for r in raw)
    return hits


def normalize_flippa(raw: dict) -> dict:
    # Flippa's /v3/listings feed returns the fields directly on the record
    # (not nested under "attributes"), but we keep the fallback just in case.
    a = raw.get("attributes", raw)

    def first(*keys, default=None):
        for k in keys:
            v = a.get(k)
            if v not in (None, "", 0):
                return v
        return default

    # Real field names confirmed live against the endpoint:
    #   profit_per_month / average_profit, current_price / display_price,
    #   revenue_per_month, business_model, html_url.
    profit = first("profit_per_month", "average_profit", default=0) or 0
    price = first("current_price", "display_price", "buy_it_now_price", default=0) or 0
    revenue = first("revenue_per_month", "average_revenue", default=0) or 0
    lid = str(raw.get("id") or first("id", default=""))
    annual = profit * 12
    url = first("html_url", default="") or (f"https://flippa.com/{lid}" if lid else "")
    # established_at = when the BUSINESS was created (business age -> durability
    # signal); starts_at = when the LISTING was posted. Both display-only.
    established = first("established_at", default=None)
    return {
        "source": "flippa",
        "id": f"flippa:{lid}",
        "title": first("title", "property_name", default="") or "",
        "summary": first("summary", "description", default="") or "",
        "price": price,
        "profit_monthly": profit,
        "revenue_monthly": revenue,
        # SaaS listings rarely populate business_model in this feed; the
        # property_type=saas filter already guarantees recurring software.
        "monetization": (first("business_model", default="") or "").lower(),
        "verified_revenue": bool(a.get("has_verified_revenue")),
        "multiple": (price / annual) if annual else None,
        "business_age_months": _months_since(established),
        "posted_date": (first("starts_at", default="") or "")[:10] or None,
        "url": url,
    }


def _months_since(iso: str | None):
    """Whole months between an ISO date and now, or None. Display-only signal."""
    if not iso:
        return None
    try:
        dt = datetime.datetime.fromisoformat(iso)
    except ValueError:
        return None
    now = datetime.datetime.now(dt.tzinfo)
    return max(0, (now.year - dt.year) * 12 + (now.month - dt.month))


def passes_flippa(x: dict) -> bool:
    c = CRITERIA
    text = f"{x['title']} {x['summary']}".lower()
    if x["profit_monthly"] < c["min_monthly_profit"]:
        return False
    if c["max_price"] is not None and x["price"] > c["max_price"]:
        return False
    if c["max_multiple"] is not None:
        if x["multiple"] is None or x["multiple"] > c["max_multiple"]:
            return False
    if c["require_recurring"] and x["monetization"]:
        if not any(w in x["monetization"]
                   for w in ("subscription", "recurring", "saas", "membership")):
            return False
    if any(kw in text for kw in c["exclude_keywords"]):
        return False
    if c["include_keywords"] and not any(kw in text for kw in c["include_keywords"]):
        return False
    return True


# ====================================================================
# SOURCE 2: REDDIT  (free public RSS feed -- NO app, NO keys needed)
# ====================================================================
# Reddit blocks anonymous .json and gates app creation, but the RSS/Atom
# feed is still served to a browser UA with no auth. Hitting one sub at a
# time gets you rate-limited (429) fast, so we combine ALL subs into a
# SINGLE request via the /r/a+b+c/new.rss syntax -- one call, no throttling.
import time
import re as _re
import xml.etree.ElementTree as _ET

_ATOM = {"a": "http://www.w3.org/2005/Atom"}


def fetch_reddit() -> list[dict]:
    hits = []
    ua = {"User-Agent": HEADERS["User-Agent"]}
    combined = "+".join(REDDIT_SUBS)
    url = f"https://www.reddit.com/r/{combined}/new.rss?limit=100"

    xml = None
    for attempt in range(4):
        try:
            resp = requests.get(url, headers=ua, timeout=30)
        except requests.RequestException as e:
            print(f"[reddit] request failed: {e}", file=sys.stderr)
            return hits
        if resp.status_code == 200 and resp.text.strip():
            xml = resp.text
            break
        time.sleep(5 * (attempt + 1))  # back off through any 429
    if not xml:
        print("[reddit] rate-limited/empty after retries; skipping this run.",
              file=sys.stderr)
        return hits

    try:
        root = _ET.fromstring(xml)
    except _ET.ParseError as e:
        print(f"[reddit] parse error: {e}", file=sys.stderr)
        return hits

    for entry in root.findall("a:entry", _ATOM):
        title_el = entry.find("a:title", _ATOM)
        id_el = entry.find("a:id", _ATOM)
        link_el = entry.find("a:link", _ATOM)
        content_el = entry.find("a:content", _ATOM)
        cat_el = entry.find("a:category", _ATOM)
        pub_el = entry.find("a:published", _ATOM)
        title = (title_el.text if title_el is not None else "") or ""
        body = (content_el.text if content_el is not None else "") or ""
        # content is HTML-escaped; strip tags for keyword matching only.
        body_txt = _re.sub(r"<[^>]+>", " ", body)
        text = f"{title} {body_txt}".lower()
        if not any(sig in text for sig in REDDIT_SIGNALS):
            continue
        sub = (cat_el.get("label") if cat_el is not None else "") or "reddit"
        pid = (id_el.text if id_el is not None else "") or title
        url = link_el.get("href") if link_el is not None else ""
        # Posting date is free in the RSS feed; display only, never filter on it.
        posted = (pub_el.text[:10] if pub_el is not None and pub_el.text else None)
        hits.append({
            "source": f"reddit/{sub.lstrip('r/')}",
            "id": f"reddit:{pid}",
            "title": title,
            "summary": body_txt.strip()[:200],
            "price": None,
            "profit_monthly": None,
            "monetization": "",
            "multiple": None,
            "posted_date": posted,
            "url": url,
        })
    return hits


# ====================================================================
# SOURCE 3: MICRONS  (micro-SaaS marketplace, public listing cards)
# ====================================================================
# Microns shows ANNUAL REVENUE and ASKING PRICE on public cards but
# hides profit behind Premium ($40/mo). So your profit floor can't be
# applied directly. Instead we use annual revenue as a NECESSARY
# condition: nothing can clear $2k/mo profit on under $2k/mo revenue,
# so anything below min_monthly_profit * 12 in annual revenue is safe
# to drop. You confirm the actual profit when you open the listing.
#
# NOTE: the /categories/saas page only lists SOLD deals. The homepage is
# where the currently-live acquisition opportunities are rendered.
MICRONS_URL = "https://www.microns.io/"
MICRONS_MIN_ANNUAL_REVENUE = CRITERIA["min_monthly_profit"] * 12


def fetch_microns() -> list[dict]:
    from bs4 import BeautifulSoup  # local import so Flippa/Reddit run without bs4
    ua = {"User-Agent": HEADERS["User-Agent"]}
    try:
        resp = requests.get(MICRONS_URL, headers=ua, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[microns] request failed: {e}", file=sys.stderr)
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    # Webflow renders each listing as a .w-dyn-item collection card; only the
    # ones containing a /startup-listings/ link are real listings.
    cards = [
        c for c in soup.select(".w-dyn-item")
        if c.find("a", href=lambda h: h and "/startup-listings/" in h)
    ]
    if not cards:
        print("[microns] no listing cards found; Webflow markup may have "
              "changed, check the selector.", file=sys.stderr)
        return []
    return [h for h in (normalize_microns(c) for c in cards) if h]


def normalize_microns(card):
    import re
    link = card.find("a", href=lambda h: h and "/startup-listings/" in h)
    if not link:
        return None
    href = link["href"]
    slug = href.rstrip("/").split("/startup-listings/")[-1]
    text = card.get_text(" ", strip=True)

    if "SOLD" in text.upper():
        return None  # already gone

    def money(label):
        m = re.search(rf"\$([\d,]+)\s*{label}", text)
        return int(m.group(1).replace(",", "")) if m else None

    heading = card.find(["h5", "h4", "h3"])  # first heading in the card is the title
    title = heading.get_text(strip=True) if heading else slug.replace("-", " ").title()

    cat_el = card.select_one(".listing-card-tag, .tag-mc")
    category = cat_el.get_text(strip=True) if cat_el else ""

    return {
        "source": "microns",
        "id": f"microns:{slug}",
        "title": title,
        "summary": text[:200],
        "category": category,
        "price": money("Asking Price") or 0,
        "profit_monthly": None,                 # not public on Microns
        "annual_revenue": money("Annual Revenue"),
        "monetization": "",
        "multiple": None,
        "url": href if href.startswith("http") else f"https://www.microns.io{href}",
    }


# Your playbook is recurring-subscription SaaS. Microns also lists mobile
# apps, e-commerce, marketplaces and newsletters -- keep only SaaS-shaped
# categories so the digest stays on-criteria.
MICRONS_ALLOWED_CATEGORIES = {"micro-saas", "saas", "web app"}


def passes_microns(x: dict) -> bool:
    text = f"{x['title']} {x['summary']}".lower()
    cat = (x.get("category") or "").lower()
    if cat and cat not in MICRONS_ALLOWED_CATEGORIES:
        return False
    if any(kw in text for kw in CRITERIA["exclude_keywords"]):
        return False
    rev = x.get("annual_revenue")
    if rev is not None and rev < MICRONS_MIN_ANNUAL_REVENUE:
        return False  # can't possibly meet your monthly profit floor
    if CRITERIA["max_price"] and x["price"] and x["price"] > CRITERIA["max_price"]:
        return False
    return True


# ====================================================================
# SOURCE 4: EMPIRE FLIPPERS  (public marketplace, SaaS filter)
# ====================================================================
# EF renders listing cards server-side, and supports ?monetization=saas
# to return only SaaS/subscription businesses -- one clean request. Full
# profit/price/multiple data is public, so the normal playbook filter
# applies exactly like Flippa. EF deals skew large, so most will exceed
# your max_price; the few in-budget SaaS assets are the point.
EF_URL = "https://empireflippers.com/marketplace/?monetization=saas"


def fetch_empireflippers() -> list[dict]:
    from bs4 import BeautifulSoup
    ua = {"User-Agent": HEADERS["User-Agent"]}
    try:
        resp = requests.get(EF_URL, headers=ua, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[empireflippers] request failed: {e}", file=sys.stderr)
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.select(".listing-item")
    if not cards:
        print("[empireflippers] no .listing-item cards; markup may have changed.",
              file=sys.stderr)
        return []
    return [h for h in (normalize_ef(c) for c in cards) if h]


def normalize_ef(card) -> dict | None:
    import re

    def val(sel):
        el = card.select_one(sel)
        return el.get_text(strip=True) if el else ""

    def money(sel):
        m = re.search(r"\$([\d,]+)", val(sel))
        return int(m.group(1).replace(",", "")) if m else 0

    num_el = card.select_one(".listing-number")
    num = (num_el.get_text(strip=True).lstrip("#") if num_el else "")
    if not num:
        return None
    price = money(".metric-item.price .value")
    profit = money(".metric-item.profit .value")
    revenue = money(".metric-item.revenue .value")
    monetization = val(".metric-item.monetization .value")
    niche = val(".niches") or val(".top-info-niches")
    annual = profit * 12
    title = f"{niche} — {monetization}".strip(" —") or f"EF listing {num}"
    return {
        "source": "empireflippers",
        "id": f"ef:{num}",
        "title": title,
        "summary": monetization,
        "price": price,
        "profit_monthly": profit,
        "revenue_monthly": revenue,
        "monetization": monetization.lower(),
        "multiple": (price / annual) if annual else None,
        "url": f"https://empireflippers.com/listing/{num}/",
    }


def passes_ef(x: dict) -> bool:
    c = CRITERIA
    text = f"{x['title']} {x['summary']}".lower()
    if x["profit_monthly"] < c["min_monthly_profit"]:
        return False
    if c["max_price"] is not None and x["price"] and x["price"] > c["max_price"]:
        return False
    if c["max_multiple"] is not None:
        if x["multiple"] is None or x["multiple"] > c["max_multiple"]:
            return False
    if any(kw in text for kw in c["exclude_keywords"]):
        return False
    return True


# ====================================================================
# SOURCE 5: ACQUIRE.COM  (public sitemap; full data behind free login)
# ====================================================================
# Acquire.com (ex-MicroAcquire) hides the full listing behind a free login,
# BUT its public sitemap (app.acquire.com/sitemap.xml) exposes every listing
# URL, and the slug embeds the title plus -- for a good chunk of them -- real
# financials (ARR / MRR / TTM profit). We surface ONLY listings whose slug
# reveals a revenue/profit signal at or above your floor, so you get concrete
# leads even though the exact price is on the (free) listing page.
#
# The whole catalog (~1000+) carries today's date, so we can't detect "new"
# by date -- dedup via seen.json does that. To avoid flooding the first email,
# main() surfaces at most ACQUIRE_MAX_PER_RUN, smallest-revenue (most likely
# in your budget) first, and only marks the ones it actually emails as seen.
ACQUIRE_SITEMAP = "https://app.acquire.com/sitemap.xml"
ACQUIRE_MAX_PER_RUN = 25
ACQUIRE_NON_SAAS = [
    "shopify", "ecommerce", "e-commerce", "dtc", "amazon", "fba", "-store",
    "brand", "hemp", "toys", "newsletter", "agency", "dropship", "wellness-e",
]


def _acquire_financials(slug: str) -> dict:
    import re
    out = {}
    for kind in ("arr", "mrr", "profit", "revenue"):
        m = re.search(r"(\d+(?:-\d+)?)(k|m)-(?:ttm-|usd-yr-|yoy-|net-)?" + kind, slug)
        if m and not (kind == "profit" and "profit-margin" in slug):
            num = m.group(1).replace("-", ".")
            out[kind] = float(num) * (1000 if m.group(2) == "k" else 1_000_000)
    return out


def fetch_acquire() -> list[dict]:
    import re
    ua = {"User-Agent": HEADERS["User-Agent"]}
    try:
        resp = requests.get(ACQUIRE_SITEMAP, headers=ua, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[acquire] request failed: {e}", file=sys.stderr)
        return []
    locs = [l for l in re.findall(r"<loc>(.*?)</loc>", resp.text) if "/startup/" in l]
    out = []
    for loc in locs:
        slug = loc.split("/startup/")[-1]
        # first 10 chars are the listing id, then a dash, then the title slug.
        code = slug[:10]
        rest = slug[11:] if len(slug) > 11 else slug
        low = rest.lower()
        if any(k in low for k in ACQUIRE_NON_SAAS):
            continue
        if any(kw in low for kw in CRITERIA["exclude_keywords"]):
            continue
        fin = _acquire_financials(low)
        if not fin:
            continue  # no readable financials -> can't assess, skip
        monthly = max([fin.get("arr", 0) / 12, fin.get("mrr", 0),
                       fin.get("revenue", 0) / 12, fin.get("profit", 0) / 12])
        if monthly < CRITERIA["min_monthly_profit"]:
            continue
        parts = []
        if "arr" in fin: parts.append(f"${int(fin['arr']):,} ARR")
        if "mrr" in fin: parts.append(f"${int(fin['mrr']):,} MRR")
        if "profit" in fin: parts.append(f"${int(fin['profit']):,} profit")
        if "revenue" in fin: parts.append(f"${int(fin['revenue']):,} rev")
        out.append({
            "source": "acquire",
            "id": f"acquire:{code}",
            "title": rest.replace("-", " ").strip(),
            "summary": ", ".join(parts),
            "monthly_est": monthly,
            "url": loc,
        })
    # smallest revenue first == most likely inside your $20K-$150K budget.
    out.sort(key=lambda x: x["monthly_est"])
    return out


# ====================================================================
# SOURCE 6: TRUSTMRR  (revenue verified via Stripe/LemonSqueezy/RevenueCat)
# ====================================================================
# TrustMRR's "for sale" cards are currently in the homepage HTML as
# <a href="/startup/..."> tiles reading e.g.:
#   "FOR SALE ReddGrow AI | Marketing | Revenue $17k Price $1M Multiple 4.7x"
# Revenue is MONTHLY and third-party VERIFIED, which is why it's worth trust.
# Cards show revenue (not profit), so we gate on annual revenue like Microns
# and tell the owner to confirm profit on the listing page.
#
# JS-SPA warning: modern marketplaces often hydrate listings client-side. If
# this fetch suddenly returns 0 FOR SALE cards, do NOT assume the selectors
# are wrong first -- the page may have gone fully JS-rendered (the dedicated
# /acquire route already is). Tell: raw HTML has no /startup/ FOR SALE links.
# Fix: find a JSON endpoint in DevTools Network (like Flippa) or fall back to
# a headless browser. Microns worked with plain requests only because it is
# server-rendered Webflow; that is not guaranteed here.
TRUSTMRR_URL = "https://trustmrr.com/"
TRUSTMRR_NON_SAAS = ["e-commerce", "ecommerce"]


def _money(text: str):
    import re
    # Unit must be ADJACENT to the number (no space) and not the first letter
    # of a following word -- otherwise "$67 Multiple" would read the "M" of
    # "Multiple" as millions.
    m = re.search(r"\$\s*([\d.,]+)(k|m|b)?(?![a-z])", text, re.I)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    u = (m.group(2) or "").lower()
    return v * (1000 if u == "k" else 1_000_000 if u == "m"
               else 1_000_000_000 if u == "b" else 1)


def fetch_trustmrr() -> list[dict]:
    from bs4 import BeautifulSoup
    ua = {"User-Agent": HEADERS["User-Agent"]}
    try:
        resp = requests.get(TRUSTMRR_URL, headers=ua, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[trustmrr] request failed: {e}", file=sys.stderr)
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    seen_href, out = set(), []
    for a in soup.find_all("a", href=lambda h: h and h.startswith("/startup/")):
        text = a.get_text(" ", strip=True)
        if "FOR SALE" not in text.upper():
            continue
        href = a["href"]
        if href in seen_href:
            continue
        seen_href.add(href)
        h = normalize_trustmrr(text, href)
        if h:
            out.append(h)
    if not out:
        print("[trustmrr] no FOR SALE cards found; markup may have changed.",
              file=sys.stderr)
    return out


def normalize_trustmrr(text: str, href: str) -> dict | None:
    import re
    slug = href.rstrip("/").split("/startup/")[-1]
    body = re.sub(r"^\s*FOR SALE\s*", "", text, flags=re.I)
    title = re.split(r"\b(Revenue|Price|Profit|Multiple)\b", body)[0].strip()

    def field(label):
        m = re.search(label + r"\s*(\$[\d.,]+[km]?)", text, re.I)
        return _money(m.group(1)) if m else None

    rev = field("Revenue")
    price = field("Price")
    profit = field("Profit")
    return {
        "source": "trustmrr",
        "id": f"trustmrr:{slug}",
        "title": title[:80] or slug.replace("-", " ").title(),
        "summary": body[:200],
        "revenue_monthly": rev,
        "annual_revenue": int(rev * 12) if rev else None,
        "profit_monthly": profit,
        "price": price or 0,
        "url": f"https://trustmrr.com{href}",
    }


def passes_trustmrr(x: dict) -> bool:
    text = x["summary"].lower()
    if any(k in text for k in TRUSTMRR_NON_SAAS):
        return False
    if any(kw in text for kw in CRITERIA["exclude_keywords"]):
        return False
    if CRITERIA["max_price"] and x["price"] and x["price"] > CRITERIA["max_price"]:
        return False
    if x.get("profit_monthly"):
        return x["profit_monthly"] >= CRITERIA["min_monthly_profit"]
    rev = x.get("annual_revenue")
    if rev is None:
        return False  # revenue masked/confidential -> can't verify the floor
    return rev >= CRITERIA["min_monthly_profit"] * 12


# ====================================================================
# SOURCE 7: BUY SELL STARTUPS
# ====================================================================
# buysellstartups.com currently ships a handful of listing cards in the
# homepage HTML as <a href="/listings/..."> tiles reading e.g.:
#   "SaaS Featured Mentioned ... Asking Sign in -> MRR $1.0k Multiple 0.8x"
# Asking price is often gated behind login ("Sign in"); MRR is shown when the
# seller isn't confidential. We gate on MRR (annual) like the other
# revenue-only sources and note to confirm profit/price on the listing.
#
# JS-SPA warning: /browse and /listings are already client-side (404/empty to
# a bot). If the homepage fetch returns 0 /listings/ cards, treat that as a
# JS-render regression first, not a bad selector. Fix: JSON endpoint from the
# Network tab (like Flippa) or a headless browser -- same playbook as TrustMRR.
BSS_URL = "https://buysellstartups.com/"


def fetch_buysellstartups() -> list[dict]:
    from bs4 import BeautifulSoup
    import re
    ua = {"User-Agent": HEADERS["User-Agent"]}
    try:
        resp = requests.get(BSS_URL, headers=ua, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[buysellstartups] request failed: {e}", file=sys.stderr)
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    seen_href, out = set(), []
    for a in soup.find_all("a", href=True):
        if not re.match(r"/listings/[\w-]+", a["href"]):
            continue
        href = a["href"]
        if href in seen_href:
            continue
        seen_href.add(href)
        h = normalize_bss(a.get_text(" ", strip=True), href)
        if h:
            out.append(h)
    if not out:
        print("[buysellstartups] no /listings/ cards found; markup may have "
              "changed.", file=sys.stderr)
    return out


def normalize_bss(text: str, href: str) -> dict | None:
    import re
    slug = href.rstrip("/").split("/listings/")[-1]

    def field(label):
        # Unit adjacent to the number only; "MRR $67 Multiple" must read 67,
        # not 67 million from the "M" in "Multiple".
        m = re.search(label + r"\s*\$([\d.,]+)(k|m)?(?![a-z])", text, re.I)
        if not m:
            return None
        u = (m.group(2) or "").lower()
        return float(m.group(1).replace(",", "")) * (
            1000 if u == "k" else 1_000_000 if u == "m" else 1)

    mrr = field("MRR")
    asking = field("Asking")
    title = re.split(r"\bAsking\b", text)[0]
    title = re.sub(r"^(SaaS|Featured|Confidential|↗|\s)+", "", title, flags=re.I).strip()
    return {
        "source": "buysellstartups",
        "id": f"bss:{slug}",
        "title": title[:80] or slug,
        "summary": text[:200],
        "revenue_monthly": mrr,
        "annual_revenue": int(mrr * 12) if mrr else None,
        "price": asking or 0,
        "url": f"https://buysellstartups.com{href}",
    }


def passes_buysellstartups(x: dict) -> bool:
    text = x["summary"].lower()
    if any(kw in text for kw in CRITERIA["exclude_keywords"]):
        return False
    if CRITERIA["max_price"] and x["price"] and x["price"] > CRITERIA["max_price"]:
        return False
    rev = x.get("annual_revenue")
    if rev is None:
        return False  # MRR masked/confidential -> can't verify the floor
    return rev >= CRITERIA["min_monthly_profit"] * 12


def load_seen() -> set[str]:
    return set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()


def save_seen(seen: set[str]) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(seen)))


# ====================================================================
# EMAIL
# ====================================================================
def build_body(hits: list[dict], raw_counts: dict | None = None) -> str:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"SaaS Scout digest  {stamp}", ""]
    # Source tally: raw counts fetched from each source BEFORE dedup/filter.
    # A 0 where a number is expected = a silently broken scraper (this is how
    # the old Flippa profit-key regression would have been caught instantly).
    if raw_counts:
        tally = " | ".join(f"{name} {n}" for name, n in raw_counts.items())
        lines.append(f"Sources fetched: {tally}")
        lines.append("(raw counts before dedup/filter; a 0 may mean a broken "
                     "scraper -- investigate.)")
        lines.append("")
    flippa = [h for h in hits if h["source"] == "flippa"]
    reddit = [h for h in hits if h["source"].startswith("reddit")]
    microns = [h for h in hits if h["source"] == "microns"]
    ef = [h for h in hits if h["source"] == "empireflippers"]
    acquire = [h for h in hits if h["source"] == "acquire"]
    trustmrr = [h for h in hits if h["source"] == "trustmrr"]
    bss = [h for h in hits if h["source"] == "buysellstartups"]

    if flippa:
        lines.append(f"FLIPPA -- {len(flippa)} passed the machine filter:")
        lines.append("(* = hits your $3K+ preference. Still verify by hand: "
                     "organic growth, churn, competition, panic test -- none "
                     "of those are in listing data.)")
        for h in sorted(flippa, key=lambda x: x["multiple"] or 99):
            mult = f"{h['multiple']:.1f}x" if h["multiple"] else "n/a"
            star = " *" if (h["profit_monthly"] or 0) >= CRITERIA["prefer_monthly_profit"] else ""
            vrf = " [verified rev]" if h.get("verified_revenue") else ""
            rev = f" | ${h['revenue_monthly']:,}/mo rev" if h.get("revenue_monthly") else ""
            lines.append(f"  {h['title'][:70]}{star}{vrf}")
            lines.append(f"    ${h['profit_monthly']:,}/mo profit{rev} | ask ${h['price']:,} | {mult}")
            age = h.get("business_age_months")
            if age is not None:
                flag = "  <- young, scrutinize churn/durability" if age < 12 else ""
                meta = f"    business age {age} mo{flag}"
                if h.get("posted_date"):
                    meta += f" | listed {h['posted_date']}"
                lines.append(meta)
            elif h.get("posted_date"):
                lines.append(f"    listed {h['posted_date']}")
            lines.append(f"    {h['url']}")
        lines.append("")
    if reddit:
        lines.append(f"REDDIT -- {len(reddit)} posts to eyeball (no auto-filter):")
        for h in reddit:
            posted = f"  ({h['posted_date']})" if h.get("posted_date") else ""
            lines.append(f"  [{h['source']}] {h['title'][:80]}{posted}")
            lines.append(f"    {h['url']}")
        lines.append("")
    if microns:
        lines.append(f"MICRONS -- {len(microns)} passed the revenue floor:")
        lines.append("(shows annual revenue, not profit -- confirm profit and "
                     "margin on the listing page before getting interested.)")
        for h in microns:
            rev = f"${h['annual_revenue']:,}/yr rev" if h.get("annual_revenue") else "rev n/a"
            price = f"ask ${h['price']:,}" if h["price"] else "ask n/a"
            cat = f" [{h['category']}]" if h.get("category") else ""
            lines.append(f"  {h['title'][:70]}{cat}")
            lines.append(f"    {rev} | {price}")
            lines.append(f"    {h['url']}")
        lines.append("")
    if ef:
        lines.append(f"EMPIRE FLIPPERS -- {len(ef)} SaaS passed the machine filter:")
        lines.append("(bigger, vetted businesses; EF verifies financials. "
                     "Actual business name is hidden until you unlock it.)")
        for h in sorted(ef, key=lambda x: x["multiple"] or 99):
            mult = f"{h['multiple']:.1f}x" if h["multiple"] else "n/a"
            star = " *" if (h["profit_monthly"] or 0) >= CRITERIA["prefer_monthly_profit"] else ""
            rev = f" | ${h['revenue_monthly']:,}/mo rev" if h.get("revenue_monthly") else ""
            lines.append(f"  {h['title'][:70]}{star}")
            lines.append(f"    ${h['profit_monthly']:,}/mo profit{rev} | ask ${h['price']:,} | {mult}")
            lines.append(f"    {h['url']}")
        lines.append("")
    if acquire:
        lines.append(f"ACQUIRE.COM -- {len(acquire)} SaaS with revenue signals:")
        lines.append("(financials parsed from the listing title; exact price is "
                     "on the listing page behind a free Acquire login. Smallest "
                     "first = most likely in your budget.)")
        for h in sorted(acquire, key=lambda x: x.get("monthly_est", 0)):
            lines.append(f"  {h['title'][:70]}")
            lines.append(f"    {h['summary']}")
            lines.append(f"    {h['url']}")
        lines.append("")
    if trustmrr:
        lines.append(f"TRUSTMRR -- {len(trustmrr)} passed the revenue floor:")
        lines.append("(revenue is third-party VERIFIED via Stripe/LemonSqueezy/"
                     "RevenueCat, but it's revenue not profit -- confirm profit "
                     "and margin on the listing page.)")
        for h in sorted(trustmrr, key=lambda x: x.get("annual_revenue") or 0):
            rev = f"${h['annual_revenue']:,}/yr rev" if h.get("annual_revenue") else "rev n/a"
            price = f"ask ${h['price']:,.0f}" if h["price"] else "ask n/a"
            lines.append(f"  {h['title'][:70]}")
            lines.append(f"    {rev} | {price}")
            lines.append(f"    {h['url']}")
        lines.append("")
    if bss:
        lines.append(f"BUY SELL STARTUPS -- {len(bss)} passed the revenue floor:")
        lines.append("(shows MRR; asking price is often behind a free login -- "
                     "confirm profit, margin and price on the listing page.)")
        for h in sorted(bss, key=lambda x: x.get("annual_revenue") or 0):
            rev = f"${h['annual_revenue']:,}/yr rev" if h.get("annual_revenue") else "rev n/a"
            price = f"ask ${h['price']:,.0f}" if h["price"] else "ask (login)"
            lines.append(f"  {h['title'][:70]}")
            lines.append(f"    {rev} | {price}")
            lines.append(f"    {h['url']}")
        lines.append("")
    if not hits:
        lines.append("Nothing new cleared today.")
    return "\n".join(lines)


# Where the digest goes. Override with the DIGEST_TO env var if you ever
# want it sent somewhere else.
DEFAULT_DIGEST_TO = "karanpatel7301@gmail.com"


def send_email(body: str) -> None:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASS")
    to = os.environ.get("DIGEST_TO", DEFAULT_DIGEST_TO)
    if not host or not user or not pw:
        print("[email] SMTP_HOST/SMTP_USER/SMTP_PASS not set; printing digest "
              "instead of sending:\n")
        print(body)
        return
    msg = EmailMessage()
    msg["Subject"] = f"SaaS Scout {datetime.date.today().isoformat()}"
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)
    print(f"[email] sent to {to}")


# ====================================================================
# MAIN
# ====================================================================
def main() -> None:
    seen = load_seen()
    new_hits = []
    raw_counts = {}

    # Each source: fetch once (record the raw count for the tally line), then
    # dedup against seen.json and apply that source's filter.
    flippa = fetch_flippa()
    raw_counts["Flippa"] = len(flippa)
    for h in flippa:
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        if passes_flippa(h):
            new_hits.append(h)

    ef = fetch_empireflippers()
    raw_counts["Empire Flippers"] = len(ef)
    for h in ef:
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        if passes_ef(h):
            new_hits.append(h)

    trustmrr = fetch_trustmrr()
    raw_counts["TrustMRR"] = len(trustmrr)
    for h in trustmrr:
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        if passes_trustmrr(h):
            new_hits.append(h)

    bss = fetch_buysellstartups()
    raw_counts["Buy Sell Startups"] = len(bss)
    for h in bss:
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        if passes_buysellstartups(h):
            new_hits.append(h)

    microns = fetch_microns()
    raw_counts["Microns"] = len(microns)
    for h in microns:
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        if passes_microns(h):
            new_hits.append(h)

    reddit = fetch_reddit()
    raw_counts["Reddit"] = len(reddit)
    for h in reddit:
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        new_hits.append(h)  # reddit already keyword-filtered

    # Acquire.com: cap per run and only mark the ones we actually surface as
    # seen, so the backlog drips out over a few runs instead of flooding.
    acquire = fetch_acquire()
    raw_counts["Acquire"] = len(acquire)
    acquire_count = 0
    for h in acquire:
        if h["id"] in seen:
            continue
        if acquire_count >= ACQUIRE_MAX_PER_RUN:
            break
        seen.add(h["id"])
        new_hits.append(h)
        acquire_count += 1

    save_seen(seen)

    # Don't spam empty digests three times a day. Only email when something
    # new cleared the filters; otherwise just log it for the run output.
    force = os.environ.get("SAAS_SCOUT_ALWAYS_EMAIL") == "1"
    if new_hits or force:
        send_email(build_body(new_hits, raw_counts))
    else:
        print("[scout] no new listings cleared the filters this run; "
              "no email sent.")
        print("[scout] tally: " +
              " | ".join(f"{k} {v}" for k, v in raw_counts.items()))


if __name__ == "__main__":
    main()
