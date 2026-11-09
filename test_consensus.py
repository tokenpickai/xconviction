from datetime import datetime, timezone

import sqlite3

from build import collapse, consensus, emerging, local_time, picks, speaker, spend


def r(handle, kind, direction, weight="high", t="2026-11-06", flip=False):
    return dict(handle=handle, asset="BTC", kind=kind, direction=direction, weight=weight, created_at=t, flip=flip)


# 5 bearish signals from one busy account still count as one vote
rows = [r("a", "onchain", "bearish") for _ in range(5)] + [r("b", "call", "bullish"), r("c", "call", "bullish")]
assert consensus(rows, "2026-11-01") == [("BTC", 2, 1, False)]
# an account's latest call wins over its earlier one, and flags the flip
assert consensus([r("a", "call", "bullish"), r("a", "call", "bearish", flip=True)], "2026-11-01") == [("BTC", 0, 1, True)]
# anything older than the window drops out
assert consensus([r("a", "call", "bullish", t="2026-10-01")], "2026-11-01") == []
# a later low-weight remark can't change the vote
assert consensus([r("a", "call", "bullish", "medium"), r("a", "call", "bearish", "low")], "2026-11-01") == [("BTC", 1, 0, False)]
# but low-weight calls still count when they're all the account said
assert consensus([r("a", "call", "bearish", "low")], "2026-11-01") == [("BTC", 0, 1, False)]
assert local_time("2026-11-06T02:22:57.000Z") == "Nov 5, 7:22 PM"  # BC stays on UTC-7 year-round from 2026 (tzdata)
assert local_time("2026-07-01T12:05:00.000Z") == "Jul 1, 5:05 AM"
assert speaker({"name": "Ansem 🐂🀄️", "handle": "blknoiz06"}) == "Ansem"
assert speaker({"name": "🐧", "handle": "Pentosh1"}) == "Pentosh1"
# picks: a fresh call beats a stale one, a call the price already ran with drops out, low weight never shows
now = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
def c(asset, w, t, p0=None, p1=None, d="bullish", kind="call"):
    return dict(asset=asset, weight=w, created_at=t, p0=p0, p1=p1, direction=d, kind=kind)
got = picks([c("OLD", "high", "2026-11-07T14:00:00Z"), c("NEW", "high", "2026-11-08T11:00:00Z"),
             c("RAN", "high", "2026-11-08T11:00:00Z", 100, 115), c("LOW", "low", "2026-11-08T11:00:00Z")], now, {})
assert [r["asset"] for r, _, _ in got] == ["NEW", "OLD"], got
# agreement lifts a medium call above a lone high one of the same age
got = picks([c("A", "high", "2026-11-08T11:00:00Z"), c("B", "medium", "2026-11-08T11:00:00Z")], now, {"B": (3, 0)})
assert got[0][0]["asset"] == "B"
# a position snapshot ranks below a fresh move of the same weight
got = picks([dict(c("SNAP", "high", "2026-11-08T11:00:00Z"), snapshot=1), c("MOVE", "high", "2026-11-08T11:00:00Z")], now, {})
assert [r["asset"] for r, _, _ in got] == ["MOVE", "SNAP"]
# spend: user lookups count once per UTC day, everything else adds up
con = sqlite3.connect(":memory:")
con.execute("create table usage(at text, item text, units integer, usd real)")
con.executemany("insert into usage values(?,?,?,?)", [
    ("2026-11-08T01:00:00Z", "x_users", 7, 0.07), ("2026-11-08T02:00:00Z", "x_users", 7, 0.07),
    ("2026-11-08T02:00:00Z", "x_posts", 10, 0.05), ("2026-11-08T02:00:00Z", "claude_tokens", 1000, 0.03)])
assert "$0.15 last 24h" in spend(con, now), spend(con, now)
# emerging: a theme 2 accounts picked up ranks first; a lone mention only counts while brand-new; new = first seen in 48h
con.execute("create table themes(post_id text, handle text, created_at text, theme text)")
con.executemany("insert into themes values(?,?,?,?)", [
    ("1", "a", "2026-11-08T10:00:00Z", "Elysium"), ("2", "b", "2026-11-08T11:00:00Z", "Elysium"),
    ("3", "a", "2026-11-07T10:00:00Z", "Privacy"), ("4", "a", "2026-11-07T11:00:00Z", "Privacy"),
    ("5", "a", "2026-10-20T10:00:00Z", "Privacy"), ("6", "c", "2026-11-08T09:00:00Z", "Once")])
