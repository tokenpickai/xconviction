"""Weekly account scouting: find accounts your tracked accounts keep mentioning, trial the most
promising, add the ones that produce real signal, and drop tracked accounts that have gone silent.

Runs inside the hourly pipeline but only does work on Mondays at 13:xx UTC (or with --now).
Writes scout-report.md when it did something, for the workflow to post as a GitHub issue.

    .venv/bin/python scout.py --now
"""
import json, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic

import check, extract, fetch

ROOT = Path(__file__).parent
MAX_TRIALS = 5   # ~$0.20-0.40 each in X reads + Claude
RETRY_DAYS = 30  # don't re-trial a rejected handle for a month
# ponytail: hand-set bars, from the first dozen accounts; tune as the scout's track record grows
MIN_POSTS, MIN_ITEMS, MIN_YIELD = 10, 3, 0.10


def ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def log(con, handle, action, detail):
    con.execute("insert into scout_log values(?,?,?,?)", (ts(datetime.now(timezone.utc)), handle, action, detail))


def candidates(con, tracked, skip, now, days=14):
    """Handles mentioned by tracked accounts, most distinct mentioners first. Needs 2+ mentioners or 3+ mentions."""
    by = {}
    for handle, raw in con.execute("select handle, raw from posts where created_at >= ?", (ts(now - timedelta(days=days)),)):
        for m in json.loads(raw).get("entities", {}).get("mentions", []):
            u = m["username"]
            if u.lower() not in tracked and u.lower() not in skip:
                by.setdefault(u, []).append(handle)
    ranked = sorted(by.items(), key=lambda kv: (-len(set(kv[1])), -len(kv[1])))
    return [(u, len(set(hs)), len(hs)) for u, hs in ranked if len(set(hs)) >= 2 or len(hs) >= 3]


def verdict(trial, handle):
    """(passed, reason) from a trial database."""
    posts = trial.execute("select count(*) from posts where handle=?", (handle,)).fetchone()[0]
    items = trial.execute("select count(*) from calls where handle=?", (handle,)).fetchone()[0]
    _, problems = check.report(trial, handle)
    if posts < MIN_POSTS or items < MIN_ITEMS or items / max(posts, 1) < MIN_YIELD:
        problems.append(f"{items} items from {posts} posts (need {MIN_ITEMS}+ items, {MIN_YIELD:.0%}+ of {MIN_POSTS}+ posts)")
    return not problems, "; ".join(dict.fromkeys(problems)) or f"{items} items from {posts} posts"


def silent(con, tracked, now, days=7):
    """Tracked accounts we've had for 7+ days whose posts produced nothing (no item, no theme) in that time."""
    cut = ts(now - timedelta(days=days))
    out = []
    for h in tracked:
        first = con.execute("select min(created_at) from posts where lower(handle)=?", (h,)).fetchone()[0]
        if not first or first > cut:
            continue  # too new to judge
        n = con.execute("""select count(*) from calls where lower(handle)=? and created_at >= ?""", (h, cut)).fetchone()[0]
        n += con.execute("""select count(*) from themes where lower(handle)=? and created_at >= ?""", (h, cut)).fetchone()[0]
        if n == 0:
            out.append(h)
    return out


def main(now, force=False):
    if not force and not (now.weekday() == 0 and now.hour == 13):
        return
    con = fetch.db()
    extract.db(con)
    con.execute("create table if not exists scout_log(at text, handle text, action text, detail text)")
    path = ROOT / "accounts.txt"
    handles = path.read_text().split()
    tracked = {h.lower() for h in handles}
    skip = {h.lower() for (h,) in con.execute("select handle from scout_log where at >= ?",
                                              (ts(now - timedelta(days=RETRY_DAYS)),))}
    lines, added, dropped = [], [], []

    client = anthropic.Anthropic(api_key=extract.env("ANTHROPIC_API_KEY"))
    for u, mentioners, mentions in candidates(con, tracked, skip, now)[:MAX_TRIALS]:
        trial = fetch.db(":memory:")
        extract.db(trial)
        fetch.fetch(trial, u, fetch.token(), 3)
        extract.process(trial, client)
        # count the trial's spend in the real usage log, so the footer stays honest
        con.executemany("insert into usage values(?,?,?,?)", trial.execute("select * from usage").fetchall())
        ok, why = verdict(trial, u)
        sample = trial.execute("""select weight, asset, direction, summary from calls where handle=?
            order by case weight when 'high' then 0 when 'medium' then 1 else 2 end limit 2""", (u,)).fetchall()
        log(con, u, "added" if ok else "rejected", why)
        lines.append(f"- {'✅ added' if ok else '❌ skipped'} **@{u}** (mentioned {mentions}× by {mentioners} accounts): {why}"
                     + "".join(f"\n  - {w} {a} {d}: {s}" for w, a, d, s in sample))
        if ok:
            added.append(u)

    for h in silent(con, tracked, now):
        name = next(x for x in handles if x.lower() == h)
        dropped.append(name)
        log(con, name, "dropped", "no calls or themes in 7 days")
        lines.append(f"- 🗑 dropped **@{name}**: no calls or themes in the last 7 days")

    con.commit()
    if added or dropped:
        path.write_text("".join(f"{h}\n" for h in handles if h not in dropped) + "".join(f"{h}\n" for h in added))
    if lines:
        (ROOT / "scout-report.md").write_text(
            f"Weekly scout, {now:%b %d, %Y}. Accounts tracked: {len(handles) - len(dropped) + len(added)}.\n\n" + "\n".join(lines) + "\n")
    print("\n".join(lines) or "scout: nothing to report")


if __name__ == "__main__":
    main(datetime.now(timezone.utc), force="--now" in sys.argv)
