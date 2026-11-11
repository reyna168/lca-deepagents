"""
篩選「標的股收盤價 >= 可轉債轉換價」的可轉債（轉換價值 >= 100）。

流程
  1. 從櫃買中心 OpenAPI 找「轉(交)換債發行資料」，取得 CB 代碼、簡稱、轉換價
  2. 從證交所/櫃買 OpenAPI 取全市場個股收盤價
  3. 標的股代號 = CB 代碼前 4 碼（例：24867 -> 2486）
  4. 轉換價值 = 股價 / 轉換價 * 100，>= 100 即「站上轉換價」

用法
  uv add requests pandas
  uv run python cb_above_conv.py            # 執行篩選
  uv run python cb_above_conv.py --discover # 只列出櫃買 OpenAPI 中和轉換債有關的端點

重要
  OpenAPI 提供的是「發行時轉換價」。除權息、增資後轉換價會下修，
  若要精確，請建立 conv_price_override.csv（欄位：cb_code,conv_price）
  填入「現行轉換價」，腳本會優先使用。
"""
import json
import sys
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

NEAR_PCT = 95           # 轉換價值 >= 此值但未達 100，列為「接近」
OVERRIDE_CSV = "conv_price_override.csv"
OUT_CSV = "cb_above_conv.csv"

SWAGGER = "https://www.tpex.org.tw/openapi/swagger.json"
TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
HEADERS = {"User-Agent": "Mozilla/5.0"}
KEYWORDS = ("轉(交)換債", "可轉換", "轉換公司債", "轉換債")


def get_json(url):
    r = requests.get(url, timeout=30, headers=HEADERS)
    r.raise_for_status()
    text = r.content.decode("utf-8-sig", errors="replace").strip()
    if not text:
        raise SystemExit(f"回傳內容是空的：{url}（HTTP {r.status_code}）")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise SystemExit(f"回傳不是 JSON：{url}\nHTTP {r.status_code}，"
                         f"Content-Type={r.headers.get('Content-Type')}\n"
                         f"內容開頭：{text[:300]}")


def api_base(sw):
    """從 swagger 取得 API 網址前綴，例如 https://www.tpex.org.tw/openapi/v1"""
    servers = sw.get("servers") or []
    if servers and servers[0].get("url"):
        url = servers[0]["url"]
        if url.startswith("/"):
            url = "https://www.tpex.org.tw" + url
        return url.rstrip("/")
    host = sw.get("host", "www.tpex.org.tw")
    return f"https://{host}{sw.get('basePath', '/openapi/v1')}".rstrip("/")


def list_cb_endpoints():
    """掃 swagger，找出摘要或描述含轉換債關鍵字的路徑。"""
    sw = get_json(SWAGGER)
    base = api_base(sw)
    found = []
    for path, methods in sw.get("paths", {}).items():
        for m in methods.values():
            text = f"{m.get('summary', '')} {m.get('description', '')}"
            if any(k in text for k in KEYWORDS):
                found.append((path, m.get("summary", "")))
    return found, base


def find_col(df, candidates):
    """依候選關鍵字（中英皆可）找欄位，找不到回傳 None。"""
    for key in candidates:
        for c in df.columns:
            if key.lower() in str(c).lower():
                return c
    return None


def to_num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", ""), errors="coerce")


def load_cb_master():
    eps, base = list_cb_endpoints()
    # 優先「轉(交)換債」那支
    eps.sort(key=lambda x: ("轉(交)換債" not in x[1], "海外" in x[1]))
    if not eps:
        raise SystemExit("swagger 中找不到轉換債端點，請改用 --discover 檢查，"
                         "或自行準備 CB 清單 CSV。")
    path, summary = eps[0]
    url = base + path
    print(f"使用端點：{url}（{summary}）")
    df = pd.DataFrame(get_json(url))

    c_code = find_col(df, ["BondCode", "債券代碼"])
    c_name = find_col(df, ["ShortName", "債券簡稱"])
    c_conv = find_col(df, ["Conversion/ExchangePriceAtIssuance", "PriceAtIssuance",
                           "發行時轉換價格", "轉換價格"])
    c_bal = find_col(df, ["OutstandingAmount", "目前餘額"])
    if not (c_code and c_conv):
        raise SystemExit(f"認不得欄位，請把下列欄位貼給我：{list(df.columns)}")

    out = pd.DataFrame({
        "cb_code": df[c_code].astype(str).str.strip(),
        "cb_name": df[c_name] if c_name else "",
        "conv_price": to_num(df[c_conv]),
    })
    if c_bal:                                   # 只留還有餘額的
        out = out[to_num(df[c_bal]).fillna(0) > 0]
    out = out.dropna(subset=["conv_price"])
    out = out[out["conv_price"] > 0]
    out["stock_code"] = out["cb_code"].str[:4]
    return out.drop_duplicates("cb_code")


