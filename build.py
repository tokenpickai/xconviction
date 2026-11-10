"""Render xconviction.db into site/index.html (no network, no LLM).

    python3 build.py && open site/index.html
"""
import html, json, re, sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import fetch

ROOT = Path(__file__).parent
W = {"high": 3, "medium": 2, "low": 1}
ADDRESS = re.compile(r"^(0X[0-9A-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$")
WINDOW_DAYS = 7  # consensus only looks this far back, for every account, old or new


def load():
    con = fetch.db()
    cols = "post_id handle created_at kind asset direction weight summary snapshot proven usd".split()
    rows = [dict(zip(cols, r)) for r in con.execute(f"select {','.join(cols)} from calls order by created_at")]
    rows = [r for r in rows if not ADDRESS.match(r["asset"])]  # contract addresses that slipped in as tickers
    # a flip = an account's firm call on an asset pointing the other way from its previous firm call;
    # low-weight remarks can't flip
    last = {}
    for r in rows:
        if r["kind"] != "call" or r["weight"] == "low":
            continue
        key = (r["handle"], r["asset"])
        prev = last.get(key)
        r["flip"] = prev is not None and prev["direction"] != r["direction"]
        last[key] = r
    names = dict(con.execute("select handle, name from accounts"))
    (ROOT / "site" / "avatars").mkdir(parents=True, exist_ok=True)
    avatars = set()
    for handle, blob in con.execute("select handle, avatar from accounts where avatar is not null"):
        (ROOT / "site" / "avatars" / f"{handle}.jpg").write_bytes(blob)
        avatars.add(handle)
    at_call = {(p, a): v for p, a, v in con.execute("select post_id, asset, price from call_prices")}
    now = dict(con.execute("select symbol, price from coins"))
    for r in rows:
        r["name"] = names.get(r["handle"], "")
        r["avatar"] = r["handle"] in avatars
    rates = win_rates(con, datetime.now(timezone.utc))
    volume = dict(con.execute("select symbol, volume from coins where volume > 0"))
    for r in rows:
        r["wr"] = rates.get(r["handle"], (0, 0))
        r["p0"], r["p1"] = at_call.get((r["post_id"], r["asset"])), now.get(r["asset"])
        if r["kind"] == "onchain" and r.get("usd") and r["asset"] in volume:
            r["share"] = resize(r, volume[r["asset"]])
    return rows


def resize(r, volume):
    """Re-rate an on-chain move by its size against the coin's daily trading volume, not raw dollars:
    $20M is noise on ETH but huge on a small coin. Returns the share of daily volume."""
    share = r["usd"] / volume
    # ponytail: hand-set cut-offs (5% of a day's volume = high, 1% = medium); tune with more data
    r["weight"] = "high" if share >= 0.05 else "medium" if share >= 0.01 else "low"
    return share


def usd(x):
    return f"${x:,.2f}" if x >= 1 else f"${x:.4g}"


def move(r):
    if not (r["p0"] and r["p1"]):
        return ""
    pct = 100 * (r["p1"] / r["p0"] - 1)
    return (f'<span class="px">{usd(r["p0"])} → {usd(r["p1"])} '
            f'<b class="dir {"bullish" if pct >= 0 else "bearish"}">{pct:+.1f}%</b> since</span> ')


def e(s):
    return html.escape(str(s))


def speaker(r):
    # plain name for quick reading: drop emoji, and fall back to the handle if nothing is left
    return re.sub(r"[^\w .'-]", "", r["name"]).strip() or r["handle"]


def when(iso):
    """<time> showing the Vancouver date; the page script swaps in "2h ago", keeping the date as the hover text."""
    utc = iso.replace(".000Z", "Z")
    return f'<time datetime="{utc}" title="{local_time(iso)}">{local_time(iso)}</time>'


def local_time(iso):
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Vancouver"))
    return f"{t:%b} {t.day}, {t.hour % 12 or 12}:{t:%M} {t:%p}"


def wr_tag(wr):
    """Small "4/10" after a KOL's name once they have enough scored stances."""
    if not wr or wr[1] < MIN_SCORED:
        return ""
    w, n = wr
    tone = "good" if w / n >= 0.5 else "bad" if w / n < 0.4 else ""
    return f' <span class="wr {tone}" title="{w} of {n} calls beat BTC (judged 7 days after the call; newer ones so far)">{w}/{n}</span>'


def size_note(r):
    s = r.get("share")
    if s is None:
        return ""
    pct = f"{s:.0%}" if s >= 0.01 else "<1%"
    return f'<span class="why">{pct} of daily volume</span> · '


