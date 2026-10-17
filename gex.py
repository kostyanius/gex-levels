#!/usr/bin/env python3
"""
GEX levels for TradingView.

Same calculation as the GexCore class in the ATAS SMC indicator (Smc.cs) / MT5 / Quantower versions:
options from CBOE (delayed quotes, US) or Deribit (crypto, live) -> Call / Put walls, Gamma Flip,
Max Pain, nearest-expiry (0DTE) levels, 1D Min / Max, extra GEX strikes.

Output (site/): levels.txt - one line per instrument for the mega-script "Levels" field,
                index.html - the same with a copy button, status.json - run status.
Line format: KEY1,KEY2|@=<underlying spot>=<UTC ms the spot refers to, 0 = live>|Name=<price>=0|...
The levels are in the underlying's prices (GLD, DIA, QQQ, SPX, ETH...); the Pine script multiplies
them by (chart close at that moment / spot), exactly like the ATAS version does.

Optional: New Relic custom events (env NR_LICENSE_KEY, NR_REGION=US|EU): "GexRun" per instrument, "GexJob" per run.
"""
import datetime as dt
import html
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

# keys (matched against the TradingView ticker, comma separated), source, underlying, days window, band, include today's expiry
INSTRUMENTS = [
    ("US30,DJ30,DJI,WS30,YM1,MYM", "CBOE", "DIA", 45, 0.06, False),
    ("US100,NAS100,USTEC,NDX,NQ1,MNQ", "CBOE", "QQQ", 45, 0.06, False),
    ("US500,SPX500,SP500,SPX,ES1,MES", "CBOE", "SPX", 45, 0.06, False),
    ("XAUUSD,GOLD,GC1,MGC", "CBOE", "GLD", 45, 0.06, False),
    ("ETH", "DERIBIT", "ETH", 45, 0.12, False),
    ("BTC", "DERIBIT", "BTC", 45, 0.12, False),
]
SHOW_NEAR, SHOW_RANGE, BOX_FILTER, GEX_SIGN, TOP_N = True, True, True, True, 2
TV_BARS = 15   # profile strikes in the line ("#GEX n +=price=length"), same as InpTvBars in GexLevelsEA


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def num(d, name):
    v = d.get(name)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return 0.0
    return 0.0


def bs_gamma(S, K, T, iv):
    if iv <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0
    v = iv * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * iv * iv * T) / v
    return math.exp(-0.5 * d1 * d1) / (math.sqrt(2 * math.pi) * S * v)


def anchor_utc(ext):
    """UTC moment the 15-min delayed US price refers to. ext: ETFs quote 04:00-20:00 ET, indices 09:30-16:00."""
    utc = dt.datetime.now(UTC)
    et = utc.astimezone(ET)
    mins = et.hour * 60 + et.minute + et.second / 60
    open_, close_, close_h = (255, 1215, 20) if ext else (585, 975, 16)
    wd = et.weekday() < 5
    day = et.date()
    if wd and open_ <= mins <= close_:
        c = dt.datetime.combine(day, dt.time(close_h), ET).astimezone(UTC)
        d = utc - dt.timedelta(minutes=15)
        return min(d, c)
    if not (wd and mins > close_):
        day -= dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return (dt.datetime.combine(day, dt.time(close_h), ET) - dt.timedelta(minutes=1)).astimezone(UTC)


class Row:
    __slots__ = ("K", "OI", "G", "IV", "D", "M", "CP", "Ex")

    def __init__(self, K, OI, G, IV, D, CP, Ex, M=0.0):
        self.K, self.OI, self.G, self.IV, self.D, self.CP, self.Ex, self.M = K, OI, G, IV, D, CP, Ex, M


