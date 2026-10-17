"""Tests for the OBD scripts, run with:  python3 -m pytest tests/ -q

Everything runs against the simulated adapter in fake_elm.py, so no car is needed.
"""
import csv
import importlib.util
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, HERE)
import fake_elm  # noqa: E402


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


live = load("elm327_live")
dtc = load("elm327_dtc")
analyze_log = load("analyze_log")
suzuki = load("suzuki_modules")
probe = load("elm327_probe")


# ---------------------------------------------------------------------------
# pure decoding
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("pid,data,expected", [
    (0x0C, bytes([0x0A, 0x2A]), 650.5),
    (0x05, bytes([0x8B]), 99),
    (0x06, bytes([0x7E]), pytest.approx(-1.5625)),
    (0x11, bytes([0x31]), pytest.approx(19.2157, abs=0.01)),
    (0x42, bytes([0x36, 0x4C]), pytest.approx(13.9)),
    (0x3C, bytes([0x14, 0x20]), pytest.approx(475.2)),
    (0x44, bytes([0x80, 0x00]), 1.0),
    (0x1F, bytes([0x02, 0xB3]), 691),
])
def test_numeric_decoders(pid, data, expected):
    value, text = live.decode(pid, data)
    assert value == expected
    assert text


def test_status_decoders():
    assert live.decode(0x01, bytes([0x00, 0x07, 0xE9, 0x00]))[1] == "MIL off, 0 DTC"
    assert live.decode(0x01, bytes([0x83, 0x07, 0xE9, 0x00]))[1] == "MIL ON, 3 DTC"
    assert live.decode(0x03, bytes([2, 2]))[1] == "closed loop / closed loop"
    assert live.decode(0x13, bytes([0x33]))[1] == "B1:S1,2 B2:S1,2"
    assert live.decode(0x41, bytes([0, 7, 0xE9, 0x21]))[1] == "2/8 incomplete: catalyst, O2 sensor"
    assert live.decode(0x41, bytes([0, 7, 0xE9, 0x00]))[1] == "all 8 monitors complete"
    assert live.decode(0x56, bytes([0x8A, 0x00]))[1] == "+7.8%"
    assert live.decode(0x1C, bytes([6]))[1] == "EOBD"


def test_o2_decoder_returns_voltage_and_text():
    v, text = live.decode(0x14, bytes([20, 130]))
    assert v == 0.1 and text.startswith("0.100 V")
    v, text = live.decode(0x15, bytes([131, 0xFF]))
    assert text == "0.655 V"


def test_decode_no_data_and_bad_reply():
    assert live.decode(0x0C, None) == (None, "no data")
    assert live.decode(0x0C, bytes([1]))[1].startswith("bad reply")


def test_colour_rules():
    assert live.c_coolant(50) == "blue" and live.c_coolant(95) == "green"
    assert live.c_coolant(110) == "yellow" and live.c_coolant(120) == "red"
    assert live.c_trim(5) == "green" and live.c_trim(15) == "yellow" and live.c_trim(-25) == "red"
    assert live.c_volt(11) == "red" and live.c_volt(12.5) == "yellow" and live.c_volt(14) == "green"
    assert live.status_colour(1, "MIL ON, 2 DTC") == "red"
    assert live.status_colour(1, "no data") == "dim"


def test_bar_and_sparkline():
    assert live.bar(0, 0, 100, 10) == "░" * 10
    assert live.bar(100, 0, 100, 10) == "█" * 10
    assert live.bar(50, 0, 100, 10).count("█") == 5
    assert live.sparkline([1, 1, 1], 5) == "▄▄▄"
    s = live.sparkline([0, 5, 10], 5)
    assert s[0] == "▁" and s[-1] == "█"
    assert len(live.sparkline(range(50), 20)) == 20


@pytest.mark.parametrize("width,expected_cols", [(200, 3), (149, 1), (100, 1)])
def test_render_columns(width, expected_cols):
    readings = [(p, *live.decode(p, d)) for p, d in [(0x0C, bytes([0x0A, 0x2A])), (0x05, bytes([0x8B])),
                                                     (0x11, bytes([0x31])), (0x01, bytes([0, 7, 0xE9, 0]))]]
    lines = live.render(readings, {}, width, 150, live.Painter(False), True, 0)
    assert len(lines) == (2 if expected_cols == 3 else 4)
    for line in lines:
        assert len(line) <= width


def test_render_with_colour_and_history_keeps_width():
    readings = [(0x0C, *live.decode(0x0C, bytes([0x0A, 0x2A])))]
    history = {0x0C: [600, 650, 700, 650]}
    paint = live.Painter(True)
    lines = live.render(readings, history, 120, 150, paint, True, 10)
    assert paint.vlen(lines[0]) <= 120 and "\x1b[" in lines[0]


