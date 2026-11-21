"""Local job dashboard: your tracker as a page, with a Cover letter button on each job.

Runs only on this computer (127.0.0.1) unless DASHBOARD_KEY is set; then your phone can reach it too, with
the key (see guard()). Nothing here applies to jobs: you read the letter, check the flags, download the
Word file and apply yourself.

    .venv\\Scripts\\python.exe dashboard.py      then open http://127.0.0.1:5000
"""
import csv
import hmac
import ipaddress
import json
import logging
import os
import socket
import subprocess
import shutil
import threading
import html
import re
import datetime as dt
from pathlib import Path

from flask import Flask, abort, redirect, request, send_file, url_for

import job_finder as jf
import letters
import triage

app = Flask(__name__)


class HideQuery(logging.Filter):
    """Keep the phone's ?key=... (or any query string) out of dashboard.log."""
    def filter(self, rec):
        rec.args = tuple(re.sub(r"\?\S*", "?***", a) if isinstance(a, str) else a for a in (rec.args or ()))
        return True


logging.getLogger("werkzeug").addFilter(HideQuery())
PORT = 5000
# Set the DASHBOARD_KEY user environment variable to open the dashboard to your home network (phone);
# without it, it stays on this PC only.
KEY = os.environ.get("DASHBOARD_KEY", "")
HOST = "0.0.0.0" if KEY else "127.0.0.1"


def lan_ip():
    """This PC's address on the home network: the one its internet route uses (a UDP 'connect' sends nothing)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]


def tailscale(cmd):
    """Connect ("up") or disconnect ("down") Tailscale along with the dashboard, so the phone link only works
    while the dashboard runs. Only with DASHBOARD_KEY set and "tailscale_with_dashboard": true."""
    if not (KEY and jf.CFG.get("tailscale_with_dashboard")):
        return
    exe = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    try:
        r = subprocess.run([exe, cmd], capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(f"tailscale {cmd}: exit {r.returncode} {r.stderr.strip()[:200]}")
    except (OSError, subprocess.TimeoutExpired) as e:  # no Tailscale: the dashboard still runs on this PC
        print(f"tailscale {cmd} failed: {e}")


def tailscale_ips():
    """This PC's Tailscale addresses (100.64.0.0/10), so the phone works from anywhere, not just home wifi."""
    cgnat = ipaddress.ip_network("100.64.0.0/10")
    return [ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if ipaddress.ip_address(ip) in cgnat]


LAN_IPS = [*tailscale_ips(), lan_ip()] if KEY else []


def phone_ips():
    """Address shown on the page: Tailscale if it is up right now (even if it came up after start), else LAN."""
    return ([*tailscale_ips(), *LAN_IPS] if KEY else [])[:1]
ALLOWED_HOSTS = {f"{h}:{PORT}" for h in ["127.0.0.1", "localhost", *LAN_IPS]}
RESUMES = [(jf.HERE / p).resolve() for p in jf.CFG.get("resume_files", [])]
STATUSES = ["new", "applied", "skip", "rejected", "interview"]
SORTS = {  # column -> sort key; jobs with no score or pay sort as 0
    "score": lambda r: int(r["score"]) if r["score"].isdigit() else 0,
    "pay": lambda r: int(r["pay_max"]) if r["pay_max"].isdigit() else 0,
    "fit": lambda r: r["fit"] + 1 if "fit" in r else 0,  # +1 so a real 0 still sorts above "not triaged"
    "found": lambda r: r["found"],
    "company": lambda r: r["company"].lower(),
    "status": lambda r: r["status"],
}

