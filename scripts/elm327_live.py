#!/usr/bin/env python3
"""Read live OBD-II sensor data (mode 01) through an ELM327 adapter.

Usage:
  elm327_live.py [PORT] [--baud N] [--watch] [--interval S] [--pids 0C,0D,05]

Without --watch, reads every supported PID once and prints them.
With --watch, refreshes the screen continuously until Ctrl+C.
Layout: 3 columns when the terminal is at least --wide columns (default 120),
one column otherwise. The layout is re-evaluated on every refresh.

Numeric readings get a colour by range (green normal, yellow high, red alarm,
blue cold/lean), a bar gauge over the PID's range, and in --watch mode a
sparkline of the last --history samples. Bars and sparklines appear only when
the column is wide enough. Colour is disabled with --no-color, NO_COLOR, or
when stdout is not a terminal.
"""
import argparse
import os
import re
import shutil
from collections import deque
import sys
import time
from datetime import datetime

try:
    import serial
except ImportError:
    print("pyserial is missing: pip install pyserial")
    sys.exit(1)


# --------------------------------------------------------------------------
# PID decoders. Each entry: pid -> (name, unit, decoder(bytes) -> value|str)
# --------------------------------------------------------------------------
def temp(b):        return b[0] - 40
def pct(b):         return b[0] * 100 / 255
def trim(b):        return (b[0] - 128) * 100 / 128
def word(b):        return b[0] * 256 + b[1]


FUEL_SYSTEM = {0: "off", 1: "open loop (cold)", 2: "closed loop", 4: "open loop (load)",
               8: "open loop (fault)", 16: "closed loop (fault)"}
SEC_AIR = {1: "upstream", 2: "downstream", 4: "off/outside", 8: "diagnostics"}
OBD_STD = {1: "OBD-II (CARB)", 2: "OBD (EPA)", 3: "OBD+OBD-II", 4: "OBD-I", 5: "none",
           6: "EOBD", 7: "EOBD+OBD-II", 8: "EOBD+OBD", 9: "EOBD+OBD+OBD-II", 10: "JOBD",
           11: "JOBD+OBD-II", 12: "JOBD+EOBD", 13: "JOBD+EOBD+OBD-II"}
FUEL_TYPE = {0: "n/a", 1: "gasoline", 2: "methanol", 3: "ethanol", 4: "diesel", 5: "LPG",
             6: "CNG", 7: "propane", 8: "electric", 9: "bifuel gasoline", 10: "bifuel methanol",
             11: "bifuel ethanol", 12: "bifuel LPG", 13: "bifuel CNG", 14: "bifuel propane",
             15: "bifuel electric", 16: "bifuel elec+comb", 17: "hybrid gasoline",
             18: "hybrid ethanol", 19: "hybrid diesel", 20: "hybrid electric",
             21: "hybrid elec+comb", 22: "hybrid regen", 23: "bifuel diesel"}


def monitor_status(b):
    mil = "MIL ON" if b[0] & 0x80 else "MIL off"
    return f"{mil}, {b[0] & 0x7F} DTC"


def fuel_status(b):
    s = FUEL_SYSTEM.get(b[0], f"?{b[0]}")
    if len(b) > 1 and b[1]:
        s += " / " + FUEL_SYSTEM.get(b[1], f"?{b[1]}")
    return s


def o2_narrow(b):
    v = b[0] / 200
    if b[1] == 0xFF:
        return v, f"{v:.3f} V"
    return v, f"{v:.3f} V {trim(b[1:]):+.1f}%"


def o2_wide_v(b):
    lam = word(b) * 2 / 65536
    return lam, f"λ {lam:.3f} {word(b[2:]) * 8 / 65536:.3f} V"


def o2_wide_i(b):
    lam = word(b) * 2 / 65536
    return lam, f"λ {lam:.3f} {word(b[2:]) / 256 - 128:+.2f} mA"


