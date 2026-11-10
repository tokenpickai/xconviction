"""Prices from CoinGecko's free API (top 500 coins by market cap): the latest price per coin, and per call
the coin's price at the call (price), BTC at the call (btc0), and both 7 days later (p7, b7) for win rates.

    python3 prices.py
"""
import json, sqlite3, time, urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
API = "https://api.coingecko.com/api/v3"
HORIZON = 7 * 86400  # win rates judge a call 7 days after it was made


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


def schema(con):
    con.executescript("""
        create table if not exists coins(symbol text primary key, id text, price real, updated_at text);
        create table if not exists call_prices(post_id text, asset text, price real, primary key(post_id, asset));""")
    cols = [c[1] for c in con.execute("pragma table_info(call_prices)")]
    for col in ("btc0", "p7", "b7"):
        if col not in cols:
            con.execute(f"alter table call_prices add column {col} real")
    if "volume" not in [c[1] for c in con.execute("pragma table_info(coins)")]:
        con.execute("alter table coins add column volume real")  # 24h USD volume, to size on-chain moves
    con.execute("create table if not exists series(symbol text primary key, data text, updated_at real)")


def nearest(series, t):
    return min(series, key=lambda p: abs(p[0] / 1000 - t))[1]


def fill(con, coins, now):
    """Fill price/btc0 for new calls and p7/b7 for calls now 7+ days old. coins = {symbol: (id, price, updated)}."""
    schema(con)
    rows = con.execute("""select distinct c.post_id, c.asset, c.created_at, p.price, p.btc0, p.p7, p.b7 from calls c
        left join call_prices p on p.post_id = c.post_id and p.asset = c.asset""").fetchall()
    need = [(pid, a, ts(at), p, b0, p7, b7) for pid, a, at, p, b0, p7, b7 in rows
            if a in coins and (p is None or b0 is None or (p7 is None and now - ts(at) >= HORIZON))]
    if not need or "BTC" not in coins:
        return 0
    # ponytail: hourly points (CoinGecko granularity for 1-90 day ranges), so prices are within ~30 min of the moment
    rng = lambda cid, start: get(f"/coins/{cid}/market_chart/range?vs_currency=usd&from={int(start)}&to={int(now)}")["prices"]
    btc = rng(coins["BTC"][0], min(x[2] for x in need) - 3600)
    series = {}
    for a in {x[1] for x in need}:
        if a not in series:
            series[a] = btc if a == "BTC" else rng(coins[a][0], min(x[2] for x in need if x[1] == a) - 3600)
            time.sleep(3)
    for pid, a, t, p, b0, p7, b7 in need:
        aged = now - t >= HORIZON
        con.execute("insert or replace into call_prices(post_id, asset, price, btc0, p7, b7) values(?,?,?,?,?,?)", (
            pid, a, p if p is not None else nearest(series[a], t), b0 if b0 is not None else nearest(btc, t),
            p7 if p7 is not None else (nearest(series[a], t + HORIZON) if aged else None),
            b7 if b7 is not None else (nearest(btc, t + HORIZON) if aged else None)))
    con.commit()
    return len(need)


MAJORS = ("BTC", "ETH", "SOL", "BNB", "XRP")


def history(con, coins, now, max_age=6 * 3600):
    """30-day hourly prices for the majors, for 2-week trends and the market call. Refreshed every 6 hours."""
    con.execute("create table if not exists history(symbol text primary key, data text, updated_at real)")
    have = dict(con.execute("select symbol, updated_at from history"))
    n = 0
    for a in MAJORS:
        if a in coins and now - (have.get(a) or 0) >= max_age:
            data = get(f"/coins/{coins[a][0]}/market_chart?vs_currency=usd&days=30")["prices"]
            con.execute("insert or replace into history values(?,?,?)", (a, json.dumps(data), now))
            n += 1
            time.sleep(3)
    con.commit()
    return n


def charts(con, coins, now, max_age=6 * 3600, top=25):
    """7-day hourly price series for the 25 most-discussed coins of the week, refreshed when 6+ hours old."""
    # ponytail: CoinGecko's free tier throttles bursts hard (36 charts took 11 min locally), so cap the count and refresh rarely
    week = datetime.fromtimestamp(now - 7 * 86400).strftime("%Y-%m-%dT%H:%M:%S")
    have = dict(con.execute("select symbol, updated_at from series"))
    n = 0
    for (a,) in con.execute("""select asset from calls where created_at >= ? and asset != 'CRYPTO'
            group by asset order by count(*) desc limit ?""", (week, top)).fetchall():
        if a in coins and now - (have.get(a) or 0) >= max_age:
            data = get(f"/coins/{coins[a][0]}/market_chart?vs_currency=usd&days=7")["prices"]
            con.execute("insert or replace into series values(?,?,?)", (a, json.dumps(data), now))
            n += 1
            time.sleep(3)
    con.commit()
    return n


if __name__ == "__main__":
    con = sqlite3.connect(ROOT / "xconviction.db")
    schema(con)
    # symbol -> id by market cap rank; first (largest) coin wins when symbols collide
    coins, vol = {}, {}
    for page in (1, 2):
        for c in get(f"/coins/markets?vs_currency=usd&per_page=250&page={page}"):
            if c["symbol"].upper() not in coins:
                coins[c["symbol"].upper()] = (c["id"], c["current_price"], c["last_updated"])
                vol[c["symbol"].upper()] = c.get("total_volume")
    con.execute("delete from coins")
    con.executemany("insert into coins(symbol, id, price, updated_at, volume) values(?,?,?,?,?)",
                    [(s, *v, vol[s]) for s, v in coins.items()])
    con.commit()
    print(f"{len(coins)} coins, {fill(con, coins, time.time())} call price rows filled, {charts(con, coins, time.time())} charts and {history(con, coins, time.time())} histories refreshed")
