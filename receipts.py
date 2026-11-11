"""Larp receipts (unbranded): (1) replies to victory laps, made of the author's own earlier posts that contradict them,
(2) miscall charts: the coin's price with a real screenshot of the post pinned where it was made.
Local report only (receipts/index.html); nothing is posted anywhere.

    .venv/bin/python receipts.py
"""
import json, sqlite3
from datetime import datetime, timedelta, timezone
from html import escape as e
from pathlib import Path

import anthropic

import build, extract, fetch

ROOT = Path(__file__).parent
OUT = ROOT / "receipts"
ONCHAIN = {"lookonchain", "OnchainLens", "EmberCN", "ai_9684xtpa"}
MISS = 0.10  # a stance that went 10%+ against its call (vs BTC) is a miss; 10%+ with it is a great call

LAP_PROMPT = """You find "victory laps" in crypto traders' posts: the author claims he was right, called it, warned people,
front-ran a move, or brags about a past prediction. Only his own past calls count. Not quoting others, not questions.
For each post return: post_id, lap (true/false), asset (ticker the claim is about, upper case, or "CRYPTO" for the
whole market, "" if none), claimed (the direction he says he called: "bullish" or "bearish", "" if unclear), quote
(the boastful phrase, max 15 words, verbatim). Every post_id appears once."""
LAP_SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": {"type": "object", "properties": {
    "post_id": {"type": "string"}, "lap": {"type": "boolean"}, "asset": {"type": "string"},
    "claimed": {"type": "string"}, "quote": {"type": "string"}},
    "required": ["post_id", "lap", "asset", "claimed", "quote"], "additionalProperties": False}}},
    "required": ["results"], "additionalProperties": False}


def victory_laps(con, client, week, posts=None):
    posts = posts if posts is not None else con.execute(f"""select id, handle, created_at, text from posts where created_at >= ?
        and handle not in ({",".join("?" * len(ONCHAIN))}) order by created_at""", (week, *ONCHAIN)).fetchall()
    laps = []
    for i in range(0, len(posts), 60):
        chunk = posts[i:i + 60]
        resp = client.messages.create(
            model=extract.MODEL, max_tokens=16000, system=LAP_PROMPT,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": LAP_SCHEMA}},
            messages=[{"role": "user", "content": "\n\n".join(f"[post_id={p[0]} @{p[1]} {p[2]}]\n{p[3]}" for p in chunk)}])
        fetch.log_usage(con, "claude_tokens", resp.usage.input_tokens + resp.usage.output_tokens,
                        resp.usage.input_tokens * extract.IN_USD + resp.usage.output_tokens * extract.OUT_USD)
        meta = {p[0]: p for p in chunk}
        for r in json.loads(next(b.text for b in resp.content if b.type == "text"))["results"]:
            if r["lap"] and r["asset"] and r["claimed"] in ("bullish", "bearish") and r["post_id"] in meta:
                laps.append(dict(zip(("post_id", "handle", "created_at", "text"), meta[r["post_id"]]), **r))
        print(f"{i + len(chunk)}/{len(posts)} posts checked for victory laps", flush=True)
    return laps


def fetch_client():
    return anthropic.Anthropic(api_key=extract.env("ANTHROPIC_API_KEY"))


def outcome(r, price, series, now):
    """How a stance has done so far vs BTC, in the called direction (BTC calls: its own move). None if unpriced."""
    t = build.ts(r["created_at"])
    if not r.get("p0") or not r.get("btc0") or r["asset"] not in price or "BTC" not in price or now.timestamp() - t > 14 * 86400:
        return None
    rel = price[r["asset"]] / r["p0"] - 1 - (0 if r["asset"] == "BTC" else price["BTC"] / r["btc0"] - 1)
    return rel if r["direction"] == "bullish" else -rel


def shoot(pg, pid):
    """A real screenshot of the post via X's public embed (no login), cached per post."""
    out = OUT / "posts" / f"{pid}.png"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        pg.goto(f"https://platform.twitter.com/embed/Tweet.html?id={pid}&theme=dark&dnt=true&hideThread=true&hideCard=true&lang=en",
                wait_until="networkidle", timeout=45000)
        pg.wait_for_timeout(1500)
        (pg.query_selector("article") or pg.query_selector("#app")).screenshot(path=str(out))
    return out.name


