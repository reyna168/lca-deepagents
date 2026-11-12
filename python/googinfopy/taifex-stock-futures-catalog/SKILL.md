---
name: taifex-stock-futures-catalog
description: Download the full TAIFEX stock-futures contract catalog (stocks and ETFs) and export contract-level and underlying-level CSVs, with optional keyword lookup. Use when the user asks about 股票期貨清單、期交所股票期貨、個股期貨、哪些股票有股期、taifex_stock_futures.csv、stock_futures_underlying.csv, or checking whether a stock has a stock future.
---

# 期交所股票期貨清單

抓取臺灣期貨交易所「保證金一覽表－股票類」頁面，解析所有股票期貨契約（含小型契約與 ETF 期貨），輸出契約明細與標的證券清單。

## 何時使用

- 使用者要「股票期貨清單／個股期貨／哪些股票有股票期貨」
- 查詢某檔股票是否有股期、標準或小型契約代碼
- 需要 `stock_futures_all.csv` 或 `stock_futures_underlying.csv`

## 執行步驟

1. 確認依賴：`pip install requests beautifulsoup4`
2. 下載並輸出 CSV：

```powershell
python scripts/taifex_stock_futures.py
```

3. 可附加關鍵字（代號或名稱）直接查詢，可傳多個：

```powershell
python scripts/taifex_stock_futures.py 2330 鴻海 台積電
```

## 輸出

- `stock_futures_all.csv`：一個契約一列。欄位：類型、英文代碼、標的證券代號、中文簡稱、標的證券、契約規格、保證金級距、結算保證金、維持保證金、原始保證金
- `stock_futures_underlying.csv`：依標的證券代號去重。欄位：類型、標的證券代號、標的證券、標準契約代碼、標準契約簡稱、小型契約代碼

兩者皆為 UTF-8-SIG。

## 判斷邏輯

- 只解析表頭含「股票期貨英文代碼」的表，排除股票選擇權。
- 有「保證金所屬級距」欄視為股票期貨；否則視為 ETF 期貨。
- 中文簡稱含「小型」→ `小型(100股/1000單位)`；其餘股票為 `標準(2000股)`、ETF 為 `標準(10000單位)`。
- 同一標的可能同時有標準與小型契約，會分別填入 `標準契約代碼` / `小型契約代碼`。

## 注意事項

- 需要網路連線；若期交所網頁改版、表頭名稱變動，會以「解析不到任何資料」結束，需更新欄位名稱。
- 頁面資料來源：`https://www.taifex.com.tw/cht/5/stockMargining`
- 保證金比例為頁面公告值，實際下單仍以期交所與期貨商公告為準。
