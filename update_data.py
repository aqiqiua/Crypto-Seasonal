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
METAL_FIRST_YEAR = 1990    # don't go absurdly far back for metals
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
UA = {"User-Agent": "Mozilla/5.0 (seasonal-index-bot)"}
HRS, DAYS = 365 * 24, 365
MIN_YEARS = 3          # drop assets without at least this many complete years
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
            if base > 0:
                cv = np.interp(grid, x, ((g.values / base) - 1) * 100)
                cur = [None if grid[i] > x[-1] else round(float(cv[i]), 3) for i in range(DAYS)]
        elif x[0] <= 0.03 and x[-1] >= 0.97 and base > 0 and (g.values > 0).all():
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
        if len(yrs) < MIN_YEARS:
            print(f"{name} [{src}]: {len(yrs)}-Yr < {MIN_YEARS}, skipped"); continue
        sea_h = cur_h = None
        if src in CRYPTO and rows:
            sea_h, cur_h = build_hourly(rows, this_year)
        out[name] = {"color": color, "yrs": yrs, "years": {str(y): years[y] for y in yrs},
                     "cur": cur, "sea_h": sea_h, "cur_h": cur_h, "daily": src == "stooq",
                     "metal": src in ("yahoo", "stooq")}
        print(f"{name} [{src}]: {yrs[0]}-{yrs[-1]} ({len(yrs)}-Yr)  rows={len(rows)}")
    order, caps = market_cap_order([n for n in COINS if n in out])
    for name in out:
        out[name]["mcap"] = caps.get(name, 0)
    yr0 = dt.datetime(this_year, 1, 1); yr1 = dt.datetime(this_year, 12, 31, 23)
    frac = min(max((now_local - yr0).total_seconds() / (yr1 - yr0).total_seconds(), 0.0), 1.0)
    months = ["","января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"]
    out["_meta"] = {"tz": "UTC+3", "H": HRS, "Dn": DAYS, "coins": order,
                    "today_h": round(frac * (HRS - 1)), "today_d": round(frac * (DAYS - 1)),
                    "asof": f"{now_local.day} {months[now_local.month]} {now_local.year}"}
    json.dump(out, open("data.json", "w"), separators=(",", ":"), ensure_ascii=False)
    print("wrote data.json")

if __name__ == "__main__":
    main()
