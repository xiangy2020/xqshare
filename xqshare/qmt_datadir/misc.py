"""
杂项数据解析模块

包含：
  - ETF 成分股列表（TradeDateAndETFStockListCache）
  - 交易所列表（marketlistinfo，Base64 编码）
  - 涨跌幅快照元数据（increase/，ZiPeDiT+zlib 压缩位图）
"""

import base64
import os
import re
import struct
import zlib


def read_etf_list(data_dir: str) -> list:
    """
    从 TradeDateAndETFStockListCache 读取 ETF 成分股代码列表。

    Returns:
        list[str]，纯6位代码（不含交易所后缀），如 ['510010', '600009', ...]
    """
    cache_path = os.path.join(data_dir, 'TradeDateAndETFStockListCache')
    if not os.path.exists(cache_path):
        raise FileNotFoundError(f"缓存文件不存在: {cache_path}")

    data = open(cache_path, 'rb').read()
    idx = data.find(b'etflist')
    if idx < 0:
        return []

    after = data[idx + len(b'etflist'):]
    codes_raw = re.findall(rb'(?<![0-9])[0-9]{6}(?![0-9])', after[:100000])
    seen = set()
    codes = []
    for c in codes_raw:
        s = c.decode()
        if s not in seen:
            seen.add(s)
            codes.append(s)
    return codes


def read_market_list(data_dir: str) -> list:
    """
    读取支持的交易所列表（marketlistinfo，Base64 编码）。

    Returns:
        list[str]，如 ['BJ', 'BKZS', 'DF', 'GF', 'HGT', 'IF',
                        'INE', 'SF', 'SGT', 'SH', 'SHO', 'SZ', 'SZO', 'ZF']
    """
    fp = os.path.join(data_dir, 'marketlistinfo')
    if not os.path.exists(fp):
        raise FileNotFoundError(f"文件不存在: {fp}")

    raw = open(fp, 'rb').read()
    decoded = base64.b64decode(raw.strip()).decode('ascii')
    return [m for m in decoded.strip(',').split(',') if m]


def read_increase_meta(data_dir: str, market: str = 'SH') -> dict:
    """
    读取涨跌幅快照文件的元数据（文件头信息）。

    注意：increase/ 文件内部是 ZiPeDiT+zlib 压缩位图，完整解析需要配合
    股票列表，此函数仅返回文件头元数据，不解析位图内容。

    Args:
        data_dir: datadir 根目录
        market:   市场代码，如 'SH'/'SZ'/'BJ'

    Returns:
        dict，字段：
          market            str  市场代码
          file_size         int  原始文件大小（字节）
          n_dates           int  日期数量（文件头 offset 8，uint16）
          n_stocks          int  股票数量（文件头 offset 10，uint16）
          decompressed_size int  解压后大小（字节），解压失败时为 0
    """
    fp = os.path.join(data_dir, 'increase', market)
    if not os.path.exists(fp):
        raise FileNotFoundError(f"文件不存在: {fp}")

    raw = open(fp, 'rb').read()
    n_dates  = struct.unpack_from('<H', raw, 8)[0]
    n_stocks = struct.unpack_from('<H', raw, 10)[0]

    decompressed_size = 0
    idx = raw.find(b'ZiPeDiT')
    if idx >= 0:
        try:
            dec = zlib.decompress(raw[idx + 7:])
            decompressed_size = len(dec)
        except Exception:
            pass

    return {
        'market':            market,
        'file_size':         len(raw),
        'n_dates':           n_dates,
        'n_stocks':          n_stocks,
        'decompressed_size': decompressed_size,
    }
