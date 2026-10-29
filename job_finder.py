"""Find and rank jobs from free public job feeds. It never applies: it only builds a report and a tracker.

First run: py setup.py (asks a few questions and writes my_config.json).

Usage:
    py job_finder.py            # fetch all sources, write reports/<date>.md, add new jobs to tracker.csv
    py job_finder.py --all      # include jobs already seen in earlier runs
    py job_finder.py --only himalayas,teamwork   # run some sources only
    py job_finder.py --kit <TrackerID>           # save one job's full posting for cover-letter drafting
"""
import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent


def layer(cfg, extra):
    """Merge one settings dict over another; nested dicts (like col) merge key by key."""
    for k, v in extra.items():
        cfg[k] = {**cfg[k], **v} if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v


# config.json (shared defaults) <- packs/<name>.json (industry packs) <- my_config.json (your answers from setup.py)
CFG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
MINE = json.loads((HERE / "my_config.json").read_text(encoding="utf-8")) if (HERE / "my_config.json").exists() else {}
for pack in MINE.get("packs", CFG["packs"]):
    layer(CFG, json.loads((HERE / "packs" / f"{pack}.json").read_text(encoding="utf-8")))
layer(CFG, MINE)
US_STATES = [c.lower() for c in CFG["col"]["states"]]
DREAM_TAG = CFG["dream_label"].upper() or "DREAM"  # first "why" entry on dream-industry jobs; they always get listed
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) job-finder/1.0 (personal use)"
SEEN_FILE = HERE / "seen.json"
TRACKER = HERE / "tracker.csv"
CACHE = HERE / "jobs_cache.json"


# ---------- helpers ----------

def get(url, retries=3, data=None, headers=None):
    """GET (or POST with data) and return text, retrying with backoff."""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # network errors, 429s, 5xx
            if attempt == retries:
                print(f"  ! {url[:80]} failed: {e}", file=sys.stderr)
                return None
            time.sleep(2 * attempt)


def strip_html(s):
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


MONEY = re.compile(r"\$\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d)?\s?[kK])")


def parse_salary(text):
    """Pull annual dollar amounts like $120,000 or $120k out of text. Returns (min, max) or (None, None)."""
    vals = []
    for m in MONEY.finditer(text or ""):
        raw = m.group(1).replace(",", "").replace(" ", "")
        n = float(raw[:-1]) * 1000 if raw[-1] in "kK" else float(raw)
        if 40_000 <= n <= 500_000:  # skips hourly rates and nonsense
            vals.append(int(n))
    return (min(vals), max(vals)) if vals else (None, None)


def to_int(v):
    try:
        n = int(float(v))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def job(source, title, company, location, url, description="", salary_min=None, salary_max=None, posted="",
        currency=None):
    desc = strip_html(description)
    if currency and currency.upper() != "USD":  # 272000 PLN or 775000 SEK isn't dollars: treat pay as unlisted
        salary_min = salary_max = None
    elif salary_min is None and salary_max is None:
        salary_min, salary_max = parse_salary(desc)
    return {
        "id": hashlib.sha1(f"{company}|{title}".lower().encode()).hexdigest()[:12],
        "source": source, "title": strip_html(title), "company": strip_html(company),
        "location": strip_html(location) or "?", "url": url, "description": desc,
        "salary_min": salary_min, "salary_max": salary_max,
        "posted": str(posted)[:10] if re.match(r"\d{4}-\d\d-\d\d", str(posted)) else str(posted),  # Workday: "Posted 3 Days Ago"
    }


# ---------- sources ----------

def src_himalayas():
    out = []
    for q in CFG["searches"]:
        for page in (1, 2):
            t = get(f"https://himalayas.app/jobs/api/search?q={urllib.parse.quote(q)}&page={page}")
            if not t:
                break
            for j in json.loads(t).get("jobs", []):
                loc = ", ".join(j.get("locationRestrictions") or []) or "Remote (anywhere)"
                posted = dt.datetime.fromtimestamp(int(j["pubDate"])).date() if j.get("pubDate") else ""
                out.append(job("Himalayas", j["title"], j.get("companyName", ""), "Remote: " + loc,
                               j.get("applicationLink") or j.get("guid"), j.get("description", ""),
                               to_int(j.get("minSalary")), to_int(j.get("maxSalary")), posted, j.get("currency")))
            time.sleep(1)
    return out


