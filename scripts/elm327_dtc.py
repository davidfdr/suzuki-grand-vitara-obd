#!/usr/bin/env python3
"""Read (and optionally clear) diagnostic trouble codes from the engine ECM.

Reads: MIL status and code count (01 01), stored codes (03), pending codes (07),
permanent codes (0A), freeze frame (02), readiness monitors, and Suzuki KWP
code requests over CAN (18 xx). Generic P0/P2/U0 codes get a description.

Usage: elm327_dtc.py [PORT] [--baud N]
       elm327_dtc.py --clear --yes        # erase codes and freeze frame (mode 04)
"""
import argparse
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("live", os.path.join(HERE, "elm327_live.py"))
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)

DESCRIPTIONS = {
    "P0010": "Camshaft position actuator circuit (bank 1)", "P0011": "Camshaft timing over-advanced (bank 1)",
    "P0012": "Camshaft timing over-retarded (bank 1)", "P0016": "Crank/cam position correlation (bank 1)",
    "P0020": "Camshaft position actuator circuit (bank 2)", "P0021": "Camshaft timing over-advanced (bank 2)",
    "P0030": "O2 heater control circuit B1S1", "P0031": "O2 heater circuit low B1S1", "P0032": "O2 heater circuit high B1S1",
    "P0036": "O2 heater control circuit B1S2", "P0037": "O2 heater circuit low B1S2", "P0038": "O2 heater circuit high B1S2",
    "P0050": "O2 heater control circuit B2S1", "P0051": "O2 heater circuit low B2S1", "P0052": "O2 heater circuit high B2S1",
    "P0056": "O2 heater control circuit B2S2", "P0057": "O2 heater circuit low B2S2", "P0058": "O2 heater circuit high B2S2",
    "P0100": "MAF circuit", "P0101": "MAF range/performance", "P0102": "MAF circuit low", "P0103": "MAF circuit high",
    "P0105": "MAP circuit", "P0106": "MAP range/performance", "P0107": "MAP circuit low", "P0108": "MAP circuit high",
    "P0110": "IAT circuit", "P0111": "IAT range/performance", "P0112": "IAT circuit low", "P0113": "IAT circuit high",
    "P0115": "ECT circuit", "P0116": "ECT range/performance", "P0117": "ECT circuit low", "P0118": "ECT circuit high",
    "P0120": "TPS circuit", "P0121": "TPS range/performance", "P0122": "TPS circuit low", "P0123": "TPS circuit high",
    "P0125": "Coolant temp too low for closed loop", "P0128": "Coolant below thermostat regulating temp",
    "P0130": "O2 sensor circuit B1S1", "P0131": "O2 low voltage B1S1", "P0132": "O2 high voltage B1S1",
    "P0133": "O2 slow response B1S1", "P0134": "O2 no activity B1S1", "P0135": "O2 heater B1S1",
    "P0136": "O2 sensor circuit B1S2", "P0137": "O2 low voltage B1S2", "P0138": "O2 high voltage B1S2",
    "P0139": "O2 slow response B1S2", "P0140": "O2 no activity B1S2", "P0141": "O2 heater B1S2",
    "P0150": "O2 sensor circuit B2S1", "P0151": "O2 low voltage B2S1", "P0152": "O2 high voltage B2S1",
    "P0153": "O2 slow response B2S1", "P0154": "O2 no activity B2S1", "P0155": "O2 heater B2S1",
    "P0156": "O2 sensor circuit B2S2", "P0157": "O2 low voltage B2S2", "P0158": "O2 high voltage B2S2",
    "P0159": "O2 slow response B2S2", "P0160": "O2 no activity B2S2", "P0161": "O2 heater B2S2",
    "P0171": "System too lean (bank 1)", "P0172": "System too rich (bank 1)",
    "P0174": "System too lean (bank 2)", "P0175": "System too rich (bank 2)",
    "P0201": "Injector circuit cyl 1", "P0202": "Injector circuit cyl 2", "P0203": "Injector circuit cyl 3",
    "P0204": "Injector circuit cyl 4", "P0205": "Injector circuit cyl 5", "P0206": "Injector circuit cyl 6",
    "P0300": "Random/multiple misfire", "P0301": "Misfire cyl 1", "P0302": "Misfire cyl 2", "P0303": "Misfire cyl 3",
    "P0304": "Misfire cyl 4", "P0305": "Misfire cyl 5", "P0306": "Misfire cyl 6",
    "P0325": "Knock sensor circuit (bank 1)", "P0327": "Knock sensor low (bank 1)", "P0328": "Knock sensor high (bank 1)",
    "P0330": "Knock sensor circuit (bank 2)", "P0335": "Crankshaft position sensor circuit", "P0336": "CKP range/performance",
    "P0340": "Camshaft position sensor circuit (bank 1)", "P0345": "Camshaft position sensor circuit (bank 2)",
    "P0351": "Ignition coil A primary/secondary", "P0352": "Ignition coil B", "P0353": "Ignition coil C",
    "P0354": "Ignition coil D", "P0355": "Ignition coil E", "P0356": "Ignition coil F",
    "P0400": "EGR flow", "P0401": "EGR insufficient flow", "P0402": "EGR excessive flow",
    "P0420": "Catalyst efficiency below threshold (bank 1)", "P0430": "Catalyst efficiency below threshold (bank 2)",
    "P0440": "Evap system", "P0441": "Evap incorrect purge flow", "P0442": "Evap small leak", "P0443": "Evap purge valve circuit",
    "P0446": "Evap vent control circuit", "P0450": "Evap pressure sensor", "P0455": "Evap large leak", "P0456": "Evap very small leak",
    "P0460": "Fuel level sensor circuit", "P0461": "Fuel level sensor range", "P0462": "Fuel level sensor low", "P0463": "Fuel level sensor high",
    "P0480": "Cooling fan 1 control circuit", "P0481": "Cooling fan 2 control circuit",
    "P0500": "Vehicle speed sensor", "P0505": "Idle control system", "P0506": "Idle speed low", "P0507": "Idle speed high",
    "P0510": "Closed throttle position switch", "P0520": "Oil pressure sensor circuit", "P0530": "A/C refrigerant pressure sensor",
    "P0532": "A/C pressure sensor low", "P0533": "A/C pressure sensor high", "P0560": "System voltage", "P0562": "System voltage low",
    "P0563": "System voltage high", "P0601": "ECM memory checksum", "P0602": "ECM programming error", "P0603": "ECM keep-alive memory",
    "P0604": "ECM RAM", "P0605": "ECM ROM", "P0606": "ECM processor fault", "P0607": "Control module performance",
    "P0620": "Generator control circuit", "P0700": "Transmission control system (see TCM)", "P0705": "Transmission range sensor",
    "P0710": "Transmission fluid temp sensor", "P0715": "Input/turbine speed sensor", "P0720": "Output speed sensor",
    "P0725": "Engine speed input circuit", "P0730": "Incorrect gear ratio", "P0740": "Torque converter clutch circuit",
    "P0741": "TCC stuck off", "P0750": "Shift solenoid A", "P0755": "Shift solenoid B", "P0760": "Shift solenoid C",
    "P1000": "Manufacturer-specific, see Suzuki manual", "P2101": "Throttle actuator control motor circuit range",
    "P2102": "Throttle actuator motor circuit low", "P2103": "Throttle actuator motor circuit high",
    "P2111": "Throttle actuator stuck open", "P2112": "Throttle actuator stuck closed", "P2119": "Throttle body range/performance",
    "P2122": "Pedal position sensor D low", "P2123": "Pedal position sensor D high", "P2127": "Pedal position sensor E low",
    "P2128": "Pedal position sensor E high", "P2135": "TPS A/B voltage correlation", "P2138": "Pedal sensor D/E correlation",
    "P2195": "O2 signal biased lean B1S1", "P2196": "O2 signal biased rich B1S1",
    "P2197": "O2 signal biased lean B2S1", "P2198": "O2 signal biased rich B2S1",
    "P2270": "O2 signal stuck lean B1S2", "P2271": "O2 signal stuck rich B1S2",
    "P2272": "O2 signal stuck lean B2S2", "P2273": "O2 signal stuck rich B2S2",
    "U0001": "High speed CAN bus", "U0100": "Lost communication with ECM/PCM", "U0101": "Lost communication with TCM",
    "U0121": "Lost communication with ABS", "U0140": "Lost communication with BCM", "U0155": "Lost communication with instrument cluster",
    "U0164": "Lost communication with HVAC",
}