def chart_card(r, series, shot, now):
    """1200x675: the coin's price line with the post's screenshot pinned next to the moment and price it was made."""
    W, H, L, R, T, B = 1200, 675, 24, 90, 24, 40
    t0, t1 = series[0][0] / 1000, series[-1][0] / 1000
    lo, hi = min(p for _, p in series), max(p for _, p in series)
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    x = lambda t: L + (t - t0) / (t1 - t0) * (W - L - R)
    y = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    line = " ".join(f"{x(t / 1000):.1f},{y(v):.1f}" for t, v in series)
    ticks = "".join(f'<text x="{W - R + 8}" y="{y(lo + (hi - lo) * i / 4) + 5:.0f}">{build.usd(lo + (hi - lo) * i / 4)}</text>' for i in range(5))
    cx, cy = x(build.ts(r["created_at"])), y(r["p0"])
    last = series[-1][1]
    sw, sh = 400, 300  # screenshot box: long posts are cropped at the bottom so the chart stays readable
    sx = min(max(cx + 40 if cx < W / 2 else cx - 40 - sw, L), W - R - sw)
    # put the box in the half of the chart the price line spends least time in, so it covers as little as possible
    upper = sum(y(v) < (T + H - B) / 2 for _, v in series)
    sy = H - B - sh - 10 if upper > len(series) / 2 else T + 10
    return f"""<!doctype html><meta charset="utf-8"><style>
body{{margin:0;width:{W}px;height:{H}px;background:#000;position:relative;font:13px Inter,-apple-system,sans-serif}}
svg{{position:absolute;inset:0}} text{{fill:#71767b;font-size:14px;paint-order:stroke;stroke:#000;stroke-width:5px}} .post{{position:absolute;left:{sx:.0f}px;top:{sy:.0f}px;width:{sw}px;max-height:{sh}px;overflow:hidden;
border-radius:14px;box-shadow:0 8px 30px rgba(0,0,0,.7)}} .post img{{width:100%;display:block}}</style>
<svg width="{W}" height="{H}"><polyline points="{line}" fill="none" stroke="#e7e9ea" stroke-width="2"/>{ticks}
<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{sx + (0 if sx > cx else sw):.0f}" y2="{sy + (sh - 20 if sy < cy else 20):.0f}" stroke="#ffd400" stroke-width="1.5" stroke-dasharray="4 4"/>
<circle cx="{cx:.1f}" cy="{cy:.1f}" r="7" fill="#ffd400"/><text x="{cx:.1f}" y="{cy + (24 if cy < H / 2 else -14):.1f}" text-anchor="middle"
style="fill:#ffd400;font-weight:700">{build.usd(r["p0"])}</text>
<circle cx="{x(t1):.1f}" cy="{y(last):.1f}" r="5" fill="#e7e9ea"/><text x="{x(t1) - 8:.1f}" y="{y(last) + (24 if y(last) < H / 2 else -12):.1f}"
text-anchor="end" style="fill:#e7e9ea;font-weight:700">{build.usd(last)} now</text></svg><div class="post"><img src="posts/{shot}"></div>"""


def reply_card(shots):
    """A plain reply image: the author's own earlier post(s), side by side, nothing added."""
    imgs = "".join(f'<img src="posts/{s}">' for s in shots)
    return f"""<!doctype html><meta charset="utf-8"><style>body{{margin:0;background:#000;display:flex;gap:16px;padding:16px;width:fit-content}}
img{{width:520px;border-radius:14px}}</style>{imgs}"""


