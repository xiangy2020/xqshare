"""
download_history_data2 跨版本兼容性的回归测试

背景：
- xtquant.xtdata.download_history_data2 从 250807.1.2 起才在二进制层
  接受 incrementally 关键字；之前版本传这个 kwarg 会 TypeError。
- xqshare 服务端 exposed_download_history_data2 在 1.2.37 之前硬编码
  传 incrementally=inc，导致老版本 xtquant 调用全部失败。

本测试覆盖：
1. 新版 xtquant：传 incrementally=... 时正确透传。
2. 老版 xtquant：自动回退，不传 incrementally kwarg。
3. C 扩展拿不到签名：按新版本处理，保留原行为。
4. on_progress 回调：finished >= total 时 status['done'] == True。
"""

import pytest
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, '.')

from xqshare.server import _init_logging, XtQuantService
_init_logging("WARNING")


def _make_service():
    """构造一个轻量级 XtQuantService，跳过 on_connect 走捷径"""
    service = XtQuantService.__new__(XtQuantService)
    service._xtdata = MagicMock()
    return service


def _fake_signature(*, supports_incrementally: bool):
    """构造一个 inspect.signature 风格的 mock 对象"""
    sig = MagicMock()
    params = {"callback": MagicMock()}
    if supports_incrementally:
        params["incrementally"] = MagicMock()
    sig.parameters = params
    return sig


class TestDownloadHistoryData2Compat:
    """exposed_download_history_data2 的版本兼容测试"""

    def test_new_xtquant_passes_incrementally_true(self):
        """新版本 xtquant: incrementally=True 应该透传到下游"""
        service = _make_service()
        sig = _fake_signature(supports_incrementally=True)

        with patch("xqshare.server.inspect.signature", return_value=sig):
            result = service.exposed_download_history_data2(
                stock_list=["000001.SZ"], period="1d", incrementally=True
            )

        call = service._xtdata.download_history_data2.call_args
        assert call.kwargs.get("incrementally") is True
        assert "callback" in call.kwargs
        assert call.args[0] == ["000001.SZ"]
        assert call.args[1] == "1d"
        assert isinstance(result, dict)
        assert result["done"] is False  # 未触发回调

    def test_new_xtquant_passes_incrementally_none(self):
        """新版本 xtquant: incrementally=None 也应该传过去（不丢语义）"""
        service = _make_service()
        sig = _fake_signature(supports_incrementally=True)

        with patch("xqshare.server.inspect.signature", return_value=sig):
            service.exposed_download_history_data2(
                stock_list=["000001.SZ"], period="1d", incrementally=None
            )

        call = service._xtdata.download_history_data2.call_args
        # None 是合法值，调用时应该带上
        assert "incrementally" in call.kwargs
        assert call.kwargs["incrementally"] is None

    def test_old_xtquant_skips_incrementally(self):
        """老版本 xtquant: 不支持 incrementally kwarg，调用里必须不能出现"""
        service = _make_service()
        sig = _fake_signature(supports_incrementally=False)

        with patch("xqshare.server.inspect.signature", return_value=sig):
            service.exposed_download_history_data2(
                stock_list=["000001.SZ"], period="1d", incrementally=True
            )

        call = service._xtdata.download_history_data2.call_args
        # 关键断言：旧版本根本不认识这个 kwarg，不能传
        assert "incrementally" not in call.kwargs
        # 其它参数照常透传
        assert "callback" in call.kwargs
        assert call.args[0] == ["000001.SZ"]
        assert call.args[1] == "1d"

    def test_c_extension_signature_unavailable_falls_back_to_new(self):
        """C 扩展拿不到签名（inspect 抛 TypeError）时按新版本处理"""
        service = _make_service()

        with patch(
            "xqshare.server.inspect.signature",
            side_effect=TypeError("unsupported callable"),
        ):
            service.exposed_download_history_data2(
                stock_list=["000001.SZ"], period="1d", incrementally=False
            )

        call = service._xtdata.download_history_data2.call_args
        # 拿不到签名时保持原行为：传 incrementally
        assert call.kwargs.get("incrementally") is False

    def test_old_xtquant_with_none_incrementally_still_works(self):
        """老版本 + incrementally=None: 不带 kwarg 调用，回退到全量下载语义"""
        service = _make_service()
        sig = _fake_signature(supports_incrementally=False)

        with patch("xqshare.server.inspect.signature", return_value=sig):
            service.exposed_download_history_data2(
                stock_list=["600000.SH"], period="5m"
            )

        call = service._xtdata.download_history_data2.call_args
        assert "incrementally" not in call.kwargs
        assert "callback" in call.kwargs

    def test_progress_marks_done_when_finished_equals_total(self):
        """on_progress 回调里 finished >= total 时 status['done'] 应为 True"""
        service = _make_service()
        sig = _fake_signature(supports_incrementally=True)

        with patch("xqshare.server.inspect.signature", return_value=sig):
            result = service.exposed_download_history_data2(
                stock_list=["000001.SZ"], period="1d"
            )

        # 取出传给 xtdata 的回调函数
        cb = service._xtdata.download_history_data2.call_args.kwargs["callback"]
        cb({"finished": 3, "total": 3, "message": "complete"})

        assert result["finished"] == 3
        assert result["total"] == 3
        assert result["done"] is True
        assert result["message"] == "complete"

    def test_progress_not_done_when_finished_less_than_total(self):
        """on_progress 回调里 finished < total 时 status['done'] 应为 False"""
        service = _make_service()
        sig = _fake_signature(supports_incrementally=True)

        with patch("xqshare.server.inspect.signature", return_value=sig):
            result = service.exposed_download_history_data2(
                stock_list=["000001.SZ"], period="1d"
            )

        cb = service._xtdata.download_history_data2.call_args.kwargs["callback"]
        cb({"finished": 1, "total": 5, "message": "downloading"})

        assert result["finished"] == 1
        assert result["total"] == 5
        assert result["done"] is False