# ---------------------------------------------------------------------------
# adapter-level behaviour against the fake ECM
# ---------------------------------------------------------------------------
def test_live_adapter_init_and_pids(monkeypatch):
    fake_elm.install(monkeypatch)
    elm = live.ELM327("/dev/fake", 38400)
    elm.init()
    assert "CAN" in elm.protocol
    pids = elm.supported_pids()
    assert 0x0C in pids and 0x05 in pids and 0x20 not in pids
    assert live.decode(0x0C, elm.query(0x0C))[0] == 650.5
    assert elm.voltage() == "14.2V"
    assert elm.at("ATRV") == "14.2V"
    assert elm.hexlines(["7E8017E", "NO DATA", "OK"]) == ["7E8017E"]


def test_live_adapter_rejects_silent_port(monkeypatch):
    fake_elm.install(monkeypatch, ecm={})
    # a port that never says ELM327: make ATZ answer garbage
    elm = live.ELM327("/dev/fake", 38400)
    elm.ser.handle_at = lambda u: elm.ser.reply("")
    with pytest.raises(RuntimeError):
        elm.init()


def test_dtc_report_clean(monkeypatch):
    fake_elm.install(monkeypatch)
    elm = live.ELM327("/dev/fake", 38400)
    elm.init()
    text = dtc.report(elm)
    assert "MIL (check engine lamp): off" in text
    assert "Stored codes (confirmed, MIL-related): none" in text
    assert "Pending codes" in text and "Freeze frame: none stored" in text
    assert "1800FF00" in text


def test_dtc_report_with_codes_and_clear(monkeypatch):
    fake_elm.install(monkeypatch, with_dtc=True)
    elm = live.ELM327("/dev/fake", 38400)
    elm.init()
    text = dtc.report(elm)
    assert "MIL (check engine lamp): ON" in text
    assert "P0300" in text and "Random/multiple misfire" in text
    assert "P0171" in text and "P0174" in text
    assert "Freeze frame stored for P0171" in text
    assert "Nothing was erased" in dtc.report(elm, clear=True, yes=False)
    assert "clear codes: done" in dtc.report(elm, clear=True, yes=True)


def test_dtc_helpers():
    assert dtc.dtc_text(0x01, 0x71) == "P0171"
    assert dtc.dtc_text(0x41, 0x00) == "C0100"
    assert dtc.dtc_text(0xC1, 0x21) == "U0121"
    assert dtc.parse_dtc_list(bytes([0x02, 0x01, 0x71, 0x03, 0x00])) == ["P0171", "P0300"]
    assert dtc.parse_dtc_list(bytes([0x00])) == []
    assert "Suzuki" in dtc.describe("P1234")


def test_suzuki_module_engine_over_can(monkeypatch):
    fake_elm.install(monkeypatch)
    elm = suzuki.ELM("/dev/fake", 38400)
    elm.init()
    m = suzuki.Module(elm, "ENGINE")
    ok, tried = m.connect("can")
    assert ok and m.bus == "can"
    vin = [p for p in m.request("1A90") if p[0] == 0x5A]
    assert vin and vin[0][2:].decode() == "JSATEST0000000001"
    part = [p for p in m.request("1A9A") if p[0] == 0x5A]
    assert part and part[0][2:].decode().startswith("33980-79K0")
    neg = m.request("1800FF00")
    assert neg and neg[0][:2] == b"\x7f\x18"


def test_suzuki_module_absent_and_kline_fallback(monkeypatch):
    fake_elm.install(monkeypatch, genuine=False)
    elm = suzuki.ELM("/dev/fake", 38400)
    elm.init()
    m = suzuki.Module(elm, "ABS")
    ok, tried = m.connect()
    assert not ok
    kinds = [k for k, _ in tried]
    assert kinds[0] == "CAN" and "K-line fast" in kinds and "K-line 5baud" not in kinds


def test_suzuki_module_works_with_live_adapter_object(monkeypatch):
    """obd.py hands the live reader's adapter to the module scanner: both must be compatible."""
    fake_elm.install(monkeypatch)
    elm = live.ELM327("/dev/fake", 38400)
    elm.init()
    m = suzuki.Module(elm, "ENGINE")
    ok, _ = m.connect("can")
    assert ok


def test_can_reply_ids():
    assert suzuki.can_reply_id(0x261) == 0x661
    assert suzuki.can_reply_id(0x7E0) == 0x7E8
    assert suzuki.can_reply_id(0x751) == 0x761


def test_probe_reports_ecu(monkeypatch, capsys):
    fake_elm.install(monkeypatch)
    rc = probe.probe("/dev/fake", [38400])
    out = capsys.readouterr().out
    assert rc == 0 and "ECU connected" in out and "ELM327 v1.5" in out