def chart(asset, series, rows):
    """7-day price chart for one coin with price (right) and date axes. Each KOL stance is a profile-picture
    marker at its time and price, ringed green (bullish) or red (bearish) with a small arrow; hover names it."""
    W, H, L, R, T, B = 568, 220, 8, 64, 14, 26
    t0, t1 = series[0][0] / 1000, series[-1][0] / 1000
    lo, hi = min(p for _, p in series), max(p for _, p in series)
    pad = (hi - lo) * 0.08 or hi * 0.01
    lo, hi = lo - pad, hi + pad
    x = lambda t: L + (t - t0) / (t1 - t0) * (W - L - R)
    y = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    grid, labels = [], []
    for i in range(5):  # 5 evenly spaced price levels
        v = lo + (hi - lo) * i / 4
        grid.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>')
        labels.append(f'<text x="{W - R + 6}" y="{y(v) + 4:.1f}" class="axis">{f"${v:,.0f}" if v >= 100 else usd(v)}</text>')
    day = datetime.fromtimestamp(t0, ZoneInfo("America/Vancouver")).replace(hour=0, minute=0, second=0) + timedelta(days=1)
    while day.timestamp() < t1:  # midnight (Vancouver) gridlines with dates
        xd = x(day.timestamp())
        grid.append(f'<line x1="{xd:.1f}" x2="{xd:.1f}" y1="{T}" y2="{H - B}" class="grid"/>')
        labels.append(f'<text x="{xd:.1f}" y="{H - 8}" class="axis" text-anchor="middle">{day:%b} {day.day}</text>')
        day += timedelta(days=1)
    line = " ".join(f"{x(t / 1000):.1f},{y(v):.1f}" for t, v in series)
    marks = []
    calls = [r for r in rows if r["asset"] == asset and r["kind"] == "call" and r["p0"] and t0 <= ts(r["created_at"]) <= t1]
    for r in stances(sorted(calls, key=lambda r: r["created_at"])):  # one marker per stance, not per repeat post
        cx, cy = x(ts(r["created_at"])), y(min(max(r["p0"], lo), hi))
        up = r["direction"] == "bullish"
        head = e(f'{speaker(r)} · {"▲" if up else "▼"} {r["direction"]} ({r["weight"]})')
        sub = e(f'{local_time(r["created_at"])} at {usd(r["p0"])} — {r["summary"]}')
        face = (f'<image href="avatars/{e(r["handle"])}.jpg" x="{cx - 10:.1f}" y="{cy - 10:.1f}" width="20" height="20" '
                f'clip-path="url(#av-{e(asset)})"/>' if r["avatar"] else "")
        marks.append(f'<g class="mk {r["direction"]}" data-head="{head}" data-sub="{sub}">'
                     f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="11.5" class="ring"/>{face}'
                     f'<circle cx="{cx + 9:.1f}" cy="{cy + 9:.1f}" r="5.5" class="badge"/>'
                     f'<text x="{cx + 9:.1f}" y="{cy + 11.5:.1f}" class="arrow" text-anchor="middle">{"▲" if up else "▼"}</text></g>')
    change = series[-1][1] / series[0][1] - 1
    return (f'<div class="chart" data-asset="{e(asset)}" hidden><div class="chart-head"><b>{e(asset)}</b> {usd(series[-1][1])} '
            f'<span class="dir {"bullish" if change >= 0 else "bearish"}">{change:+.1%}</span> '
            f'<span class="meta">14 days · hover a face for the call</span></div>'
            f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{e(asset)} price over 14 days with calls marked">'
            f'<defs><clipPath id="av-{e(asset)}" clipPathUnits="objectBoundingBox"><circle cx=".5" cy=".5" r=".5"/></clipPath></defs>'
            f'{"".join(grid)}<polyline points="{line}" class="price"/>{"".join(labels)}{"".join(marks)}</svg></div>')


def charts_section(con, rows):
    out = []
    for asset, data in con.execute("select symbol, data from series"):
        series = json.loads(data)
        if len(series) > 1:
            out.append(chart(asset, series, rows))
    return f'<div class="charts">{"".join(out)}</div>'


def item(r, show_asset=True, why=None):
    """why=None: a normal item. why="..." (even empty): an Act now pick, with its reasons."""
    act = '<span class="act">Act now</span>' if why is not None else ""
    arrow = "▲" if r["direction"] == "bullish" else "▼"
    was = "bearish" if r["direction"] == "bullish" else "bullish"
    flip = f'<span class="flip" title="changed from {was} to {r["direction"]}">flipped from {was}</span>' if r.get("flip") else ""
    asset = f'<b class="asset">{e(r["asset"])}</b>' if show_asset else ""
    snap = ' <span class="wt snap">position</span>' if r.get("snapshot") else ""
    if r["kind"] == "onchain" and r.get("proven") and not r.get("snapshot"):
        snap += ' <span class="wt pro" title="the post credits this trader with a strong track record">proven trader</span>'
    pic = f'<img class="av" src="avatars/{e(r["handle"])}.jpg" alt="">' if r["avatar"] else ""
    return (f'<li class="w-{r["weight"]}" data-asset="{e(r["asset"])}"><span class="dir {r["direction"]}">{arrow}</span> {asset} {act}'
            f'<span class="wt">{r["weight"]}</span>{flip}{snap} {pic}<b>{e(speaker(r))}</b>{wr_tag(r.get("wr"))}<b>:</b> {e(r["summary"])}'
            f'<div class="meta">{why or ""}{size_note(r)}{when(r["created_at"])} · {move(r)}'
            f'<a href="https://x.com/{e(r["handle"])}/status/{r["post_id"]}">post</a></div></li>')


HORIZON_DAYS, EARLY_DAYS = 7, 3  # a stance is judged 7 days after it starts; 3+ days gives an early read
MIN_SCORED = 5                   # show a win rate / let it move rankings only from 5 scored stances
PROVEN_STANCES, PROVEN_RATE = 10, 0.55  # a KOL's calls reach Act now only with this record


def stances(calls):
    """Repeat calls collapse into stances: an account's same-direction calls on a coin form one stance, which ends
    when it flips direction or 7 days after it began. Returns the first call of each stance (its entry)."""
    open_, heads = {}, []
    for c in calls:  # oldest first
        key = (c["handle"], c["asset"])
        cur = open_.get(key)
        if cur and cur["direction"] == c["direction"] and ts(c["created_at"]) - ts(cur["created_at"]) < HORIZON_DAYS * 86400:
            continue
        open_[key] = c
        heads.append(c)
    return heads


def ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def win_rates(con, now):
    """{handle: (wins, scored)} for accounts that mostly make calls (on-chain accounts are exempt).
    A stance wins if, 7 days on (or now, for 3-7 day old stances), the coin beat BTC in the called direction.
    BTC calls are judged on BTC's own move. Low-weight remarks and whole-market calls don't count."""
    kinds = {}
    for h, k, n in con.execute("select handle, kind, count(*) from calls group by 1, 2"):
        kinds.setdefault(h, {})[k] = n
    kols = {h for h, k in kinds.items() if k.get("call", 0) >= k.get("onchain", 0)}
    price_now = dict(con.execute("select symbol, price from coins"))
    cols = "handle created_at asset direction p0 btc0 p7 b7".split()
    calls = [dict(zip(cols, r)) for r in con.execute("""select c.handle, c.created_at, c.asset, c.direction,
            p.price, p.btc0, p.p7, p.b7 from calls c join call_prices p on p.post_id = c.post_id and p.asset = c.asset
            where c.kind = 'call' and c.weight != 'low' and c.asset != 'CRYPTO' order by c.created_at""")]
    out = {}
    for c in stances(c for c in calls if c["handle"] in kols):
        age = (now.timestamp() - ts(c["created_at"])) / 86400
        a1, b1 = (c["p7"], c["b7"]) if c["p7"] and c["b7"] else (price_now.get(c["asset"]), price_now.get("BTC"))
        if age < EARLY_DAYS or None in (c["p0"], c["btc0"], a1, b1):
            continue
        rel = a1 / c["p0"] - 1 - (0 if c["asset"] == "BTC" else b1 / c["btc0"] - 1)
        w, n = out.get(c["handle"], (0, 0))
        out[c["handle"]] = (w + ((rel > 0) == (c["direction"] == "bullish")), n + 1)
    return out


def consensus(rows, cutoff):
    """One vote per account per asset inside the window, so a busy account can't drown out the rest.
    An account's vote is its latest medium/high call, or the net of its on-chain signals if it made no call."""
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["created_at"] >= cutoff:
            by[r["asset"]][r["handle"]].append(r)
    out = []
    for asset, accounts in by.items():
        up = down = 0
        flip = False
        for rs in accounts.values():
            calls = [r for r in rs if r["kind"] == "call"]
            # low-weight remarks only count when the account has no firmer call in the window
            calls = [r for r in calls if r["weight"] != "low"] or calls
            if calls:
                net = 1 if calls[-1]["direction"] == "bullish" else -1
                # flagged if the account changed sides at any point in the window, not only on its very latest call
                flip = flip or any(r.get("flip", False) for r in calls)
            else:
                net = sum(W[r["weight"]] * (1 if r["direction"] == "bullish" else -1) for r in rs)
            up += net > 0
            down += net < 0
        out.append((asset, up, down, flip, sum(len(rs) for rs in accounts.values())))
    # most talked-about first: most accounts with a view, then most posts, then the most one-sided
    out.sort(key=lambda t: (-(t[1] + t[2]), -t[4], -abs(t[1] - t[2])))
    return [t[:4] for t in out]


def picks(rows, now, votes, n=3):
    """Act now: the strongest evidence-backed items of the last 24h that the price hasn't already run with.
    Qualifies (backed by the Nov 3-10 backtest, where loud KOL calls lost and big on-chain moves won):
      - on-chain moves rated high (>= 5% of the coin's daily volume) that aren't standing positions
      - fresh moves by proven traders (medium or high)
      - KOL calls (medium or high) only from accounts with a proven record: 55%+ over 10+ scored stances
    votes = {asset: (up, down)} from consensus."""
    # ponytail: hand-set heuristic (weight x track x agreement x freshness x room left); the Act now scorecard tunes it
    cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    best = {}
    for r in rows:
        if r["created_at"] < cutoff or r["weight"] == "low" or r.get("snapshot"):
            continue
        won, scored = r.get("wr") or (0, 0)
        if r["kind"] == "onchain":
            ok = r["weight"] == "high" or r.get("proven")
        else:
            ok = scored >= PROVEN_STANCES and won / scored >= PROVEN_RATE
        if not ok:
            continue
        up, down = votes.get(r["asset"], (0, 0))
        agree = max(1, up if r["direction"] == "bullish" else down)
        moved = 0.0  # % the price already moved in the call's direction
        if r["p0"] and r["p1"]:
            moved = 100 * (r["p1"] / r["p0"] - 1) * (1 if r["direction"] == "bullish" else -1)
        age_h = (now - datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))).total_seconds() / 3600
        track = 0.5 + won / scored if scored >= MIN_SCORED else 1  # better callers rank higher (x0.5 to x1.5)
        score = track * W[r["weight"]] * (1 + 0.5 * (agree - 1)) * max(0, 1 - max(0, moved) / 10) * max(0, 1 - age_h / 48)
        key = (r["asset"], r["direction"])
        if score > 0 and score > best.get(key, (0,))[0]:
            best[key] = (score, r, agree, moved)
    return [t[1:] for t in sorted(best.values(), key=lambda t: -t[0])[:n]]


def pick_reasons(rows, now, votes):
    """{(post_id, asset): reason html} for the Act now picks, in rank order."""
    out = {}
    for r, agree, moved in picks(rows, now, votes):
        won, scored = r.get("wr") or (0, 0)
        reasons = ["proven trader"] if r.get("proven") else []
        reasons += [f"caller {won}/{scored} vs BTC"] if r["kind"] == "call" and scored else []
        reasons += [f"{agree} accounts agree"] if agree > 1 else []
        if r["p0"] and r["p1"]:
            reasons.append("price hasn't moved yet" if abs(moved) < 1 else
                           f"{moved:.1f}% in its direction so far" if moved > 0 else f"price moved {-moved:.1f}% against it")
        out[(r["post_id"], r["asset"])] = "".join(f'<span class="why">{x}</span> · ' for x in reasons)
    return out


