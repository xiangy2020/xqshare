# -*- coding: utf-8 -*-
"""通道健康探测（health.py）单元测试。"""
import pytest

from xqshare.channels.health import ChannelHealth, HealthProbe


class TestChannelHealth:
    def test_mode_mini_priority(self):
        h = ChannelHealth(mini_available=True, bigqmt_available=True)
        assert h.mode == "mini"

    def test_mode_bigqmt(self):
        h = ChannelHealth(mini_available=False, bigqmt_available=True)
        assert h.mode == "bigqmt"

    def test_mode_none(self):
        h = ChannelHealth()
        assert h.mode == "none"

    def test_to_dict(self):
        h = ChannelHealth(mini_available=True, checked_at=123.0, detail={"a": 1})
        d = h.to_dict()
        assert d["mode"] == "mini"
        assert d["mini_available"] is True
        assert d["bigqmt_available"] is False
        assert d["checked_at"] == 123.0
        assert d["detail"] == {"a": 1}


class TestHealthProbe:
    def test_probe_delegates(self):
        probe = HealthProbe(
            mini_probe_func=lambda: True,
            bigqmt_probe_func=lambda: False,
        )
        h = probe.probe()
        assert h.mini_available is True
        assert h.bigqmt_available is False
        assert h.mode == "mini"
        assert h.checked_at > 0

    def test_probe_exception_swallowed(self):
        def boom():
            raise RuntimeError("probe failed")

        probe = HealthProbe(mini_probe_func=boom, bigqmt_probe_func=lambda: False)
        h = probe.probe()
        assert h.mini_available is False
        assert h.mode == "none"

    def test_probe_both_true_mode_mini(self):
        probe = HealthProbe(
            mini_probe_func=lambda: True,
            bigqmt_probe_func=lambda: True,
        )
        h = probe.probe()
        assert h.mini_available is True
        assert h.bigqmt_available is True
        assert h.mode == "mini"  # mini 优先


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