def src_remotive():
    out = []
    for q in CFG["searches"]:
        t = get(f"https://remotive.com/api/remote-jobs?search={urllib.parse.quote(q)}&limit=50")
        if not t:
            continue
        for j in json.loads(t).get("jobs", []):
            lo, hi = parse_salary(j.get("salary", ""))
            out.append(job("Remotive", j["title"], j["company_name"], "Remote: " + j.get("candidate_required_location", ""),
                           j["url"], j.get("description", ""), lo, hi, j.get("publication_date", "")))
        time.sleep(1)
    return out


def src_jobicy():
    out = []
    for tag in CFG["jobicy_tags"]:
        t = get(f"https://jobicy.com/api/v2/remote-jobs?count=50&tag={tag}")
        if not t:
            continue
        for j in json.loads(t).get("jobs", []):
            out.append(job("Jobicy", j["jobTitle"], j["companyName"], "Remote: " + j.get("jobGeo", ""), j["url"],
                           j.get("jobDescription", ""), to_int(j.get("salaryMin") or j.get("annualSalaryMin")),
                           to_int(j.get("salaryMax") or j.get("annualSalaryMax")), j.get("pubDate", ""),
                           j.get("salaryCurrency")))
        time.sleep(1)
    return out


def src_remoteok():
    t = get("https://remoteok.com/api")
    out = []
    for j in (json.loads(t)[1:] if t else []):  # item 0 is the legal notice
        out.append(job("RemoteOK", j.get("position", ""), j.get("company", ""), "Remote: " + (j.get("location") or "anywhere"),
                       j.get("url", ""), j.get("description", ""), to_int(j.get("salary_min")),
                       to_int(j.get("salary_max")), j.get("date", "")))
    return out


def src_hn():
    """Latest 'Ask HN: Who is hiring?' thread. Each top-level comment is one company's post."""
    t = get("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10")
    if not t:
        return []
    story = next((h for h in json.loads(t)["hits"] if "who is hiring" in h["title"].lower()), None)
    if not story:
        return []
    t = get(f"https://hn.algolia.com/api/v1/items/{story['objectID']}")
    out = []
    for c in (json.loads(t).get("children", []) if t else []):
        text = c.get("text") or ""
        first = strip_html(text.split("<p>")[0])
        parts = [p.strip() for p in first.split("|")]
        if len(parts) < 2:
            continue
        loc = next((p for p in parts if re.search(r"remote|onsite|hybrid|, [A-Z]{2}\b", p, re.I)), "?")
        out.append(job("HN Who's Hiring", first, parts[0], loc,
                       f"https://news.ycombinator.com/item?id={c['id']}", text, posted=c.get("created_at", "")))
    return out


CARD = re.compile(
    r'browse-jobs-card__content--title" href="(?P<href>[^"]+)">\s*<div[^>]*>(?P<title>[^<]+)</div>.*?'
    r'content--organization">(?P<org>[^<]*)</div>.*?content--small">(?P<loc>[^<]*)</div>', re.S)


