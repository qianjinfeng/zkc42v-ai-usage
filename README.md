# ZKC42V e-paper DIY driver (macOS)

> 在 macOS 上通过 CoreBluetooth 直接驱动 Zkong Valley **ZKC42V** 4.2" 三色电子墨水价签（400x300 BWR）。
> 逆向官方协议后无需官方 App，直接用 Mac 推图、显示 AI 额度面板（含农历/节气/干支 + 道德经每日一句）。

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## 特性

- 🔗 **纯 BLE 直连**：绕过官方云端/App，CoreBluetooth 直接推帧显示。
- 🖼 **传任意图片** `img`，及 `INIT(model)+SET_SLOT+WRITE_IMG(0x30,RLE)+REFRESH(0x05)` 协议。
- 📊 **AI 额度面板** `quotas`：codex / grok / kimi / opencode-go 四行实时额度 + 相对重置时间。
- 🎨 **Apple 风格排版**：白底 tile + 单一红色强调；头部大号时间 + 农历/节气/干支；底部随机《道德经》一句 + 白话（本地缓存，无需联网）。
- 🕘 **差量刷新 + 定时窗口**：数据没变自动跳过推送；`--start/--end` 限定活跃时段省电。
- 🔬 **逆向工具链**：`scan` / `inspect` / `cmd` / `seq` / `server`（模拟官方 App）。

## 快速开始

```bash
# 1) 构建 BLE 工具
swiftc -o build/bleprobe mac/BLEProbe.swift

# 2) 扫描并找到你的价签 UUID
./epaper.sh scan

# 3) 设好环境变量后推送任意图片
EPAPER_UUID=<你的UUID> ./epaper.sh img photo.png
```

**依赖**：macOS + Python 3（Pillow）；额度面板还用到 `lunar_python`（农历）：
```bash
pip3 install pillow lunar_python
```

## 目录

- `mac/BLEProbe.swift` — BLE 扫描 + GATT Profile 分析工具
  - `scan <seconds>`：扫描并列出附近 BLE 设备（含完整广播包 hex），保存 `data/scan-<ts>.json`
  - `inspect <UUID>`：连接指定设备，导出 Services / Characteristics / 可读属性，保存 `data/profile-<uuid>.json`
  - `cmd <UUID> <char> <hex>`：连接并写单条命令
  - `seq <UUID> <listen> <char:hex>...`：连接后顺序写入多条命令，监听响应
  - `send <UUID> <frame> [--init]`：连价签并按 EPD-nRF5 0x30 帧协议发送图像
  - `server <MAC> [frame]`：模拟官方 ZkongESL App（GATT Server + 广播），价签主动连接
- `data/`：扫描与抓包结果

## 逆向结论（2026-08-08）

### 硬件
- 主控：GigaDevice **GR5513**，固件 `v1.10-gr5513`（读特征 `62750003` 得到）
- 屏幕：400x300 BWR（黑/白/红），SSD1619 类控制器

### 价签上的 GATT（价签 = server，广播服务 `62750001`）
- `62750002`：命令通道（write + notify），EPD-nRF5 风格命令；连接后回状态文本
  `slots=7 1 0`、`clock_enable=1`、`sid=...`、`mtu=244 rle=1`、`t=...`
- `62750003`：只读固件版本
- `A6ED0401`：数据服务（`A6ED0402` notify / `A6ED0403` writeNoResp / `A6ED0404` writeNoResp+indicate）

### 官方 App（ZkongESL 7.2.0，已反编译）的传图协议
**方向与直觉相反**：手机是 GATT Server，价签是 client。
1. App 调云端 `getDataByUuid` 拿到 Base64 图片数据 + 价签真实 MAC
2. 手机广播服务 `0783B03E-8535-B5A0-7140-A304D2495CB7`，厂商数据含价签 MAC
   （`[crc8, 0x06, 0x08, mac1..mac6]`，crc = XOR of {06,08,mac...}）
3. 价签扫描到自己的 MAC → 主动连接手机
4. MTU 协商后 `chunk = mtu - 20`（230B），手机按块 notify 到
   `0783B03E-...-5CB8`，价签 write ≥35B 回 ACK 流控，发完 <35B 确认

### 结论 / 状态（✅ 已打通）

**成功：Mac 直接驱动价签显示图片 + 时钟！**

关键步骤（来自 epdiy.cn 官方 Web 客户端逆向）：
1. `INIT(0x01, model=0x02)` — 初始化 SSD1619 三色面板（必须带模型参数！）
2. `SET_SLOT(0x31, [0, 0])` — 先选中槽位 0 才能写图（之前全黑的根因）
3. `WRITE_IMG(0x30, RLE flags)` — 平面按 RLE 压缩，flags：
   - 首块：`0x04 | 0x02 | (bw?0:1)`
   - 续块：`0x04 | (bw?0:1)`
4. `REFRESH(0x05)`

实测：黑底白字时钟图成功显示。固件 `rle=1` 必须用 RLE 压缩传输。

### 显示一下又变白 / 变黑？

