"""
Seasonal-index updater (FULL YEAR, multi-source, per-year data).
Per coin we ship EACH complete year's daily cumulative-log path (so the page can
re-average over a chosen lookback, measure similarity, and overlay a single year),
the current-year actual % path, plus (for crypto) an hourly averaged seasonal
index for the intraday detail view.
Sources: binance | okx | bybit (crypto, hourly) ; stooq (metals, daily only).
Add an asset: one line in COINS -> (symbol, color, source).
"""
import json, time, csv, io, datetime as dt, urllib.request, urllib.parse
import numpy as np, pandas as pd

TZ = 3
FIRST_YEAR = 2018          # crypto start; metals use all available history
COINS = {                  # name -> (symbol, color, source)
    "BTC":  ("BTCUSDT",  "#FB7E14", "binance"),
    "ETH":  ("ETHUSDT",  "#818CF8", "binance"),
    "BNB":  ("BNBUSDT",  "#A3E635", "binance"),
    "SOL":  ("SOLUSDT",  "#A855F7", "binance"),
    "XRP":  ("XRPUSDT",  "#06B6D4", "binance"),
    "DOGE": ("DOGEUSDT", "#EC4899", "binance"),
    "TRX":  ("TRXUSDT",  "#FB7185", "binance"),
    "LINK": ("LINKUSDT", "#3B82F6", "binance"),
    "BCH":  ("BCHUSDT",  "#22C55E", "binance"),
    "LTC":  ("LTCUSDT",  "#94A3B8", "binance"),
    "GRAM": ("GRAM-USDT","#EF4444", "okx"),
    "ZEC":  ("ZECUSDT",  "#FDE047", "binance"),
    "XAU":  ("GC=F",     "#FFD24A", "yahoo"),
    "XAG":  ("SI=F",     "#D6DCE6", "yahoo"),
}
METAL_FIRST_YEAR = 1990    # don't go absurdly far back for metals
CG = {"BTC":"bitcoin","ETH":"ethereum","BNB":"binancecoin","SOL":"solana","XRP":"ripple",
      "DOGE":"dogecoin","TRX":"tron","LINK":"chainlink","BCH":"bitcoin-cash","LTC":"litecoin",
      "GRAM":"the-open-network","ZEC":"zcash"}
BINANCE = "https://data-api.binance.vision/api/v3/klines"
BYBIT   = "https://api.bybit.com/v5/market/kline"
OKX     = "https://www.okx.com/api/v5/market/history-candles"
STOOQ   = "https://stooq.com/q/d/l/"
YF      = "https://query1.finance.yahoo.com/v8/finance/chart/"
UA = {"User-Agent": "Mozilla/5.0 (seasonal-index-bot)"}
HRS, DAYS = 365 * 24, 365
CRYPTO = {"binance", "okx", "bybit"}

def ms(d): return int(d.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40)

def fetch_binance(symbol, start, end, interval="1h"):
    out, cur, endms = [], ms(start), ms(end)
    while cur < endms:
        q = urllib.parse.urlencode({"symbol": symbol, "interval": interval,
                                    "startTime": cur, "endTime": endms, "limit": 1000})
        try:
            with get(BINANCE + "?" + q) as r: batch = json.load(r)
        except Exception as e:
            print("  binance error", symbol, e); break
        if not batch: break
        out += [[k[0], float(k[4])] for k in batch]
        cur = batch[-1][0] + 1
        if len(batch) < 1000: break
        time.sleep(0.2)
    return out

def fetch_bybit(symbol, start, end):
    out, startms, cur_end = [], ms(start), ms(end)
    while cur_end > startms:
        q = urllib.parse.urlencode({"category": "spot", "symbol": symbol, "interval": "60",
                                    "start": startms, "end": cur_end, "limit": 1000})
        try:
            with get(BYBIT + "?" + q) as r: j = json.load(r)
        except Exception as e:
            print("  bybit error", symbol, e); break
        lst = (j.get("result") or {}).get("list") or []
        if not lst: break
        for k in lst: out.append([int(k[0]), float(k[4])])
        oldest = int(lst[-1][0])
        if oldest <= startms or len(lst) < 1000: break
        cur_end = oldest - 1
        time.sleep(0.2)
    return out

