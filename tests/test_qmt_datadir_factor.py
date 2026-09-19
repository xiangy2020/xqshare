# -*- coding: utf-8 -*-
"""qmt_datadir 因子库（factor.py）单元测试。"""
import os

import pandas as pd
import pytest

pyarrow = pytest.importorskip("pyarrow")
import pyarrow as pa
import pyarrow.feather as feather

from xqshare.qmt_datadir import (
    QmtDataReader,
    read_factor,
    list_factor_classes,
)

# 2024-01-01 / 2024-01-02 / 2024-01-03 的毫秒时间戳（UTC）
_TS = {
    '20240101': 1704067200000,
    '20240102': 1704153600000,
    '20240103': 1704240000000,
}


def _write_feather(path, times_ms, stocks, values):
    table = pa.table({
        '_time':  pa.array(times_ms, type=pa.int64()),
        '_stock': pa.array(stocks, type=pa.string()),
        'value':  pa.array(values, type=pa.float64()),
    })
    feather.write_feather(table, path)


def _make_datadir(tmp_path, factor_class='factor_growth',
                  times=None, stocks=None, values=None):
    """构造最小 datadir：EP/{factor_class}_Xdat2/data.fe"""
    times = times or [_TS['20240101'], _TS['20240102'], _TS['20240103']]
    stocks = stocks or ['600000.SH', '000001.SZ', '600000.SH']
    values = values or [1.0, 2.0, 3.0]
    xdat2 = tmp_path / 'EP' / f'{factor_class}_Xdat2'
    xdat2.mkdir(parents=True)
    _write_feather(str(xdat2 / 'data.fe'), times, stocks, values)
    return str(tmp_path)


class TestReadFactor:
    def test_basic_schema_normalize(self, tmp_path):
        """_time→date、_stock→symbol 标准化，值保留"""
        data_dir = _make_datadir(tmp_path)
        df = read_factor(data_dir, 'factor_growth')
        assert list(df.columns)[:2] == ['symbol', 'date']
        assert df['symbol'].tolist() == ['600000.SH', '000001.SZ', '600000.SH']
        assert df['value'].tolist() == [1.0, 2.0, 3.0]
        # date 应为 datetime
        assert pd.api.types.is_datetime64_any_dtype(df['date'])

    def test_symbols_filter_full_code(self, tmp_path):
        data_dir = _make_datadir(tmp_path)
        df = read_factor(data_dir, 'factor_growth', symbols=['000001.SZ'])
        assert df['symbol'].unique().tolist() == ['000001.SZ']

    def test_symbols_filter_pure_code(self, tmp_path):
        """纯 6 位代码也能匹配（兼容 600000 → 600000.SH/SZ）"""
        data_dir = _make_datadir(tmp_path)
        df = read_factor(data_dir, 'factor_growth', symbols=['600000'])
        assert df['symbol'].unique().tolist() == ['600000.SH']

    def test_date_filter(self, tmp_path):
        data_dir = _make_datadir(tmp_path)
        df = read_factor(data_dir, 'factor_growth',
                         start_date='20240102', end_date='20240103')
        dates = pd.to_datetime(df['date']).dt.strftime('%Y%m%d').unique().tolist()
        assert dates == ['20240102', '20240103']

    def test_missing_feather_raises(self, tmp_path):
        """data.fe 不存在时抛 FileNotFoundError"""
        ep = tmp_path / 'EP'
        ep.mkdir()
        with pytest.raises(FileNotFoundError):
            read_factor(str(tmp_path), 'factor_growth')


class TestListFactorClasses:
    def test_lists_both_kinds(self, tmp_path):
        """同时识别 _Xdat2（已下载）和 _Xdat（仅定义）"""
        ep = tmp_path / 'EP'
        (ep / 'factor_growth_Xdat2').mkdir(parents=True)
        (ep / 'factor_metrics_Xdat').mkdir(parents=True)
        classes = list_factor_classes(str(tmp_path))
        assert classes == ['factor_growth', 'factor_metrics']

    def test_empty_when_no_ep(self, tmp_path):
        assert list_factor_classes(str(tmp_path)) == []


class TestReaderMethods:
    def test_reader_factors(self, tmp_path):
        data_dir = _make_datadir(tmp_path)
        reader = QmtDataReader(data_dir)
        df = reader.factors('factor_growth', symbols=['000001.SZ'])
        assert df['symbol'].unique().tolist() == ['000001.SZ']

    def test_reader_factor_classes(self, tmp_path):
        ep = tmp_path / 'EP'
        (ep / 'beta_Xdat2').mkdir(parents=True)
        reader = QmtDataReader(str(tmp_path))
        assert reader.factor_classes() == ['beta']


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
