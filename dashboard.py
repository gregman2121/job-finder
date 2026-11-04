"""Local job dashboard: your tracker as a page, with a Cover letter button on each job.

Runs only on this computer (127.0.0.1). Nothing here applies to jobs: you read the letter, check the
flags, download the Word file and apply yourself.

    .venv\\Scripts\\python.exe dashboard.py      then open http://127.0.0.1:5000
"""
import csv
import os
import threading
import html
import re
import datetime as dt
from pathlib import Path

from flask import Flask, abort, redirect, request, send_file, url_for

import job_finder as jf
import letters

app = Flask(__name__)
STATUSES = ["new", "applied", "skip", "rejected", "interview"]

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


def page(title, body):
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport "
            f"content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body><main>{body}</main></body></html>")


def read_tracker():
    with jf.TRACKER.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_tracker(rows):
    with jf.TRACKER.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(rows)


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
    rows.sort(key=lambda r: (r["found"], int(r["score"] or 0)), reverse=True)
    links = " ".join(f"<a href='?status={s}&days={days}'>{s}</a>" for s in STATUSES + ["all"])
    trs = "".join(
        f"<tr><td class=score>{html.escape(r['score'] or '-')}</td>"
        f"<td><a href='{url_for('job', job_id=r['id'])}'>{html.escape(r['title'][:90])}</a><br>"
        f"<span class=muted>{html.escape(r['company'])} · {html.escape(r['location'][:40])}</span></td>"
        f"<td>{('$' + format(int(r['pay_max']), ',')) if r['pay_max'].isdigit() else '-'}</td>"
        f"<td>{html.escape(r['found'])}</td><td>{html.escape(r['status'])}</td></tr>" for r in rows)
    return page("Job dashboard", f"<h1>Job dashboard</h1><p class=muted>{len(rows)} jobs, status "
                f"<b>{html.escape(show)}</b>, last {days} days</p><p class=filters>{links}</p>"
                f"<form method=post action='/quit' style='float:right;margin-top:-40px'>"
                f"<button class=plain>Stop dashboard</button></form>"
                f"<table><tr><th>Score</th><th>Job</th><th>Pay up to</th><th>Found</th><th>Status</th></tr>"
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
def same_site_only():
    """Block form posts from other websites (the dashboard only listens on 127.0.0.1, but a page you
    visit could still try to post to it)."""
    origin = request.headers.get("Origin")
    if request.method == "POST" and origin and not re.match(r"^http://(127\.0\.0\.1|localhost):5000$", origin):
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
        f"<p><label><b>{html.escape(k)}</b><br><textarea name='{i}' rows=2>{html.escape(notes.get(k, ''))}"
        f"</textarea></label></p>" for i, k in enumerate(letters.INPUT_KEYS))
    out = ""
    if letter:
        flag_html = "".join(f"<div class=flag>{html.escape(l[2:])}</div>" for l in flags.splitlines()
                            if l.startswith("- ") and l != "- none") or "<p>No flags: every claim matched your facts file.</p>"
        dl = f"<a class=btn href='{url_for('download', job_id=job_id)}'>Download Word file</a>"
        out = (f"<div class=card><h2>Cover letter <span class=muted>({html.escape(when)})</span></h2>"
               f"<h3>Fact-check flags</h3>{flag_html}<p>{dl}</p><pre>{html.escape(letter)}</pre></div>")
    err = request.args.get("error")
    err_html = f"<div class=flag>{html.escape(err)}</div>" if err else ""
    return page(row["title"], f"<p><a href='/'>← all jobs</a></p><h1>{html.escape(row['title'])}</h1>"
                f"<p class=muted>{html.escape(row['company'])} · {html.escape(row['location'])} · score "
                f"{html.escape(row['score'] or '-')} · status <b>{html.escape(row['status'])}</b></p>"
                f"<p><a href='{html.escape(row['url'])}' target=_blank rel=noopener>Open the posting</a></p>"
                f"<form method=post action='{url_for('status', job_id=job_id)}'>Mark as: {status_btns}</form>"
                f"{err_html}<div class=card><form method=post action='{url_for('generate', job_id=job_id)}'>"
                f"{fields}<button>Write cover letter</button> <span class=muted>About 1 to 3 minutes. "
                f"Your answers are saved to the kit.</span></form></div>{out}"
                f"<details class=card><summary>Posting</summary><pre>{html.escape(posting)}</pre></details>")


@app.post("/job/<job_id>/generate")
def generate(job_id):
    find_row(job_id)
    if not (letters.KITS / f"{job_id}.md").exists():
        return redirect(url_for("job", job_id=job_id, error="No kit for this job, so there's no posting to write from."))
    letters.save_notes(job_id, {k: request.form.get(str(i), "") for i, k in enumerate(letters.INPUT_KEYS)})
    try:
        res = letters.write_letter(job_id)
    except (RuntimeError, SystemExit) as e:
        return redirect(url_for("job", job_id=job_id, error=str(e)))
    return redirect(url_for("job", job_id=job_id))


@app.route("/job/<job_id>/download")
def download(job_id):
    kit = letters.KITS / f"{job_id}.md"
    path = latest_letter(kit.read_text(encoding="utf-8"))[3] if kit.exists() else None
    if not path or not path.exists():
        abort(404, "No Word file for this job yet. Write the letter first.")
    return send_file(path, as_attachment=True, download_name=path.name)


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
    app.run(host="127.0.0.1", port=5000, debug=False)