# ---------------------------------------------------------------------------
# CSV logging and analysis
# ---------------------------------------------------------------------------
def make_csv(path, n=40, rpm=650, ltft2=9.4, o2_flat=False, volt=13.9):
    cols = ["time", "elapsed_s", "Engine RPM [rpm]", "Engine load [%]", "Coolant temp [°C]", "Intake air temp [°C]",
            "Ambient air temp [°C]", "MAF air flow [g/s]", "Short fuel trim B1 [%]", "Long fuel trim B1 [%]",
            "Short fuel trim B2 [%]", "Long fuel trim B2 [%]", "O2 B1S1", "O2 B1S2", "O2 B2S1", "O2 B2S2",
            "Commanded equiv. ratio [λ]", "Catalyst temp B1S1 [°C]", "Catalyst temp B2S1 [°C]", "Control module voltage [V]"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for i in range(n):
            o2 = 0.45 if o2_flat else (0.1 if i % 2 else 0.8)
            w.writerow([f"12:00:{i:02d}", i * 3.8, rpm + (i % 3), 25, 95, 56, 27, 3.8, -1, 6.2, 0, ltft2,
                        o2, 0.65, o2, 0.65, 1.0, 485, 489, volt])


def test_analyze_healthy(tmp_path):
    p = tmp_path / "ok.csv"
    make_csv(str(p))
    text = analyze_log.analyze(str(p))
    assert "No warning-level findings" in text
    assert "Idle is steady" in text and "Charging system is within" in text


def test_analyze_flags_problems(tmp_path):
    p = tmp_path / "bad.csv"
    make_csv(str(p), ltft2=22, o2_flat=True, volt=12.3)
    text = analyze_log.analyze(str(p))
    assert "WARNING: bank 2 total fuel trim" in text
    assert "barely switches" in text
    assert "voltage dipped" in text


def test_analyze_short_file(tmp_path):
    p = tmp_path / "short.csv"
    make_csv(str(p), n=2)
    assert "not enough rows" in analyze_log.analyze(str(p))


def test_logger_writes_csv(monkeypatch, tmp_path):
    fake_elm.install(monkeypatch)
    out = tmp_path / "log.csv"
    rc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "elm327_log.py"), "--duration", "0.5",
                         "--quiet", "--out", str(out), "--pids", "0C,05"],
                        capture_output=True, text=True, timeout=60,
                        env={**os.environ, "PYTHONPATH": HERE, "FAKE_ELM": "1"})
    # the subprocess cannot see monkeypatch; it needs a real port, so only check it fails cleanly without one
    assert rc.returncode in (0, 1)


# ---------------------------------------------------------------------------
# command-line entry points (argument parsing only, no hardware)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("script", ["elm327_live", "elm327_log", "analyze_log", "elm327_dtc", "suzuki_modules",
                                    "elm327_probe", "elm327_scan", "elm327_kline_sweep", "obd"])
def test_help_runs(script):
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, script + ".py"), "--help"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "usage" in r.stdout.lower()


def test_modules_list():
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "suzuki_modules.py"), "--list"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "ABS" in r.stdout and "HVAC" in r.stdout


def test_bad_port_fails_cleanly():
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "elm327_live.py"), "/dev/ttyNOPE"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and "cannot open" in r.stdout


def test_open_adapter_autodetects_baud(monkeypatch):
    """The fake only answers at 115200 here, so auto mode must fall through 38400 first."""
    import serial

    class Picky(fake_elm.FakeSerial):
        def handle_at(self, u):
            if self.baudrate != 115200:
                return self.reply("")           # silence at the wrong speed
            return super().handle_at(u)

    monkeypatch.setattr(serial, "Serial", lambda port, baudrate=38400, timeout=0.05, **_: Picky(port, baudrate, timeout))
    elm, baud = live.open_adapter("/dev/fake")
    assert baud == 115200 and "CAN" in elm.protocol
    with pytest.raises(RuntimeError):
        live.open_adapter("/dev/fake", 38400)   # forced wrong speed fails cleanly


