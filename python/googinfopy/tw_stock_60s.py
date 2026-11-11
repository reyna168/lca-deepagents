"""
查詢台股收盤價 60 <= 價格 < 70 的股票，並匯出 CSV。
資料來源：證交所 OpenAPI（上市）、櫃買中心 OpenAPI（上櫃）
用法：
    pip install requests pandas
    python tw_stock_60s.py
可調整下方 LOW / HIGH / INCLUDE_ETF。
"""
import requests
import pandas as pd
 
LOW, HIGH = 60, 70        # 60 <= 收盤價 < 70
INCLUDE_ETF = False       # True 則保留 ETF、特別股等非 4 碼代號
 
TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
 
 
def fetch(url, rename, market):
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    df = pd.DataFrame(resp.json())
    missing = [c for c in rename if c not in df.columns]
    if missing:
        raise KeyError(f"{market} 欄位不符 {missing}，實際欄位：{list(df.columns)}")
    df = df.rename(columns=rename)[list(rename.values())]
    df["market"] = market
    return df
 
 
twse = fetch(TWSE_URL,
             {"Code": "code", "Name": "name", "ClosingPrice": "close"}, "上市")
tpex = fetch(TPEX_URL,
             {"SecuritiesCompanyCode": "code", "CompanyName": "name", "Close": "close"}, "上櫃")
 
df = pd.concat([twse, tpex], ignore_index=True)
df["close"] = pd.to_numeric(df["close"].astype(str).str.replace(",", ""), errors="coerce")
 
if not INCLUDE_ETF:
    df = df[df["code"].astype(str).str.fullmatch(r"\d{4}")]
 
hit = df[(df["close"] >= LOW) & (df["close"] < HIGH)].sort_values(["market", "close"])
 
print(hit.groupby("market").size().to_string())
print("合計：", len(hit), "檔")
 
out = "tw_stock_60s.csv"
hit.to_csv(out, index=False, encoding="utf-8-sig")   # utf-8-sig 讓 Excel 正確顯示中文
print("已匯出：", out)
 