# -*- coding: utf-8 -*-
"""
台股量化選股系統 v2
道氏理論 + 均線 + 成交量 + 黃金切割(費波那契回檔) + 除息填息 + 回測

功能
 1. 掃描上市、上櫃普通股(4 碼數字代號)
 2. 道氏趨勢(右側確認的波段高低點,不偷看未來)
 3. MA(短/中/長)、量比、買賣訊號
 4. 黃金切割:最近一段上升波段的 0.382~0.618 回檔區、延伸目標價
 5. 除息填息:歷史填息率、平均填息天數、最近一次除息是否已填息
 6. 回測:動能策略與黃金切割策略(隔日開盤成交、含手續費/證交稅/滑價)
 7. 輸出 CSV 與多工作表 Excel

資料處理原則
 - 趨勢、均線、波段、回測使用「還原價」(A 開頭欄位),避免除息缺口造成假訊號
 - 填息判斷使用「未還原價」,因為要和除息前一日實際收盤價比較
 - 成交量單位為「股」,過濾條件以「張」(1 張 = 1000 股)設定

只產生訊號,不會自動下單。內容僅供研究,不構成投資建議。

用法
    python tw_stock_scanner.py                    # 全市場掃描
    python tw_stock_scanner.py --refresh          # 忽略快取,重新下載
    python tw_stock_scanner.py --limit 50         # 只掃前 50 檔(測試用)
    python tw_stock_scanner.py --backtest 2330 2317   # 指定個股回測
    python tw_stock_scanner.py --no-backtest      # 掃描後不做前 N 名回測

需要安裝
    pip install yfinance twstock pandas numpy tqdm openpyxl
"""

import argparse
import os
import time
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import twstock
import yfinance as yf
from tqdm import tqdm

warnings.filterwarnings("ignore")

# ==================================================
# 參數設定(以下參數全部會被程式實際使用)
# ==================================================

CONFIG = {
    # ---- 資料 ----
    "period": "5y",              # 填息統計需要較長歷史,建議 3y 以上
    "min_bars": 120,             # 至少需要幾根 K 棒
    "max_stale_days": 7,         # 最後一筆資料距今超過幾天視為停牌/下市,略過
    "cache_dir": "cache",
    "output_dir": "output",
    "cache_ttl_hours": 12,       # 快取有效時間,超過才重新下載
    "request_sleep": 0.15,       # 實際下載後的間隔(秒),使用快取時不等待
    "retries": 2,                # 下載失敗重試次數

    # ---- 均線與量能 ----
    "ma_short": 5,
    "ma_mid": 20,
    "ma_long": 60,
    "volume_period": 20,
    "volume_ratio": 1.5,         # 量比達此值視為爆量確認
    "volume_ratio_weak": 1.2,    # 量比達此值給部分分數

    # ---- 道氏波段(左右各 N 根 K 棒確認) ----
    "pivot_left": 5,
    "pivot_right": 5,

    # ---- 黃金切割 ----
    "fib_zone": (0.382, 0.618),      # 回檔買進區間
    "fib_extensions": (1.272, 1.618),  # 延伸目標

    # ---- 除息填息 ----
    "fill_use_close": True,      # True=收盤價回到除息前收盤才算填息;False=盤中最高價即可

    # ---- 過濾 ----
    "min_price": 10,
    "lot_size": 1000,            # 1 張 = 1000 股
    "min_avg_lots": 500,         # 20 日均量至少幾「張」

    # ---- 回測 ----
    "commission": 0.001425,      # 單邊手續費
    "tax": 0.003,                # 賣出證交稅
    "slippage": 0.001,           # 單邊滑價
    "backtest_top_n": 10,        # 掃描後,對分數前 N 名做回測
}


# ==================================================
# 小工具
# ==================================================

def rnd(x, nd=2):
    """四捨五入;NaN / None 回傳 None"""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if np.isnan(v):
        return None
    return round(v, nd)


