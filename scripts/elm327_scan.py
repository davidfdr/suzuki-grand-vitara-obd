#!/usr/bin/env python3
"""Discover which control modules answer on the OBD connector, and how.

Phases (all read-only diagnostic requests):
  1. CAN 11-bit 500k: listen to bus traffic, then functional broadcast (7DF)
  2. CAN 11-bit 500k: physical sweep of request IDs 0x700-0x7FF
  3. CAN 29-bit 500k and both 250k variants: functional broadcast only
  4. K-line ISO 14230 fast init: sweep of module addresses
  5. For each module found: read identification (KWP 1A xx / UDS 22 F1xx)

Usage: elm327_scan.py [PORT] [--baud N] [--full] [--slow] [--skip-can] [--skip-kline] [--out FILE]
  --full   K-line: try all 256 addresses instead of the common list
  --slow   K-line: also try ISO 9141 / KWP 5-baud init on the common list
  --out    write results as JSON
"""
import argparse
import json
import sys
import time
from datetime import datetime

try:
    import serial
except ImportError:
    print("pyserial is missing: pip install pyserial")
    sys.exit(1)

ELM_ERRORS = ("NO DATA", "CAN ERROR", "BUS INIT", "UNABLE", "ERROR", "BUFFER FULL",
              "BUS BUSY", "STOPPED", "?", "SEARCHING", "FB ERROR", "DATA ERROR", "<")

# Requests used for detection: (hex, description). All are read-only.
DETECT_REQUESTS = [("3E00", "UDS tester present"), ("1001", "UDS default session"),
                   ("1A87", "KWP read ECU id"), ("0100", "OBD supported PIDs")]

# Identification requests tried on every module found.
IDENT_REQUESTS = [("1A81", "KWP id 81"), ("1A87", "KWP id 87 (part no.)"), ("1A90", "KWP id 90 (VIN)"),
                  ("1A88", "KWP id 88"), ("1A8A", "KWP id 8A"),
                  ("22F187", "UDS spare part no."), ("22F18C", "UDS serial"), ("22F190", "UDS VIN"),
                  ("22F191", "UDS hw no."), ("22F194", "UDS sw no."), ("22F1A0", "UDS F1A0"),
                  ("0902", "OBD VIN"), ("0904", "OBD calibration id")]

# K-line addresses tried by default (SAE J2178 / common Suzuki, Denso, Bosch use)
KLINE_COMMON = [0x10, 0x11, 0x12, 0x13, 0x17, 0x18, 0x1A, 0x1B, 0x20, 0x21, 0x22, 0x25, 0x28, 0x29,
                0x2A, 0x2B, 0x2C, 0x30, 0x31, 0x33, 0x38, 0x3A, 0x40, 0x41, 0x48, 0x4A, 0x50, 0x51,
                0x52, 0x55, 0x58, 0x59, 0x5A, 0x5B, 0x60, 0x61, 0x62, 0x63, 0x64, 0x68, 0x6A, 0x6B,
                0x70, 0x71, 0x72, 0x73, 0x75, 0x78, 0x7A, 0x7B, 0x80, 0x88, 0x91, 0x92, 0x93, 0xA0,
                0xA1, 0xB0, 0xC0, 0xC1, 0xD0, 0xE0, 0xF0, 0xF1]

# Human names for common CAN diagnostic IDs and K-line addresses.
CAN_NAMES = {0x7E0: "engine (OBD #1)", 0x7E1: "transmission (OBD #2)", 0x7E2: "OBD #3", 0x7E3: "OBD #4",
             0x7E4: "OBD #5", 0x7E5: "OBD #6", 0x7E6: "OBD #7", 0x7E7: "OBD #8"}
KLINE_NAMES = {0x10: "engine", 0x11: "engine #2", 0x18: "transmission", 0x28: "ABS/ESP", 0x29: "ABS #2",
               0x30: "instrument", 0x33: "OBD functional", 0x40: "body", 0x51: "SRS", 0x58: "SRS",
               0x60: "instrument", 0x61: "body", 0x62: "immobilizer", 0x68: "A/C", 0x6A: "A/C", 0x70: "4WD"}


