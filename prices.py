"""Price at each call and latest price, from CoinGecko's free API (top 500 coins by market cap).

    python3 prices.py
"""
import json, sqlite3, time, urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
API = "https://api.coingecko.com/api/v3"


def get(path):
    req = urllib.request.Request(API + path, headers={"accept": "application/json"})
    for wait in (0, 20, 60):  # free tier rate-limits bursts with 429
        time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
    raise RuntimeError(f"rate limited: {path}")


def ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


if __name__ == "__main__":
    con = sqlite3.connect(ROOT / "xconviction.db")
    con.executescript("""
        create table if not exists coins(symbol text primary key, id text, price real, updated_at text);
        create table if not exists call_prices(post_id text, asset text, price real, primary key(post_id, asset));""")

    # symbol -> id by market cap rank; first (largest) coin wins when symbols collide
    coins = {}
    for page in (1, 2):
        for c in get(f"/coins/markets?vs_currency=usd&per_page=250&page={page}"):
            coins.setdefault(c["symbol"].upper(), (c["id"], c["current_price"], c["last_updated"]))
    con.execute("delete from coins")
    con.executemany("insert into coins values(?,?,?,?)", [(s, *v) for s, v in coins.items()])

    todo = con.execute("""select distinct c.post_id, c.asset, c.created_at from calls c
        left join call_prices p on p.post_id = c.post_id and p.asset = c.asset
        where p.post_id is null""").fetchall()
    by_asset = {}
    for post_id, asset, created in todo:
        if asset in coins:
            by_asset.setdefault(asset, []).append((post_id, ts(created)))
    now = time.time()
    for asset, calls in by_asset.items():
        start = min(t for _, t in calls) - 3600
        # ponytail: hourly points (CoinGecko granularity for 1-90 day ranges), so "price at call" is within ~30 min
        series = get(f"/coins/{coins[asset][0]}/market_chart/range?vs_currency=usd&from={int(start)}&to={int(now)}")["prices"]
        for post_id, t in calls:
            price = min(series, key=lambda p: abs(p[0] / 1000 - t))[1]
            con.execute("insert or replace into call_prices values(?,?,?)", (post_id, asset, price))
        time.sleep(3)
    con.commit()
    print(f"{len(coins)} coins, {sum(len(v) for v in by_asset.values())} call prices filled, "
          f"{len(todo) - sum(len(v) for v in by_asset.values())} without a top-500 match")
