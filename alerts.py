"""Live larp alerts to Telegram, every pipeline run:
  - a victory lap in a post fetched since the last run, with the author's own earlier calls the other way (reply image)
  - a call that has just crossed -10% vs BTC (miss chart with the post pinned at its price)
Sent once each (alerts_sent). Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID; without them it does nothing.

    .venv/bin/python alerts.py          # live
    .venv/bin/python alerts.py --test   # one test message
"""
import json, os, sys, urllib.request, uuid
from datetime import datetime, timedelta, timezone
from html import escape as e

import build, extract, fetch, receipts

TOKEN, CHAT = os.environ.get("TELEGRAM_BOT_TOKEN") or extract.env("TELEGRAM_BOT_TOKEN"), \
    os.environ.get("TELEGRAM_CHAT_ID") or extract.env("TELEGRAM_CHAT_ID")


def send(caption, png=None):
    """Telegram sendPhoto (or sendMessage without an image), HTML caption."""
    if not png:
        data = json.dumps({"chat_id": CHAT, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data, {"Content-Type": "application/json"})
    else:
        b = uuid.uuid4().hex
        part = lambda k, v: f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        body = (part("chat_id", CHAT) + part("caption", caption[:1024]) + part("parse_mode", "HTML")
                + f'--{b}\r\nContent-Disposition: form-data; name="photo"; filename="card.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
                + png.read_bytes() + f"\r\n--{b}--\r\n".encode())
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendPhoto", body,
                                     {"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["ok"]


def main():
    if not (TOKEN and CHAT):
        print("alerts: no Telegram credentials, skipping")
        return
    if "--test" in sys.argv:
        print("test sent:", send("✅ Larp alerts are connected. You'll get victory-lap replies and miscall charts here."))
        return
    now = datetime.now(timezone.utc)
    con = fetch.db()
    con.executescript("""create table if not exists alerts_sent(key text primary key, at text);
        create table if not exists lap_scanned(post_id text primary key);""")
    first_run = not con.execute("select 1 from alerts_sent limit 1").fetchone()
    rows = build.load()
    price = dict(con.execute("select symbol, price from coins"))
    series = {a: json.loads(d) for a, d in con.execute("select symbol, data from series")}
    btc0 = dict(((p, a), b) for p, a, b in con.execute("select post_id, asset, btc0 from call_prices"))
    for r in rows:
        r["btc0"] = btc0.get((r["post_id"], r["asset"]))
    heads = build.stances([r for r in rows if r["kind"] == "call" and r["weight"] != "low" and r["asset"] != "CRYPTO"])
    sent = {k for (k,) in con.execute("select key from alerts_sent")}
    out = []  # (key, kind, caption, render)

    # victory laps among KOL posts from the last 12h that haven't been scanned yet
    cut = (now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S")
    fresh = con.execute(f"""select id, handle, created_at, text from posts where created_at >= ?
        and handle not in ({",".join("?" * len(receipts.ONCHAIN))}) and id not in (select post_id from lap_scanned)""",
        (cut, *receipts.ONCHAIN)).fetchall()
    laps = receipts.victory_laps(con, receipts.fetch_client(), cut, fresh) if fresh else []
    con.executemany("insert or ignore into lap_scanned values(?)", [(p[0],) for p in fresh])
    for lap in laps:
        earlier = [h for h in heads if h["handle"] == lap["handle"] and h["asset"] == lap["asset"]
                   and h["direction"] != lap["claimed"] and h["created_at"] < lap["created_at"]]
        if earlier and f"lap:{lap['post_id']}" not in sent:
            cap = (f'🧾 <b>@{e(lap["handle"])}</b> just claimed: "{e(lap["quote"])}" ({lap["claimed"]} {e(lap["asset"])})\n'
                   f'His earlier {e(lap["asset"])} calls the other way are in the image.\n'
                   f'Reply here: https://x.com/{e(lap["handle"])}/status/{lap["post_id"]}')
            out.append((f"lap:{lap['post_id']}", "reply", cap, earlier[-2:]))

    # stances that are now 10%+ against their call vs BTC
    for h in heads:
        o = receipts.outcome(h, price, series, now)
        if o is not None and o <= -receipts.MISS and h["asset"] in series and f"miss:{h['post_id']}:{h['asset']}" not in sent:
            cap = (f'📉 <b>@{e(h["handle"])}</b> was {h["direction"]} {e(h["asset"])} at {build.usd(h["p0"])} '
                   f'({build.local_time(h["created_at"])}). Now {build.usd(price[h["asset"]])}, {o:+.0%} vs BTC.\n'
                   f'Post: https://x.com/{e(h["handle"])}/status/{h["post_id"]}')
            out.append((f"miss:{h['post_id']}:{h['asset']}", "miss", cap, h))

    if first_run:  # don't flood on day one: today's backlog is marked as seen; only new events alert from here on
        con.executemany("insert or ignore into alerts_sent values(?,?)", [(k, now.isoformat()) for k, *_ in out])
        con.commit()
        send(f"✅ Larp alerts are live. {len(out)} existing items marked as seen; new ones will arrive here.")
        return

    if out:
        from playwright.sync_api import sync_playwright
        receipts.OUT.mkdir(exist_ok=True)
        with sync_playwright() as p:
            b = p.chromium.launch(**({"channel": "chrome"} if os.environ.get("CI") else {}))
            shot_pg = b.new_page(viewport={"width": 550, "height": 1200}, device_scale_factor=2, color_scheme="dark")
            pg = b.new_page(viewport={"width": 1200, "height": 675}, device_scale_factor=2)
            for key, kind, cap, x in out:
                f = receipts.OUT / f"alert-{key.replace(':', '-')}.html"
                if kind == "reply":
                    shots = [receipts.shoot(shot_pg, h["post_id"]) for h in x]
                    f.write_text(receipts.reply_card(shots))
                    pg.set_viewport_size({"width": 16 + 536 * len(shots), "height": 675})
                else:
                    f.write_text(receipts.chart_card(x, series[x["asset"]], receipts.shoot(shot_pg, x["post_id"]), now))
                    pg.set_viewport_size({"width": 1200, "height": 675})
                pg.goto(f.as_uri()); pg.wait_for_timeout(400)
                png = f.with_suffix(".png")
                pg.screenshot(path=str(png), full_page=kind == "reply")
                if send(cap, png):
                    con.execute("insert or ignore into alerts_sent values(?,?)", (key, now.isoformat()))
            b.close()
    con.commit()
    print(f"alerts: {len(out)} sent")


if __name__ == "__main__":
    main()
