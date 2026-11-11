"""
讀取 tw_stock_60s.csv，為每檔股票畫「日 K（左）＋週 K（右）」，
輸出：
  charts/<代號>_<名稱>.png   每檔一張
  kline_all.pdf              全部合併成一份 PDF（一檔一頁）

安裝：uv add yfinance mplfinance pandas matplotlib
執行：uv run python plot_kline.py
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
import yfinance as yf
from matplotlib.backends.backend_pdf import PdfPages

CSV_PATH = "tw_stock_60s.csv"
OUT_DIR = Path("charts")
PDF_PATH = "kline_all.pdf"
DAILY_BARS = 90      # 日 K 顯示最近幾根
WEEKLY_BARS = 60     # 週 K 顯示最近幾根

# 中文字型（Windows 優先，其次 macOS / Linux）
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "PingFang TC",
                                   "Noto Sans CJK TC", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

# 台股慣例：紅漲綠跌
MC = mpf.make_marketcolors(up="red", down="green", edge="inherit",
                           wick="inherit", volume="inherit")
STYLE = mpf.make_mpf_style(base_mpf_style="charles", marketcolors=MC,
                           rc={"font.sans-serif": plt.rcParams["font.sans-serif"],
                               "axes.unicode_minus": False})


def to_ticker(code, market):
    return f"{code}.TW" if market == "上市" else f"{code}.TWO"


def fetch_daily(ticker):
    df = yf.download(ticker, period="2y", interval="1d",
                     auto_adjust=False, progress=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):      # 新版 yfinance 會有多層欄位
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    return df if len(df) >= 10 else None


def to_weekly(daily):
    wk = daily.resample("W-FRI").agg({"Open": "first", "High": "max",
                                      "Low": "min", "Close": "last",
                                      "Volume": "sum"})
    return wk.dropna()


def draw(daily, code, name, market):
    weekly = to_weekly(daily)
    d, w = daily.tail(DAILY_BARS), weekly.tail(WEEKLY_BARS)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    mpf.plot(d, type="candle", ax=ax1, style=STYLE, mav=(5, 20), volume=False)
    mpf.plot(w, type="candle", ax=ax2, style=STYLE, mav=(5, 10), volume=False)
    ax1.set_title(f"日K  MA5/MA20（最近 {len(d)} 日）")
    ax2.set_title(f"週K  MA5/MA10（最近 {len(w)} 週）")
    last = daily["Close"].iloc[-1]
    fig.suptitle(f"{code} {name}（{market}）  最新收盤 {last:.2f}",
                 fontsize=15)
    fig.tight_layout()
    return fig


def main():
    df = pd.read_csv(CSV_PATH, dtype={"code": str})
    OUT_DIR.mkdir(exist_ok=True)
    failed = []

    with PdfPages(PDF_PATH) as pdf:
        for i, r in enumerate(df.itertuples(index=False), 1):
            tk = to_ticker(r.code, r.market)
            print(f"[{i}/{len(df)}] {tk} {r.name}", flush=True)
            try:
                daily = fetch_daily(tk)
                if daily is None:
                    failed.append((r.code, r.name, "無資料或資料不足"))
                    continue
                fig = draw(daily, r.code, r.name, r.market)
                safe = str(r.name).replace("*", "").replace("/", "-")
                fig.savefig(OUT_DIR / f"{r.code}_{safe}.png", dpi=110)
                pdf.savefig(fig)
                plt.close(fig)
            except Exception as e:                      # 單檔失敗不中斷
                failed.append((r.code, r.name, repr(e)))

    print(f"\n完成：{len(df) - len(failed)} 檔成功，{len(failed)} 檔失敗")
    for f in failed:
        print("  失敗：", *f)
    print(f"輸出：{OUT_DIR}/ 與 {PDF_PATH}")


if __name__ == "__main__":
    sys.exit(main())