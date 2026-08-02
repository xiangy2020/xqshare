#!/usr/bin/env python3
"""
交易事件推送回调示例

展示如何通过 xqshare 远程 trader 订阅账号，并接收 on_stock_* 推送事件。
服务端会把 xtquant 回调对象序列化后推送到远端客户端。

注意:
  - 需要先在 Windows 上安装并运行 QMT 交易客户端
  - 服务端必须能访问真实 xtquant 才能产生推送事件

环境变量:
  XQSHARE_REMOTE_HOST     - 服务端地址
  XQSHARE_REMOTE_PORT     - 服务端端口
  XQSHARE_CLIENT_SECRET   - 认证密钥
  QMT_ACCOUNT_ID          - 资金账号
  QMT_USERDATA_PATH       - QMT客户端 userdata_mini 目录路径

使用示例:
    export XQSHARE_REMOTE_HOST="21.214.136.216"
    export QMT_ACCOUNT_ID="12345678"
    export QMT_USERDATA_PATH="C:\\QMT\\userdata_mini"
    python examples/trader_callbacks.py
"""

import argparse
import os
import sys
import time

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xqshare import XtQuantRemote


ACCOUNT_TYPES = {
    "STOCK": "股票",
    "CREDIT": "信用",
    "FUTURE": "期货",
    "HUGANGTONG": "沪港通",
    "SHENGANGTONG": "深港通",
}


def on_stock_asset(event_name: str, account_id: str, payload: dict):
    """账户资产变动回调

    payload 是服务端序列化后的 dict，字段与 xtquant 的 XtAsset 一致，例如：
    account_id, cash, frozen_cash, market_value, total_asset
    """
    print(f"\n[资产] account={account_id} cash={payload.get('cash')} total={payload.get('total_asset')}")


def on_stock_order(event_name: str, account_id: str, payload: dict):
    print(f"\n[委托] account={account_id} order_id={payload.get('order_id')} "
          f"code={payload.get('stock_code')} status={payload.get('order_status')}")


def on_stock_trade(event_name: str, account_id: str, payload: dict):
    print(f"\n[成交] account={account_id} trade_id={payload.get('traded_id')} "
          f"code={payload.get('stock_code')} volume={payload.get('traded_volume')}")


def on_stock_position(event_name: str, account_id: str, payload: dict):
    print(f"\n[持仓] account={account_id} code={payload.get('stock_code')} "
          f"volume={payload.get('volume')} can_use={payload.get('can_use_volume')}")


def main():
    parser = argparse.ArgumentParser(
        description="订阅交易账号并接收远端推送事件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
说明:
  1. 服务端会在本地注册 _TraderEventCallback 收集 xtquant 事件
  2. 客户端调用 trader.subscribe(account) 时自动把本地接收器注册到服务端
  3. 事件发生时服务端通过 RPyC 反向调用推送到远端客户端
        """
    )
    parser.add_argument("--host", help="服务端地址")
    parser.add_argument("--port", type=int, help="服务端端口")
    parser.add_argument("--secret", help="认证密钥")
    parser.add_argument("--account-id", help="资金账号")
    parser.add_argument("--account-type", default="STOCK", choices=list(ACCOUNT_TYPES.keys()))
    parser.add_argument("--path", help="QMT 客户端 userdata_mini 目录路径")

    args = parser.parse_args()

    account_id = args.account_id or os.environ.get("QMT_ACCOUNT_ID")
    userdata_path = args.path or os.environ.get("QMT_USERDATA_PATH")

    if not account_id:
        print("错误: 必须提供资金账号（--account-id 或 QMT_ACCOUNT_ID）")
        return 1
    if not userdata_path:
        print("错误: 必须提供 userdata_mini 路径（--path 或 QMT_USERDATA_PATH）")
        return 1

    print(f"正在连接服务端...")
    xt = XtQuantRemote(
        host=args.host,
        port=args.port,
        client_secret=args.secret,
    )

    try:
        trader = xt.create_trader(userdata_path)
        trader.start()

        account = xt.xttype.StockAccount(account_id, args.account_type)

        # 注册远端回调。注意：事件是在调用 subscribe 之后才实际路由到客户端。
        trader.register_trader_callback("on_stock_asset", on_stock_asset)
        trader.register_trader_callback("on_stock_order", on_stock_order)
        trader.register_trader_callback("on_stock_trade", on_stock_trade)
        trader.register_trader_callback("on_stock_position", on_stock_position)

        connect_result = trader.connect()
        if connect_result != 0:
            print(f"连接失败: {connect_result}")
            return 1

        # 订阅账号，这一步会把客户端的 RemoteTraderCallback 注册到服务端事件路由器
        subscribe_result = trader.subscribe(account)
        print(f"订阅账号结果: {subscribe_result}")

        print("\n等待推送事件... 按 Ctrl+C 退出")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n退出")

    finally:
        try:
            trader.stop()
        except Exception:
            pass
        xt.close()


if __name__ == "__main__":
    sys.exit(main())
