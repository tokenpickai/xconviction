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
    tiles = []
    for asset, rs in by_asset.items():
        # consensus = each account's latest call + every on-chain signal, counted up vs down
        votes = [r for (h, a), r in last.items() if a == asset] + [r for r in rs if r["kind"] == "onchain"]
        up = sum(r["direction"] == "bullish" for r in votes)
        tiles.append((asset, up, len(votes) - up, any(r.get("flip") for r in votes)))
    # strongest consensus first: biggest up/down gap, then most votes
    tiles.sort(key=lambda t: (-abs(t[1] - t[2]), -(t[1] + t[2])))
    cards = []
    for asset, up, down, flip in tiles:
        cards.append(f'<div class="card"><b>{e(asset)}</b>{"<i title=flipped></i>" if flip else ""}'
                     f'<span class="dir bullish">▲{up}</span><span class="dir bearish">▼{down}</span></div>')
    return (f'<div class="strip"><button class="nav prev" aria-label="Scroll left">‹</button>'
            f'<div class="cards">{"".join(cards)}</div>'
            f'<button class="nav next" aria-label="Scroll right">›</button></div>')


def page(rows, last):
    calls = [r for r in reversed(rows) if r["kind"] == "call"]
    chain = [r for r in reversed(rows) if r["kind"] == "onchain"]
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Xconviction</title>
<style>
:root{{--bg:#fff;--fg:#111;--mute:#666;--line:#e5e5e5;--tile:#f4f4f5;--up:#0a8a3a;--down:#c62828;--flip:#d97706;--hi:#6d28d9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#eee;--mute:#999;--line:#2a2a2a;--tile:#1c1c1f;--up:#4cc27a;--down:#ef6b6b;--flip:#f0a940;--hi:#8b5cf6}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,system-ui,sans-serif;max-width:860px;margin:0 auto;padding:16px}}
nav a{{margin-right:16px;color:var(--fg)}} h2{{margin-top:32px;border-bottom:1px solid var(--line);padding-bottom:4px}}
ul{{list-style:none;padding:0}} li{{padding:8px 0;border-bottom:1px solid var(--line)}}
.meta{{color:var(--mute);font-size:12px}} .meta a{{color:var(--mute)}}
.dir.bullish{{color:var(--up)}} .dir.bearish{{color:var(--down)}} .asset{{margin-right:4px}}
.wt{{font-size:11px;border-radius:999px;padding:1px 7px;margin-right:4px;border:1px solid}}
.w-high .wt{{background:var(--hi);border-color:var(--hi);color:#fff}} .w-medium .wt{{color:var(--hi);border-color:var(--hi)}}
.w-low .wt{{color:var(--mute);border-color:var(--line)}}
.flip{{font-size:11px;color:var(--flip);border:1px solid var(--flip);border-radius:999px;padding:1px 7px;margin-right:4px}}
.logo{{display:flex;align-items:center;gap:6px;font-size:26px;font-weight:700;letter-spacing:-.5px;margin:8px 0 20px}} .logo svg{{width:26px;height:26px;fill:var(--fg)}}
.strip{{position:relative}}
.cards{{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;scroll-behavior:smooth}} .cards::-webkit-scrollbar{{display:none}}
.card{{flex:0 0 auto;display:flex;align-items:center;gap:6px;background:var(--tile);border-radius:999px;padding:4px 12px;font-size:13px;white-space:nowrap}}
.card b{{font-weight:700}} .card .dir{{font-weight:600}} .card i{{width:6px;height:6px;border-radius:50%;background:var(--flip)}}
.nav{{position:absolute;top:0;bottom:0;width:48px;border:0;font-size:22px;color:var(--fg);cursor:pointer;opacity:0;transition:opacity .15s}}
.nav.prev{{left:0;background:linear-gradient(to right,var(--bg) 40%,transparent);text-align:left}}
.nav.next{{right:0;background:linear-gradient(to left,var(--bg) 40%,transparent);text-align:right}}
.strip:hover .nav:not([hidden]){{opacity:1}} .nav[hidden]{{display:none}}
</style>
<div class="logo"><svg viewBox="0 0 24 24" aria-label="X"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>conviction</div>
{assets_section(rows, last)}
<h2>Calls ({len(calls)})</h2><ul>{"".join(item(r) for r in calls)}</ul>
<h2>On-chain ({len(chain)})</h2><ul>{"".join(item(r) for r in chain)}</ul>
<script>
const c = document.querySelector('.cards'), prev = document.querySelector('.prev'), next = document.querySelector('.next');
const sync = () => {{ prev.hidden = c.scrollLeft <= 0; next.hidden = c.scrollLeft + c.clientWidth >= c.scrollWidth - 1; }};
prev.onclick = () => c.scrollBy({{left: -c.clientWidth * 0.8}});
next.onclick = () => c.scrollBy({{left: c.clientWidth * 0.8}});
c.parentElement.addEventListener('wheel', ev => {{ if (Math.abs(ev.deltaY) > Math.abs(ev.deltaX)) {{ c.scrollLeft += ev.deltaY; ev.preventDefault(); }} }}, {{passive: false}});
c.addEventListener('scroll', sync); addEventListener('resize', sync); sync();
</script>
"""


if __name__ == "__main__":
    rows, last = load()
    (ROOT / "site").mkdir(exist_ok=True)
    (ROOT / "site" / "index.html").write_text(page(rows, last))
    print(f"site/index.html: {len(rows)} items")
