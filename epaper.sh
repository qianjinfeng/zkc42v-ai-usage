#!/bin/bash
# epaper — ZKC42V 4.2" 三色电子价签 macOS 驱动
# 用法:
#   ./epaper.sh img <png图片>           把图片显示到价签
#   ./epaper.sh clock                   显示当前时间(黑底白字时钟)
#   ./epaper.sh time                    同步价签时钟
#   ./epaper.sh scan                    扫描附近 BLE 设备
#   ./epaper.sh inspect <UUID>          查看设备 GATT Profile
set -e
cd "$(dirname "$0")"

# 近的那台价签（CoreBluetooth UUID）。如果换设备/换场景，用 scan 重新找。
export EPAPER_UUID="${EPAPER_UUID:-B06FB391-E5B5-5E90-F40A-CEEA404594CB}"

usage() { sed -n '3,8p' "$0"; exit 0; }

case "${1:-}" in
  img)
    [ -n "${2:-}" ] || usage
    python3 tools/make_image.py "$2" --out /tmp/epaper-frame.bin
    ./build/bleprobe send "$EPAPER_UUID" /tmp/epaper-frame.bin
    ;;
  clock)
    python3 tools/clock_image.py
    python3 tools/make_image.py /tmp/epaper-clock.png --out /tmp/epaper-frame.bin
    ./build/bleprobe send "$EPAPER_UUID" /tmp/epaper-frame.bin
    ;;
  time)
    CMD=$(python3 -c "
import time
ts=int(time.time()); tz=8
print(f'206a{ts:08x}{tz:02x}01')
")
    ./build/bleprobe seq "$EPAPER_UUID" 5 "62750002-D828-918D-FB46-B6C11C675AEC:$CMD"
    ;;
  scan) ./build/bleprobe scan "${2:-12}" ;;
  inspect) [ -n "${2:-}" ] || usage; ./build/bleprobe inspect "$2" ;;
  *) usage ;;
esac