def src_teamwork():
    """TeamWork Online golf boards (PGA TOUR, PGA of America, USGA, Topgolf...). Page scrape, so it can break."""
    out = []
    for board in CFG["teamwork_boards"]:
        for page in range(1, 6):
            t = get(f"https://www.teamworkonline.com/{board}?page={page}")
            if not t:
                break
            found = CARD.findall(t)
            for href, title, org, loc in found:
                out.append(job("TeamWork (golf)", title, org, loc, "https://www.teamworkonline.com" + href))
            if not found:  # org pages use a different layout: fall back to job links
                for href in sorted(set(re.findall(r'href="(/golf-tennis-jobs/[^"/]+/[^"/]+/[^"/]+-\d+)"', t))):
                    org, slug = href.split("/")[3], href.rsplit("/", 1)[-1]
                    title = slug.rsplit("-", 1)[0].replace("-", " ").title()
                    out.append(job("TeamWork (golf)", title, org.replace("-", " ").title(), "?",
                                   "https://www.teamworkonline.com" + href))
            time.sleep(1)
            if f"page={page + 1}" not in t:
                break
    for j in out:  # list pages have no location or description: fetch them for tech-ish roles only
        title = " " + j["title"].lower() + " "
        if j["location"] != "?" or not has(title, CFG["dream_tech_words"]) or has(title, CFG["title_exclude"][:6]):
            continue
        t = get(j["url"])
        if not t:
            continue
        items = [strip_html(m) for m in re.findall(r'opportunity-preview__info-content-item[^>]*>(.*?)</div>', t, re.S)]
        j["location"] = next((i for i in items if re.search(r", [A-Z]{2}\b|remote", i, re.I)), "?")
        start = t.find("opportunity-preview__body")
        body = t[t.find(">", start) + 1:] if start >= 0 else ""
        body = body.split("Other PGA Tour Jobs")[0].split("Search TeamWork Online Jobs")[0]
        j["description"] = strip_html(body)[:20000]
        j["salary_min"], j["salary_max"] = parse_salary(j["description"])
        time.sleep(1)
    return out


def src_workday():
    """Company Workday boards (PGA TOUR, Titleist, TaylorMade...). Public JSON feed behind each company's careers site."""
    out = []
    for b in CFG["workday_boards"]:
        base = f"https://{b['host']}/wday/cxs/{b['tenant']}/{b['site']}"
        posts, total = [], 1
        while len(posts) < min(total, 1000):  # Workday serves 20 per page; Topgolf alone has 700+ venue jobs
            body = json.dumps({"appliedFacets": {}, "limit": 20, "offset": len(posts), "searchText": ""}).encode()
            t = get(base + "/jobs", data=body, headers={"Content-Type": "application/json", "Accept": "application/json"})
            try:
                page = json.loads(t) if t else {}
            except json.JSONDecodeError:  # Workday serves an HTML maintenance page on some weekends
                print(f"  ! {b['name']} Workday is unavailable (maintenance?)", file=sys.stderr)
                break
            if not page.get("jobPostings"):
                break
            total = page.get("total") or total  # Workday only reports the total on the first page
            posts += page["jobPostings"]
            time.sleep(0.5)
        for p in posts:
            if not p.get("title") or not p.get("externalPath"):
                continue  # Workday sometimes returns a stub posting with no title (crashed the 2026-10-28 run)
            desc = ""
            if has(" " + p["title"].lower() + " ", CFG["dream_tech_words"]):  # details only for tech-ish roles
                d = get(base + p["externalPath"], headers={"Accept": "application/json"})
                try:
                    desc = json.loads(d)["jobPostingInfo"].get("jobDescription", "") if d else ""
                except (json.JSONDecodeError, KeyError):
                    pass
            out.append(job(f"Workday ({b['name']})", p["title"], b["name"], p.get("locationsText", "?"),
                           f"https://{b['host']}/{b['site']}{p['externalPath']}", desc, posted=p.get("postedOn", "")))
        time.sleep(1)
    return out


def src_golfjobs():
    """golf-jobs.com: golf-industry aggregator (TrackMan, Toptracer, Topgolf, clubs...). Public RSS, searched by tech keyword."""
    out, seen = [], set()
    for q in CFG["golfjobs_queries"]:
        t = get("https://golf-jobs.com/rss/jobs?q=" + urllib.parse.quote(q))
        for item in re.findall(r"<item>(.*?)</item>", t or "", re.S):
            f = {k: html.unescape(re.sub(r"^<!\[CDATA\[|\]\]>$", "", v.strip()))
                 for k, v in re.findall(r"<(\w+)>(.*?)</\1>", item, re.S)}
            loc = f.get("location", "")
            company = f.get("company", "?")
            if f.get("guid") in seen or not re.search(r"united states|^remote$", loc.lower()):
                continue  # most of the board is UK and European clubs
            if has(company.lower(), CFG["golfjobs_skip_companies"]):
                continue  # already read straight from that company's own Workday board
            seen.add(f.get("guid"))
            title = re.sub(r"\s+at\s+" + re.escape(company) + r"\s*$", "", f.get("title", ""))  # "X at Company" -> "X"
            out.append(job("Golf-Jobs", title, company, loc, f.get("link", ""), f.get("description", ""),
                           posted=f.get("pubDate", "")))
        time.sleep(1)
    return out


