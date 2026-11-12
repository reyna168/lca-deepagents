---
name: tw-stock-kline-charts
description: Generate per-stock daily and weekly candlestick charts (PNG) plus a combined PDF from a Taiwan stock CSV using yfinance and mplfinance. Use when the user asks to 畫台股 K 線、日 K 週 K、技術線圖、kline_all.pdf、charts 圖表, or visualize stocks from tw_stock_60s.csv.
---

# 台股日 K／週 K 線圖

讀取台股清單 CSV，逐檔用 yfinance 下載近兩年日線，畫出「日 K（左）＋週 K（右）」，輸出每檔 PNG 與合併 PDF。

## 何時使用

- 使用者要「畫 K 線圖／日 K／週 K／技術線圖」
- 已有 `tw_stock_60s.csv`（可由 `tw-stock-price-screener` 產生）並想視覺化
- 需要 `charts/` PNG 或 `kline_all.pdf`

## 執行步驟

1. 確認依賴：`uv add yfinance mplfinance pandas matplotlib`，或使用 pip：
   `pip install yfinance mplfinance pandas matplotlib`
2. 確認工作目錄有 `tw_stock_60s.csv`，欄位需包含 `code`、`name`、`market`（`上市`／`上櫃`）。
3. 執行：

```powershell
python scripts/plot_kline.py
```

## 輸出

- `charts/<代號>_<名稱>.png`：每檔一張（顯示名稱中的 `*`、`/` 會置換）
- `kline_all.pdf`：全部合併，一檔一頁
- 終端機顯示進度、成功／失敗檔數與失敗原因

## 圖表規格

- 日 K：近 90 根，MA5／MA20
- 週 K：近 60 根，MA5／MA10
- 台股慣例紅漲綠跌
- 標題含代號、名稱、市場、最新收盤價

## 注意事項

- 上市代號用 `<code>.TW`，上櫃用 `<code>.TWO`。
- 需網路連線；單檔下載失敗不會中斷，會列在最後的失敗清單。
- 依賴 Windows 中文字型（Microsoft JhengHei）；其他平台會依序 fallback 到 PingFang TC、Noto Sans CJK TC、Arial Unicode MS。
- 資料來自 Yahoo Finance，可能延遲或缺漏。
