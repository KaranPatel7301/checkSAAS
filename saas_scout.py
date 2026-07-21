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

    # NOT from your playbook. Your doc specifies ROIC / cash-flow-to-price,
    # not a fixed multiple. Your own examples run ~0.8x (the $30k/$3k deal)
    # to 2.5x (the $150k/$5k deal). 4.0x is a loose ceiling I picked just
    # to drop obviously overpriced listings. Tighten it toward your examples.
    "max_multiple": 4.0,

    # Guardrail near your $20K-$150K+ capital range.
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
    "microsaas",              # highest signal: tiny SaaS, frequent sale posts
    "SaaS",                   # general SaaS, some "selling my app" posts
    "SaaSSales",              # SaaS-specific deal talk
    "EntrepreneurRideAlong",  # founders offloading side projects
    "SideProject",            # launches + occasional sales
    "AppBusiness",            # buying/selling apps & SaaS
    "juststart",              # website/app flipping community
    "flipping",               # asset flipping, incl. digital
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
        "url": url,
    }


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
        hits.append({
            "source": f"reddit/{sub.lstrip('r/')}",
            "id": f"reddit:{pid}",
            "title": title,
            "summary": body_txt.strip()[:200],
            "price": None,
            "profit_monthly": None,
            "monetization": "",
            "multiple": None,
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


def load_seen() -> set[str]:
    return set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()


def save_seen(seen: set[str]) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(seen)))


# ====================================================================
# EMAIL
# ====================================================================
def build_body(hits: list[dict]) -> str:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"SaaS Scout digest  {stamp}", ""]
    flippa = [h for h in hits if h["source"] == "flippa"]
    reddit = [h for h in hits if h["source"].startswith("reddit")]
    microns = [h for h in hits if h["source"] == "microns"]
    ef = [h for h in hits if h["source"] == "empireflippers"]

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
            lines.append(f"    {h['url']}")
        lines.append("")
    if reddit:
        lines.append(f"REDDIT -- {len(reddit)} posts to eyeball (no auto-filter):")
        for h in reddit:
            lines.append(f"  [{h['source']}] {h['title'][:80]}")
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

    for h in fetch_flippa():
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        if passes_flippa(h):
            new_hits.append(h)

    for h in fetch_reddit():
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        new_hits.append(h)  # reddit already keyword-filtered

    for h in fetch_microns():
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        if passes_microns(h):
            new_hits.append(h)

    for h in fetch_empireflippers():
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        if passes_ef(h):
            new_hits.append(h)

    save_seen(seen)

    # Don't spam empty digests three times a day. Only email when something
    # new cleared the filters; otherwise just log it for the run output.
    force = os.environ.get("SAAS_SCOUT_ALWAYS_EMAIL") == "1"
    if new_hits or force:
        send_email(build_body(new_hits))
    else:
        print("[scout] no new listings cleared the filters this run; "
              "no email sent.")


if __name__ == "__main__":
    main()
