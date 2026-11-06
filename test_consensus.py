from build import consensus, local_time, speaker


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
print("ok")
