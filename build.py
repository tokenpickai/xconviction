"""Render xconviction.db into site/index.html (no network, no LLM).

    python3 build.py && open site/index.html
"""
import html, re, sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import fetch

ROOT = Path(__file__).parent
W = {"high": 3, "medium": 2, "low": 1}
WINDOW_DAYS = 7  # consensus only looks this far back, for every account, old or new


def load():
    con = fetch.db()
    cols = "post_id handle created_at kind asset direction weight summary".split()
    rows = [dict(zip(cols, r)) for r in con.execute(f"select {','.join(cols)} from calls order by created_at")]
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
        r["p0"], r["p1"] = at_call.get((r["post_id"], r["asset"])), now.get(r["asset"])
    return rows


def usd(x):
    return f"${x:,.2f}" if x >= 1 else f"${x:.4g}"


def move(r):
    if not (r["p0"] and r["p1"]):
        return ""
    pct = 100 * (r["p1"] / r["p0"] - 1)
    return (f'<span class="px">{usd(r["p0"])} → {usd(r["p1"])} '
            f'<b class="dir {"bullish" if pct >= 0 else "bearish"}">{pct:+.1f}%</b></span> · ')


def e(s):
    return html.escape(str(s))


def speaker(r):
    # plain name for quick reading: drop emoji, and fall back to the handle if nothing is left
    return re.sub(r"[^\w .'-]", "", r["name"]).strip() or r["handle"]


def local_time(iso):
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Vancouver"))
    return f"{t:%b} {t.day}, {t.hour % 12 or 12}:{t:%M} {t:%p}"


def item(r, show_asset=True, why=""):
    arrow = "▲" if r["direction"] == "bullish" else "▼"
    flip = '<span class="flip">flipped</span>' if r.get("flip") else ""
    asset = f'<b class="asset">{e(r["asset"])}</b>' if show_asset else ""
    pic = f'<img class="av" src="avatars/{e(r["handle"])}.jpg" alt="">' if r["avatar"] else ""
    return (f'<li class="w-{r["weight"]}" data-asset="{e(r["asset"])}"><span class="dir {r["direction"]}">{arrow}</span> {asset} '
            f'<span class="wt">{r["weight"]}</span>{flip} {pic}<b>{e(speaker(r))}:</b> {e(r["summary"])}'
            f'<div class="meta">{why}{local_time(r["created_at"])} · {move(r)}'
            f'<a href="https://x.com/{e(r["handle"])}/status/{r["post_id"]}">post</a></div></li>')


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
                flip = flip or calls[-1].get("flip", False)
            else:
                net = sum(W[r["weight"]] * (1 if r["direction"] == "bullish" else -1) for r in rs)
            up += net > 0
            down += net < 0
        out.append((asset, up, down, flip))
    # strongest consensus first: biggest up/down gap, then most votes
    return sorted(out, key=lambda t: (-abs(t[1] - t[2]), -(t[1] + t[2])))


def picks(rows, now, votes, n=3):
    """Act now: the strongest medium/high items of the last 24h that the price hasn't already run with.
    votes = {asset: (up, down)} from consensus."""
    # ponytail: hand-set heuristic (weight x agreement x freshness x room left); tune once there's a week of data
    cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    best = {}
    for r in rows:
        if r["created_at"] < cutoff or r["weight"] == "low":
            continue
        up, down = votes.get(r["asset"], (0, 0))
        agree = max(1, up if r["direction"] == "bullish" else down)
        moved = 0.0  # % the price already moved in the call's direction
        if r["p0"] and r["p1"]:
            moved = 100 * (r["p1"] / r["p0"] - 1) * (1 if r["direction"] == "bullish" else -1)
        age_h = (now - datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))).total_seconds() / 3600
        score = W[r["weight"]] * (1 + 0.5 * (agree - 1)) * max(0, 1 - max(0, moved) / 10) * max(0, 1 - age_h / 48)
        key = (r["asset"], r["direction"])
        if score > 0 and score > best.get(key, (0,))[0]:
            best[key] = (score, r, agree, moved)
    return [t[1:] for t in sorted(best.values(), key=lambda t: -t[0])[:n]]


def picks_section(rows, now, votes):
    out = []
    for r, agree, moved in picks(rows, now, votes):
        reasons = [f"{agree} accounts agree"] if agree > 1 else []
        if r["p0"] and r["p1"]:
            reasons.append("price hasn't moved yet" if abs(moved) < 1 else f"{moved:+.1f}% in its direction so far")
        why = "".join(f'<span class="why">{x}</span> · ' for x in reasons)
        out.append(item(r, why=why))
    return f'<div class="label">Act now</div><ul class="picks">{"".join(out)}</ul>' if out else ""


def assets_section(rows):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    cards = []
    for asset, up, down, flip in consensus(rows, cutoff):
        cards.append(f'<div class="card" data-asset="{e(asset)}" role="button" tabindex="0"><b>{e(asset)}</b>{"<i title=flipped></i>" if flip else ""}'
                     f'<span class="dir bullish">▲{up}</span><span class="dir bearish">▼{down}</span></div>')
    return (f'<div class="strip"><button class="nav prev" aria-label="Scroll left">‹</button>'
            f'<div class="cards">{"".join(cards)}</div>'
            f'<button class="nav next" aria-label="Scroll right">›</button></div>')