class ELM327:
    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baud, timeout=0.05)

    def raw(self, c, wait=2.0):
        self.ser.reset_input_buffer()
        self.ser.write((c + "\r").encode())
        buf, t0 = b"", time.time()
        while time.time() - t0 < wait:
            chunk = self.ser.read(512)
            if chunk:
                buf += chunk
                if b">" in buf:
                    break
        txt = buf.decode(errors="replace").replace("\r", "\n")
        lines = [l.strip() for l in txt.splitlines()]
        return [l for l in lines if l and l != ">" and l.upper() != c.upper()]

    def at(self, c, wait=2.0):
        r = self.raw(c, wait)
        return r[0] if r else ""

    def reset(self):
        r = self.raw("ATZ", 3)
        if not any("ELM" in l for l in r):
            raise RuntimeError(f"no ELM327 answer: {r}")
        for c in ("ATE0", "ATL0", "ATS0", "ATH1", "ATCAF1", "ATAT1", "ATST 32"):
            self.at(c)
        return next(l for l in r if "ELM" in l)

    def monitor(self, seconds):
        """ATMA for a few seconds, return the raw lines seen."""
        self.ser.reset_input_buffer()
        self.ser.write(b"ATMA\r")
        buf, t0 = b"", time.time()
        while time.time() - t0 < seconds:
            buf += self.ser.read(4096)
        self.ser.write(b"\r")           # any character stops monitoring
        t0 = time.time()
        while time.time() - t0 < 1.0:
            chunk = self.ser.read(4096)
            buf += chunk
            if b">" in chunk:
                break
        lines = buf.decode(errors="replace").replace("\r", "\n").splitlines()
        return [l.strip() for l in lines if l.strip() and l.strip() not in (">", "ATMA", "STOPPED")]

    def close(self):
        try:
            self.raw("ATZ", 1)
        finally:
            self.ser.close()


def data_lines(lines):
    """Keep only lines that look like hex payloads."""
    out = []
    for l in lines:
        u = l.upper().replace(" ", "")
        if any(e in l.upper() for e in ELM_ERRORS):
            continue
        if u and all(ch in "0123456789ABCDEF" for ch in u):
            out.append(u)
    return out