# Look from the design-taste-frontend skill: dealt palette moss-sand, type Geist, radius soft (2026-11-18).
CSS = """
:root{--p-bg:#ddd6c6;--p-surface:#d2cab8;--p-ink:#2a2a26;--p-muted:#55534b;--p-accent:#4f6238;--p-accent-ink:#fff;
--p-action:#4f6238;--p-action-ink:#fff;--p-inv-bg:#2a2a26;--p-inv-surface:#353430;--p-inv-ink:#ddd6c6;--p-inv-muted:#a39e92;
--p-inv-action:#ddd6c6;--p-inv-action-ink:#2a2a26;
--font:"Geist",system-ui,"Segoe UI",sans-serif;--mono:"Geist Mono",ui-monospace,Consolas,monospace;
--ground:var(--p-surface);--card:var(--p-bg);--ink:var(--p-ink);--muted:var(--p-muted);--action:var(--p-action);
--action-ink:var(--p-action-ink);--warn:#8a3b12;--step:var(--p-ink);
--line:color-mix(in srgb,var(--ink) 14%,transparent);--edge:color-mix(in srgb,var(--ink) 30%,transparent);
--r-lg:20px;--r-md:12px;--r-sm:8px;--m-in:140ms;--m-out:200ms;--m-press:90ms;--m-ease:cubic-bezier(.2,.8,.2,1);
--gut:24px;--top:64px}
@media (prefers-color-scheme:dark){:root{--ground:var(--p-inv-bg);--card:var(--p-inv-surface);--ink:var(--p-inv-ink);
--muted:var(--p-inv-muted);--action:var(--p-inv-action);--action-ink:var(--p-inv-action-ink);--warn:#f0956a;--step:var(--p-inv-bg)}}
@media (max-width:639px){:root{--gut:16px;--top:56px}}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--ground);color:var(--ink);font:400 16px/1.6 var(--font);-webkit-font-smoothing:antialiased}
.wrap{max-width:1040px;margin:0 auto;padding:0 var(--gut)}
.top{position:sticky;top:0;z-index:10;background:var(--ground);border-bottom:1px solid var(--line)}
.top .wrap{height:var(--top);display:flex;align-items:center;justify-content:space-between;gap:12px}
.brand{font-weight:500;font-size:20px;letter-spacing:-.02em;color:var(--ink);text-decoration:none}
main{padding:32px 0 64px}
h1{font-weight:500;font-size:32px;line-height:1.15;letter-spacing:-.025em;margin:0 0 8px}
h2{font-weight:500;font-size:20px;line-height:1.3;letter-spacing:-.01em;margin:0 0 16px}
@media (max-width:639px){h1{font-size:26px}main{padding:24px 0 48px}}
p{margin:0 0 16px}.muted{color:var(--muted)}.small{font-size:14px}.label{font-size:13px;font-weight:500;color:var(--muted)}
.mono{font-family:var(--mono)}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r-lg);padding:24px;margin:0 0 16px}
@media (max-width:639px){.card{padding:16px;border-radius:var(--r-md)}}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 16px}
.scroll{flex-wrap:nowrap;overflow-x:auto;padding:2px 2px 4px;scrollbar-width:none}.scroll::-webkit-scrollbar{display:none}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:44px;padding:0 24px;
border-radius:var(--r-sm);border:1px solid transparent;font:500 16px/1 var(--font);text-decoration:none;cursor:pointer;
white-space:nowrap;-webkit-tap-highlight-color:transparent;flex:none;
transition:transform var(--m-out) var(--m-ease),background-color var(--m-out) var(--m-ease),border-color var(--m-out) var(--m-ease)}
.btn--primary{background:var(--action);color:var(--action-ink)}
.btn--secondary{background:transparent;color:var(--ink);border-color:var(--edge)}
.btn--sm{min-height:40px;padding:0 16px;font-size:15px}
.btn[aria-current]{background:var(--ink);color:var(--card);border-color:var(--ink)}
@media (hover:hover) and (pointer:fine){
.btn:hover{transform:translateY(-1px);transition-duration:var(--m-in)}
.btn--primary:hover{background:color-mix(in srgb,var(--action) 86%,var(--step))}
.btn--secondary:not([aria-current]):hover{background:color-mix(in srgb,var(--ink) 6%,transparent);border-color:color-mix(in srgb,var(--ink) 55%,transparent)}
a.job:hover{transform:translateY(-2px);border-color:var(--edge);box-shadow:0 8px 24px color-mix(in srgb,var(--ink) 10%,transparent);transition-duration:180ms}}
.btn:active{transform:translateY(1px) scale(.985);transition-duration:var(--m-press)}
:focus-visible{outline:2px solid var(--action);outline-offset:2px}
@media (max-width:639px){.stack{flex-direction:column;align-items:stretch}.stack>.btn,.stack>form>.btn,.stack>button{width:100%}}
.jobs{display:grid;gap:12px}
a.job{display:grid;grid-template-columns:56px 1fr;gap:16px;align-items:start;padding:20px 24px;background:var(--card);
border:1px solid var(--line);border-radius:var(--r-lg);color:var(--ink);text-decoration:none;
transition:transform 180ms var(--m-ease),border-color var(--m-out) var(--m-ease),box-shadow var(--m-out) var(--m-ease)}
@media (max-width:639px){a.job{grid-template-columns:44px 1fr;gap:12px;padding:16px;border-radius:var(--r-md)}}
.score{font:500 18px/44px var(--mono);text-align:center;border-radius:var(--r-sm);background:var(--ground);height:44px}
.score.hi{background:var(--ink);color:var(--card)}
.job h3{font-weight:500;font-size:17px;line-height:1.35;margin:0 0 4px}
.job .meta{font-size:14px;color:var(--muted);margin:0}
.tag{display:inline-block;font-size:13px;font-weight:500;line-height:1.6;padding:0 10px;border-radius:999px;border:1px solid var(--edge);color:var(--ink)}
label.field{display:block;margin:0 0 16px}label.field>span{display:block;font-size:13px;font-weight:500;color:var(--muted);margin:0 0 6px}
textarea,select,input{width:100%;font:400 16px/1.5 var(--font);color:var(--ink);background:var(--ground);
border:1px solid var(--edge);border-radius:var(--r-sm);padding:10px 12px;min-height:48px;
transition:border-color var(--m-in) var(--m-ease),box-shadow var(--m-in) var(--m-ease)}
textarea{min-height:76px;resize:vertical}
textarea:focus,select:focus,input:focus{outline:none;border-color:var(--action);box-shadow:0 0 0 3px color-mix(in srgb,var(--action) 25%,transparent)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.rule{display:grid;grid-template-columns:1.3fr 1fr 1fr;gap:12px;align-items:end;padding:16px 0 0;border-top:1px solid var(--line)}
.rule label.field{margin:0 0 16px}
@media (max-width:639px){.grid2{grid-template-columns:1fr}.rule{grid-template-columns:1fr 1fr;column-gap:12px;row-gap:0}
.rule>label:first-child{grid-column:1/-1}.fill>.btn{flex:1 1 auto}}
.flag{background:color-mix(in srgb,var(--warn) 10%,var(--card));border:1px solid color-mix(in srgb,var(--warn) 40%,transparent);
border-left-width:4px;border-radius:var(--r-sm);padding:12px 16px;margin:0 0 12px}
.ok{border-left:4px solid var(--action);padding:12px 16px;border-radius:var(--r-sm);background:color-mix(in srgb,var(--action) 8%,var(--card));margin:0 0 16px}
pre{white-space:pre-wrap;font:400 16px/1.7 var(--font);margin:0}
details summary{cursor:pointer;font-weight:500;min-height:44px;display:flex;align-items:center}
details[open] summary{margin-bottom:12px}
progress{width:100%;height:8px;accent-color:var(--action);margin:0 0 16px}
ul.steps{margin:0 0 16px;padding-left:20px;color:var(--muted);font-size:15px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""


def btn(label, href, kind="secondary", extra=""):
    """A link that looks and acts like a button (the dashboard has no plain text links)."""
    return f"<a class='btn btn--{kind}' href='{html.escape(href)}'{extra}>{label}</a>"


def page(title, body, refresh=0, actions=""):
    meta = f"<meta http-equiv=refresh content={refresh}>" if refresh else ""
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>{meta}<meta name=viewport "
            f"content='width=device-width,initial-scale=1,viewport-fit=cover'><title>{html.escape(title)}</title>"
            f"<meta name=theme-color content='#d2cab8' media='(prefers-color-scheme: light)'>"
            f"<meta name=theme-color content='#2a2a26' media='(prefers-color-scheme: dark)'>"
            f"<link rel=preconnect href='https://fonts.googleapis.com'><link rel=preconnect href='https://fonts.gstatic.com' crossorigin>"
            f"<link rel=stylesheet href='https://fonts.googleapis.com/css2?family=Geist:wght@400..700&family=Geist+Mono:wght@400..500&display=swap'>"
            f"<style>{CSS}</style></head><body><header class=top><div class=wrap>"
            f"<a class=brand href='/'>Job finder</a><div class=row style='margin:0'>{actions}</div></div></header>"
            f"<main><div class=wrap>{body}</div></main></body></html>")


def read_tracker():
    if not jf.TRACKER.exists():  # dashboard opened before the first finder run
        return []
    with jf.TRACKER.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_tracker(rows):
    if not rows:
        raise ValueError("refusing to write an empty tracker")
    # write a temp file and swap it in, so a crash mid-write can't empty the tracker
    tmp = jf.TRACKER.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(rows)
    shutil.copy2(jf.TRACKER, jf.TRACKER.with_suffix(".csv.bak"))
    os.replace(tmp, jf.TRACKER)


def fits():
    """{job id: {"fit", "reason", "model"}} from triage.json. A job scored under an older prompt too: newest wins."""
    return {k.split(":")[0]: v for k, v in triage.load().items()}


def find_row(job_id):
    for r in read_tracker():
        if r["id"] == job_id:
            return r
    abort(404)


@app.route("/")
def index():
    show = request.args.get("status", "new")
    days = int(request.args.get("days", 14))
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = [r for r in read_tracker() if (show == "all" or r["status"] == show) and r["found"] >= since]
    llm = fits()
    for r in rows:
        if r["id"] in llm:
            r["fit"] = llm[r["id"]]["fit"]
    # Click a column header to sort by it; click it again to flip the order. Default: newest first.
    sort = request.args.get("sort", "found") if request.args.get("sort") in SORTS else "found"
    desc = request.args.get("dir", "desc") != "asc"
    rows.sort(key=lambda r: (SORTS[sort](r), r["found"]), reverse=desc)  # ties: newest first
    if sort in ("score", "pay", "fit"):  # jobs with no score or pay listed stay at the bottom either way
        rows = [r for r in rows if SORTS[sort](r)] + [r for r in rows if not SORTS[sort](r)]
    q = lambda **kw: "?" + "&".join(f"{k}={v}" for k, v in {"status": show, "days": days, "sort": sort,
                                                                "dir": "desc" if desc else "asc", **kw}.items())
    status_btns = "".join(
        btn(s.capitalize(), q(status=s), extra=" aria-current=page" if s == show else "") for s in STATUSES + ["all"])
    sort_btns = "".join(  # tap the active one again to flip the order
        btn(label + ((" ↓" if desc else " ↑") if key == sort else ""),
            q(sort=key, dir=("asc" if desc else "desc") if key == sort else "desc"),
            extra=" aria-current=true" if key == sort else "")
        for key, label in (("found", "Newest"), ("score", "Score"), ("fit", "LLM fit"), ("pay", "Pay"),
                           ("company", "Company")))

    def card(r):
        pay = f"${int(r['pay_max']):,}" if r["pay_max"].isdigit() else "Pay not listed"
        hi = " hi" if r["score"].isdigit() and int(r["score"]) >= 80 else ""
        fit = f" · LLM fit {r['fit']}" if "fit" in r else ""
        return (f"<a class=job href='{url_for('job', job_id=r['id'])}'><div class='score{hi}'>{html.escape(r['score'] or '–')}</div>"
                f"<div><h3>{html.escape(r['title'][:110])}</h3>"
                f"<p class=meta>{html.escape(r['company'])} · {html.escape(r['location'][:40])}</p>"
                f"<p class=meta>{pay} · found {html.escape(r['found'])}"
                f"{fit}"
                f" · <span class=tag>{html.escape(r['status'])}</span></p>"
                f"</div></a>")
    phone = "".join(f"<p class='muted small'>On your phone: <span class=mono>http://{ip}:{PORT}</span></p>"
                    for ip in phone_ips())
    return page("Job finder", f"<h1>Jobs</h1><p class=muted>{len(rows)} {html.escape(show)} jobs from the last "
                f"{days} days</p>{phone}<div class='row scroll'>{status_btns}</div>"
                f"<div class='row scroll'><span class=label>Sort</span>{sort_btns}</div>"
                f"<div class=jobs>{''.join(card(r) for r in rows) or '<div class=card>Nothing here yet.</div>'}</div>",
                actions=btn("Settings", "/settings"))


def latest_letter(kit_text):
    """The newest Gemini '## Cover letter' section in a kit: (when, letter, flags text, Word file path).
    New sections go just above '## Posting', so the last one in the file is the newest."""
    secs = []
    for sec in re.split(r"^(?=## )", kit_text, flags=re.M):  # one '## ' section at a time
        m = re.match(r"## Cover letter \(([^)]*)\)\n\n(.*?)\n### Fact-check flags\n\n(.*?)\n\nWord file: (.*?)\n",
                     sec, re.S)
        if m:
            secs.append(m.groups())
    if not secs:
        return None, None, None, None
    when, letter, flags, path = secs[-1]
    return when, letter.strip(), flags.strip(), (Path(path) if path != "not made" else None)


@app.before_request
def guard():
    """Only known host names (stops DNS-rebinding pages), no form posts from other websites, and a key for
    any device that isn't this PC. Open http://<PC address>:5000/?key=<DASHBOARD_KEY> once on the phone."""
    if request.host not in ALLOWED_HOSTS and KEY:  # Tailscale may have connected after the dashboard started
        ALLOWED_HOSTS.update(f"{ip}:{PORT}" for ip in tailscale_ips())
    if request.host not in ALLOWED_HOSTS:
        abort(403)
    origin = request.headers.get("Origin")
    if request.method == "POST" and origin and origin != f"http://{request.host}":
        abort(403)
    if request.remote_addr in ("127.0.0.1", "::1"):
        return None
    if KEY and hmac.compare_digest(request.args.get("key", ""), KEY):
        resp = redirect(request.path)
        resp.set_cookie("key", KEY, max_age=365 * 86400, httponly=True, samesite="Strict")
        return resp
    if not (KEY and hmac.compare_digest(request.cookies.get("key", ""), KEY)):
        abort(403)


