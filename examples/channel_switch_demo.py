# -*- coding: utf-8 -*-
r"""大QMT ↔ miniQMT 无感切换 —— 使用方接入演示。

演示使用方（如 QmtQuant）如何一行代码不改，接入 xqshare 的无感切换：

  - 使用方代码 = 标准 xqshare 客户端用法（connect + xtdata），照旧
  - 只需配 host/port + 凭证（.env 或参数，凭证要匹配 server 端 clients.yaml）
  - server 端自动路由：miniQMT 可用走 mini，不可用自动切大QMT（bigqmt）
  - 新增 get_channel_status() 诊断当前通道（可选，非必需）

使用方 .env 配置示例（连国金 server 18813）：
  XQSHARE_REMOTE_HOST=192.168.31.233
  XQSHARE_REMOTE_PORT=18813
  XQSHARE_CLIENT_ID=client-standard
  XQSHARE_CLIENT_SECRET=xqshare-default-secret

注意：使用方（客户端）只需装 xqshare 本体，不需要 bigqmt extra（那是
server 端依赖 xtquant-big-convert）。
"""
import os

# 使用方凭证（从 .env 或参数来；这里 setdefault 等价于 .env 里的 client-standard）
os.environ.setdefault("XQSHARE_CLIENT_ID", "client-standard")
os.environ.setdefault("XQSHARE_CLIENT_SECRET", "xqshare-default-secret")

from xqshare import connect


def main():
    # 使用方代码（与接入 miniQMT 时完全一样）
    xt = connect(host="192.168.31.233", port=18813)

    # 1. 诊断：当前通道（新增接口，可选）
    status = xt.get_channel_status()
    print("[1] 通道状态:", status)

    # 2. 行情（使用方原有代码，自动走通道）
    ticks = xt.xtdata.get_full_tick(["000001.SZ"])
    print("[2] get_full_tick:", type(ticks), list(ticks.keys()) if isinstance(ticks, dict) else ticks)
    if isinstance(ticks, dict) and ticks:
        c = list(ticks.keys())[0]
        print("    lastPrice:", ticks[c].get("lastPrice"))

    # 3. 历史数据（自动走通道）
    df = xt.xtdata.get_market_data_ex(["close"], ["000001.SZ"], "1d", count=3)
    print("[3] get_market_data_ex:", type(df))
    if isinstance(df, dict):
        for k, v in df.items():
            print("    %s:\n%s" % (k, v.tail(2) if hasattr(v, "tail") else v))

    # 4. 板块（自动走通道）
    stocks = xt.xtdata.get_stock_list_in_sector("沪深A股")
    print("[4] 板块成分股数量:", len(stocks) if isinstance(stocks, list) else type(stocks))

    xt.close()
    print("DONE")


if __name__ == "__main__":
    main()