def o2_present(b):
    banks = []
    for bank, nib in ((1, b[0] & 0x0F), (2, b[0] >> 4)):
        s = [str(i + 1) for i in range(4) if nib & (1 << i)]
        if s:
            banks.append(f"B{bank}:S" + ",".join(s))
    return " ".join(banks) or "none"


def cat_temp(b):    return word(b) / 10 - 40


COMMON_MON = ("misfire", "fuel sys", "components")
SPARK_MON = ("catalyst", "heated cat", "evap", "sec air", "A/C refrig", "O2 sensor", "O2 heater", "EGR/VVT")
DIESEL_MON = ("NMHC cat", "NOx/SCR", "-", "boost", "-", "exhaust gas", "PM filter", "EGR/VVT")


def monitor_cycle(b):
    """PID 41: readiness of the emission monitors in this drive cycle."""
    avail, incomplete = [], []
    for i, name in enumerate(COMMON_MON):
        if b[1] & (1 << i):
            avail.append(name)
            if b[1] & (1 << (i + 4)):
                incomplete.append(name)
    names = DIESEL_MON if b[1] & 0x08 else SPARK_MON
    for i, name in enumerate(names):
        if b[2] & (1 << i):
            avail.append(name)
            if b[3] & (1 << i):
                incomplete.append(name)
    if not avail:
        return "no monitors"
    if not incomplete:
        return f"all {len(avail)} monitors complete"
    return f"{len(incomplete)}/{len(avail)} incomplete: " + ", ".join(incomplete)


def sec_o2_trim(b):
    """PIDs 55-58: trim for two banks, 0x00 means bank not present."""
    parts = [f"{trim(b):+.1f}%"]
    if len(b) > 1 and b[1]:
        parts.append(f"{trim(b[1:]):+.1f}%")
    return trim(b), " ".join(parts)