@app.route("/job/<job_id>")
def job(job_id):
    row = find_row(job_id)
    kit = letters.KITS / f"{job_id}.md"
    if not kit.exists():
        try:
            jf.make_kit(job_id)
        except SystemExit:
            pass
    text = kit.read_text(encoding="utf-8") if kit.exists() else ""
    notes = letters.read_kit(job_id)[2] if text else {k: "" for k in letters.INPUT_KEYS}
    posting = text.split("## Posting", 1)[1].strip() if "## Posting" in text else "(No kit: job isn't in the cache.)"
    when, letter, flags, _ = latest_letter(text)
    status_btns = "".join(
        f"<button class='btn btn--secondary btn--sm' name=status value={s}"
        f"{' aria-current=true disabled' if s == row['status'] else ''}>{s.capitalize()}</button>" for s in STATUSES)
    fields = "".join(
        f"<label class=field><span>{html.escape(k)}"
        f"{' (leave blank and it gets drafted for you)' if i == 0 else ''}</span><textarea name='{i}' rows=2>"
        f"{html.escape(notes.get(k, ''))}</textarea></label>" for i, k in enumerate(letters.INPUT_KEYS))
    out = ""
    if letter:
        flag_html = "".join(f"<div class=flag>{html.escape(l[2:])}</div>" for l in flags.splitlines()
                            if l.startswith("- ") and l != "- none") or "<div class=ok>No flags: every claim matched your facts file.</div>"
        dl = (btn("Download Word file", url_for("download", job_id=job_id))
              + "<button class='btn btn--secondary' type=button onclick='copyLetter(this)'>Copy letter text</button>")
        out = (f"<div class=card><h2>Your cover letter</h2><p class='label'>Written {html.escape(when)}</p>"
               f"<p class=label>Fact-check</p>{flag_html}<div class='row stack'>{dl}</div>"
               f"<pre id=letter>{html.escape(letter)}</pre></div>"
               # the phone reaches the dashboard over plain http, where navigator.clipboard is blocked,
               # so copy through a selected textarea instead
               "<script>function copyLetter(b){const t=document.createElement('textarea');"
               "t.value=document.getElementById('letter').textContent;document.body.appendChild(t);t.select();"
               "document.execCommand('copy');t.remove();b.textContent='Copied';}</script>")
    err = request.args.get("error")
    run = RUNS.get(job_id)
    if run and run["done"]:  # finished since the last refresh: show its error once, the letter shows below
        RUNS.pop(job_id)
        err = err or run["error"]
        run = None
    err_html = f"<div class=flag>{html.escape(err)}</div>" if err else ""
    if run:  # still writing: a status box instead of the form, and the page refreshes itself every 3 s
        secs = int((dt.datetime.now() - run["start"]).total_seconds())
        lines = "".join(f"<li>{html.escape(l)}</li>" for l in run["lines"]) or "<li>Starting...</li>"
        write_card = (f"<div class=card><h2>Writing your cover letter... <span class=mono>{secs // 60}:{secs % 60:02d}"
                      f"</span></h2><progress></progress><ul class=steps>{lines}</ul>"
                      f"<p class='muted small'>Claude: under a minute. Gemini: 1 to 3 minutes. This page updates by "
                      f"itself; you can leave and come back.</p></div>")
    else:
        picker = (f"<label class=field><span>Model</span>"
                  f"{select('model', letters.MODELS, jf.CFG.get('letter_model', 'gemini'), MODEL_LABELS)}</label>"
                  f"<label class=field><span>Effort</span>"
                  f"{select('effort', ['', *letters.EFFORTS], jf.CFG.get('letter_effort', ''), EFFORT_LABELS)}</label>")
        write_card = (f"<div class=card><h2>Cover letter</h2><form method=post action='{url_for('generate', job_id=job_id)}'>"
                      f"{fields}<div class=grid2>{picker}</div>"
                      f"<p class='muted small'>{html.escape(letters.usage_line())}</p>"
                      f"<div class='row stack'><button class='btn btn--primary'>Write cover letter</button>"
                      f"{btn('Letter settings', '/settings')}</div></form></div>")
    pay = f"${int(row['pay_max']):,}" if row["pay_max"].isdigit() else "pay not listed"
    t = fits().get(job_id)
    fit_html = (f"<div class=card><p class=label>LLM fit</p><p style='margin:0'><span class=mono>{t['fit']}/100</span> · "
                f"{html.escape(t['reason'])}</p></div>" if t else "")
    links = (f"<a class='btn btn--secondary' href='{html.escape(row['url'])}' target=_blank rel=noopener>Open posting ↗</a>"
             + "".join(btn(f"Resume ({p.suffix[1:].upper()})", url_for("resume", i=i)) for i, p in enumerate(RESUMES)))
    return page(row["title"], f"<h1>{html.escape(row['title'])}</h1>"
                f"<p class=muted>{html.escape(row['company'])} · {html.escape(row['location'])} · {pay} · score "
                f"<span class=mono>{html.escape(row['score'] or '–')}</span></p>"
                f"{fit_html}<div class='row fill'>{links}</div>"
                f"<form method=post action='{url_for('status', job_id=job_id)}' class='row scroll'>"
                f"<span class=label>Status</span>{status_btns}</form>"
                f"{err_html}{write_card}{'' if run else out}"
                f"<details class=card><summary>Job posting</summary><pre>{html.escape(posting)}</pre></details>",
                refresh=3 if run else 0, actions=btn("← All jobs", "/"))


