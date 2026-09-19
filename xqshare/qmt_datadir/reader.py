"""
QmtDataReader —— QMT 本地 datadir 统一读取入口

将所有子模块的功能聚合为一个易用的类接口。

用法示例：
    from xqshare.qmt_datadir import QmtDataReader

    reader = QmtDataReader('~/Downloads/pazq_qmt/.../userdata_mini/datadir')

    # K线
    df = reader.kline('600000.SH', '1d')
    df5 = reader.kline('600000.SH', '5m')

    # 板块
    sw = reader.sectors('申万行业')          # {板块名: [代码列表]}
    bank = reader.sector('申万行业', 'SW1银行')

    # 权重
    wt = reader.weight()                     # {板块名: [{'symbol':..., 'weight':...}]}

    # 分红除权
    divid = reader.divid(exchange='SH', code='600000')

    # ETF / 交易所 / 涨跌幅元数据
    etfs = reader.etf_list()
    markets = reader.market_list()
    meta = reader.increase_meta('SH')
"""

import os
from typing import Optional

import pandas as pd

from .kline import read_kline, read_kline_dir
from .sector import (list_sector_categories, read_all_sectors,
                     read_sector, read_weight)
from .divid import read_divid
from .factor import read_factor, list_factor_classes
from .misc import read_etf_list, read_market_list, read_increase_meta


class QmtDataReader:
    """
    QMT 本地 datadir 统一读取器。

    Args:
        data_dir: userdata_mini/datadir 的绝对路径或 ~ 开头的路径
    """

    def __init__(self, data_dir: str):
        self.data_dir = os.path.expanduser(data_dir)
        if not os.path.isdir(self.data_dir):
            raise FileNotFoundError(f"datadir 不存在: {self.data_dir}")

    # ── K线 ──────────────────────────────────────────────────────────────────

    def kline(self, symbol: str, period: str = '1d',
              include_raw_ts: bool = False) -> pd.DataFrame:
        """
        读取 K 线数据。

        Args:
            symbol:         股票代码，如 '600000.SH'
            period:         周期，支持 '1d'/'86400'/'5m'/'300'/'1m'/'60'
            include_raw_ts: 是否保留原始 Unix 时间戳列

        Returns:
            DataFrame，index=DatetimeIndex，
            columns: open, high, low, close, volume, amount,
                     pre_close, adj_factor, cum_adj_factor
        """
        return read_kline_dir(self.data_dir, symbol, period, include_raw_ts)

    # ── 板块 ─────────────────────────────────────────────────────────────────

    def sector_categories(self) -> list:
        """列出所有板块分类，如 ['申万行业', '证监会行业']"""
        return list_sector_categories(self.data_dir)

    def sectors(self, category: str = '申万行业') -> dict:
        """
        读取某分类下所有板块成分股。

        Returns:
            dict[str, list[str]]，{板块名: [代码列表]}
        """
        return read_all_sectors(self.data_dir, category)

    def sector(self, category: str, sector_name: str) -> list:
        """
        读取单个板块成分股列表。

        Args:
            category:    板块分类，如 '申万行业'
            sector_name: 板块名称，如 'SW1银行'

        Returns:
            list[str]，股票代码列表
        """
        fp = os.path.join(self.data_dir, 'Sector', 'Temple',
                          category, sector_name)
        return read_sector(fp)

    # ── 权重 ─────────────────────────────────────────────────────────────────

    def weight(self) -> dict:
        """
        读取指数/板块权重数据。

        Returns:
            dict[str, list[dict]]，{板块名: [{'symbol': str, 'weight': float}]}
        """
        return read_weight(self.data_dir)

    # ── 分红除权 ──────────────────────────────────────────────────────────────

    def divid(self, exchange: Optional[str] = None,
              code: Optional[str] = None) -> pd.DataFrame:
        """
        读取分红除权数据（DividData LevelDB）。

        Args:
            exchange: 可选过滤，如 'SH'/'SZ'/'BJ'
            code:     可选过滤，纯6位代码，如 '600000'

        Returns:
            DataFrame，columns: exchange, code, symbol, ex_date, ratio, raw_ts
        """
        return read_divid(self.data_dir, exchange=exchange, code=code)

    # ── ETF 列表 ──────────────────────────────────────────────────────────────

    def etf_list(self) -> list:
        """
        读取 ETF 成分股代码列表（纯6位代码）。

        Returns:
            list[str]，如 ['510010', '600009', ...]
        """
        return read_etf_list(self.data_dir)

    # ── 交易所列表 ────────────────────────────────────────────────────────────

    def market_list(self) -> list:
        """
        读取支持的交易所列表。

        Returns:
            list[str]，如 ['BJ', 'SH', 'SZ', ...]
        """
        return read_market_list(self.data_dir)

    # ── 涨跌幅快照元数据 ──────────────────────────────────────────────────────

    def increase_meta(self, market: str = 'SH') -> dict:
        """
        读取涨跌幅快照文件元数据（不解析位图内容）。

        Args:
            market: 市场代码，如 'SH'/'SZ'/'BJ'

        Returns:
            dict: market, file_size, n_dates, n_stocks, decompressed_size
        """
        return read_increase_meta(self.data_dir, market)

    # ── 因子库 ────────────────────────────────────────────────────────────────

    def factor_classes(self) -> list:
        """
        列出 datadir 下已存在的因子表（英文 modelName）。

        Returns:
            list[str]，如 ['factor_growth', 'factor_metrics', ...]
        """
        return list_factor_classes(self.data_dir)

    def factors(self, factor_class: str,
                symbols: Optional[list] = None,
                start_date: Optional[str] = None,
                end_date: Optional[str] = None) -> pd.DataFrame:
        """
        读取因子库数据（feather 文件）。

        Args:
            factor_class: 因子表名（英文 modelName，如 'factor_growth'）
            symbols:      可选，股票代码列表（如 ['600000.SH']），过滤
            start_date:   可选，起始日期（'YYYYMMDD' 或 'YYYY-MM-DD'）
            end_date:     可选，结束日期（同上）

        Returns:
            DataFrame，columns: symbol, date, 以及因子数值列。
            因子数据未下载（无 data.fe）时抛 FileNotFoundError。
        """
        return read_factor(self.data_dir, factor_class,
                           symbols=symbols,
                           start_date=start_date,
                           end_date=end_date)

    def __repr__(self) -> str:
        return f"QmtDataReader(data_dir='{self.data_dir}')"
