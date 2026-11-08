"""Turn unprocessed posts into weighted crypto calls and on-chain signals.

    .venv/bin/python extract.py
"""
import json, os, sqlite3
from pathlib import Path

import anthropic

import fetch

ROOT = Path(__file__).parent
MODEL = "claude-opus-5-5"
IN_USD, OUT_USD = 4 / 1e6, 20 / 1e6  # per token for this model; thinking is billed as output
BATCH = 40

SYSTEM = """You read posts by crypto accounts the user trusts and pull out what matters for crypto investing. Report only what the author says. Never add your own view.

CRYPTO ONLY. Ignore equities, commodities, oil, macro indices, politics and social chatter entirely (no item).

Two kinds of item:

1. "call": the author's own directional view on a crypto asset (a token, or the market as a whole as asset "CRYPTO").
   - Only when the view is in the author's own voice. Quotes of others, jokes, questions ("when does X launch?"), comparisons and hypotheticals with no position are NOT calls.
   - A post that names a coin with no direction is not a call.
   - Promotional posts (podcast or video episode blurbs, sponsor reads, event plugs) are NOT calls, even if they describe market views.
   - direction: bullish or bearish.
   - weight:
       high   = strong conviction in his own words: states a position or size, "strongly", "at these levels", "buying", "shorted", a clear thesis with reasons.
       medium = a clear directional view, some reasoning, no stated position; or saying he still holds / hasn't sold a position (bullish).
       low    = casual, hedged or conditional ("if X gets attention, maybe..."), or a short throwaway reply.
   - When unsure between two weights, pick the lower one.

2. "onchain": a reported on-chain or flow event (whale or smart-wallet move, exchange deposit/withdrawal, large leveraged position, liquidation, ETF or fund flow, treasury buy/sell, unlock).
   - direction = the market pressure it implies for the asset: deposit to exchange, selling, opening shorts = bearish; withdrawal from exchange, buying, opening longs, inflows = bullish. Liquidations: bearish for longs liquidated, bullish for shorts liquidated.
   - weight by USD size: high >= $50M, medium >= $5M, low below $5M.
   - A post covering several assets gives one item per asset.
   - Skip moves by hackers, exploiters and scammers (laundering or dumping stolen funds): no item.
   - snapshot: true when the post describes a position or holding that already exists ("holds", "is sitting on", "still has", "unrealized profit", "current position") without a new action. false when it reports an action that just happened (bought, sold, deposited, withdrew, opened, closed, added to, got liquidated, flows today). For "call" items, always false.

asset: the ticker in upper case without "$" (BTC, ETH, SOL, ZEC, HYPE). Use "CRYPTO" for the market as a whole.
summary: one plain sentence a reader can act on, with numbers and the actor when given (e.g. "BlackRock moved 7,244 BTC ($558M) out of its ETF", "Strongly bullish SOL; says the single-chain thesis still holds").

Every input post_id appears exactly once in results; posts with nothing relevant get items: []."""

SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "post_id": {"type": "string"},
            "items": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["call", "onchain"]},
                    "asset": {"type": "string"},
                    "direction": {"type": "string", "enum": ["bullish", "bearish"]},
                    "weight": {"type": "string", "enum": ["high", "medium", "low"]},
                    "summary": {"type": "string"},
                    "snapshot": {"type": "boolean"},
                },
                "required": ["kind", "asset", "direction", "weight", "summary", "snapshot"],
                "additionalProperties": False}},
        },
        "required": ["post_id", "items"],
        "additionalProperties": False}}},
    "required": ["results"],
    "additionalProperties": False,
}


def env(key):
    if os.environ.get(key):
        return os.environ[key]
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()


def db(con=None):
    con = con or sqlite3.connect(ROOT / "xconviction.db")
    con.executescript("""
        create table if not exists calls(post_id text, handle text, created_at text, kind text,
            asset text, direction text, weight text, summary text, snapshot integer default 0);
        create table if not exists extracted(post_id text primary key);
        create table if not exists usage(at text, item text, units integer, usd real);""")
    return con


def run(client, posts):
    lines = [f"[post_id={p[0]} @{p[1]} {p[2]} {p[4]} conversation={p[5]}]\n{p[3]}" for p in posts]
    resp = client.beta.messages.create(
        model=MODEL, max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": "\n\n".join(lines)}],
    )
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"stopped: {resp.stop_reason}")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))["results"], resp.usage


def process(con, client):
    todo = con.execute("""select id, handle, created_at, text, kind, conversation_id from posts
        where id not in (select post_id from extracted) order by handle, created_at""").fetchall()
    meta = {p[0]: p for p in todo}
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        results, usage = run(client, chunk)
        tokens = usage.input_tokens + usage.output_tokens
        fetch.log_usage(con, "claude_tokens", tokens, usage.input_tokens * IN_USD + usage.output_tokens * OUT_USD)
        for r in results:
            p = meta.get(r["post_id"])
            if not p:
                continue
            for it in r["items"]:
                con.execute("""insert into calls(post_id, handle, created_at, kind, asset, direction, weight,
                    summary, snapshot) values(?,?,?,?,?,?,?,?,?)""", (
                    p[0], p[1], p[2], it["kind"], it["asset"].upper().lstrip("$"),
                    it["direction"], it["weight"], it["summary"], int(it["snapshot"])))
            # only posts the model returned are marked done; dropped ones retry next run
            con.execute("insert or ignore into extracted values(?)", (p[0],))
        con.commit()
        print(f"{i + len(chunk)}/{len(todo)} posts")


if __name__ == "__main__":
    process(db(), anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY")))