def first_true(s):
    """只保留連續為真的第一天"""
    s = s.fillna(False).astype(bool)
    return s & ~s.shift(1, fill_value=False)


def ma_name(key):
    return f"MA{CONFIG[key]}"


# ==================================================
# 一、股票清單
# ==================================================

def get_stock_universe():
    stocks = []

    for code, item in twstock.codes.items():
        if item.type != "股票":
            continue
        if item.market not in ("上市", "上櫃"):
            continue
        # 只取 4 碼數字代號,排除特別股、存託憑證等
        if not (code.isdigit() and len(code) == 4):
            continue

        suffix = ".TW" if item.market == "上市" else ".TWO"
        stocks.append({
            "code": code,
            "name": item.name,
            "market": item.market,
            "ticker": code + suffix,
        })

    return pd.DataFrame(stocks).drop_duplicates(subset=["ticker"]).reset_index(drop=True)


# ==================================================
# 二、取得資料(含快取、除息、還原因子)
# ==================================================

CACHE_COLUMNS = ["Open", "High", "Low", "Close", "Volume", "Dividends", "AdjFactor"]


def dividend_adj_factor(close, div):
    """
    由現金股利自行計算向前還原因子(最新一日為 1)。
    除息日之前的價格乘上 (1 - 股利 / 除息前一日收盤)。
    """
    n = len(close)
    factor = np.ones(n)
    for p in np.where(div > 0)[0]:
        if p == 0:
            continue
        f = 1.0 - div[p] / close[p - 1]
        if f > 0:
            factor[:p] *= f
    return factor


def fetch_from_yahoo(ticker):
    raw = yf.Ticker(ticker).history(
        period=CONFIG["period"],
        interval="1d",
        auto_adjust=False,
        actions=True,
    )

    if raw is None or raw.empty:
        return None

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    needed = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in raw.columns for c in needed):
        return None

    raw = raw.copy()
    for c in needed:
        raw[c] = pd.to_numeric(raw[c], errors="coerce")

    if "Dividends" not in raw.columns:
        raw["Dividends"] = 0.0
    raw["Dividends"] = pd.to_numeric(raw["Dividends"], errors="coerce").fillna(0.0)

    raw = raw.dropna(subset=needed)
    raw = raw[raw["Volume"] > 0]

    if raw.empty:
        return None

    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
    raw = raw[~raw.index.duplicated(keep="last")].sort_index()

    # 還原因子:優先用 Yahoo 的 Adj Close,否則用現金股利自行計算
    factor = None
    if "Adj Close" in raw.columns:
        adj = pd.to_numeric(raw["Adj Close"], errors="coerce")
        f = (adj / raw["Close"]).to_numpy()
        if np.isfinite(f).all() and (f > 0).all():
            factor = f

    if factor is None:
        factor = dividend_adj_factor(
            raw["Close"].to_numpy(), raw["Dividends"].to_numpy()
        )

    raw["AdjFactor"] = factor

    return raw[CACHE_COLUMNS].copy()


def load_stock(ticker, refresh=False):
    """
    回傳 (df, downloaded)。
    快取有效就直接讀取;否則下載並寫入快取。
    """
    cache_file = os.path.join(
        CONFIG["cache_dir"], ticker.replace(".", "_") + ".csv"
    )

    if not refresh and os.path.exists(cache_file):
        age_hours = (time.time() - os.path.getmtime(cache_file)) / 3600
        if age_hours < CONFIG["cache_ttl_hours"]:
            try:
                df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                if all(c in df.columns for c in CACHE_COLUMNS) and len(df) > 0:
                    return df[CACHE_COLUMNS], False
            except Exception:
                pass  # 快取損毀就重新下載

    df = None
    for attempt in range(CONFIG["retries"] + 1):
        try:
            df = fetch_from_yahoo(ticker)
            break
        except Exception as e:
            if attempt == CONFIG["retries"]:
                print(f"下載失敗 {ticker}: {e}")
            else:
                time.sleep(1.5 * (attempt + 1))

    time.sleep(CONFIG["request_sleep"])

    if df is None or len(df) < CONFIG["min_bars"]:
        return None, True

    df.to_csv(cache_file)
    return df, True


