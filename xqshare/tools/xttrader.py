#!/usr/bin/env python3
"""xttrader - 交易命令行工具

支持 XtQuantTrader 的交易操作。

参数规则:
    - 工具参数（--host, --port, --account-id 等）必须放在 command 之前
    - API 函数参数放在 command 之后
    - account 参数会自动补充，无需手动传递

使用示例:
    # 查询持仓
    xttrader  --userdata-path "C:\\\\QMT\\\\userdata_mini" query_stock_positions

    # 查询资产
    xttrader  query_stock_asset

    # 下单
    xttrader  order_stock --stock-code "000001.SZ" --order-type 23 --order-volume 100 --price-type 11 --price 10.0

    # 查询当日委托（根据接口可用情况）
    xttrader  query_stock_orders

    # 查询当日成交
    xttrader  query_stock_trades

    # 撤单（order_id 替换为实际返回的订单号）
    xttrader  order_cancel --order-id 1082130745
"""
import sys
import os
import argparse
from .common import (
    create_client, parse_kv_args, preprocess_params,
    format_output, add_global_args, add_trader_args, create_trader,
    extract_global_args, ENV_FORMAT
)


def main():
    parser = argparse.ArgumentParser(
        prog="xttrader",
        description="xtquant 交易命令行工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
参数规则:
  工具参数 (--host, --port, --account-id 等) 必须放在 command 之前
  API 函数参数放在 command 之后
  account 参数会自动补充，无需手动传递

限制:
  不支持以 subscribe/register 开头的命令（订阅/回调功能）
  不支持 callback 参数（回调功能需要使用 Python API）

常用命令:
  query_stock_positions  - 查询持仓
  query_stock_asset      - 查询资产
  order_stock            - 下单
  order_cancel           - 撤单

示例:
  xttrader  query_stock_positions
  xttrader  order_stock --stock-code "000001.SZ" --order-type 23 --order-volume 100
        """
    )
    add_global_args(parser)
    add_trader_args(parser)
    parser.add_argument("command", help="API函数名")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="函数参数 (--key value)")

    args = parser.parse_args()

    # 补充环境变量默认值
    output_format = args.output_format or os.environ.get(ENV_FORMAT, "text")

    # 提取后置的全局参数（支持放在 command 之后）
    args.args, global_overrides = extract_global_args(args.args)
    if global_overrides:
        if 'compact' in global_overrides:
            args.compact = True
        if 'verbose' in global_overrides or 'v' in global_overrides:
            args.verbose = True
        if 'output' in global_overrides or 'o' in global_overrides:
            args.output = global_overrides.get('output') or global_overrides.get('o')
        if 'limit' in global_overrides or 'n' in global_overrides:
            args.limit = int(global_overrides.get('limit') or global_overrides.get('n', 0))
        if 'format' in global_overrides or 'f' in global_overrides:
            output_format = global_overrides.get('format') or global_overrides.get('f')

    # 拒绝订阅相关命令
    if args.command.startswith('subscribe') or args.command.startswith('register'):
        print(f"错误: 命令行工具不支持订阅/回调功能 '{args.command}'", file=sys.stderr)
        print("提示: 订阅功能需要回调函数支持，请使用 Python API 或 examples 脚本", file=sys.stderr)
        sys.exit(1)

    with create_client(args.host, args.port, args.secret, args.client_id, quiet=not args.verbose) as xt:
        try:
            trader, account = create_trader(xt, args.userdata_path, args.account_id, args.account_type)
        except Exception as e:
            print(f"错误: {e}", file=sys.stderr)
            sys.exit(1)

        try:
            func = getattr(trader, args.command, None)
            if func is None:
                print(f"错误: 未知命令 '{args.command}'", file=sys.stderr)
                sys.exit(1)

            params = parse_kv_args(args.args)
            params = preprocess_params(params)

            # 检查 callback 参数
            if 'callback' in params:
                print("错误: 命令行工具不支持回调参数 'callback'", file=sys.stderr)
                print("提示: 回调功能需要使用 Python API 或 examples 脚本", file=sys.stderr)
                sys.exit(1)

            # 部分查询命令本身不支持 order_id 参数，先提取用于本地过滤
            filter_order_id = None
            if args.command in ('query_stock_orders', 'query_stock_trades') and 'order_id' in params:
                filter_order_id = params.pop('order_id')

            # 自动添加 account 参数
            params['account'] = account

            result = func(**params)

            # 本地按 order_id 过滤（兼容 dict 和 netref 对象）
            if filter_order_id is not None and isinstance(result, (list, tuple)):
                def _get_order_value(item, names):
                    for name in names:
                        if isinstance(item, dict):
                            if name in item:
                                return item[name]
                        elif hasattr(item, name):
                            try:
                                return getattr(item, name)
                            except Exception:
                                pass
                    return None

                def _match_order(item, oid):
                    candidates = {
                        _get_order_value(item, ('order_id',)),
                        _get_order_value(item, ('m_nOrderID',)),
                    }
                    return any(str(c) == str(oid) for c in candidates if c is not None)

                result = [item for item in result if _match_order(item, filter_order_id)]

            limit = None if args.limit == 0 else args.limit
            format_output(result, limit, args.output, output_format, args.compact)

            # 对下单/撤单命令追加追踪提示
            if args.command == 'order_stock' and isinstance(result, (int, float)):
                print(f"\n# 订单已提交，订单号: {result}")
                print(f"# 查询委托: xttrader query_stock_orders")
                print(f"# 查询成交: xttrader query_stock_trades")
                print(f"# 撤销订单: xttrader order_cancel --order-id {result}")
            elif args.command == 'order_cancel':
                print(f"\n# 撤单请求已提交，返回值: {result}")
                print(f"# 查询最新委托: xttrader query_stock_orders")
        finally:
            trader.stop()


if __name__ == "__main__":
    main()