def fetch_okx(inst, start, end):
    out, startms, after = [], ms(start), ms(end)
    while after > startms:
        q = urllib.parse.urlencode({"instId": inst, "bar": "1H", "after": after, "limit": 100})
        try:
            with get(OKX + "?" + q) as r: j = json.load(r)
        except Exception as e:
            print("  okx error", inst, e); break
        if str(j.get("code")) not in ("0", "None"):
            print("  okx code", j.get("code"), j.get("msg")); break
        data = j.get("data") or []
        if not data: break
        for k in data: out.append([int(k[0]), float(k[4])])
        oldest = int(data[-1][0])
        if oldest <= startms or len(data) < 100: break
        after = oldest
        time.sleep(0.15)
    return out

def fetch_stooq(symbol):
    out = []
    try:
        with get(STOOQ + "?" + urllib.parse.urlencode({"s": symbol, "i": "d"})) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception as e:
        print("  stooq error", symbol, e); return out
    rd = csv.reader(io.StringIO(text))
    head = next(rd, None)
    if not head or "Date" not in head[0]:
        print("  stooq bad response", symbol, text[:60]); return out
    for row in rd:
        if len(row) < 5: continue
        try:
            t = ms(dt.datetime.strptime(row[0], "%Y-%m-%d"))
            out.append([t, float(row[4])])
        except Exception:
            continue
    return out

def fetch_yahoo(symbol):
    out = []
    url = YF + urllib.parse.quote(symbol) + "?period1=0&period2=9999999999&interval=1d"
    try:
        with get(url) as r: j = json.load(r)
    except Exception as e:
        print("  yahoo error", symbol, e); return out
    try:
        res = j["chart"]["result"][0]
        ts = res["timestamp"]; cl = res["indicators"]["quote"][0]["close"]
        for t, c in zip(ts, cl):
            if c is None: continue
            out.append([int(t) * 1000, float(c)])
    except Exception as e:
        print("  yahoo parse error", symbol, e)
    return out

