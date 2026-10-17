#!/usr/bin/env python3
"""Interactive OBD console: live watch by default, hotkeys for the other features.

Usage: obd.py [PORT] [--baud N] [--wide 150] [--history 10] [--pids 0C,0D,...]

Hotkeys (shown on screen):
  1  live watch (default)          2  read error codes (DTC)
  3  start / stop CSV recording    4  analyse the latest recording
  5  scan Suzuki modules           6  clear error codes (asks for confirmation)
  q  quit
"""
import argparse
import csv
import glob
import importlib.util
import os
import select
import shutil
import sys
import termios
import time
import tty
from collections import deque
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


live = load("elm327_live")
dtc = load("elm327_dtc")
analyze_log = load("analyze_log")
suzuki = load("suzuki_modules")

LOG_PIDS = [0x0C, 0x04, 0x43, 0x05, 0x0F, 0x46, 0x10, 0x11, 0x45, 0x4C, 0x0E, 0x06, 0x07, 0x08, 0x09,
            0x14, 0x15, 0x18, 0x19, 0x44, 0x2E, 0x3C, 0x3D, 0x42, 0x1F]
MENU = "[1] watch  [2] errors  [3] rec  [4] analyse  [5] modules  [6] clear DTC  [q] quit"


class Keys:
    """Non-blocking single-key reader. Falls back to no keys when stdin is not a terminal."""

    def __init__(self):
        self.tty = sys.stdin.isatty()
        self.saved = None

    def __enter__(self):
        if self.tty:
            self.saved = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        return self

    def __exit__(self, *exc):
        if self.saved is not None:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.saved)

    def get(self):
        if not self.tty:
            return None
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1)
        return None

    def wait(self, timeout=None):
        if not self.tty:
            return None
        r = select.select([sys.stdin], [], [], timeout)[0]
        return sys.stdin.read(1) if r else None


class Recorder:
    def __init__(self, pids):
        self.pids = pids
        self.file = self.writer = None
        self.path = None
        self.rows = 0
        self.t0 = 0

    @property
    def on(self):
        return self.file is not None

    def start(self):
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        self.path = os.path.join(ROOT, "logs", f"rec_{datetime.now():%Y%m%d_%H%M%S}.csv")
        self.file = open(self.path, "w", newline="")
        self.writer = csv.writer(self.file)
        cols = [f"{live.PIDS[p][0]} [{live.PIDS[p][1]}]".replace(" []", "") for p in self.pids]
        self.writer.writerow(["time", "elapsed_s"] + cols)
        self.rows, self.t0 = 0, time.time()

    def stop(self):
        if self.file:
            self.file.close()
        self.file = self.writer = None

    def write(self, values):
        if not self.on:
            return
        row = [datetime.now().strftime("%H:%M:%S.%f")[:-3], f"{time.time() - self.t0:.1f}"]
        row += ["" if v is None else f"{v:.3f}".rstrip("0").rstrip(".") for v in values]
        self.writer.writerow(row)
        self.file.flush()
        self.rows += 1


