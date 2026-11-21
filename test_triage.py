"""The 6 AM run must survive a failing triage, and the report must show triage when it works.  Run: py test_triage.py"""
import job_finder as jf
import triage

JOB = {"id": "x1", "title": "Automation Engineer", "company": "Acme", "location": "Remote: US", "url": "https://a.b",
       "description": "", "salary_min": None, "salary_max": None, "posted": "", "source": "Himalayas"}


def test_failure_is_not_fatal():
    jf.CFG["triage"] = True
    for err in (RuntimeError("busy"), SystemExit("no key")):
        def boom(jobs, err=err):
            raise err
        triage.triage = boom
        j = dict(JOB)
        jf.add_triage([j])  # must not raise
        assert "triage" not in j


def test_report_shows_fit():
    triage.triage = lambda jobs: {"x1": {"fit": 82, "reason": "Strong HL7 match.", "model": "m"}}
    j = dict(JOB)
    jf.add_triage([j])
    assert "- **LLM fit:** 82/100. Strong HL7 match." in jf.entry(1, 70, ["remote"], j)
    assert not any("LLM fit" in l for l in jf.entry(1, 70, ["remote"], dict(JOB)))


if __name__ == "__main__":
    test_failure_is_not_fatal()
    test_report_shows_fit()
    print("ok")
