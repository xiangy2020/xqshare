# 交接：龙虎榜 get_longhubang 是 QMT 内置策略 Context API（使用方 QmtQuant 实测报告）

> 本文档由使用方项目 **QmtQuant** 的排查会话生成，交接给 xqshare 项目。
> xqshare 侧的 AI 读本文即可接手，无需追问背景。行号/版本基于 2026-08 安装的 xqshare 版本，若已变动以实际代码为准。

## 零、一句话结论

QmtQuant 龙虎榜同步调用 `xtdata.get_longhubang(...)` 报 `AttributeError: module 'xtquant.xtdata' has no attribute 'get_longhubang'`。**根因不是 xqshare 漏封了这个 API，而是 `get_longhubang` 根本不在 `xtdata` 模块上**——它是 QMT 客户端「内置 Python 策略」的 Context 对象 `C` 的专有方法（`C.get_longhubang`），xqshare 现有的「模块级 xtdata 透传 + xttrader 实例透传」两条代理通道都覆盖不到这个对象。

## 一、问题一句话

QmtQuant 的 `data_manager/sync_extended.py::sync_longhubang()` 需要拉沪深 A 股龙虎榜，但 `get_longhubang` 属于 QMT 内置策略 Context（`C`），无法通过 xqshare 现有代理获取，导致龙虎榜数据域（`stock/dragonboard`）永远 empty。

## 二、背景：使用方（QmtQuant）怎么用

QmtQuant 通过 `from env import xtdata`（env.py 统一走 xqshare 连接）做数据同步，龙虎榜同步入口：

```python
# data_manager/sync_extended.py::sync_longhubang
def sync_longhubang(stocks=None, start_time="", end_time="", max_stocks=200) -> int:
    from env import xtdata   # ← xqshare 代理的 xtdata
    if not stocks:
        basic = _db.load_stock_basic()
        stocks = list(basic.keys())[:max_stocks]
    for sym in stocks:
        try:
            df = xtdata.get_longhubang([sym], start_time, end_time)  # ← 报 AttributeError
            if df is not None and not df.empty:
                if _db.save_longhubang(sym, df):
                    total += 1
        except Exception as e:
            logger.warning(f"sync_longhubang: {sym} 失败：{e}")
    return total
```

调用链：`dashboard` 数据中心「龙虎榜」更新按钮 → `POST /api/dashboard/ext/sync` → `run_ext_sync_task` → `sync_longhubang`。

## 三、现象（2026-08-20 生产实测）

在 QmtQuant 生产（21.6.51.30，xqshare server 21.214.136.216:18812）触发龙虎榜同步：

```
PR-F sync_longhubang: 000001.SZ 失败：module 'xtquant.xtdata' has no attribute 'get_longhubang'
AttributeError: module 'xtquant.xtdata' has no attribute 'get_longhubang'
PR-F sync_longhubang: 000002.SZ 失败：module 'xtquant.xtdata' has no attribute 'get_longhubang'
...（200 只全失败）
```

结果：节点 `done`、任务 `success=True`，但落库 **0 条**（fail-soft 静默，表面成功实际无数据）。

## 四、根因（官方文档依据）

`get_longhubang` 的官方文档标注为「**内置python**」，示例签名是：

```python
def init(C):
    return

def handlebar(C):
    print(C.get_longhubang(['000002.SZ'], '20100101', '20180101'))
```

即 `C.get_longhubang`，其中 `C` 是 **QMT 客户端「内置 Python 策略」运行时的 ContextInfo 对象**，只在 QMT 客户端进程内、策略 `init(C)`/`handlebar(C)` 执行期间存在。

xqshare 现有代理通道覆盖的是：

| 通道 | 代理对象 | 覆盖 API 类型 |
|------|---------|--------------|
| `xtdata` | `xtquant.xtdata` 模块 | 模块级行情函数（`get_market_data_ex` 等） |
| `xttrader` | `create_trader()` 返回的 `XtQuantTrader` 实例 | 交易接口 |
| `xtview` / `datadir` | 对应模块/对象 | 视图 / 数据目录 |

**没有任何通道暴露 QMT 内置策略 Context `C`**。因此 `get_longhubang` 在当前 xqshare 架构下属于「API 对象边界」——不是补一个白名单条目或代理方法能解决的。

（注：北向资金 `northfinancechange1d` 返回 None 是另一个问题——VIP 权限数据，见 QmtQuant `docs/PR-xqshare-data-source-boundary.md` §3.2，本文不展开。）

## 五、实测证据

| 项 | 结果 |
|----|------|
| `xtdata.get_longhubang(['600519.SH'], '20240101', '')` | `AttributeError: module 'xtquant.xtdata' has no attribute 'get_longhubang'` |
| `xqshare.create_trader()` 返回实例的 `get_longhubang` | 也无此方法（`AttributeError: 'XtQuantTrader' object has no attribute 'get_longhubang'`） |

## 六、API 契约（若 xqshare 侧实现 Context 桥接，需导出的形状）

使用方期望的调用语义（对齐 QmtQuant 现有 `sync_longhubang`）：