def prepare_prices(df):
    """加上還原價欄位(A 開頭)。最新一日因子為 1,所以還原價等同「以今日價格尺度」"""
    df = df.copy()
    f = df["AdjFactor"]
    df["AOpen"] = df["Open"] * f
    df["AHigh"] = df["High"] * f
    df["ALow"] = df["Low"] * f
    df["AClose"] = df["Close"] * f
    return df


# ==================================================
# 三、道氏趨勢 + 上升波段(供黃金切割使用)
# ==================================================

def calculate_dow_trend(df):
    """
    以還原價的高低點判斷趨勢。
    波段高低點要等右側 pivot_right 根 K 棒收完才承認,不偷看未來。

    DowTrend: 1=多頭(高低點同步墊高)  -1=空頭  0=盤整/未確認
    SwingHigh/SwingLow: 最近一段「上升波段」的高點與其前一個低點
    """
    df = df.copy()

    left = CONFIG["pivot_left"]
    right = CONFIG["pivot_right"]

    highs = df["AHigh"].to_numpy()
    lows = df["ALow"].to_numpy()
    n = len(df)

    pivot_highs = []  # (位置, 價格)
    pivot_lows = []

    trend = np.zeros(n, dtype=int)
    sw_high = np.full(n, np.nan)
    sw_low = np.full(n, np.nan)
    sw_high_idx = np.full(n, -1)
    sw_low_idx = np.full(n, -1)

    current_trend = 0
    cur = (np.nan, np.nan, -1, -1)

    for i in range(n):
        p = i - right
        changed = False

        if p >= left:
            hw = highs[p - left:p + right + 1]
            lw = lows[p - left:p + right + 1]

            if highs[p] == hw.max() and np.sum(hw == highs[p]) == 1:
                pivot_highs.append((p, highs[p]))
                changed = True

            if lows[p] == lw.min() and np.sum(lw == lows[p]) == 1:
                pivot_lows.append((p, lows[p]))
                changed = True

        if changed:
            if len(pivot_highs) >= 2 and len(pivot_lows) >= 2:
                h1, h2 = pivot_highs[-2][1], pivot_highs[-1][1]
                l1, l2 = pivot_lows[-2][1], pivot_lows[-1][1]

                if h2 > h1 and l2 > l1:
                    current_trend = 1
                elif h2 < h1 and l2 < l1:
                    current_trend = -1
                else:
                    current_trend = 0

            # 最近一個高點,以及「在它之前」的最近一個低點 = 上升波段
            if pivot_highs:
                h_idx, h_px = pivot_highs[-1]
                k = len(pivot_lows) - 1
                while k >= 0 and pivot_lows[k][0] >= h_idx:
                    k -= 1
                if k >= 0 and pivot_lows[k][1] < h_px:
                    cur = (h_px, pivot_lows[k][1], h_idx, pivot_lows[k][0])
                else:
                    cur = (np.nan, np.nan, -1, -1)

        trend[i] = current_trend
        sw_high[i], sw_low[i], sw_high_idx[i], sw_low_idx[i] = cur

    df["DowTrend"] = trend
    df["SwingHigh"] = sw_high
    df["SwingLow"] = sw_low
    df["SwingHighIdx"] = sw_high_idx
    df["SwingLowIdx"] = sw_low_idx

    return df


# ==================================================
# 四、均線、量能與交易訊號(使用 CONFIG 參數)
# ==================================================