SOURCES = {"himalayas": src_himalayas, "remotive": src_remotive, "jobicy": src_jobicy, "remoteok": src_remoteok,
           "hn": src_hn, "teamwork": src_teamwork, "workday": src_workday, "golfjobs": src_golfjobs}


# ---------- scoring ----------

def has(text, words):
    """Words found in text as whole words, so 'ping' doesn't match 'shipping' and 'git' doesn't match 'digital'."""
    return [w for w in words if re.search(r"(?<![a-z0-9])" + re.escape(w.strip()) + r"(?![a-z0-9])", text)]


def pay_floor(j):
    """Lowest pay that equals salary_min at home after cost of living. Returns (floor, place or None)."""
    base, col = CFG["salary_min"], CFG["col"]
    loc = j["location"]
    low = f" {loc.lower()} "
    if "remote" in low or "remote" in j["title"].lower():
        return base, None  # remote: you'd still live at home
    for place, (idx, words) in col["metros"].items():
        if has(low, words):
            break
    else:
        m = re.search(r",\s*([A-Z]{2})\b", loc)
        code = m.group(1) if m else next((c for n, c in col["state_names"].items() if has(low, [n])), None)
        if not code:
            return base, None  # "?", "United States", "3 Locations": nothing to adjust against
        place, idx = code, col["states"].get(code, col["default"])
    return max(base, int(round(base * idx / col["home"], -3))), place


def score(j):
    """Return (score, reasons) or (None, reason) when the job is filtered out."""
    title = " " + j["title"].lower() + " "
    desc = j["description"].lower()
    blob = f"{title} {j['company'].lower()} {j['location'].lower()} {desc}"
    dream = bool(has(f"{title} {j['company'].lower()} {j['source'].lower()}", CFG["dream_terms"])) or j["source"].startswith(
        tuple(CFG["dream_sources"]))

    # The dream industry gets a looser filter: an IT manager at the PGA TOUR is worth seeing, an intern isn't.
    excluded = has(title, CFG["title_exclude"])
    if excluded and not (dream and all(w.strip() in ("manager", "director", "head of", "principal") for w in excluded)):
        return None, "title excluded"
    if has(desc, CFG["description_exclude"]):
        return None, "clearance, non-US or sales quota"

    pts, why = 0, []
    for tier, words in sorted(CFG["title_tiers"].items(), key=lambda kv: -int(kv[0])):
        hit = has(title, words)
        if hit:
            pts += int(tier)
            why.append(f"title: {hit[0]}")
            break
    if not pts:
        if dream and has(title, CFG["dream_tech_words"]):
            pts += 20
            why.append(f"{CFG['dream_label']} tech role")
        else:
            return None, "title not a fit"

    skills = has(desc, CFG["skills"])
    pts += min(len(skills) * 2, 24)
    if skills:
        why.append("your skills: " + ", ".join(skills[:8]))
    gaps = has(desc, CFG["gaps"])
    if gaps:  # tools the posting wants that aren't on your resume
        pts -= min(len(gaps) * 4, 12)
        why.append("gaps: " + ", ".join(gaps[:5]))

    hi = j["salary_max"] or j["salary_min"]
    if hi and hi < 40_000:  # monthly or hourly figures from some APIs: treat as unknown
        hi = None
    floor, place = pay_floor(j)
    if hi:
        if hi < floor:
            return None, f"pay tops out at ${hi:,}" + (f" (needs ${floor:,} in {place})" if place else "")
        pts += 15
        why.append(f"pay up to ${hi:,}")
    if place and floor > CFG["salary_min"]:
        why.append(f"cost of living: needs ${floor:,} in {place} to match ${CFG['salary_min']:,} in {CFG['col']['home_name']}")

    loc = j["location"].lower()
    preferred = has(f" {loc} ", CFG["preferred_places"])
    if preferred:
        pts += 12
        why.append(CFG["preferred_label"])
    if "remote" in loc or "remote" in title:
        where = loc.split("remote", 1)[1].strip(" :-()") if "remote" in loc else ""
        us_ok = re.search(r"united states|\bus\b|\busa\b|\bu\.s\.|anywhere|worldwide|global|americas|north america|"
                          r", (?:" + "|".join(US_STATES) + r")\b"  # real state codes only: ", uk" isn't a state
                          + "".join("|" + re.escape(p) for p in CFG["preferred_places"]), where)
        if where and not us_ok:  # "Remote: South Africa", "Remote: Germany, Poland"...
            return None, "remote but not US"
        pts += 10
        why.append("remote")
    elif CFG["preferred_places"] and not preferred and j["source"] != "HN Who's Hiring" and loc != "?":
        pts -= 5  # on-site somewhere you didn't pick: still listed, but a move has to be worth it
        why.append(f"on-site outside {CFG['preferred_label']}")

    if dream:
        pts += 15
        why.insert(0, DREAM_TAG)
    if re.search(r"\bsenior\b|\bsr\.?\b|\blead\b|\bstaff\b", title):
        why.append("senior-level title")
    return pts, why


