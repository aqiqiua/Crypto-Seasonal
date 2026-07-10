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
COINS = {                  # name -> (symbol, color, source)   [top-50 by market cap,
    "BTC":  ("BTCUSDT",  "#FB7E14", "binance"),   # excluding stablecoins & wrapped tokens]
    "ETH":  ("ETHUSDT",  "#818CF8", "binance"),
    "BNB":  ("BNBUSDT",  "#A3E635", "binance"),
    "XRP":  ("XRPUSDT",  "#06B6D4", "binance"),
    "SOL":  ("SOLUSDT",  "#A855F7", "binance"),
    "TRX":  ("TRXUSDT",  "#FB7185", "binance"),
    "HYPE": ("HYPE-USDT","#2DE1C2", "okx"),
    "DOGE": ("DOGEUSDT", "#EC4899", "binance"),
    "LEO":  ("LEO-USDT", "#6FCF97", "okx"),
    "ZEC":  ("ZECUSDT",  "#FDE047", "binance"),
    "ADA":  ("ADAUSDT",  "#4F9BFF", "binance"),
    "XLM":  ("XLMUSDT",  "#9AA7C7", "binance"),
    "LINK": ("LINKUSDT", "#3B82F6", "binance"),
    "BCH":  ("BCHUSDT",  "#22C55E", "binance"),
    "GRAM": ("GRAMUSDT", "#EF4444", "bybit"),
    "LTC":  ("LTCUSDT",  "#94A3B8", "binance"),
    "HBAR": ("HBARUSDT", "#14B8A6", "binance"),
    "SUI":  ("SUIUSDT",  "#38BDF8", "binance"),
    "AVAX": ("AVAXUSDT", "#FF5A5F", "binance"),
    "CRO":  ("CRO-USDT", "#2E5BFF", "okx"),
    "SHIB": ("SHIBUSDT", "#FF9F45", "binance"),
    "NEAR": ("NEARUSDT", "#00D9A3", "binance"),
    "UNI":  ("UNIUSDT",  "#FF6FB5", "binance"),
    "TAO":  ("TAOUSDT",  "#34D399", "binance"),
    "WLFI": ("WLFIUSDT", "#D4A373", "binance"),
    "ASTER":("ASTERUSDT","#B794F6", "binance"),
    "OKB":  ("OKB-USDT", "#1FA2FF", "okx"),
    "ONDO": ("ONDOUSDT", "#6C8CFF", "binance"),
    "DOT":  ("DOTUSDT",  "#E6007A", "binance"),
    "HTX":  ("HTXUSDT",  "#7B9CF0", "bybit"),
    "XAU":  ("GC=F",     "#FFD24A", "yahoo"),
    "XAG":  ("SI=F",     "#D6DCE6", "yahoo"),
    "XPT":  ("PL=F",     "#D9DCE1", "yahoo"),
    "DXY":  ("DX-Y.NYB", "#22D3EE", "yahoo"),
    "SPX":  ("^GSPC",    "#CBD5E1", "yahoo"),
    "NDX":  ("^NDX",     "#A78BFA", "yahoo"),
    "WTI":  ("CL=F",     "#F97316", "yahoo"),
    "HG":   ("HG=F",     "#D08B5A", "yahoo"),
}
METAL_FIRST_YEAR = 1970    # metals/macro: use full source history
DEEP_FIRST_YEAR = 2011     # crypto floor once deep sources (Yahoo/Bitstamp) are merged in
CG = {"BTC":"bitcoin","ETH":"ethereum","BNB":"binancecoin","XRP":"ripple","SOL":"solana",
      "TRX":"tron","HYPE":"hyperliquid","DOGE":"dogecoin","LEO":"leo-token","ZEC":"zcash",
      "ADA":"cardano","XLM":"stellar","LINK":"chainlink","BCH":"bitcoin-cash",
      "GRAM":"the-open-network","LTC":"litecoin","HBAR":"hedera-hashgraph","SUI":"sui",
      "AVAX":"avalanche-2","CRO":"crypto-com-chain","SHIB":"shiba-inu","NEAR":"near",
      "UNI":"uniswap","TAO":"bittensor","WLFI":"world-liberty-financial","ASTER":"aster-2",
      "OKB":"okb","ONDO":"ondo-finance","DOT":"polkadot","HTX":"htx-dao"}
