# 大QMT 端部署指南（bigqmt 桥接通道）

> 本文档指导在**大QMT 完整客户端**上部署 xtquant_big_convert 桥接，让 xqshare 的 bigqmt 通道能远程调行情与交易。
> 设计背景见 `doc/channel-abstraction.md`，调研见 `doc/daqmt-signal-bridge.md`。
> 目标环境：192.168.31.233（平安大QMT，`C:\Install\pazq_qmt\pazq_qmt_2.0.8.0`，资金账号 307100903096）。

## 零、问题一句话

bigqmt 通道依赖大QMT 内置 Python 策略运行 xtquant_big_convert 桥接脚本，把大QMT 的行情/交易封装成 Redis RPC。本文说明如何把这套桥接部署到大QMT 端并接上 xqshare server。

## 一、前置条件

| # | 条件 | 说明 |
|---|------|------|
| 1 | 大QMT 开通「策略交易」Python 策略权限 | **待实测**：开户账号是否默认开放，还是需券商额外开通 |
| 2 | 内置 Python 3.6 已安装 `redis` 包 | **待实测**：大QMT 内置 Python 是否自带 redis，否则需手动装到其 site-packages |
| 3 | 一台可访问的 Redis 实例 | 用于 xqshare server ↔ 大QMT 策略之间的信号/回报传输 |
| 4 | 大QMT 完整客户端已登录 | 策略运行依赖客户端登录态 |

> 待实测点清单见 §六。

## 二、部署步骤

### 2.1 获取 xtquant_big_convert 源码