def calculate_signals(df):
    df = df.copy()

    c = df["AClose"]

    df["MA_S"] = c.rolling(CONFIG["ma_short"]).mean()
    df["MA_M"] = c.rolling(CONFIG["ma_mid"]).mean()
    df["MA_L"] = c.rolling(CONFIG["ma_long"]).mean()

    df["VOL_AVG"] = df["Volume"].rolling(CONFIG["volume_period"]).mean()
    df["VolumeRatio"] = df["Volume"] / df["VOL_AVG"]

    df["BullishMA"] = (c > df["MA_S"]) & (df["MA_S"] > df["MA_M"]) & (df["MA_M"] > df["MA_L"])
    df["PriceUp"] = c > c.shift(1)
    df["VolumeConfirm"] = df["VolumeRatio"] >= CONFIG["volume_ratio"]

    df["DowBullish"] = df["DowTrend"] == 1
    df["DowBearish"] = df["DowTrend"] == -1

    # 買進:道氏多頭 + 均線多頭排列 + 收紅 + 爆量
    df["BuySetup"] = (
        df["DowBullish"] & df["BullishMA"] & df["PriceUp"] & df["VolumeConfirm"]
    )

    # 賣出:道氏空頭,或短均線向下穿越中均線
    cross_down = (df["MA_S"] < df["MA_M"]) & (df["MA_S"].shift(1) >= df["MA_M"].shift(1))
    df["SellSetup"] = df["DowBearish"] | cross_down

    df["BuySignal"] = first_true(df["BuySetup"])
    df["SellSignal"] = first_true(df["SellSetup"])

    return df


# ==================================================
# 五、黃金切割(費波那契回檔)
# ==================================================

def calculate_fibonacci(df):
    """
    以最近一段上升波段(SwingLow → SwingHigh)計算:
      FibRetrace  回檔比例(0=在高點, 1=回到低點, 負數=已創新高)
      FibZoneTop / FibZoneBottom  黃金區上、下緣價位
      Target1 / Target2           延伸目標價
    """
    df = df.copy()

    hi = df["SwingHigh"]
    lo = df["SwingLow"]
    rng = hi - lo
    valid = (df["SwingLowIdx"] >= 0) & (rng > 0)

    z_top, z_bot = CONFIG["fib_zone"]
    e1, e2 = CONFIG["fib_extensions"]

    df["FibRetrace"] = np.where(valid, (hi - df["AClose"]) / rng, np.nan)
    df["FibZoneTop"] = np.where(valid, hi - rng * z_top, np.nan)
    df["FibZoneBottom"] = np.where(valid, hi - rng * z_bot, np.nan)
    df["Target1"] = np.where(valid, lo + rng * e1, np.nan)
    df["Target2"] = np.where(valid, lo + rng * e2, np.nan)

    df["InGoldenZone"] = (
        valid
        & (df["FibRetrace"] >= z_top)
        & (df["FibRetrace"] <= z_bot)
    )

    # 回檔承接:道氏多頭 + 在黃金區 + 收紅 + 站在長均線之上
    df["FibBuySetup"] = (
        df["DowBullish"]
        & df["InGoldenZone"]
        & df["PriceUp"]
        & (df["AClose"] > df["MA_L"])
    )
    df["FibBuySignal"] = first_true(df["FibBuySetup"])

    # 跌破波段低點 = 回檔失敗
    df["FibBreak"] = valid & (df["AClose"] < lo)

    return df


def analyze_prices(df):
    """完整技術分析流程"""
    df = prepare_prices(df)
    df = calculate_dow_trend(df)
    df = calculate_signals(df)
    df = calculate_fibonacci(df)
    return df


# ==================================================
# 六、除息填息分析(使用未還原價)
# ==================================================

def analyze_dividends(df):
    """
    逐次除息計算填息情形。
    填息 = 價格回到「除息前一日收盤價」。除息當天就達標算 1 天。
    僅處理現金股利;股票股利(除權)不在此計算。
    """
    close = df["Close"].to_numpy()
    high = df["High"].to_numpy()
    div = df["Dividends"].to_numpy()
    idx = df.index

    series = close if CONFIG["fill_use_close"] else high

    rows = []
    for p in np.where(div > 0)[0]:
        if p == 0:
            continue

        pre_close = close[p - 1]
        cash = div[p]

        hit = np.where(series[p:] >= pre_close)[0]
        filled = len(hit) > 0

        rows.append({
            "除息日": idx[p].date(),
            "現金股利": float(cash),
            "除息前收盤": float(pre_close),
            "參考價": float(pre_close - cash),
            "殖利率%": float(cash / pre_close * 100),
            "是否填息": filled,
            "填息天數": int(hit[0]) + 1 if filled else None,
            "距填息%": None if filled else float((pre_close - close[-1]) / pre_close * 100),
            "距今交易日": int(len(df) - 1 - p),
        })

    return pd.DataFrame(rows)


