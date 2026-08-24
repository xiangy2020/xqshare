"""
Quote 行情事件回调路由单元测试

验证方向 2（server 端 quote 事件路由）的核心链路：
  - server 端 _QuoteEventCallback 序列化 tick 并反向推送（JSON 字符串按值传输）
  - LoggingProxy 拦截 xtdata.subscribe_whole_quote / subscribe_quote，接管 callback
  - client 端 RemoteQuoteCallback 还原 JSON 为本地 dict 并分发给用户回调

不依赖远端服务，纯 mock。
"""

import json
import sys
import time
from unittest.mock import MagicMock

# 在导入 server 之前 mock xtquant（Mac 上无 xtquant）
mock_xtquant = MagicMock()
mock_xtdata = MagicMock()
mock_xttrader = MagicMock()
mock_xttype = MagicMock()
mock_xtquant.xtdata = mock_xtdata
mock_xtquant.xttrader = mock_xttrader
mock_xtquant.xttype = mock_xttype
sys.modules['xtquant'] = mock_xtquant
sys.modules['xtquant.xtdata'] = mock_xtdata
sys.modules['xtquant.xttrader'] = mock_xttrader
sys.modules['xtquant.xttype'] = mock_xttype

from xqshare.server import _init_logging
_init_logging("WARNING")

from xqshare.server import LoggingProxy, _QuoteEventCallback
from xqshare.client import RemoteQuoteCallback
from xqshare.auth import AccountLevel


