#!/usr/bin/env python3
"""
抓取臺灣期貨交易所（TAIFEX）所有「股票期貨」商品清單。

資料來源：期交所「保證金一覽表－股票類」
    https://www.taifex.com.tw/cht/5/stockMargining
該頁的 (一)標的為股票、(二)標的為受益憑證(ETF) 兩張表即為目前上市的全部股票期貨契約。

輸出（預設放在目前資料夾）：
    stock_futures_all.csv   所有契約（含小型契約、ETF 期貨），一個契約一列
    stock_futures_underlying.csv  以「標的證券代號」去重後的清單（哪些股票有股期）

用法：
    pip install requests beautifulsoup4
    python taifex_stock_futures.py                # 下載並輸出 CSV
    python taifex_stock_futures.py 2330 鴻海      # 另外依代號/名稱關鍵字查詢
"""
import csv
import re
import sys
from collections import OrderedDict

import requests
from bs4 import BeautifulSoup

URL = "https://www.taifex.com.tw/cht/5/stockMargining"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; taifex-ssf-list/1.0)"}

# 輸出欄位
FIELDS = [
    "類型", "英文代碼", "標的證券代號", "中文簡稱", "標的證券",
    "契約規格", "保證金級距", "結算保證金", "維持保證金", "原始保證金",
]


def fetch_html(url: str = URL) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def _clean(text: str) -> str:
    """去掉所有空白（表頭常夾 <br>）。"""
    return re.sub(r"\s+", "", text or "")


def _cell(td) -> str:
    return re.sub(r"\s+", " ", td.get_text(" ", strip=True)).strip()


def parse_stock_futures(html: str):
    """回傳股票期貨契約的 list[dict]。"""
    soup = BeautifulSoup(html, "html.parser")
    rows_out = []

    for table in soup.find_all("table"):
        first_tr = table.find("tr")
        if first_tr is None:
            continue
        header = [_clean(c.get_text()) for c in first_tr.find_all(["th", "td"])]
        # 只要「股票期貨」表，排除「股票選擇權」表
        if "股票期貨英文代碼" not in header:
            continue

        # (一)股票：有「保證金所屬級距」欄；(二)ETF：保證金是固定金額
        is_stock = "保證金所屬級距" in header
        idx = {name: i for i, name in enumerate(header)}

        for tr in table.find_all("tr")[1:]:
            cells = [_cell(td) for td in tr.find_all(["td", "th"])]
            if len(cells) < len(header) or not cells[idx["股票期貨英文代碼"]]:
                continue

            def get(col):
                return cells[idx[col]] if col in idx else ""

            name = get("股票期貨中文簡稱")
            row = {
                "類型": "股票" if is_stock else "ETF",
                "英文代碼": get("股票期貨英文代碼"),
                "標的證券代號": get("股票期貨標的證券代號"),
                "中文簡稱": name,
                "標的證券": get("股票期貨標的證券"),
                "契約規格": "小型(100股/1000單位)" if "小型" in name
                            else ("標準(2000股)" if is_stock else "標準(10000單位)"),
            }
            if is_stock:
                row["保證金級距"] = get("保證金所屬級距")
                row["結算保證金"] = get("結算保證金適用比例")
                row["維持保證金"] = get("維持保證金適用比例")
                row["原始保證金"] = get("原始保證金適用比例")
            else:
                row["保證金級距"] = ""
                row["結算保證金"] = get("結算保證金")
                row["維持保證金"] = get("維持保證金")
                row["原始保證金"] = get("原始保證金")
            rows_out.append(row)

    return rows_out


def build_underlying(rows):
    """依標的證券代號去重，標示是否同時有標準/小型契約。"""
    groups = OrderedDict()
    for r in rows:
        code = r["標的證券代號"]
        g = groups.setdefault(code, {
            "類型": r["類型"],
            "標的證券代號": code,
            "標的證券": r["標的證券"],
            "標準契約代碼": "",
            "小型契約代碼": "",
            "標準契約簡稱": "",
        })
        if "小型" in r["中文簡稱"]:
            g["小型契約代碼"] = r["英文代碼"]
        else:
            g["標準契約代碼"] = r["英文代碼"]
            g["標準契約簡稱"] = r["中文簡稱"]
    return list(groups.values())


def write_csv(path, rows, fields):
    # utf-8-sig：用 Excel 直接開啟中文不會亂碼
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def search(underlying, keywords):
    for kw in keywords:
        hits = [u for u in underlying
                if kw.lower() in u["標的證券代號"].lower()
                or kw in u["標的證券"] or kw in u["標準契約簡稱"]]
        if hits:
            for h in hits:
                mini = f"，小型契約 {h['小型契約代碼']}" if h["小型契約代碼"] else ""
                print(f"✔ {kw}：{h['標的證券代號']} {h['標的證券']}"
                      f"（標準契約 {h['標準契約代碼']}{mini}）")
        else:
            print(f"✘ {kw}：目前沒有找到股票期貨")


def main():
    html = fetch_html()
    rows = parse_stock_futures(html)
    if not rows:
        sys.exit("解析不到任何資料：網頁版型可能改版，請檢查表頭欄位名稱。")

    underlying = build_underlying(rows)
    write_csv("stock_futures_all.csv", rows, FIELDS)
    write_csv("stock_futures_underlying.csv", underlying,
              ["類型", "標的證券代號", "標的證券", "標準契約代碼", "標準契約簡稱", "小型契約代碼"])

    n_stock = sum(1 for u in underlying if u["類型"] == "股票")
    n_etf = sum(1 for u in underlying if u["類型"] == "ETF")
    print(f"共 {len(rows)} 個契約；標的 {len(underlying)} 檔（股票 {n_stock}、ETF {n_etf}）")
    print("已輸出：stock_futures_all.csv、stock_futures_underlying.csv")

    if len(sys.argv) > 1:
        print()
        search(underlying, sys.argv[1:])


if __name__ == "__main__":
    main()