"""First-run setup: a few questions -> my_config.json, which job_finder.py layers over config.json.

    py setup.py

Re-run it any time to change your answers. config.json stays untouched, so the defaults are always there.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
CFG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
PACKS = sorted(p.stem for p in (HERE / "packs").glob("*.json"))


def ask(prompt, default=""):
    ans = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    return ans or default


def words(text):
    """'DevOps Engineer, SRE' -> ['devops engineer', 'sre']"""
    return [w.strip().lower() for w in text.split(",") if w.strip()]


def money(text):
    """'115k', '$115,000', '115000' -> 115000"""
    t = text.lower().replace("$", "").replace(",", "").strip()
    return int(float(t[:-1]) * 1000) if t.endswith("k") else int(float(t))


def main():
    print("Job finder setup. Press Enter to keep the value in [brackets].\n")
    mine = {}

    titles = words(ask("1. What job titles are you looking for? (comma-separated)", "automation engineer, devops engineer"))
    mine["title_tiers"] = {"40": titles}  # your titles score highest; the default tiers below still apply
    mine["searches"] = titles  # what the search APIs are asked for

    mine["salary_min"] = money(ask("2. Minimum yearly pay (USD), where you live now", "100k"))

    state = ask("3. What state do you live in now? (2-letter code)", "UT").upper()
    col = CFG["col"]
    if state not in col["states"]:
        print(f"   No cost-of-living figure for {state}; using the US average.")
    mine["col"] = {"home": col["states"].get(state, col["default"]), "home_name": state}

    if ask("4. Willing to relocate? (y/n)", "n").lower().startswith("y"):
        places = words(ask("   Where would you most like to move? (cities or states, comma-separated, blank = anywhere)", ""))
        mine["preferred_places"] = places  # preferred places get a scoring bonus
        mine["preferred_label"] = places[0].title() if len(places) == 1 else ("your preferred places" if places else "")
    else:
        mine["preferred_places"] = [f", {state.lower()}"]  # home state is the only on-site bonus
        mine["preferred_label"] = state

    print(f"5. Dream industry? Built-in packs: {', '.join(PACKS) or 'none'}.")
    industry = ask("   Type a pack name, your own keywords (comma-separated), or leave blank", "")
    if industry.lower() in PACKS:
        mine["packs"] = [industry.lower()]  # the pack brings its own boards, keywords and label
    else:
        terms = words(industry)
        mine["packs"] = []
        mine["dream_terms"] = terms  # dream-industry jobs always get listed, with a bonus
        mine["dream_label"] = terms[0] if terms else ""  # blank hides the dream-industry section

    skills = words(ask("6. Your main skills and tools (comma-separated)", ", ".join(CFG["skills"])))
    mine["skills"] = skills  # each one found in a posting adds points
    mine["gaps"] = words(ask("7. Tools you DON'T know that should lower a job's score (comma-separated, blank = none)", ""))

    (HERE / "my_config.json").write_text(json.dumps(mine, indent=2), encoding="utf-8")
    print("\nSaved my_config.json. Run: py job_finder.py")


if __name__ == "__main__":
    main()
