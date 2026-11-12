
"""
台股道氏理論 + 均線 + 成交量量化選股系統
功能：
1. 掃描上市、上櫃普通股
2. 計算道氏趨勢
3. 計算 MA5 / MA20 / MA60
4. 成交量確認
5. 買賣訊號
6. 單股歷史回測
7. 輸出 Excel 與 CSV

注意：只產生訊號，不會自動下單。
"""

import os
import time
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf
import twstock

from tqdm import tqdm

warnings.filterwarnings("ignore")

# ==================================================
# 參數設定
# ==================================================

CONFIG = {
    "period": "2y",
    "interval": "1d",

    "ma_short": 5,
    "ma_mid": 20,
    "ma_long": 60,

    "volume_period": 20,
    "volume_ratio": 1.5,

    # 道氏波段確認參數
    "pivot_left": 5,
    "pivot_right": 5,

    # 回測參數
    "initial_capital": 1000000,
    "commission": 0.001425,
    "tax": 0.003,
    "slippage": 0.001,

    "min_price": 10,
    "min_avg_volume": 1000,

    "cache_dir": "cache",
    "output_dir": "output",
}

os.makedirs(CONFIG["cache_dir"], exist_ok=True)
os.makedirs(CONFIG["output_dir"], exist_ok=True)


# ==================================================
# 一、取得上市、上櫃股票清單
# ==================================================

def get_stock_universe():

    stocks = []

    for code, item in twstock.codes.items():

        if item.type != "股票":
            continue

        if item.market not in ["上市", "上櫃"]:
            continue

        suffix = ".TW" if item.market == "上市" else ".TWO"

        stocks.append({
            "code": code,
            "name": item.name,
            "market": item.market,
            "ticker": code + suffix
        })

    return pd.DataFrame(stocks).drop_duplicates(
        subset=["ticker"]
    )


# ==================================================
# 二、取得股票歷史資料
# ==================================================

def download_stock(ticker):

    cache_file = os.path.join(
        CONFIG["cache_dir"],
        ticker.replace(".", "_") + ".csv"
    )

    try:

        df = yf.download(
            ticker,
            period=CONFIG["period"],
            interval=CONFIG["interval"],
            auto_adjust=False,
            progress=False,
            threads=False
        )

        if df.empty:
            return None

        # 處理 yfinance MultiIndex
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        required = ["Open", "High", "Low", "Close", "Volume"]

        if not all(col in df.columns for col in required):
            return None

        df = df[required].copy()

        for col in required:
            df[col] = pd.to_numeric(
                df[col], errors="coerce"
            )

        df = df.dropna()
        df = df[df["Volume"] > 0]

        if len(df) < 100:
            return None

        df.to_csv(cache_file)

        return df

    except Exception as e:

        print(f"下載失敗 {ticker}: {e}")
        return None


# ==================================================
# 三、道氏理論：確認波段高低點
# ==================================================

def calculate_dow_trend(df):

    df = df.copy()

    left = CONFIG["pivot_left"]
    right = CONFIG["pivot_right"]

    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()

    n = len(df)

    pivot_highs = []
    pivot_lows = []

    trend = np.zeros(n, dtype=int)

    # 1 = 多頭
    # -1 = 空頭
    # 0 = 盤整或未確認

    current_trend = 0

    for i in range(n):

        # 僅在右側確認K棒已經完成後，
        # 才承認過去的波段高低點。
        p = i - right

        if p >= left:

            high_window = highs[p-left:p+right+1]
            low_window = lows[p-left:p+right+1]

            if (
                highs[p] == np.max(high_window)
                and np.sum(high_window == highs[p]) == 1
            ):
                pivot_highs.append(highs[p])

            if (
                lows[p] == np.min(low_window)
                and np.sum(low_window == lows[p]) == 1
            ):
                pivot_lows.append(lows[p])

        # 最近兩個已確認波段高點與低點
        if len(pivot_highs) >= 2 and len(pivot_lows) >= 2:

            h1, h2 = pivot_highs[-2:]
            l1, l2 = pivot_lows[-2:]

            if h2 > h1 and l2 > l1:
                current_trend = 1

            elif h2 < h1 and l2 < l1:
                current_trend = -1

            else:
                current_trend = 0

        trend[i] = current_trend

    df["DowTrend"] = trend

    return df


