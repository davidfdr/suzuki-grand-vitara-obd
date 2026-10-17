#!/usr/bin/env python3
"""Sweep K-line module addresses through an ELM327 (ISO 14230 / ISO 9141).

For each address it performs an init (fast, 5-baud, or both), reports whether the
module answered the init, and if so tries a few read-only identification requests.

Usage: elm327_kline_sweep.py [PORT] [--init fast|slow|both] [--kbaud 10400|9600]
                             [--addrs 28,58,6A] [--from 00 --to FF] [--out FILE]
"""
import argparse
import json
import sys
import time

try:
    import serial
except ImportError:
    print("pyserial is missing: pip install pyserial")
    sys.exit(1)

IDENT = ["1A81", "1A87", "1A90", "1A9A", "1A8A", "3E", "2101"]


class ELM:
    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baud, timeout=0.05)

    def raw(self, c, wait):
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
        return [l.strip() for l in txt.splitlines() if l.strip() and l.strip() != ">" and l.strip().upper() != c.upper()]

    def at(self, c, wait=2.0):
        r = self.raw(c, wait)
        return r[0] if r else ""


def hexlines(lines):
    out = []
    for l in lines:
        u = l.upper().replace(" ", "")
        if u and all(ch in "0123456789ABCDEF" for ch in u) and len(u) >= 4:
            out.append(u)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=38400, help="serial baud to the adapter")
    ap.add_argument("--init", choices=["fast", "slow", "both"], default="both")
    ap.add_argument("--kbaud", choices=["10400", "9600"], default="10400", help="K-line baud rate")
    ap.add_argument("--addrs", help="comma-separated hex addresses (overrides --from/--to)")
    ap.add_argument("--from", dest="lo", default="00")
    ap.add_argument("--to", dest="hi", default="FF")
    ap.add_argument("--out", help="JSON output file")
    a = ap.parse_args()

    addrs = [int(x, 16) for x in a.addrs.split(",")] if a.addrs else list(range(int(a.lo, 16), int(a.hi, 16) + 1))
    inits = ["fast", "slow"] if a.init == "both" else [a.init]

    elm = ELM(a.port, a.baud)
    r = elm.raw("ATZ", 3)
    if not any("ELM" in l for l in r):
        print("no ELM327 answer:", r)
        return 1
    for c in ("ATE0", "ATL0", "ATS0", "ATH1", "ATKW0", "ATAT1", "ATST 32", f"ATIB {'96' if a.kbaud == '9600' else '10'}"):
        elm.at(c)
    print(f"adapter {next(l for l in r if 'ELM' in l)}, K-line {a.kbaud} bps, {len(addrs)} addresses, init: {inits}", flush=True)

    found = []
    for mode in inits:
        proto = "5" if mode == "fast" else "4"
        per = 1.3 if mode == "fast" else 3.5
        print(f"\n== {mode} init (ATSP{proto}), about {len(addrs) * per / 60:.0f} min ==", flush=True)
        t0 = time.time()
        for n, addr in enumerate(addrs):
            elm.at(f"ATSP{proto}")
            elm.at(f"ATIIA {addr:02X}")
            elm.at(f"ATSH 81 {addr:02X} F1")
            elm.at(f"ATWM 81 {addr:02X} F1 3E")
            lines = elm.raw("3E", 8.0 if mode == "slow" else 4.0)
            up = " ".join(lines).upper()
            init_ok = "BUS INIT" in up and "OK" in up
            data = hexlines(lines)
            if init_ok or data:
                print(f"  addr {addr:02X}: {'init OK' if init_ok else 'reply'} {lines}", flush=True)
                entry = {"init": mode, "addr": f"{addr:02X}", "first": lines, "ident": {}}
                for req in IDENT:
                    rl = elm.raw(req, 3.0)
                    dl = hexlines(rl)
                    if dl:
                        print(f"      {req:<6} -> {dl}", flush=True)
                        entry["ident"][req] = dl
                found.append(entry)
            elif (n + 1) % 16 == 0:
                print(f"  ...{addr:02X} ({n + 1}/{len(addrs)}, {time.time() - t0:.0f}s)", flush=True)
            elm.at("ATPC")
    elm.raw("ATZ", 2)
    elm.ser.close()

    print("\n== RESULT ==")
    if not found:
        print("no K-line module answered any init")
    for e in found:
        print(f"{e['init']:<5} addr {e['addr']}  {e['first']}  ident: {list(e['ident'])}")
    if a.out:
        json.dump(found, open(a.out, "w"), indent=2)
        print("written", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
