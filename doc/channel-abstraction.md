# 通道抽象层设计（大QMT ↔ miniQMT 无感切换）

> 本文档是「大QMT ↔ miniQMT 无感切换」能力的核心设计文档。设计决策已与用户确认，实现与本文档严格一致。
> 背景调研见 `doc/miniqmt-eol-adaptation.md`（停服背景）与 `doc/daqmt-signal-bridge.md`（交易通道出路调研）。
> 部署指南见 `doc/bigqmt-bridge-deploy.md`。

## 零、问题一句话

miniQMT 逐步停服后，xqshare 依赖的行情（`xtdata`）与交易（`xttrader`）宿主消失。本方案在 server 端加一层**通道抽象层（ChannelRouter）**，自动探测 miniQMT 可用性：可用走 mini 通道（xtdata/xttrader），不可用自动切 bigqmt 通道（复用开源库 xtquant_big_convert，Redis RPC 封装行情+交易），恢复后自动切回，**使用方一行代码不改**。

## 一、结论

- **通道选型**：复用开源库 [xtquant_big_convert](https://github.com/litaolemo/xtquant_big_convert)（MIT 协议），把大QMT 内置策略封装成 Redis RPC，行情与交易一起封装。
- **无感边界**：历史数据、`get_full_tick`、`subscribe_whole_quote`（真推送）、下单/查询/回调 = 无感；`subscribe_quote`/`subscribe_quote2` = 降级为快照语义；L2/期权等 bigqmt 缺失接口 = 报清晰错误。
- **路由粒度**：行情侧通道级动态解析；交易侧在 `create_trader` 创建时锁定通道，运行中不切换。
- **安全**：下单双重门禁（客户端 `XQSHARE_BIGQMT_ALLOW_ORDER` + 服务端 `rpc_allow_order_methods`），默认关闭。

## 二、背景（xqshare 当前怎么用 miniQMT）

```
Mac/Linux 客户端 ──RPyC──► Windows server ──import xtquant──► miniQMT/XtMiniQmt.exe
```

- 行情：`xqshare.xtdata` → 远端 `xtdata`（依赖 miniQMT 进程 + 登录）
- 交易：`xt.create_trader()` → 远端 `XtQuantTrader(userdata_mini, session_id)`（依赖 miniQMT + `userdata_mini`）
- 文件解析：`xqshare.datadir` → 远端 `QmtDataReader()` 直接读 .DAT 文件，**无需任何进程**

停服后 `xtdata`/`xttrader` 宿主消失，但大QMT 完整客户端（`XtItClient.exe`）仍在，其内置 Python 策略环境（handlebar 框架 + `passorder`）不依赖 miniQMT，可继续下单/回传行情。xtquant_big_convert 正是把这一能力封装成 Redis RPC 的开源实现。

## 三、架构图

```
使用方（QmtQuant 等）
        │  客户端 API 不变（xtdata / create_trader / ...）
        ▼
┌─────────────────────── xqshare client ───────────────────────┐
│  XtQuantRemote  ·  get_channel_status() 诊断接口（新增）        │
└──────────────────────────────┬───────────────────────────────┘
                               │ RPyC (18812)
                               ▼
┌─────────────────────── xqshare server ───────────────────────┐
│  LoggingProxy（target_resolver 动态解析当前目标）                │
│                          │                                     │
│                 ┌────────▼────────┐                            │
│                 │  ChannelRouter  │  通道抽象层（新增）          │
│                 │  健康探测 + 路由  │                            │
│                 └───┬─────────┬───┘                            │
│              mini ▼         ▼ bigqmt                           │
│        ┌──────────────┐ ┌──────────────────────────────┐       │
│        │ mini 通道     │ │ bigqmt 通道                   │       │
│        │ xtdata       │ │ BigQmtXtData（Redis RPC 代理） │       │
│        │ xttrader     │ │ BigQmtXtTrader（Redis RPC 代理）│      │
│        └──────┬───────┘ └──────────────┬───────────────┘       │
└───────────────┼────────────────────────┼───────────────────────┘
                ▼                        ▼
        XtMiniQmt.exe            Redis ──► 大QMT 内置 Python 策略
        (miniQMT 进程)                    (xtquant_big_convert 桥接)
```

两个通道实现同一套接口语义，客户端看到的始终是同一套 `xtdata`/`xttrader` 调用。`datadir` 保持独立接口，**不进路由**。

## 四、路由逻辑

### 4.1 行情通道：通道级动态解析

- server 端 `LoggingProxy` 新增 `target_resolver`：**每次属性访问**从 `ChannelRouter` 取当前目标（mini 的 `xtdata` 或 bigqmt 的 `BigQmtXtData`），按需懒加载。
- 好处：切通道无需重启 server，也无需客户端重建 `xtdata` 代理；下一次属性访问即命中新目标。
- 双通道都不可用时（mini 探活失败 + bigqmt RPC ping 失败），抛**清晰错误**（提示当前无可用数据源及原因），不静默返回空数据。
- **订阅锁定创建时通道**：`subscribe_whole_quote` / `subscribe_quote` 创建的订阅对象在创建那一刻锁定通道，之后不随路由切换迁移；已建立的订阅在通道切换后由重连/恢复机制按新通道重建（见「订阅自动恢复」）。

### 4.2 交易通道：创建时锁定

- `create_trader` / `create_trader_and_connect` 按当前路由决定构造 `XtQuantTrader`（mini）或 `BigQmtXtTrader`（bigqmt）。
- **创建后运行中不切换**：已创建的 trader 实例绑定创建时通道，避免持仓/委托状态跨通道错乱。切换发生在下次 `create_trader` 调用。
- bigqmt 通道**忽略 `userdata_path`**（大QMT 内置策略不需要 `userdata_mini` 路径）；账号走 `BIGQMT_ACCOUNT_ID`。

### 4.3 通道选择结果

| XQSHARE_FORCE_CHANNEL | 路由行为 |
|----------------------|---------|
| 空（默认） | 自动：mini 可用走 mini，否则 bigqmt 可用走 bigqmt，都不可用报错 |
| `mini` | 强制 mini（不可用时报清晰错误） |
| `bigqmt` | 强制 bigqmt（不可用时报清晰错误） |

## 五、无感边界（三档）

| 能力 | 通道表现 | 档位 |
|------|---------|------|
| 历史数据（K 线/板块/因子等） | mini: `xtdata`；bigqmt: `BigQmtXtData` 透传 | ✅ 无感 |
| `get_full_tick` 快照 | mini / bigqmt 均支持 | ✅ 无感 |
| `subscribe_whole_quote`（全推行情） | 真推送，tick 实时回调 | ✅ 无感 |
| 下单 / 撤单 / 委托 / 成交 / 持仓 / 资产 | mini: `xttrader`；bigqmt: `BigQmtXtTrader` 桥接 | ✅ 无感（需实盘 + 显式开启，见 §八） |
| 交易/行情回调事件 | 回调映射对齐（见 §六） | ✅ 无感 |
| `subscribe_quote` / `subscribe_quote2`（单股订阅） | bigqmt 无真推送 → 快照语义，回调只触发一次 | ⚠️ 降级 |
| L2 行情 / 期权等 bigqmt 缺失接口 | 调用即抛清晰错误 | ❌ 报错 |

> 「无感」定义：使用方代码不变，返回结构与语义一致，仅内部数据源切换。
> 「降级」定义：接口仍可调用，但语义从「持续推送」退化为「一次性快照」，回调仅触发一次。
> 「报错」定义：bigqmt 未实现的接口抛明确异常，提示该能力在 bigqmt 通道不可用。

## 六、回调映射

bigqmt 桥接侧的回调事件与 mini（xttrader）事件模型不同，server 端做映射对齐：

| bigqmt 侧事件 | 映射结果 |
|--------------|---------|
| `on_order_error` / `on_cancel_error` | 合成 `on_stock_order`（`order_status=57` 废单），推给客户端 |
| `on_account_status` | 仅日志，不推送客户端 |
| `on_*_async_response`（各异步回报） | 仅日志，不推送客户端 |
| 委托 / 成交 / 持仓 / 资产 | 对齐 `on_stock_order` / `on_stock_trade` / `on_stock_position` / `on_stock_asset` |

> 目标：客户端注册的 `register_trader_callback`（`on_stock_*` 事件）在两种通道下都收到同构事件，实现无感。

## 七、健康探测策略

`ChannelRouter` 周期性探测两通道可用性，决定路由。

| 通道 | 探测方式 |
|------|---------|
| mini | 进程检测 `XtMiniQmt.exe`（psutil）+ `xtdata.get_full_tick` 探活（超时 5s） |
| bigqmt | RPC ping（ZMQ/Redis，验证传输 + 大QMT 策略进程存活） |

- **探测间隔**：`XQSHARE_PROBE_INTERVAL`，默认 30s。
- **防抖**：连续 2 次探测结果一致才切换路由（避免单次抖动导致频繁切换）。
- **探测结果**：写入路由状态，供路由逻辑与 `get_channel_status()` 读取。

## 八、能力边界（重要）

1. **`subscribe_quote` 快照降级**：bigqmt 通道无单股真推送，`subscribe_quote`/`subscribe_quote2` 退化为「一次性快照 + 回调触发一次」。需要持续推送请用 `subscribe_whole_quote`。
2. **下单需实盘 + 显式开启**：bigqmt 通道下单是真实交易，默认**双重门禁关闭**（见 §九），且需大QMT 实盘环境（详见 `doc/bigqmt-bridge-deploy.md`）。
3. **L2 / 期权等缺失接口报错**：bigqmt 未实现的接口直接抛清晰错误，不做静默降级。
4. **datadir 不进路由**：`xqshare.datadir` 保持独立，始终可用（无需任何进程），作为离线/兜底行情源。

## 九、安全：下单双重门禁

bigqmt 通道下单默认关闭，两道门禁**都**打开才允许写操作：

1. **客户端门禁**：`XQSHARE_BIGQMT_ALLOW_ORDER`（默认 `false`）
2. **服务端门禁**：桥接侧 `rpc_allow_order_methods`（默认 `False`）

拦截的写操作（全部）：

- `order_stock`
- `order_stock_async`
- `order_stock_batch`
- `cancel_order_stock`
- `cancel_order_stock_sysid`
- `cancel_order_stock_async`

任一门禁未开启时，调用上述任一方法返回清晰错误（提示需开启下单权限）。查询类操作（持仓/资产/委托/成交）不受影响。

## 十、客户端诊断接口

客户端新增 `get_channel_status()` 诊断接口（只读，不影响路由），返回：

```python
{
    "mode": "mini",            # 生效通道: mini / bigqmt / none（受 XQSHARE_FORCE_CHANNEL 影响）
    "mini_available": True,    # 最近一次 mini 探测结果
    "bigqmt_available": False, # 最近一次 bigqmt 探测结果
    "checked_at": 1756000000,  # 最近一次探测时间戳
    "detail": {                # 各通道探测详情
        "mini": True,
        "bigqmt": False,
    }
}
```

> **客户端不主动推切换事件**：通道切换对上层透明，使用方需要感知时主动调用 `get_channel_status()` 查询即可。

## 十一、配置项说明

### 服务端（xqshare server）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `XQSHARE_BIGQMT_ENABLED` | `false` | 是否启用 bigqmt 通道（路由候选） |
| `XQSHARE_BIGQMT_ALLOW_ORDER` | `false` | 是否允许 bigqmt 通道下单（双重门禁之一） |
| `XQSHARE_FORCE_CHANNEL` | 空 | 强制通道：空=自动，`mini`/`bigqmt` 强制 |
| `XQSHARE_PROBE_INTERVAL` | `30` | 健康探测间隔（秒） |

### 透传 bigqmt 桥接配置（BIGQMT_*）

| 配置项 | 说明 |
|--------|------|
| `BIGQMT_ACCOUNT_ID` | 大QMT 资金账号 |
| `BIGQMT_REDIS_HOST` | Redis 主机 |
| `BIGQMT_REDIS_PORT` | Redis 端口 |
| `BIGQMT_REDIS_DB` | Redis DB 编号 |
| `BIGQMT_REDIS_PASSWORD` | Redis 密码（可选） |
| `BIGQMT_REDIS_USERNAME` | Redis 用户名（可选） |

这些 BIGQMT_* 配置由 xqshare server 透传给 bigqmt 桥接客户端，与大QMT 端策略脚本的 `bigqmt_signal_trader_local_config.py` 保持一致（详见部署指南）。

## 十二、交付与验证

- **本轮交付**：通道抽象层代码 + mock 测试 + 本文档。
- **真机验证**：192.168.31.233（平安大QMT）留待验证阶段，覆盖策略权限、Redis 内置、passorder 账号绑定、实盘回报完整性（见 `doc/bigqmt-bridge-deploy.md` 待实测点）。

## 代码位置索引

- 通道路由核心：server 端 `ChannelRouter`（新增）
- 行情动态解析：server 端 `LoggingProxy` 的 `target_resolver`（新增）
- 交易创建锁定：server 端 `exposed_create_trader` / `exposed_create_trader_and_connect`
- bigqmt 行情代理：`BigQmtXtData`（新增）
- bigqmt 交易代理：`BigQmtXtTrader`（新增）
- 客户端诊断：`XtQuantRemote.get_channel_status()`（新增）
- 既有 mini 探测：`check_env.py` 的 `get_miniqmt_info()`（进程探测，保留作为健康探测输入）