# ==================================================
# 四、均線、成交量與交易訊號
# ==================================================

def calculate_signals(df):

    df = df.copy()

    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()

    df["VOL20"] = df["Volume"].rolling(20).mean()

    df["VolumeRatio"] = (
        df["Volume"] / df["VOL20"]
    )

    # 均線多頭排列
    df["BullishMA"] = (
        (df["Close"] > df["MA5"]) &
        (df["MA5"] > df["MA20"]) &
        (df["MA20"] > df["MA60"])
    )

    # 均線空頭排列
    df["BearishMA"] = (
        (df["Close"] < df["MA5"]) &
        (df["MA5"] < df["MA20"]) &
        (df["MA20"] < df["MA60"])
    )

    # 價格上漲
    df["PriceUp"] = df["Close"] > df["Close"].shift(1)

    # 成交量確認
    df["VolumeConfirm"] = (
        df["VolumeRatio"] >= CONFIG["volume_ratio"]
    )

    # 道氏多頭
    df["DowBullish"] = df["DowTrend"] == 1

    # 道氏空頭
    df["DowBearish"] = df["DowTrend"] == -1

    # 買進條件
    df["BuySetup"] = (
        df["DowBullish"] &
        df["BullishMA"] &
        df["PriceUp"] &
        df["VolumeConfirm"]
    )

    # 賣出條件
    df["SellSetup"] = (
        df["DowBearish"] |
        (
            (df["MA5"] < df["MA20"]) &
            (df["MA5"].shift(1) >= df["MA20"].shift(1))
        )
    )

    # 訊號只在條件首次成立時出現
    df["BuySignal"] = (
        df["BuySetup"] &
        ~df["BuySetup"].shift(1, fill_value=False)
    )

    df["SellSignal"] = (
        df["SellSetup"] &
        ~df["SellSetup"].shift(1, fill_value=False)
    )

    return df


# ==================================================
# 五、計算選股分數
# ==================================================

def calculate_score(row):

    score = 0

    if row["DowTrend"] == 1:
        score += 40

    if row["BullishMA"]:
        score += 30

    if row["VolumeRatio"] >= 1.5:
        score += 20

    elif row["VolumeRatio"] >= 1.2:
        score += 10

    if row["PriceUp"]:
        score += 10

    return score


# ==================================================
# 六、掃描全部股票
# ==================================================

def scan_market():

    universe = get_stock_universe()

    print("股票總數:", len(universe))

    results = []

    for _, stock in tqdm(
        universe.iterrows(),
        total=len(universe)
    ):

        ticker = stock["ticker"]

        df = download_stock(ticker)

        if df is None:
            continue

        df = calculate_dow_trend(df)
        df = calculate_signals(df)

        last = df.iloc[-1]

        if pd.isna(last["MA60"]):
            continue

        avg_volume = last["VOL20"]

        if (
            last["Close"] < CONFIG["min_price"]
            or avg_volume < CONFIG["min_avg_volume"]
        ):
            continue

        score = calculate_score(last)

        if last["BuySetup"]:
            signal = "買進觀察"
        elif last["SellSetup"]:
            signal = "賣出"
        elif last["DowTrend"] == 1:
            signal = "多頭持續"
        elif last["DowTrend"] == -1:
            signal = "空頭"
        else:
            signal = "觀望"

        results.append({

            "代碼": stock["code"],
            "名稱": stock["name"],
            "市場": stock["market"],

            "收盤價": round(last["Close"], 2),

            "MA5": round(last["MA5"], 2),
            "MA20": round(last["MA20"], 2),
            "MA60": round(last["MA60"], 2),

            "成交量比": round(last["VolumeRatio"], 2),

            "道氏趨勢": int(last["DowTrend"]),

            "選股分數": score,

            "交易訊號": signal,

            "買進觸發": bool(last["BuySignal"]),
            "賣出觸發": bool(last["SellSignal"]),

            "資料日期": str(df.index[-1].date())

        })

        # 降低免費資料來源的請求頻率
        time.sleep(0.1)

    result = pd.DataFrame(results)

    if result.empty:
        return result

    result = result.sort_values(
        "選股分數",
        ascending=False
    )

    return result