BINANCE = "https://data-api.binance.vision/api/v3/klines"
BYBIT   = "https://api.bybit.com/v5/market/kline"
OKX     = "https://www.okx.com/api/v5/market/history-candles"
STOOQ   = "https://stooq.com/q/d/l/"
YF      = "https://query1.finance.yahoo.com/v8/finance/chart/"
BITSTAMP = "https://www.bitstamp.net/api/v2/ohlc/btcusd/"   # BTC/USD daily back to 2012
UA = {"User-Agent": "Mozilla/5.0 (seasonal-index-bot)"}
HRS, DAYS = 365 * 24, 365
MIN_YEARS = 3          # drop assets without at least this many complete years
HALVINGS = [dt.date(2012, 11, 28), dt.date(2016, 7, 9), dt.date(2020, 5, 11), dt.date(2024, 4, 20)]
CYCLE_DAYS = 1461      # ~4 years of days since a halving
CRYPTO = {"binance", "okx", "bybit"}

def ms(d): return int(d.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40)

def fetch_binance(symbol, start, end, interval="1d"):
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
        q = urllib.parse.urlencode({"category": "spot", "symbol": symbol, "interval": "D",
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
        q = urllib.parse.urlencode({"instId": inst, "bar": "1Dutc", "after": after, "limit": 100})
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

def fetch_bitstamp_daily():
    """BTC/USD daily closes since 2012 (for halving-cycle view) -> [[ms, close], ...]"""
    out, cur = [], int(dt.datetime(2012, 1, 1).timestamp())
    end = int((dt.datetime.utcnow() + dt.timedelta(days=1)).timestamp())
    while cur < end:
        q = urllib.parse.urlencode({"step": 86400, "limit": 1000, "start": cur})
        try:
            with get(BITSTAMP + "?" + q) as r: j = json.load(r)
        except Exception as e:
            print("  bitstamp error", e); break
        o = (j.get("data") or {}).get("ohlc") or []
        if not o: break
        for k in o: out.append([int(k["timestamp"]) * 1000, float(k["close"])])
        cur = int(o[-1]["timestamp"]) + 86400
        if len(o) < 1000: break
        time.sleep(0.2)
    return out

def build_cycles(rows):
    """Align BTC by days-since-halving: each cycle's cumulative % path + geo-mean avg."""
    if not rows: return None
    df = pd.DataFrame(rows, columns=["t", "close"]).drop_duplicates("t")
    df["d"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
    s = df.set_index("d")["close"].resample("D").last().ffill()
    s = s[s > 0]
    if s.empty: return None
    today = s.index[-1]
    lst = []
    for i, h in enumerate(HALVINGS):
        h_ts = pd.Timestamp(h)
        seg = s[s.index >= h_ts]
        if seg.empty: continue
        base = float(seg.iloc[0])
        end = pd.Timestamp(HALVINGS[i + 1]) if i + 1 < len(HALVINGS) else today
        current = (i == len(HALVINGS) - 1)
        path = [None] * CYCLE_DAYS
        for dd in range(CYCLE_DAYS):
            date = h_ts + pd.Timedelta(days=dd)
            if date > today or date > end: break
            if date in s.index:
                path[dd] = round(float(s.loc[date] / base - 1) * 100, 2)
        lst.append({"label": str(h.year), "path": path, "current": current})
    complete = [c for c in lst if not c["current"]]
    avg = [None] * CYCLE_DAYS
    for dd in range(CYCLE_DAYS):
        vals = [c["path"][dd] for c in complete if c["path"][dd] is not None and c["path"][dd] > -100]
        if vals:
            avg[dd] = round(float(np.exp(np.mean([np.log(1 + v / 100) for v in vals])) - 1) * 100, 2)
    return {"days": CYCLE_DAYS, "today": int((today - pd.Timestamp(HALVINGS[-1])).days), "list": lst, "avg": avg}

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
            if base > 0:
                cv = np.interp(grid, x, ((g.values / base) - 1) * 100)
                cur = [None if grid[i] > x[-1] else round(float(cv[i]), 3) for i in range(DAYS)]
        elif x[0] <= 0.03 and x[-1] >= 0.97 and base > 0 and (g.values > 0).all():
            cl = np.interp(grid, x, (np.log(g.values) - np.log(base)) * 100)
            years[y] = [round(float(v), 3) for v in cl]
    return years, cur, sorted(years)

def market_cap_order(names):
    """-> (order sorted by live market cap, {coin: market_cap_usd})"""
    cryptos = [n for n in names if n in CG]
    metals = [n for n in names if n not in CG]
    caps = {}
    try:
        ids = ",".join(CG[n] for n in cryptos)
        url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=" + ids + "&per_page=250"
        with get(url) as r: data = json.load(r)
        cap = {d["id"]: (d.get("market_cap") or 0) for d in data}
        caps = {n: cap.get(CG[n], 0) for n in cryptos}
        cryptos.sort(key=lambda n: caps.get(n, 0), reverse=True)
    except Exception as e:
        print("coingecko order failed, keeping default:", e)
    order = cryptos + metals
    print("order:", order); return order, caps

def _first_ms(rows):
    return min((t for t, _ in rows), default=None)

def _last_ms(rows):
    return max((t for t, _ in rows), default=None)

def _daycorr(a, b):
    """log-return correlation of two daily series over overlapping days (0..1); low => different asset."""
    da = {t // 86400000: c for t, c in a if c > 0}
    db = {t // 86400000: c for t, c in b if c > 0}
    common = sorted(set(da) & set(db))
    if len(common) < 61: return 0.0
    ra = np.diff(np.log(np.array([da[d] for d in common])))
    rb = np.diff(np.log(np.array([db[d] for d in common])))
    if ra.std() == 0 or rb.std() == 0: return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])

def exch_daily(sym, src, this_year):
    fetch = {"binance": fetch_binance, "bybit": fetch_bybit, "okx": fetch_okx}.get(src, fetch_binance)
    rows = []
    for y in range(FIRST_YEAR, this_year + 1):
        rows += fetch(sym, dt.datetime(y, 1, 1), dt.datetime(y + 1, 1, 1, 3))
    if not rows and src != "binance":
        fb = sym.replace("-", "")
        for y in range(FIRST_YEAR, this_year + 1):
            rows += fetch_binance(fb, dt.datetime(y, 1, 1), dt.datetime(y + 1, 1, 1, 3))
    return rows

def deepest_crypto(name, sym, src, this_year):
    """Deepest daily source, validated against the exchange anchor by return-correlation (rejects
    same-ticker collisions like Yahoo's old UNI/SUI/HYPE). Returns (rows, source_tag)."""
    anchor = exch_daily(sym, src, this_year)
    best, best_ms, tag = anchor, _first_ms(anchor), src
    anchor_last = _last_ms(anchor)
    cands = [("yahoo", fetch_yahoo(name + "-USD"))]
    if name == "BTC":
        cands.append(("bitstamp", fetch_bitstamp_daily()))
    for cname, cand in cands:
        if not cand: continue
        cms = _first_ms(cand)
        if cms is None or (best_ms is not None and cms >= best_ms): continue          # not deeper
        if anchor_last is not None and (_last_ms(cand) or 0) < anchor_last - 10 * 86400000:
            continue                                                                  # deep source lags live exchange -> keep anchor
        if (anchor and _daycorr(cand, anchor) >= 0.90) or (not anchor and cname == "bitstamp"):
            best, best_ms, tag = cand, cms, cname
    return best, tag

def main():
    now_local = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=TZ)
    this_year = now_local.year
    FETCH = {"binance": fetch_binance, "bybit": fetch_bybit, "okx": fetch_okx}
    out = {}
    for name, (sym, color, src) in COINS.items():
        if src in ("yahoo", "stooq"):
            rows = fetch_yahoo(sym) if src == "yahoo" else fetch_stooq(sym); fy = METAL_FIRST_YEAR
            tag = src
        else:
            rows, tag = deepest_crypto(name, sym, src, this_year); fy = DEEP_FIRST_YEAR
        years, cur, yrs = daily_years(rows, this_year, fy)
        if len(yrs) < MIN_YEARS:
            print(f"{name} [{tag}]: {len(yrs)}-Yr < {MIN_YEARS}, skipped"); continue
        out[name] = {"color": color, "yrs": yrs, "years": {str(y): years[y] for y in yrs},
                     "cur": cur, "daily": src == "stooq", "metal": src in ("yahoo", "stooq")}
        print(f"{name} [{tag}]: {yrs[0]}-{yrs[-1]} ({len(yrs)}-Yr)  rows={len(rows)}")
    order, caps = market_cap_order([n for n in COINS if n in out])
    for name in out:
        out[name]["mcap"] = caps.get(name, 0)
    try:
        cyc = build_cycles(fetch_bitstamp_daily())
        if cyc: out["_cycles"] = cyc; print(f"cycles: {[c['label'] for c in cyc['list']]}")
    except Exception as e:
        print("cycles failed:", e)
    yr0 = dt.datetime(this_year, 1, 1); yr1 = dt.datetime(this_year, 12, 31, 23)
    frac = min(max((now_local - yr0).total_seconds() / (yr1 - yr0).total_seconds(), 0.0), 1.0)
    months = ["","января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"]
    out["_meta"] = {"tz": "UTC+3", "H": HRS, "Dn": DAYS, "coins": order,
                    "today_h": round(frac * (HRS - 1)), "today_d": round(frac * (DAYS - 1)),
                    "asof": f"{now_local.day} {months[now_local.month]} {now_local.year}"}
    json.dump(out, open("data.json", "w"), separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    print("wrote data.json")

if __name__ == "__main__":
    main()