def summarize_dividends(events):
    empty = {
        "除息次數": 0,
        "歷史填息率%": None,
        "平均填息天數": None,
        "最近除息日": None,
        "最近現金股利": None,
        "最近殖利率%": None,
        "最近已填息": None,
        "距填息%": None,
        "距除息交易日": None,
    }

    if events is None or events.empty:
        return empty

    done = events[events["是否填息"]]
    last = events.iloc[-1]

    return {
        "除息次數": int(len(events)),
        "歷史填息率%": rnd(events["是否填息"].mean() * 100, 1),
        "平均填息天數": rnd(done["填息天數"].mean(), 1) if not done.empty else None,
        "最近除息日": str(last["除息日"]),
        "最近現金股利": rnd(last["現金股利"], 3),
        "最近殖利率%": rnd(last["殖利率%"], 2),
        "最近已填息": bool(last["是否填息"]),
        "距填息%": rnd(last["距填息%"], 2),
        "距除息交易日": int(last["距今交易日"]),
    }


# ==================================================
# 七、選股分數與標籤
# ==================================================

def calculate_score(row):
    score = 0

    if row["DowTrend"] == 1:
        score += 40
    if row["BullishMA"]:
        score += 30

    if row["VolumeRatio"] >= CONFIG["volume_ratio"]:
        score += 20
    elif row["VolumeRatio"] >= CONFIG["volume_ratio_weak"]:
        score += 10

    if row["PriceUp"]:
        score += 10

    return score


def classify_signal(last):
    """標籤依優先順序判斷,每個分支都可能出現"""
    if last["BuySetup"]:
        return "買進觀察"
    if last["FibBuySetup"]:
        return "黃金切割回檔"
    if last["DowTrend"] == -1:
        return "空頭(避開)"
    if last["SellSetup"]:
        return "轉弱(均線死叉)"
    if last["DowTrend"] == 1:
        return "多頭持續"
    return "觀望"


# ==================================================
# 八、單檔分析與全市場掃描
# ==================================================

def analyze_one(stock, raw_df):
    df = analyze_prices(raw_df)
    last = df.iloc[-1]

    # 停牌或已下市
    stale = (pd.Timestamp.now().normalize() - df.index[-1]).days
    if stale > CONFIG["max_stale_days"]:
        return None

    if pd.isna(last["MA_L"]) or pd.isna(last["VOL_AVG"]):
        return None

    avg_lots = last["VOL_AVG"] / CONFIG["lot_size"]

    if last["Close"] < CONFIG["min_price"] or avg_lots < CONFIG["min_avg_lots"]:
        return None

    fill = summarize_dividends(analyze_dividends(df))

    return {
        "代碼": stock["code"],
        "名稱": stock["name"],
        "市場": stock["market"],
        "資料日期": str(df.index[-1].date()),

        "收盤價": rnd(last["Close"]),
        ma_name("ma_short"): rnd(last["MA_S"]),
        ma_name("ma_mid"): rnd(last["MA_M"]),
        ma_name("ma_long"): rnd(last["MA_L"]),
        "量比": rnd(last["VolumeRatio"]),
        "均量(張)": int(avg_lots),

        "道氏趨勢": int(last["DowTrend"]),
        "選股分數": calculate_score(last),
        "交易訊號": classify_signal(last),
        "買進觸發": bool(last["BuySignal"]),
        "賣出觸發": bool(last["SellSignal"]),

        "波段低點": rnd(last["SwingLow"]),
        "波段高點": rnd(last["SwingHigh"]),
        "回檔比例": rnd(last["FibRetrace"], 3),
        "黃金區上緣": rnd(last["FibZoneTop"]),
        "黃金區下緣": rnd(last["FibZoneBottom"]),
        "目標價1": rnd(last["Target1"]),
        "目標價2": rnd(last["Target2"]),
        "在黃金區": bool(last["InGoldenZone"]),
        "黃金切割觸發": bool(last["FibBuySignal"]),

        "除息次數": fill["除息次數"],
        "歷史填息率%": fill["歷史填息率%"],
        "平均填息天數": fill["平均填息天數"],
        "最近除息日": fill["最近除息日"],
        "最近現金股利": fill["最近現金股利"],
        "最近殖利率%": fill["最近殖利率%"],
        "最近已填息": fill["最近已填息"],
        "距填息%": fill["距填息%"],
        "距除息交易日": fill["距除息交易日"],

        "_ticker": stock["ticker"],
    }


