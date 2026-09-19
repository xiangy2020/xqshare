# -*- coding: utf-8 -*-
"""
通道路由 —— 周期探测 + 抖动保护 + 通道选择。

单例 ChannelRouter 后台周期性探测 mini/bigqmt 可用性，用「连续 N 次一致才切换」
的抖动保护避免网络抖动导致来回切换。路由决策暴露给 server 端：

  - data_target()：数据源目标（mini xtdata 或 bigqmt 数据源）
  - trader_mode()：交易通道模式（'mini' | 'bigqmt'）

支持 XQSHARE_FORCE_CHANNEL 强制通道（'mini' / 'bigqmt'，空 = 自动路由），
用于真机验证时无需停 mini 即可测大QMT 通道。
"""

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger("xqshare.channels.router")

from .health import HealthProbe, ChannelHealth
from .bigqmt import BigQmtChannel


def _mini_data_target():
    """惰性获取 mini 通道数据源（xtquant.xtdata），不可用时返回 None。"""
    try:
        import xtquant.xtdata as _xtdata
        return _xtdata
    except ImportError:
        return None


class ChannelRouter:
    """通道路由器（单例）。"""

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self, probe: HealthProbe = None, probe_interval: int = None,
                 switch_threshold: int = 2):
        self._probe = probe or HealthProbe()
        if probe_interval is None:
            probe_interval = int(os.environ.get("XQSHARE_PROBE_INTERVAL", "30") or 30)
        self._probe_interval = probe_interval
        self._switch_threshold = switch_threshold

        # 初始乐观假设 mini 可用（现状默认通道），避免首探测前 data_target 返回 None
        self._current_mode = "mini"
        self._last_health = ChannelHealth()
        self._pending_mode = None
        self._pending_count = 0

        self._running = False
        self._thread = None
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()

    # ---- 单例 ----

    @classmethod
    def instance(cls) -> "ChannelRouter":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = ChannelRouter()
            return cls._instance

    @classmethod
    def reset_for_tests(cls):
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.stop()
            cls._instance = None

    # ---- 路由决策 ----

    def _forced_channel(self) -> Optional[str]:
        force = os.environ.get("XQSHARE_FORCE_CHANNEL", "").strip().lower()
        return force if force in ("mini", "bigqmt") else None

    def snapshot(self) -> dict:
        force = self._forced_channel()
        with self._state_lock:
            health = self._last_health
            mode = self._current_mode
        # 生效通道：强制通道优先于自动路由结果
        effective_mode = force if force is not None else mode
        return {
            "mode": effective_mode,
            "mini_available": health.mini_available,
            "bigqmt_available": health.bigqmt_available,
            "checked_at": health.checked_at,
            "detail": dict(health.detail),
        }

    def is_mini_available(self) -> bool:
        with self._state_lock:
            return self._last_health.mini_available

    def is_bigqmt_available(self) -> bool:
        with self._state_lock:
            return self._last_health.bigqmt_available

    def data_target(self):
        """当前活跃数据源目标；双通道都不可用时返回 None（调用方报清晰错误）。"""
        force = self._forced_channel()
        if force == "bigqmt":
            return BigQmtChannel.data_source()
        if force == "mini":
            return _mini_data_target()
        with self._state_lock:
            mode = self._current_mode
        if mode == "bigqmt":
            return BigQmtChannel.data_source()
        if mode == "mini":
            return _mini_data_target()
        return None

    def trader_mode(self) -> str:
        """交易通道模式：'mini' 或 'bigqmt'。"""
        force = self._forced_channel()
        if force is not None:
            return force
        with self._state_lock:
            mode = self._current_mode
        # 双通道都不可用时回落到 mini（现状默认），由 mini 通道报原生错误
        return mode if mode in ("mini", "bigqmt") else "mini"

    # ---- 探测循环 ----

    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="xqshare-channel-router", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _loop(self):
        while not self._stop_event.is_set():
            self._probe_once()
            self._stop_event.wait(self._probe_interval)

    def _probe_once(self):
        health = self._probe.probe()
        with self._state_lock:
            self._apply_probe(health)

    def _apply_probe(self, health: ChannelHealth):
        """抖动保护：连续 switch_threshold 次探测到新模式才切换。"""
        self._last_health = health
        new_mode = health.mode
        if new_mode == self._current_mode:
            self._pending_mode = None
            self._pending_count = 0
            return
        if new_mode == self._pending_mode:
            self._pending_count += 1
        else:
            self._pending_mode = new_mode
            self._pending_count = 1
        if self._pending_count >= self._switch_threshold:
            logger.info(
                "[ChannelRouter] 通道切换: %s -> %s",
                self._current_mode, new_mode,
            )
            self._current_mode = new_mode
            self._pending_mode = None
            self._pending_count = 0
