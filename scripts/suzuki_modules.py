#!/usr/bin/env python3
"""Talk to Suzuki control modules (ABS/ESP, HVAC, BCM, ...) through an ELM327-compatible adapter.

Addresses and init sequence follow SZ Viewer. Needs an adapter whose ATCRA, ATFI and ATSI
really work (OBDLink, vLinker FS, ...). Cheap clones only reach the engine ECM.

Usage:
  suzuki_modules.py [PORT] [--baud N] --list
  suzuki_modules.py [PORT] [--baud N] --scan            # try every known module on CAN and K-line
  suzuki_modules.py [PORT] [--baud N] --module ABS      # connect, identify, read DTCs
  suzuki_modules.py [PORT] [--baud N] --module HVAC          # HVAC also prints its decoded live data
  suzuki_modules.py [PORT] [--baud N] --module HVAC --data   # also dump local identifiers 21 00..FF
  suzuki_modules.py [PORT] [--baud N] --module HVAC --watch  # refresh the decoded live data until Ctrl+C
  suzuki_modules.py [PORT] [--baud N] --module ABS --bus kline

Without --baud, 115200 (vLinker FS) then 38400 are tried.
"""
import argparse
import sys
import time

try:
    import serial
except ImportError:
    print("pyserial is missing: pip install pyserial")
    sys.exit(1)

# name -> (CAN request id or None, K-line address or None)
MODULES = {
    "ENGINE": (0x7E0, 0x11), "AT": (0x7E1, 0x19), "ABS": (0x261, 0x29), "4WD": (0x262, 0x21),
    "PS": (0x263, 0x31), "CCM": (0x264, None), "BCM": (0x271, 0x5A), "HVAC": (0x272, 0x99),
    "HVAC2": (None, 0x98), "AC": (None, 0x09), "SRS": (0x273, 0x59), "SRSH": (None, 0x58),
    "IPC": (0x274, 0x61), "KLS": (0x275, None), "ALC": (0x276, None), "ICM": (0x277, None),
    "IMMO": (None, 0x5D), "TPMS": (None, 0x5C), "OCM": (None, 0x5B), "POWERTRAIN": (None, 0x01),
}
DESC = {"ENGINE": "engine ECM", "AT": "automatic transmission", "ABS": "ABS/ESP", "4WD": "4WD control",
        "PS": "power steering", "CCM": "cruise control", "BCM": "body control", "HVAC": "climate control",
        "HVAC2": "climate control (2)", "AC": "air conditioning", "SRS": "airbag", "SRSH": "airbag (H)",
        "IPC": "combination meter", "KLS": "keyless start", "ALC": "headlight levelling",
        "ICM": "stop/start", "IMMO": "immobiliser", "TPMS": "tyre pressure", "OCM": "occupant sensor",
        "POWERTRAIN": "engine+AT (old)"}
IDENT = [("1A81", "id 81"), ("1A87", "id 87"), ("1A88", "id 88"), ("1A8A", "id 8A"), ("1A90", "VIN"),
         ("1A9A", "part number"), ("1A9B", "id 9B"), ("22F190", "UDS VIN"), ("22F187", "UDS part no")]
DTC_REQS = [("1800FF00", "KWP readDTCByStatus"), ("1802FF00", "KWP readDTC (all)"),
            ("13", "KWP readDTC"), ("1902FF", "UDS readDTC"), ("03", "OBD stored DTCs")]
ERRORS = ("NO DATA", "CAN ERROR", "BUS INIT", "UNABLE", "ERROR", "BUFFER FULL", "BUS BUSY", "STOPPED", "?")


def can_reply_id(code):
    if 0x200 <= code <= 0x2FF:
        return code + 0x400
    if code >= 0x7E0:
        return code | 8
    return code + 0x10


