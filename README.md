# Job finder

A daily job-search pipeline that pulls listings from 8 free public sources, normalizes them into one
schema, drops duplicates and reposts, scores each job against your titles, skills and pay floor, and
writes a ranked report.

**It never applies to anything.** You read the report, open the links you like, and submit yourself.
Auto-apply bots spray low-quality applications and get accounts flagged; this does the boring part
(finding and filtering) and leaves the judgment to you.

Pure Python standard library: no installs, no API keys, no accounts.

## Quick start

```bash
git clone <this repo>
cd job-finder
python setup.py        # 7 questions: titles, pay floor, home state, relocation, industry, skills, gaps
python job_finder.py   # about 3-4 minutes -> reports/<date>.md and tracker.csv
```

Run it again tomorrow and you only see jobs that are new since the last run.

```bash
python job_finder.py --only himalayas,remotive   # just some sources
python job_finder.py --all                       # include jobs you've already seen
python job_finder.py --kit <TrackerID>           # save one job's full posting to kits/ for cover-letter drafting
```

## What it does

1. **Fetches** from every source (one broken feed is logged and skipped, not fatal).
2. **Normalizes** each listing to the same fields: title, company, location, pay, description, posted date.
   Salaries come from structured fields when a source has them, otherwise from text like `$120k` or `$120,000`.
   Non-USD pay is treated as unlisted instead of being misread as dollars.
3. **De-duplicates**: the same job from two sources, jobs you've already seen (`seen.json`), and reposts of
   a job under a different company name (same title plus a matching stretch of description).
4. **Filters** out titles you'd never take, clearance-only or region-locked postings, remote jobs that aren't
   open to the US, and jobs whose posted pay tops out below your floor.
5. **Scores** what's left: title match tier, how many of your skills the posting names, tools you don't know
   (a penalty), pay listed, remote, your preferred places, and your dream industry.
6. **Reports** the top matches with the reasons for each score, and appends them to `tracker.csv`, where you
   mark each one `applied` or `skip`.

## Cost-of-living pay floor

Remote jobs need your `salary_min`. On-site and hybrid jobs need whatever matches it where you live now,
after local prices: `salary_min x place / home`, using the BEA's 2024 Regional Price Parities for all 50
states and DC plus a handful of expensive metros. A $100k floor in Texas becomes about $114k in California
and $119k in San Francisco. The index covers prices only, not income tax. BEA publishes new figures each
December; update `col` in `config.json` when it does.

## Sources

All free and public. LinkedIn and Indeed are left out on purpose: their terms ban scraping.

| Source | What | Notes |
|---|---|---|
| Himalayas | Remote jobs, often with salary | Search API, uses your titles |
| Remotive | Remote tech jobs | API terms ask for a link back; every report entry links the source |
| Jobicy | Remote jobs by tag | Same credit rule; tags in `jobicy_tags` |
| RemoteOK | Latest 100 remote jobs | Same credit rule |
| HN Who's Hiring | The monthly Hacker News thread | Startups, often with pay listed |
| TeamWork Online | Sports-industry job boards | Page scrape; used by the golf pack |
| Workday | Any company's Workday careers site | Public JSON feed; used by the golf pack |
| golf-jobs.com | Golf-industry aggregator | Public RSS; used by the golf pack |

## Industry packs

A pack is a JSON file in `packs/` that adds a dream industry: its keywords, the company boards to read,
and a label for its own report section. Jobs in your dream industry always get listed, with a bonus,
and get a looser title filter (an IT manager at your dream company is worth a look).

The included **golf pack** reads the PGA TOUR, PGA of America, USGA, Topgolf, Acushnet (Titleist/FootJoy),
TaylorMade and Topgolf Callaway Brands boards plus golf-jobs.com. To make your own, copy
`packs/golf.json`, swap in your industry's keywords and boards, and pick it in `setup.py`. Find a
company's Workday board on its careers page (the URL looks like `company.wd5.myworkdayjobs.com/SiteName`);
don't guess.

## Configuration

Settings are layered, last one wins:

1. `config.json`: shared defaults (title tiers, exclusions, the cost-of-living table)
2. `packs/<name>.json`: any packs you turned on
3. `my_config.json`: your answers from `setup.py`, which is git-ignored along with everything the finder
   writes about your search (`tracker.csv`, `seen.json`, `reports/`, `kits/`)

Edit `my_config.json` directly for anything setup doesn't ask, like `min_score` or extra `title_exclude` words.

## Run it every morning

Windows Task Scheduler: create a daily task that runs `pythonw.exe job_finder.py` in this folder. With no
console attached it logs to `daily.log`. On macOS/Linux, a cron line like
`0 7 * * * cd /path/to/job-finder && python3 job_finder.py` does the same.

## License

MIT
