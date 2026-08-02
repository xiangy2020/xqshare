"""
分红除权数据解析模块

数据来源：datadir/DividData/*.ldb（LevelDB SSTable 格式）

解析方式：直接扫描 .ldb 文件中的可读字符串，无需安装 LevelDB 库。

key 格式：{exchange}|{code}|4000|{除权日时间戳ms}
value 结构：
  offset 16: int64   除权日时间戳（ms）
  offset 24: float64 分红比例（每股分红金额，元）

注意：
  - ratio 极小值（< 0.01）是 LevelDB 扫描误命中的浮点数噪声，已过滤
  - 可转债代码（114xxx/127xxx）的 ratio 可能是转股价（10~30），属正常值
"""

import os
import re
import struct
from datetime import datetime
from typing import Optional

import pandas as pd

_KEY_PATTERN = re.compile(rb'[A-Z]{2}\|[0-9]{6}\|[0-9]+\|[0-9]+')


def read_divid(data_dir: str,
               exchange: Optional[str] = None,
               code: Optional[str] = None) -> pd.DataFrame:
    """
    读取分红除权数据（DividData LevelDB）。

    Args:
        data_dir: datadir 根目录
        exchange: 可选过滤，如 'SH'/'SZ'/'BJ'
        code:     可选过滤，如 '600000'（纯6位代码，不含交易所后缀）

    Returns:
        DataFrame，columns:
          exchange  str      交易所代码
          code      str      股票代码（6位）
          symbol    str      完整代码，如 '600000.SH'
          ex_date   datetime 除权日
          ratio     float    每股分红金额（元）
          raw_ts    int      key 中的原始时间戳（ms）
    """
    div_dir = os.path.join(data_dir, 'DividData')
    if not os.path.isdir(div_dir):
        raise FileNotFoundError(f"DividData 目录不存在: {div_dir}")

    # 构建前缀过滤（加速扫描）
    prefix = b''
    if exchange and code:
        prefix = f'{exchange}|{code}|'.encode()
    elif exchange:
        prefix = f'{exchange}|'.encode()
    elif code:
        prefix = f'|{code}|'.encode()

    records = []
    for fname in sorted(os.listdir(div_dir)):
        if not fname.endswith('.ldb'):
            continue
        fpath = os.path.join(div_dir, fname)
        data = open(fpath, 'rb').read()

        for m in _KEY_PATTERN.finditer(data):
            k = m.group()
            if prefix and prefix not in k:
                continue

            parts = k.decode().split('|')
            if len(parts) != 4:
                continue
            ex, cd, _, ts_str = parts

            if exchange and ex != exchange:
                continue
            if code and cd != code:
                continue

            raw_ts = int(ts_str)
            val = data[m.end(): m.end() + 80]
            if len(val) < 32:
                continue

            # offset 16: int64 ms 时间戳（除权日）
            ex_ts = struct.unpack_from('<q', val, 16)[0]
            # offset 24: float64 分红比例
            ratio = struct.unpack_from('<d', val, 24)[0]

            # 时间戳合理性校验
            if not (1e12 < ex_ts < 2e12):
                if raw_ts > 1e12:
                    ex_ts = raw_ts
                else:
                    continue

            # 过滤噪声：极小浮点数是 LevelDB 扫描误命中
            if not (0.01 <= ratio < 100):
                continue

            try:
                ex_date = datetime.fromtimestamp(ex_ts / 1000)
                if not (2000 <= ex_date.year <= 2030):
                    continue
            except Exception:
                continue

            records.append({
                'exchange': ex,
                'code':     cd,
                'symbol':   f'{cd}.{ex}',
                'ex_date':  ex_date,
                'ratio':    ratio,
                'raw_ts':   raw_ts,
            })

    if not records:
        return pd.DataFrame(columns=['exchange', 'code', 'symbol',
                                     'ex_date', 'ratio', 'raw_ts'])

    df = pd.DataFrame(records)
    df['ex_date'] = pd.to_datetime(df['ex_date'])
    df = df.drop_duplicates(subset=['symbol', 'ex_date'])
    df = df.sort_values(['symbol', 'ex_date']).reset_index(drop=True)
    return df
