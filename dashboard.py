"""Local job dashboard: your tracker as a page, with a Cover letter button on each job.

Runs only on this computer (127.0.0.1) unless DASHBOARD_KEY is set; then your phone can reach it too, with
the key (see guard()). Nothing here applies to jobs: you read the letter, check the flags, download the
Word file and apply yourself.

    .venv\\Scripts\\python.exe dashboard.py      then open http://127.0.0.1:5000
"""
import csv
import hmac
import ipaddress
import logging
import os
import socket
import shutil
import threading
import html
import re
import datetime as dt
from pathlib import Path

from flask import Flask, abort, redirect, request, send_file, url_for

import job_finder as jf
import letters

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


def tailscale_ips():
    """This PC's Tailscale addresses (100.64.0.0/10), so the phone works from anywhere, not just home wifi."""
    cgnat = ipaddress.ip_network("100.64.0.0/10")
    return [ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if ipaddress.ip_address(ip) in cgnat]


LAN_IPS = [*tailscale_ips(), lan_ip()] if KEY else []  # first one is the address shown on the page
ALLOWED_HOSTS = {f"{h}:{PORT}" for h in ["127.0.0.1", "localhost", *LAN_IPS]}
RESUMES = [(jf.HERE / p).resolve() for p in jf.CFG.get("resume_files", [])]
STATUSES = ["new", "applied", "skip", "rejected", "interview"]
SORTS = {  # column -> sort key; jobs with no score or pay sort as 0
    "score": lambda r: int(r["score"]) if r["score"].isdigit() else 0,
    "pay": lambda r: int(r["pay_max"]) if r["pay_max"].isdigit() else 0,
    "found": lambda r: r["found"],
    "company": lambda r: r["company"].lower(),
    "status": lambda r: r["status"],
}

CSS = """
:root{--bg:#f6f5f1;--card:#fff;--ink:#1d1d1b;--muted:#6b6a64;--line:#e3e1d9;--accent:#1f6b45;--warn:#a5481d;--flag:#fff4e5}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--card:#1f1f1c;--ink:#ecebe6;--muted:#9c9a92;--line:#33322e;--accent:#5cc28c;--warn:#f0956a;--flag:#3a2a17}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px}a{color:var(--accent)}h1{font-size:22px;margin:0 0 4px}
.muted{color:var(--muted)}table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line)}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:13px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;margin:16px 0}
textarea{width:100%;font:inherit;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
button,.btn{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:8px 14px;font:inherit;cursor:pointer;text-decoration:none;display:inline-block}
button.plain{background:transparent;color:var(--accent);border:1px solid var(--line)}
pre{white-space:pre-wrap;font:inherit;margin:0}.flag{background:var(--flag);border-left:3px solid var(--warn);padding:8px 10px;margin:6px 0}
.filters a{margin-right:12px}.score{font-weight:600}
"""


def page(title, body, refresh=0):
    meta = f"<meta http-equiv=refresh content={refresh}>" if refresh else ""
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>{meta}<meta name=viewport "
            f"content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body><main>{body}</main></body></html>")


