#!/usr/bin/env bash
# Hardware self-test: runs every script with its main options against the car.
# Needs the adapter plugged in and the ignition on. Unit tests (no car) are: .venv/bin/python -m pytest tests/
#
# Usage: scripts/selftest.sh [PORT]
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-/dev/ttyUSB0}"
TMP="$(mktemp -d)"
pass=0; fail=0

run() {                       # run NAME cmd...  -> ok/FAIL, catches tracebacks and non-zero exits
  local name="$1"; shift
  local out rc
  out="$("$@" 2>&1)"; rc=$?
  if [ $rc -ne 0 ] || grep -qE 'Traceback|Error:' <<<"$out"; then
    echo "FAIL [$rc] $name"; sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' <<<"$out" | grep -aE 'Traceback|Error|File "' | tail -5 | sed 's/^/    /'
    fail=$((fail + 1))
  else
    echo "ok   $name"; pass=$((pass + 1))
  fi
}
expect_fail() {               # like run, but the command is expected to exit non-zero with a clean message
  local name="$1"; shift
  local out rc
  out="$("$@" 2>&1)"; rc=$?
  if [ $rc -ne 0 ] && ! grep -q Traceback <<<"$out"; then echo "ok   $name (clean failure)"; pass=$((pass + 1))
  else echo "FAIL $name (expected a clean non-zero exit)"; fail=$((fail + 1)); fi
}

echo "== port $PORT =="
run "check_elm327.sh"                 "$HERE/check_elm327.sh" "$PORT"
run "probe"                           python3 "$HERE/elm327_probe.py" "$PORT"
run "live one-shot"                   python3 "$HERE/elm327_live.py" "$PORT"
run "live --pids --no-color --no-bars" python3 "$HERE/elm327_live.py" "$PORT" --pids 0C,0D,05,11 --no-color --no-bars
run "live --history 0 --wide 50"      python3 "$HERE/elm327_live.py" "$PORT" --pids 0C,05 --history 0 --wide 50
run "live --watch (8 s)"              timeout -s INT --preserve-status 8 script -qec "python3 $HERE/elm327_live.py $PORT --watch --pids 0C,05,06,14" /dev/null
run "log 8 s"                         python3 "$HERE/elm327_log.py" "$PORT" --duration 8 --quiet --out "$TMP/t.csv"
run "log --pids"                      python3 "$HERE/elm327_log.py" "$PORT" --duration 4 --quiet --pids 0C,05 --out "$TMP/t2.csv"
run "analyze"                         python3 "$HERE/analyze_log.py" "$TMP/t.csv" --md "$TMP/t.md"
run "analyze small"                   python3 "$HERE/analyze_log.py" "$TMP/t2.csv"
run "dtc"                             python3 "$HERE/elm327_dtc.py" "$PORT"
run "dtc --clear without --yes"       python3 "$HERE/elm327_dtc.py" "$PORT" --clear
run "modules --list"                  python3 "$HERE/suzuki_modules.py" --list
run "modules --module ENGINE"         python3 "$HERE/suzuki_modules.py" "$PORT" --module ENGINE --bus can
run "modules --scan --bus can"        python3 "$HERE/suzuki_modules.py" "$PORT" --scan --bus can
run "kline sweep one address"         python3 "$HERE/elm327_kline_sweep.py" "$PORT" --addrs 29 --init fast
run "obd console keys 2,3,4,6,q"      bash -c "(sleep 9; printf '2'; sleep 10; printf '1'; sleep 2; printf '3'; sleep 9; printf '4'; sleep 3; printf '1'; sleep 2; printf '3'; sleep 2; printf '6'; sleep 2; printf 'n'; sleep 3; printf 'q') | COLUMNS=160 timeout 90 script -qec 'python3 $HERE/obd.py $PORT' /dev/null"
expect_fail "bad port"                python3 "$HERE/elm327_live.py" /dev/ttyNOPE

rm -rf "$TMP"
echo; echo "passed $pass, failed $fail"
[ $fail -eq 0 ]
