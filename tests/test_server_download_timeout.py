"""
download 类调用超时隔离的回归测试

背景：盘后平安 QMT 挂起时，xtquant 的 download_* C 扩展调用会永久阻塞
xqshare worker 线程，堵死后续所有下载请求。本测试覆盖新增的
_call_download_with_timeout 包装器（daemon 线程 + join 超时）。
"""

import time
import traceback
import pytest
from unittest.mock import MagicMock, patch

from xqshare.server import (
    _init_logging,
    _call_download_with_timeout,
    _get_download_timeout,
    LoggingProxy,
)

_init_logging("WARNING")  # 初始化 server 全局 api_logger


class TestCallDownloadWithTimeout:
    def test_fast_function_returns_result(self):
        def fn(x):
            return x * 2

        assert _call_download_with_timeout(fn, 21) == 42

    def test_function_returns_none(self):
        def fn():
            return None

        assert _call_download_with_timeout(fn) is None

    def test_function_exception_is_re_raised_with_original_type(self):
        def fn():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            _call_download_with_timeout(fn)

    def test_exception_preserves_original_traceback(self):
        """重抛的异常应保留 worker 线程的原始 traceback（而非只指向重抛点）。"""

        def _boom():
            raise ValueError("底层错误")

        with pytest.raises(ValueError) as exc_info:
            _call_download_with_timeout(_boom)

        frames = [f.name for f in traceback.extract_tb(exc_info.value.__traceback__)]
        assert "_boom" in frames
        assert "_worker" in frames

    def test_timeout_raises_timeout_error(self):
        def slow():
            time.sleep(0.5)
            return "late"

        with pytest.raises(TimeoutError, match="超时"):
            _call_download_with_timeout(slow, timeout=0.05)


class TestGetDownloadTimeout:
    def test_default_is_600(self):
        with patch.dict("os.environ", {}, clear=True):
            assert _get_download_timeout() == 600

    def test_env_var_override(self):
        with patch.dict("os.environ", {"XQSHARE_DOWNLOAD_TIMEOUT": "120"}, clear=True):
            assert _get_download_timeout() == 120

    def test_invalid_env_var_falls_back(self):
        with patch.dict("os.environ", {"XQSHARE_DOWNLOAD_TIMEOUT": "not-a-number"}, clear=True):
            assert _get_download_timeout() == 600


class _ServiceNoExposed:
    """无任何 exposed_* 方法的服务，用于模拟直通下载场景。"""


class _ServiceWithExposed:
    """带 exposed_download_sector_data 封装的服务。"""

    def exposed_download_sector_data(self, detail=False):
        return {"success": True, "native_result": None}


class TestLoggingProxyDownloadTimeout:
    def test_passthrough_download_is_timeout_wrapped(self):
        """无 exposed_* 封装的直通 download 方法应被超时包装（超时抛 TimeoutError）。"""
        target = MagicMock()

        def slow(*args, **kwargs):
            time.sleep(0.5)
            return None

        target.download_holiday_data = slow
        proxy = LoggingProxy(target, 'xtdata', lambda: 'test', service_instance=_ServiceNoExposed())

        with patch("xqshare.server.DOWNLOAD_TIMEOUT_SECONDS", 0.05):
            with pytest.raises(TimeoutError, match="超时"):
                proxy.download_holiday_data()

    def test_passthrough_download_calls_target(self):
        """正常返回时直通 download 方法透传到 target。"""
        target = MagicMock()
        target.download_financial_data.return_value = None
        proxy = LoggingProxy(target, 'xtdata', lambda: 'test', service_instance=_ServiceNoExposed())

        with patch("xqshare.server.DOWNLOAD_TIMEOUT_SECONDS", 1):
            proxy.download_financial_data(["000001.SZ"], ["ASHAREEODDERIVATIVEINDICATOR"])

        target.download_financial_data.assert_called_once_with(
            ["000001.SZ"], ["ASHAREEODDERIVATIVEINDICATOR"]
        )

    def test_exposed_download_not_double_wrapped(self):
        """有 exposed_* 封装的 download 方法走服务端封装，不直接透传 target。"""
        target = MagicMock()
        service = _ServiceWithExposed()
        proxy = LoggingProxy(target, 'xtdata', lambda: 'test', service_instance=service)

        proxy.download_sector_data()
        # 走的是 exposed 封装，而非直接透传 target
        target.download_sector_data.assert_not_called()
