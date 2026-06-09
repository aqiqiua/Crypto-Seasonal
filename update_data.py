"""
Auto-updater for the seasonal-index page (FULL YEAR version).
Downloads Binance 1h candles for the whole calendar year of each year,
computes the seasonal index (UTC+3, hourly + daily, cumulative log-return
from Jan 1, averaged over complete years, min-max normalized to 0-100),
and writes data.json. Run by GitHub Actions once a day.

To add a coin: add it to COINS below (ticker + display color).
"""
import json, time, datetime as dt, urllib.request, urllib.parse
import numpy as np, pandas as pd

TZ = 3                       # display timezone offset (UTC+3)
FIRST_YEAR = 2018
COINS = {                    # ticker -> dark-theme color
    "BTC":  ("BTCUSDT",  "#F7A93B"),
    "XRP":  ("XRPUSDT",  "#38BDF8"),
    "SOL":  ("SOLUSDT",  "#A78BFA"),
    "LINK": ("LINKUSDT", "#3B82F6"),
    "ZEC":  ("ZECUSDT",  "#FACC15"),
    "BCH":  ("BCHUSDT",  "#34D399"),
    "TON":  ("TONUSDT",  "#22D3EE"),
    "DOGE": ("DOGEUSDT", "#F472B6"),
}
BASE = "https://data-api.binance.vision/api/v3/klines"
HRS = 365 * 24               # grid points for hourly resolution
DAYS = 365                   # grid points for daily resolution

def ms(d): return int(d.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

def fetch(symbol, start, end):
    out, cur, endms = [], ms(start), ms(end)
    while cur < endms:
        q = urllib.parse.urlencode({"symbol": symbol, "interval": "1h",
                                    "startTime": cur, "endTime": endms, "limit": 1000})
        try:
            with urllib.request.urlopen(BASE + "?" + q, timeout=30) as r:
                batch = json.load(r)
        except Exception as e:
            print("fetch error", symbol, e); break
        if not batch: break
        out += [[k[0], float(k[4])] for k in batch]
        cur = batch[-1][0] + 1
        if len(batch) < 1000: break
        time.sleep(0.2)
    return out

def build(rows, freq, periods, this_year):
    if not rows:
        return [None]*periods, [None]*periods, [], -1.0
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
        x = (g.index - yr0).total_seconds().values / span      # 0..~1 within the year
        if y == this_year:
            cur = np.interp(grid, x, ((g.values / base) - 1) * 100)
            cur_fmax = float(x[-1])
        else:
            if x[0] <= 0.03 and x[-1] >= 0.97:                 # year essentially complete
                paths[y] = np.interp(grid, x, (np.log(g.values) - np.log(base)) * 100)
    comp = sorted(paths)
    if not comp:
        return [None]*periods, [None]*periods, [], cur_fmax
    avg = np.mean(np.vstack([paths[y] for y in comp]), axis=0)
    lo, hi = float(np.min(avg)), float(np.max(avg))
    idx0 = (avg - lo) / (hi - lo) * 100 if hi > lo else avg * 0
    cv = [None if (cur is None or grid[i] > cur_fmax) else round(float(cur[i]), 2) for i in range(periods)]
    return [round(float(v), 2) for v in idx0], cv, comp, cur_fmax

def main():
    now_local = dt.datetime.utcnow() + dt.timedelta(hours=TZ)
    this_year = now_local.year
    out = {}
    for name, (sym, color) in COINS.items():
        rows = []
        for y in range(FIRST_YEAR, this_year + 1):
            rows += fetch(sym, dt.datetime(y, 1, 1), dt.datetime(y + 1, 1, 1, 3))
        sea_h, cur_h, comp, _ = build(rows, "h", HRS, this_year)
        sea_d, cur_d, _, _    = build(rows, "D", DAYS, this_year)
        win = f"{comp[0]}-{comp[-1]} ({len(comp)}-Yr)" if comp else "n/a"
        out[name] = {"color": color, "window": win,
                     "sea_h": sea_h, "cur_h": cur_h, "sea_d": sea_d, "cur_d": cur_d}
        print(f"{name}: {win}")
    # today's fractional position in the year (local)
    yr0 = dt.datetime(this_year, 1, 1); yr1 = dt.datetime(this_year, 12, 31, 23)
    frac = (now_local - yr0).total_seconds() / (yr1 - yr0).total_seconds()
    frac = min(max(frac, 0.0), 1.0)
    months = ["","января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"]
    out["_meta"] = {"tz": "UTC+3", "H": HRS, "Dn": DAYS, "coins": list(COINS.keys()),
                    "today_h": round(frac * (HRS - 1)), "today_d": round(frac * (DAYS - 1)),
                    "asof": f"{now_local.day} {months[now_local.month]} {now_local.year}"}
    json.dump(out, open("data.json", "w"), separators=(",", ":"), ensure_ascii=False)
    print("wrote data.json")

if __name__ == "__main__":
    main()