got = emerging(con, now)
assert [(t["theme"], t["new"]) for t in got] == [("Elysium", True), ("Once", True), ("Privacy", False)], got
# a new single-account theme still makes the list
con.executemany("insert into themes values(?,?,?,?)", [("7", "a", "2026-11-08T08:00:00Z", "Solo"), ("8", "a", "2026-11-08T08:30:00Z", "Solo"),
    ("9", "b", "2026-11-07T08:00:00Z", "Privacy"), ("10", "b", "2026-10-01T08:00:00Z", "Privacy")])
assert "Solo" in [t["theme"] for t in emerging(con, now)]
# a flip stays flagged after the account repeats its new view
assert consensus([r("a", "call", "bullish"), r("a", "call", "bearish", flip=True), r("a", "call", "bearish")], "2026-11-01") == [("BTC", 0, 1, True)]
# order: more accounts first, then more posts, so a contested coin everyone talks about isn't buried
def v(a, h, d): return dict(handle=h, asset=a, kind="call", direction=d, weight="high", created_at="2026-11-06", flip=False)
got = consensus([v("ZEC", "a", "bullish"), v("ZEC", "b", "bearish"), v("ZEC", "b", "bearish"), v("WLD", "c", "bearish")], "2026-11-01")
assert [t[0] for t in got] == ["ZEC", "WLD"], got
# on-chain items never headline, and a lone medium call doesn't qualify
assert picks([c("ETH", "high", "2026-11-08T11:00:00Z", kind="onchain"), c("SOL", "medium", "2026-11-08T11:00:00Z")], now, {}) == []
# a fresh move by a proven trader can headline; the same trader's standing position can't
got = picks([dict(c("ETH", "medium", "2026-11-08T11:00:00Z", kind="onchain"), proven=1),
             dict(c("BTC", "high", "2026-11-08T11:00:00Z", kind="onchain"), proven=1, snapshot=1)], now, {})
assert [r["asset"] for r, _, _ in got] == ["ETH"], got
# scout: candidates need 2+ mentioners or 3+ mentions, skip tracked handles; silent flags quiet old accounts
import json as _json
from scout import candidates, silent
sc = sqlite3.connect(":memory:")
sc.execute("create table posts(handle text, created_at text, raw text)")
sc.execute("create table calls(handle text, created_at text)"); sc.execute("create table themes(handle text, created_at text)")
m = lambda *us: _json.dumps({"entities": {"mentions": [{"username": u} for u in us]}})
sc.executemany("insert into posts values(?,?,?)", [("a", "2026-11-08T00:00:00", m("Good", "Tracked")), ("b", "2026-11-08T00:00:00", m("Good")),
    ("a", "2026-11-08T00:00:00", m("Once")), ("a", "2026-10-20T00:00:00", m("Old")), ("a", "2026-11-08T00:00:00", m("Old"))])
assert [c[0] for c in candidates(sc, {"a", "b", "tracked"}, set(), now)] == ["Good"]
sc.executemany("insert into calls values(?,?)", [("a", "2026-11-07T00:00:00")])
assert silent(sc, {"a", "b"}, now) == []  # a has a call, b is too new to judge (first post inside the window)
sc.execute("insert into posts values('b','2026-10-25T00:00:00','{}')")
assert silent(sc, {"a", "b"}, now) == ["b"]
# win rate: a bullish call wins if the coin beat BTC; BTC calls count plain direction; too-young calls are skipped
from scout import win_rate
class FakePx:
    def __init__(self, at): self.f = at
    def at(self, asset, t): return self.f(asset, t)
t0 = datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()
grow = lambda asset, t: {"SOL": 1 + 0.10 * (t - t0) / 86400 / 7, "BTC": 1 + 0.05 * (t - t0) / 86400 / 7}[asset]
calls = [("2026-11-01T00:00:00Z", "SOL", "bullish"), ("2026-11-01T00:00:00Z", "SOL", "bearish"),
         ("2026-11-01T00:00:00Z", "BTC", "bullish"), ("2026-11-07T12:00:00Z", "SOL", "bullish")]
assert win_rate(calls, FakePx(grow), now) == (2, 3, 3), win_rate(calls, FakePx(grow), now)
# collapse: the same account + coin + direction folds under its first item; a different direction stays separate
rs = [dict(handle="k", asset="ZCAT", direction="bullish", kind="call", n=i) for i in range(3)] + [dict(handle="k", asset="ZCAT", direction="bearish", kind="call", n=9)]
g = collapse(rs)
assert [(h["n"], [x["n"] for x in more]) for h, more in g] == [(0, [1, 2]), (9, [])], g
print("ok")
