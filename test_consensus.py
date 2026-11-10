from datetime import datetime, timedelta, timezone

import sqlite3

from build import change, regime, log_picks, score_picks, scorecard, chart, resize, collapse, consensus, emerging, local_time, picks, speaker, spend, stances, win_rates


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
# picks: proven callers qualify (fresh beats stale, a call the price already ran with drops out, low never shows)
now = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
PRO = (7, 10)  # a caller with a 70% record over 10 stances
def c(asset, w, t, p0=None, p1=None, d="bullish", kind="call", wr=PRO):
    return dict(asset=asset, weight=w, created_at=t, p0=p0, p1=p1, direction=d, kind=kind, wr=wr)
got = picks([c("OLD", "high", "2026-11-07T14:00:00Z"), c("NEW", "high", "2026-11-08T11:00:00Z"),
             c("RAN", "high", "2026-11-08T11:00:00Z", 100, 115), c("LOW", "low", "2026-11-08T11:00:00Z")], now, {})
assert [r["asset"] for r, _, _ in got] == ["NEW", "OLD"], got
# unproven or losing KOLs never qualify, however loud
assert picks([c("A", "high", "2026-11-08T11:00:00Z", wr=(0, 0)), c("B", "high", "2026-11-08T11:00:00Z", wr=(3, 10))], now, {}) == []
# agreement lifts a medium call above a lone high one of the same age
got = picks([c("A", "high", "2026-11-08T11:00:00Z"), c("B", "medium", "2026-11-08T11:00:00Z")], now, {"B": (3, 0)})
assert got[0][0]["asset"] == "B"
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
# on-chain: a high (big vs volume) move qualifies; medium only from a proven trader; standing positions never
got = picks([c("ETH", "high", "2026-11-08T11:00:00Z", kind="onchain", wr=None), c("SOL", "medium", "2026-11-08T11:00:00Z", kind="onchain", wr=None)], now, {})
assert [r["asset"] for r, _, _ in got] == ["ETH"], got
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
# resize: the same $20M is noise on a big coin and huge on a small one
assert (resize(dict(usd=20e6), 15e9), ) and resize(r1 := dict(usd=20e6), 15e9) < 0.01 and r1["weight"] == "low"
assert resize(r2 := dict(usd=20e6), 100e6) == 0.2 and r2["weight"] == "high"
# chart: one dot per in-range item, coloured by direction
svg = chart("ZEC", [[1762300000000, 1300], [1762900000000, 1170]], [dict(asset="ZEC", created_at="2025-11-08T00:00:00Z", p0=1250,
    direction="bullish", kind="call", weight="high", summary="s", handle="k", name="K", avatar=True)])
assert svg.count('class="mk bullish"') == 1 and "avatars/k.jpg" in svg and 'class="axis"' in svg
# act now log: picks are logged once, scored 72h later vs BTC, and summarised
lg = sqlite3.connect(":memory:")
lg.executescript("create table coins(symbol, price); create table series(symbol, data);")
lg.executemany("insert into coins values(?,?)", [("SOL", 100), ("BTC", 50000)])
pick = dict(post_id="p", asset="SOL", direction="bullish", kind="onchain", handle="h")
log_picks(lg, [(pick, 1, 0)], now); log_picks(lg, [(pick, 1, 0)], now)
assert lg.execute("select count(*) from act_log").fetchone()[0] == 1
assert scorecard(lg) == ""
lg.executemany("update coins set price=? where symbol=?", [(110, "SOL"), (50000, "BTC")])
score_picks(lg, now + timedelta(hours=73))
assert "1/1" in scorecard(lg) and "+10.0%" in scorecard(lg), scorecard(lg)
# contract addresses are never treated as tickers
from build import ADDRESS
assert ADDRESS.match("0X7FE995A80075DF3DC8AE11A9B82C7FE4202CD87F") and not ADDRESS.match("ZEC") and not ADDRESS.match("CRYPTO")
# change: % over the last N days of an hourly series; None if the series is too short
hs = [[(1762000000 + i * 3600) * 1000, 100 + i] for i in range(24 * 15)]
assert abs(change(hs, 14) - (459 / 123 - 1)) < 1e-9 and change(hs[:24], 14) is None
# regime: falling BTC with bearish KOLs is risk-off; weak alts alone is rotate; strong alts with bullish KOLs is risk-on
k = lambda a, d: dict(handle=a + d, asset="BTC", kind="call", direction=d, weight="high", created_at="2026-11-07T00:00:00Z", flip=False)
assert regime([k("a", "bearish"), k("b", "bearish")], {"BTC": -0.08, "ETH": -0.01, "SOL": 0.0}, now)[0] == "Risk-off"
assert regime([], {"BTC": 0.0, "ETH": -0.06, "SOL": -0.05}, now)[0] == "Rotate to BTC"
assert regime([k("a", "bullish")], {"BTC": 0.02, "ETH": 0.05, "SOL": 0.06}, now)[0] == "Risk-on"
assert regime([], {}, now)[0] == "Neutral · lean BTC"
# one_way: 20+ calls, 90%+ bullish, half on one coin = bag promoter
from scout import one_way
ow = sqlite3.connect(":memory:")
ow.execute("create table calls(handle, kind, asset, direction)")
ow.executemany("insert into calls values(?,?,?,?)", [("k", "call", "ZCAT", "bullish")] * 15 + [("k", "call", "ZEC", "bullish")] * 6 + [("d", "call", "BTC", "bullish")] * 12 + [("d", "call", "ETH", "bearish")] * 10)
assert one_way(ow, "k") and not one_way(ow, "d")
print("ok")