# ---------- output ----------

def load_seen():
    try:
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def entry(n, s, why, j):
    pay = f"${j['salary_min']:,}–${j['salary_max']:,}" if j["salary_min"] and j["salary_max"] else (
        f"${(j['salary_max'] or j['salary_min']):,}" if (j["salary_max"] or j["salary_min"]) else "pay not listed")
    return [f"### {n}. {j['title']} at {j['company']} (score {s})",
            f"- **Where:** {j['location']} | **Pay:** {pay} | **Posted:** {j['posted'] or '?'} | via {j['source']}",
            f"- **Why:** {'; '.join(why)}",
            f"- **Link:** {j['url']}",
            f"- **Tracker ID:** `{j['id']}`", ""]


def write_report(ranked, dream, counts, path):
    today = dt.date.today().isoformat()
    label = CFG["dream_label"]
    lines = [f"# Job finds: {today}", "",
             f"{len(ranked)} new matches" + (f", plus {len(dream)} {label}-industry tech roles" if label else "")
             + ". Nothing was applied to: you open each link and decide.", "",
             "Sources: " + ", ".join(f"{k} {v}" for k, v in counts.items()), ""]
    if label:
        lines += [f"## {label.capitalize()} industry", ""]
        if not dream:
            lines += [f"No new {label} tech roles today.", ""]
        for n, (s, why, j) in enumerate(dream, 1):
            lines += entry(n, s, why, j)
    lines += ["## Best matches", ""]
    for n, (s, why, j) in enumerate(ranked, 1):
        lines += entry(n, s, why, j)
    path.write_text("\n".join(lines), encoding="utf-8")


def update_tracker(ranked):
    new_file = not TRACKER.exists()
    with TRACKER.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["id", "found", "score", "title", "company", "location", "pay_max", "url", "status", "notes"])
        for s, _, j in ranked:
            w.writerow([j["id"], dt.date.today().isoformat(), s, j["title"], j["company"], j["location"],
                        j["salary_max"] or j["salary_min"] or "", j["url"], "new", ""])


def load_cache():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def is_repost(a, b):
    """Same title and a shared 150-character stretch of description means the same job posted twice."""
    da, db = (re.sub(r"\s+", " ", x["description"] or "") for x in (a, b))
    short, long_ = sorted((da, db), key=len)
    if len(short) < 500:
        return False
    return short[250:400] in long_