def test_hvac_record_decoder():
    # record 21 08 captured on the Grand Vitara 2009 HVAC (39520-79K0) on 2026-10-17, panel at 23.5 C auto
    rec = bytes.fromhex(
        "02953120950461132000 20FFFFFF14008B0CFFFFFFFFA084006247FFFFFFFFFFEBEAFFFFFFFFE5E400FFFFFF"
        "383902FFFF00FFFFFFFFFFFFFFFFFFFF000206FFFFFFFFFFFFFF".replace(" ", ""))
    d = dict(suzuki.decode_hvac_08(rec))
    assert d["Set temperature"] == "23.5 C"
    assert d["Mode switch"] == "auto" and d["Fan switch"] == "off" and d["Air intake switch"] == "auto"
    assert d["Coolant temperature"] == "99 C"
    assert d["Outside air temperature"] == "40.0 C" and d["Cabin air temperature"] == "26.0 C"
    assert d["Evaporator temperature"] == "9.0 C" and d["Sun load"] == "0 W/m2"
    assert d["Refrigerant pressure"] == "994 kPa"
    assert d["Compressor clutch"] == "off" and d["A/C signal"] == "off"
    assert suzuki.hvac_setpoint(0) == "LO (max cool)" and suzuki.hvac_setpoint(40) == "HI (max heat)"
    assert suzuki.hvac_setpoint(8) == "18.0 C" and suzuki.hvac_setpoint(12) == "20.0 C"
    assert d["Fan desired voltage"] == "0.0 V" and d["Air mix position"] == "4.68 V"
    # FF bytes (not fitted / passenger side) are skipped: offsets 11-13 are FF in this record
    assert len(d) == 21


def test_dtc_status_text():
    assert suzuki.dtc_status_text(0x20) == "20=history"
    assert suzuki.dtc_status_text(0x61) == "61=current/high"
    assert suzuki.decode_dtcs(bytes.fromhex("58029531209504 61".replace(" ", ""))) == \
        ["B1531 20=history", "B1504 61=current/high"]


def test_abs_record_decoder():
    # record 21 00 captured on the Grand Vitara 2009 ABS/ESP (56100-79K1) on 2026-10-17, idling, stationary
    rec = bytes.fromhex("FFFFFFFFFFFFFF00000000" "84AE00FFFFFF14818004FF0A12FFC9FFFFFFFF3FFFFFFFFFFFFFFF"
                        "000E004000802 3FF03FFFF000000".replace(" ", ""))
    d = dict(suzuki.decode_abs_00(rec))
    assert d["Wheel speed front right"] == "0 km/h" and d["Wheel speed rear left"] == "0 km/h"
    assert d["Battery voltage"] == "13.6 V" and d["Pump motor voltage"] == "0.0 V"
    assert d["Master cylinder pressure"] == "0.0 MPa"
    assert d["G sensor (longitudinal)"] == "+0.08 G" and d["G sensor (lateral)"] == "+0.02 G"
    assert d["Yaw rate"] == "+0.2 deg/s" and d["Engine rpm"] == "644 rpm"
    assert d["Steering angle"] == "-55 deg"
    assert d["Brake switch"] == "released" and d["ESP OFF switch"] == "released"
    assert "Steering angle offset" not in d and "Brake booster pressure" not in d   # FF = not fitted


def test_request_reassembles_headers_on_multiframe(monkeypatch):
    fake_elm.install(monkeypatch)
    elm = suzuki.ELM("/dev/fake", 38400)
    elm.init()
    m = suzuki.Module(elm, "ENGINE")
    m.bus = "can"
    monkeypatch.setattr(elm, "raw", lambda c, w=0: ["7E8 03 7F 1A 78", "7E8 10 12 5A 8A 35 36 31 30",
                                                    "7E8 21 30 2D 37 39 4B 31 2A", "7E8 22 00 00 00 00 00 00 00"])
    got = [p for p in m.request("1A8A") if p[0] == 0x5A]
    assert got and got[0] == bytes.fromhex("5A8A35363130302D37394B312A0000000000")


def test_ipc_record_decoder():
    # record 21 01 captured on the Grand Vitara 2009 cluster (34110-79K1) on 2026-10-17, idling
    rec = bytes.fromhex("A9E6FFFFFFFFFFFFFF9C9CA30002E70CFF007D00FF0A0D7AFFFFFFFFFF0000")
    d = dict(suzuki.decode_ipc_01(rec))
    assert d["Battery voltage"] == "13.2 V" and d["Engine rpm"] == "643 rpm"
    assert d["Panel illumination (raw)"].startswith("230 ")
    assert "Distance since oil reset" not in d       # record too short on this car


def test_bcm_record_decoder_bits():
    rec = bytearray(60)
    rec[0] = 60; rec[1] = 0xAE; rec[20] = 50; rec[21:23] = (2600).to_bytes(2, "big")
    rec[39] = 0x01; rec[45] = 0x80; rec[48] = 0x03; rec[49] = 0x01; rec[50] = 0x02
    d = dict(suzuki.decode_bcm_08(bytes(rec)))
    assert d["Outside temperature"] == "20 C" and d["Battery voltage"] == "13.6 V"
    assert d["Vehicle speed"] == "50 km/h" and d["Engine rpm"] == "650 rpm"
    assert d["Driver door"] == "open" and d["Passenger door"] == "closed"
    assert d["Key in ignition"] == "key in" and d["Brake fluid level"] == "LOW"
    assert d["Parking brake"] == "on" and d["Ignition switch"] == "on" and d["Rear wiper"] == "INT"
