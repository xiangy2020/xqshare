"""
K线数据解析模块

QMT .DAT 文件格式（逆向工程确认）：
  - 文件头：8 字节，魔数 0xFEFFFFFFFFFFFF7F
  - 每条记录：64 字节，小端序
    offset  0: uint32  Unix 秒时间戳（本地时间）
    offset  4: uint32  open  × 100
    offset  8: uint32  high  × 100
    offset 12: uint32  low   × 100
    offset 16: uint32  close × 100
    offset 20: uint32  （保留，始终0）
    offset 24: uint32  volume（手）
    offset 28: uint32  （保留，始终0）
    offset 32: int64   amount（元）
    offset 40: uint32  （保留）
    offset 44: float32 当日复权因子（除权当天 ≠ 1.0）
    offset 48: float32 累计复权因子（从上市至今）
    offset 52: uint32  pre_close × 100
    offset 56: uint32  （保留，始终0）
    offset 60: uint32  （保留，固定 32764）
"""

import os
import struct
from datetime import datetime

import pandas as pd

# ── 常量 ──────────────────────────────────────────────────────────────────────
FILE_HEADER_SIZE = 8
RECORD_SIZE = 64
FILE_MAGIC = 0x7FFFFFFFFFFFFFFE  # int64 小端

PERIOD_MAP = {
    '1d': '86400', 'day': '86400', 'd': '86400', '86400': '86400',
    '5m': '300',   '5min': '300',  '300': '300',
    '1m': '60',    '1min': '60',   '60': '60',
}


def _parse_record(rec: bytes) -> dict:
    """解析单条 64 字节 K 线记录，返回字段字典"""
    ts             = struct.unpack_from('<I', rec, 0)[0]
    open_x100      = struct.unpack_from('<I', rec, 4)[0]
    high_x100      = struct.unpack_from('<I', rec, 8)[0]
    low_x100       = struct.unpack_from('<I', rec, 12)[0]
    close_x100     = struct.unpack_from('<I', rec, 16)[0]
    volume         = struct.unpack_from('<I', rec, 24)[0]
    amount         = struct.unpack_from('<q', rec, 32)[0]
    adj_factor     = struct.unpack_from('<f', rec, 44)[0]
    cum_adj_factor = struct.unpack_from('<f', rec, 48)[0]
    pre_close_x100 = struct.unpack_from('<I', rec, 52)[0]
    return {
        'timestamp':       ts,
        'datetime':        datetime.fromtimestamp(ts),
        'open':            open_x100 / 100.0,
        'high':            high_x100 / 100.0,
        'low':             low_x100 / 100.0,
        'close':           close_x100 / 100.0,
        'volume':          volume,
        'amount':          amount,
        'pre_close':       pre_close_x100 / 100.0,
        'adj_factor':      adj_factor,       # 当日复权因子（除权当天 ≠ 1.0）
        'cum_adj_factor':  cum_adj_factor,   # 累计复权因子（从上市至今）
    }


def read_kline(filepath: str, include_raw_ts: bool = False) -> pd.DataFrame:
    """
    读取 QMT .DAT K线文件，返回 DataFrame。

    Args:
        filepath:       .DAT 文件绝对路径
        include_raw_ts: 是否保留原始 Unix 时间戳列（timestamp）

    Returns:
        DataFrame，index 为 DatetimeIndex（本地时间），
        columns: open, high, low, close, volume, amount,
                 pre_close, adj_factor, cum_adj_factor
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"文件不存在: {filepath}")

    with open(filepath, 'rb') as f:
        f.read(FILE_HEADER_SIZE)
        data = f.read()

    n = len(data) // RECORD_SIZE
    if n == 0:
        return pd.DataFrame()

    records = []
    for i in range(n):
        rec = data[i * RECORD_SIZE: (i + 1) * RECORD_SIZE]
        if len(rec) < RECORD_SIZE:
            break
        try:
            records.append(_parse_record(rec))
        except Exception:
            continue

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime')
    df.index.name = 'datetime'
    df = df[(df['close'] > 0) & (df.index.year >= 1990)]

    if not include_raw_ts:
        df = df.drop(columns=['timestamp'], errors='ignore')

    return df


def read_kline_dir(data_dir: str, symbol: str, period: str,
                   include_raw_ts: bool = False) -> pd.DataFrame:
    """
    通过股票代码和周期读取 K线数据。

    Args:
        data_dir: datadir 根目录
        symbol:   股票代码，如 '600000.SH' 或 '000001.SZ'
        period:   周期，如 '1d'/'86400'/'5m'/'300'/'1m'/'60'

    Returns:
        DataFrame（同 read_kline）
    """
    code, exchange = (symbol.rsplit('.', 1) if '.' in symbol
                      else (symbol, 'SH'))
    exchange = exchange.upper()
    period_dir = PERIOD_MAP.get(period.lower(), period)
    filepath = os.path.join(data_dir, exchange, period_dir, f'{code}.DAT')
    return read_kline(filepath, include_raw_ts=include_raw_ts)
