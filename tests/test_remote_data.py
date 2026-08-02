"""
XtQuant Share (xqshare) 远端数据下载/获取集成测试

使用 .env.client 配置连接远端服务，测试实际的数据下载和获取功能。
运行方式: pytest tests/test_remote_data.py -v -m remote

前置条件:
  - 项目根目录下存在 .env.client 文件（可从 .env.client.example 复制）
  - 远端 xqshare 服务已启动并可访问
"""

import os
import time
import pytest

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_CLIENT_FILE = os.path.join(PROJECT_ROOT, ".env.client")


def _skip_if_no_env():
    """如果没有 .env.client 文件则跳过测试"""
    if not os.path.exists(ENV_CLIENT_FILE):
        pytest.skip(f"缺少 {ENV_CLIENT_FILE}，跳过远端测试。可从 .env.client.example 复制。")


# ==================== Session 级别共享连接 ====================
# 远端服务有连接数限制，整个 session 共享一个客户端连接，
# 避免频繁创建/断开导致 connection closed by peer

@pytest.fixture(scope="session")
def remote_client():
    """Session 级别的远端客户端连接"""
    _skip_if_no_env()
    from xqshare import XtQuantRemote

    client = XtQuantRemote(env_file=ENV_CLIENT_FILE, heartbeat_interval=30)
    assert client.is_connected(), "连接远端服务失败"
    yield client
    client.close()


# ────────────────────────────────────────────────────────────
#  数据获取测试
# ────────────────────────────────────────────────────────────

@pytest.mark.remote
class TestRemoteDataGet:
    """远端数据获取测试"""

    # ──────────── 基础连接 ────────────

    def test_connection_and_auth(self, remote_client):
        """测试连接和认证"""
        assert remote_client.is_connected()
        # 认证成功后能正常调用 API 即代表认证通过
        stocks = remote_client.xtdata.get_stock_list_in_sector("沪深A股")
        assert stocks is not None and len(stocks) > 0, "认证后应能正常调用 API"

    def test_service_status(self, remote_client):
        """测试获取服务状态"""
        status = remote_client.get_service_status()
        assert isinstance(status, dict)
        assert "uptime" in status

    # ──────────── 股票列表/板块 ────────────

    def test_get_stock_list_in_sector(self, remote_client):
        """测试获取沪深A股列表"""
        stocks = remote_client.xtdata.get_stock_list_in_sector("沪深A股")
        assert stocks is not None
        assert len(stocks) > 0
        # 验证格式：股票代码应以 .SZ 或 .SH 结尾
        assert any(code.endswith(".SZ") or code.endswith(".SH") for code in stocks)

    def test_get_sector_list(self, remote_client):
        """测试获取板块列表"""
        sectors = remote_client.xtdata.get_sector_list()
        assert sectors is not None
        assert len(sectors) > 0
        assert "沪深A股" in sectors

    # ──────────── K线/行情数据 ────────────

    def test_get_market_data(self, remote_client):
        """测试获取日线K线数据 (get_market_data)"""
        data = remote_client.xtdata.get_market_data(
            field_list=["open", "high", "low", "close", "volume"],
            stock_list=["000001.SZ"],
            period="1d",
            start_time="20260101",
            end_time="20260630",
        )
        assert data is not None
        # get_market_data 返回 {field: DataFrame} 格式
        assert "close" in data or "open" in data

    def test_get_market_data_ex(self, remote_client):
        """测试获取扩展K线数据 (get_market_data_ex)"""
        data = remote_client.xtdata.get_market_data_ex(
            field_list=[],
            stock_list=["000001.SZ"],
            period="1d",
            start_time="20260101",
            end_time="20260630",
        )
        assert data is not None
        assert "000001.SZ" in data

    def test_get_full_tick(self, remote_client):
        """测试获取实时行情快照"""
        tick = remote_client.xtdata.get_full_tick(["000001.SZ"])
        assert tick is not None
        assert "000001.SZ" in tick

    # ──────────── 合约信息 ────────────

    def test_get_instrument_detail(self, remote_client):
        """测试获取合约详情"""
        detail = remote_client.xtdata.get_instrument_detail("000001.SZ")
        assert detail is not None
        assert isinstance(detail, dict)

    # ──────────── 交易日历 ────────────

    def test_get_trading_dates(self, remote_client):
        """测试获取交易日历"""
        dates = remote_client.xtdata.get_trading_dates("SH", "20260101", "20260630")
        assert dates is not None
        assert len(dates) > 0

    def test_get_holidays(self, remote_client):
        """测试获取节假日列表

        注：get_holidays() 返回结果依赖 QMT 本地数据环境，
        某些券商版本或无数据缓存时可能返回空列表，因此只校验类型。
        """
        holidays = remote_client.xtdata.get_holidays()
        assert holidays is not None
        assert isinstance(holidays, list)


# ────────────────────────────────────────────────────────────
#  数据下载测试
# ────────────────────────────────────────────────────────────

@pytest.mark.remote
class TestRemoteDataDownload:
    """远端数据下载测试"""

    def test_download_history_data(self, remote_client):
        """测试下载历史数据 (download_history_data)

        download_history_data 是异步下载，调用后返回 None 表示已提交下载任务。
        后续可通过 get_local_data 获取已下载的数据。
        """
        result = remote_client.xtdata.download_history_data(
            stock_code="000001.SZ",
            period="1d",
            start_time="20260101",
            end_time="20260630",
        )
        # download_history_data 返回 None 表示下载任务已提交
        assert result is None

    def test_download_history_data2(self, remote_client):
        """测试批量下载历史数据 (download_history_data2)

        download_history_data2 也是异步下载，调用后返回 None 表示已提交下载任务。
        """
        result = remote_client.xtdata.download_history_data2(
            stock_list=["000001.SZ"],
            period="1d",
            start_time="20260101",
            end_time="20260630",
        )
        # download_history_data2 返回 None 表示下载任务已提交
        assert result is None

    def test_download_and_get_local_data(self, remote_client):
        """测试下载后获取本地数据（完整流程）"""
        # 1. 先下载数据
        remote_client.xtdata.download_history_data(
            stock_code="000002.SZ",
            period="1d",
            start_time="20260101",
            end_time="20260630",
        )

        # 2. 等待下载完成后获取本地数据
        time.sleep(2)

        data = remote_client.xtdata.get_local_data(
            field_list=[],
            stock_list=["000002.SZ"],
            period="1d",
            start_time="20260101",
            end_time="20260630",
        )
        assert data is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "remote"])
