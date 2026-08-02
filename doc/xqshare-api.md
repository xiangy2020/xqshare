# xqshare API 参考文档

本文档详细描述 xqshare 提供的所有公共接口、参数、返回值、异常及使用方式。

> **相关文档：** 原始 xtquant 库的接口文档请参阅 [doc/](../doc/) 目录下的 `xtquant-xtdata-doc.md`、`xtquant-xttrader-doc.md`、`xtquant-xttype-doc.md`、`xtquant-xtconstant-doc.md`。

---

## 目录

- [1. XtQuantRemote 核心类](#1-xtquantremote-核心类)
  - [1.1 构造函数 `__init__()`](#11-构造函数-__init__)
  - [1.2 公共方法](#12-公共方法)
  - [1.3 属性](#13-属性)
  - [1.4 上下文管理器](#14-上下文管理器)
- [2. 全局便捷函数](#2-全局便捷函数)
  - [2.1 `connect()`](#21-connect)
  - [2.2 `disconnect()`](#22-disconnect)
  - [2.3 `get_client()`](#23-get_client)
  - [2.4 全局模块代理对象](#24-全局模块代理对象)
- [3. datadir 模块 API](#3-datadir-模块-api)
  - [3.1 `datadir.kline()`](#31-datadirkline)
  - [3.2 `datadir.sector_categories()`](#32-datadirsector_categories)
  - [3.3 `datadir.sectors()`](#33-datadirsectors)
  - [3.4 `datadir.sector()`](#34-datadirsector)
  - [3.5 `datadir.weight()`](#35-datadirweight)
  - [3.6 `datadir.divid()`](#36-datadirdivid)
  - [3.7 `datadir.etf_list()`](#37-datadiretf_list)
  - [3.8 `datadir.market_list()`](#38-datadirmarket_list)
  - [3.9 `datadir.increase_meta()`](#39-datadirincrease_meta)
  - [3.10 datadir 不可用时的处理](#310-datadir-不可用时的处理)
- [4. 异常与错误处理](#4-异常与错误处理)
  - [4.1 `ConnectionError`](#41-connectionerror)
  - [4.2 `AuthenticationError`](#42-authenticationerror)
  - [4.3 `CallbackError`](#43-callbackerror)
  - [4.4 RemoteModule 异常传播机制](#44-remotemodule-异常传播机制)
- [5. 连接与重连机制](#5-连接与重连机制)
  - [5.1 认证流程](#51-认证流程)
  - [5.2 心跳保活机制](#52-心跳保活机制)
  - [5.3 ReconnectPolicy 重连策略](#53-reconnectpolicy-重连策略)
  - [5.4 重连后自动恢复](#54-重连后自动恢复)
- [6. 数据传输与序列化](#6-数据传输与序列化)
  - [6.1 服务端序列化格式](#61-服务端序列化格式)
  - [6.2 客户端反序列化逻辑](#62-客户端反序列化逻辑)
  - [6.3 无 pandas 环境的降级行为](#63-无-pandas-环境的降级行为)
- [7. 命令行工具](#7-命令行工具)
  - [7.1 xtdata 命令行工具](#71-xtdata-命令行工具)
  - [7.2 xttrader 命令行工具](#72-xttrader-命令行工具)
  - [7.3 命令行工具限制](#73-命令行工具限制)

---

## 1. XtQuantRemote 核心类

`XtQuantRemote` 是 xqshare 的核心客户端类，提供对远程 xtquant 服务的透明代理访问。

### 1.1 构造函数 `__init__()`

```python
XtQuantRemote(
    host=None,
    port=None,
    client_id=None,
    client_secret=None,
    use_ssl=False,
    ssl_verify=True,
    auto_reconnect=True,
    max_retries=5,
    heartbeat_interval=30,
    log_level="INFO",
    env_file=None,
)
```

**参数说明：**

| 参数 | 类型 | 默认值 | 说明 | 环境变量回退 |
|------|------|--------|------|-------------|
| `host` | `str` | `"localhost"` | 服务端地址 | `XQSHARE_REMOTE_HOST` |
| `port` | `int` | `18812` | 服务端端口 | `XQSHARE_REMOTE_PORT` |
| `client_id` | `str` | `"client-standard"` | 客户端标识，用于多客户端认证 | `XQSHARE_CLIENT_ID` |
| `client_secret` | `str` | `"xqshare-default-secret"` | 认证密钥 | `XQSHARE_CLIENT_SECRET` |
| `use_ssl` | `bool` | `False` | 是否启用 SSL/TLS 加密 | — |
| `ssl_verify` | `bool` | `True` | 是否验证 SSL 证书（自签名证书需设为 `False`） | — |
| `auto_reconnect` | `bool` | `True` | 是否启用断线自动重连 | — |
| `max_retries` | `int` | `5` | 最大重连重试次数 | — |
| `heartbeat_interval` | `int` | `30` | 心跳检测间隔（秒），设为 `0` 禁用心跳 | — |
| `log_level` | `str` | `"INFO"` | 日志级别（`DEBUG`/`INFO`/`WARNING`/`ERROR`） | — |
| `env_file` | `str` | `None` | 指定 `.env` 文件路径 | — |

**环境变量优先级规则：** 显式参数 > 环境变量 > 默认值

**`.env` 文件加载顺序：**

1. 显式指定的 `env_file` 路径（若提供）
2. 项目根目录下的 `.env` 文件
3. 当前工作目录下的 `.env` 文件

> **注意：** 构造时会自动发起连接和认证。若连接失败，将抛出 `ConnectionError`。

**示例：**

```python
from xqshare import XtQuantRemote

# 最简用法（依赖环境变量配置）
xt = XtQuantRemote()

# 显式指定地址和密钥
xt = XtQuantRemote("21.214.136.216", client_secret="my-secret")

# 完整参数
xt = XtQuantRemote(
    host="21.214.136.216",
    port=18812,
    client_id="my-app",
    client_secret="my-secret",
    use_ssl=True,
    ssl_verify=False,
    auto_reconnect=True,
    max_retries=10,
    heartbeat_interval=15,
    log_level="DEBUG",
)

xt.close()
```

### 1.2 公共方法

#### `create_trader(userdata_path=None, session_id=None)`

创建交易实例（不自动连接，需自行调用 `start()`/`connect()`）。

```python
def create_trader(self, userdata_path: str = None, session_id: int = None) -> RemoteModule
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `userdata_path` | `str` | `None` | QMT 客户端 `userdata_mini` 目录路径，默认从环境变量 `QMT_USERDATA_PATH` 读取 |
| `session_id` | `int` | `None` | 会话 ID，默认自动生成时间戳 |

**返回值：** `RemoteModule` — 包装了 `XtQuantTrader` 的远程模块代理，支持所有交易方法。

**示例：**

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    trader = xt.create_trader("C:\\QMT\\userdata_mini")
    account = xt.xttype.StockAccount("12345678", "STOCK")
    # 需手动连接
    trader.start()
    # ... 交易操作
```

---

#### `create_trader_and_connect(userdata_path=None, session_id=None, connect_timeout=10.0)`

创建交易实例并等待真实交易连接建立（推荐用于下单场景）。

```python
def create_trader_and_connect(
    self, userdata_path: str = None, session_id: int = None, connect_timeout: float = 10.0
) -> RemoteModule
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `userdata_path` | `str` | `None` | QMT 客户端 `userdata_mini` 目录路径，默认从环境变量 `QMT_USERDATA_PATH` 读取 |
| `session_id` | `int` | `None` | 会话 ID，默认自动生成时间戳 |
| `connect_timeout` | `float` | `10.0` | 等待连接建立的最大时间（秒） |

**返回值：** `RemoteModule` — 已建立连接的 `XtQuantTrader` 代理实例，可直接下单。

**异常：** 若在 `connect_timeout` 时间内未收到 `on_connected` 回调，将抛出超时异常。

**示例：**

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    trader = xt.create_trader_and_connect("C:\\QMT\\userdata_mini")
    account = xt.xttype.StockAccount("12345678", "STOCK")
    positions = trader.query_stock_positions(account)
```

---

#### `download_history_data2(stock_list, period="1d", start_time="", end_time="", incrementally=None)`

下载历史数据（服务端封装版本，返回完整状态）。

```python
def download_history_data2(
    self, stock_list: list, period: str = "1d",
    start_time: str = "", end_time: str = "", incrementally: bool = None
) -> dict
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `stock_list` | `list` | — | 股票代码列表，如 `["000001.SZ", "600000.SH"]` |
| `period` | `str` | `"1d"` | K 线周期：`1d`/`5m`/`1m` 等 |
| `start_time` | `str` | `""` | 开始时间，格式 `YYYYMMDD` |
| `end_time` | `str` | `""` | 结束时间，格式 `YYYYMMDD` |
| `incrementally` | `bool` | `None` | 是否增量下载 |

**返回值：** `dict` — 包含下载进度的字典：

```python
{
    'finished': int,   # 已完成数量
    'total': int,      # 总数量
    'done': bool,      # 是否全部完成
    'message': str,    # 状态消息
    'result': dict     # 详细结果
}
```

**示例：**

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    result = xt.download_history_data2(
        ["000001.SZ", "600000.SH"],
        period="1d",
        start_time="20260101",
        end_time="20260228"
    )
    print(f"进度: {result['finished']}/{result['total']}, 完成: {result['done']}")
```

---

#### `get_all_stocks()`

获取所有股票列表。

```python
def get_all_stocks(self) -> list
```

**参数：** 无

**返回值：** `list` — 股票代码列表。

**示例：**

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    stocks = xt.get_all_stocks()
    print(f"共 {len(stocks)} 只股票")
```

---

#### `get_index_list()`

获取指数列表。

```python
def get_index_list(self) -> list
```

**参数：** 无

**返回值：** `list` — 指数代码列表。

**示例：**

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    indices = xt.get_index_list()
    print(f"共 {len(indices)} 个指数")
```

---

#### `is_connected()`

检查当前连接状态。

```python
def is_connected(self) -> bool
```

**参数：** 无

**返回值：** `bool` — `True` 表示已连接，`False` 表示已断开。

**示例：**

```python
xt = XtQuantRemote("21.214.136.216", client_secret="xxx")
if xt.is_connected():
    print("连接正常")
xt.close()
```

---

#### `get_service_status()`

获取服务端运行状态。

```python
def get_service_status(self) -> dict
```

**参数：** 无

**返回值：** `dict` — 服务端状态信息，包含 `uptime`（运行时长）、`active_tokens`（活跃认证数）、`active_callbacks`（活跃回调数）等字段。

**示例：**

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    status = xt.get_service_status()
    print(f"运行时间: {status['uptime']}s, 活跃连接: {status['active_tokens']}")
```

---

#### `reconnect()`

手动触发重连。

```python
def reconnect(self) -> None
```

**参数：** 无

**返回值：** 无

**异常：** 若重连失败达到最大重试次数，抛出 `ConnectionError`。

**示例：**

```python
xt = XtQuantRemote("21.214.136.216", client_secret="xxx")
# ... 连接中断后手动重连
xt.reconnect()
```

---

#### `close()`

关闭连接，释放所有资源（心跳线程、后台服务线程、网络连接）。

```python
def close(self) -> None
```

**参数：** 无

**返回值：** 无

**示例：**

```python
xt = XtQuantRemote("21.214.136.216", client_secret="xxx")
# ... 使用完毕后关闭
xt.close()
```

### 1.3 属性

| 属性 | 类型 | 说明 | 对应 xtquant 模块 |
|------|------|------|-------------------|
| `xtdata` | `RemoteModule` | 行情数据模块代理，提供 `get_market_data_ex()`、`get_full_tick()`、`get_stock_list_in_sector()` 等行情接口 | `xtquant.xtdata` |
| `xttype` | `RemoteModule` | 类型定义模块代理，提供 `StockAccount` 等类型构造 | `xtquant.xttype` |
| `xtconstant` | `RemoteModule` | 常量定义模块代理，提供 `STOCK_BUY`、`STOCK_SELL` 等交易常量 | `xtquant.xtconstant` |
| `xtview` | `RemoteModule` | 视图控制模块代理，提供调度任务管理等功能 | `xtquant.xtview` |
| `datadir` | `RemoteModule` | QMT datadir 文件解析模块代理，提供离线 K 线和板块数据读取 | `xqshare.qmt_datadir.QmtDataReader` |

> **说明：** `RemoteModule` 是完全透明的动态代理，调用方式与本地 xtquant 完全一致。首次访问属性时自动从服务端获取对应模块对象，后续调用直接代理到远程方法。

**示例：**

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    # xtdata —— 行情数据
    stocks = xt.xtdata.get_stock_list_in_sector("沪深A股")

    # xttype —— 类型定义
    account = xt.xttype.StockAccount("12345678", "STOCK")

    # xtconstant —— 常量
    buy_type = xt.xtconstant.STOCK_BUY

    # datadir —— 离线文件解析
    df = xt.datadir.kline("600000.SH", "1d")
```

### 1.4 上下文管理器

`XtQuantRemote` 支持 Python 上下文管理器协议（`with` 语句），退出时自动调用 `close()` 释放资源。

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    stocks = xt.xtdata.get_stock_list_in_sector("沪深A股")
# 离开 with 块后自动关闭连接
```

**资源自动释放行为：**

1. 停止心跳线程
2. 停止后台服务线程（`BgServingThread`，用于异步回调）
3. 关闭 RPyC 网络连接

> **推荐：** 优先使用 `with` 语句，确保连接资源可靠释放，避免资源泄漏。

---

## 2. 全局便捷函数

xqshare 提供模块级全局函数，适合简单场景下快速使用。

### 2.1 `connect()`

创建全局连接。

```python
def connect(host=None, port=None, **kwargs) -> XtQuantRemote
```

| 参数 | 类型 | 默认值 | 说明 | 环境变量 |
|------|------|--------|------|---------|
| `host` | `str` | `"localhost"` | 服务端地址 | `XQSHARE_REMOTE_HOST` |
| `port` | `int` | `18812` | 服务端端口 | `XQSHARE_REMOTE_PORT` |
| `**kwargs` | — | — | 传递给 `XtQuantRemote` 的额外参数（如 `client_id`、`client_secret` 等） | `client_id` → `XQSHARE_CLIENT_ID`，`client_secret` → `XQSHARE_CLIENT_SECRET` |

**优先级规则：** 显式参数 > 环境变量 > 默认值

**返回值：** `XtQuantRemote` 实例（同时设为全局客户端）

**示例：**

```python
import xqshare

# 依赖环境变量配置
xqshare.connect()

# 显式指定参数
xqshare.connect(host="21.214.136.216", client_secret="my-secret")

# 通过全局代理访问
stocks = xqshare.xtdata.get_stock_list_in_sector("沪深A股")
```

### 2.2 `disconnect()`

断开全局连接。

```python
def disconnect() -> None
```

**参数：** 无

**返回值：** 无

**说明：** 调用全局客户端的 `close()` 方法，并置空全局客户端引用。

**示例：**

```python
import xqshare

xqshare.connect(host="21.214.136.216", client_secret="my-secret")
# ... 使用完毕
xqshare.disconnect()
```

### 2.3 `get_client()`

获取全局客户端实例。

```python
def get_client() -> XtQuantRemote | None
```

**参数：** 无

**返回值：** 当前全局 `XtQuantRemote` 实例，若未连接则返回 `None`。

**示例：**

```python
import xqshare

xqshare.connect(host="21.214.136.216", client_secret="my-secret")
client = xqshare.get_client()
if client and client.is_connected():
    print("全局连接正常")
```

### 2.4 全局模块代理对象

xqshare 在模块级别提供以下代理对象，支持延迟连接：

| 代理对象 | 对应属性 | 说明 |
|---------|---------|------|
| `xqshare.xtdata` | `client.xtdata` | 行情数据模块 |
| `xqshare.xttrader` | `client.xttrader` | 交易模块 |
| `xqshare.xttype` | `client.xttype` | 类型定义模块 |
| `xqshare.xtview` | `client.xtview` | 视图控制模块 |
| `xqshare.datadir` | `client.datadir` | datadir 文件解析模块 |

**工作机制：**

- **延迟连接：** 代理对象在创建时不会立即连接，只有在实际调用方法时才会检查全局客户端是否已初始化。
- **未连接时行为：** 若全局客户端未初始化（即未调用 `connect()`），访问代理对象的任何方法都会抛出 `RuntimeError`：

```python
import xqshare

# 未调用 connect()，直接访问会报错
xqshare.xtdata.get_stock_list_in_sector("沪深A股")
# RuntimeError: 请先调用 connect() 建立连接
```

---

## 3. datadir 模块 API

`datadir` 模块将 Windows 端 QMT `datadir` 目录的文件解析能力通过 RPyC 透明暴露给客户端，**无需 miniQMT 进程**即可读取 K 线、板块等数据。

### 3.1 `datadir.kline()`

读取 K 线数据（直接解析 `.DAT` 文件）。

```python
datadir.kline(symbol: str, period: str = '1d', include_raw_ts: bool = False) -> pd.DataFrame
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `symbol` | `str` | — | 股票代码，如 `"600000.SH"`、`"000001.SZ"` |
| `period` | `str` | `"1d"` | K 线周期（见下表） |
| `include_raw_ts` | `bool` | `False` | 是否保留原始 Unix 时间戳列 |

**支持的周期：**

| 周期值 | 说明 | 别名 |
|--------|------|------|
| `"1d"` | 日线 | `"day"`、`"d"`、`"86400"` |
| `"5m"` | 5 分钟线 | `"5min"`、`"300"` |
| `"1m"` | 1 分钟线 | `"1min"`、`"60"` |

**返回值：** `pd.DataFrame`

- **Index：** `DatetimeIndex`（本地时间）
- **Columns：** `open`、`high`、`low`、`close`、`volume`、`amount`、`pre_close`、`adj_factor`（当日复权因子）、`cum_adj_factor`（累计复权因子）

**示例：**

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    # 读取日线
    df = xt.datadir.kline("600000.SH", "1d")
    print(df.tail())

    # 读取5分钟线
    df5m = xt.datadir.kline("000001.SZ", "5m")

    # 保留原始时间戳
    df_raw = xt.datadir.kline("600000.SH", "1d", include_raw_ts=True)
```

### 3.2 `datadir.sector_categories()`

列出所有板块分类。

```python
datadir.sector_categories() -> list
```

**参数：** 无

**返回值：** `list[str]` — 板块分类名称列表，如 `["申万行业", "证监会行业"]`。

**示例：**

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    categories = xt.datadir.sector_categories()
    print(categories)  # ['申万行业', '证监会行业', ...]
```

### 3.3 `datadir.sectors()`

读取某分类下所有板块成分股。

```python
datadir.sectors(category: str = '申万行业') -> dict
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `category` | `str` | `"申万行业"` | 板块分类名称 |

**返回值：** `dict[str, list[str]]` — 板块名 → 成分股代码列表。

**示例：**

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    sw_sectors = xt.datadir.sectors("申万行业")
    for name, codes in sw_sectors.items():
        print(f"{name}: {len(codes)} 只")
```

### 3.4 `datadir.sector()`

读取单个板块成分股列表。

```python
datadir.sector(category: str, sector_name: str) -> list
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `category` | `str` | — | 板块分类，如 `"申万行业"` |
| `sector_name` | `str` | — | 板块名称，如 `"SW1银行"` |

**返回值：** `list[str]` — 股票代码列表。

**示例：**

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    bank_stocks = xt.datadir.sector("申万行业", "SW1银行")
    print(f"银行板块：{len(bank_stocks)} 只")
```

### 3.5 `datadir.weight()`

读取指数/板块权重数据。

```python
datadir.weight() -> dict
```

**参数：** 无

**返回值：** `dict[str, list[dict]]` — 板块名 → 成分权重列表，每个元素为 `{"symbol": str, "weight": float}`。

**示例：**

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    wt = xt.datadir.weight()
    for name, members in wt.items():
        print(f"{name}: {len(members)} 个成分")
```

### 3.6 `datadir.divid()`

读取分红除权数据。

```python
datadir.divid(exchange: str = None, code: str = None) -> pd.DataFrame
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `exchange` | `str` | `None` | 可选过滤交易所，如 `"SH"`/`"SZ"`/`"BJ"` |
| `code` | `str` | `None` | 可选过滤纯6位代码，如 `"600000"` |

**返回值：** `pd.DataFrame` — columns: `exchange`、`code`、`symbol`、`ex_date`、`ratio`、`raw_ts`。

**示例：**

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    # 所有分红数据
    df = xt.datadir.divid()

    # 仅查询浦发银行
    df = xt.datadir.divid(exchange="SH", code="600000")
```

### 3.7 `datadir.etf_list()`

读取 ETF 成分股代码列表。

```python
datadir.etf_list() -> list
```

**参数：** 无

**返回值：** `list[str]` — 纯6位 ETF 代码列表。

### 3.8 `datadir.market_list()`

读取支持的交易所列表。

```python
datadir.market_list() -> list
```

**参数：** 无

**返回值：** `list[str]` — 交易所代码列表，如 `["BJ", "SH", "SZ"]`。

### 3.9 `datadir.increase_meta()`

读取涨跌幅快照文件元数据（不解析位图内容）。

```python
datadir.increase_meta(market: str = 'SH') -> dict
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `market` | `str` | `"SH"` | 市场代码，如 `"SH"`/`"SZ"`/`"BJ"` |

**返回值：** `dict` — 包含 `market`、`file_size`、`n_dates`、`n_stocks`、`decompressed_size` 字段。

### 3.10 datadir 不可用时的处理

若服务端未配置 `QMT_DATADIR_PATH` 且无法自动推断，调用 `datadir` 相关方法时会抛出 `RuntimeError`：

```
RuntimeError: datadir 不可用：未配置 QMT_DATADIR_PATH 且无法自动推断路径
路径：<未配置>
请在 server 端 .env 中配置 QMT_DATADIR_PATH，或确认 xtdata.get_data_dir() 可用。
```

**配置指引：** 在服务端 `.env` 文件中添加：

```ini
QMT_DATADIR_PATH=D:\国金证券QMT交易端\datadir
```

> **自动推断规则：** 若未配置 `QMT_DATADIR_PATH`，server 会尝试调用 `xtdata.get_data_dir()` 获取默认路径，并自动将 `userdata_mini\datadir` 替换为 `datadir`（主数据目录，数据更全）。

---

## 4. 异常与错误处理

### 4.1 `ConnectionError`

```python
class ConnectionError(Exception):
    """连接错误"""
```

**触发场景：**

- 连接服务端失败（网络不通、端口未监听）
- 重连失败达到最大重试次数
- 连接已断开且 `auto_reconnect=False` 时尝试调用 API

**示例：**

```python
from xqshare import XtQuantRemote, ConnectionError

try:
    xt = XtQuantRemote("unreachable-host", client_secret="xxx")
except ConnectionError as e:
    print(f"连接失败: {e}")
```

### 4.2 `AuthenticationError`

```python
class AuthenticationError(Exception):
    """认证错误"""
```

**触发场景：**

- `client_secret` 与服务端配置不匹配
- `client_id` 未在服务端注册

> **注意：** 当前版本中，认证失败通常表现为 `ConnectionError`（因为认证在连接阶段进行）。`AuthenticationError` 保留用于未来更精细的认证错误区分。

### 4.3 `CallbackError`

```python
class CallbackError(Exception):
    """回调错误"""
```

**触发场景：**

- 异步回调处理过程中发生异常
- 回调函数注册/执行失败

### 4.4 RemoteModule 异常传播机制

`RemoteModule` 代理在调用远程方法时，若远程端抛出异常，该异常会**原样抛出**到客户端代码中。

```python
with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    try:
        # 假设远程端 xtdata.get_market_data() 抛出 ValueError
        result = xt.xtdata.get_market_data(...)
    except ValueError as e:
        # 远程异常被原样传播
        print(f"远程调用出错: {e}")
```

**异常处理建议：**

1. 捕获 `ConnectionError` 处理网络问题
2. 对于业务异常，参考原始 xtquant 的异常类型进行捕获
3. 若启用了 `auto_reconnect`，网络相关的异常会自动触发重连

---

## 5. 连接与重连机制

### 5.1 认证流程

xqshare 使用 HMAC token 认证机制，流程如下：

```
客户端                                    服务端
  │                                         │
  │  1. RPyC 连接建立                        │
  │ ──────────────────────────────────────► │
  │                                         │
  │  2. authenticate(client_id, secret)     │
  │ ──────────────────────────────────────► │
  │                                         │  3. 验证密钥
  │                                         │     查找 client_id 配置
  │                                         │     比对 client_secret
  │                                         │
  │  4. 返回 {level: "standard"}            │
  │ ◄────────────────────────────────────── │
  │                                         │
  │  5. 记录账号等级，认证完成                 │
  │                                         │
```

**认证步骤：**

1. 客户端发起 RPyC 连接
2. 连接建立后，客户端调用 `authenticate(client_id, client_secret)`
3. 服务端验证密钥：查找 `client_id` 对应的配置，比对 `client_secret`
4. 验证通过后，服务端返回账号信息（包含 `level` 字段表示账号等级）
5. 客户端记录账号等级，后续 API 调用受该等级权限控制

**多客户端认证：** 服务端支持为不同客户端配置独立密钥和权限等级：

```bash
# 服务端环境变量
export XQSHARE_CLIENT_app1="secret-for-app1"    # app1 的密钥
export XQSHARE_CLIENT_app2="secret-for-app2"    # app2 的密钥
```

```python
# 客户端1
xt1 = XtQuantRemote(client_id="app1", client_secret="secret-for-app1")

# 客户端2
xt2 = XtQuantRemote(client_id="app2", client_secret="secret-for-app2")
```

### 5.2 心跳保活机制

xqshare 客户端通过心跳检测保持连接活跃：

**工作流程：**

1. 连接成功后，客户端启动一个守护线程执行心跳循环
2. 每隔 `heartbeat_interval` 秒，向服务端发送 `heartbeat()` 调用
3. 若心跳调用成功，继续下一轮检测
4. 若心跳调用失败（抛出异常）且 `auto_reconnect=True`，自动触发重连
5. 若 `heartbeat_interval` 设为 `0`，禁用心跳机制

**参数配置：**

```python
xt = XtQuantRemote(
    host="21.214.136.216",
    heartbeat_interval=30,  # 每30秒发送一次心跳
)
```

**心跳检测逻辑伪代码：**

```python
while not stop_event.is_set():
    if connected:
        try:
            conn.root.heartbeat()
        except Exception:
            if auto_reconnect:
                reconnect()
    stop_event.wait(heartbeat_interval)
```

### 5.3 ReconnectPolicy 重连策略

```python
class ReconnectPolicy:
    def __init__(self, max_retries=5, base_delay=1, max_delay=30, backoff_factor=2):
        ...

    def get_delay(self, retry_count) -> float:
        delay = self.base_delay * (self.backoff_factor ** retry_count)
        return min(delay, self.max_delay)
```

**参数说明：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_retries` | `int` | `5` | 最大重连重试次数 |
| `base_delay` | `int` | `1` | 初始退避延迟（秒） |
| `max_delay` | `int` | `30` | 最大退避延迟（秒） |
| `backoff_factor` | `int` | `2` | 退避倍数（指数退避） |

**指数退避算法：**

重连间隔按指数递增，避免在服务端故障时产生过多重连请求：

| 重试次数 | 计算公式 | 延迟（秒） |
|---------|---------|-----------|
| 第 1 次 | 1 × 2⁰ | 1 |
| 第 2 次 | 1 × 2¹ | 2 |
| 第 3 次 | 1 × 2² | 4 |
| 第 4 次 | 1 × 2³ | 8 |
| 第 5 次 | 1 × 2⁴ | 16 |
| 第 6 次+ | min(1×2⁵, 30) | 30（封顶） |

**配置方式：** 通过 `XtQuantRemote` 构造参数 `max_retries` 间接配置（`ReconnectPolicy` 的其他参数使用默认值）。

```python
xt = XtQuantRemote(
    host="21.214.136.216",
    auto_reconnect=True,
    max_retries=10,  # 最多重试10次
)
```

**断线检测条件：** 当异常消息中包含以下关键词时，触发重连：

- `connection`、`closed`、`reset`、`broken`、`timeout`、`refused`、`eof`、`socket`

### 5.4 重连后自动恢复

重连成功后，xqshare 自动执行以下恢复操作：

1. **RemoteModule 缓存重置：** 将 `xtdata`、`xttype`、`xtconstant`、`xtview`、`datadir` 五个代理模块的内部缓存置空，下次调用时自动重新获取远程对象。

2. **订阅自动重启：** 遍历订阅列表，对活跃的订阅重新调用 `start()` 恢复行情推送。

**重连伪代码：**

```python
def _reconnect(self):
    while retry_count < max_retries:
        try:
            self._connect()  # 重新连接和认证

            # 重置模块缓存
            self._xtdata._module = None
            self._xttype._module = None
            self._xtconstant._module = None
            self._xtview._module = None
            self._datadir._module = None

            # 恢复订阅
            for sub in self._subscriptions:
                if sub._active:
                    sub.start()

            return  # 重连成功
        except Exception:
            retry_count += 1
            time.sleep(self._reconnect_policy.get_delay(retry_count - 1))
```

---

## 6. 数据传输与序列化

### 6.1 服务端序列化格式

为优化大数据量传输性能，服务端支持三种序列化格式，根据返回值类型自动选择：

| 格式标记 | 格式名称 | 适用场景 | 数据结构 |
|---------|---------|---------|---------|
| `"json"` | JSON | 普通字典/列表等可 JSON 序列化的数据 | `{"__xqshare_serialized__": "json", "data": "..."}` |
| `"dataframe_csv"` | DataFrame CSV | 单个 DataFrame | `{"__xqshare_serialized__": "dataframe_csv", "data": "csv_string"}` |
| `"dict_with_dataframe"` | 嵌套 DataFrame JSON | 字典中嵌套 DataFrame（如 `get_market_data_ex` 返回值） | `{"__xqshare_serialized__": "dict_with_dataframe", "data": "json_string"}` |

此外还有 `"none"` 格式用于表示 `None` 返回值。

### 6.2 客户端反序列化逻辑

客户端在 `RemoteModule._wrap_call()` 中自动调用 `_deserialize_from_transfer()` 进行反序列化，过程对用户完全透明：

```
服务端返回数据
    │
    ▼
检查是否包含 __xqshare_serialized__ 标记
    │
    ├── 无标记 → 直接返回原始数据
    │
    ├── "none" → 返回 None
    │
    ├── "json" → json.loads(data)
    │
    ├── "dataframe_csv" → pd.read_csv(data, index_col=0)
    │                     重建 DataFrame
    │
    └── "dict_with_dataframe" → json.loads(data) 后递归遍历
                                将 {"__df__": true, "csv": "..."} 还原为 DataFrame
```

**嵌套 DataFrame 反序列化细节：**

服务端将字典中的 DataFrame 标记为 `{"__df__": true, "csv": "csv_string"}`，客户端递归遍历整个数据结构，将所有标记对象还原为 `pd.DataFrame`。

### 6.3 无 pandas 环境的降级行为

当客户端未安装 `pandas` 时，反序列化逻辑会自动降级：

| 序列化格式 | 有 pandas | 无 pandas |
|-----------|----------|----------|
| `dataframe_csv` | `pd.DataFrame` | 原始 CSV 字符串 |
| `dict_with_dataframe` | 递归还原 DataFrame | 返回 JSON 解析后的原始字典（DataFrame 部分保持 `{"__df__": true, "csv": "..."}` 结构） |

> **建议：** 生产环境中强烈建议安装 pandas，以获得最佳的数据处理体验。

---

## 7. 命令行工具

安装 xqshare 后提供两个命令行工具：`xtdata`（行情）和 `xttrader`（交易）。

### 7.1 xtdata 命令行工具

**基本格式：**

```bash
xtdata [全局参数] <command> [API参数]
```

> **参数规则：** 工具参数（`--host`、`--port`、`--limit` 等）必须放在 command 之前，API 函数参数放在 command 之后。

**全局参数：**

| 参数 | 短参数 | 环境变量 | 说明 |
|------|--------|---------|------|
| `--host` | — | `XQSHARE_REMOTE_HOST` | 服务端地址 |
| `--port` | — | `XQSHARE_REMOTE_PORT` | 服务端端口 |
| `--secret` | — | `XQSHARE_CLIENT_SECRET` | 认证密钥 |
| `--client-id` | — | `XQSHARE_CLIENT_ID` | 客户端标识 |
| `--limit` | `-n` | — | 列表输出数量限制（默认 50，`0` 表示不限制） |
| `--verbose` | `-v` | — | 显示详细日志 |
| `--output` | `-o` | — | 输出文件路径 |
| `--format` | `-f` | `XQSHARE_FORMAT` | 输出格式：`text`（默认）/`json`/`csv` |
| `--compact` | — | — | 紧凑模式输出（仅对 json 格式有效，去除缩进） |
| `--list-commands` | — | — | 列出所有可用的 xtdata 命令并退出 |

**输出格式：**

| 格式 | 说明 |
|------|------|
| `text` | 人类可读的文本格式（默认） |
| `json` | JSON 格式，适合程序处理 |
| `csv` | CSV 格式，适合 DataFrame 数据 |

**`--compact` 选项：** 仅对 `json` 输出格式有效，生成无缩进的紧凑 JSON，减少输出体积。

**`--limit` 选项：** 限制列表/元组类型结果的输出数量，默认 50 条。设为 `0` 则不限制。

**常用命令示例：**

```bash
# 获取股票列表
xtdata get_stock_list_in_sector --sector-name "沪深A股"

# 限制输出数量
xtdata --limit 100 get_stock_list_in_sector --sector-name "沪深A股"

# 指定服务端地址
xtdata --host 21.214.136.216 get_full_tick --code-list "['000001.SZ']"

# 获取K线数据
xtdata get_market_data_ex --stock-list "['000001.SZ']" --period "1d" --start-time "20260101" --end-time "20260228"

# 获取交易日历
xtdata get_trading_calendar --market SH --start-time "20260101" --end-time "20261231"

# JSON 格式输出
xtdata --format json get_stock_list_in_sector --sector-name "沪深A股"

# 紧凑 JSON 输出
xtdata -f json --compact get_stock_list_in_sector --sector-name "沪深A股"

# 列出所有可用命令
xtdata --list-commands
```

### 7.2 xttrader 命令行工具

**基本格式：**

```bash
xttrader [全局参数] [交易参数] <command> [API参数]
```

**交易参数（除全局参数外）：**

| 参数 | 环境变量 | 说明 |
|------|---------|------|
| `--userdata-path` | `QMT_USERDATA_PATH` | QMT 客户端 `userdata_mini` 目录路径（**必填**） |
| `--account-id` | `QMT_ACCOUNT_ID` | 资金账号（**必填**） |
| `--account-type` | — | 账户类型（默认 `STOCK`），可选：`STOCK`/`CREDIT`/`FUTURE`/`HUGANGTONG`/`SHENGANGTONG` |

> **注意：** `account` 参数会自动补充，无需手动传递。

**常用命令：**

| 命令 | 说明 |
|------|------|
| `query_stock_positions` | 查询持仓 |
| `query_stock_asset` | 查询资产 |
| `query_stock_orders` | 查询当日委托 |
| `query_stock_trades` | 查询当日成交 |
| `order_stock` | 下单 |
| `order_cancel` | 撤单 |

**下单参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `--stock-code` | `str` | 股票代码，如 `"000001.SZ"` |
| `--order-type` | `int` | 买卖方向（23=买入，24=卖出） |
| `--order-volume` | `int` | 委托数量（股） |
| `--price-type` | `int` | 报价类型（11=限价，5=最新价） |
| `--price` | `float` | 委托价格 |

**⚠️ 风险提示：** `order_stock` 和 `order_cancel` 是真实交易操作，请务必确认参数正确后再执行。建议先在模拟环境测试。

**命令示例：**

```bash
# 查询持仓
xttrader --userdata-path "C:\QMT\userdata_mini" --account-id "12345678" query_stock_positions

# 查询资产
xttrader query_stock_asset

# 下单（买入 100 股浦发银行，限价 10.0 元）
xttrader order_stock --stock-code "000001.SZ" --order-type 23 --order-volume 100 --price-type 11 --price 10.0

# 撤单
xttrader order_cancel --order-id 1082130745

# 按订单号过滤委托
xttrader query_stock_orders --order-id 1082130745
```

**下单后追踪提示：** 下单成功后，工具会自动输出后续操作提示：

```
订单已提交，订单号: 1082130745
查询委托: xttrader query_stock_orders
查询成交: xttrader query_stock_trades
撤销订单: xttrader order_cancel --order-id 1082130745
```

### 7.3 命令行工具限制

| 限制项 | 说明 | 原因 |
|--------|------|------|
| 不支持 `subscribe` 开头命令 | 如 `subscribe_quote`、`subscribe_whole_quote` | 订阅功能需要回调函数支持 |
| 不支持 `callback` 参数 | — | 回调功能需要 Python 运行时环境 |
| 不支持 `register` 开头命令 | 如 `register_callback`（仅 xttrader） | 回调注册需要 Python 运行时环境 |

**替代方案：** 需要订阅或回调功能时，请使用 Python API 或 `examples/` 目录下的脚本。

```python
# 订阅行情请使用 Python API
from xqshare import XtQuantRemote

with XtQuantRemote("21.214.136.216", client_secret="xxx") as xt:
    def on_data(data):
        print(data)

    xt.xtdata.subscribe_quote("000001.SZ", callback=on_data)
```