MONITORS_COMMON = ("Misfire", "Fuel system", "Components")
MONITORS_SPARK = ("Catalyst", "Heated catalyst", "Evap system", "Secondary air", "A/C refrigerant", "O2 sensor", "O2 heater", "EGR/VVT")


def dtc_text(hi, lo):
    v = hi << 8 | lo
    return f"{'PCBU'[v >> 14]}{(v >> 12) & 3}{(v >> 8) & 0xF:X}{(v >> 4) & 0xF:X}{v & 0xF:X}"


def describe(code):
    d = DESCRIPTIONS.get(code)
    if d is None and code[1] in "123":
        d = "manufacturer-specific code, see Suzuki service manual"
    return d or "no description in table"


def obd_request(elm, mode_hex, wait=3.0):
    """Send a raw OBD mode request; return list of response byte strings (mode+0x40 prefix)."""
    want = f"{int(mode_hex[:2], 16) + 0x40:02X}"
    out = []
    for line in elm.raw(mode_hex, wait):
        u = line.replace(" ", "").upper()
        if u.startswith(want) and all(c in "0123456789ABCDEF" for c in u):
            out.append(bytes.fromhex(u[: len(u) // 2 * 2]))
    return out


def parse_dtc_list(payload):
    """payload: bytes after the 43/47/4A byte. Returns code strings."""
    data = payload
    if len(data) % 2 == 1:          # CAN format carries a count byte first
        data = data[1:]
    codes = []
    for i in range(0, len(data) - 1, 2):
        if data[i] or data[i + 1]:
            codes.append(dtc_text(data[i], data[i + 1]))
    return codes


def report(elm, clear=False, yes=False):
    """Read everything and return the report as text. elm must already be initialised."""
    lines = []

    def print(*args):             # noqa: A001  (collect instead of printing)
        lines.append(" ".join(str(x) for x in args))
    if True:
        print(f"protocol: {elm.protocol}   battery: {elm.voltage()}\n")

        # --- MIL status and readiness (01 01) --------------------------------
        st = elm.query(0x01)
        if st and len(st) >= 4:
            mil = bool(st[0] & 0x80)
            n = st[0] & 0x7F
            print(f"MIL (check engine lamp): {'ON' if mil else 'off'}   stored codes reported: {n}")
            avail, incomplete = [], []
            for i, name in enumerate(MONITORS_COMMON):
                if st[1] & (1 << i):
                    avail.append(name)
                    if st[1] & (1 << (i + 4)):
                        incomplete.append(name)
            for i, name in enumerate(MONITORS_SPARK):
                if st[2] & (1 << i):
                    avail.append(name)
                    if st[3] & (1 << i):
                        incomplete.append(name)
            print(f"readiness monitors: {len(avail) - len(incomplete)}/{len(avail)} complete"
                  + (f", incomplete: {', '.join(incomplete)}" if incomplete else ""))
        else:
            print("MIL status: no answer")
        print()

        # --- stored / pending / permanent -------------------------------------
        for mode, label in (("03", "Stored codes (confirmed, MIL-related)"),
                            ("07", "Pending codes (seen once, not yet confirmed)"),
                            ("0A", "Permanent codes (cleared only by the ECU after repair)")):
            resp = obd_request(elm, mode)
            if not resp:
                print(f"{label}: no answer / not supported")
                continue
            codes = []
            for r in resp:
                codes += parse_dtc_list(r[1:])
            print(f"{label}: {len(codes) or 'none'}")
            for c in codes:
                print(f"   {c}  {describe(c)}")
        print()

        # --- freeze frame (02 02 00 = DTC that triggered the freeze frame) ------
        ff = obd_request(elm, "020200")
        ff_code = None
        for r in ff:
            if len(r) >= 5 and (r[3] or r[4]):
                ff_code = dtc_text(r[3], r[4])
        if ff_code:
            print(f"Freeze frame stored for {ff_code}  {describe(ff_code)}")
            for pid, label in ((0x0C, "RPM"), (0x0D, "speed"), (0x05, "coolant"), (0x04, "load"),
                               (0x06, "STFT B1"), (0x07, "LTFT B1"), (0x0B, "MAP"), (0x0F, "IAT"), (0x11, "throttle")):
                rr = obd_request(elm, f"02{pid:02X}00")
                for r in rr:
                    if len(r) > 3:
                        v, text = live.decode(pid, r[3:])
                        print(f"   {label:<10} {text}")
        else:
            print("Freeze frame: none stored")
        print()

        # --- Suzuki KWP code requests over CAN --------------------------------
        print("Suzuki KWP requests (raw):")
        for req in ("1800FF00", "1802FF00", "1800", "13", "1A9C"):
            r = elm.raw(req, 3.0)
            print(f"   {req:<9} -> {r}")

        # --- clear ------------------------------------------------------------
        if clear:
            if not yes:
                print("\n--clear needs --yes as well. Nothing was erased.")
            else:
                r = elm.raw("04", 5.0)
                ok = any(l.replace(" ", "").upper().startswith("44") for l in r)
                print(f"\nclear codes: {'done' if ok else 'failed'} {r}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=None, help="serial baud (default: auto)")
    ap.add_argument("--clear", action="store_true", help="clear codes and freeze frame (mode 04)")
    ap.add_argument("--yes", action="store_true", help="required together with --clear")
    a = ap.parse_args()
    elm, _ = live.open_adapter(a.port, a.baud)
    try:
        sys.stdout.write(report(elm, a.clear, a.yes) + "\n")
    finally:
        elm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
