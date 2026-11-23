"""write_tracker must never leave tracker.csv empty.  Run: .venv\\Scripts\\python.exe test_tracker.py"""
import tempfile
from pathlib import Path

import dashboard
import job_finder as jf

dashboard.letters.refresh_usage = lambda: None  # tests must never call Claude


def test_write_tracker_is_safe():
    with tempfile.TemporaryDirectory() as d:
        jf.TRACKER = Path(d) / "tracker.csv"
        jf.TRACKER.write_text("id,status\n1,new\n", encoding="utf-8-sig")
        before = jf.TRACKER.read_bytes()

        try:
            dashboard.write_tracker([])
            raise AssertionError("empty write should be refused")
        except ValueError:
            pass
        assert jf.TRACKER.read_bytes() == before

        dashboard.write_tracker([{"id": "1", "status": "applied"}])
        assert dashboard.read_tracker() == [{"id": "1", "status": "applied"}]
        assert (Path(d) / "tracker.csv.bak").read_bytes() == before
        assert not (Path(d) / "tracker.csv.tmp").exists()


def test_dashboard_before_first_run():
    with tempfile.TemporaryDirectory() as d:
        jf.TRACKER = Path(d) / "tracker.csv"  # doesn't exist yet
        c = dashboard.app.test_client()
        r = c.get("/", base_url="http://127.0.0.1:5000", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert r.status_code == 200 and "Nothing here yet" in r.get_data(as_text=True)


def test_dashboard_shows_fit():
    import json
    import triage
    with tempfile.TemporaryDirectory() as d:
        jf.TRACKER = Path(d) / "tracker.csv"
        jf.TRACKER.write_text("id,found,score,title,company,location,pay_max,url,status,notes\n"
                              "a1,2099-01-01,70,Fit Job,Acme,Remote,,https://a.b,new,\n"
                              "b2,2099-01-01,90,Unscored Job,Beta,Remote,,https://a.b,new,\n", encoding="utf-8-sig")
        jf.HERE = Path(d)  # the job page makes a kit; keep it out of the real kits/ folder
        dashboard.letters.KITS = Path(d) / "kits"
        triage.CACHE = Path(d) / "triage.json"
        triage.CACHE.write_text(json.dumps({"a1:old": {"fit": 10, "reason": "old", "model": "m"},
                                            "a1:new": {"fit": 0, "reason": "Needs <Kubernetes>.", "model": "m"}}))
        c = dashboard.app.test_client()
        kw = {"base_url": "http://127.0.0.1:5000", "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}
        page = c.get("/?sort=fit", **kw).get_data(as_text=True)
        assert "LLM fit 0" in page and page.index("Fit Job") < page.index("Unscored Job")  # a real 0 beats unscored
        job = c.get("/job/a1", **kw).get_data(as_text=True)
        assert "0/100" in job and "Needs &lt;Kubernetes&gt;." in job  # newest entry wins, reason is escaped


def test_board_links_get_careers_search():
    with tempfile.TemporaryDirectory() as d:
        jf.TRACKER = Path(d) / "tracker.csv"
        jf.HERE = Path(d)
        dashboard.letters.KITS = Path(d) / "kits"
        jf.TRACKER.write_text("id,found,score,title,company,location,pay_max,url,status,notes\n"
                              "j1,2099-01-01,70,Sr. IT Eng,Carrot,Remote,,https://jobicy.com/jobs/1,new,\n"
                              "g1,2099-01-01,70,Eng,Acme,Remote,,https://job-boards.greenhouse.io/acme/jobs/1,new,\n",
                              encoding="utf-8-sig")
        c = dashboard.app.test_client()
        kw = {"base_url": "http://127.0.0.1:5000", "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}
        assert "q=Carrot+careers+Sr.+IT+Eng" in c.get("/job/j1", **kw).get_data(as_text=True)
        assert "Find on their site" not in c.get("/job/g1", **kw).get_data(as_text=True)  # already the employer


def test_status_reason_goes_to_notes():
    with tempfile.TemporaryDirectory() as d:
        jf.TRACKER = Path(d) / "tracker.csv"
        jf.HERE = Path(d)
        dashboard.letters.KITS = Path(d) / "kits"
        jf.TRACKER.write_text("id,found,score,title,company,location,pay_max,url,status,notes\n"
                              "a1,2099-01-01,70,Eng,Acme,Remote,,https://a.b,new,old note\n"
                              "b2,2099-01-01,70,Eng,Beta,Remote,,https://a.b,new,\n", encoding="utf-8-sig")
        c = dashboard.app.test_client()
        kw = {"base_url": "http://127.0.0.1:5000", "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}
        c.post("/job/a1/status", data={"status": "skip", "reason": "pay\ntoo low"}, **kw)
        c.post("/job/b2/status", data={"status": "applied", "reason": ""}, **kw)  # reason is optional
        a, b = dashboard.read_tracker()
        assert a["status"] == "skip" and a["notes"].startswith("old note | ") and a["notes"].endswith(": skip: pay too low")
        assert b["status"] == "applied" and b["notes"] == ""
        assert "pay too low" in c.get("/job/a1", **kw).get_data(as_text=True)


def test_add_fact():
    import letters
    with tempfile.TemporaryDirectory() as d:
        letters.FACTS = Path(d) / "facts.md"
        letters.FACTS.write_text("# F\n\n## Skills\n- Python\n\n## Not yet (never claim these)\n- Kubernetes\n",
                                 encoding="utf-8")
        letters.add_fact("I built a job finder", True)
        letters.add_fact("Terraform", False)
        letters.add_fact("I run a golf site", True)
        assert letters.FACTS.read_text(encoding="utf-8") == (
            "# F\n\n## Skills\n- Python\n\n## Confirmed by Greg\n- I built a job finder\n- I run a golf site\n\n"
            "## Not yet (never claim these)\n- Kubernetes\n- Terraform\n")


def test_usage_line_after_reset():
    import letters
    import time
    with tempfile.TemporaryDirectory() as d:
        letters.USAGE = Path(d) / "u.json"
        past = time.time() - 60
        letters.USAGE.write_text(f'{{"seven_day": {{"utilization": 0.9, "resetsAt": {past}}}, "saved_at": 1}}')
        assert "90%" not in letters.usage_line() and "reset since" in letters.usage_line()


def test_closing_pc_tab_stops_only_when_idle():
    import time
    stops = []
    timers = []
    real_timer, real_exit, real_ts = dashboard.threading.Timer, dashboard.os._exit, dashboard.tailscale
    dashboard.threading.Timer = lambda secs, fn: type("T", (), {"start": lambda self: timers.append(fn)})()
    dashboard.os._exit = stops.append
    dashboard.tailscale = lambda cmd: None
    try:
        c = dashboard.app.test_client()
        local = {"base_url": "http://127.0.0.1:5000", "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}
        c.post("/closed", environ_base={"REMOTE_ADDR": "100.64.0.5"}, base_url="http://127.0.0.1:5000")
        assert not timers  # phone closing its tab never stops the PC dashboard
        c.post("/closed", **local)
        dashboard.LAST_HIT[0] = time.time() + 1  # a new page loaded during the grace period (reload, link)
        timers.pop()()
        assert not stops
        c.post("/closed", **local)
        dashboard.LAST_HIT[0] = 0
        timers.pop()()
        assert stops == [0]  # tab really closed: dashboard stops
    finally:
        dashboard.threading.Timer, dashboard.os._exit, dashboard.tailscale = real_timer, real_exit, real_ts


def test_docx_has_word_type():
    import mimetypes
    assert mimetypes.guess_type("a.docx")[0].endswith("wordprocessingml.document")  # else Android saves ".bin"


if __name__ == "__main__":
    test_docx_has_word_type()
    test_board_links_get_careers_search()
    test_write_tracker_is_safe()
    test_dashboard_before_first_run()
    test_dashboard_shows_fit()
    test_status_reason_goes_to_notes()
    test_add_fact()
    test_usage_line_after_reset()
    test_closing_pc_tab_stops_only_when_idle()
    print("ok")