| 现象 | 常见原因 | 处理 |
|------|----------|------|
| 先有图再**变白** | `SET_TIME`/日历模式强制 GUI 刷新（picture 模式会 fill 白）；或槽位轮播切到空槽 | 别跑 `time 1/2`；`send` 默认 `SET_SLIDE off`；同步时间后再推一次图 |
| 先有图再**变黑** | 发送末尾误发 `SLEEP(0x06)`（本固件会黑屏）；或 `INIT`/缺 `SET_SLOT` | 当前 `send` **不再发 SLEEP**；保持 `INIT(model)+SET_SLOT+WRITE+REFRESH` |
| `clock_enable=1` | 若进了时钟模式会每分钟全刷 | 不要 `./epaper.sh time 2`；只用 `img`/`quotas` |

环境变量：`EPAPER_SET_SLIDE_OFF=0` 可关掉「关轮播」命令（默认开）。

## 一键工具

```bash
./epaper.sh img clock.png     # 传任意图片
./epaper.sh clock             # 显示当前时间(黑底白字)
./epaper.sh quotas            # 拉取 codex/grok/kimi/opencode-go 额度并显示
./epaper.sh quotas --no-send  # 只生成 400×300 PNG + frame，不推 BLE
./epaper.sh quotas-loop 900   # 每 900 秒刷新额度面板（默认 15 分钟）
./epaper.sh quotas-loop 900 --start 09:00 --end 18:00   # 仅早九晚六刷新
./epaper.sh time              # 同步价签内部时钟
./epaper.sh scan              # 扫 BLE 设备
```

### AI 额度面板（quotas）

从本机已登录凭据读取额度（**不**在源码里写密钥）：

| 服务 | 凭据位置 | 数据源 |
|------|----------|--------|
| codex | `~/.codex/auth.json` | `GET chatgpt.com/backend-api/codex/usage` |
| grok | `~/.grok/auth.json` | `GET cli-chat-proxy.grok.com/v1/billing?format=credits` |
| kimi | `KIMI_API_KEY` / `KIMI_CODING_API_KEY`，或 `~/.kimi-code/credentials/kimi-code.json`（kimi-code 登录 OAuth） | `GET api.kimi.com/coding/v1/usages`；OAuth 过期走 `auth.kimi.com/api/oauth/token` 刷新 |
| opencode-go | `~/.local/share/opencode/auth.json`；完整窗口需 `OPENCODE_GO_WORKSPACE_ID` + `OPENCODE_GO_AUTH_COOKIE` | 仪表盘 scrape 或 key 探测 |

单个服务失败只显示 error/unavailable，不会中断其它三行。

额度与时间均本地化显示：剩余额度统一百分比、重置时间显示为相对时间（`3天2时` / `35分`）；
顶部显示农历 + 节气 + 天干地支；底部随机显示一句《道德经》（`tools/quotas/daodejing.json` 本地缓存）。

差量刷新：固件只支持整幅 WRITE_IMG + 全屏 REFRESH（0x30/0x05），无法做无闪烁的局部刷新。
因此每次推送前会比较上一次的额度快照——数据没变就跳过 BLE 推送（不再每次全刷闪屏）；
`./epaper.sh quotas --force` 可强制推送（比如想更新头部时间戳）。

定时刷新（二选一）：

```bash
# A) 进程内循环（默认 900s）；可加活跃时段，窗口外自动跳过刷新
./epaper.sh quotas-loop 900
./epaper.sh quotas-loop 900 --start 09:00 --end 18:00   # 早九晚六
./epaper.sh quotas-loop 900 --start 22:00 --end 06:00   # 跨午夜

# B) launchd（StartInterval=900）
cp launchd/com.local.epaper-quotas.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.local.epaper-quotas.plist
```

单元测试：

```bash
python3 -m unittest discover -s tests -v
```

## 构建

```bash
swiftc -o build/bleprobe mac/BLEProbe.swift
```

## 使用

```bash
./epaper.sh img photo.png
./epaper.sh clock
./epaper.sh quotas
./epaper.sh time
```

## 目录结构

```
.
├── epaper.sh                 # 一键入口（img/clock/quotas/quotas-loop/time/scan/...）
├── mac/BLEProbe.swift        # BLE 扫描/GATT 分析/推帧 Swift 工具
├── build/bleprobe            # 编译出的二进制（gitignored）
├── tools/
│   ├── make_image.py         # PNG → 400x300 BWR frame.bin 平面转换
│   ├── clock_image.py        # 时钟图生成
│   └── quotas/               # AI 额度面板
│       ├── cli.py            # CLI + 差量刷新 + 定时窗口
│       ├── fetch.py          # 各 provider 额度拉取
│       ├── layout.py         # Apple 风格渲染 + 农历/节气 + 道德经栏
│       ├── credentials.py    # 本地凭据发现（密钥永不输出）
│       └── daodejing.json    # 道德经本地缓存
├── fonts/Roboto.ttf          # 英文字体（网络下载，随包附带）
├── launchd/                  # 定时刷新 LaunchAgent 模板
├── tests/                    # 单元测试
└── data/                     # BLE 抓包/扫描（含真实 UUID/MAC，gitignored）
```

## 安全说明

- 额度凭据从**本机用户目录**的 `auth.json` / 环境变量读取，**源码里不保存任何密钥**。
- `credentials.py` 的 `redacted()` 保证日志/测试永不输出真实 token。
- `data/` 含价签真实 UUID 与抓包，已在 `.gitignore` 中排除，**请勿提交**。

## 许可

[MIT](LICENSE) © e-paper contributors. 第三方字体（Roboto，Apache 2.0）与本文许可证无关，见 `fonts/`。
