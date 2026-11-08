from datetime import datetime, timezone

import sqlite3

from build import consensus, local_time, picks, speaker, spend


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
def c(asset, w, t, p0=None, p1=None, d="bullish"):
    return dict(asset=asset, weight=w, created_at=t, p0=p0, p1=p1, direction=d)
got = picks([c("OLD", "high", "2026-11-07T14:00:00Z"), c("NEW", "high", "2026-11-08T11:00:00Z"),
             c("RAN", "high", "2026-11-08T11:00:00Z", 100, 115), c("LOW", "low", "2026-11-08T11:00:00Z")], now, {})
assert [r["asset"] for r, _, _ in got] == ["NEW", "OLD"], got
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
print("ok")