def make_kit(job_id):
    """Save one job's full posting to kits/<id>.md so Claude can draft a cover letter from it."""
    j = load_cache().get(job_id)
    if not j:
        sys.exit(f"No job with ID {job_id} in the cache. Use a Tracker ID from a report.")
    (HERE / "kits").mkdir(exist_ok=True)
    path = HERE / "kits" / f"{job_id}.md"
    path.write_text("\n".join([
        f"# {j['title']} at {j['company']}", "",
        f"- Where: {j['location']}", f"- Link: {j['url']}", f"- Source: {j['source']}", "",
        "## Your input (fill in before asking Claude to draft)", "",
        "- Why this job / company:", "- A story or project to lead with:", "- Anything to avoid mentioning:", "",
        "## Posting", "", j["description"] or "(No description in the feed. Paste it from the job page.)", ""]),
        encoding="utf-8")
    print(f"Kit: {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="include jobs seen in earlier runs")
    ap.add_argument("--only", help="comma-separated sources: " + ",".join(SOURCES))
    ap.add_argument("--limit", type=int, default=40, help="max jobs in the report (default 40)")
    ap.add_argument("--kit", metavar="ID", help="write an application kit for one Tracker ID, then stop")
    args = ap.parse_args()
    if sys.stdout is None:  # scheduled run via pyw.exe has no console, so log to a file instead
        sys.stdout = sys.stderr = (HERE / "daily.log").open("a", encoding="utf-8")
        print(f"\n--- {dt.datetime.now():%Y-%m-%d %H:%M} ---")
    if args.kit:
        return make_kit(args.kit)

    picked = args.only.split(",") if args.only else list(SOURCES)
    jobs, counts = [], {}
    for name in picked:
        print(f"Fetching {name}...")
        try:
            got = SOURCES[name]()
        except Exception as e:  # one broken feed shouldn't cost the whole morning's report
            print(f"  ! {name} failed: {type(e).__name__}: {e}", file=sys.stderr)
            got = []
        counts[name] = len(got)
        jobs += got

    seen = load_seen()
    by_title = {}  # reposts: same job under a reposter's name (GE HealthCare via Lifelancer)
    if not args.all:
        for old in load_cache().values():
            by_title.setdefault(norm_title(old["title"]), []).append(old)
    unique, ids = [], set()
    for j in jobs:
        if j["id"] in ids or (j["id"] in seen and not args.all):
            continue
        if any(is_repost(j, o) for o in by_title.get(norm_title(j["title"]), [])):
            continue
        ids.add(j["id"])
        by_title.setdefault(norm_title(j["title"]), []).append(j)
        unique.append(j)

    ranked, dream, dropped, done = [], [], {}, set()
    for j in unique:
        s, why = score(j)
        if s is not None and why and why[0] == DREAM_TAG:  # dream-industry tech roles always get listed
            dream.append((s, why, j))
            done.add(j["id"])
        elif s is None or s < CFG["min_score"]:
            key = why if s is None else "score too low"
            dropped[key] = dropped.get(key, 0) + 1
            done.add(j["id"])
        else:
            ranked.append((s, why, j))
    ranked.sort(key=lambda r: -r[0])
    ranked = ranked[: args.limit]  # matches past the limit stay unseen and show up next run
    done |= {j["id"] for _, _, j in ranked}

    (HERE / "reports").mkdir(exist_ok=True)
    report = HERE / "reports" / f"{dt.date.today().isoformat()}.md"
    if report.exists():  # second run the same day gets its own file instead of overwriting the first
        report = report.with_name(f"{dt.datetime.now():%Y-%m-%d-%H%M}.md")
    dream.sort(key=lambda r: -r[0])
    cache = load_cache()
    cache.update({j["id"]: j for _, _, j in dream + ranked})
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    write_report(ranked, dream, counts, report)
    update_tracker(dream + ranked)
    SEEN_FILE.write_text(json.dumps(sorted(seen | done)), encoding="utf-8")

    print(f"\nFetched {len(jobs)} ({len(unique)} new). Kept {len(ranked)} + {len(dream)} {CFG['dream_label'] or 'dream'}. "
          f"Report: {report}")
    print("Dropped:", ", ".join(f"{k}: {v}" for k, v in sorted(dropped.items(), key=lambda kv: -kv[1])[:6]))


if __name__ == "__main__":
    main()
