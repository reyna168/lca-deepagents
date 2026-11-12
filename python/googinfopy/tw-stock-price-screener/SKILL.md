---
name: tw-stock-price-screener
description: Screen Taiwan listed and OTC stocks by closing-price range and export a CSV. Use when the user asks to 查詢台股收盤價、篩選台股價格區間、找 60 到 70 元的股票、抓上市上櫃收盤價、產生 tw_stock_60s.csv, or screen TWSE/TPEX stocks by price.
---

# 台股收盤價區間篩選

使用證交所（TWSE）與櫃買中心（TPEX）的免費 OpenAPI，抓取上市與上櫃個股當日收盤價，篩選指定價格區間後輸出 CSV。

## 何時使用

- 使用者要「查/篩選台股價格區間」（例如 60 <= 收盤價 < 70）
- 需要上市＋上櫃全市場收盤價清單
- 需要產生 `tw_stock_60s.csv` 供 `tw-stock-kline-charts` 後續畫 K 線

## 執行步驟

1. 確認依賴：`pip install requests pandas`
2. 依需求調整腳本開頭的參數（`scripts/tw_stock_60s.py` 第 12-13 行）：
   - `LOW, HIGH = 60, 70`：價格區間，含下限、不含上限
   - `INCLUDE_ETF = False`：`True` 時保留 ETF、特別股等非 4 碼代號
3. 在目標輸出資料夾執行：

```powershell
python scripts/tw_stock_60s.py
```

## 輸出

`tw_stock_60s.csv`（UTF-8-SIG，Excel 可直接開）：

| 欄位 | 說明 |
| --- | --- |
| code | 股票代號 |
| name | 股票名稱 |
| close | 收盤價（已轉數字） |
| market | 上市／上櫃 |

同時在終端機印出各市場檔數與合計。

## 注意事項

- 需要網路連線；OpenAPI 欄位若改版，腳本會抛 `KeyError` 並列出實際欄位。
- 價格比較用當日收盤價，非即時報價。
- ETF／權證等非 4 碼代號預設被排除。
- 資料來源：
  - 上市：`https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`
  - 上櫃：`https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes`