def scan_market(universe, refresh=False, limit=None):
    if limit:
        universe = universe.head(int(limit))

    print("掃描股票數:", len(universe))

    rows = []
    for _, stock in tqdm(universe.iterrows(), total=len(universe)):
        df, _ = load_stock(stock["ticker"], refresh=refresh)
        if df is None:
            continue

        try:
            row = analyze_one(stock, df)
        except Exception as e:
            print(f"分析失敗 {stock['ticker']}: {e}")
            continue

        if row is not None:
            rows.append(row)

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    return result.sort_values(
        ["選股分數", "量比"], ascending=[False, False]
    ).reset_index(drop=True)


# ==================================================
# 九、回測
# ==================================================

def backtest(df, strategy="momentum"):
    """
    df 需為 analyze_prices() 的輸出。
    strategy:
      "momentum": 進場 BuySignal;出場 SellSetup
      "fib"     : 進場 FibBuySignal;出場 SellSetup 或跌破波段低點
    訊號於收盤後產生,隔一交易日開盤成交。
    報酬以還原價計算(已含除息影響,不含股利所得稅)。
    """
    commission = CONFIG["commission"]
    tax = CONFIG["tax"]
    slip = CONFIG["slippage"]

    if strategy == "fib":
        entry = df["FibBuySignal"].fillna(False).to_numpy()
        exit_ = (df["SellSetup"] | df["FibBreak"]).fillna(False).to_numpy()
    else:
        entry = df["BuySignal"].fillna(False).to_numpy()
        exit_ = df["SellSetup"].fillna(False).to_numpy()

    opens = df["AOpen"].to_numpy()
    closes = df["AClose"].to_numpy()
    idx = df.index
    n = len(df)

    equity = 1.0
    curve = np.ones(n)
    pos = False
    ref = 0.0
    entry_price = 0.0
    entry_date = None
    pending = None
    trades = []

    for i in range(n):
        # 先執行前一日收盤後產生的委託(今日開盤成交)
        if pending == "buy" and not pos:
            entry_price = opens[i] * (1 + slip)
            entry_date = idx[i]
            equity *= (1 - commission)
            ref = entry_price
            pos = True

        elif pending == "sell" and pos:
            exit_price = opens[i] * (1 - slip)
            equity *= exit_price / ref
            equity *= (1 - commission - tax)
            ret = (exit_price / entry_price) * (1 - commission) * (1 - commission - tax) - 1
            trades.append({
                "買進日期": entry_date, "賣出日期": idx[i],
                "買進價": entry_price, "賣出價": exit_price,
                "報酬率": ret, "持有交易日": int(idx.get_loc(idx[i]) - idx.get_loc(entry_date)),
                "狀態": "已平倉",
            })
            pos = False

        pending = None

        # 持有中:每日以收盤價評價
        if pos:
            equity *= closes[i] / ref
            ref = closes[i]

        curve[i] = equity

        # 收盤後產生訊號
        if not pos and entry[i]:
            pending = "buy"
        elif pos and exit_[i]:
            pending = "sell"

    # 期末仍持有:以最後收盤價結算(扣賣出成本)
    if pos:
        exit_price = closes[-1] * (1 - slip)
        ret = (exit_price / entry_price) * (1 - commission) * (1 - commission - tax) - 1
        trades.append({
            "買進日期": entry_date, "賣出日期": idx[-1],
            "買進價": entry_price, "賣出價": exit_price,
            "報酬率": ret, "持有交易日": int(n - 1 - idx.get_loc(entry_date)),
            "狀態": "未平倉(期末估值)",
        })

    trades_df = pd.DataFrame(trades)
    buy_hold = (closes[-1] / closes[0] - 1) * 100

    curve_s = pd.Series(curve, index=idx)
    max_dd = (curve_s / curve_s.cummax() - 1).min() * 100

    if trades_df.empty:
        summary = {
            "交易次數": 0, "勝率%": None, "平均報酬%": None, "累積報酬%": 0.0,
            "最大回撤%": rnd(max_dd), "平均持有天數": None,
            "最大單筆%": None, "最差單筆%": None, "買進持有%": rnd(buy_hold),
        }
        return {"summary": summary, "trades": trades_df}

    r = trades_df["報酬率"]
    summary = {
        "交易次數": int(len(trades_df)),
        "勝率%": rnd((r > 0).mean() * 100, 1),
        "平均報酬%": rnd(r.mean() * 100),
        "累積報酬%": rnd(((1 + r).prod() - 1) * 100),
        "最大回撤%": rnd(max_dd),
        "平均持有天數": rnd(trades_df["持有交易日"].mean(), 1),
        "最大單筆%": rnd(r.max() * 100),
        "最差單筆%": rnd(r.min() * 100),
        "買進持有%": rnd(buy_hold),
    }
    return {"summary": summary, "trades": trades_df}


