#!/usr/bin/env python3
"""Log OBD-II mode 01 readings to a CSV file for later analysis.

Usage: elm327_log.py [PORT] [--baud N] [--duration SEC] [--pids 0C,0D,...] [--out FILE] [--quiet]
Default PID set covers the dynamic engine values (RPM, load, temps, air, trims, O2, timing, voltage).
Each CSV row is one full pass over the PID list; the first columns are wall-clock time and elapsed seconds.
"""
import argparse
import csv
import importlib.util
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("live", os.path.join(HERE, "elm327_live.py"))
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)

DEFAULT_PIDS = [0x0C, 0x04, 0x43, 0x05, 0x0F, 0x46, 0x10, 0x11, 0x45, 0x4C, 0x0E, 0x06, 0x07, 0x08, 0x09,
                0x14, 0x15, 0x18, 0x19, 0x44, 0x2E, 0x3C, 0x3D, 0x42, 0x1F]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=None, help="serial baud (default: auto)")
    ap.add_argument("--duration", type=float, default=300, help="seconds to log (default 300)")
    ap.add_argument("--pids", help="comma-separated hex PIDs (default: dynamic engine set)")
    ap.add_argument("--out", default=None, help="CSV path (default logs/idle_<timestamp>.csv)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    pids = [int(x, 16) for x in a.pids.split(",")] if a.pids else DEFAULT_PIDS
    pids = [p for p in pids if p in live.PIDS]
    out = a.out or os.path.join(os.path.dirname(HERE), "logs", f"idle_{datetime.now():%Y%m%d_%H%M%S}.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    elm, _ = live.open_adapter(a.port, a.baud)
    try:
        supported = set(elm.supported_pids())
        pids = [p for p in pids if p in supported] or pids
        cols = [f"{live.PIDS[p][0]} [{live.PIDS[p][1]}]".replace(" []", "") for p in pids]
        with open(out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time", "elapsed_s"] + cols)
            t_start = time.time()
            rows = 0
            while time.time() - t_start < a.duration:
                row = [datetime.now().strftime("%H:%M:%S.%f")[:-3], f"{time.time() - t_start:.1f}"]
                for p in pids:
                    v, _ = live.decode(p, elm.query(p))
                    row.append("" if v is None else f"{v:.3f}".rstrip("0").rstrip("."))
                w.writerow(row)
                f.flush()
                rows += 1
                if not a.quiet:
                    print(f"\r  {rows} rows, {time.time() - t_start:.0f}s / {a.duration:.0f}s", end="", flush=True)
        print(f"\nwrote {rows} rows to {out}")
    finally:
        elm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