PIDS = {
    0x01: ("Monitor status",            "",     monitor_status),
    0x03: ("Fuel system status",        "",     fuel_status),
    0x04: ("Engine load",               "%",    pct),
    0x05: ("Coolant temp",              "°C",   temp),
    0x06: ("Short fuel trim B1",        "%",    trim),
    0x07: ("Long fuel trim B1",         "%",    trim),
    0x08: ("Short fuel trim B2",        "%",    trim),
    0x09: ("Long fuel trim B2",         "%",    trim),
    0x0A: ("Fuel pressure",             "kPa",  lambda b: b[0] * 3),
    0x0B: ("Intake manifold press.",    "kPa",  lambda b: b[0]),
    0x0C: ("Engine RPM",                "rpm",  lambda b: word(b) / 4),
    0x0D: ("Vehicle speed",             "km/h", lambda b: b[0]),
    0x0E: ("Timing advance",            "°",    lambda b: b[0] / 2 - 64),
    0x0F: ("Intake air temp",           "°C",   temp),
    0x10: ("MAF air flow",              "g/s",  lambda b: word(b) / 100),
    0x11: ("Throttle position",         "%",    pct),
    0x12: ("Secondary air status",      "",     lambda b: SEC_AIR.get(b[0], f"?{b[0]}")),
    0x13: ("O2 sensors present",        "",     o2_present),
    0x14: ("O2 B1S1",                   "",     o2_narrow),
    0x15: ("O2 B1S2",                   "",     o2_narrow),
    0x16: ("O2 B1S3",                   "",     o2_narrow),
    0x17: ("O2 B1S4",                   "",     o2_narrow),
    0x18: ("O2 B2S1",                   "",     o2_narrow),
    0x19: ("O2 B2S2",                   "",     o2_narrow),
    0x1A: ("O2 B2S3",                   "",     o2_narrow),
    0x1B: ("O2 B2S4",                   "",     o2_narrow),
    0x1C: ("OBD standard",              "",     lambda b: OBD_STD.get(b[0], f"?{b[0]}")),
    0x1F: ("Run time since start",      "s",    word),
    0x21: ("Distance with MIL on",      "km",   word),
    0x22: ("Fuel rail press. (vac)",    "kPa",  lambda b: word(b) * 0.079),
    0x23: ("Fuel rail gauge press.",    "kPa",  lambda b: word(b) * 10),
    0x24: ("O2 wide B1S1",              "",     o2_wide_v),
    0x25: ("O2 wide B1S2",              "",     o2_wide_v),
    0x26: ("O2 wide B1S3",              "",     o2_wide_v),
    0x27: ("O2 wide B1S4",              "",     o2_wide_v),
    0x28: ("O2 wide B2S1",              "",     o2_wide_v),
    0x29: ("O2 wide B2S2",              "",     o2_wide_v),
    0x2A: ("O2 wide B2S3",              "",     o2_wide_v),
    0x2B: ("O2 wide B2S4",              "",     o2_wide_v),
    0x2C: ("Commanded EGR",             "%",    pct),
    0x2D: ("EGR error",                 "%",    trim),
    0x2E: ("Commanded evap purge",      "%",    pct),
    0x2F: ("Fuel level",                "%",    pct),
    0x30: ("Warm-ups since clear",      "",     lambda b: b[0]),
    0x31: ("Distance since clear",      "km",   word),
    0x32: ("Evap vapor pressure",       "Pa",   lambda b: (word(b) - 65536 if word(b) & 0x8000 else word(b)) / 4),
    0x33: ("Barometric pressure",       "kPa",  lambda b: b[0]),
    0x34: ("O2 wide B1S1 (mA)",         "",     o2_wide_i),
    0x35: ("O2 wide B1S2 (mA)",         "",     o2_wide_i),
    0x36: ("O2 wide B1S3 (mA)",         "",     o2_wide_i),
    0x37: ("O2 wide B1S4 (mA)",         "",     o2_wide_i),
    0x38: ("O2 wide B2S1 (mA)",         "",     o2_wide_i),
    0x39: ("O2 wide B2S2 (mA)",         "",     o2_wide_i),
    0x3A: ("O2 wide B2S3 (mA)",         "",     o2_wide_i),
    0x3B: ("O2 wide B2S4 (mA)",         "",     o2_wide_i),
    0x3C: ("Catalyst temp B1S1",        "°C",   cat_temp),
    0x3D: ("Catalyst temp B2S1",        "°C",   cat_temp),
    0x3E: ("Catalyst temp B1S2",        "°C",   cat_temp),
    0x3F: ("Catalyst temp B2S2",        "°C",   cat_temp),
    0x41: ("Monitors this cycle",       "",     monitor_cycle),
    0x42: ("Control module voltage",    "V",    lambda b: word(b) / 1000),
    0x43: ("Absolute load",             "%",    lambda b: word(b) * 100 / 255),
    0x44: ("Commanded equiv. ratio",    "λ",    lambda b: word(b) / 32768),
    0x45: ("Relative throttle",         "%",    pct),
    0x46: ("Ambient air temp",          "°C",   temp),
    0x47: ("Throttle B",                "%",    pct),
    0x48: ("Throttle C",                "%",    pct),
    0x49: ("Accelerator pedal D",       "%",    pct),
    0x4A: ("Accelerator pedal E",       "%",    pct),
    0x4B: ("Accelerator pedal F",       "%",    pct),
    0x4C: ("Cmd throttle actuator",     "%",    pct),
    0x4D: ("Time with MIL on",          "min",  word),
    0x4E: ("Time since codes clear",    "min",  word),
    0x51: ("Fuel type",                 "",     lambda b: FUEL_TYPE.get(b[0], f"?{b[0]}")),
    0x52: ("Ethanol fuel",              "%",    pct),
    0x55: ("Short sec. O2 trim B1/B3",  "",     sec_o2_trim),
    0x56: ("Long sec. O2 trim B1/B3",   "",     sec_o2_trim),
    0x57: ("Short sec. O2 trim B2/B4",  "",     sec_o2_trim),
    0x58: ("Long sec. O2 trim B2/B4",   "",     sec_o2_trim),
    0x59: ("Fuel rail abs. press.",     "kPa",  lambda b: word(b) * 10),
    0x5A: ("Rel. accelerator pedal",    "%",    pct),
    0x5B: ("Hybrid battery life",       "%",    pct),
    0x5C: ("Engine oil temp",           "°C",   temp),
    0x5D: ("Fuel injection timing",     "°",    lambda b: word(b) / 128 - 210),
    0x5E: ("Engine fuel rate",          "L/h",  lambda b: word(b) / 20),
    0x61: ("Driver demand torque",      "%",    lambda b: b[0] - 125),
    0x62: ("Actual engine torque",      "%",    lambda b: b[0] - 125),
    0x63: ("Reference torque",          "Nm",   word),
}