STRATEGY_LABEL = {"momentum": "動能(道氏+均線+量)", "fib": "黃金切割回檔"}


def run_backtests(targets, refresh=False):
    """
    targets: list of dict(code, name, ticker)
    回傳 (摘要 DataFrame, 明細 DataFrame)
    """
    summaries, details = [], []

    for t in tqdm(targets, desc="回測"):
        raw, _ = load_stock(t["ticker"], refresh=refresh)
        if raw is None:
            continue

        df = analyze_prices(raw)

        for strategy in ("momentum", "fib"):
            res = backtest(df, strategy)
            summaries.append({
                "代碼": t["code"], "名稱": t["name"],
                "策略": STRATEGY_LABEL[strategy], **res["summary"],
            })
            if not res["trades"].empty:
                tr = res["trades"].copy()
                tr.insert(0, "策略", STRATEGY_LABEL[strategy])
                tr.insert(0, "名稱", t["name"])
                tr.insert(0, "代碼", t["code"])
                details.append(tr)

    summary_df = pd.DataFrame(summaries)
    detail_df = pd.concat(details, ignore_index=True) if details else pd.DataFrame()
    return summary_df, detail_df


# ==================================================
# 十、輸出報告
# ==================================================

def _autofit(writer, sheet_name, df):
    ws = writer.sheets[sheet_name]
    for i, col in enumerate(df.columns, start=1):
        width = max(len(str(col)) * 2, 8)
        sample = df[col].astype(str).head(200)
        if not sample.empty:
            width = max(width, min(sample.str.len().max() + 2, 28))
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    ws.freeze_panes = "C2"


