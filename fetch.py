"""Pull recent posts for every handle in accounts.txt into xconviction.db.

    python3 fetch.py            # new posts since last run (first run: last 3 days)
    python3 fetch.py --days 3   # force a window
"""
import argparse, json, os, sqlite3, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
API = "https://api.x.com/2"
FIELDS = "created_at,conversation_id,in_reply_to_user_id,referenced_tweets,public_metrics,note_tweet,entities"


def token():
    if os.environ.get("X_BEARER_TOKEN"):
        return os.environ["X_BEARER_TOKEN"]
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("X_BEARER_TOKEN="):
            return line.split("=", 1)[1].strip()


def get(path, params, tok):
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def db(path=ROOT / "xconviction.db"):
    con = sqlite3.connect(path)
    con.execute("""create table if not exists posts(
        id text primary key, handle text, created_at text, text text,
        kind text, conversation_id text, metrics text, raw text)""")
    con.execute("create table if not exists accounts(handle text primary key, name text, avatar blob)")
    # spend log; prices from docs.x.com/x-api/getting-started/pricing and Anthropic's price list (Nov 2026)
    con.execute("create table if not exists usage(at text, item text, units integer, usd real)")
    if "avatar" not in [c[1] for c in con.execute("pragma table_info(accounts)")]:
        con.execute("alter table accounts add column avatar blob")
    return con


X_POST_USD, X_USER_USD = 0.005, 0.010  # per resource read; X bills each resource at most once per UTC day


def log_usage(con, item, units, usd):
    con.execute("insert into usage values(strftime('%Y-%m-%dT%H:%M:%SZ','now'),?,?,?)", (item, units, usd))


def names(con, handles, tok):
    users = get("/users/by", {"usernames": ",".join(handles), "user.fields": "profile_image_url"}, tok).get("data", [])
    log_usage(con, "x_users", len(users), len(users) * X_USER_USD)
    for u in users:
        con.execute("""insert into accounts(handle, name) values(?,?)
            on conflict(handle) do update set name=excluded.name""", (u["username"], u["name"]))
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    u["profile_image_url"], headers={"User-Agent": "xconviction"}), timeout=15) as r:
                con.execute("update accounts set avatar=? where handle=?", (r.read(), u["username"]))
        except (KeyError, OSError) as err:  # keep the last good picture; a missing one isn't worth failing the run
            print(f"@{u['username']}: no avatar update ({err})")
    con.commit()


def kind(t, uid):
    refs = {r["type"] for r in t.get("referenced_tweets", [])}
    if "quoted" in refs:
        return "quote"
    if "replied_to" in refs:
        return "self_thread" if t.get("in_reply_to_user_id") == uid else "reply"
    return "post"


def fetch(con, handle, tok, days):
    # recent search (7-day window) instead of the user timeline: the timeline endpoint
    # returned truncated/empty pages inconsistently; search returned the full set every time
    last = con.execute("select max(id) from posts where handle=?", (handle,)).fetchone()[0]
    params = {"query": f"from:{handle} -is:retweet", "max_results": 100,
              "tweet.fields": FIELDS + ",author_id"}
    if last and days is None:
        params["since_id"] = last
    else:
        start = datetime.now(timezone.utc) - timedelta(days=days or 3)
        params["start_time"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    n = 0
    while True:
        page = get("/tweets/search/recent", params, tok)
        for t in page.get("data", []):
            text = t.get("note_tweet", {}).get("text") or t["text"]
            con.execute("insert or replace into posts values(?,?,?,?,?,?,?,?)", (
                t["id"], handle, t["created_at"], text, kind(t, t["author_id"]),
                t.get("conversation_id"), json.dumps(t.get("public_metrics")), json.dumps(t)))
            n += 1
        nxt = page.get("meta", {}).get("next_token")
        if not nxt:
            break
        params["next_token"] = nxt
        time.sleep(1)
    log_usage(con, "x_posts", n, n * X_POST_USD)
    con.commit()
    print(f"@{handle}: {n} posts")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int)
    args = ap.parse_args()
    con, tok = db(), token()
    handles = [h.lstrip("@") for h in (ROOT / "accounts.txt").read_text().split()]
    names(con, handles, tok)
    for h in handles:
        fetch(con, h, tok, args.days)
