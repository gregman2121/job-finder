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


if __name__ == "__main__":
    test_write_tracker_is_safe()
    print("ok")
