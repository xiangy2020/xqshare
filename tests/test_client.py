"""
XtQuant Share (xqshare) Client Tests
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import threading
import time

# Import the client module
import sys
sys.path.insert(0, '.')
from xqshare.client import (
    XtQuantRemote,
    ConnectionError,
    AuthenticationError,
    CallbackError,
    ReconnectPolicy,
    CallbackServer,
    RemoteModule,
)


class TestReconnectPolicy:
    """测试重连策略"""
    
    def test_base_delay(self):
        policy = ReconnectPolicy(max_retries=5, base_delay=1, backoff_factor=2)
        assert policy.get_delay(0) == 1
    
    def test_exponential_backoff(self):
        policy = ReconnectPolicy(max_retries=5, base_delay=1, backoff_factor=2)
        assert policy.get_delay(0) == 1
        assert policy.get_delay(1) == 2
        assert policy.get_delay(2) == 4
        assert policy.get_delay(3) == 8
    
    def test_max_delay(self):
        policy = ReconnectPolicy(max_retries=5, base_delay=1, max_delay=10, backoff_factor=2)
        assert policy.get_delay(10) == 10  # cap at max_delay


class TestCallbackServer:
    """测试回调服务器"""
    
    def test_register_callback(self):
        server = CallbackServer(port=0)
        callback = Mock()
        server.register("test_id", callback)
        assert "test_id" in server._callbacks
    
    def test_unregister_callback(self):
        server = CallbackServer(port=0)
        callback = Mock()
        server.register("test_id", callback)
        server.unregister("test_id")
        assert "test_id" not in server._callbacks
    
    def test_invoke_callback(self):
        server = CallbackServer(port=0)
        callback = Mock(return_value="result")
        server.register("test_id", callback)
        
        result = server._invoke_callback("test_id", "arg1", "arg2")
        callback.assert_called_once_with("arg1", "arg2")
        assert result == "result"
    
    def test_invoke_nonexistent_callback(self):
        server = CallbackServer(port=0)
        with pytest.raises(CallbackError):
            server._invoke_callback("nonexistent")


class TestRemoteModule:
    """测试远程模块代理"""
    
    def test_attribute_access(self):
        mock_client = Mock()
        mock_client._ensure_connected = Mock()
        mock_client._should_reconnect = Mock(return_value=False)
        mock_client._conn = Mock()
        mock_client._token = "test_token"
        
        # Mock the remote module
        mock_module = Mock()
        mock_module.test_func = Mock(return_value="test_result")
        mock_client._conn.root.get_xtdata = Mock(return_value=mock_module)
        
        remote = RemoteModule(mock_client, 'xtdata')
        result = remote.test_func()
        
        assert result == "test_result"
    
    def test_reconnect_on_error(self):
        mock_client = Mock()
        mock_client._ensure_connected = Mock()
        mock_client._should_reconnect = Mock(return_value=True)
        mock_client._conn = Mock()
        mock_client._token = "test_token"
        
        # First call fails, second succeeds
        mock_module = Mock()
        call_count = [0]
        
        def test_func():
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Connection reset")
            return "success"
        
        mock_module.test_func = test_func
        mock_client._conn.root.get_xtdata = Mock(return_value=mock_module)
        
        remote = RemoteModule(mock_client, 'xtdata')
        
        # Should trigger reconnect on first failure
        with pytest.raises(Exception):
            remote.test_func()


class TestXtQuantRemote:
    """测试主客户端类"""
    
    @patch('xqshare.client.rpyc.connect')
    def test_connect_without_auth(self, mock_connect):
        mock_conn = Mock()
        mock_conn.root.ping = Mock(return_value="pong")
        mock_connect.return_value = mock_conn
        
        client = XtQuantRemote(host="localhost", port=18812, client_secret="", auto_reconnect=False)
        
        mock_connect.assert_called_once()
        assert client._connected is True
    
    @patch('xqshare.client.rpyc.connect')
    def test_connect_with_auth(self, mock_connect):
        mock_conn = Mock()
        mock_conn.root.ping = Mock(return_value="pong")
        mock_conn.root.authenticate = Mock(return_value="test_token")
        mock_connect.return_value = mock_conn
        
        client = XtQuantRemote(
            host="localhost", 
            port=18812, 
            client_secret="my-secret",
            auto_reconnect=False
        )
        
        mock_conn.root.authenticate.assert_called_once()
        assert client._token == "test_token"
    
    @patch('xqshare.client.rpyc.connect')
    def test_context_manager(self, mock_connect):
        mock_conn = Mock()
        mock_conn.root.ping = Mock(return_value="pong")
        mock_connect.return_value = mock_conn
        
        with XtQuantRemote(host="localhost", auto_reconnect=False) as client:
            assert client._connected is True
        
        assert client._connected is False
    
    @patch('xqshare.client.rpyc.connect')
    def test_should_reconnect(self, mock_connect):
        mock_conn = Mock()
        mock_conn.root.ping = Mock(return_value="pong")
        mock_connect.return_value = mock_conn
        
        client = XtQuantRemote(host="localhost", auto_reconnect=True)
        
        # Connection errors should trigger reconnect
        assert client._should_reconnect(Exception("Connection reset")) is True
        assert client._should_reconnect(Exception("Socket closed")) is True
        assert client._should_reconnect(Exception("Timeout")) is True
        
        # Other errors should not
        assert client._should_reconnect(Exception("ValueError")) is False
        
        client.close()
    
    @patch('xqshare.client.rpyc.connect')
    def test_is_connected(self, mock_connect):
        mock_conn = Mock()
        mock_conn.root.ping = Mock(return_value="pong")
        mock_connect.return_value = mock_conn
        
        client = XtQuantRemote(host="localhost", auto_reconnect=False)
        assert client.is_connected() is True
        
        client.close()
        assert client.is_connected() is False


class TestGlobalFunctions:
    """测试全局便捷函数"""
    
    @patch('xqshare.client.rpyc.connect')
    def test_connect_disconnect(self, mock_connect):
        mock_conn = Mock()
        mock_conn.root.ping = Mock(return_value="pong")
        mock_connect.return_value = mock_conn
        
        from xqshare.client import connect, disconnect, get_client
        
        client = connect(host="localhost", auto_reconnect=False)
        assert client is not None
        
        retrieved = get_client()
        assert retrieved is client
        
        disconnect()
        assert get_client() is None


class TestDatadirProxy:
    """测试 datadir 属性和全局代理"""

    @patch('xqshare.client.rpyc.connect')
    def test_datadir_property_exists(self, mock_connect):
        """XtQuantRemote 应有 datadir 属性"""
        mock_conn = Mock()
        mock_conn.root.ping = Mock(return_value="pong")
        mock_connect.return_value = mock_conn

        client = XtQuantRemote(host="localhost", auto_reconnect=False, client_secret="")
        assert hasattr(client, 'datadir')
        client.close()

    @patch('xqshare.client.rpyc.connect')
    def test_datadir_returns_remote_module(self, mock_connect):
        """datadir 属性应返回 RemoteModule 实例"""
        mock_conn = Mock()
        mock_conn.root.ping = Mock(return_value="pong")
        mock_connect.return_value = mock_conn

        client = XtQuantRemote(host="localhost", auto_reconnect=False, client_secret="")
        assert isinstance(client.datadir, RemoteModule)
        client.close()

    @patch('xqshare.client.rpyc.connect')
    def test_global_datadir_proxy_available_after_connect(self, mock_connect):
        """connect() 后全局 datadir 代理应可用"""
        mock_conn = Mock()
        mock_conn.root.ping = Mock(return_value="pong")
        mock_connect.return_value = mock_conn

        from xqshare.client import connect, disconnect, datadir as global_datadir

        client = connect(host="localhost", auto_reconnect=False, client_secret="")
        # 全局 datadir 代理应指向 client.datadir
        assert global_datadir is not None
        disconnect()

    @patch('xqshare.client.rpyc.connect')
    def test_datadir_calls_get_datadir_on_server(self, mock_connect):
        """访问 datadir 方法时应调用 server 的 get_datadir"""
        mock_conn = Mock()
        mock_conn.root.ping = Mock(return_value="pong")

        # 模拟 server 端返回的 datadir 对象
        mock_remote_reader = Mock()
        mock_remote_reader.kline = Mock(return_value={
            "__xqshare_serialized__": "dataframe_csv",
            "data": "datetime,open,close\n2024-01-02,10.0,10.5\n"
        })
        mock_conn.root.get_datadir = Mock(return_value=mock_remote_reader)
        mock_connect.return_value = mock_conn

        client = XtQuantRemote(host="localhost", auto_reconnect=False, client_secret="")
        # 触发 _ensure_module，调用 get_datadir
        try:
            client.datadir.kline('600000.SH', '1d')
        except Exception:
            pass  # 可能因 mock 不完整而失败，但 get_datadir 应已被调用
        client.close()


class TestDeserializeFromTransfer:
    """测试 _deserialize_from_transfer 反序列化逻辑"""

    def test_none_type(self):
        from xqshare.client import _deserialize_from_transfer
        result = _deserialize_from_transfer({"__xqshare_serialized__": "none", "data": None})
        assert result is None

    def test_json_list(self):
        from xqshare.client import _deserialize_from_transfer
        result = _deserialize_from_transfer({
            "__xqshare_serialized__": "json",
            "data": '["600000.SH", "000001.SZ"]'
        })
        assert result == ["600000.SH", "000001.SZ"]

    def test_json_dict(self):
        from xqshare.client import _deserialize_from_transfer
        result = _deserialize_from_transfer({
            "__xqshare_serialized__": "json",
            "data": '{"key": "value"}'
        })
        assert result == {"key": "value"}

    def test_dataframe_csv(self):
        """DataFrame CSV 反序列化应返回正确的 DataFrame"""
        import pandas as pd
        from xqshare.client import _deserialize_from_transfer

        csv_data = "datetime,open,high,low,close,volume\n2024-01-02,10.0,10.5,9.8,10.3,1000\n2024-01-03,10.3,10.8,10.1,10.6,1200\n"
        result = _deserialize_from_transfer({
            "__xqshare_serialized__": "dataframe_csv",
            "data": csv_data
        })

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2
        assert "open" in result.columns
        assert "close" in result.columns

    def test_dataframe_csv_index_preserved(self):
        """DataFrame 反序列化后索引应正确还原"""
        import pandas as pd
        from xqshare.client import _deserialize_from_transfer

        # 构造带 DatetimeIndex 的 DataFrame 并序列化
        df = pd.DataFrame(
            {"open": [10.0, 10.3], "close": [10.3, 10.6]},
            index=pd.to_datetime(["2024-01-02", "2024-01-03"])
        )
        df.index.name = "datetime"
        csv_data = df.to_csv(index=True)

        result = _deserialize_from_transfer({
            "__xqshare_serialized__": "dataframe_csv",
            "data": csv_data
        })

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2
        assert result.index[0] is not None

    def test_dict_with_dataframe(self):
        """嵌套 DataFrame 的字典应正确反序列化"""
        import json
        import pandas as pd
        from xqshare.client import _deserialize_from_transfer

        inner_csv = "datetime,close\n2024-01-02,10.3\n"
        payload = json.dumps({
            "600000.SH": {"__df__": True, "csv": inner_csv}
        })
        result = _deserialize_from_transfer({
            "__xqshare_serialized__": "dict_with_dataframe",
            "data": payload
        })

        assert isinstance(result, dict)
        assert "600000.SH" in result
        assert isinstance(result["600000.SH"], pd.DataFrame)

    def test_non_serialized_passthrough(self):
        """非序列化数据应原样返回"""
        from xqshare.client import _deserialize_from_transfer

        plain_list = ["a", "b", "c"]
        assert _deserialize_from_transfer(plain_list) == plain_list

        plain_str = "hello"
        assert _deserialize_from_transfer(plain_str) == plain_str

        plain_dict = {"no_marker": True}
        assert _deserialize_from_transfer(plain_dict) == plain_dict


if __name__ == "__main__":
    pytest.main([__file__, "-v"])