def emerging(con, now, days=3, n=10):
    """Themes getting talked about lately: at least 2 posts or 2 accounts in the last `days`,
    ranked by accounts, posts and newness. new = first ever seen in the last 48h."""
    cut = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    fresh = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")
    first = dict(con.execute("select theme, min(created_at) from themes group by theme"))
    out = []
    for theme, posts, accounts, latest, who in con.execute("""select theme, count(distinct post_id), count(distinct handle),
            max(created_at), min(handle) from themes where created_at >= ? group by theme""", (cut,)):
        if posts >= 2 or accounts >= 2 or first[theme] >= fresh:  # a brand-new pitch counts even once
            out.append(dict(theme=theme, posts=posts, accounts=accounts, latest=latest, new=first[theme] >= fresh, who=who))
    out.sort(key=lambda t: t["latest"], reverse=True)  # newest first among ties
    # ponytail: hand-set score; breadth beats volume (posts capped at 5 so one chatty account can't top it),
    # brand-new gets a bump. Tune with more history.
    # a lone account's repeats count for little: its posts cap at 2, a shared theme's at 5
    out.sort(key=lambda t: -(3 * t["accounts"] + min(t["posts"], 5 if t["accounts"] > 1 else 2) + 3 * t["new"]))
    # a theme only one account talks about takes at most one slot per account, so one shiller can't fill the card
    seen, kept = set(), []
    for t in out:
        if t["accounts"] == 1:
            if t["who"] in seen:
                continue
            seen.add(t["who"])
        kept.append(t)
    return kept[:n]