def read_tracker():
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
    # Click a column header to sort by it; click it again to flip the order. Default: newest first.
    sort = request.args.get("sort", "found") if request.args.get("sort") in SORTS else "found"
    desc = request.args.get("dir", "desc") != "asc"
    rows.sort(key=lambda r: (SORTS[sort](r), r["found"]), reverse=desc)  # ties: newest first
    if sort in ("score", "pay"):  # jobs with no score or pay listed stay at the bottom either way
        rows = [r for r in rows if SORTS[sort](r)] + [r for r in rows if not SORTS[sort](r)]
    links = " ".join(f"<a href='?status={s}&days={days}&sort={sort}&dir={'desc' if desc else 'asc'}'>{s}</a>"
                     for s in STATUSES + ["all"])

    def th(label, key):
        flip = "asc" if (key == sort and desc) else "desc"
        arrow = (" ▼" if desc else " ▲") if key == sort else ""
        return f"<th><a href='?status={show}&days={days}&sort={key}&dir={flip}'>{label}{arrow}</a></th>"
    trs = "".join(
        f"<tr><td class=score>{html.escape(r['score'] or '-')}</td>"
        f"<td><a href='{url_for('job', job_id=r['id'])}'>{html.escape(r['title'][:90])}</a><br>"
        f"<span class=muted>{html.escape(r['company'])} · {html.escape(r['location'][:40])}</span></td>"
        f"<td>{('$' + format(int(r['pay_max']), ',')) if r['pay_max'].isdigit() else '-'}</td>"
        f"<td>{html.escape(r['found'])}</td><td>{html.escape(r['status'])}</td></tr>" for r in rows)
    return page("Job dashboard", f"<h1>Job dashboard</h1><p class=muted>{len(rows)} jobs, status "
                f"<b>{html.escape(show)}</b>, last {days} days</p>"
                + "".join(f"<p class=muted>On your phone: http://{ip}:{PORT}</p>" for ip in LAN_IPS[:1])
                + f"<p class=filters>{links}</p>"
                f"<form method=post action='/quit' style='float:right;margin-top:-40px'>"
                f"<button class=plain>Stop dashboard</button></form>"
                f"<table><tr>{th('Score', 'score')}{th('Job', 'company')}{th('Pay up to', 'pay')}"
                f"{th('Found', 'found')}{th('Status', 'status')}</tr>"
                f"{trs or '<tr><td colspan=5>Nothing here.</td></tr>'}</table>")


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
        f"<button class=plain name=status value={s}>{s}</button> " for s in STATUSES if s != row["status"])
    fields = "".join(
        f"<p><label><b>{html.escape(k)}</b>"
        f"{' <span class=muted>(leave blank and Gemini drafts it)</span>' if i == 0 else ''}<br><textarea name='{i}' rows=2>{html.escape(notes.get(k, ''))}"
        f"</textarea></label></p>" for i, k in enumerate(letters.INPUT_KEYS))
    out = ""
    if letter:
        flag_html = "".join(f"<div class=flag>{html.escape(l[2:])}</div>" for l in flags.splitlines()
                            if l.startswith("- ") and l != "- none") or "<p>No flags: every claim matched your facts file.</p>"
        dl = (f"<a class=btn href='{url_for('download', job_id=job_id)}'>Download Word file</a> "
              f"<button class=plain type=button onclick='copyLetter(this)'>Copy letter text</button>")
        out = (f"<div class=card><h2>Cover letter <span class=muted>({html.escape(when)})</span></h2>"
               f"<h3>Fact-check flags</h3>{flag_html}<p>{dl}</p><pre id=letter>{html.escape(letter)}</pre></div>"
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
        write_card = (f"<div class=card><h2>Writing your cover letter... {secs // 60}:{secs % 60:02d}</h2>"
                      f"<progress style='width:100%'></progress><ul class=muted>{lines}</ul>"
                      f"<p class=muted>Usually 1 to 3 minutes. This page updates by itself; you can leave and "
                      f"come back.</p></div>")
    else:
        write_card = (f"<div class=card><form method=post action='{url_for('generate', job_id=job_id)}'>"
                      f"{fields}<button>Write cover letter</button> <span class=muted>About 1 to 3 minutes. "
                      f"Your answers are saved to the kit.</span></form></div>")
    return page(row["title"], f"<p><a href='/'>← all jobs</a></p><h1>{html.escape(row['title'])}</h1>"
                f"<p class=muted>{html.escape(row['company'])} · {html.escape(row['location'])} · score "
                f"{html.escape(row['score'] or '-')} · status <b>{html.escape(row['status'])}</b></p>"
                f"<p><a href='{html.escape(row['url'])}' target=_blank rel=noopener>Open the posting</a></p>"
                + "".join(f"<a class='btn' style='margin:0 8px 8px 0' href='{url_for('resume', i=i)}'>Resume ({p.suffix[1:].upper()})</a>"
                          for i, p in enumerate(RESUMES))
                +
                f"<form method=post action='{url_for('status', job_id=job_id)}'>Mark as: {status_btns}</form>"
                f"{err_html}{write_card}{'' if run else out}"
                f"<details class=card><summary>Posting</summary><pre>{html.escape(posting)}</pre></details>",
                refresh=3 if run else 0)


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
    run = RUNS[job_id] = {"start": dt.datetime.now(), "lines": [], "done": False, "error": None}

    def work():
        try:
            letters.write_letter(job_id, say=run["lines"].append)
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
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return page("Stopped", "<h1>Dashboard stopped</h1><p class=muted>You can close this tab. "
                "Use the desktop shortcut to start it again.</p>")


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False)
