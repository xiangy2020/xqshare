# 调研：大QMT 信号桥接机制（miniQMT 停服后的交易通道出路）

> 本文档调研「miniQMT 停服后，大QMT 如何承接交易下单」，为 xqshare 交易通道改造提供依据。
> 调研时间 2026-08-25，信息来源：迅投官方知识库 / CSDN 文件通信桥接方案 / 百家号 miniQMT 关停迁移方案（2026-08-14），并结合本机 xtquant 源码（`qmttools/functions.py`、`xtview.py`）交叉验证。
>
> **【结论已定 2026-08-25】复用开源库 xtquant_big_convert（路线 A），不再自研文件/Redis 桥接。** 详见下文「一、最终结论」。本文保留原调研的价值信息（机制、路线对比、文件桥接细节）作为背景参考。

## 零、一句话结论

大QMT 交易不再有「外部直连 API」（`XtQuantTrader` 依赖的 miniQMT/minibroker 停服后失效）。交易通道改为**「信号桥接」**：外部程序把下单信号通过**文件或消息队列**送进大QMT 内置的 Python 策略脚本，由策略脚本用内部 `passorder` 下单，回报再反向写回。核心是**大QMT 内置 Python 策略运行环境（handlebar 框架 + `passorder`）**，它不依赖 miniQMT。

## 一、最终结论（已定，2026-08-25）

