# 交接：一台服务器只能跑一个 QMT（xqshare 负责单 QMT 契约）

> 使用方 QmtQuant 在 233 调试机排查「科创板实时行情缺失」时暴露：一台机器上并存**平安 + 国金两套 QMT**，xqshare 无「单 QMT 契约」，行情与交易各走一套造成隐式错配。本文档交接给 xqshare：**核心诉求 = 一台服务器上只能跑一个 QMT，xqshare 对此负责**。

## 零、问题一句话

xqshare server 是「单 QMT 假设」但没有**显式保证**：`import xtquant` 靠 sys.path 决定绑哪套 QMT，`.env` 的 `QMT_USERDATA_PATH`/账户又是另一套独立机制。当一台机器上有多套 QMT 时，行情（xtdata）与交易（xttrader）可能**各绑一套**，隐式错配且无告警，出了问题才暴露。xqshare 要负责「一台服务器一个 QMT」，检测多套并存、显式绑定、暴露状态。

## 一、背景：233 上实际暴露的错配

233 上两套 QMT：

| QMT | 路径 | 当前实际角色 |
|-----|------|------------|
| 平安 | `C:\Install\pazq_qmt\pazq_qmt_2.0.8.0` | 交易（`.env` `QMT_USERDATA_PATH` + `QMT_ACCOUNT_ID=307100903096`） |
| 国金 | `C:\Install\gjzqqmt_bin` | 行情（server 进程 `import xtquant` 实际拿到它，`get_data_dir()` 返回 `gjzqqmt_bin`） |

结果：行情走国金、交易走平安。科创板（688）实时行情 `subscribe_whole_quote` 不推 tick（国金通道缺口），而交易侧（平安）能拿到 688 持仓最新价——两边数据源不一致，排查时才暴露「xqshare 对『绑哪套 QMT』毫无掌控」。

## 二、契约：一台服务器一个 QMT

- **硬约束**：xtquant 是 C++ 扩展（`.pyd`，DLL 绑定），同一进程 `import xtquant` 只能是一套，无法共存多套。
- **契约**：一个 xqshare server 进程 = 一套 QMT；行情与交易必须**同源**（来自同一套 QMT 安装）。
- 现状违反契约的原因：行情（import 路径）与交易（.env userdata）是两个互不知晓的机制，各自独立指向，可能指向不同券商。

## 三、xqshare 要负责的三件事

| # | 责任 | 说明 |
|---|------|------|
| 1 | **检测** | 启动/运行时检测机器上有几套 QMT、当前绑哪套；发现多套并存（可能混淆）时**明确告警**，不静默错配 |
| 2 | **绑定** | 显式声明当前 server 绑定的**单一 QMT**（bin_dir + userdata_mini + datadir + account），行情和交易都从这一处取，保证同源 |
| 3 | **暴露** | 诊断接口告诉使用方「当前绑定哪套、机器上还有哪几套、是否同源异常」 |

## 四、方案设计

### 4.1 单一 QMT 声明（替代两套独立机制）

新增配置，一处声明当前 server 绑定的 QMT（结束「sys.path 碰运气 + .env 另一套」的双源）。**配置载体定为 yaml**（`qmt_binding.yaml`，放在 server 工作目录；不存在时回退 `.env` 单套行为）：

```yaml
qmt:
  broker: "平安证券"                                                  # 显式配置，不推断
  bin_dir: "C:\\Install\\pazq_qmt\\pazq_qmt_2.0.8.0\\bin.x64"
  userdata_mini: "C:\\Install\\pazq_qmt\\pazq_qmt_2.0.8.0\\userdata_mini"
  datadir: "C:\\Install\\pazq_qmt\\pazq_qmt_2.0.8.0\\datadir"
  account_id: "307100903096"
```

- **行情绑定**：server 启动时把 `bin_dir` 注入 `sys.path` 再 `import xtquant`，行情明确来自声明 QMT。
- **交易绑定**：`create_trader` 的 `userdata_path` 从 `userdata_mini` 取、`account_id` 默认用声明值（替代单一 `QMT_USERDATA_PATH`）。
- **兼容**：未声明时回退现有 `.env` 单套行为，零破坏。

