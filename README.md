# ZKC42V e-paper DIY driver (macOS)

Zkong Valley ZKC42V — 4.2" 三色电子墨水 ESL（400x300）。
目标：在 macOS 上通过 CoreBluetooth 直接连接并驱动显示内容。

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

## 一键工具

```bash
./epaper.sh img clock.png     # 传任意图片
./epaper.sh clock             # 显示当前时间(黑底白字)
./epaper.sh time              # 同步价签内部时钟
./epaper.sh scan              # 扫 BLE 设备
```

## 构建

```bash
swiftc -o build/bleprobe mac/BLEProbe.swift
```

## 使用

```bash
./epaper.sh img photo.png
./epaper.sh clock
./epaper.sh time
```
