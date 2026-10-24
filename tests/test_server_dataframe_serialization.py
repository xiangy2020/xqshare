"""pandas/numpy 返回值必须走序列化通道，不能被包成 netref。

背景：`get_divid_factors` 返回裸 DataFrame，被 `LoggingProxy.__getattr__` 的
「复杂对象递归包装」提前包成 netref，客户端 `pd.DataFrame(netref)` 触发
`object __array__ method not producing an array`，逐只股票全部失败。
"""

import logging

import numpy as np
import pandas as pd
import pytest

from xqshare import server as server_module
from xqshare.server import SERIALIZED_MARKER, LoggingProxy


class _FakeXtData:
    @staticmethod
    def get_divid_factors(symbol):
        return pd.DataFrame(
            {"interest": [0.3], "dr": [1.4]}, index=[19910502]
        )

    @staticmethod
    def get_series():
        return pd.Series([1.0, 2.0], index=["a", "b"])

    @staticmethod
    def get_array():
        return np.array([1.0, 2.0])


@pytest.fixture
def proxy(monkeypatch):
    # api_logger 在 server 启动时才初始化，单测里注入标准 logger
    monkeypatch.setattr(server_module, "api_logger", logging.getLogger("test"))
    return LoggingProxy(_FakeXtData(), "xtdata", lambda: "test_client")


def test_dataframe_not_wrapped_as_netref(proxy):
    result = proxy.get_divid_factors("000001.SZ")

    assert not isinstance(result, LoggingProxy)
    assert result[SERIALIZED_MARKER] == "dataframe_csv"
    assert "interest" in result["data"]


def test_series_not_wrapped_as_netref(proxy):
    result = proxy.get_series()

    assert not isinstance(result, LoggingProxy)
    assert result[SERIALIZED_MARKER] == "json"


def test_ndarray_not_wrapped_as_netref(proxy):
    result = proxy.get_array()

    assert not isinstance(result, LoggingProxy)
    assert result[SERIALIZED_MARKER] == "json"