def page(paint, keys, title, text):
    """Show a text screen and wait for a key. Returns the key."""
    sys.stdout.write("\x1b[2J\x1b[H")
    print(paint(title, "bold"))
    print(paint("-" * min(shutil.get_terminal_size((80, 24)).columns, 78), "grey"))
    print(text)
    print()
    print(paint(MENU + "   (any other key: back to watch)", "grey"))
    sys.stdout.flush()
    return keys.wait()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=None, help="serial baud (default: auto, 38400 then 115200)")
    ap.add_argument("--wide", type=int, default=150, help="terminal width from which 3 columns are used (default 150)")
    ap.add_argument("--history", type=int, default=10, help="sparkline length in samples (default 10)")
    ap.add_argument("--pids", help="PIDs to watch (default: all supported)")
    ap.add_argument("--no-color", action="store_true")
    a = ap.parse_args()

    colour_on = not a.no_color and not os.environ.get("NO_COLOR") and sys.stdout.isatty()
    paint = live.Painter(colour_on)
    history = {}

    print("initialising adapter...")
    try:
        elm, baud = live.open_adapter(a.port, a.baud)
    except Exception as e:
        print(f"cannot open {a.port}: {e}")
        return 1
    supported = elm.supported_pids()
    watch_pids = [int(x, 16) for x in a.pids.split(",")] if a.pids else supported
    watch_pids = [p for p in watch_pids if p in live.PIDS]
    rec = Recorder([p for p in LOG_PIDS if p in supported] or LOG_PIDS)

    mode = "watch"
    pending_key = None
    with Keys() as keys:
        try:
            if colour_on:
                sys.stdout.write("\x1b[?25l")
            while True:
                key = pending_key
                pending_key = None
                if mode == "watch":
                    # one pass over the watch PIDs, checking keys between requests
                    readings, latest = [], {}
                    t0 = time.time()
                    for p in watch_pids:
                        v, text = live.decode(p, elm.query(p))
                        latest[p] = v
                        if v is not None and a.history:
                            history.setdefault(p, deque(maxlen=a.history)).append(v)
                        readings.append((p, v, text))
                        key = key or keys.get()
                        if key:
                            break
                    if not key:
                        # recorder columns may include PIDs not in the watch list
                        rec.write([latest.get(p) if p in latest else live.decode(p, elm.query(p))[0] for p in rec.pids])
                        width = shutil.get_terminal_size((80, 24)).columns
                        volt = elm.voltage()
                        try:
                            vcol = live.c_volt(float(volt.rstrip("V")))
                        except ValueError:
                            vcol = "white"
                        head = (paint(f"{datetime.now():%H:%M:%S}", "bold") + "  " + paint(elm.protocol, "cyan")
                                + "  battery " + paint(volt, vcol, "bold")
                                + paint(f"  ({len(watch_pids)} PIDs in {time.time() - t0:.1f}s)", "grey"))
                        if rec.on:
                            head += "  " + paint(f"REC {os.path.basename(rec.path)} {rec.rows} rows", "red", "bold")
                        body = live.render(readings, history, width, a.wide, paint, True, a.history)
                        sys.stdout.write("\x1b[2J\x1b[H")
                        print(head)
                        print(paint("-" * min(width, paint.vlen(head)), "grey"))
                        print("\n".join(body))
                        print()
                        print(paint(MENU, "grey"))
                        sys.stdout.flush()
                        continue

                if key in (None, "1"):
                    mode = "watch"
                    continue
                if key in ("q", "Q", "\x03"):
                    break
                if key == "2":
                    sys.stdout.write("\x1b[2J\x1b[H reading error codes...\n")
                    sys.stdout.flush()
                    text = dtc.report(elm)
                    pending_key = page(paint, keys, "Error codes (engine ECM)", text)
                elif key == "3":
                    if rec.on:
                        rec.stop()
                    else:
                        rec.start()
                    mode = "watch"
                elif key == "4":
                    files = sorted(glob.glob(os.path.join(ROOT, "logs", "*.csv")), key=os.path.getmtime)
                    if rec.on:
                        rec.file.flush()
                    target = rec.path if rec.on else (files[-1] if files else None)
                    text = analyze_log.analyze(target) if target else "no recording found in logs/"
                    pending_key = page(paint, keys, f"Analysis of {os.path.basename(target) if target else '-'}", text)
                elif key == "5":
                    sys.stdout.write("\x1b[2J\x1b[H scanning Suzuki modules (CAN, then K-line)... this takes a minute\n")
                    sys.stdout.flush()
                    lines = []
                    for name in suzuki.MODULES:
                        m = suzuki.Module(elm, name)
                        ok, _ = m.connect()
                        lines.append(f"  {name:<11} {suzuki.DESC[name]:<24} {'via ' + m.bus if ok else 'no answer'}")
                        if ok:
                            for req, label in suzuki.IDENT[:6]:
                                for p in m.request(req):
                                    if p[0] != 0x7F:
                                        lines.append(f"      {label:<12} {suzuki.describe(p)}")
                            elm.at("ATPC")
                    elm.init()      # back to plain OBD settings for the watch
                    pending_key = page(paint, keys, "Suzuki modules", "\n".join(lines))
                elif key == "6":
                    k = page(paint, keys, "Clear error codes", "This erases stored codes, freeze frame and readiness monitors.\n\nPress y to confirm, any other key to cancel.")
                    if k == "y":
                        r = elm.raw("04", 5.0)
                        ok = any(l.replace(" ", "").upper().startswith("44") for l in r)
                        pending_key = page(paint, keys, "Clear error codes", f"clear: {'done' if ok else 'failed'} {r}")
                mode = "watch"
        except KeyboardInterrupt:
            pass
        finally:
            rec.stop()
            if colour_on:
                sys.stdout.write("\x1b[?25h\x1b[2J\x1b[H")
            elm.close()
    if rec.path:
        print(f"recording saved: {rec.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
