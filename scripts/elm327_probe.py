#!/usr/bin/env python3
"""Probe an ELM327 adapter on a serial port and check the ECU link.

Usage: elm327_probe.py [PORT] [--baud N]
Default port is /dev/ttyUSB0. Without --baud, tries 38400, 9600, 115200.
Exit codes: 0 = ELM327 and ECU answered, 2 = ELM327 ok but no ECU,
3 = no ELM327 response, 1 = port could not be opened.
"""
import argparse
import sys
import time

try:
    import serial
except ImportError:
    print("pyserial is missing: pip install pyserial")
    sys.exit(1)


def cmd(ser, c, wait=1.5):
    ser.reset_input_buffer()
    ser.write((c + "\r").encode())
    buf = b""
    t0 = time.time()
    while time.time() - t0 < wait:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            if b">" in buf:
                break
    txt = buf.decode(errors="replace").replace("\r", "\n")
    lines = [l.strip() for l in txt.splitlines() if l.strip() and l.strip() != ">"]
    txt = " | ".join(lines)
    print(f"  {c:<6} -> {txt or '(no response)'}")
    return txt


def probe(port, bauds):
    for baud in bauds:
        print(f"=== {port} @ {baud} baud ===")
        try:
            ser = serial.Serial(port, baud, timeout=0.2)
        except Exception as e:
            print(f"OPEN FAILED: {e}")
            return 1
        r = cmd(ser, "ATZ", 2.5)
        if "ELM" not in r and "OK" not in r:
            ser.close()
            continue

        print("ELM327 responds. Adapter info:")
        cmd(ser, "ATE0")
        cmd(ser, "ATI")
        cmd(ser, "AT@1")
        cmd(ser, "ATRV")

        print("ECU check (ignition must be on):")
        cmd(ser, "ATSP0")
        r = cmd(ser, "0100", 8)
        proto = cmd(ser, "ATDP")
        cmd(ser, "ATDPN")
        ecu_ok = "41 00" in r.replace("\n", " ")
        if ecu_ok:
            print("Supported PIDs 01-20 received. Reading VIN:")
            cmd(ser, "0902", 8)
        ser.close()

        if ecu_ok:
            print(f"RESULT: ECU connected, protocol: {proto}")
            return 0
        print("RESULT: ELM327 ok but no ECU reply "
              "(UNABLE TO CONNECT / NO DATA -> check ignition, OBD plug, protocol)")
        return 2
    print("RESULT: no ELM327 response at any baud rate "
          "(wrong port, wrong baud, or adapter not powered by the car)")
    return 3


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, help="single baud rate to try")
    a = ap.parse_args()
    bauds = [a.baud] if a.baud else [38400, 9600, 115200]
    sys.exit(probe(a.port, bauds))