def _wait_until(cond, timeout=2.0):
    """轮询等待条件成立，返回是否成立。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class TestQuoteEventCallback:
    """服务端行情事件路由器"""

    def test_serialize_and_push_json_string(self):
        """on_data 应把 tick 序列化为 JSON 字符串并按值推送给订阅者"""
        qec = _QuoteEventCallback()
        try:
            subscriber = MagicMock()
            qec.register(subscriber)
            qec.on_data({"000001.SZ": {"lastPrice": 11.39, "lastClose": 11.38}})

            assert _wait_until(lambda: subscriber.exposed_on_quote_event.called), \
                "dispatch 线程未推送事件"
            payload = subscriber.exposed_on_quote_event.call_args[0][0]
            assert isinstance(payload, str), "应推送 JSON 字符串（str 按值传输，避免 netref）"
            data = json.loads(payload)
            assert data["000001.SZ"]["lastPrice"] == 11.39
        finally:
            qec.stop()

    def test_remove_failed_subscriber(self):
        """推送到失效订阅者失败时，应自动移除该订阅者"""
        qec = _QuoteEventCallback()
        try:
            bad = MagicMock()
            bad.exposed_on_quote_event.side_effect = RuntimeError("connection closed")
            qec.register(bad)
            qec.on_data({"000001.SZ": {"lastPrice": 1.0}})

            assert _wait_until(lambda: len(qec._subscribers) == 0), \
                "推送失败后订阅者应被移除"
        finally:
            qec.stop()

    def test_broken_netref_hash_does_not_crash(self):
        """断开的 netref 其 __hash__/__eq__ 会触发 RPC 抛 EOFError；
        路由器必须用 id() 做 key，register/unregister/自动移除都不应触发 hash。"""
        qec = _QuoteEventCallback()
        try:
            class BrokenNetref:
                def __hash__(self):
                    raise EOFError("stream has been closed")

                def exposed_on_quote_event(self, payload):
                    raise EOFError("stream has been closed")

            broken = BrokenNetref()
            qec.register(broken)          # 不应触发 __hash__
            assert len(qec._subscribers) == 1
            qec.unregister(broken)        # 不应触发 __hash__
            assert len(qec._subscribers) == 0

            # dispatch 自动移除路径也不应触发 __hash__
            qec.register(broken)
            qec.on_data({"000001.SZ": {"lastPrice": 1.0}})
            assert _wait_until(lambda: len(qec._subscribers) == 0), \
                "dispatch 线程应自动移除断开订阅者且不崩溃"
        finally:
            qec.stop()

    def test_callable(self):
        """xtquant 以 callback(datas) 形式调用路由器，需 __call__ 可调用"""
        qec = _QuoteEventCallback()
        try:
            subscriber = MagicMock()
            qec.register(subscriber)
            # 模拟 xtquant subscribe_callback_wrapper 的 callback(datas) 调用
            qec({"000001.SZ": {"lastPrice": 10.0}})
            assert _wait_until(lambda: subscriber.exposed_on_quote_event.called), \
                "__call__ 应触发序列化并推送"
        finally:
            qec.stop()


class TestLoggingProxyQuoteIntercept:
    """LoggingProxy 拦截 xtdata 订阅并接管 callback"""

    def _proxy(self, target, qec):
        return LoggingProxy(
            target, 'xtdata',
            lambda: "test-client",
            None,  # 无权限检查
            AccountLevel.STANDARD,
            quote_event_callback=qec,
        )

    def test_subscribe_whole_quote_kwarg_callback(self):
        """关键字 callback 应注册到路由器，原生 callback 由路由器接管"""
        mock = MagicMock()
        mock.subscribe_whole_quote.return_value = 1
        qec = _QuoteEventCallback()
        try:
            proxy = self._proxy(mock, qec)
            client_cb = MagicMock()
            proxy.subscribe_whole_quote(["000001.SZ"], callback=client_cb)

            kwargs = mock.subscribe_whole_quote.call_args.kwargs
            assert kwargs["callback"] is qec, "原生 callback 应由服务端路由器接管"
            assert len(qec._subscribers) == 1 and next(iter(qec._subscribers.values())) is client_cb, \
                "客户端回调应注册到路由器"
        finally:
            qec.stop()

    def test_subscribe_whole_quote_positional_callback(self):
        """位置 callback 应注册到路由器，并替换为路由器"""
        mock = MagicMock()
        mock.subscribe_whole_quote.return_value = 1
        qec = _QuoteEventCallback()
        try:
            proxy = self._proxy(mock, qec)
            client_cb = MagicMock()
            proxy.subscribe_whole_quote(["000001.SZ"], client_cb)

            args = mock.subscribe_whole_quote.call_args[0]
            assert args[1] is qec, "位置 callback 应由路由器接管"
            assert len(qec._subscribers) == 1
        finally:
            qec.stop()

    def test_subscribe_quote_positional_callback(self):
        """subscribe_quote 第 6 个位置参数 callback 应被正确接管"""
        mock = MagicMock()
        mock.subscribe_quote.return_value = 1
        qec = _QuoteEventCallback()
        try:
            proxy = self._proxy(mock, qec)
            proxy.subscribe_quote("000001.SZ", "tick", "", "", -1, MagicMock())

            args = mock.subscribe_quote.call_args[0]
            assert args[5] is qec, "subscribe_quote 位置 callback 应由路由器接管"
        finally:
            qec.stop()

    def test_subscribe_without_callback(self):
        """未传 callback 时，仍用路由器接管（无订阅者，数据被丢弃）"""
        mock = MagicMock()
        mock.subscribe_whole_quote.return_value = 1
        qec = _QuoteEventCallback()
        try:
            proxy = self._proxy(mock, qec)
            proxy.subscribe_whole_quote(["000001.SZ"])

            kwargs = mock.subscribe_whole_quote.call_args.kwargs
            assert kwargs["callback"] is qec
            assert len(qec._subscribers) == 0
        finally:
            qec.stop()


class TestRemoteQuoteCallback:
    """客户端行情回调接收器"""

    def test_receive_json_and_dispatch_local_dict(self):
        """JSON 字符串应还原为本地 dict 后分发给用户回调（非 netref）"""
        rqc = RemoteQuoteCallback(MagicMock())
        try:
            collected = []
            rqc.register(lambda data: collected.append(data))
            rqc.exposed_on_quote_event(
                json.dumps({"000001.SZ": {"lastPrice": 10.5}})
            )

            assert _wait_until(lambda: len(collected) > 0), "用户回调未收到数据"
            assert isinstance(collected[0], dict), "客户端应拿到本地 dict 而非 netref"
            assert collected[0]["000001.SZ"]["lastPrice"] == 10.5
        finally:
            rqc.stop()

    def test_receive_dict_passthrough(self):
        """直接传 dict（兜底）也应正确分发"""
        rqc = RemoteQuoteCallback(MagicMock())
        try:
            collected = []
            rqc.register(lambda data: collected.append(data))
            rqc.exposed_on_quote_event({"000001.SZ": {"lastPrice": 1.0}})

            assert _wait_until(lambda: len(collected) > 0)
            assert collected[0]["000001.SZ"]["lastPrice"] == 1.0
        finally:
            rqc.stop()

    def test_user_callback_exception_isolated(self):
        """单个用户回调异常不应影响其他回调"""
        rqc = RemoteQuoteCallback(MagicMock())
        try:
            collected = []

            def boom(data):
                raise ValueError("bad")

            rqc.register(boom)
            rqc.register(lambda data: collected.append(data))
            rqc.exposed_on_quote_event({"000001.SZ": {"lastPrice": 2.0}})

            assert _wait_until(lambda: len(collected) > 0), "正常回调应仍被调用"
        finally:
            rqc.stop()
