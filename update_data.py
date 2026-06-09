"""
Auto-updater for the seasonal-index page.
Downloads Binance 1h candles for May-July of each year, computes the seasonal
index (UTC+3, hourly + daily, log-return averaged + min-max normalized), and
writes data.json. Run by GitHub Actions once a day.

To add a coin: add it to COINS below (ticker + display color).
"""
import json, time, datetime as dt, urllib.request, urllib.parse
import numpy as np, pandas as pd

TZ = 3                      # display timezone offset (UTC+3)
FIRST_YEAR = 2018
COINS = {                   # ticker -> dark-theme color
    "BTC": ("BTCUSDT", "#F7A93B"),
    "XRP": ("XRPUSDT", "#5BC8FF"),
    "SOL": ("SOLUSDT", "#B57BFF"),
}
BASE = "https://data-api.binance.vision/api/v3/klines"
H = 92 * 24                 # hours in May+Jun+Jul

def ms(d): return int(d.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

def fetch(symbol, start, end):
    """Fetch 1h klines [start,end) as list of [openTimeMs, close]."""
    out, cur = [], ms(start)
    endms = ms(end)
    while cur < endms:
        q = urllib.parse.urlencode({"symbol": symbol, "interval": "1h",
                                    "startTime": cur, "endTime": endms, "limit": 1000})
        with urllib.request.urlopen(BASE + "?" + q, timeout=30) as r:
            batch = json.load(r)
        if not batch: break
        out += [[k[0], float(k[4])] for k in batch]
        cur = batch[-1][0] + 1
        if len(batch) < 1000: break
        time.sleep(0.2)
    return out

def series(rows, freq, periods):
    """rows -> per-year cumulative paths (local tz); returns avg-index, current."""
    if not rows:
        return [None]*periods, [None]*periods, []
    df = pd.DataFrame(rows, columns=["t", "close"])
    df["loc"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_localize(None) + pd.Timedelta(hours=TZ)
    df["year"] = df["loc"].dt.year
    paths, cur, curN = {}, None, 0
    this_year = (dt.datetime.utcnow() + dt.timedelta(hours=TZ)).year
    for y in sorted(df["year"].unique()):
        g = df[df["year"] == y]
        idx = pd.date_range(pd.Timestamp(y, 5, 1), periods=periods, freq=freq)
        s = g.set_index("loc")["close"].resample(freq).last().reindex(idx)
        cov = int(s.notna().sum()); s = s.ffill().bfill()
        if s.isna().all(): continue
        base = s.iloc[0]
        if y == this_year:
            cur = ((s / base - 1) * 100).values; curN = cov
        elif cov >= periods * 0.95:
            paths[y] = (np.log(s.values) - np.log(base)) * 100
    comp = sorted(paths)
    if not comp:
        return [None]*periods, [None]*periods, []
    avg = np.nanmean(np.vstack([paths[y] for y in comp]), axis=0)
    lo, hi = np.nanmin(avg), np.nanmax(avg)
    idx0 = (avg - lo) / (hi - lo) * 100 if hi > lo else avg*0
    cv = [None if (i >= curN or cur is None or np.isnan(cur[i])) else round(float(cur[i]), 2) for i in range(periods)]
    return [round(float(v), 2) for v in idx0], cv, comp

def main():
    now_local = dt.datetime.utcnow() + dt.timedelta(hours=TZ)
    this_year = now_local.year
    out = {}
    for name, (sym, color) in COINS.items():
        rows = []
        for y in range(FIRST_YEAR, this_year + 1):
            rows += fetch(sym, dt.datetime(y, 4, 30), dt.datetime(y, 8, 1, 4))
        sea_h, cur_h, comp = series(rows, "h", H)
        sea_d, cur_d, _    = series(rows, "D", 92)
        win = f"{comp[0]}-{comp[-1]} ({len(comp)}-Yr)" if comp else "n/a"
        out[name] = {"color": color, "window": win,
                     "sea_h": sea_h, "cur_h": cur_h, "sea_d": sea_d, "cur_d": cur_d}
        print(f"{name}: {win}")
    # today position in May-Jul window (local); -1 if outside
    day = (now_local.date() - dt.date(this_year, 5, 1)).days
    in_win = 0 <= day < 92
    months = ["","января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"]
    out["_meta"] = {"tz": "UTC+3", "H": H, "Dn": 92, "coins": list(COINS.keys()),
                    "today_h": (day*24 + now_local.hour) if in_win else -1,
                    "today_d": day if in_win else -1,
                    "asof": f"{now_local.day} {months[now_local.month]} {now_local.year}"}
    json.dump(out, open("data.json", "w"), separators=(",", ":"), ensure_ascii=False)
    print("wrote data.json")

if __name__ == "__main__":
    main()