def can_responses(lines, sid):
    """Parse 11-bit CAN lines with headers on. Returns [(rx_id, payload_bytes)] for
    frames that look like a diagnostic answer to service `sid`."""
    found = []
    for u in data_lines(lines):
        if len(u) < 3 + 4:
            continue
        rx = int(u[:3], 16)
        body = bytes.fromhex(u[3: len(u) // 2 * 2 + 1] if len(u) % 2 else u[3:])
        if not body:
            continue
        pci = body[0] >> 4
        if pci == 0:                      # single frame: [0L, SID, ...]
            payload = body[1: 1 + (body[0] & 0x0F)]
        elif pci == 1:                    # first frame: [1L, LL, SID, ...]
            payload = body[2:]
        else:
            continue
        if not payload:
            continue
        if payload[0] == sid + 0x40 or (payload[0] == 0x7F and len(payload) > 1 and payload[1] == sid):
            found.append((rx, payload))
    return found


def kline_responses(lines, sid):
    found = []
    for u in data_lines(lines):
        b = bytes.fromhex(u[: len(u) // 2 * 2])
        for i in range(len(b)):
            if b[i] == sid + 0x40 or (b[i] == 0x7F and i + 1 < len(b) and b[i + 1] == sid):
                found.append(b[i:])
                break
    return found


def describe(payload):
    hexs = payload.hex(" ").upper()
    if payload and payload[0] == 0x7F:
        nrc = {0x10: "general reject", 0x11: "service not supported", 0x12: "sub-function not supported",
               0x21: "busy", 0x22: "conditions not correct", 0x31: "request out of range",
               0x33: "security access denied", 0x78: "response pending", 0x7E: "not in this session",
               0x7F: "not in this session"}.get(payload[2] if len(payload) > 2 else -1, "negative")
        return f"{hexs}  (negative: {nrc})"
    printable = "".join(chr(c) if 32 <= c < 127 else "." for c in payload[1:])
    if sum(32 <= c < 127 for c in payload[1:]) >= max(3, len(payload) // 2):
        return f"{hexs}  '{printable}'"
    return hexs


def ascii_of(payload):
    txt = "".join(chr(c) for c in payload if 32 <= c < 127)
    return txt if len(txt) >= 3 else ""


# --------------------------------------------------------------------------
def phase_can_functional(elm, proto, label, results):
    print(f"\n== CAN {label}: functional broadcast ==")
    elm.at(f"ATSP{proto}")
    elm.at("ATST 32")
    if proto in (6, 8):
        elm.at("ATSH 7DF"); elm.at("ATCF 700"); elm.at("ATCM 700")
    else:
        elm.at("ATSH 18DB33F1"); elm.at("ATCF 18DAF100"); elm.at("ATCM 1FFFFF00")
    seen = {}
    for req, desc in (("0100", "OBD supported PIDs"), ("3E00", "UDS tester present"),
                      ("1001", "UDS default session"), ("1A87", "KWP read ECU id")):
        lines = elm.raw(req, 4.0)
        errs = [l for l in lines if any(e in l.upper() for e in ELM_ERRORS)]
        if any("CAN ERROR" in e for e in errs) or any("UNABLE" in e for e in errs):
            print(f"  {req} ({desc}): {' / '.join(errs)}")
            break
        for rx, payload in can_responses(lines, int(req[:2], 16)):
            seen.setdefault(rx, []).append((req, payload))
            print(f"  {req} ({desc}): reply from {rx:03X}: {describe(payload)}")
        if not lines or (not seen and errs):
            print(f"  {req} ({desc}): {' / '.join(errs) or 'nothing'}")
    for rx, replies in seen.items():
        results.append({"bus": f"CAN {label}", "mode": "functional", "tx": "7DF" if proto in (6, 8) else "18DB33F1",
                        "rx": f"{rx:03X}", "name": CAN_NAMES.get(rx - 8, ""), "replies": [(r, p.hex().upper()) for r, p in replies]})
    return seen


def phase_can_monitor(elm, proto, label):
    print(f"\n== CAN {label}: listening to bus traffic for 2 s ==")
    elm.at(f"ATSP{proto}")
    elm.at("ATCF 000"); elm.at("ATCM 000")
    lines = elm.monitor(2.0)
    ids = {}
    for u in data_lines(lines):
        if len(u) >= 3:
            ids[u[:3]] = ids.get(u[:3], 0) + 1
    errs = {l for l in lines if any(e in l.upper() for e in ELM_ERRORS)}
    if ids:
        top = sorted(ids.items(), key=lambda kv: -kv[1])[:24]
        print(f"  {len(lines)} frames, {len(ids)} distinct IDs: " + " ".join(f"{i}({n})" for i, n in top))
        print("  -> the OBD port is on a live vehicle CAN bus; other modules may be reachable here")
    else:
        print(f"  no traffic seen ({', '.join(errs) or 'silent bus'}) -> diagnostic-only CAN or gateway")
    return ids


def phase_can_physical(elm, results, known_rx):
    print("\n== CAN 11-bit 500k: physical sweep 700-7FF (~1-2 min) ==")
    elm.at("ATSP6")
    elm.at("ATST 14")            # 80 ms max wait per request
    elm.at("ATCF 700"); elm.at("ATCM 700")
    found = {}
    t0 = time.time()
    for tx in range(0x700, 0x800):
        if tx == 0x7DF or tx in known_rx:
            continue
        elm.at(f"ATSH {tx:03X}")
        hit = None
        for req, desc in DETECT_REQUESTS:
            lines = elm.raw(req, 1.0)
            if any("CAN ERROR" in l.upper() for l in lines):
                print("  CAN ERROR - bus problem, aborting sweep")
                return found
            resp = [(rx, p) for rx, p in can_responses(lines, int(req[:2], 16)) if rx != tx and rx not in known_rx]
            if resp:
                hit = (req, desc, resp)
                break
        if hit:
            req, desc, resp = hit
            rx, payload = resp[0]
            found[tx] = rx
            print(f"  {tx:03X} -> {rx:03X}  {CAN_NAMES.get(tx, ''):<22} {req} ({desc}): {describe(payload)}")
            results.append({"bus": "CAN 11/500", "mode": "physical", "tx": f"{tx:03X}", "rx": f"{rx:03X}",
                            "name": CAN_NAMES.get(tx, ""), "replies": [(req, payload.hex().upper())]})
        elif (tx & 0x0F) == 0x0F:
            sys.stdout.write(f"\r  ...{tx:03X} ({time.time() - t0:.0f}s)   ")
            sys.stdout.flush()
    print()
    return found


def can_identify(elm, tx, rx, entry):
    elm.at("ATSP6"); elm.at("ATST 64"); elm.at("ATH0")
    elm.at(f"ATSH {tx:03X}")
    if elm.at(f"ATCRA {rx:03X}").startswith("?"):
        elm.at(f"ATCF {rx:03X}"); elm.at("ATCM 7FF")
    elm.at("ATFCSH " + f"{tx:03X}"); elm.at("ATFCSD 30 00 00"); elm.at("ATFCSM 1")
    print(f"  module {tx:03X}/{rx:03X}:")
    for req, desc in IDENT_REQUESTS:
        lines = elm.raw(req, 3.0)
        payloads = []
        for u in data_lines(lines):
            b = bytes.fromhex(u[: len(u) // 2 * 2])
            sid = int(req[:2], 16)
            if b and (b[0] == sid + 0x40 or (b[0] == 0x7F and len(b) > 1 and b[1] == sid)):
                payloads.append(b)
        for p in payloads:
            if p[0] != 0x7F:
                print(f"    {req:<7} {desc:<22} {describe(p)}")
                entry.setdefault("ident", {})[req] = {"hex": p.hex().upper(), "ascii": ascii_of(p[3:] if req.startswith("22") else p[2:])}
    elm.at("ATH1"); elm.at("ATCRA"); elm.at("ATCF 700"); elm.at("ATCM 700")


def phase_kline(elm, addresses, results, slow=False):
    proto = "4" if slow else "5"
    name = "ISO 14230 5-baud init" if slow else "ISO 14230 fast init"
    print(f"\n== K-line {name}: {len(addresses)} addresses (~{len(addresses) * (3 if slow else 1.2):.0f}s) ==")
    found = []
    for i, addr in enumerate(addresses):
        elm.at(f"ATSP{proto}")
        elm.at("ATST 32")
        elm.at(f"ATIIA {addr:02X}")
        elm.at(f"ATSH 81 {addr:02X} F1")
        elm.at("ATWM 81 {:02X} F1 3E".format(addr))
        lines = elm.raw("1A87", 6.0 if slow else 3.0)
        init_ok = any("BUS INIT" in l.upper() and "OK" in l.upper() for l in lines)
        resp = kline_responses(lines, 0x1A)
        if init_ok or resp:
            what = describe(resp[0]) if resp else "init OK, no answer to 1A87"
            print(f"  addr {addr:02X} {KLINE_NAMES.get(addr, ''):<14} -> {what}")
            entry = {"bus": f"K-line {name}", "mode": "physical", "tx": f"{addr:02X}", "rx": "F1",
                     "name": KLINE_NAMES.get(addr, ""), "replies": [("1A87", resp[0].hex().upper())] if resp else []}
            kline_identify(elm, entry)
            results.append(entry)
            found.append(addr)
        else:
            sys.stdout.write(f"\r  ...{addr:02X} ({i + 1}/{len(addresses)})   ")
            sys.stdout.flush()
        elm.at("ATPC")   # close the session before the next address
    print()
    return found


def kline_identify(elm, entry):
    for req, desc in IDENT_REQUESTS:
        if req.startswith("09"):
            continue
        lines = elm.raw(req, 3.0)
        for p in kline_responses(lines, int(req[:2], 16)):
            if p[0] != 0x7F:
                print(f"    {req:<7} {desc:<22} {describe(p)}")
                entry.setdefault("ident", {})[req] = {"hex": p.hex().upper(), "ascii": ascii_of(p[3:] if req.startswith("22") else p[2:])}


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=38400)
    ap.add_argument("--full", action="store_true", help="K-line: sweep all 256 addresses")
    ap.add_argument("--slow", action="store_true", help="K-line: also try 5-baud init")
    ap.add_argument("--skip-can", action="store_true")
    ap.add_argument("--skip-kline", action="store_true")
    ap.add_argument("--out", help="write JSON results to this file")
    a = ap.parse_args()

    try:
        elm = ELM327(a.port, a.baud)
    except Exception as e:
        print(f"cannot open {a.port}: {e}")
        return 1
    results = []
    try:
        print("adapter:", elm.reset(), "| battery:", elm.at("ATRV"))
        if not a.skip_can:
            phase_can_monitor(elm, 6, "11-bit 500k")
            seen = phase_can_functional(elm, 6, "11-bit 500k", results)
            known_rx = set(seen)
            physical = phase_can_physical(elm, results, known_rx)
            for entry in [r for r in results if r["bus"].startswith("CAN 11/500") or r["bus"] == "CAN 11-bit 500k"]:
                can_identify(elm, int(entry["tx"], 16) if entry["mode"] == "physical" else int(entry["rx"], 16) - 8,
                             int(entry["rx"], 16), entry)
            for proto, label in ((7, "29-bit 500k"), (8, "11-bit 250k"), (9, "29-bit 250k")):
                phase_can_functional(elm, proto, label, results)
        if not a.skip_kline:
            addrs = list(range(0x00, 0x100)) if a.full else KLINE_COMMON
            phase_kline(elm, addrs, results)
            if a.slow:
                phase_kline(elm, KLINE_COMMON, results, slow=True)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        elm.close()

    print("\n==================== SUMMARY ====================")
    if not results:
        print("no modules found")
    for r in results:
        ident = r.get("ident", {})
        strings = [v["ascii"] for v in ident.values() if v["ascii"]]
        print(f"{r['bus']:<26} {r['mode']:<10} tx {r['tx']:<9} rx {r['rx']:<4} {r['name']:<20} "
              + (" | ".join(strings) if strings else ""))
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"date": datetime.now().isoformat(timespec="seconds"), "modules": results}, f, indent=2)
        print(f"results written to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