# ==================================================
# 七、歷史回測
# ==================================================

def backtest(df):

    df = df.copy()

    capital = CONFIG["initial_capital"]

    position = 0
    entry_price = 0
    entry_date = None

    trades = []

    commission = CONFIG["commission"]
    tax = CONFIG["tax"]
    slippage = CONFIG["slippage"]

    # 交易訊號於收盤後產生，
    # 下一個交易日開盤執行。
    for i in range(len(df) - 1):

        row = df.iloc[i]
        next_row = df.iloc[i + 1]

        if position == 0 and row["BuySignal"]:

            entry_price = (
                next_row["Open"] * (1 + slippage)
            )

            entry_date = df.index[i + 1]

            position = 1

        elif position == 1 and row["SellSetup"]:

            exit_price = (
                next_row["Open"] * (1 - slippage)
            )

            gross_return = (
                exit_price / entry_price
            ) - 1

            # 來回交易成本
            net_return = (
                (1 + gross_return)
                * (1 - commission)
                * (1 - commission - tax)
            ) - 1

            trades.append({

                "買進日期": entry_date,
                "賣出日期": df.index[i + 1],

                "買進價格": entry_price,
                "賣出價格": exit_price,

                "報酬率": net_return

            })

            position = 0

    # 回測結束仍持有股票，以最後收盤價估值
    if position == 1:

        exit_price = df.iloc[-1]["Close"] * (1 - slippage)

        gross_return = (
            exit_price / entry_price
        ) - 1

        net_return = (
            (1 + gross_return)
            * (1 - commission)
            * (1 - commission - tax)
        ) - 1

        trades.append({

            "買進日期": entry_date,
            "賣出日期": df.index[-1],

            "買進價格": entry_price,
            "賣出價格": exit_price,

            "報酬率": net_return

        })

    trades_df = pd.DataFrame(trades)

    if trades_df.empty:

        return {
            "交易次數": 0,
            "勝率": 0,
            "平均報酬": 0,
            "累積報酬": 0,
            "最大單筆報酬": 0,
            "最差單筆報酬": 0,
            "交易明細": trades_df
        }

    returns = trades_df["報酬率"]

    equity = (1 + returns).cumprod()

    drawdown = equity / equity.cummax() - 1

    return {

        "交易次數": len(trades_df),

        "勝率": round(
            (returns > 0).mean() * 100, 2
        ),

        "平均報酬": round(
            returns.mean() * 100, 2
        ),

        "累積報酬": round(
            (equity.iloc[-1] - 1) * 100, 2
        ),

        "最大回撤": round(
            drawdown.min() * 100, 2
        ),

        "最大單筆報酬": round(
            returns.max() * 100, 2
        ),

        "最差單筆報酬": round(
            returns.min() * 100, 2
        ),

        "交易明細": trades_df

    }


# ==================================================
# 八、輸出報告
# ==================================================

def export_report(results):

    if results.empty:
        print("沒有符合條件的股票")
        return

    today = datetime.now().strftime("%Y%m%d")

    csv_path = os.path.join(
        CONFIG["output_dir"],
        f"stock_signals_{today}.csv"
    )

    excel_path = os.path.join(
        CONFIG["output_dir"],
        f"stock_signals_{today}.xlsx"
    )

    results.to_csv(
        csv_path,
        index=False,
        encoding="utf-8-sig"
    )

    results.to_excel(
        excel_path,
        index=False
    )

    print("\n========== 選股結果 ==========")

    print(
        results.head(30).to_string(index=False)
    )

    print("\nCSV:", csv_path)
    print("Excel:", excel_path)


# ==================================================
# 九、執行
# ==================================================

def main():

    print("=" * 55)
    print("台股道氏理論＋均線＋成交量量化系統")
    print("=" * 55)

    print("開始掃描上市、上櫃股票...")

    results = scan_market()

    if results.empty:
        print("沒有取得有效選股資料")
        return

    export_report(results)

    print("\n掃描完成！")


if __name__ == "__main__":
    main()