def main():
    now = datetime.now(timezone.utc)
    week = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    con = fetch.db()
    rows = build.load()
    price = dict(con.execute("select symbol, price from coins"))
    series = {a: json.loads(d) for a, d in con.execute("select symbol, data from series")}
    btc0 = dict(((p, a), b) for p, a, b in con.execute("select post_id, asset, btc0 from call_prices"))
    for r in rows:
        r["btc0"] = btc0.get((r["post_id"], r["asset"]))
    heads = build.stances([r for r in rows if r["kind"] == "call" and r["weight"] != "low" and r["asset"] != "CRYPTO"])
    OUT.mkdir(exist_ok=True)
    cache = OUT / "laps.json"  # re-runs within 6h reuse the (paid) victory-lap scan
    if cache.exists() and now.timestamp() - cache.stat().st_mtime < 6 * 3600:
        laps = json.loads(cache.read_text())
    else:
        laps = victory_laps(con, fetch_client(), week)
        cache.write_text(json.dumps(laps))

    replies, misses, seen = [], [], set()
    for lap in sorted(laps, key=lambda l: l["created_at"], reverse=True):
        key = (lap["handle"], lap["asset"])
        # his own earlier posts on the same coin calling the other way, latest first
        earlier = [h for h in heads if h["handle"] == lap["handle"] and h["asset"] == lap["asset"]
                   and h["direction"] != lap["claimed"] and h["created_at"] < lap["created_at"]]
        if earlier and key not in seen:
            seen.add(key)
            replies.append((lap, earlier[-2:]))
    for h in heads:
        o = outcome(h, price, series, now)
        if o is not None and o <= -MISS and h["asset"] in series:
            misses.append((h, o))
    misses.sort(key=lambda m: m[1])

    from playwright.sync_api import sync_playwright
    parts = [f"<h1>Receipts · week to {now:%b %d}</h1><p>Drafts only. Check each source before posting.</p>",
             "<h2>1 · Replies to victory claims</h2>"]
    with sync_playwright() as p:
        b = p.chromium.launch()
        shot_pg = b.new_page(viewport={"width": 550, "height": 1200}, device_scale_factor=2, color_scheme="dark")
        pg = b.new_page(viewport={"width": 1200, "height": 675}, device_scale_factor=2)
        for i, (lap, earlier) in enumerate(replies):
            shots = [shoot(shot_pg, h["post_id"]) for h in earlier]
            f = OUT / f"reply-{i}.html"
            f.write_text(reply_card(shots))
            pg.set_viewport_size({"width": 16 + 536 * len(shots), "height": 675})
            pg.goto(f.as_uri()); pg.wait_for_timeout(300)
            pg.screenshot(path=str(OUT / f"reply-{i}.png"), full_page=True)
            parts.append(f'<section><p><b>@{e(lap["handle"])}</b> claimed: "{e(lap["quote"])}" '
                         f'(<a href="https://x.com/{e(lap["handle"])}/status/{lap["post_id"]}">reply to this post</a>)<br>'
                         f'His earlier {e(lap["asset"])} calls the other way:</p><img src="reply-{i}.png" style="max-width:100%"></section>')
        if not replies:
            parts.append("<p>None this week.</p>")
        parts.append("<h2>2 · Miscall charts</h2>")
        pg.set_viewport_size({"width": 1200, "height": 675})
        for i, (h, o) in enumerate(misses):
            shot = shoot(shot_pg, h["post_id"])
            f = OUT / f"miss-{i}.html"
            f.write_text(chart_card(h, series[h["asset"]], shot, now))
            pg.goto(f.as_uri()); pg.wait_for_timeout(400)
            pg.screenshot(path=str(OUT / f"miss-{i}.png"))
            parts.append(f'<section><p><b>@{e(h["handle"])}</b> {e(h["direction"])} {e(h["asset"])} at {build.usd(h["p0"])}, '
                         f'{o:+.0%} vs BTC since · <a href="https://x.com/{e(h["handle"])}/status/{h["post_id"]}">post</a></p>'
                         f'<img src="miss-{i}.png" style="max-width:100%"></section>')
        b.close()
    (OUT / "index.html").write_text("<!doctype html><meta charset=utf-8><title>Receipts</title>"
                                    "<body style='font:15px Inter,sans-serif;max-width:760px;margin:24px auto'>" + "".join(parts))
    con.commit()
    print(f"{len(replies)} replies, {len(misses)} miscall charts -> receipts/index.html")


if __name__ == "__main__":
    main()
