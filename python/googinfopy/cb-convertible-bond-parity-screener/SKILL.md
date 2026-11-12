---
name: cb-convertible-bond-parity-screener
description: Screen Taiwan convertible bonds whose underlying stock close is at or above the conversion price (parity >= 100), using TPEX/TWSE OpenAPI plus yfinance history, and export cb_above_conv.csv. Use when the user asks about 可轉債、轉換價、轉換價值、CB 站上轉換價、parity、cb_above_conv.csv, or screening convertible bonds by conversion parity.
---

# 可轉債轉換價值篩選

找出「標的股收盤價 >= 可轉債轉換價」（轉換價值 parity >= 100）的可轉債，並可補上「最近一次站上轉換價」的日期與連續天數。

## 何時使用

- 使用者要「找站上轉換價的可轉債／CB 轉換價值／parity 篩選」
- 需要 `cb_above_conv.csv`
- 想檢查櫃買 OpenAPI 有哪些轉換債端點

## 執行步驟

1. 確認依賴：`uv add requests pandas yfinance`，或 `pip install requests pandas yfinance`
2. 執行篩選：

```powershell
python scripts/cb_above_conv.py
```

3. 若端點或欄位有問題，先列出可用端點：

```powershell
python scripts/cb_above_conv.py --discover
```

## 選用：現行轉換價覆寫

OpenAPI 提供的是「發行時轉換價」，除權息或增資後會下修。若需精確，建立 `conv_price_override.csv`（放在執行目錄）：

```csv
cb_code,conv_price
24867,25.5
```

腳本會優先使用覆寫檔的現行轉換價。

## 輸出

`cb_above_conv.csv`（UTF-8-SIG），列出 parity >= 95 的標的，欄位：

| 欄位 | 說明 |
| --- | --- |
| cb_code / cb_name | 可轉債代碼／簡稱 |
| stock_code / stock_name / market | 標的股代號／名稱／市場 |
| close | 標的股收盤價 |
| conv_price | 轉換價 |
| parity | 轉換價值 = 收盤價 / 轉換價 * 100 |
| status | 站上轉換價（>=100）／接近（95-100） |
| cross_date | 最近一次由下向上站上轉換價的日期 |
| days_above | 站上後連續天數 |
| note | 例如「查詢期間起始前即已站上」「無歷史價資料」 |

## 判斷邏輯

1. 從櫃買 swagger 找「轉(交)換債發行資料」端點，取 CB 代碼、簡稱、轉換價與餘額（只留有餘額者）。
2. 標的股代號 = CB 代碼前 4 碼。
3. 從證交所與櫃買 OpenAPI 取全市場收盤價。
4. 以 yfinance 近一年日線判斷最近一次站上轉換價的日期。

## 注意事項

- 需要網路連線；OpenAPI 欄位改版時，腳本會提示認不得欄位並列出實際欄位。
- 只查「站上轉換價」的標的歷史價，以減少 yfinance 請求。
- 資料來源為公開 OpenAPI 與 Yahoo Finance，僅供研究，不構成投資建議。
