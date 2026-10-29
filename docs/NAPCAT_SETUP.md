# NapCat 安装与 OneBot 配置（Windows 11）

这份文档只做一件事：把 **NapCatQQ** 跑起来，并让本软件能通过
**OneBot 11 HTTP** 连上它。

> NapCatQQ 是第三方 QQ 协议端，不是腾讯官方软件。建议使用小号，并遵守
> QQ 用户协议。不要把 NapCat 的端口暴露到公网。

## 1. 它们分别是什么

```text
QQ NT 客户端
   ↓ 由 NapCat 加载/接管
NapCatQQ
   ↓ 提供 OneBot 11 HTTP 接口
本软件（QQ 签到助手）
```

- **QQ NT**：你电脑上的 QQ 客户端。你的机器已经安装在
  `C:\Program Files\Tencent\QQNT`。
- **NapCatQQ**：第三方协议端。它负责登录 QQ、提供群/好友/消息/签到接口。
  本软件不直接登录 QQ。
- **OneBot 11**：一套接口协议，不是单独要下载的软件。NapCat 启动后可以
  开启一个 OneBot HTTP 服务，本软件向它发请求。

当前检测结果：QQ 已安装；没有检测到正在运行的 NapCat，也没有在常见目录
找到 NapCat。也就是说，**NapCat 还需要下载安装**。

## 2. 下载 NapCat

打开官方 Releases 页面：

<https://github.com/NapNeko/NapCatQQ/releases/latest>

在 Assets 里选择：

- **推荐：`NapCat.Shell.zip`**
  - 适合已经安装 QQ 的电脑。
  - 当前版本约 36 MB。
  - 解压后运行 `launcher.bat`。
- 或者 `NapCat.Shell.Windows.OneKey.zip`
  - 一键安装器，适合没有 QQ 的电脑。
  - 你已经有 QQ，所以一般不需要。

不要从陌生网盘、群文件下载，认准 GitHub Releases。

## 3. 解压和启动

### 3.1 解压到英文路径

建议解压到：

```text
C:\NapCat.Shell
```

不要放在：

- `C:\Program Files`（需要管理员权限）
- 中文路径（部分启动器/插件可能出问题）

解压后目录里应该能直接看到：

```text
launcher.bat
launcher-win10.bat
```

### 3.2 先完全退出 QQ

NapCat 需要接管 QQ 启动。你现在有多个 QQ 进程在运行，先：

1. 右下角托盘找到 QQ；
2. 右键 → 退出；
3. 确认任务管理器里没有 `QQ.exe`。

如果不退出，NapCat 可能启动失败或卡住。

### 3.3 启动 NapCat

Windows 11 双击：

```text
launcher.bat
```

Windows 10 用：

```text
launcher-win10.bat
```

如果窗口一闪而过，在该目录打开 PowerShell：

```powershell
cd "C:\NapCat.Shell"
.\launcher.bat
```

这样能看到具体报错。

启动成功后，控制台会显示类似：

```text
[NapCat] [WebUi] WebUi User Panel Url: http://127.0.0.1:6099/webui?token=xxxxx
```

这个网址和 token 很重要，复制下来。

## 4. 扫码登录 QQ

1. 打开控制台里的 WebUI 地址；
2. 进入 QQ 登录页；
3. 点二维码登录；
4. 用手机 QQ 扫码（建议小号）；
5. 按提示完成登录，必要时修改 WebUI 密码。

登录成功后，NapCat 控制台/WebUI 会显示账号在线。

## 5. 配置 OneBot 11 HTTP

这一步就是“弄 OneBot”。

进入 NapCat WebUI：

```text
网络配置 → 新建 → HTTP 服务端
```

填写：

| 项目 | 建议值 |
|---|---|
| 名称 | `qq-signin` |
| 启用 | 开 |
| Host / 监听地址 | `127.0.0.1` |
| Port / 端口 | `3000` |
| Token | 留空，或自己设一个 |
| 消息格式 | 默认即可 |

保存时勾选“保存时启用”，或者保存后手动启用。

要点：

- 选 **HTTP 服务端**，不是 HTTP 客户端，也不是 WebSocket；
- `127.0.0.1` 表示只允许本机访问，更安全；
- 端口不要和 WebUI 的 `6099` 冲突；
- 如果 3000 被占用，换一个端口，软件设置里也要填同一个；
- 如果设置了 Token，本软件设置里必须填一样的 Token。

## 6. 验证 OneBot 通不通

在项目目录运行：

```powershell
cd /d "D:\qq签到"
python tools/check_env.py --url http://127.0.0.1:3000
```

如果设置了 token：

```powershell
python tools/check_env.py --url http://127.0.0.1:3000 --token 你的token
```

正常应看到：

```text
[✓] OneBot 连接
[✓] 群聊列表
[✓] 好友列表
[✓] QQ 打卡 Cookie
```

然后打开本软件：

1. 左侧“设置”；
2. OneBot API 地址填 `http://127.0.0.1:3000`；
3. Token 填一致（没有就留空）；
4. “保存设置” → “测试连接”；
5. 右上角“连接并同步”。

## 7. 手机控制上报（可选）

手机 QQ 给自己发命令需要 NapCat 把“自身消息”上报给本软件：

1. NapCat WebUI → 网络配置 → HTTP 客户端 → 添加；
2. URL：`http://127.0.0.1:18080/event`；
3. 消息格式：`array`；
4. **上报自身消息 / reportSelfMessage：开启**；
5. Token 留空；
6. 保存后重启 NapCat。

对应配置片段：

```json
"httpClients": [
  {
    "enable": true,
    "name": "qq-signin-remote",
    "url": "http://127.0.0.1:18080/event",
    "messagePostFormat": "array",
    "reportSelfMessage": true,
    "token": "",
    "debug": false
  }
]
```

本软件只监听 `127.0.0.1:18080`，不会暴露到公网。

## 8. 常见问题

| 现象 | 原因/处理 |
|---|---|
| 双击 launcher 一闪而过 | 在 PowerShell 里运行看报错；确认 QQ 已完全退出 |
| WebUI 打不开 | 看控制台实际端口；6099 被占用会自动换端口 |
| 连接被拒绝 | OneBot HTTP 服务没启用，或端口不一致 |
| HTTP 401 | Token 不一致 |
| `get_cookies` 失败 | NapCat 版本太旧，升级到最新版 |
| 个人打卡提示 Cookie 失效 | 手机 QQ 先打开一次“已打卡 → QQ打卡”页面 |
| 群打卡接口失败 | 群没有该功能，或 NapCat 版本不支持 |
| QQ 表情发出去变成字母 `n` | 不要直接复制表情，用任务编辑框上方的“QQ 表情 → 插入” |

## 9. 安全提醒

- 使用小号；
- 不要把 3000/6099/18080 端口映射到公网；
- 不要把 `data/state.json` 发给别人（里面有 token 和联系人）；
- 这是第三方协议端，存在账号风控/封号风险，请自行评估。