def page(rows):
    calls = [r for r in reversed(rows) if r["kind"] == "call"]
    chain = [r for r in reversed(rows) if r["kind"] == "onchain"]
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Xconviction</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700;800&family=Archivo:wght@800&display=swap');
:root{{--bg:#fff;--fg:#0f1419;--mute:#536471;--line:#eff3f4;--tile:#f7f9f9;--up:#00ba7c;--down:#f4212e;--flip:var(--fg);--hi:#f7931a;--md:var(--fg)}}
@media (prefers-color-scheme:dark){{:root{{--bg:#000;--fg:#e7e9ea;--mute:#71767b;--line:#2f3336;--tile:#16181c}}}}
body{{background:var(--bg);color:var(--fg);font:400 15px/20px TwitterChirp,Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased;max-width:600px;margin:0 auto;padding:16px}}
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
.updated{{margin-left:auto;font:400 13px/20px TwitterChirp,Inter,sans-serif;letter-spacing:0;color:var(--mute)}}
.logo svg{{width:17px;height:17px;fill:var(--fg);margin-right:1px}}
.strip{{position:relative}}
.cards{{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;scroll-behavior:smooth}} .cards::-webkit-scrollbar{{display:none}}
.card{{flex:0 0 auto;display:flex;align-items:center;gap:6px;background:var(--tile);border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:14px;white-space:nowrap}}
.card{{cursor:pointer}} .card.on{{border-color:var(--fg)}}
.label{{margin:20px 0 0;font-size:13px;font-weight:700;color:var(--hi);text-transform:uppercase;letter-spacing:.5px}}
.picks{{border-bottom:2px solid var(--line)}} .picks li:last-child{{border-bottom:0}} .why{{color:var(--fg);font-weight:500}}
.card b{{font-weight:700}} .card .dir{{font-weight:600}} .card i{{width:6px;height:6px;border-radius:50%;background:var(--flip)}}
.nav{{position:absolute;top:0;bottom:0;width:48px;border:0;font-size:22px;color:var(--fg);cursor:pointer;opacity:0;transition:opacity .15s}}
.nav.prev{{left:0;background:linear-gradient(to right,var(--bg) 40%,transparent);text-align:left}}
.nav.next{{right:0;background:linear-gradient(to left,var(--bg) 40%,transparent);text-align:right}}
.strip:hover .nav:not([hidden]){{opacity:1}} .nav[hidden]{{display:none}}
</style>
<div class="logo"><svg viewBox="0 0 24 24" aria-label="X"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>conviction<span class="updated" data-t="{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"></span></div>
{assets_section(rows)}
{picks_section(rows, datetime.now(timezone.utc), {a: (u, d) for a, u, d, _ in consensus(rows, (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%S"))})}
<div class="tabs" role="tablist"><button role="tab" data-tab="calls">Calls</button><button role="tab" data-tab="onchain">On-chain</button></div>
<ul id="calls" role="tabpanel">{"".join(item(r) for r in calls)}</ul>
<ul id="onchain" role="tabpanel">{"".join(item(r) for r in chain)}</ul>
<script>
const tabs = document.querySelectorAll('.tabs button');
const show = name => tabs.forEach(t => {{
  const on = t.dataset.tab === name;
  t.setAttribute('aria-selected', on);
  document.getElementById(t.dataset.tab).hidden = !on;
}});
tabs.forEach(t => t.onclick = () => {{ show(t.dataset.tab); try {{ localStorage.tab = t.dataset.tab; }} catch (_) {{}} }});
let saved; try {{ saved = localStorage.tab; }} catch (_) {{}}
show(saved === 'onchain' ? 'onchain' : 'calls');
const u = document.querySelector('.updated'), mins = Math.round((Date.now() - new Date(u.dataset.t)) / 60000);
u.textContent = 'updated ' + (mins < 60 ? mins + ' min' : Math.round(mins / 60) + ' hr') + ' ago';
let only = null;
document.querySelectorAll('.card').forEach(card => card.onclick = card.onkeydown = ev => {{
  if (ev.type === 'keydown' && ev.key !== 'Enter' && ev.key !== ' ') return;
  only = only === card.dataset.asset ? null : card.dataset.asset;
  document.querySelectorAll('.card').forEach(k => k.classList.toggle('on', k.dataset.asset === only));
  document.querySelectorAll('li[data-asset]').forEach(li => li.hidden = only !== null && li.dataset.asset !== only);
}});
const c = document.querySelector('.cards'), prev = document.querySelector('.prev'), next = document.querySelector('.next');
const sync = () => {{ prev.hidden = c.scrollLeft <= 0; next.hidden = c.scrollLeft + c.clientWidth >= c.scrollWidth - 1; }};
prev.onclick = () => c.scrollBy({{left: -c.clientWidth * 0.8}});
next.onclick = () => c.scrollBy({{left: c.clientWidth * 0.8}});
c.parentElement.addEventListener('wheel', ev => {{ if (Math.abs(ev.deltaY) > Math.abs(ev.deltaX)) {{ c.scrollLeft += ev.deltaY; ev.preventDefault(); }} }}, {{passive: false}});
c.addEventListener('scroll', sync); addEventListener('resize', sync); sync();
</script>
"""


if __name__ == "__main__":
    rows = load()
    (ROOT / "site").mkdir(exist_ok=True)
    (ROOT / "site" / "index.html").write_text(page(rows))
    print(f"site/index.html: {len(rows)} items")
