# -*- coding: utf-8 -*-
"""大QMT 通道封装（bigqmt.py）单元测试：回调适配 + 下单闸门。"""
import pytest

from xqshare.channels.bigqmt import (
    BIGQMT_AVAILABLE,
    BigQmtCallbackAdapter,
    BigQmtChannel,
    _GuardedTrader,
    _SyntheticEvent,
    order_allowed,
)


class TestOrderAllowed:
    def test_default_false(self, monkeypatch):
        monkeypatch.delenv("XQSHARE_BIGQMT_ALLOW_ORDER", raising=False)
        assert order_allowed() is False

    def test_true(self, monkeypatch):
        monkeypatch.setenv("XQSHARE_BIGQMT_ALLOW_ORDER", "true")
        assert order_allowed() is True


class TestGuardedTrader:
    def test_read_passthrough(self):
        trader = _SyntheticEvent(query_stock_asset=lambda acc: "asset")
        guarded = _GuardedTrader(trader)
        assert guarded.query_stock_asset("acc") == "asset"

    def test_write_blocked_when_disallowed(self, monkeypatch):
        monkeypatch.setenv("XQSHARE_BIGQMT_ALLOW_ORDER", "false")
        trader = _SyntheticEvent()
        guarded = _GuardedTrader(trader)
        with pytest.raises(RuntimeError):
            guarded.order_stock("acc", "600000.SH", 23, 100, 11, 10.0, "s", "r")

    def test_cancel_blocked_when_disallowed(self, monkeypatch):
        monkeypatch.setenv("XQSHARE_BIGQMT_ALLOW_ORDER", "false")
        trader = _SyntheticEvent()
        guarded = _GuardedTrader(trader)
        with pytest.raises(RuntimeError):
            guarded.cancel_order_stock("acc", "SYS1")

    def test_write_allowed_when_enabled(self, monkeypatch):
        monkeypatch.setenv("XQSHARE_BIGQMT_ALLOW_ORDER", "true")
        trader = _SyntheticEvent(order_stock=lambda *a, **k: "orderid")
        guarded = _GuardedTrader(trader)
        assert guarded.order_stock("acc", "600000.SH", 23, 100, 11, 10.0, "s", "r") == "orderid"


class TestCallbackAdapter:
    def _make(self):
        from unittest.mock import MagicMock
        server_cb = MagicMock()
        adapter = BigQmtCallbackAdapter(server_cb, account_id="ACC123")
        return adapter, server_cb

    def test_on_stock_order_passthrough(self):
        adapter, server_cb = self._make()
        order = _SyntheticEvent(account_id="ACC123", stock_code="600000.SH")
        adapter.on_stock_order(order)
        server_cb.on_stock_order.assert_called_once_with(order)

    def test_on_stock_trade_passthrough(self):
        adapter, server_cb = self._make()
        trade = _SyntheticEvent(account_id="ACC123")
        adapter.on_stock_trade(trade)
        server_cb.on_stock_trade.assert_called_once_with(trade)

    def test_on_order_error_synthesizes_junk_order(self):
        adapter, server_cb = self._make()
        err = _SyntheticEvent(
            stock_code="600000.SH", order_sysid="SYS1", order_id="SYS1",
            strategy_name="s", order_remark="r", error_msg="拒单",
        )
        adapter.on_order_error(err)
        server_cb.on_stock_order.assert_called_once()
        order = server_cb.on_stock_order.call_args[0][0]
        assert order.account_id == "ACC123"
        assert order.stock_code == "600000.SH"
        assert order.order_status == 57
        assert order.status_msg == "拒单"

    def test_on_cancel_error_synthesizes_junk_order(self):
        adapter, server_cb = self._make()
        err = _SyntheticEvent(order_sysid="SYS2", error_msg="撤单被拒")
        adapter.on_cancel_error(err)
        server_cb.on_stock_order.assert_called_once()
        order = server_cb.on_stock_order.call_args[0][0]
        assert order.order_status == 57
        assert "撤单失败" in order.status_msg

    def test_on_account_status_not_forwarded(self):
        adapter, server_cb = self._make()
        adapter.on_account_status(_SyntheticEvent(account_id="ACC123", status=1))
        server_cb.on_stock_order.assert_not_called()
        server_cb.on_stock_trade.assert_not_called()

    def test_on_async_response_not_forwarded(self):
        adapter, server_cb = self._make()
        adapter.on_order_stock_async_response(_SyntheticEvent(seq=1))
        server_cb.on_stock_order.assert_not_called()


class TestBigQmtChannel:
    def test_enabled_false_when_lib_missing(self, monkeypatch):
        monkeypatch.setenv("XQSHARE_BIGQMT_ENABLED", "true")
        if BIGQMT_AVAILABLE:
            pytest.skip("xtquant_big_convert 已安装，跳过未安装场景")
        assert BigQmtChannel.enabled() is False

    def test_enabled_requires_flag(self, monkeypatch):
        monkeypatch.delenv("XQSHARE_BIGQMT_ENABLED", raising=False)
        assert BigQmtChannel.enabled() is False

    def test_data_source_none_when_disabled(self, monkeypatch):
        monkeypatch.delenv("XQSHARE_BIGQMT_ENABLED", raising=False)
        assert BigQmtChannel.data_source() is None

    def test_ping_false_when_disabled(self, monkeypatch):
        monkeypatch.delenv("XQSHARE_BIGQMT_ENABLED", raising=False)
        assert BigQmtChannel.ping() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
