# Windows 服务器 SSH 打通手册（可复用）

> 目的：从 macOS 免密 SSH 登录一台**全新 Windows 服务器**（默认无 OpenSSH Server），用于开发调试。
> 本文基于 2026-08-24 打通 `192.168.31.233`（机器名 `win-7vfcr8m8n17`）的实操沉淀，步骤与命令可复制复用。

## 0. 前置条件

- 目标 Windows 与 macOS 网络可达（同内网，或能 ping 通）
- 能通过 **RDP（3389）或物理控制台**登录目标 Windows（首次配置 SSH 前必须有一个访问通道）
- Windows 为 Win10 1809+ / Win11 / Server 2019+（`Add-WindowsCapability` 可用）
- macOS 已有 SSH 密钥对（`~/.ssh/id_rsa.pub`，没有则 `ssh-keygen -t rsa` 生成）

## 1. macOS 端：探测现状

```bash
ping -c 3 192.168.31.233          # 网络通？TTL=128 说明是 Windows
nc -z -G 5 192.168.31.233 22      # 22 端口（SSH）是否已开
nc -z -G 5 192.168.31.233 3389    # 3389（RDP）是否开，作为备选通道
```

- 22 通 → SSH 已可用，跳到第 3 步推公钥
- 22 不通、3389 通 → 走第 2 步装 OpenSSH Server

## 2. Windows 端：安装并启动 OpenSSH Server

**RDP 登录目标机，以「管理员身份」打开 PowerShell**，逐条执行（每条单独一行，勿换行）：

```powershell
# 2.1 安装 OpenSSH Server
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

# 2.2 启动 sshd 并设开机自启
Start-Service sshd
Set-Service -Name sshd -StartupType 'Automatic'

# 2.3 防火墙放行 22（规则通常已由安装自动创建，只需把 Profile 改成 Any）
Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Profile Any
```

验证（三条都应有正常输出）：

```powershell
Get-Service sshd                                  # Status = Running
netstat -an | findstr ":22"                       # 应见 TCP 0.0.0.0:22 ... LISTENING
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' | Format-List Name, Enabled, Profile, Action
```

> 若第 2.3 条报「找不到规则」（规则不存在），则先新建：
> ```powershell
> New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
> ```

## 3. macOS 端：推公钥 + 配 config

### 3.1 推公钥（普通账户）

```bash
ssh-copy-id marcus@192.168.31.233    # 首次需输 Windows 登录密码
```

### 3.2 配置 ssh config 别名

`~/.ssh/config` 追加：

```
Host win-debug
  HostName 192.168.31.233
  User marcus
  IdentityFile ~/.ssh/id_rsa
  ServerAliveInterval 60
  ServerAliveCountMax 10
```

### 3.3 验证免密

```bash
ssh -o BatchMode=yes win-debug "whoami"   # 应直接返回 user\marcus，不提示密码
```

## 4. 两个必踩的坑（关键）

### 坑 1：防火墙规则 `Profile=Private` 挡住公用网络

OpenSSH 安装时自动建的入站规则 `OpenSSH-Server-In-TCP`，`Profile` 默认只有 `Private`。若目标机所在网络被 Windows 识别为「公用网络 (Public)」，规则不生效，22 端口被挡（表现为 `nc` 22 不通，但 sshd 已在 `Running`、`netstat` 也在 `LISTENING`）。

**解决**：`Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Profile Any`

### 坑 2：管理员组账户的公钥读 `administrators_authorized_keys`

Windows OpenSSH 对 **Administrators 组成员**有特判：公钥从
`C:\ProgramData\ssh\administrators_authorized_keys` 读取，**不是** `~\.ssh\authorized_keys`。
因此 `ssh-copy-id` 推到用户目录对管理员账户无效，免密仍报 `Permission denied (publickey,password,keyboard-interactive)`。

**解决**（管理员 PowerShell，把公钥追加到正确位置）：

```powershell
Add-Content -Force -Path $env:ProgramData\ssh\administrators_authorized_keys -Value '<你的 id_rsa.pub 完整内容>'
```

> 若不想走特判，也可注释掉 `C:\ProgramData\ssh\sshd_config` 末尾的 `Match Group administrators` 块，使其回退到默认 `authorized_keys`。用 `administrators_authorized_keys` 更省事。

## 5. 快速排查顺序（连不上时）

1. `nc -z IP 22` —— 端口不通：检查 sshd 是否 Running、防火墙 Profile 是否 Any
2. `ssh -vv IP` 看认证方式 —— `publickey,password` 都有说明服务正常，只是没 key/密码
3. 免密被拒 → 检查是否管理员账户（坑 2）、公钥是否落到正确文件、`authorized_keys` 文件权限

## 6. 本次实例记录

| 项 | 值 |
|----|----|
| IP / 机器名 | `192.168.31.233` / `win-7vfcr8m8n17` |
| SSH 别名 | `win-debug` |
| 用户 | `marcus` |
| Python | 3.13.13（`C:\Users\marcus\AppData\Local\Programs\Python\Python313\`） |
| git | 未安装（待装） |
| 用途 | xqshare 的 Windows 端开发调试 |