### 4.2 多套并存检测（含 strict 模式）

- 启动时扫描常见安装根（`C:\Install\*` 等）找 `bin.x64/XtMiniQmt.exe`，对比声明 QMT 的 `bin_dir`。
- 发现**额外 QMT 安装**（≠ 当前声明）→ 按 `XQSHARE_STRICT_SINGLE_QMT` 分级处置：
  - **默认（未设 / 非 1）**：输出 WARNING 日志 + 诊断接口标注 `extra_instances`，提示「多套 QMT 并存，请确认绑定无误，避免行情/交易错配」。
  - **`XQSHARE_STRICT_SINGLE_QMT=1`**：**启动即拒绝**——抛清晰异常并中止启动（日志写明检测到的额外 QMT 列表 + 处置建议），不进入服务态。
- 不做自动切换、不管理多套——多套并存是**需人工确认的异常态**，xqshare 的职责是「发现并告知（或按 strict 拒绝启动）」，不是「支持多套」。

### 4.3 同源校验

- `import xtquant` 后校验其实际来源（如 `get_data_dir()` / 模块文件路径）是否落在声明 QMT 的安装目录内；不一致则告警（这正是 233 的「声明平安、实际国金」场景）。
- 交易侧同理：`userdata_mini` 归属声明 QMT。

### 4.4 诊断接口（客户端只读）

对齐现有 `get_channel_status()` 风格，新增 `get_qmt_binding_status()`：

```python
{
  "bound": {"bin_dir": "...", "broker": "平安证券", "account_id": "307100903096"},
  "quote_source_ok": True,      # xtdata 实际来源 == 声明 QMT？
  "trade_source_ok": True,      # userdata 归属声明 QMT？
  "extra_instances": [          # 检测到的额外 QMT 安装（多套并存告警）
    {"bin_dir": "C:\\Install\\gjzqqmt_bin\\bin.x64", "broker": "国金证券"}
  ],
}
```

## 五、已拍板决策

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 配置载体 | **yaml**（`qmt_binding.yaml`，server 工作目录；不存在时回退 `.env`） |
| 2 | 多套并存处置 | 默认 WARNING 告警 + 诊断标注；**`XQSHARE_STRICT_SINGLE_QMT=1` 时启动即拒绝** |
| 3 | broker 名 | **显式配置**（`qmt.broker` 字段，不推断） |

## 六、配置项汇总

| 配置项 | 来源 | 默认 | 说明 |
|--------|------|------|------|
| `qmt_binding.yaml` 的 `qmt.*` | yaml | 无 | 单一 QMT 声明（broker/bin_dir/userdata_mini/datadir/account_id） |
| `XQSHARE_STRICT_SINGLE_QMT` | 环境变量 | 空 | `1` = 检测到多套 QMT 并存时拒绝启动 |

> 兼容边界：无 yaml 声明时，全量回退现有 `.env`（`QMT_USERDATA_PATH`/`QMT_ACCOUNT_ID` + `import xtquant`），行为与旧版一致。

## 七、代码位置索引（预期，实现时对齐）

- 声明解析 + 同源校验：`xqshare/qmt_binding.py`（新增）
- 行情绑定（sys.path 注入）：`server.py` 的 `import xtquant` 前
- 交易绑定（userdata 取声明）：`exposed_create_trader` / `exposed_create_trader_and_connect`
- 多套检测：复用 `check_env.py` 的 miniQMT 探测扩展
- 诊断接口：`exposed_get_qmt_binding_status` + 客户端 `XtQuantRemote.get_qmt_binding_status()`

## 八、验证

- 单测：声明解析、同源校验、无声明时回退旧行为。
- 真机（233）：声明平安 QMT，检测到国金为 `extra_instances`，告警 + 诊断正确；行情/交易同源走平安后，科创板行情问题应随数据源切换而消除（或暴露为国金通道的独立问题）。