```python
get_longhubang(stock_list: list[str], start_time: str = "", end_time: str = "") -> pd.DataFrame
```

- `stock_list`：股票列表，如 `['600000.SH']`（QmtQuant 逐只调用，每只 1 次）
- `start_time` / `end_time`：`'YYYYMMDD'` 格式（如 `'20200101'`），`end_time` 空 = 至今
- 返回 `pd.DataFrame`，列：`stockCode / stockName / date / reason / close / spreadRate / TurnoverVolune / Turnover_Amount`，以及两个嵌套 DataFrame 列：

| 嵌套列 | 字段 |
|--------|------|
| `buyTraderBooth` | `traderName / buyAmount / buyPercent / sellAmount / sellPercent / totalAmount / rank / direction` |
| `sellTraderBooth` | 同上 |

> 注意跨 RPyC 传输时嵌套 DataFrame 需显式序列化（xqshare 已有 `_serialize_for_transfer` / `_deserialize_from_transfer` 机制，`dict_with_dataframe` 分支支持嵌套 DataFrame），否则会重蹈 `get_divid_factors` 返回 rpyc 代理对象、本地 `len()`/`pd.DataFrame` 失败的覆辙（见 QmtQuant `docs/PR-xqshare-data-source-boundary.md` §3.5「divid-factor 序列化」）。

## 七、xqshare 侧出路（供排期，二选一）

### 方案 A：服务端加「内置策略 Context 桥接」（xqshare 侧可做，但工作量大）

xqshare 服务端与 QMT 客户端同机（Windows）。要拿到 `C.get_longhubang`，需要：

1. 在 Windows 端 QMT 里跑一个**内置 Python 策略**（QMT 客户端加载策略文件，策略里 `handlebar(C)` 拿到 `C`）；
2. 策略内部把 `C.get_longhubang(...)` 的结果落盘（如写文件 / 共享内存 / 本地 socket），xqshare 服务端从该出口读取；
3. xqshare 服务端新增一个 `exposed_get_longhubang(...)`（或 `datadir` 式代理），把结果序列化后返回客户端。

难点：QMT 内置策略是「事件驱动、在 QMT 客户端进程内按 bar 触发」的模型，`handlebar(C)` 不是「随时可调用」的远程函数——需要设计一个「请求→策略响应」的通道（策略常驻、轮询/监听请求、返回结果）。这是 xqshare 目前没有的能力（现有的都是「同步 RPC 透传」或「单向事件推送」）。

### 方案 B：换数据源（不依赖 xqshare）

龙虎榜数据公开可得（沪深交易所官网每日披露 / 第三方数据接口），QmtQuant 侧可自行评估从这些渠道抓取，绕过 QMT Context 限制。此方案 xqshare 侧无需改动。

**建议**：先由使用方（QmtQuant）确认龙虎榜是「必须从 QMT 拿」还是「可换数据源」；若必须走 QMT，再评估方案 A 的 Context 桥接投入。

## 八、使用方（QmtQuant）侧的表结构与接入预期

若 xqshare 侧打通，QmtQuant 侧 `save_longhubang` 已就绪，预期落库到两张表（DDL 摘录，完整见 QmtQuant `docs/PR-F-longhubang-north.md`）：

```sql
CREATE TABLE IF NOT EXISTS longhubang (
    symbol TEXT NOT NULL, trade_date DATE NOT NULL, reason TEXT,
    close DOUBLE, spread_rate DOUBLE, turnover_volume DOUBLE, turnover_amount DOUBLE,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, trade_date)
);
CREATE TABLE IF NOT EXISTS longhubang_booth (
    symbol TEXT NOT NULL, trade_date DATE NOT NULL, direction INTEGER NOT NULL,
    trader_name TEXT, buy_amount DOUBLE, buy_percent DOUBLE,
    sell_amount DOUBLE, sell_percent DOUBLE, total_amount DOUBLE, rank INTEGER,
    PRIMARY KEY (symbol, trade_date, direction, trader_name, rank)
);
```

`save_longhubang` 已实现「主表 + 席位拆分」逻辑，拿到上面 §6 的 DataFrame 即可落库。

## 九、相关文档位置索引

| 位置 | 内容 |
|------|------|
| QmtQuant `docs/stock_api_doc.md:1149` | `C.get_longhubang` 官方文档（「内置python」） |
| QmtQuant `docs/PR-xqshare-data-source-boundary.md` §3.1 | 龙虎榜数据源边界实测（2026-08-16） |
| QmtQuant `docs/PR-F-longhubang-north.md` | 龙虎榜 + 北向设计（表结构 / 字段 / save 逻辑） |
| QmtQuant `data_manager/sync_extended.py::sync_longhubang` | 使用方调用入口 |
| xqshare `xqshare/server.py` `LoggingProxy.__getattr__` | 代理通道：`service_instance` 暴露方法优先，否则 `getattr(target, name)` 透传底层模块 |
| xqshare `xqshare/server.py` `exposed_get_xtdata` | 模块级 xtdata 透传出口 |
| xqshare `xqshare/client.py` `RemoteModule.__getattr__` | 客户端动态代理（`getattr(module, name)` 失败即 AttributeError） |