class ELM:
    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baud, timeout=0.05)

    def raw(self, c, wait=2.0):
        self.ser.reset_input_buffer()
        self.ser.write((c + "\r").encode())
        buf, t0 = b"", time.time()
        while time.time() - t0 < wait:
            ch = self.ser.read(512)
            if ch:
                buf += ch
                if b">" in buf:
                    break
        txt = buf.decode(errors="replace").replace("\r", "\n")
        return [l.strip() for l in txt.splitlines()
                if l.strip() and l.strip() != ">" and l.strip().upper() != c.upper()]

    def at(self, c, wait=2.0):
        r = self.raw(c, wait)
        return r[0] if r else ""

    def init(self):
        r = self.raw("ATZ", 3)
        ver = next((l for l in r if "ELM" in l or "STN" in l or "OBDLink" in l), None)
        if not ver:
            raise RuntimeError(f"no adapter answer: {r}")
        for c in ("ATE0", "ATL0", "ATS0", "ATH1", "ATAL", "ATIB 10", "ATKW0", "ATSW00", "ATAT0",
                  "ATCAF1", "ATCFC1", "ATFCSM0"):
            self.at(c)
        return ver

    def hexlines(self, lines):
        out = []
        for l in lines:
            u = l.upper().replace(" ", "")
            if u and all(ch in "0123456789ABCDEF" for ch in u):
                out.append(u)
        return out