从 [litaolemo/xtquant_big_convert](https://github.com/litaolemo/xtquant_big_convert)（MIT）拉取源码，本指南只用到 `src` 目录。

### 2.2 同步桥接包到大QMT Python 目录

把以下内容复制到大QMT 内置 Python 的策略可加载目录（即策略脚本 `import` 能找得到的位置）：

- `src/bigqmt_signal_trader` 包（桥接核心：行情 + 交易封装）
- `src/BIGQMT_REDIS_DRYRUN.py`（连通性自检脚本，用于验证 Redis 与账号配置）

### 2.3 生成本地配置

复制 `bigqmt_signal_trader_local_config.example.py` 为 `bigqmt_signal_trader_local_config.py`，并填写：

- `BIGQMT_ACCOUNT_ID`：大QMT 资金账号（如 `307100903096`）
- `BIGQMT_REDIS_CONFIG`：Redis 连接信息（host / port / db / password / username，与 xqshare server 端 `BIGQMT_REDIS_*` 保持一致）
- `rpc_allow_order_methods`：设为 `False`（**下单默认关闭**，待验证阶段再显式打开）

### 2.4 在 QMT 策略编辑器加载入口脚本并启动

1. 打开大QMT 客户端的「策略交易」模块 → 策略编辑器。
2. 新建/导入入口脚本（桥接主循环：连接 Redis、监听 RPC 请求、处理行情订阅与交易指令）。
3. 设置定时运行（建议 3 秒周期），点启动运行。

> 运行后可用 §四 的验证步骤确认桥接已就绪。

## 三、服务端（xqshare server）配置

在 xqshare server 端 `.env` 中启用并配置 bigqmt 通道：

```ini
# 启用 bigqmt 通道（作为 mini 不可用时的路由候选）
XQSHARE_BIGQMT_ENABLED=true

# 下单双重门禁之一（默认 false，验证阶段暂不开启）
XQSHARE_BIGQMT_ALLOW_ORDER=false

# 透传 bigqmt 桥接配置（与大QMT 端 local_config 保持一致）
BIGQMT_ACCOUNT_ID=307100903096
BIGQMT_REDIS_HOST=127.0.0.1
BIGQMT_REDIS_PORT=6379
BIGQMT_REDIS_DB=0
# BIGQMT_REDIS_PASSWORD=
# BIGQMT_REDIS_USERNAME=
```

可选调试项：

```ini
# 强制走 bigqmt 通道（空=自动，可选 mini / bigqmt）
XQSHARE_FORCE_CHANNEL=bigqmt

# 健康探测间隔（秒），默认 30
XQSHARE_PROBE_INTERVAL=30
```

> 注意：server 端改动后需重启 xqshare server 生效（见 CODEBUDDY.md 自检边界）。

## 四、验证步骤

1. **Redis 连通性**：在大QMT 内置 Python 下运行 `BIGQMT_REDIS_DRYRUN.py`，确认能连上 Redis 且账号配置读取正常。
2. **bigqmt 通道探活**：在 xqshare 客户端调用 `get_channel_status()`，确认 `bigqmt_available` 为 `true`。
3. **行情拉取**：走 bigqmt 通道调用 `get_full_tick` / 历史数据，确认返回结构与 mini 通道一致。
4. **行情订阅**：`subscribe_whole_quote` 确认真推送 tick 回调；`subscribe_quote` 确认降级为一次性快照。
5. **交易查询**：`query_stock_positions` / `query_stock_asset` 返回正常（查询类不受下单门禁影响）。
6. **下单（验证阶段）**：显式打开双重门禁后，小单实测 `order_stock` 并确认回报（见 §六 待实测点 4）。

## 五、故障排查

| 现象 | 排查方向 |
|------|---------|
| `get_channel_status()` 里 `bigqmt_available=false` | Redis 是否可访问；大QMT 策略是否已启动；`BIGQMT_REDIS_CONFIG` 两端是否一致 |
| 桥接策略启动报 import 错误 | `bigqmt_signal_trader` 包是否放对目录；redis 包是否已装（见 §六 待实测点 2） |
| 下单被拒 | 双重门禁是否都已开启；账号是否绑定（见 §六 待实测点 3） |
| 回报不完整 | 实盘回报链路（见 §六 待实测点 4） |

## 六、待实测点清单

| # | 待实测点 | 说明 |
|---|---------|------|
| 1 | 策略权限是否开放 | 大QMT「策略交易」Python 策略对开户账号是否默认开放，还是需券商开通 |
| 2 | redis 是否内置 | 大QMT 内置 Python 3.6 是否自带 redis 包，否则需手动安装 |
| 3 | passorder 账号绑定 | 下单账号绑定方式（策略内 `set_account` 还是跟随客户端登录账号） |
| 4 | 实盘回报完整性 | 委托/成交/废单回报是否完整推回，`on_order_error` 合成 `on_stock_order(57)` 是否符合预期 |

> 这些点需在验证阶段（192.168.31.233 平安大QMT）逐一确认，结果回填本文档与 `doc/daqmt-signal-bridge.md`。

## 七、实测部署记录（2026-08-25，国金大QMT）

在 192.168.31.233 上先测了**国金大QMT**（`C:\Install\gjzqqmt_bin`，账号 25010003），未动平安大QMT。结论：**bigqmt 通道行情 + 交易查询全通**。

**实际部署差异（相对上文 Redis 方案）：**

- **传输改用 ZMQ**：本机无 redis-server（只有 redis 客户端库），而国金内置 Python 3.6 已含 `pyzmq 18.0.1`，ZMQ 免装服务端、同机 ~0.7ms。`local_config` 里 `"transport": "zmq"` + `"rpc_background_threads": True`。
- **客户端装包**：`pip install xtquant-big-convert==0.2.8 pyzmq redis`（版本与服务端源码对齐）。

**验证结果：**

| 项 | 结果 |
|---|---|
| RPC ping | ✅ account_id=25010003, allow_order_methods=False |
| get_full_tick | ✅ 平安银行 000001.SZ 收盘快照 |
| get_market_data_ex（日线） | ✅ 真实 K 线 |
| get_stock_list_in_sector | ✅ 沪深A股 5214 只 |
| query_stock_asset / positions / orders | ✅ 真实资产（cash=0.93），持仓/委托为空 |
| get_channel_status | ✅ mini_available=True + bigqmt_available=True，FORCE 走 bigqmt |

**踩过的坑（务必记录）：**

1. **xtquant shim 覆盖真 xtquant**：`pip install xtquant-big-convert` 会把它自带的 `xtquant` shim 包（`xtdata.py`/`xttype.py`/`xttrader.py`/`xtconstant.py`/`__init__.py`）覆盖到 venv site-packages，抢走真 xtquant 命名空间 → 平安 mini 通道失效。修复：`pip install --force-reinstall --no-deps xtquant==<版本>`（从镜像重装真 xtquant）。**正确做法：只装 `bigqmt_signal_trader` 包，不要装它的 xtquant shim**。
2. **psutil 依赖**：`check_env.get_miniqmt_info()` 依赖 psutil，缺失时静默返回 `not_found` → 健康探测进程检测恒失败 → mini 误判不可用。已把 `psutil>=5.9` 加入 pyproject 依赖。
3. **探活方法**：平安 miniQMT 不支持 `get_trading_calendar`（返回「功能未实现」），健康探测的 xtdata 连通性探活改用 `get_full_tick`。
4. **ssh 启动进程被回收**：ssh 会话结束会清理子进程树，server 后台进程无法经 ssh 常驻。用 schtasks 注册计划任务（`/SC ONSTART` + `/Run`）可靠常驻。