def export_report(results, bt_summary=None, bt_detail=None):
    if results.empty:
        print("沒有符合條件的股票")
        return

    today = datetime.now().strftime("%Y%m%d")
    out = results.drop(columns=["_ticker"], errors="ignore")

    csv_path = os.path.join(CONFIG["output_dir"], f"stock_signals_{today}.csv")
    xlsx_path = os.path.join(CONFIG["output_dir"], f"stock_signals_{today}.xlsx")

    out.to_csv(csv_path, index=False, encoding="utf-8-sig")

    buy_labels = ("買進觀察", "黃金切割回檔")
    buy_list = out[out["交易訊號"].isin(buy_labels)]
    dividend_list = out[(out["最近已填息"] == False) & out["最近除息日"].notna()]  # noqa: E712

    params = pd.DataFrame(
        [(k, str(v)) for k, v in CONFIG.items()], columns=["參數", "值"]
    )

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        sheets = [
            ("選股結果", out),
            ("買進訊號", buy_list),
            ("未填息", dividend_list),
        ]
        if bt_summary is not None and not bt_summary.empty:
            sheets.append(("回測摘要", bt_summary))
        if bt_detail is not None and not bt_detail.empty:
            d = bt_detail.copy()
            for c in ("買進日期", "賣出日期"):
                d[c] = pd.to_datetime(d[c]).dt.date
            sheets.append(("回測明細", d))
        sheets.append(("參數", params))

        for name, frame in sheets:
            frame.to_excel(writer, sheet_name=name, index=False)
            _autofit(writer, name, frame)

    show_cols = [
        "代碼", "名稱", "收盤價", "量比", "道氏趨勢", "選股分數",
        "交易訊號", "回檔比例", "歷史填息率%", "最近已填息",
    ]
    print("\n========== 選股結果(前 30) ==========")
    print(out[show_cols].head(30).to_string(index=False))

    print("\n買進訊號檔數:", len(buy_list))
    print("CSV  :", csv_path)
    print("Excel:", xlsx_path)


def print_backtest(summary_df):
    if summary_df is None or summary_df.empty:
        print("沒有回測結果")
        return
    print("\n========== 回測摘要 ==========")
    print(summary_df.to_string(index=False))
    print("\n註:回測使用還原價,未計股利所得稅;單檔、少量樣本的結果不具代表性。")


# ==================================================
# 十一、執行
# ==================================================

def parse_args():
    p = argparse.ArgumentParser(description="台股量化選股系統")
    p.add_argument("--refresh", action="store_true", help="忽略快取,重新下載資料")
    p.add_argument("--limit", type=int, default=None, help="只掃描前 N 檔(測試用)")
    p.add_argument("--backtest", nargs="+", metavar="CODE", help="只對指定股票代號回測,例如 2330 2317")
    p.add_argument("--no-backtest", action="store_true", help="掃描後不自動回測前 N 名")
    return p.parse_args()


def main():
    args = parse_args()

    os.makedirs(CONFIG["cache_dir"], exist_ok=True)
    os.makedirs(CONFIG["output_dir"], exist_ok=True)

    print("=" * 60)
    print("台股量化選股系統:道氏 + 均線 + 量能 + 黃金切割 + 除息填息")
    print("=" * 60)

    universe = get_stock_universe()

    # ---- 指定個股回測 ----
    if args.backtest:
        targets = []
        for code in args.backtest:
            m = universe[universe["code"] == code]
            if m.empty:
                print(f"找不到代號 {code}(僅支援上市、上櫃 4 碼普通股)")
                continue
            s = m.iloc[0]
            targets.append({"code": s["code"], "name": s["name"], "ticker": s["ticker"]})

        summary_df, _ = run_backtests(targets, refresh=args.refresh)
        print_backtest(summary_df)
        return

    # ---- 全市場掃描 ----
    results = scan_market(universe, refresh=args.refresh, limit=args.limit)

    if results.empty:
        print("沒有取得有效選股資料")
        return

    bt_summary, bt_detail = None, None

    if not args.no_backtest and CONFIG["backtest_top_n"] > 0:
        top = results.head(CONFIG["backtest_top_n"])
        targets = [
            {"code": r["代碼"], "name": r["名稱"], "ticker": r["_ticker"]}
            for _, r in top.iterrows()
        ]
        bt_summary, bt_detail = run_backtests(targets)
        print_backtest(bt_summary)

    export_report(results, bt_summary, bt_detail)
    print("\n掃描完成!")


if __name__ == "__main__":
    main()