class Module:
    def __init__(self, elm, name):
        self.elm, self.name = elm, name
        self.can_id, self.kaddr = MODULES[name]
        self.bus = None

    # ---- connection -------------------------------------------------------
    def connect_can(self):
        e = self.elm
        e.at("ATH1")         # need the reply ID to tell modules apart
        e.at("ATSP6")
        e.at(f"ATSH {self.can_id:03X}")
        e.at(f"ATCRA {can_reply_id(self.can_id):03X}")
        e.at(f"ATFCSH {self.can_id:03X}")
        e.at("ATFCSD 30 00 00")
        e.at("ATFCSM 1")     # explicit flow control 30 00 00 from our ID: needed on the vLinker FS, where
                             # mode 0 never fetches the consecutive frames of 261/272/274 answers
        e.at("ATST 19")
        r = e.raw("3E", 1.5)
        want = f"{can_reply_id(self.can_id):03X}"
        # with headers on a valid answer is "<reply id>017E"; the engine ECM also answers requests
        # sent to other IDs on 7E8, so the reply ID must match, not just the 7E byte
        ok = any(l.upper().startswith(want) and l.upper().endswith("7E") for l in e.hexlines(r))
        return ok, r

    def connect_kline(self, slow=False):
        e = self.elm
        e.at("ATH1")
        if slow:
            e.at("ATSP4")
            e.at(f"ATIIA {self.kaddr:02X}")
            e.at("ATST 19")
            r = e.raw("ATSI", 8.0)
        else:
            e.at("ATSP5")
            e.at(f"ATSH 81 {self.kaddr:02X} F1")
            e.at("ATST 19")
            r = e.raw("ATFI", 5.0)
        up = " ".join(r).upper()
        if "?" in up:                       # adapter without ATFI/ATSI: let the first request init
            e.no_init_cmd = True
            r = e.raw("3E", 4.0)
            up = " ".join(r).upper()
            ok = ("BUS INIT" in up and "OK" in up) or bool(e.hexlines(r))
        else:
            ok = "OK" in up and "ERROR" not in up
        if ok:
            e.at(f"ATSH 81 {self.kaddr:02X} F1")
        return ok, r

    def connect(self, bus=None):
        tried = []
        order = [bus] if bus else ["can", "kline"]
        for b in order:
            if b == "can" and self.can_id is not None:
                ok, r = self.connect_can()
                tried.append(("CAN", r))
                if ok:
                    self.bus = "can"
                    self.elm.at("ATH0")      # headers off so the adapter reassembles multi-frame answers
                    return True, tried
            if b == "kline" and self.kaddr is not None:
                for slow in (False, True):
                    if slow and getattr(self.elm, "no_init_cmd", False):
                        break               # adapter has no ATSI either; skip the slow retry
                    ok, r = self.connect_kline(slow)
                    tried.append(("K-line 5baud" if slow else "K-line fast", r))
                    if ok:
                        self.bus = "kline"
                        self.elm.at("ATH0")
                        return True, tried
                    self.elm.at("ATPC")
        return False, tried

    # ---- requests ---------------------------------------------------------
    def request(self, hexreq, wait=3.0):
        """Returns list of payload bytes objects (positive or negative responses).

        Handles the three ways an ELM327 prints a CAN answer: headers off single line ("5A90..."),
        headers off multi-frame ("012" then "0: 5A 90 ..", "1: .."), and headers on raw ISO-TP
        frames ("661 10 12 5A 8A ..", "661 21 .."), which are reassembled per reply ID here."""
        e = self.elm
        sid = int(hexreq[:2], 16)
        lines = e.raw(hexreq, wait)
        payloads = []
        segs = [l.split(":", 1)[1].replace(" ", "") for l in lines if ":" in l]
        if segs:                                   # headers off, adapter-segmented long answer:
            joined = "".join(segs)                 # "012" (total length) then "0: 5A 8A ..", "1: .."
            lens = [l for l in lines if ":" not in l and len(l.replace(" ", "")) == 3
                    and all(ch in "0123456789ABCDEFabcdef" for ch in l.replace(" ", ""))]
            n = int(lens[0], 16) if lens else len(joined) // 2
            payloads.append(joined[:n * 2])
            lines = [l for l in lines if ":" not in l and l not in lens]
        multi = {}                                 # reply id -> [expected length, hex so far]
        for u in e.hexlines(lines):
            if len(u) % 2 == 1 and len(u) >= 5:    # "661" + frame bytes: headers on
                rid, frame = u[:3], u[3:]
                pci = int(frame[:2], 16) >> 4
                if pci == 0:                       # single frame
                    n = int(frame[:2], 16) & 0xF
                    payloads.append(frame[2:2 + n * 2])
                elif pci == 1:                     # first frame
                    n = (int(frame[:2], 16) & 0xF) << 8 | int(frame[2:4], 16)
                    multi[rid] = [n, frame[4:]]
                elif pci == 2 and rid in multi:    # consecutive frame
                    multi[rid][1] += frame[2:]
                else:
                    payloads.append(frame)
            elif len(u) % 2 == 1:                  # odd length without a header: drop the first nibble
                payloads.append(u[1:])
            else:
                payloads.append(u)
        for n, hx in multi.values():
            payloads.append(hx[:n * 2])
        out = []
        for hx in payloads:
            try:
                b = bytes.fromhex(hx[: len(hx) // 2 * 2])
            except ValueError:
                continue
            for i in range(min(4, len(b))):
                if b[i] == sid + 0x40 or (b[i] == 0x7F and i + 1 < len(b) and b[i + 1] == sid):
                    out.append(b[i:])
                    break
        return out


def describe(p):
    h = p.hex(" ").upper()
    if p[0] == 0x7F:
        return f"{h}  (negative)"
    txt = "".join(chr(c) if 32 <= c < 127 else "." for c in p[2:])
    return f"{h}  '{txt}'" if sum(32 <= c < 127 for c in p[2:]) >= 3 else h


def decode_dtcs(p):
    """KWP 58 / 5A-style and OBD 43 answers -> list of code strings (best effort)."""
    codes = []
    if p[0] == 0x58 and len(p) >= 2:            # 58 <count> [hi lo status]*
        n = p[1]
        for i in range(n):
            j = 2 + i * 3
            if j + 1 < len(p):
                codes.append((p[j] << 8 | p[j + 1], p[j + 2] if j + 2 < len(p) else None))
    elif p[0] == 0x43:                          # OBD mode 3
        data = p[1:]
        if data and data[0] <= 20 and len(data) % 2 == 1:
            data = data[1:]
        for i in range(0, len(data) - 1, 2):
            v = data[i] << 8 | data[i + 1]
            if v:
                codes.append((v, None))
    elif p[0] == 0x59 and len(p) >= 3:          # UDS 59 02 mask [hi mid lo status]*
        for i in range(3, len(p) - 3, 4):
            codes.append((p[i] << 8 | p[i + 1], p[i + 3]))
    out = []
    for v, st in codes:
        letter = "PCBU"[v >> 14]
        code = f"{letter}{(v >> 12) & 3}{(v >> 8) & 0xF:X}{(v >> 4) & 0xF:X}{v & 0xF:X}"
        out.append(code + (f" {dtc_status_text(st)}" if st is not None else ""))
    return out


DTC_STATES = ("no failure", "history", "pending", "current")
DTC_SYMPTOMS = ((1, "high"), (2, "low"), (4, "no signal"), (8, "invalid signal"))


def dtc_status_text(st):
    """Suzuki KWP status byte: bits 6-5 state, bit 7 MIL, bit 4 test not complete, low nibble symptom."""
    parts = [DTC_STATES[(st >> 5) & 3]]
    parts += [name for bit, name in DTC_SYMPTOMS if st & bit]
    if st & 0x80:
        parts.append("MIL")
    return f"{st:02X}={'/'.join(parts)}"



# HVAC live data record (request 21 08 on CAN 272, reply 61 08 + 70 bytes). Offsets count from the byte
# after "61 08"; the first 7 bytes repeat the DTC list. Layout = SZ Viewer HVAC_CAN_272_39530_61M0_08,
# verified on the Grand Vitara 2009 unit 39520-79K0 (NOTES.md, 2026-10-17).
HVAC_MODE = {0: "face", 2: "bi-level", 4: "foot", 6: "defog+foot", 8: "defrost", 32: "auto"}
HVAC_INTAKE = {0: "recirculate", 2: "fresh", 32: "auto"}


def hvac_setpoint(v):
    if v == 0:
        return "LO (max cool)"
    if v == 40:
        return "HI (max heat)"
    if 2 <= v <= 36:
        return f"{(v - 6) / 2 + 17:.1f} C"
    return f"? ({v})"


HVAC_RECORD_08 = [
    # (offset, name, decoder)
    (7, "Set temperature", hvac_setpoint),
    (8, "Mode switch", lambda v: HVAC_MODE.get(v, f"? ({v})")),
    (9, "Fan switch", lambda v: "off" if v == 0 else ("auto" if v == 32 else str(v))),
    (10, "Air intake switch", lambda v: HVAC_INTAKE.get(v, f"? ({v})")),
    (14, "Engine rpm", lambda v: f"{v * 32} rpm"),
    (15, "Vehicle speed", lambda v: f"{v} km/h"),
    (16, "Coolant temperature", lambda v: f"{v - 40} C"),
    (22, "Outside air temperature", lambda v: f"{v / 2 - 40:.1f} C"),
    (23, "Cabin air temperature", lambda v: f"{v / 2 - 40:.1f} C"),
    (24, "Sun load", lambda v: f"{v * 5} W/m2"),
    (25, "Evaporator temperature", lambda v: f"{v / 2 - 40:.1f} C"),
    (26, "Refrigerant pressure", lambda v: f"{v * 14} kPa"),
    (33, "Air mix position", lambda v: f"{v * 0.02:.2f} V"),
    (39, "Mode position", lambda v: f"{v * 0.02:.2f} V"),
    (45, "Fresh air position", lambda v: f"{v * 0.02:.2f} V"),
    (49, "Fan desired voltage", lambda v: f"{v * 0.1:.1f} V"),
    (60, "A/C signal", lambda v: "on" if v & 0x01 else "off"),
    (60, "Blower", lambda v: "on" if v & 0x02 else "off"),
    (60, "Rear defogger", lambda v: "on" if v & 0x04 else "off"),
    (60, "Compressor clutch", lambda v: "on" if v & 0x08 else "off"),
    (60, "Engine run request", lambda v: "on" if v & 0x40 else "off"),
]


def decode_hvac_08(data):
    """data = bytes after '61 08'. Returns [(name, text)], skipping bytes the unit reports as FF."""
    out = []
    for off, name, fn in HVAC_RECORD_08:
        if off < len(data) and data[off] != 0xFF:
            out.append((name, fn(data[off])))
    return out


# ABS/ESP live data record (request 21 00 on CAN 261, reply 61 00 + 50 bytes). Offsets count from the
# byte after "61 00". Layout = SZ Viewer ABS_CAN_00_Local plus the 56100-61MB extras that fit the
# record returned by the Grand Vitara 2009 unit 56100-79K1 (checked against idle values, 2026-10-17).
def _word(d, i):
    return d[i] << 8 | d[i + 1]


ABS_RECORD_00 = [
    # (offset, length, name, decoder over the raw int)
    (7, 1, "Wheel speed front right", lambda v: f"{v} km/h"),
    (8, 1, "Wheel speed front left", lambda v: f"{v} km/h"),
    (9, 1, "Wheel speed rear right", lambda v: f"{v} km/h"),
    (10, 1, "Wheel speed rear left", lambda v: f"{v} km/h"),
    (11, 1, "G sensor (longitudinal)", lambda v: f"{v * 0.02 - 2.56:+.2f} G"),
    (12, 1, "Battery voltage", lambda v: f"{v * 0.0784:.1f} V"),
    (13, 1, "Pump motor voltage", lambda v: f"{v * 0.0804:.1f} V"),
    (14, 1, "Steering angle offset", lambda v: f"{v * 0.5 - 64:.1f} deg"),
    (16, 1, "Brake booster pressure", lambda v: f"{v * 0.5:.1f} kPa"),
    (17, 1, "Master cylinder pressure", lambda v: f"{v * 0.1 - 2:.1f} MPa"),
    (18, 1, "G sensor (lateral)", lambda v: f"{v * 0.02 - 2.56:+.2f} G"),
    (19, 2, "Yaw rate", lambda v: f"{v * 0.05 - 1638.4:+.1f} deg/s"),
    (22, 2, "Engine rpm", lambda v: f"{v * 0.25:.0f} rpm"),
    (24, 2, "Steering angle", lambda v: f"{v - 65536 if v > 32767 else v:+d} deg"),
    (39, 1, "Hill hold active", lambda v: "yes" if v & 0x02 else "no"),
    (40, 1, "Brake switch", lambda v: "pressed" if v & 0x01 else "released"),
    (41, 1, "Stability control active", lambda v: "yes" if v & 0x02 else "no"),
    (41, 1, "TCS brake control", lambda v: "yes" if v & 0x04 else "no"),
    (41, 1, "TCS engine control", lambda v: "yes" if v & 0x08 else "no"),
    (42, 1, "ESP OFF switch", lambda v: "pressed" if v & 0x08 else "released"),
    (42, 1, "Steering angle signal", lambda v: "non neutral" if v & 0x40 else "neutral"),
    (47, 1, "Hill hold fitted", lambda v: "yes" if v & 0x02 else "no"),
    (47, 1, "Cluster communication", lambda v: "abnormal" if v & 0x20 else "normal"),
]


def decode_abs_00(data):
    """data = bytes after '61 00'. Returns [(name, text)], skipping FF (not supported) bytes."""
    return _decode_record(ABS_RECORD_00, data)


# BCM live data record (request 21 08 on CAN 271). Layout = SZ Viewer BCM_CAN_GV32_08_Local
# ("GV32" = Grand Vitara 3.2). Offsets count from the byte after "61 08".
BCM_RECORD_08 = [
    (0, 1, "Outside temperature", lambda v: f"{v - 40} C"),
    (1, 1, "Battery voltage", lambda v: f"{v * 0.0784:.1f} V"),
    (4, 1, "Auto light sensor", lambda v: f"{v * 0.0196:.2f} V"),
    (20, 1, "Vehicle speed", lambda v: f"{v} km/h"),
    (21, 2, "Engine rpm", lambda v: f"{v * 0.25:.0f} rpm"),
    (39, 1, "Driver door", lambda v: "open" if v & 0x01 else "closed"),
    (39, 1, "Passenger door", lambda v: "open" if v & 0x02 else "closed"),
    (41, 1, "Auto light switch", lambda v: "on" if v & 0x01 else "off"),
    (41, 1, "Front fog light switch", lambda v: "on" if v & 0x02 else "off"),
    (44, 1, "Door key switch", lambda v: "lock" if v & 0x01 else ("unlock" if v & 0x02 else "-")),
    (44, 1, "Door lock switch", lambda v: "lock" if v & 0x04 else ("unlock" if v & 0x08 else "-")),
    (45, 1, "Key in ignition", lambda v: "key in" if v & 0x80 else "pulled"),
    (46, 1, "Headlight switch", lambda v: "on" if v & 0x01 else "off"),
    (46, 1, "Tail light switch", lambda v: "on" if v & 0x02 else "off"),
    (47, 1, "Driver seatbelt", lambda v: "fastened" if v & 0x01 else "unfastened"),
    (48, 1, "Brake fluid level", lambda v: "LOW" if v & 0x01 else "normal"),
    (48, 1, "Parking brake", lambda v: "on" if v & 0x02 else "off"),
    (48, 1, "Charge light", lambda v: "on" if v & 0x04 else "off"),
    (48, 1, "Oil pressure switch", lambda v: "on" if v & 0x08 else "off"),
    (48, 1, "A/C switch", lambda v: "on" if v & 0x10 else "off"),
    (49, 1, "Ignition switch", lambda v: "on" if v & 0x01 else "off"),
    (49, 1, "ACC switch", lambda v: "on" if v & 0x04 else "off"),
    (50, 1, "Rear wiper", lambda v: "ON" if v & 0x01 else ("INT" if v & 0x02 else "off")),
    (50, 1, "Rear washer switch", lambda v: "on" if v & 0x80 else "off"),
    (57, 1, "Security alarm", lambda v: "active" if v & 0x20 else "disabled"),
]

# Combination meter live data record (request 21 01 on CAN 274). SZ Viewer's generic IPC_CAN_01_Local
# gives battery voltage at 0 and oil-change fields at 25..38; this car's record is only 30 bytes, so only
# the fields verified against the car are listed (rpm word at 21 matched the engine, 2026-10-17).
IPC_RECORD_01 = [
    (0, 1, "Battery voltage", lambda v: f"{v * 0.0784:.1f} V"),
    (1, 1, "Panel illumination (raw)", lambda v: f"{v} (230 lights off, 77 headlights on)"),
    (21, 2, "Engine rpm", lambda v: f"{v * 0.25:.0f} rpm"),
    (25, 1, "Oil change request light", lambda v: "on" if v & 0x08 else "off"),
    (30, 1, "Oil change conditions", lambda v: ("normal", "severe", "optional", "?")[(v >> 2) & 3]),
    (35, 2, "Distance since oil reset", lambda v: f"{v} km"),
    (37, 2, "Days since oil reset", lambda v: f"{v} days"),
]


def _decode_record(table, data):
    out = []
    for off, ln, name, fn in table:
        if off + ln > len(data):
            continue
        raw = data[off:off + ln]
        if all(b == 0xFF for b in raw):
            continue
        out.append((name, fn(raw[0] if ln == 1 else _word(data, off))))
    return out


def decode_bcm_08(data):
    return _decode_record(BCM_RECORD_08, data)


def decode_ipc_01(data):
    return _decode_record(IPC_RECORD_01, data)


# module name -> (local identifier, decoder) for a decoded live-data screen
RECORDS = {"HVAC": (0x08, decode_hvac_08), "ABS": (0x00, decode_abs_00),
           "BCM": (0x08, decode_bcm_08), "IPC": (0x01, decode_ipc_01)}


def open_adapter(port, bauds):
    last = None
    for b in bauds:
        try:
            elm = ELM(port, b)
            ver = elm.init()
            return elm, b, ver
        except Exception as ex:      # noqa
            last = ex
            try:
                elm.ser.close()
            except Exception:
                pass
    raise SystemExit(f"cannot talk to adapter on {port}: {last}")


def watch_record(m, lid, decoder, interval=1.0):
    """Refresh the decoded live-data record on screen until Ctrl+C."""
    try:
        while True:
            rows = []
            for p in m.request(f"21{lid:02X}", 2.0):
                if p[0] == 0x61 and len(p) > 2:
                    rows = decoder(p[2:])
            print("\033[H\033[J", end="")
            print(f"{m.name} live data (21 {lid:02X})  {time.strftime('%H:%M:%S')}   Ctrl+C to stop")
            for label, text in rows or [("no data", "")]:
                print(f"  {label:<26} {text}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print()


def run_module(elm, name, bus, dump_data, watch=False):
    m = Module(elm, name)
    ok, tried = m.connect(bus)
    print(f"== {name} ({DESC[name]}) ==")
    for b, r in tried:
        print(f"  {b}: {r}")
    if not ok:
        print("  not reachable")
        return False
    print(f"  connected via {m.bus}")
    for req, label in IDENT:
        for p in m.request(req):
            if p[0] != 0x7F:
                print(f"  {label:<12} {describe(p)}")
    for req, label in DTC_REQS:
        for p in m.request(req):
            if p[0] != 0x7F:
                codes = decode_dtcs(p)
                print(f"  {label:<22} {p.hex(' ').upper()}  -> {codes or 'no codes'}")
    if name in RECORDS:
        lid, decoder = RECORDS[name]
        if watch:
            watch_record(m, lid, decoder)
            elm.at("ATPC")
            return True
        for p in m.request(f"21{lid:02X}", 2.0):
            if p[0] == 0x61 and len(p) > 2:
                print(f"  live data (21 {lid:02X}):")
                for label, text in decoder(p[2:]):
                    print(f"    {label:<26} {text}")
    if dump_data:
        print("  local identifiers (21 xx) with a positive answer:")
        for i in range(0x100):
            for p in m.request(f"21{i:02X}", 1.5):
                if p[0] == 0x61:
                    print(f"    21 {i:02X}: {p[2:].hex(' ').upper()}")
    elm.at("ATPC")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--module", help="module name from --list")
    ap.add_argument("--bus", choices=["can", "kline"])
    ap.add_argument("--data", action="store_true", help="dump local identifiers 21 00..FF")
    ap.add_argument("--watch", action="store_true", help="refresh the decoded live data (modules with a known record)")
    a = ap.parse_args()

    if a.list:
        for n, (c, k) in MODULES.items():
            print(f"  {n:<11} {DESC[n]:<24} CAN {f'{c:03X}' if c else '-':<4} K-line {f'{k:02X}' if k else '-'}")
        return 0
    if not (a.scan or a.module):
        ap.error("use --list, --scan or --module NAME")

    elm, baud, ver = open_adapter(a.port, [a.baud] if a.baud else [115200, 38400])
    print(f"adapter: {ver} @ {baud}  battery {elm.at('ATRV')}")
    try:
        if a.module:
            run_module(elm, a.module.upper(), a.bus, a.data, a.watch)
        else:
            found = []
            for n in MODULES:
                m = Module(elm, n)
                ok, tried = m.connect(a.bus)
                print(f"  {n:<11} {'via ' + m.bus if ok else 'no answer'}", flush=True)
                if ok:
                    found.append(n)
                    elm.at("ATPC")
            print("\nreachable modules:", ", ".join(found) or "none")
    finally:
        elm.raw("ATZ", 1)
        elm.ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
