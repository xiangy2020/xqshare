# -*- coding: utf-8 -*-
"""通道路由（router.py）单元测试：抖动保护 + 路由决策 + 强制通道。"""
import pytest

from xqshare.channels.health import ChannelHealth, HealthProbe
from xqshare.channels.router import ChannelRouter


class FakeProbe:
    """按队列顺序返回预设探测结果。"""

    def __init__(self, results):
        self._results = list(results)

    def probe(self):
        return self._results.pop(0)


def _health(mini, bigqmt):
    return ChannelHealth(mini_available=mini, bigqmt_available=bigqmt, checked_at=1.0)


class TestJitterProtection:
    def test_switch_after_threshold(self):
        router = ChannelRouter(
            probe=FakeProbe([_health(False, True), _health(False, True)]),
            switch_threshold=2,
        )
        router._probe_once()
        assert router.snapshot()["mode"] == "mini"  # 第一次，未达阈值
        router._probe_once()
        assert router.snapshot()["mode"] == "bigqmt"  # 第二次，切换

    def test_no_switch_on_single_flap(self):
        router = ChannelRouter(
            probe=FakeProbe([_health(False, True), _health(True, False)]),
            switch_threshold=2,
        )
        router._probe_once()
        router._probe_once()
        assert router.snapshot()["mode"] == "mini"  # 抖动，未切换

    def test_switch_back(self):
        router = ChannelRouter(
            probe=FakeProbe([
                _health(False, True), _health(False, True),  # mini -> bigqmt
                _health(True, False), _health(True, False),  # bigqmt -> mini
            ]),
            switch_threshold=2,
        )
        router._probe_once()
        router._probe_once()
        assert router.snapshot()["mode"] == "bigqmt"
        router._probe_once()
        router._probe_once()
        assert router.snapshot()["mode"] == "mini"


class TestSnapshot:
    def test_snapshot_structure(self):
        router = ChannelRouter(probe=FakeProbe([_health(True, False)]), switch_threshold=1)
        router._probe_once()
        snap = router.snapshot()
        assert set(snap.keys()) == {
            "mode", "mini_available", "bigqmt_available", "checked_at", "detail",
        }
        assert snap["mode"] == "mini"
        assert snap["mini_available"] is True


class TestTraderMode:
    def test_trader_mode_follows_current_mode(self):
        router = ChannelRouter(
            probe=FakeProbe([_health(False, True), _health(False, True)]),
            switch_threshold=2,
        )
        router._probe_once()
        router._probe_once()
        assert router.trader_mode() == "bigqmt"

    def test_trader_mode_default_mini(self):
        router = ChannelRouter(probe=FakeProbe([_health(False, False)]), switch_threshold=1)
        router._probe_once()
        assert router.trader_mode() == "mini"  # 双通道不可用回落 mini


class TestForceChannel:
    def test_force_bigqmt_trader_mode(self, monkeypatch):
        monkeypatch.setenv("XQSHARE_FORCE_CHANNEL", "bigqmt")
        router = ChannelRouter(probe=FakeProbe([_health(True, False)]), switch_threshold=1)
        router._probe_once()
        assert router.trader_mode() == "bigqmt"

    def test_force_mini_trader_mode(self, monkeypatch):
        monkeypatch.setenv("XQSHARE_FORCE_CHANNEL", "mini")
        router = ChannelRouter(probe=FakeProbe([_health(False, True)]), switch_threshold=1)
        router._probe_once()
        assert router.trader_mode() == "mini"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
