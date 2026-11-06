"""Render xconviction.db into site/index.html (no network, no LLM).

    python3 build.py && open site/index.html
"""
import html, sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
W = {"high": 3, "medium": 2, "low": 1}


def load():
    con = sqlite3.connect(ROOT / "xconviction.db")
    cols = "post_id handle created_at kind asset direction weight summary".split()
    rows = [dict(zip(cols, r)) for r in con.execute(f"select {','.join(cols)} from calls order by created_at")]
    # a flip = an account's call on an asset pointing the other way from its previous call on that asset
    last = {}
    for r in rows:
        if r["kind"] != "call":
            continue
        key = (r["handle"], r["asset"])
        prev = last.get(key)
        r["flip"] = prev is not None and prev["direction"] != r["direction"]
        last[key] = r
    names = dict(con.execute("select handle, name from accounts"))
    for r in rows:
        r["name"] = names.get(r["handle"], "")
    return rows, last


def e(s):
    return html.escape(str(s))


def item(r, show_asset=True):
    arrow = "▲" if r["direction"] == "bullish" else "▼"
    flip = '<span class="flip">flipped</span>' if r.get("flip") else ""
    asset = f'<b class="asset">{e(r["asset"])}</b>' if show_asset else ""
    return (f'<li class="w-{r["weight"]}"><span class="dir {r["direction"]}">{arrow}</span> {asset} '
            f'<span class="wt">{r["weight"]}</span>{flip} {e(r["summary"])}'
            f'<div class="meta">{e(r["name"])} @{e(r["handle"])} ·{r["created_at"][5:16].replace("T", " ")} UTC · '
            f'<a href="https://x.com/{e(r["handle"])}/status/{r["post_id"]}">post</a></div></li>')


def assets_section(rows, last):
    """One card per asset with an up/down consensus count."""
    by_asset = defaultdict(list)
    for r in rows:
        by_asset[r["asset"]].append(r)
    ranked = sorted(by_asset.items(), key=lambda kv: -sum(W[r["weight"]] for r in kv[1]))
    cards = []
    for asset, rs in ranked:
        # consensus = each account's latest call + every on-chain signal, counted up vs down
        votes = [r for (h, a), r in last.items() if a == asset] + [r for r in rs if r["kind"] == "onchain"]
        up = sum(r["direction"] == "bullish" for r in votes)
        flip = '<span class="flip">flip</span>' if any(r.get("flip") for r in votes) else ""
        cards.append(f'<div class="card"><b>{e(asset)}</b> <span class="dir bullish">▲{up}</span> '
                     f'<span class="dir bearish">▼{len(votes) - up}</span>{flip}</div>')
    return f'<div class="cards">{"".join(cards)}</div>'


def page(rows, last):
    calls = [r for r in reversed(rows) if r["kind"] == "call"]
    chain = [r for r in reversed(rows) if r["kind"] == "onchain"]
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Xconviction</title>
<style>
:root{{--bg:#fff;--fg:#111;--mute:#666;--line:#e5e5e5;--up:#0a8a3a;--down:#c62828;--flip:#b26a00}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#eee;--mute:#999;--line:#2a2a2a;--up:#4cc27a;--down:#ef6b6b;--flip:#f0a940}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,system-ui,sans-serif;max-width:860px;margin:0 auto;padding:16px}}
nav a{{margin-right:16px;color:var(--fg)}} h2{{margin-top:32px;border-bottom:1px solid var(--line);padding-bottom:4px}}
ul{{list-style:none;padding:0}} li{{padding:8px 0;border-bottom:1px solid var(--line)}} li.w-low{{opacity:.7}} li.w-high{{font-weight:600}}
.meta{{color:var(--mute);font-size:12px;font-weight:400}} .meta a{{color:var(--mute)}}
.dir.bullish{{color:var(--up)}} .dir.bearish{{color:var(--down)}} .asset{{margin-right:4px}}
.wt{{font-size:11px;color:var(--mute);border:1px solid var(--line);border-radius:4px;padding:0 4px;margin-right:4px;font-weight:400}}
.flip{{font-size:11px;color:var(--flip);border:1px solid var(--flip);border-radius:4px;padding:0 4px;margin-right:4px}}
.cards{{display:flex;flex-wrap:wrap;gap:8px}}
.card{{border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:14px;white-space:nowrap}}
.card b{{margin-right:4px}} .card .dir{{margin-left:4px;font-weight:600}} .card .flip{{margin:0 0 0 6px}}
</style>
<h1>Xconviction</h1>
{assets_section(rows, last)}
<h2>Calls ({len(calls)})</h2><ul>{"".join(item(r) for r in calls)}</ul>
<h2>On-chain ({len(chain)})</h2><ul>{"".join(item(r) for r in chain)}</ul>
"""


if __name__ == "__main__":
    rows, last = load()
    (ROOT / "site").mkdir(exist_ok=True)
    (ROOT / "site" / "index.html").write_text(page(rows, last))
    print(f"site/index.html: {len(rows)} items")
