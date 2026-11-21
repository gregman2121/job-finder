"""write_tracker must never leave tracker.csv empty.  Run: .venv\\Scripts\\python.exe test_tracker.py"""
import tempfile
from pathlib import Path

import dashboard
import job_finder as jf


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


if __name__ == "__main__":
    test_write_tracker_is_safe()
    test_dashboard_before_first_run()
    test_dashboard_shows_fit()
    print("ok")
