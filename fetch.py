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
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("X_BEARER_TOKEN="):
            return line.split("=", 1)[1].strip()
    return os.environ["X_BEARER_TOKEN"]


def get(path, params, tok):
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def db():
    con = sqlite3.connect(ROOT / "xconviction.db")
    con.execute("""create table if not exists posts(
        id text primary key, handle text, created_at text, text text,
        kind text, conversation_id text, metrics text, raw text)""")
    return con


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
    con.commit()
    print(f"@{handle}: {n} posts")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int)
    args = ap.parse_args()
    con, tok = db(), token()
    for h in (ROOT / "accounts.txt").read_text().split():
        fetch(con, h.lstrip("@"), tok, args.days)