def daily_years(rows, this_year, first_year):
    """-> years={Y:[365 cumLog]}, cur=[365 % partial], yrs=[...]"""
    if not rows: return {}, [None]*DAYS, []
    df = pd.DataFrame(rows, columns=["t", "close"]).drop_duplicates("t")
    df["loc"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_localize(None) + pd.Timedelta(hours=TZ)
    df = df.set_index("loc").sort_index()
    grid = np.linspace(0, 1, DAYS)
    years, cur = {}, [None]*DAYS
    for y in sorted(set(df.index.year)):
        if y < first_year: continue
        yr0 = pd.Timestamp(y, 1, 1); yr1 = pd.Timestamp(y, 12, 31, 23)
        span = (yr1 - yr0).total_seconds()
        g = df.loc[(df.index >= yr0) & (df.index <= pd.Timestamp(y, 12, 31, 23, 59)), "close"]
        g = g.resample("D").last().ffill().bfill()
        if g.empty: continue
        base = g.iloc[0]
        x = (g.index - yr0).total_seconds().values / span
        if y == this_year:
            cv = np.interp(grid, x, ((g.values / base) - 1) * 100)
            cur = [None if grid[i] > x[-1] else round(float(cv[i]), 3) for i in range(DAYS)]
        elif x[0] <= 0.03 and x[-1] >= 0.97:
            cl = np.interp(grid, x, (np.log(g.values) - np.log(base)) * 100)
            years[y] = [round(float(v), 3) for v in cl]
    return years, cur, sorted(years)

def build_hourly(rows, this_year):
    """existing averaged hourly seasonal index (max window) for crypto detail view"""
    if not rows: return None, None
    df = pd.DataFrame(rows, columns=["t", "close"]).drop_duplicates("t")
    df["loc"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_localize(None) + pd.Timedelta(hours=TZ)
    df = df.set_index("loc").sort_index()
    grid = np.linspace(0, 1, HRS); paths = {}; cur = None; fmax = -1.0
    for y in sorted(set(df.index.year)):
        yr0 = pd.Timestamp(y, 1, 1); yr1 = pd.Timestamp(y, 12, 31, 23)
        span = (yr1 - yr0).total_seconds()
        g = df.loc[(df.index >= yr0) & (df.index <= pd.Timestamp(y, 12, 31, 23, 59)), "close"]
        g = g.resample("h").last().ffill().bfill()
        if g.empty: continue
        base = g.iloc[0]; x = (g.index - yr0).total_seconds().values / span
        if y == this_year:
            cur = np.interp(grid, x, ((g.values / base) - 1) * 100); fmax = float(x[-1])
        elif x[0] <= 0.03 and x[-1] >= 0.97:
            paths[y] = np.interp(grid, x, (np.log(g.values) - np.log(base)) * 100)
    if not paths: return None, None
    avg = np.mean(np.vstack([paths[y] for y in sorted(paths)]), axis=0)
    lo, hi = float(np.min(avg)), float(np.max(avg))
    idx0 = (avg - lo) / (hi - lo) * 100 if hi > lo else avg * 0
    cv = [None if (cur is None or grid[i] > fmax) else round(float(cur[i]), 2) for i in range(HRS)]
    return [round(float(v), 2) for v in idx0], cv

def market_cap_order(names):
    cryptos = [n for n in names if n in CG]
    metals = [n for n in names if n not in CG]
    try:
        ids = ",".join(CG[n] for n in cryptos)
        url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=" + ids + "&per_page=250"
        with get(url) as r: data = json.load(r)
        cap = {d["id"]: (d.get("market_cap") or 0) for d in data}
        cryptos.sort(key=lambda n: cap.get(CG[n], 0), reverse=True)
    except Exception as e:
        print("coingecko order failed, keeping default:", e)
    order = cryptos + metals
    print("order:", order); return order

STABLES = {"usdt","usdc","dai","fdusd","usde","tusd","usdd","pyusd","busd","gusd","usdp",
           "frax","lusd","susd","usds","usdl","crvusd","eurc","usd1"}
WRAPPED = {"wbtc","weth","wsteth","steth","weeth","wbeth","reth","cbeth","meth","rseth","ezeth",
           "bnsol","jupsol","wbnb","lbtc","solvbtc","cbbtc","tbtc","susde","weeth"}

def _last(cur):
    for i in range(len(cur) - 1, -1, -1):
        if cur[i] is not None: return i
    return -1

def _corr(a, b, lo, hi):
    xs, ys = [], []
    for i in range(lo, hi + 1):
        u, v = a[i], b[i]
        if u is None or v is None: continue
        xs.append(u); ys.append(v)
    n = len(xs)
    if n < 6: return None
    mx = sum(xs) / n; my = sum(ys) / n
    sxy = sxx = syy = 0.0
    for i in range(n):
        dx = xs[i] - mx; dy = ys[i] - my
        sxy += dx * dy; sxx += dx * dx; syy += dy * dy
    if sxx <= 0 or syy <= 0: return None
    return sxy / ((sxx * syy) ** 0.5)

def best_match(years, cur, yrs):
    L = _last(cur)
    if L < 20: return None
    best = None
    for y in yrs:
        r = _corr(cur, years[y], 0, L)
        if r is not None and (best is None or r > best[1]):
            best = (y, r)
    return best

def top_candidates():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=50&page=1"
        with get(url) as r: data = json.load(r)
    except Exception as e:
        print("top50 list failed:", e); return []
    names = []
    for d in data:
        sym = (d.get("symbol") or "").lower(); nm = sym.upper()
        if not sym or sym in STABLES or sym in WRAPPED or nm in COINS: continue
        names.append(nm)
    return names

def scan_top(this_year):
    best = None; cands = top_candidates()
    print(f"scanning {len(cands)} top pairs (daily)...")
    for nm in cands:
        rows = []
        for y in range(FIRST_YEAR, this_year + 1):
            rows += fetch_binance(nm + "USDT", dt.datetime(y, 1, 1), dt.datetime(y + 1, 1, 1, 3), "1d")
        if not rows: continue
        years, cur, yrs = daily_years(rows, this_year, FIRST_YEAR)
        if len(yrs) < 3: continue
        bm = best_match(years, cur, yrs)
        if bm and (best is None or bm[1] > best["score"]):
            best = {"name": nm, "years": years, "cur": cur, "yrs": yrs, "year": bm[0], "score": bm[1]}
    if best:
        print(f"SUGGESTED: {best['name']} ~ {best['year']} ({best['score']*100:.0f}%)")
    else:
        print("SUGGESTED: none")
    return best

def main():
    now_local = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=TZ)
    this_year = now_local.year
    FETCH = {"binance": fetch_binance, "bybit": fetch_bybit, "okx": fetch_okx}
    out = {}
    for name, (sym, color, src) in COINS.items():
        if src in ("yahoo", "stooq"):
            rows = fetch_yahoo(sym) if src == "yahoo" else fetch_stooq(sym); fy = METAL_FIRST_YEAR
        else:
            fetch = FETCH.get(src, fetch_binance); rows = []; fy = FIRST_YEAR
            for y in range(FIRST_YEAR, this_year + 1):
                rows += fetch(sym, dt.datetime(y, 1, 1), dt.datetime(y + 1, 1, 1, 3))
            if not rows:
                fb = sym.replace("-", "")
                print(f"  {name}: {src} empty -> fallback Binance {fb}")
                for y in range(FIRST_YEAR, this_year + 1):
                    rows += fetch_binance(fb, dt.datetime(y, 1, 1), dt.datetime(y + 1, 1, 1, 3))
        years, cur, yrs = daily_years(rows, this_year, fy)
        sea_h = cur_h = None
        if src in CRYPTO and rows:
            sea_h, cur_h = build_hourly(rows, this_year)
        out[name] = {"color": color, "yrs": yrs, "years": {str(y): years[y] for y in yrs},
                     "cur": cur, "sea_h": sea_h, "cur_h": cur_h, "daily": src == "stooq"}
        span = f"{yrs[0]}-{yrs[-1]} ({len(yrs)}-Yr)" if yrs else "n/a"
        print(f"{name} [{src}]: {span}  rows={len(rows)}")
    order = market_cap_order(list(COINS))
    sug = scan_top(this_year); suggested = None
    if sug:
        nm = sug["name"]; suggested = nm
        out[nm] = {"color": "#F0ABFC", "yrs": sug["yrs"],
                   "years": {str(y): sug["years"][y] for y in sug["yrs"]},
                   "cur": sug["cur"], "sea_h": None, "cur_h": None, "daily": True,
                   "suggested": True, "match": {"year": sug["year"], "score": round(sug["score"], 3)}}
        order = order + [nm]
    yr0 = dt.datetime(this_year, 1, 1); yr1 = dt.datetime(this_year, 12, 31, 23)
    frac = min(max((now_local - yr0).total_seconds() / (yr1 - yr0).total_seconds(), 0.0), 1.0)
    months = ["","января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"]
    out["_meta"] = {"tz": "UTC+3", "H": HRS, "Dn": DAYS, "coins": order, "suggested": suggested,
                    "today_h": round(frac * (HRS - 1)), "today_d": round(frac * (DAYS - 1)),
                    "asof": f"{now_local.day} {months[now_local.month]} {now_local.year}"}
    json.dump(out, open("data.json", "w"), separators=(",", ":"), ensure_ascii=False)
    print("wrote data.json")

if __name__ == "__main__":
    main()