RUNS = {}  # job_id -> {"start", "lines", "done", "error"}: letters being written in the background


@app.post("/job/<job_id>/generate")
def generate(job_id):
    """Start writing in a background thread and go straight back to the job page, which shows progress."""
    find_row(job_id)
    if not (letters.KITS / f"{job_id}.md").exists():
        return redirect(url_for("job", job_id=job_id, error="No kit for this job, so there's no posting to write from."))
    if job_id in RUNS and not RUNS[job_id]["done"]:
        return redirect(url_for("job", job_id=job_id))  # already writing (double tap)
    letters.save_notes(job_id, {k: request.form.get(str(i), "") for i, k in enumerate(letters.INPUT_KEYS)})
    model = request.form.get("model") if request.form.get("model") in letters.MODELS else None
    effort = request.form.get("effort") if request.form.get("effort") in letters.EFFORTS else ""
    run = RUNS[job_id] = {"start": dt.datetime.now(), "lines": [], "done": False, "error": None}

    def work():
        try:
            letters.write_letter(job_id, say=run["lines"].append, model=model, effort=effort)
        except (RuntimeError, SystemExit) as e:
            run["error"] = str(e)
        except Exception as e:  # anything unexpected still ends the run, so the page stops spinning
            run["error"] = f"{type(e).__name__}: {e}"
        run["done"] = True
    threading.Thread(target=work, daemon=True).start()
    return redirect(url_for("job", job_id=job_id))