def candidates(con, now):
    """Big-bet candidates: themes 3+ different accounts pitched in the last 7 days whose own coins are up 5%+ this week."""
    week = (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    up = {a: json.loads(d) for a, d in con.execute("select symbol, data from series")}
    out = []
    for theme, accounts, posts in con.execute("""select theme, count(distinct handle), count(distinct post_id) from themes
            where created_at >= ? group by theme having count(distinct handle) >= 3""", (week,)).fetchall():
        # the theme's own coins: called in at least 2 of its posts (a one-off list of picks doesn't make a coin the theme's)
        coins = [a for a, n in con.execute("""select c.asset, count(distinct c.post_id) from themes t join calls c on c.post_id = t.post_id
                where t.theme = ? and t.created_at >= ? and c.asset != 'CRYPTO' group by c.asset""", (theme, week)) if n >= 2]
        rising = [a for a in coins if a in up and up[a][-1][1] / up[a][0][1] - 1 >= 0.05]  # up 5%+ on the week
        if rising:
            out.append((theme, accounts, posts, rising))
    return out


def emerging_section(con, now):
    themes = emerging(con, now)
    cands = {t: (n, ps, coins) for t, n, ps, coins in candidates(con, now)}
    known = {t["theme"] for t in themes}
    themes = [dict(theme=t, posts=ps, accounts=n, new=False, latest="") for t, (n, ps, _) in cands.items() if t not in known] + themes
    themes.sort(key=lambda t: t["theme"] not in cands)  # candidates pinned first
    if not themes:
        return ""
    names = dict(con.execute("select handle, name from accounts"))
    pics = {h for (h,) in con.execute("select handle from accounts where avatar is not null")}
    blocks = []
    for t in themes:
        posts, per = [], {}
        for row in con.execute("""select p.id, p.handle, p.created_at, p.text from themes t join posts p on p.id = t.post_id
                where t.theme = ? order by p.created_at desc""", (t["theme"],)):
            per[row[1]] = per.get(row[1], 0) + 1
            if per[row[1]] <= 2 and len(posts) < 5:  # at most 2 posts per account, 5 in all
                posts.append(row)
        lis = "".join(
            f'<li>{f"<img class=av src=avatars/{e(h)}.jpg alt>" if h in pics else ""}'
            f'<b>{e(speaker(dict(name=names.get(h, ""), handle=h)))}:</b> {e(text[:180])}{"…" if len(text) > 180 else ""}'
            f'<div class="meta">{when(at)} · <a href="https://x.com/{e(h)}/status/{pid}">post</a></div></li>'
            for pid, h, at, text in posts)
        tag = '<span class="wt new">new</span>' if t["new"] else ""
        if t["theme"] in cands:
            tag = f'<span class="act">candidate</span>{tag}<span class="meta"> rising: {e(", ".join(cands[t["theme"]][2]))}</span>'
        pitch = con.execute("""select pitch from themes where theme = ? and pitch is not null
            order by created_at desc limit 1""", (t["theme"],)).fetchone()
        blocks.append(f'<details><summary><b>{e(t["theme"])}</b>{tag}<span class="meta">{t["posts"]} post{"s" * (t["posts"] != 1)} · '
                      f'{t["accounts"]} account{"s" * (t["accounts"] != 1)}</span>'
                      f'{f"<div class=pitch>{e(pitch[0])}</div>" if pitch else ""}</summary><ul>{lis}</ul></details>')
    return f'<div class="card-box"><h2>Narrative</h2><div class="emerging">{"".join(blocks)}</div></div>'


def spend(con, now):
    """Estimated $ over the last n days. User lookups are billed once per UTC day, so take each day's max, not the sum."""
    def since(days):
        t = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rest = con.execute("select coalesce(sum(usd),0) from usage where at>=? and item!='x_users'", (t,)).fetchone()[0]
        users = con.execute("""select coalesce(sum(m),0) from (select max(usd) m from usage
            where at>=? and item='x_users' group by substr(at,1,10))""", (t,)).fetchone()[0]
        return rest + users
    first = con.execute("select min(at) from usage").fetchone()[0]
    if not first:
        return ""
    tracked = (now - datetime.fromisoformat(first.replace("Z", "+00:00"))).total_seconds() / 86400
    week = since(7)
    # a monthly pace from a few hours is wildly off (one day's user-lookup charge lands in the first run), so wait a day
    pace = f" · ~${week / min(7, tracked) * 30:.0f}/month pace" if tracked >= 1 else " · monthly pace after 24h"
    return f'Est. spend: ${since(1):.2f} last 24h · ${week:.2f} last 7 days{pace}'


TREND_DAYS = 14


def change(series, days):
    """% change over the last `days` of an hourly [ms, price] series (None if it doesn't reach back that far)."""
    t_end = series[-1][0] / 1000
    if series[0][0] / 1000 > t_end - days * 86400 + 3600:
        return None
    then = min(series, key=lambda p: abs(p[0] / 1000 - (t_end - days * 86400)))[1]
    return series[-1][1] / then - 1


STABLES = {"USDT", "USDC", "DAI", "USDE", "FDUSD", "PYUSD", "USD1", "CRYPTO"}


def trends(con):
    """{asset: 2-week change} for every coin with a stored price series: BTC in USD, everything else vs BTC."""
    series = {a: json.loads(d) for a, d in con.execute("select symbol, data from series")}
    btc = change(series["BTC"], TREND_DAYS) if "BTC" in series else None
    out = {}
    for a, sr in series.items():
        c = change(sr, TREND_DAYS)
        if c is not None and btc is not None and a not in STABLES:
            out[a] = btc if a == "BTC" else (1 + c) / (1 + btc) - 1
    return out


def regime(rows, trend, now):
    """The weekly market call for a spot holder: Risk-on (alts fine), Rotate to BTC, or Risk-off (stables).
    Looks across every coin we track, not just a few: BTC's own trend, how many coins are beating BTC,
    what KOLs say about the market vs alts, and net on-chain flow. Returns (label, css tone, reasons)."""
    week = (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    votes = {a: (u, d) for a, u, d, _ in consensus(rows, week)}
    mk_up = sum(votes.get(a, (0, 0))[0] for a in ("CRYPTO", "BTC"))
    mk_dn = sum(votes.get(a, (0, 0))[1] for a in ("CRYPTO", "BTC"))
    al_up = sum(u for a, (u, d) in votes.items() if a not in ("CRYPTO", "BTC") and a not in STABLES)
    al_dn = sum(d for a, (u, d) in votes.items() if a not in ("CRYPTO", "BTC") and a not in STABLES)
    flow = sum(W[r["weight"]] * (1 if r["direction"] == "bullish" else -1) for r in rows
               if r["kind"] == "onchain" and r["created_at"] >= week and not r.get("snapshot") and r["asset"] not in STABLES)
    btc = trend.get("BTC")
    alts = sorted(v for a, v in trend.items() if a != "BTC")
    median = alts[len(alts) // 2] if alts else None
    beat = sum(v > 0 for v in alts) / len(alts) if alts else None
    sig = lambda v, lo, hi: 0 if v is None else -1 if v < lo else 1 if v > hi else 0
    # ponytail: fixed cut-offs (BTC ±5%, median alt vs BTC ±3% or under 30% / over 60% of coins beating BTC,
    # KOL lean ±20%); score the calls once a few weeks are logged
    s_btc = sig(btc, -0.05, 0.05)
    s_alt = 0 if median is None else -1 if median < -0.03 or beat < 0.3 else 1 if median > 0.03 and beat > 0.6 else 0
    s_mkt = sig((mk_up - mk_dn) / (mk_up + mk_dn) if mk_up + mk_dn else None, -0.2, 0.2)
    s_kal = sig((al_up - al_dn) / (al_up + al_dn) if al_up + al_dn else None, -0.2, 0.2)
    s_flow = -1 if flow < -3 else 1 if flow > 3 else 0
    reasons = [f"BTC {btc:+.1%} over 2 weeks" if btc is not None else "BTC trend: no data",
               f"{sum(v > 0 for v in alts)} of {len(alts)} tracked coins beat BTC (median {median:+.1%})" if alts else "alts vs BTC: no data",
               f"KOLs: market {mk_up}▲ {mk_dn}▼, alts {al_up}▲ {al_dn}▼",
               "on-chain flow: " + ("net selling" if s_flow < 0 else "net buying" if s_flow > 0 else "mixed")]
    if s_btc < 0 and (s_mkt < 0 or s_flow < 0):
        return "Risk-off", "bearish", reasons
    if s_alt < 0 or (s_kal < 0 and s_btc >= 0):
        return "Rotate to BTC", "warn", reasons
    if s_alt > 0 and s_kal >= 0 and s_btc >= 0:
        return "Risk-on", "bullish", reasons
    return "Neutral · lean BTC", "", reasons  # J's rule: when in doubt, BTC


def regime_line(rows, trend, now):
    label, tone, reasons = regime(rows, trend, now)
    return (f'<div class="regime hovertip" data-head="Market call: {e(label)}" data-sub="{e(" · ".join(reasons))}">'
            f'Market: <b class="dir {tone}">{e(label)}</b></div>')


def assets_section(rows, trend={}):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    cards = []
    for asset, up, down, flip in consensus(rows, cutoff):
        tr = trend.get(asset)
        tag = (f'<small class="tr dir {"bullish" if tr >= 0 else "bearish"}" title="2-week change{"" if asset == "BTC" else " vs BTC"}">'
               f'{tr:+.0%}{"" if asset == "BTC" else " vs BTC"}</small>') if tr is not None else ""
        cards.append(f'<div class="card" data-asset="{e(asset)}" role="button" tabindex="0"><b>{e(asset)}</b>{tag}{"<i title=flipped></i>" if flip else ""}'
                     f'<span class="dir bullish">▲{up}</span><span class="dir bearish">▼{down}</span></div>')
    return f'<nav class="tickers">{"".join(cards)}</nav>'


PAGE = 30


def pinned(rows, whys, now=None):
    """Act now picks first, in rank order, then fresh moves by proven traders (last 48h), then everything else
    newest first."""
    key = lambda r: (r["post_id"], r["asset"])
    top = sorted((r for r in rows if key(r) in whys), key=lambda r: list(whys).index(key(r)))
    fresh = ((now or datetime.now(timezone.utc)) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")
    pro = [r for r in rows if key(r) not in whys and r["kind"] == "onchain" and r.get("proven")
           and not r.get("snapshot") and r["created_at"] >= fresh]
    seen = {key(r) for r in top + pro}
    return top + pro + [r for r in rows if key(r) not in seen]


def collapse(rows):
    """Group repeats: the same account saying the same direction on the same coin shows once (its first,
    i.e. pinned or newest, item), with the rest folded under it. [(head, [repeats...]), ...]"""
    groups, at = [], {}
    for r in rows:
        key = (r["handle"], r["asset"], r["direction"], r["kind"])
        if key in at:
            groups[at[key]][1].append(r)
        else:
            at[key] = len(groups)
            groups.append((r, []))
    return groups


def paged(rows, whys={}):
    """Repeats folded under their first item; first PAGE groups visible, the rest revealed PAGE at a time."""
    lis = []
    for i, (r, more) in enumerate(collapse(rows)):
        li = item(r, why=whys.get((r["post_id"], r["asset"])))
        if more:
            fold = (f'<details class="repeats"><summary>+{len(more)} more from {e(speaker(r))} on {e(r["asset"])}</summary>'
                    f'<ul>{"".join(item(x) for x in more)}</ul></details>')
            li = li[:-len("</li>")] + fold + "</li>"
        lis.append(li.replace('<li class="', '<li class="later ' if i >= PAGE else '<li class="', 1))
    lis = "".join(lis)
    rows = collapse(rows)
    more = '<button class="more">Show more</button>' if len(rows) > PAGE else ""
    return f'<ul class="paged">{lis}</ul>{more}'


def page(rows, spend_html="", emerging_html="", charts_html="", record_html="", trend={}):
    # the feed covers the same 7 days as the consensus; older items stay in the database
    week = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    now = datetime.now(timezone.utc)
    whys = pick_reasons(rows, now, {a: (u, d) for a, u, d, _ in consensus(rows, week)})
    calls = [r for r in reversed(rows) if r["kind"] == "call" and r["created_at"] >= week]
    chain = [r for r in reversed(rows) if r["kind"] == "onchain" and r["created_at"] >= week]
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Xconviction</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700;800&family=Archivo:wght@800&display=swap');
:root{{--bg:#fff;--fg:#0f1419;--mute:#536471;--line:#eff3f4;--tile:#f7f9f9;--up:#00ba7c;--down:#f4212e;--flip:var(--fg);--hi:#b8860b;--md:#c08a3e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#000;--fg:#e7e9ea;--mute:#71767b;--line:#2f3336;--tile:#16181c;--hi:#ffd400}}}}
body{{background:var(--bg);color:var(--fg);font:400 15px/20px TwitterChirp,Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased;margin:0}}
/* X-style 3 columns: empty left rail, 600px main column, 350px right sidebar; stacks on narrow screens */
.layout{{display:grid;grid-template-columns:minmax(0,1fr) 600px minmax(0,1fr);min-height:100vh}}
.layout main{{border-left:1px solid var(--line);border-right:1px solid var(--line);padding:0;min-width:0}}
main li{{padding:12px 16px}} main footer{{padding:0 16px}}
.layout .right{{padding:16px 0 16px 30px}} .right-inner{{position:sticky;top:16px;max-width:350px}}
.card-box{{border:1px solid var(--line);border-radius:16px;padding:12px 16px}}
.card-box h2{{font-size:20px;line-height:24px;font-weight:800;margin:0 0 4px;color:var(--fg)}}
.card-box .emerging details:last-child{{border-bottom:0}}
@media (max-width:1000px){{.layout{{grid-template-columns:1fr;max-width:600px;margin:0 auto}} 
  .layout main{{border:0}} .layout .right{{padding:0 16px 16px}} .right-inner{{position:static;max-width:none}}}}
.tabs{{display:flex;margin-top:16px}}
.tabs button{{flex:1;background:none;border:0;border-bottom:2px solid var(--line);padding:16px 0;font:500 15px/20px inherit;font-family:inherit;color:var(--mute);cursor:pointer}}
.tabs button[aria-selected=true]{{color:var(--fg);font-weight:700;border-bottom-color:var(--fg)}}
.tabs button:hover{{background:var(--tile)}}
ul{{list-style:none;padding:0;margin:0}} li{{padding:12px 0;border-bottom:1px solid var(--line)}}
.av{{width:18px;height:18px;border-radius:50%;vertical-align:-4px;margin-right:4px}}
.meta{{color:var(--mute);font-size:13px;margin-top:4px}} .meta a{{color:var(--mute);text-decoration:none}} .meta a:hover{{text-decoration:underline}}
.dir.bullish{{color:var(--up)}} .dir.bearish{{color:var(--down)}} .asset{{font-weight:700;margin-right:4px}}
.wt,.flip{{font-size:12px;line-height:16px;font-weight:500;border-radius:999px;padding:0 8px;margin-right:6px;border:1px solid;display:inline-block}}
.w-high .wt{{color:var(--hi);border-color:var(--hi)}} .w-medium .wt{{color:var(--md);border-color:var(--md)}} .w-low .wt{{color:var(--mute);border-color:var(--line)}}
.flip{{color:var(--bg);background:var(--fg);border-color:var(--fg)}}
.logo{{display:flex;align-items:center;font:800 20px/24px Archivo,sans-serif;letter-spacing:-.4px;margin:4px 0 16px}}

.logo svg{{width:17px;height:17px;fill:var(--fg);margin-right:1px}}
.emerging details{{border-bottom:1px solid var(--line);padding:10px 0}} .emerging summary{{cursor:pointer}}
.emerging summary .meta{{display:inline-block;margin-left:8px;white-space:nowrap}} .emerging .new{{color:var(--up);border-color:var(--up);margin-left:6px}}
.emerging .pitch{{font-size:14px;margin-top:2px}} .emerging ul{{margin-top:4px}} .emerging li{{padding:8px 0 8px 12px;border-bottom:0;font-size:14px}}
li.later{{display:none}} .filtering li.later:not([hidden]){{display:list-item}} .filtering .more{{display:none}}
.more{{display:block;width:100%;padding:14px 0;background:none;border:0;border-bottom:1px solid var(--line);color:var(--fg);font:500 15px/20px inherit;font-family:inherit;cursor:pointer}}
.more:hover{{background:var(--tile)}}
.repeats{{margin-top:6px}} .repeats summary{{cursor:pointer;color:var(--mute);font-size:13px}}
main .repeats li{{padding:10px 0 0 12px;border-bottom:0;font-size:14px}}
footer{{margin:24px 0 8px;font-size:13px;color:var(--mute);text-align:center}}
.left-inner{{position:sticky;top:0;max-height:100vh;overflow-y:auto;scrollbar-width:none;width:240px;margin-left:auto;padding:16px 20px 16px 0;box-sizing:border-box}}
.left-inner::-webkit-scrollbar{{display:none}} .left .logo{{flex-wrap:wrap;margin:4px 0 16px 12px}}

.tickers{{display:flex;flex-direction:column;gap:2px}}
.card{{display:flex;align-items:center;gap:8px;padding:8px 12px;border:1px solid transparent;border-radius:999px;font-size:15px;cursor:pointer;white-space:nowrap}}
.card:hover{{background:var(--tile)}} .card .tr{{font-size:11px;font-weight:500;margin-right:auto;opacity:.85}} .card b:has(+ .tr){{margin-right:0}} .regime{{margin:-8px 0 14px 12px;font-size:13px;color:var(--mute);cursor:default}}
.dir.warn{{color:var(--hi)}} .wt.pro{{color:var(--up);border-color:var(--up)}} .card.on{{border-color:var(--fg)}} .card b{{margin-right:auto}}
.charts .chart{{padding:16px 16px 4px;border-bottom:1px solid var(--line)}} .chart svg{{width:100%;height:auto;display:block;margin-top:8px}}
.chart .price{{fill:none;stroke:var(--fg);stroke-width:1.5;opacity:.8}} .chart .grid{{stroke:var(--line);stroke-width:1}}
.chart .axis{{fill:var(--mute);font-size:11px}} .chart .mk .ring{{fill:var(--tile);stroke-width:2.5}}
.chart .mk.bullish .ring,.chart .mk.bullish .badge{{stroke:var(--up);fill:var(--up)}} .chart .mk.bearish .ring,.chart .mk.bearish .badge{{stroke:var(--down);fill:var(--down)}}
.chart .mk .ring{{fill:var(--tile)}} .chart .arrow{{fill:#fff;font-size:7px}} .chart .mk:hover .ring{{stroke-width:4}} .chart .mk{{cursor:pointer}}
.tip{{position:fixed;z-index:10;max-width:300px;padding:8px 10px;border-radius:8px;background:var(--bg);border:1px solid var(--line);
  box-shadow:0 4px 16px rgba(0,0,0,.35);font-size:13px;line-height:17px;pointer-events:none}} .tip b{{display:block;margin-bottom:2px}}
.chart-head .meta{{display:inline;margin-left:6px}}
.record{{padding:12px 16px;font-size:13px;color:var(--mute);border-bottom:1px solid var(--line)}} .record b{{color:var(--fg)}}
.label{{margin:20px 0 0;font-size:13px;font-weight:700;color:var(--hi);text-transform:uppercase;letter-spacing:.5px}}
.why{{color:var(--fg);font-weight:500}}
.wr{{font-size:12px;font-weight:500;color:var(--mute)}} .wr.good{{color:var(--up)}} .wr.bad{{color:var(--down)}}
.act{{font-size:12px;line-height:16px;font-weight:700;border-radius:999px;padding:0 8px;margin-right:6px;background:var(--hi);color:#000;display:inline-block}}
.card b{{font-weight:700}} .card .dir{{font-weight:600}} .card i{{width:6px;height:6px;border-radius:50%;background:var(--flip)}}
@media (max-width:1000px){{.layout{{grid-template-columns:minmax(0,1fr)}} .layout .left{{min-width:0}} .left-inner{{position:static;width:auto;max-height:none;margin:0;padding:16px 16px 0}}
  .tickers{{flex-direction:row;overflow-x:auto;scrollbar-width:none}} .card b{{margin-right:0}}}}
</style>
<div class="layout"><aside class="left"><div class="left-inner">
<div class="logo"><svg viewBox="0 0 24 24" aria-label="X"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>conviction</div>
{regime_line(rows, trend, datetime.now(timezone.utc)) if trend else ""}
{assets_section(rows, trend)}
</div></aside><main>
{charts_html}
{record_html}
<div class="tabs" role="tablist"><button role="tab" data-tab="calls">Calls</button><button role="tab" data-tab="onchain">On-chain</button></div>
<div id="calls" role="tabpanel">{paged(pinned(calls, whys), whys)}</div>
<div id="onchain" role="tabpanel">{paged(pinned(chain, whys), whys)}</div>
<footer><span class="updated" data-t="{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"></span>{f" · {spend_html}" if spend_html else ""}</footer>
</main><aside class="right"><div class="right-inner">{emerging_html}</div></aside></div>
<script>
const tabs = document.querySelectorAll('.tabs button');
const show = name => tabs.forEach(t => {{
  const on = t.dataset.tab === name;
  t.setAttribute('aria-selected', on);
  document.getElementById(t.dataset.tab).hidden = !on;
}});
tabs.forEach(t => t.onclick = () => show(t.dataset.tab));
show('calls');
const ago = t => {{
  const m = Math.max(0, Math.round((Date.now() - new Date(t)) / 60000));
  return m < 1 ? 'just now' : m < 60 ? m + 'm ago' : m < 1440 ? Math.round(m / 60) + 'h ago' : Math.round(m / 1440) + 'd ago';
}};
document.querySelectorAll('time[datetime]').forEach(el => el.textContent = ago(el.getAttribute('datetime')));
const u = document.querySelector('.updated');
u.textContent = 'Updated ' + ago(u.dataset.t);
document.querySelectorAll('.more').forEach(b => b.onclick = () => {{
  const later = b.previousElementSibling.querySelectorAll('li.later');
  [...later].slice(0, {PAGE}).forEach(li => li.classList.remove('later'));
  if (later.length <= {PAGE}) b.remove();
}});
const tip = document.createElement('div'); tip.className = 'tip'; tip.hidden = true; document.body.append(tip);
document.querySelectorAll('.mk, .hovertip').forEach(m => {{
  m.onmouseenter = () => {{ tip.innerHTML = '<b></b><span></span>'; tip.firstChild.textContent = m.dataset.head;
    tip.lastChild.textContent = m.dataset.sub; tip.hidden = false; }};
  m.onmousemove = ev => {{ tip.style.left = Math.min(ev.clientX + 14, innerWidth - 320) + 'px'; tip.style.top = (ev.clientY + 14) + 'px'; }};
  m.onmouseleave = () => tip.hidden = true;
}});
let only = null;
document.querySelectorAll('.card').forEach(card => card.onclick = card.onkeydown = ev => {{
  if (ev.type === 'keydown' && ev.key !== 'Enter' && ev.key !== ' ') return;
  only = only === card.dataset.asset ? null : card.dataset.asset;
  document.querySelectorAll('.card').forEach(k => k.classList.toggle('on', k.dataset.asset === only));
  document.querySelectorAll('li[data-asset]').forEach(li => li.hidden = only !== null && li.dataset.asset !== only);
  document.body.classList.toggle('filtering', only !== null);  // a filtered view shows every match, unpaged
  document.querySelectorAll('.chart').forEach(ch => ch.hidden = ch.dataset.asset !== only);
}});
</script>
"""


LOOKBACK_H = 72  # Act now picks are judged 72 hours after they were shown


def log_picks(con, picked, now):
    """Record each Act now pick the first time it's shown, with the coin's and BTC's price at that moment."""
    con.execute("""create table if not exists act_log(post_id text, asset text, direction text, kind text, handle text,
        picked_at text, p0 real, b0 real, p72 real, b72 real, primary key(post_id, asset))""")
    price = dict(con.execute("select symbol, price from coins"))
    for r, _, _ in picked:
        if r["asset"] in price and "BTC" in price:
            con.execute("insert or ignore into act_log values(?,?,?,?,?,?,?,?,null,null)", (r["post_id"], r["asset"],
                        r["direction"], r["kind"], r["handle"], now.strftime("%Y-%m-%dT%H:%M:%SZ"), price[r["asset"]], price["BTC"]))


def score_picks(con, now):
    """Fill the 72h-later prices for logged picks, from stored price series or (just after 72h) the latest price."""
    price = dict(con.execute("select symbol, price from coins"))
    series = {a: json.loads(d) for a, d in con.execute("select symbol, data from series")}
    at = lambda a, t: (min(series[a], key=lambda p: abs(p[0] / 1000 - t))[1]
                       if a in series and abs(series[a][-1][0] / 1000 - t) < 7 * 86400 and series[a][0][0] / 1000 <= t + 3600
                       else price.get(a) if 0 <= now.timestamp() - t < 3 * 3600 else None)
    for pid, a, picked in con.execute("select post_id, asset, picked_at from act_log where p72 is null").fetchall():
        t = ts(picked) + LOOKBACK_H * 3600
        if t <= now.timestamp():
            p, b = at(a, t), at("BTC", t)
            if p and b:
                con.execute("update act_log set p72=?, b72=? where post_id=? and asset=?", (p, b, pid, a))
    con.commit()


def scorecard(con):
    """One line: how Act now picks did 72h later against BTC. This is the number to trust Act now by."""
    rows = con.execute("select asset, direction, p0, b0, p72, b72 from act_log").fetchall()
    done = [r for r in rows if r[4]]
    if not done:
        return ""  # nothing scored yet: show nothing rather than a placeholder
    rel = [(p72 / p0 - 1 - (0 if a == "BTC" else b72 / b0 - 1)) * (1 if d == "bullish" else -1) for a, d, p0, b0, p72, b72 in done]
    wins = sum(x > 0 for x in rel)
    return (f'<div class="record">Act now record: <b>{wins}/{len(done)}</b> picks beat BTC {LOOKBACK_H}h later '
            f'(<span class="dir {"bullish" if sum(rel) >= 0 else "bearish"}">avg {sum(rel) / len(rel):+.1%}</span>)'
            f'{f" · {len(rows) - len(done)} pending" if len(rows) > len(done) else ""}</div>')


def log_regime(con, rows, trend, now):
    """One row per day: the market call and BTC/ETH/SOL prices, so the call can later be scored against holding."""
    con.execute("create table if not exists regime_log(day text primary key, label text, btc real, eth real, sol real)")
    if trend:
        price = dict(con.execute("select symbol, price from coins"))
        con.execute("insert or ignore into regime_log values(?,?,?,?,?)", (now.strftime("%Y-%m-%d"), regime(rows, trend, now)[0],
                    price.get("BTC"), price.get("ETH"), price.get("SOL")))
        con.commit()


if __name__ == "__main__":
    rows = load()
    (ROOT / "site").mkdir(exist_ok=True)
    con, now = fetch.db(), datetime.now(timezone.utc)
    week = (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    log_picks(con, picks(rows, now, {a: (u, d) for a, u, d, _ in consensus(rows, week)}), now)
    score_picks(con, now)
    log_regime(con, rows, trends(con), now)
    (ROOT / "site" / "index.html").write_text(page(rows, spend(con, now), emerging_section(con, now), charts_section(con, rows),
                                                   scorecard(con), trends(con)))
    print(f"site/index.html: {len(rows)} items")