def load(src, und, days, band, today):
    s = {"source": f"{src}:{und}", "spot": 0.0, "live": False, "ext": False, "levels": [], "profile": [], "error": "",
         "net": 0.0, "win_oi": 0.0, "move": 0.0, "anchor_ms": 0}
    rows = []
    yr, base_close, mult, iv30 = 252.0, 0.0, 100.0, 0.0
    if src == "CBOE":
        sym = "_" + und if und in ("SPX", "NDX", "RUT", "VIX") else und
        d = get_json(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json")["data"]
        s["spot"] = num(d, "current_price") or num(d, "close")
        s["ext"] = d.get("security_type") != "index"
        iv30 = num(d, "iv30") / 100.0
        base_close = num(d, "prev_day_close")
        et_now = dt.datetime.now(ET).replace(tzinfo=None)
        p_key, p_cp, p_oi, p_row = "", 0, 0.0, None
        for op in d.get("options", []):
            occ = op.get("option", "")
            L = len(occ)
            if L < 16:
                continue
            tail = occ[L - 15:]
            k = float(tail[7:15]) / 1000.0
            cp = 1 if tail[6] == "C" else -1
            oi = num(op, "open_interest")
            # call and put of one strike+expiry are adjacent: the smaller OI is the "matched" part
            key = occ[:L - 9] + tail[7:15]
            mt = 0.0
            if key == p_key and cp != p_cp:
                mt = min(oi, p_oi)
                if p_row is not None:
                    p_row.M = mt
            p_key, p_cp, p_oi, p_row = key, cp, oi, None
            ex = dt.datetime.strptime(tail[:6], "%y%m%d")
            dd = (ex + dt.timedelta(hours=16) - et_now).total_seconds() / 86400.0   # options stop at 16:00 New York
            if k <= 0 or dd <= 0 or oi <= 0:
                continue
            p_row = Row(k, oi, num(op, "gamma"), num(op, "iv"), max(dd, 0.02), cp, ex, mt)
            rows.append(p_row)
        s["anchor_ms"] = int(anchor_utc(s["ext"]).timestamp() * 1000)
    elif src == "DERIBIT":
        res = get_json(f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={und}&kind=option")["result"]
        months = "JANFEBMARAPRMAYJUNJULAUGSEPOCTNOVDEC"
        now = dt.datetime.now(UTC).replace(tzinfo=None)
        for op in res:
            a = op.get("instrument_name", "").split("-")
            if len(a) != 4:
                continue
            if s["spot"] <= 0:
                s["spot"] = num(op, "estimated_delivery_price") or num(op, "underlying_price")
            n = len(a[1])
            mi = months.find(a[1][n - 5:n - 2])
            if mi < 0:
                continue
            ex = dt.datetime(2000 + int(a[1][n - 2:]), mi // 3 + 1, int(a[1][:n - 5]), 8, 0, 0)
            k, oi = float(a[2]), num(op, "open_interest")
            dd = (ex - now).total_seconds() / 86400.0
            if k <= 0 or dd <= 0 or oi <= 0:
                continue
            rows.append(Row(k, oi, 0.0, num(op, "mark_iv") / 100.0, max(dd, 0.02), 1 if a[3] == "C" else -1, ex))
        s["live"] = True
        yr, mult = 365.0, 1.0
    else:
        s["error"] = "unknown source " + src
        return s
    if s["spot"] <= 0:
        s["error"] = "no underlying price"
        return s
    if not rows:
        s["error"] = "no options with open interest"
        return s
    compute(s, rows, days, band, today, mult, yr, base_close, iv30)
    return s


def compute(s, rows, days, band, today, mult, yr, base_close, iv30):
    spot = s["spot"]
    unit = mult * spot * spot * 0.01
    first = min(rows, key=lambda r: r.Ex)
    first_exp, first_d = first.Ex, first.D
    # index options carry financing trades (boxes, synthetics): matched call+put, left out of the walls away from the money
    box = BOX_FILTER and not s["ext"] and not s["live"]
    cg, pg, c0, p0, pf = {}, {}, {}, {}, {}

    def add(d, k, v):
        d[k] = d.get(k, 0.0) + v

    def win(r):
        return r.D <= days and (today or not (r.Ex == first_exp and first_d < 1.0))

    for r in rows:
        g = r.G if r.G > 0 else bs_gamma(spot, r.K, r.D / 365.0, r.IV)
        e = g * r.OI * unit
        s["net"] += r.CP * e
        near, w = r.Ex == first_exp, win(r)
        if w:
            s["win_oi"] += r.OI
        if abs(r.K / spot - 1) > band or (not w and not near):
            continue
        ew = g * max(r.OI - r.M, 0) * unit if (box and abs(r.K / spot - 1) > 0.01) else e
        add(pf, r.K, r.CP * e)
        add(cg, r.K, 0.0)
        add(pg, r.K, 0.0)
        if w:
            add(cg if r.CP > 0 else pg, r.K, ew)
        if near:
            add(c0 if r.CP > 0 else p0, r.K, ew)

    up = sorted([(k, v) for k, v in cg.items() if k > spot and v > 0], key=lambda x: -x[1])
    dn = sorted([(k, v) for k, v in pg.items() if k < spot and v > 0], key=lambda x: -x[1])
    if not up or not dn:
        s["error"] = "no strikes with gamma on both sides of spot"
        return

    # gamma flip on the whole chain: zero crossing nearest to spot
    flip, prev_t, prev_s = 0.0, 0.0, 0.0
    iv_rows = [r for r in rows if r.IV > 0]
    for gi in range(41):
        S = spot * (0.90 + 0.005 * gi)
        t = 0.0
        for r in iv_rows:
            t += r.CP * r.OI * mult * S * S * 0.01 * bs_gamma(S, r.K, r.D / 365.0, r.IV)
        if gi > 0 and prev_t * t < 0:
            x = prev_s - prev_t * (S - prev_s) / (t - prev_t)
            if flip == 0 or abs(x - spot) < abs(flip - spot):
                flip = x
        prev_t, prev_s = t, S

    def max_pain(sel, strikes):
        sset = [r for r in rows if sel(r)]
        best, mp = float("inf"), 0.0
        for k in sorted(strikes):
            pain = 0.0
            for r in sset:
                pain += r.OI * max(k - r.K if r.CP > 0 else r.K - k, 0.0)
            if pain < best:
                best, mp = pain, k
        return mp

    mp_win = max_pain(win, [k for k in cg if cg[k] + pg[k] > 0])
    mp_near = max_pain(lambda r: r.Ex == first_exp, set(c0) | set(p0))

    L = s["levels"]

    def lv(n, px):
        L.append((n, px))

    lv("Call Wall", up[0][0])
    lv("Put Wall", dn[0][0])
    if len(up) > 1:
        lv(f"Call Wall 2 ({100 * up[1][1] / up[0][1]:.0f}%)", up[1][0])
    if len(dn) > 1:
        lv(f"Put Wall 2 ({100 * dn[1][1] / dn[0][1]:.0f}%)", dn[1][0])
    if flip > 0:
        lv("Gamma Flip", flip)
    if mp_win > 0:
        lv("Max Pain", mp_win)
    if SHOW_NEAR and first_d <= days:
        tag = "0DTE" if first_d < 1.0 else "Next exp"
        nu = max([(k, v) for k, v in c0.items() if k > spot and v > 0], key=lambda x: x[1], default=None)
        nd = max([(k, v) for k, v in p0.items() if k < spot and v > 0], key=lambda x: x[1], default=None)
        if nu and nu[0] != up[0][0]:
            lv(tag + " Call", nu[0])
        if nd and nd[0] != dn[0][0]:
            lv(tag + " Put", nd[0])
        if mp_near > 0 and mp_near != mp_win:
            lv(tag + " Pain", mp_near)
    if SHOW_RANGE:
        iv = iv30
        if iv <= 0:
            cand = [r for r in rows if r.IV > 0 and r.D >= 1.0]
            if cand:
                iv = min(cand, key=lambda r: abs(r.K / spot - 1) * 100 + r.D / 30.0).IV
        if iv > 0:
            m = iv * math.sqrt(1.0 / yr)
            b = base_close if base_close > 0 else spot
            s["move"] = m
            lv("1D Max", b * (1 + m))
            lv("1D Min", b * (1 - m))
    # GEX ranks: 1 = largest |net gamma| in the profile
    ranked = sorted(pf.items(), key=lambda x: -abs(x[1]))
    used = {up[0][0], dn[0][0]}
    if len(up) > 1:
        used.add(up[1][0])
    if len(dn) > 1:
        used.add(dn[1][0])
    n = 0
    for rank, (k, v) in enumerate(ranked, start=1):
        if n >= TOP_N:
            break
        if k in used or v == 0:
            continue
        lv(f"GEX {rank}" + ((" +" if v > 0 else " -") if GEX_SIGN else ""), k)
        n += 1
    # profile: (strike, net gamma, rank), sorted by strike
    rank_of = {k: i for i, (k, _) in enumerate(ranked, start=1)}
    s["profile"] = sorted((k, v, rank_of[k]) for k, v in pf.items())


def fmt(x):
    return f"{x:.2f}".rstrip("0").rstrip(".")


def line_for(keys, s):
    """Same string as GexLevelsEA writes (levels merged by price, then the profile bars),
    except the prices stay in the underlying's scale and "@=spot=time" tells the Pine side how to scale them."""
    parts = [keys, f"@={fmt(s['spot'])}={0 if s['live'] else s['anchor_ms']}"]
    merged = []                                    # levels sorted by price; same price -> one merged name
    for n, px in sorted(s["levels"], key=lambda x: x[1]):
        if merged and abs(merged[-1][1] - px) < 1e-9:
            merged[-1][0] += " / " + n
        else:
            merged.append([n, px])
    parts += [f"{n}={fmt(px)}=0" for n, px in merged]
    pmx = max((abs(v) for _, v, _ in s.get("profile", [])), default=0)
    if pmx > 0:
        for k, v, r in s["profile"]:
            if r <= TV_BARS:
                parts.append(f"#GEX {r} {'+' if v >= 0 else '-'}={fmt(k)}={max(round(100 * math.sqrt(abs(v) / pmx)), 3)}")
    return "|".join(parts)


def send_new_relic(events):
    """New Relic Event API with a License (Ingest) key; never breaks the job."""
    key = os.environ.get("NR_LICENSE_KEY", "").strip()
    if not key:
        print("New Relic: skipped, secret NR_LICENSE_KEY is not set")
        return
    # EU-region license keys start with "eu01"; NR_REGION (EU / US) overrides the guess
    region = os.environ.get("NR_REGION", "").strip().upper() or ("EU" if key.lower().startswith("eu") else "US")
    host = "insights-collector.eu01.nr-data.net" if region == "EU" else "insights-collector.newrelic.com"
    print(f"New Relic: sending {len(events)} events to {region} ({host}), key {key[:4]}...{key[-4:]}")
    req = urllib.request.Request(f"https://{host}/v1/accounts/events", data=json.dumps(events).encode(),
                                 headers={"Content-Type": "application/json", "Api-Key": key}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("New Relic:", r.status, r.read().decode(errors="replace")[:200])
    except urllib.error.HTTPError as e:
        print("New Relic error:", e.code, e.read().decode(errors="replace")[:300])
    except Exception as e:  # monitoring must never break the job
        print("New Relic error:", e)


def write_site(lines, status, out="site"):
    os.makedirs(out, exist_ok=True)
    txt = "\n".join(lines)
    with open(os.path.join(out, "levels.txt"), "w") as f:
        f.write(txt + "\n")
    with open(os.path.join(out, "status.json"), "w") as f:
        json.dump(status, f, indent=1)
    rows = "".join(
        f"<tr><td>{html.escape(i['keys'].split(',')[0])}</td><td>{html.escape(i['source'])}</td>"
        f"<td>{fmt(i['spot']) if i['spot'] else '-'}</td><td>{i['levels']}</td>"
        f"<td class='{'err' if i['error'] else 'ok'}'>{html.escape(i['error'] or 'ok')}</td></tr>"
        for i in status["instruments"])
    page = f"""<!doctype html><html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>GEX levels</title>
<style>
:root{{--bg:#11161d;--fg:#e3e8ee;--mut:#93a0ae;--pan:#171e27;--ln:#2a3441;--ac:#2962ff;--ok:#3fbf86;--er:#ef5350}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px system-ui,sans-serif}}
.w{{max-width:900px;margin:0 auto;padding:16px;display:grid;gap:12px}}
h1{{font-size:1.2rem;margin:0}} .m{{color:var(--mut);font-size:.85rem}}
button{{font:600 1rem system-ui;background:var(--ac);color:#fff;border:0;border-radius:8px;padding:12px 20px;cursor:pointer}}
pre{{background:var(--pan);border:1px solid var(--ln);border-radius:8px;padding:12px;white-space:pre-wrap;word-break:break-all;font-size:12px;margin:0}}
table{{border-collapse:collapse;font-size:.85rem}} td,th{{border-bottom:1px solid var(--ln);padding:4px 10px;text-align:left}}
.ok{{color:var(--ok)}} .err{{color:var(--er)}}
</style></head><body><div class="w">
<h1>GEX levels for TradingView</h1>
<p class="m">Updated {status['updated_utc']} UTC. Paste everything into the mega-script: GEX Levels → "Levels".</p>
<div><button id="b">Copy all</button> <span id="s" class="m"></span></div>
<pre id="t">{html.escape(txt)}</pre>
<table><tr><th>Key</th><th>Source</th><th>Spot</th><th>Levels</th><th>Status</th></tr>{rows}</table>
</div><script>
document.getElementById('b').onclick=async()=>{{const t=document.getElementById('t').textContent,s=document.getElementById('s');
try{{await navigator.clipboard.writeText(t);s.textContent='copied';}}catch(e){{const r=document.createRange();r.selectNodeContents(document.getElementById('t'));
const x=getSelection();x.removeAllRanges();x.addRange(r);document.execCommand('copy');s.textContent='copied';}}}};
</script></body></html>"""
    with open(os.path.join(out, "index.html"), "w") as f:
        f.write(page)


def main():
    t0 = time.time()
    lines, inst, events = [], [], []
    for keys, src, und, days, band, today in INSTRUMENTS:
        t1 = time.time()
        try:
            s = load(src, und, days, band, today)
        except Exception as e:
            s = {"source": f"{src}:{und}", "spot": 0.0, "levels": [], "error": f"{type(e).__name__}: {e}"}
        ok = not s["error"]
        if ok:
            lines.append(line_for(keys, s))
        print(f"{src}:{und:4} spot={s['spot']:.2f} levels={len(s['levels'])} {s['error'] or 'ok'}")
        inst.append({"keys": keys, "source": s["source"], "spot": s["spot"], "levels": len(s["levels"]), "error": s["error"]})
        events.append({"eventType": "GexRun", "instrument": und, "source": src, "ok": 1 if ok else 0,
                       "status": "ok" if ok else "error", "error": s["error"][:250],
                       "levels": len(s["levels"]), "spot": s["spot"], "durationSec": round(time.time() - t1, 2)})
    status = {"updated_utc": dt.datetime.now(UTC).strftime("%Y-%m-%d %H:%M"), "duration_sec": round(time.time() - t0, 1),
              "instruments": inst}
    write_site(lines, status)
    run = os.environ.get("GITHUB_RUN_ID", "")
    events.append({"eventType": "GexJob", "ok": 1 if lines else 0, "instrumentsOk": len(lines),
                   "instrumentsTotal": len(INSTRUMENTS), "durationSec": status["duration_sec"],
                   "trigger": os.environ.get("GITHUB_EVENT_NAME", "local"), "runId": run,
                   "runUrl": f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{run}" if run else ""})
    for e in events:
        e["job"] = "gex-levels"
    send_new_relic(events)
    # the job fails only when nothing at all could be calculated
    return 0 if lines else 1


if __name__ == "__main__":
    sys.exit(main())
