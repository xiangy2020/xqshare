# -*- coding: utf-8 -*-
"""server 端通道路由集成测试：target_resolver / create_trader 路由 / 状态接口。"""
import pytest
from unittest.mock import MagicMock, patch

from xqshare.server import _init_logging, XtQuantService

_init_logging("WARNING")  # 初始化 server 全局 logger


@pytest.fixture
def authed_service(mock_service):
    mock_service._authenticated = True
    mock_service._account_level = None  # 跳过权限检查，聚焦路由逻辑
    return mock_service


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.data_target.return_value = "XTQUANT_XTDATA"
    router.trader_mode.return_value = "mini"
    router.snapshot.return_value = {
        "mode": "mini",
        "mini_available": True,
        "bigqmt_available": False,
        "checked_at": 1.0,
        "detail": {},
    }
    XtQuantService._router = router
    yield router
    XtQuantService._router = None


class TestGetXtdataRouting:
    def test_proxy_has_target_resolver(self, authed_service, mock_router):
        proxy = authed_service.exposed_get_xtdata()
        assert proxy._target_resolver is not None
        assert proxy._target_resolver() == "XTQUANT_XTDATA"

    def test_resolver_none_raises_clear_error(self, authed_service, mock_router):
        mock_router.data_target.return_value = None
        proxy = authed_service.exposed_get_xtdata()
        with pytest.raises(RuntimeError) as exc:
            proxy.get_market_data
        assert "不可用" in str(exc.value)


class TestGetChannelStatus:
    def test_returns_snapshot(self, authed_service, mock_router):
        result = authed_service.exposed_get_channel_status()
        assert result["mode"] == "mini"
        assert result["mini_available"] is True
        mock_router.snapshot.assert_called_once()

    def test_router_failure_falls_back(self, authed_service, mock_router):
        mock_router.snapshot.side_effect = RuntimeError("router down")
        result = authed_service.exposed_get_channel_status()
        assert result["mode"] == "unknown"
        assert "error" in result["detail"]


class TestCreateTraderRouting:
    def test_bigqmt_route(self, authed_service, mock_router):
        mock_router.trader_mode.return_value = "bigqmt"
        fake_trader = MagicMock()
        with patch("xqshare.server.XTQUANT_AVAILABLE", True), \
             patch("xqshare.server.BigQmtChannel") as mock_channel:
            mock_channel.create_trader.return_value = fake_trader
            mock_channel.account_id.return_value = "ACC123"

            result = authed_service.exposed_create_trader(None, 12345)

            mock_channel.create_trader.assert_called_once_with(None, 12345)
            # 返回 LoggingProxy，target 为 fake_trader
            assert result._target is fake_trader
            # 回调适配器已注册到 trader
            fake_trader.register_callback.assert_called_once()

    def test_mini_route(self, authed_service, mock_router):
        mock_router.trader_mode.return_value = "mini"
        fake_trader = MagicMock()
        with patch("xqshare.server.XTQUANT_AVAILABLE", True), \
             patch("xqshare.server.XtQuantTrader", return_value=fake_trader):
            result = authed_service.exposed_create_trader("C:\\QMT\\userdata_mini", 12345)
            assert result._target is fake_trader


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
