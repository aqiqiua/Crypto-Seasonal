"""
Auto-updater for the seasonal-index page (FULL YEAR, multi-exchange).
Downloads 1h candles for the whole calendar year of each year from Binance
(or Bybit, per-coin), computes the seasonal index (UTC+3, hourly + daily,
cumulative log-return from Jan 1, averaged over complete years, min-max
normalized to 0-100) and writes data.json. Run by GitHub Actions daily.

To add a coin: add a line to COINS -> (exchange symbol, color, source).
source is "binance" or "bybit".
"""
import json, time, datetime as dt, urllib.request, urllib.parse
import numpy as np, pandas as pd

TZ = 3
FIRST_YEAR = 2018
COINS = {                      # name -> (symbol, color, source)
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
    "ZEC":  ("ZECUSDT",  "#FDE047", "binance"),
    "TON":  ("TONUSDT",  "#EF4444", "bybit"),
}
BINANCE = "https://data-api.binance.vision/api/v3/klines"
BYBIT   = "https://api.bybit.com/v5/market/kline"
HRS, DAYS = 365 * 24, 365

def ms(d): return int(d.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

def fetch_binance(symbol, start, end):
    out, cur, endms = [], ms(start), ms(end)
    while cur < endms:
        q = urllib.parse.urlencode({"symbol": symbol, "interval": "1h",
                                    "startTime": cur, "endTime": endms, "limit": 1000})
        try:
            with urllib.request.urlopen(BINANCE + "?" + q, timeout=30) as r:
                batch = json.load(r)
        except Exception as e:
            print("binance error", symbol, e); break
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
            with urllib.request.urlopen(BYBIT + "?" + q, timeout=30) as r:
                j = json.load(r)
        except Exception as e:
            print("bybit error", symbol, e); break
        lst = (j.get("result") or {}).get("list") or []
        if not lst: break
        for k in lst:                       # [start, open, high, low, close, ...]
            out.append([int(k[0]), float(k[4])])
        oldest = int(lst[-1][0])            # Bybit returns newest-first
        if oldest <= startms or len(lst) < 1000: break
        cur_end = oldest - 1
        time.sleep(0.2)
    return out

def build(rows, freq, periods, this_year):
    if not rows:
        return [None]*periods, [None]*periods, []
    df = pd.DataFrame(rows, columns=["t", "close"]).drop_duplicates("t")
    df["loc"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_localize(None) + pd.Timedelta(hours=TZ)
    df = df.set_index("loc").sort_index()
    paths, cur, cur_fmax = {}, None, -1.0
    grid = np.linspace(0, 1, periods)
    for y in sorted(set(df.index.year)):
        yr0 = pd.Timestamp(y, 1, 1); yr1 = pd.Timestamp(y, 12, 31, 23)
        span = (yr1 - yr0).total_seconds()
        g = df.loc[(df.index >= yr0) & (df.index <= pd.Timestamp(y, 12, 31, 23, 59)), "close"]
        if g.empty: continue
        g = g.resample(freq).last().ffill().bfill()
        if g.empty: continue
        base = g.iloc[0]
        x = (g.index - yr0).total_seconds().values / span
        if y == this_year:
            cur = np.interp(grid, x, ((g.values / base) - 1) * 100); cur_fmax = float(x[-1])
        elif x[0] <= 0.03 and x[-1] >= 0.97:
            paths[y] = np.interp(grid, x, (np.log(g.values) - np.log(base)) * 100)
    comp = sorted(paths)
    if not comp:
        return [None]*periods, [None]*periods, []
    avg = np.mean(np.vstack([paths[y] for y in comp]), axis=0)
    lo, hi = float(np.min(avg)), float(np.max(avg))
    idx0 = (avg - lo) / (hi - lo) * 100 if hi > lo else avg * 0
    cv = [None if (cur is None or grid[i] > cur_fmax) else round(float(cur[i]), 2) for i in range(periods)]
    return [round(float(v), 2) for v in idx0], cv, comp

def main():
    now_local = dt.datetime.utcnow() + dt.timedelta(hours=TZ)
    this_year = now_local.year
    out = {}
    for name, (sym, color, src) in COINS.items():
        fetch = fetch_bybit if src == "bybit" else fetch_binance
        rows = []
        for y in range(FIRST_YEAR, this_year + 1):
            rows += fetch(sym, dt.datetime(y, 1, 1), dt.datetime(y + 1, 1, 1, 3))
        sea_h, cur_h, comp = build(rows, "h", HRS, this_year)
        sea_d, cur_d, _    = build(rows, "D", DAYS, this_year)
        win = f"{comp[0]}-{comp[-1]} ({len(comp)}-Yr)" if comp else "n/a"
        out[name] = {"color": color, "window": win,
                     "sea_h": sea_h, "cur_h": cur_h, "sea_d": sea_d, "cur_d": cur_d}
        print(f"{name} [{src}]: {win}")
    yr0 = dt.datetime(this_year, 1, 1); yr1 = dt.datetime(this_year, 12, 31, 23)
    frac = min(max((now_local - yr0).total_seconds() / (yr1 - yr0).total_seconds(), 0.0), 1.0)
    months = ["","января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"]
    out["_meta"] = {"tz": "UTC+3", "H": HRS, "Dn": DAYS, "coins": list(COINS.keys()),
                    "today_h": round(frac * (HRS - 1)), "today_d": round(frac * (DAYS - 1)),
                    "asof": f"{now_local.day} {months[now_local.month]} {now_local.year}"}
    json.dump(out, open("data.json", "w"), separators=(",", ":"), ensure_ascii=False)
    print("wrote data.json")

if __name__ == "__main__":
    main()