# --------------------------------------------------------------------------
# ELM327 transport
# --------------------------------------------------------------------------
class ELM327:
    def __init__(self, port, baud, timeout=0.15):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self.protocol = "?"

    def raw(self, c, wait=1.5):
        self.ser.reset_input_buffer()
        self.ser.write((c + "\r").encode())
        buf = b""
        t0 = time.time()
        while time.time() - t0 < wait:
            chunk = self.ser.read(256)
            if chunk:
                buf += chunk
                if b">" in buf:
                    break
        txt = buf.decode(errors="replace").replace("\r", "\n")
        return [l.strip() for l in txt.splitlines() if l.strip() and l.strip() != ">"]

    def at(self, c, wait=2.0):
        """Send an AT command, return the first answer line ('' if none)."""
        r = self.raw(c, wait)
        return r[0] if r else ""

    @staticmethod
    def hexlines(lines):
        """Keep only the lines that are pure hex (adapter data, not status text)."""
        out = []
        for l in lines:
            u = l.upper().replace(" ", "")
            if u and all(ch in "0123456789ABCDEF" for ch in u):
                out.append(u)
        return out

    def init(self):
        r = self.raw("ATZ", 2.5)
        if not any(k in " ".join(r) for k in ("ELM", "STN", "OBDLink")):
            raise RuntimeError(f"no ELM327 response: {r}")
        for c in ("ATE0", "ATL0", "ATS0", "ATH0", "ATAT1", "ATSP0"):
            self.raw(c)
        # first real request triggers protocol search
        self.query(0x00, wait=8)
        dp = self.raw("ATDP")
        self.protocol = dp[0] if dp else "?"

    def voltage(self):
        r = self.raw("ATRV")
        return r[0] if r else "?"

    def query(self, pid, wait=1.5):
        """Send mode 01 request, return data bytes after '41 <pid>' or None."""
        want = f"41{pid:02X}"
        for line in self.raw(f"01{pid:02X}", wait):
            line = line.replace(" ", "").upper()
            i = line.find(want)
            if i >= 0:
                hexdata = line[i + 4:]
                try:
                    return bytes.fromhex(hexdata[: len(hexdata) // 2 * 2])
                except ValueError:
                    return None
        return None

    def supported_pids(self):
        pids = []
        base = 0x00
        while True:
            data = self.query(base, wait=3)
            if not data or len(data) < 4:
                break
            mask = int.from_bytes(data[:4], "big")
            for i in range(32):
                if mask & (1 << (31 - i)):
                    pids.append(base + i + 1)
            if not (mask & 1) or base >= 0x60:
                break
            base += 0x20
        return [p for p in pids if p % 0x20 != 0]  # drop the 0x20/0x40/0x60 bitmap PIDs

    def close(self):
        self.ser.close()


BAUDS = (38400, 115200, 9600)


def open_adapter(port, baud=None, timeout=0.15):
    """Open the adapter and run init(). With baud None, try 38400, 115200 and 9600 in turn.
    Returns (adapter, baud). Raises the last error if nothing answers."""
    last = None
    for b in ([baud] if baud else BAUDS):
        try:
            elm = ELM327(port, b, timeout)
            elm.init()
            return elm, b
        except Exception as e:      # noqa: BLE001
            last = e
            try:
                elm.ser.close()
            except Exception:       # noqa: BLE001
                pass
    raise last


# --------------------------------------------------------------------------
# Ranges and colours
# --------------------------------------------------------------------------
# pid -> (lo, hi, colour function(value) -> colour name)
def c_pct(v):        return "green"
def c_trim(v):       return "green" if abs(v) <= 10 else "yellow" if abs(v) <= 20 else "red"
def c_coolant(v):    return "blue" if v < 60 else "green" if v <= 105 else "yellow" if v <= 115 else "red"
def c_iat(v):        return "blue" if v < 0 else "green" if v <= 60 else "yellow" if v <= 80 else "red"
def c_oil(v):        return "blue" if v < 60 else "green" if v <= 120 else "yellow" if v <= 135 else "red"
def c_rpm(v):        return "green" if v < 4000 else "yellow" if v < 5500 else "red"
def c_cat(v):        return "blue" if v < 300 else "green" if v < 850 else "yellow" if v < 950 else "red"
def c_volt(v):       return "red" if v < 11.5 or v > 15.2 else "yellow" if v < 12.8 else "green"
def c_lambda(v):     return "green" if 0.95 <= v <= 1.05 else "blue" if v > 1.05 else "magenta"
def c_o2(v):         return "blue" if v < 0.45 else "magenta"
def c_none(v):       return "white"

RANGES = {
    0x04: (0, 100, c_pct),   0x05: (-40, 140, c_coolant),
    0x06: (-25, 25, c_trim), 0x07: (-25, 25, c_trim), 0x08: (-25, 25, c_trim), 0x09: (-25, 25, c_trim),
    0x0A: (0, 765, c_none),  0x0B: (0, 255, c_none),  0x0C: (0, 8000, c_rpm), 0x0D: (0, 250, c_none),
    0x0E: (-64, 64, c_none), 0x0F: (-40, 100, c_iat), 0x10: (0, 300, c_none), 0x11: (0, 100, c_pct),
    0x14: (0, 1, c_o2), 0x15: (0, 1, c_o2), 0x16: (0, 1, c_o2), 0x17: (0, 1, c_o2),
    0x18: (0, 1, c_o2), 0x19: (0, 1, c_o2), 0x1A: (0, 1, c_o2), 0x1B: (0, 1, c_o2),
    0x22: (0, 5000, c_none), 0x23: (0, 655350, c_none),
    0x24: (0.5, 1.5, c_lambda), 0x25: (0.5, 1.5, c_lambda), 0x26: (0.5, 1.5, c_lambda), 0x27: (0.5, 1.5, c_lambda),
    0x28: (0.5, 1.5, c_lambda), 0x29: (0.5, 1.5, c_lambda), 0x2A: (0.5, 1.5, c_lambda), 0x2B: (0.5, 1.5, c_lambda),
    0x2C: (0, 100, c_pct),   0x2D: (-25, 25, c_trim), 0x2E: (0, 100, c_pct),
    0x2F: (0, 100, lambda v: "red" if v < 10 else "yellow" if v < 25 else "green"),
    0x32: (-8192, 8192, c_none), 0x33: (0, 255, c_none),
    0x34: (0.5, 1.5, c_lambda), 0x35: (0.5, 1.5, c_lambda), 0x36: (0.5, 1.5, c_lambda), 0x37: (0.5, 1.5, c_lambda),
    0x38: (0.5, 1.5, c_lambda), 0x39: (0.5, 1.5, c_lambda), 0x3A: (0.5, 1.5, c_lambda), 0x3B: (0.5, 1.5, c_lambda),
    0x3C: (0, 1000, c_cat),  0x3D: (0, 1000, c_cat),  0x3E: (0, 1000, c_cat),  0x3F: (0, 1000, c_cat),
    0x42: (8, 16, c_volt),   0x43: (0, 100, c_pct),   0x44: (0.5, 1.5, c_lambda),
    0x45: (0, 100, c_pct),   0x46: (-40, 60, c_none),
    0x47: (0, 100, c_pct), 0x48: (0, 100, c_pct), 0x49: (0, 100, c_pct), 0x4A: (0, 100, c_pct),
    0x4B: (0, 100, c_pct), 0x4C: (0, 100, c_pct), 0x52: (0, 100, c_pct),
    0x55: (-25, 25, c_trim), 0x56: (-25, 25, c_trim), 0x57: (-25, 25, c_trim), 0x58: (-25, 25, c_trim),
    0x59: (0, 655350, c_none), 0x5A: (0, 100, c_pct), 0x5B: (0, 100, c_pct), 0x5C: (-40, 160, c_oil),
    0x5D: (-210, 302, c_none), 0x5E: (0, 100, c_none),
    0x61: (-125, 130, c_none), 0x62: (-125, 130, c_none), 0x63: (0, 2000, c_none),
}


def status_colour(pid, text):
    """Colour for string readings."""
    t = text.lower()
    if "no data" in t or "bad reply" in t:
        return "dim"
    if "mil on" in t or "fault" in t:
        return "red"
    if "incomplete" in t or "open loop" in t:
        return "yellow"
    if "mil off" in t or "complete" in t or "closed loop" in t:
        return "green"
    return "white"


SGR = {"reset": "0", "bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33",
       "blue": "34", "magenta": "35", "cyan": "36", "white": "37", "grey": "90"}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class Painter:
    def __init__(self, enabled):
        self.enabled = enabled

    def __call__(self, text, *styles):
        if not self.enabled or not styles or not text:
            return text
        codes = ";".join(SGR[s] for s in styles)
        return f"\x1b[{codes}m{text}\x1b[0m"

    @staticmethod
    def vlen(text):
        return len(_ANSI.sub("", text))


SPARK = "▁▂▃▄▅▆▇█"
BAR_FULL, BAR_EMPTY = "█", "░"


def bar(value, lo, hi, width):
    frac = 0.0 if hi == lo else (value - lo) / (hi - lo)
    n = round(max(0.0, min(1.0, frac)) * width)
    return BAR_FULL * n + BAR_EMPTY * (width - n)


def sparkline(values, width):
    vals = list(values)[-width:]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return SPARK[3] * len(vals)
    return "".join(SPARK[min(7, int((v - lo) / (hi - lo) * 7.999))] for v in vals)


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------
def decode(pid, data):
    """Returns (numeric value or None, display text)."""
    if data is None:
        return None, "no data"
    name, unit, dec = PIDS[pid]
    try:
        v = dec(data)
    except (IndexError, ValueError):
        return None, "bad reply " + data.hex()
    if isinstance(v, tuple):
        return v
    if isinstance(v, str):
        return None, v
    s = f"{v:.1f}" if isinstance(v, float) and abs(v) < 1000 else f"{v:.0f}"
    return float(v), f"{s} {unit}".rstrip()


def render(readings, history, width, wide_threshold, paint, show_bars, spark_len):
    """readings: list of (pid, number|None, text). Returns output lines."""
    ncol = 3 if width >= wide_threshold else 1
    colw = max(1, width // ncol)
    gap = 2 if ncol > 1 else 0          # blank space between columns
    label_w = max(len(PIDS[p][0]) for p, _, _ in readings)
    numeric_texts = [t for p, v, t in readings if v is not None]
    val_w = min(max((len(t) for t in numeric_texts), default=8), 22)
    avail = colw - gap - label_w - 3
    rest = avail - val_w
    bar_w = 0
    if show_bars:
        bar_w = 12 if rest >= 15 else 8 if rest >= 11 else 5 if rest >= 8 else 0
        if bar_w:
            rest -= bar_w + 3
    spark_w = min(spark_len, rest - 1) if rest >= 7 and spark_len else 0

    cells = []
    for pid, value, text in readings:
        label = paint(f"{PIDS[pid][0]:<{label_w}}", "cyan") + paint(" : ", "grey")
        if value is None:
            colour = status_colour(pid, text)
            cell = label + paint(text[:avail], colour if colour != "white" else "bold")
        else:
            lo, hi, cf = RANGES.get(pid, (None, None, c_none))
            colour = cf(value)
            styles = ("bold",) if colour == "white" else (colour, "bold")
            cell = label + paint(f"{text[:val_w]:<{val_w}}", *styles)
            if bar_w and lo is not None:
                cell += " " + paint("[", "grey") + paint(bar(value, lo, hi, bar_w), colour) + paint("]", "grey")
            elif bar_w:
                cell += " " * (bar_w + 3)
            if spark_w and pid in history:
                cell += " " + paint(sparkline(history[pid], spark_w), "blue")
        pad = colw - paint.vlen(cell)
        cells.append(cell + " " * max(0, pad))

    nrows = (len(cells) + ncol - 1) // ncol
    lines = []
    for r in range(nrows):
        parts = [cells[r + c * nrows] for c in range(ncol) if r + c * nrows < len(cells)]
        lines.append("".join(parts).rstrip())
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=None, help="serial baud (default: auto, 38400 then 115200)")
    ap.add_argument("--watch", "-w", action="store_true", help="refresh continuously")
    ap.add_argument("--interval", type=float, default=0.0, help="pause between refreshes (s)")
    ap.add_argument("--pids", help="comma-separated hex PIDs to read instead of all supported")
    ap.add_argument("--wide", type=int, default=150,
                    help="terminal width from which 3 columns are used (default 150)")
    ap.add_argument("--no-color", action="store_true", help="plain text output")
    ap.add_argument("--no-bars", action="store_true", help="hide the bar gauges")
    ap.add_argument("--history", type=int, default=10,
                    help="sparkline length in samples for --watch (0 disables, default 10)")
    a = ap.parse_args()

    colour_on = not a.no_color and not os.environ.get("NO_COLOR") and sys.stdout.isatty()
    paint = Painter(colour_on)
    history = {}

    try:
        print("initialising ELM327 and searching protocol...")
        elm, baud = open_adapter(a.port, a.baud)
    except Exception as e:
        print(f"cannot open {a.port}: {e}")
        return 1
    try:
        if a.pids:
            pids = [int(x, 16) for x in a.pids.split(",")]
        else:
            pids = elm.supported_pids()
        unknown = [p for p in pids if p not in PIDS]
        pids = [p for p in pids if p in PIDS]
        if not pids:
            print("ECU reported no readable PIDs (ignition on?)")
            return 2
        print(f"protocol: {elm.protocol}  |  {baud} baud  |  {len(pids)} PIDs"
              + (f"  |  unknown PIDs skipped: {' '.join(f'{p:02X}' for p in unknown)}" if unknown else ""))
        if a.watch and colour_on:
            sys.stdout.write("\x1b[?25l")  # hide cursor

        while True:
            t0 = time.time()
            readings = []
            for p in pids:
                v, text = decode(p, elm.query(p))
                if v is not None and a.watch and a.history:
                    history.setdefault(p, deque(maxlen=a.history)).append(v)
                readings.append((p, v, text))
            width = shutil.get_terminal_size((80, 24)).columns
            volt = elm.voltage()
            try:
                vcol = c_volt(float(volt.rstrip("V")))
            except ValueError:
                vcol = "white"
            header = (paint(f"{datetime.now():%H:%M:%S}", "bold") + "  " + paint(elm.protocol, "cyan")
                      + "  battery " + paint(volt, vcol, "bold")
                      + paint(f"  ({len(pids)} PIDs in {time.time() - t0:.1f}s)", "grey"))
            body = render(readings, history, width, a.wide, paint, not a.no_bars,
                          a.history if a.watch else 0)
            if a.watch:
                sys.stdout.write("\x1b[2J\x1b[H")
            print(header)
            print(paint("-" * min(width, paint.vlen(header)), "grey"))
            print("\n".join(body))
            if not a.watch:
                break
            print(paint("\nCtrl+C to stop", "grey"))
            sys.stdout.flush()
            if a.interval:
                time.sleep(a.interval)
    except KeyboardInterrupt:
        print()
    finally:
        if a.watch and colour_on:
            sys.stdout.write("\x1b[?25h")
        elm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