@app.route("/job/<job_id>/download")
def download(job_id):
    kit = letters.KITS / f"{job_id}.md"
    path = latest_letter(kit.read_text(encoding="utf-8"))[3] if kit.exists() else None
    if not path or not path.exists():
        abort(404, "No Word file for this job yet. Write the letter first.")
    return send_file(path, as_attachment=True, download_name=path.name)


def select(name, options, chosen, labels):
    opts = "".join(f"<option value='{o}'{' selected' if o == chosen else ''}>{labels.get(o, o)}</option>"
                   for o in options)
    return f"<select name={name}>{opts}</select>"


MODEL_LABELS = {"auto": "Auto (paces your Claude usage)", "sonnet": "Claude Sonnet", "haiku": "Claude Haiku",
                "opus": "Claude Opus", "gemini": "Gemini (free)", "": "(remove this rule)"}
EFFORT_LABELS = {"": "Default effort", **{e: f"{e} effort" for e in letters.EFFORTS}}


@app.route("/settings", methods=["GET", "POST"])
def settings():
    """Edit the default model/effort and the Auto rules. Saved to my_config.json (yours, git-ignored)."""
    msg = ""
    if request.method == "POST":
        f = request.form
        rules = []
        for i in range(20):
            model = f.get(f"model{i}", "")
            if model not in letters.MODELS[1:]:  # blank = removed; "auto" can't be a rule
                continue
            try:
                floor = float(f.get(f"min{i}", ""))
            except ValueError:
                continue
            effort = f.get(f"effort{i}", "") if f.get(f"effort{i}", "") in letters.EFFORTS else ""
            rules.append({"model": model, "min_per_day": max(0.0, min(floor, 100.0)), "effort": effort})
        try:
            cutoff = max(1.0, min(float(f.get("cutoff", 95)), 100.0))
        except ValueError:
            cutoff = 95.0
        if not rules:
            msg = "Keep at least one rule. Nothing was saved."
        else:
            mine_path = jf.HERE / "my_config.json"
            mine = json.loads(mine_path.read_text(encoding="utf-8")) if mine_path.exists() else {}
            new = {"auto_rules": sorted(rules, key=lambda r: -r["min_per_day"]), "auto_five_hour_cutoff": cutoff,
                   "letter_model": f.get("default_model") if f.get("default_model") in letters.MODELS else "auto",
                   "letter_effort": f.get("default_effort") if f.get("default_effort") in letters.EFFORTS else ""}
            mine.update(new)
            tmp = mine_path.with_suffix(".json.tmp")  # swap in whole, so a crash can't leave half a config
            tmp.write_text(json.dumps(mine, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, mine_path)
            jf.CFG.update(new)
            msg = "Saved."
    rows = letters.auto_rules() + [{"model": "", "min_per_day": "", "effort": ""}] * 2  # 2 spare rows to add
    trs = "".join(
        f"<div class=rule><label class=field><span>Model</span>"
        f"{select(f'model{i}', ['', *letters.MODELS[1:]], r['model'], MODEL_LABELS)}</label>"
        f"<label class=field><span>Min. % a day left</span><input name=min{i} type=number min=0 "
        f"max=100 step=0.5 inputmode=decimal value='{r['min_per_day']}'></label>"
        f"<label class=field><span>Effort</span>"
        f"{select(f'effort{i}', ['', *letters.EFFORTS], r.get('effort', ''), EFFORT_LABELS)}</label></div>"
        for i, r in enumerate(rows))
    note = (f"<div class={'ok' if msg == 'Saved.' else 'flag'}>{html.escape(msg)}</div>" if msg else "")
    phone = "".join(f"<p class=muted>Phone address: <span class=mono>http://{ip}:{PORT}</span></p>" for ip in phone_ips())
    body = (f"<h1>Letter settings</h1>{note}"
            f"<p class=muted>{html.escape(letters.usage_line())}</p>"
            f"<form method=post><div class=card><h2>Default for new letters</h2><div class=grid2>"
            f"<label class=field><span>Model</span>"
            f"{select('default_model', letters.MODELS, jf.CFG.get('letter_model', 'gemini'), MODEL_LABELS)}</label>"
            f"<label class=field><span>Effort</span>"
            f"{select('default_effort', ['', *letters.EFFORTS], jf.CFG.get('letter_effort', ''), EFFORT_LABELS)}</label>"
            f"</div><p class='muted small' style='margin:0'>You can still change it on each job page.</p></div>"
            f"<div class=card><h2>Auto rules</h2>"
            f"<p>Auto works out how much of your weekly Claude limit is left per day until it resets. A fresh week "
            f"is about 14% a day; late in a week with plenty left it climbs, and it drops as you use Claude.</p>"
            f"<p>It uses the first rule from the top that is still covered. Choose \"(remove this rule)\" to delete "
            f"one, or fill in a blank row to add one.</p>{trs}"
            f"<div class=rule style='grid-template-columns:1fr'><label class=field><span>Switch to Gemini when the "
            f"5-hour window is at least (% full)</span><input name=cutoff type=number min=1 max=100 step=1 "
            f"inputmode=numeric value='{jf.CFG.get('auto_five_hour_cutoff', 95)}'></label></div></div>"
            f"<div class='row stack'><button class='btn btn--primary'>Save settings</button>{btn('Cancel', '/')}</div>"
            f"</form><div class=card style='margin-top:32px'><h2>Dashboard</h2>{phone}"
            f"<p class='muted small'>Stopping it also disconnects Tailscale, so your phone loses access until the "
            f"desktop shortcut starts it again.</p><form method=post action='/quit' class='row stack' style='margin:0'>"
            f"<button class='btn btn--secondary'>Stop dashboard</button></form></div>")
    return page("Letter settings", body, actions=btn("← All jobs", "/"))


@app.route("/resume/<int:i>")
def resume(i):
    """Your resume files (resume_files in my_config.json), for uploading from the phone."""
    if i >= len(RESUMES) or not RESUMES[i].exists():
        abort(404, "Resume file not found. Check resume_files in my_config.json.")
    return send_file(RESUMES[i], as_attachment=True, download_name=RESUMES[i].name)


@app.post("/job/<job_id>/status")
def status(job_id):
    new = request.form.get("status")
    if new not in STATUSES:
        abort(400)
    rows = read_tracker()
    for r in rows:
        if r["id"] == job_id:
            r["status"] = new
    write_tracker(rows)
    return redirect(url_for("job", job_id=job_id))


@app.post("/quit")
def quit_app():
    """Stop the dashboard (it runs in the background when started from the desktop shortcut)."""
    threading.Timer(0.5, lambda: (tailscale("down"), os._exit(0))).start()  # after this page reaches the phone
    return page("Stopped", "<h1>Dashboard stopped</h1><p class=muted>You can close this tab. "
                "Use the desktop shortcut to start it again.</p>")


if __name__ == "__main__":
    tailscale("up")
    app.run(host=HOST, port=PORT, debug=False)
