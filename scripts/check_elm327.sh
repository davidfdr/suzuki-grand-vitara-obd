#!/usr/bin/env bash
# Check the ELM327 USB adapter end to end: USB detection, kernel driver,
# device node, permissions, then probe the adapter and the ECU.
#
# Usage: scripts/check_elm327.sh [PORT] [--fix]
#   PORT   serial port, default: first /dev/ttyUSB* found
#   --fix  add the current user to the dialout group (needs sudo, then re-login)

set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=""
FIX=0
for arg in "$@"; do
  case "$arg" in
    --fix) FIX=1 ;;
    *) PORT="$arg" ;;
  esac
done

ok()   { printf '  [OK]   %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }

echo "== 1. USB adapter =="
usb=$(lsusb 2>/dev/null | grep -iE 'prolific|pl2303|ftdi|ch34|cp210|qinheng|silicon labs')
if [ -n "$usb" ]; then ok "$usb"; else fail "no USB-serial adapter found (lsusb). Plug the ELM327 in."; fi

echo "== 2. Kernel driver =="
drv=$(lsmod | awk '/^(pl2303|ftdi_sio|ch341|cp210x) /{print $1}')
if [ -n "$drv" ]; then ok "driver loaded: $drv"; else fail "no USB-serial driver loaded"; fi

echo "== 3. Device node =="
if [ -z "$PORT" ]; then
  PORT=$(ls /dev/ttyUSB* 2>/dev/null | head -n1)
fi
if [ -n "$PORT" ] && [ -e "$PORT" ]; then
  ok "$PORT  ($(ls -l "$PORT" | awk '{print $1, $3":"$4}'))"
  ls -l /dev/serial/by-id/ 2>/dev/null | awk '/->/{print "         by-id: "$9}'
else
  fail "no /dev/ttyUSB* device. Check dmesg: sudo dmesg | tail"
  exit 1
fi

echo "== 4. Permissions =="
if [ -r "$PORT" ] && [ -w "$PORT" ]; then
  ok "user $USER can read/write $PORT"
else
  fail "user $USER cannot access $PORT (not in dialout group)"
  if [ "$FIX" -eq 1 ]; then
    echo "  adding $USER to dialout..."
    sudo usermod -aG dialout "$USER" && warn "log out and back in, then rerun this script"
  else
    echo "  fix:  $0 --fix        (permanent, needs re-login)"
    echo "   or:  sudo chmod a+rw $PORT   (until unplug)"
  fi
  exit 1
fi

echo "== 5. ELM327 / ECU probe =="
python3 "$HERE/elm327_probe.py" "$PORT"