**复用开源库 [xtquant_big_convert](https://github.com/litaolemo/xtquant_big_convert)（路线 A），不自研桥接。** 该库把大QMT 内置策略封装成 Redis RPC，行情 + 交易一起封装，正对 xqshare 通道抽象层的需求。设计落地见 `doc/channel-abstraction.md`，部署见 `doc/bigqmt-bridge-deploy.md`。

### 1.1 选型结论

| 项 | 结论 |
|----|------|
| 通道选型 | 复用 `xtquant_big_convert`（**MIT 协议**，GitHub litaolemo/xtquant_big_convert） |
| 传输选型 | **Redis**（毫秒级，对比文件轮询秒级；库原生支持 Redis/ZMQ/共享内存，选 Redis） |
| 封装范围 | 行情 + 交易一起封装（`BigQmtXtData` / `BigQmtXtTrader`） |

### 1.2 能力清单

| 能力 | 支持情况 |
|------|---------|
| 历史数据 / `get_full_tick` / `subscribe_whole_quote`（真推送） | ✅ 支持（无感） |
| 下单 / 撤单 / 委托 / 成交 / 持仓 / 资产 | ✅ 支持（无感，需实盘 + 显式开启） |
| `subscribe_quote` / `subscribe_quote2` | ⚠️ 降级为快照语义（回调只触发一次） |
| L2 / 期权等缺失接口 | ❌ 报清晰错误 |

### 1.3 回调模型

bigqmt 桥接侧回调与 xttrader 事件模型不同，server 端映射对齐：

- `on_stock_order` / `on_stock_trade` / `on_stock_position` / `on_stock_asset` → 对齐推给客户端
- `on_order_error` / `on_cancel_error` → 合成 `on_stock_order`（`order_status=57` 废单）
- `on_account_status` / `on_*_async_response` → 仅日志，不推送

### 1.4 客户端配置（BIGQMT_* 环境变量）

客户端/xqshare server 通过环境变量配置 bigqmt 桥接连接，透传给桥接客户端：

- `BIGQMT_ACCOUNT_ID`、`BIGQMT_REDIS_HOST`、`BIGQMT_REDIS_PORT`、`BIGQMT_REDIS_DB`、`BIGQMT_REDIS_PASSWORD`、`BIGQMT_REDIS_USERNAME`

完整配置项见 `doc/channel-abstraction.md` §十一。

### 1.5 下单双重门禁

bigqmt 通道下单默认关闭，两道门禁**都**打开才允许写操作：

1. 客户端 `XQSHARE_BIGQMT_ALLOW_ORDER`（默认 `false`）
2. 服务端/桥接侧 `rpc_allow_order_methods`（默认 `False`）

拦截写操作：`order_stock` / `order_stock_async` / `order_stock_batch` / `cancel_order_stock` / `cancel_order_stock_sysid` / `cancel_order_stock_async`。查询类不受影响。

## 二、本质区别：miniQMT vs 大QMT

| | miniQMT | 大QMT |
|---|---------|-------|
| 模型 | **外部调内部**（外部 Python 调 QMT 接口） | **内部自己跑**（策略写在 QMT 编辑器里点启动运行） |
| 交易通道 | `XtQuantTrader` → minibroker | 内置策略框架 → `passorder`（`client.callFormula('passorder')`） |
| 停服影响 | 通道消失 | 内置策略框架仍可用 |

关键（交叉验证）：`qmttools/functions.py:302` 的 `passorder` 通过 `client.callFormula(requestid, 'passorder', ...)` 下单，走的是大QMT **客户端内部**的「策略公式」通道，**不依赖 minibroker**。所以大QMT 内置策略仍可下单。

## 三、4 条迁移路线（按改动量从小到大）

| 路线 | 机制 | 延迟 | 适合 | 对 xqshare 的意义 |
|------|------|------|------|------------------|
| **A. 开源桥接工具** `xtquant_big_convert` | 大QMT 内置 Python 跑 RPC 翻译层，外部调用它，翻译成大QMT 命令；支持 Redis/ZMQ/共享内存 | 中 | 代码量大多、不想大改 | **✅ 已选**：server 端直接复用 |
| **B. 文件信号桥接** `cfquant` | 外部写 JSON 信号文件，大QMT 定时轮询读取下单 | 秒级 | 中低频、次日执行 | **最易落地**，见 §四 |
| **C. Redis 消息队列** | 外部推信号到 Redis 队列，大QMT 订阅队列 | 毫秒级 | 中高频 | 低延迟升级版（xtquant_big_convert 已覆盖） |
| **D. handlebar 重写** | 策略彻底改成大QMT handlebar 框架（ContextInfo） | - | 愿意重构 | 使用方（QmtQuant）策略层改造，非 xqshare 代理层 |

## 四、文件信号桥接详细机制（路线 B，最易落地）

> 说明：路线 B 为自研兜底方案，现已不采用（选路线 A）。本节省略为背景参考，保留其机制细节。

### 4.1 通信模型

```
外部 Python（算信号） ──写 JSON──► signals/pending/  ──轮询──► 大QMT 策略脚本
                                                                    │ passorder 下单
外部 Python（读回报） ◄──写 JSON── feedback/  ◄──────── 定时回写 ────┘
```

### 4.2 信号文件格式（JSON）

```json
{
  "signal_id": "20250415_102305_abc123",   // 唯一标识，去重
  "timestamp": "2025-04-15 10:23:05.500",
  "strategy_name": "dual_moving_average",
  "symbol": "000001.SZ",
  "action": "BUY",                          // BUY / SELL / CANCEL
  "order_type": "LIMIT",                    // LIMIT / MARKET
  "price": 14.25,
  "quantity": 100,
  "extra_params": {}
}
```

### 4.3 防重复 + 原子性（关键坑）

1. **原子写**：先写 `.tmp_` 临时文件 → `os.rename` 成正式文件名，避免大QMT 读到半成品。
2. **去重**：QMT 处理成功后把文件从 `pending/` 移到 `processed/`（加 `_success`/`_failed` 后缀），并维护 `signal_id` 缓存二次去重。
3. **绝对路径 + 读写权限**。

### 4.4 回报（QMT → 外部）

大QMT 策略定期把账户/持仓/委托写回 `feedback/` 下的 `account.json`、`positions` 等，外部轮询读取。

### 4.5 涉及的大QMT 模块

**「策略交易」模块**（不是篮子交易）：在 QMT 策略编辑器里创建 Python 策略，设置定时运行（建议 3 秒），策略内扫描信号目录 + `passorder` 下单 + 写回报。

## 五、对 xqshare 的适配建议

现状：xqshare 是「透明代理」，客户端 `xt.create_trader()` → server 端 `XtQuantTrader` 下单。停服后 `XtQuantTrader` 失效。

> **已定案**：以下「改造方向」原为自研信号桥接的规划，现改为复用 xtquant_big_convert（见 §一）。保留此节仅作为当初思考路径的存档；实际落地走 `doc/channel-abstraction.md` 的通道抽象层。

改造方向（交易通道从「直连 xttrader」改为「信号桥接」，与现有通道并存）：

1. **server 端新增「信号桥接通道」**：把客户端的下单/撤单请求，转成 JSON 信号文件写入约定目录（或推 Redis），由大QMT 内置桥接策略消费。
2. **回报回传**：server 端轮询 `feedback/` 回报文件，把委托/成交/持仓状态推回客户端（复用现有回调路由器）。
3. **双通道并存**：`create_trader`（xttrader）保留；新增信号桥接通道，miniQMT 停服后切到桥接通道，万一放开可切回。
4. **桥接策略脚本**：桥接入口部署到大QMT 策略目录（此脚本运行在大QMT 内，属部署资产，非 xqshare 包内代码）。

### 待确认点（需券商/迅投口径）

1. 大QMT「策略交易」模块的 Python 策略是否对开户账号默认开放？还是需要券商额外开通权限？
2. `passorder` 下单的账号绑定方式（策略内 `set_account`？还是跟随客户端登录账号？）。
3. 回报的实时性上限（文件轮询秒级，Redis 可毫秒级）—— 用户策略对延迟的要求决定选 B 还是 C。
4. ✅ 是否可直接采用开源 `xtquant_big_convert`（路线 A）省去自研桥接 → **已定：复用 xtquant_big_convert（见 §一）**。

> 待确认点 1–3 已并入 `doc/bigqmt-bridge-deploy.md` 的「待实测点清单」，待验证阶段（192.168.31.233 平安大QMT）逐一确认。

## 六、代码位置索引

- xtquant 内置策略下单：`xtquant/qmttools/functions.py`（`_passorder_impl` / `passorder`）
- 策略回报回调：`xtquant/qmttools/functions.py`（`register_external_resp_callback`）
- 策略运行入口：`xtquant/qmttools/stgentry.py`（`run_file`，handlebar 框架加载）
- 视图/调度（非交易）：`xtquant/xtview.py`（同样依赖「投研版/极简版」连接，停服后失效）
- xqshare 交易通道现状：`xqshare/server.py`（`exposed_create_trader` / `exposed_create_trader_and_connect`）
- 通道抽象层落地：`doc/channel-abstraction.md`；部署：`doc/bigqmt-bridge-deploy.md`
