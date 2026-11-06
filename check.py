"""Judge whether an account is worth tracking, without touching the real database.

    python3 check.py                 # free: health report on every tracked account, from stored data
    .venv/bin/python check.py HANDLE # trial: fetch + extract 3 days into a throwaway db, then report
"""
import sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).parent


def report(con, handle):
    """Returns (stats line, list of problems)."""
    posts = con.execute("select count(*) from posts where handle=?", (handle,)).fetchone()[0]
    w = dict(con.execute("select weight, count(*) from calls where handle=? group by 1", (handle,)))
    items = sum(w.values())
    stats = f"@{handle}: {posts} posts -> {items} items ({w.get('high', 0)} high, {w.get('medium', 0)} medium, {w.get('low', 0)} low)"
    # ponytail: rule-of-thumb thresholds set from the 7 accounts seen so far; tune as more come in
    problems = []
    if posts < 10:
        problems.append("too few posts to judge yet")
    elif items / posts < 0.03:
        problems.append("almost nothing usable: under 3% of posts give a signal")
    if items >= 10 and w.get("high", 0) / items > 0.7:
        problems.append("noisy: over 70% rated high, so weight tells you nothing")
    if items >= 10 and w.get("low", 0) / items > 0.8:
        problems.append("mostly throwaway remarks: over 80% rated low")
    return stats, problems


def show(con, handle):
    stats, problems = report(con, handle)
    print(stats)
    print("  OK" if not problems else "\n".join(f"  ! {p}" for p in problems))
    for row in con.execute("""select weight, asset, direction, summary from calls where handle=?
            order by created_at desc limit 5""", (handle,)):
        print("  ", *row[:3], "-", row[3])


if __name__ == "__main__":
    if len(sys.argv) > 1:
        import anthropic, extract, fetch
        handle = sys.argv[1].lstrip("@")
        con = fetch.db(":memory:")
        extract.db(con)
        fetch.fetch(con, handle, fetch.token(), 3)
        extract.process(con, anthropic.Anthropic(api_key=extract.env("ANTHROPIC_API_KEY")))
        show(con, handle)
    else:
        con = sqlite3.connect(ROOT / "xconviction.db")
        for h in (ROOT / "accounts.txt").read_text().split():
            show(con, h.lstrip("@"))