def apply_override(cb):
    p = Path(OVERRIDE_CSV)
    if not p.exists():
        print("（未提供 conv_price_override.csv，使用發行時轉換價估算）")
        return cb
    ov = pd.read_csv(p, dtype={"cb_code": str})
    ov["conv_price"] = pd.to_numeric(ov["conv_price"], errors="coerce")
    ov = ov.dropna().set_index("cb_code")["conv_price"]
    hit = cb["cb_code"].isin(ov.index)
    cb.loc[hit, "conv_price"] = cb.loc[hit, "cb_code"].map(ov)
    print(f"已用覆寫檔更新 {hit.sum()} 檔轉換價")
    return cb


def load_stock_prices():
    twse = pd.DataFrame(get_json(TWSE_URL)).rename(
        columns={"Code": "stock_code", "Name": "stock_name", "ClosingPrice": "close"})
    twse["market"] = "上市"
    tpex = pd.DataFrame(get_json(TPEX_URL)).rename(
        columns={"SecuritiesCompanyCode": "stock_code",
                 "CompanyName": "stock_name", "Close": "close"})
    tpex["market"] = "上櫃"
    keep = ["stock_code", "stock_name", "close", "market"]
    px = pd.concat([twse[keep], tpex[keep]], ignore_index=True)
    px["close"] = to_num(px["close"])
    return px.dropna(subset=["close"]).drop_duplicates("stock_code")


def find_cross_date(close, conv_price):
    """
    close: 以日期為索引的收盤價 Series（由舊到新）。
    回傳 (站上日期, 連續站上天數, 備註)。
    站上日期 = 最近一次「收盤由低於轉換價轉為 >= 轉換價」的那一天；
    若查詢期間一直都在轉換價之上，回傳期間第一天並註明。
    """
    close = close.dropna()
    if close.empty or close.iloc[-1] < conv_price:
        return None, 0, "最新收盤未站上"
    below = close[close < conv_price]
    if below.empty:
        first = close.index[0]
        return first.date(), len(close), "查詢期間起始前即已站上"
    last_below = below.index[-1]
    after = close[close.index > last_below]
    return after.index[0].date(), len(after), ""


def fetch_close_history(stock_code, market):
    ticker = f"{stock_code}.TW" if market == "上市" else f"{stock_code}.TWO"
    df = yf.download(ticker, period="1y", interval="1d",
                     auto_adjust=False, progress=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df["Close"]


def add_cross_dates(res):
    """替「站上轉換價」的列補上站上日期（只查有需要的標的股）。"""
    res = res.copy()
    res["cross_date"] = None
    res["days_above"] = None
    res["note"] = ""
    cache = {}
    for i, r in res[res["status"] == "站上轉換價"].iterrows():
        key = (r.stock_code, r.market)
        if key not in cache:
            try:
                cache[key] = fetch_close_history(*key)
            except Exception as e:
                cache[key] = None
                print(f"  取得 {r.stock_code} 歷史價失敗：{e!r}")
        hist = cache[key]
        if hist is None:
            res.at[i, "note"] = "無歷史價資料"
            continue
        d, n, note = find_cross_date(hist, r.conv_price)
        res.at[i, "cross_date"], res.at[i, "days_above"], res.at[i, "note"] = d, n, note
    return res


def screen(cb, px):
    df = cb.merge(px, on="stock_code", how="inner")
    df["parity"] = (df["close"] / df["conv_price"] * 100).round(2)
    df["status"] = "未達"
    df.loc[df["parity"] >= NEAR_PCT, "status"] = "接近"
    df.loc[df["parity"] >= 100, "status"] = "站上轉換價"
    cols = ["cb_code", "cb_name", "stock_code", "stock_name", "market",
            "close", "conv_price", "parity", "status"]
    return df[df["parity"] >= NEAR_PCT][cols].sort_values("parity", ascending=False)


def main():
    if "--discover" in sys.argv:
        eps, base = list_cb_endpoints()
        for path, summary in eps:
            print(base + path, "|", summary)
        return
    cb = apply_override(load_cb_master())
    res = screen(cb, load_stock_prices())
    print("取得站上轉換價的日期中…")
    res = add_cross_dates(res)
    print(res.groupby("status").size().to_string())
    res.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"已匯出：{OUT_CSV}")
    print(res.head(20).to_string(index=False))


if __name__ == "__main__":
    main()