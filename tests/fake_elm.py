"""A simulated ELM327 + Suzuki ECM for tests. No hardware needed.

FakeSerial mimics the pyserial interface used by the scripts (write/read/reset_input_buffer/
baudrate/close). It answers AT commands like an adapter and OBD/KWP requests like the
Grand Vitara ECM seen on the real car.
"""
import time

ECM = {
    # OBD mode 01
    "0100": "4100BF9FB993", "0120": "4120A007B011", "0140": "4140FED08800", "0160": "41600000000",
    "0101": "41010007E900", "0103": "41030202", "0104": "410440", "0105": "41058B", "0106": "41067E",
    "01077E": None, "0107": "410788", "0108": "410880", "0109": "41098C", "010C": "410C0A2A", "010D": "410D00",
    "010E": "410E8C", "010F": "410F6A", "0110": "41100180", "0111": "411131", "0113": "411333",
    "0114": "41141480", "0115": "41158380", "0118": "41181580", "0119": "41198680", "011C": "411C06",
    "011F": "411F02B3", "0121": "41210000", "012E": "412E3C", "0130": "413062", "0131": "413105 9D",
    "0133": "413358", "013C": "413C1420", "013D": "413D1425", "0141": "414100 07E900", "0142": "4142364C",
    "0143": "41430030", "0144": "41448000", "0145": "414508", "0146": "414643", "0147": "41472E",
    "0149": "414926", "014A": "414A13", "014C": "414C0D", "0156": "41568A00", "0158": "41588B00",
    # DTC modes
    "03": "4300", "07": "4700", "0A": "NO DATA", "020200": "4202000000",
    "04": "44",
    # KWP over CAN
    "3E": "7E", "3E00": "7F3E12", "1001": "7F1012", "1081": "7F1012", "1092": "7F1012",
    "1A87": "7F1A31", "1A81": "7F1A31",
    "1A90": "MULTI:5A904A53415445535430303030303030303031",
    "1A9A": "MULTI:5A9A33333938302D37394B3030303030303030",
    "1800FF00": "7F1811", "1802FF00": "7F1811", "1800": "7F1811", "13": "7F1311", "1A9C": "7F1A31",
    "1700FFFF": "7F1711", "2100": "7F2112", "2101": "7F2112", "1902FF": "7F1911",
    "22F190": "7F2211", "22F187": "7F2211", "1A88": "7F1A31", "1A8A": "7F1A31", "1A9B": "7F1A31",
}
RESPONDER = 0x7E8


class FakeSerial:
    def __init__(self, port="/dev/fake", baudrate=38400, timeout=0.05, ecm=None, headers_default=False,
                 with_dtc=False, genuine=True):
        self.port, self.baudrate, self.timeout = port, baudrate, timeout
        self.buf = b""
        self.inbuf = b""
        self.echo = True
        self.spaces = True
        self.headers = headers_default
        self.caf = True
        self.proto = "0"
        self.header = 0x7DF
        self.cra = None
        self.genuine = genuine          # genuine: ATCRA works, ATFI/ATSI exist
        self.ecm = dict(ECM if ecm is None else ecm)
        self.sent = []
        self.is_open = True
        if with_dtc:
            self.ecm["0101"] = "4101 8207E900".replace(" ", "")
            self.ecm["03"] = "4301030001710174"
            self.ecm["07"] = "4701030001710000"
            self.ecm["020200"] = "4202000171"

    # --- pyserial surface -------------------------------------------------
    def write(self, data):
        self.inbuf += data
        while b"\r" in self.inbuf:
            cmd, self.inbuf = self.inbuf.split(b"\r", 1)
            self.handle(cmd.decode(errors="replace").strip())
        return len(data)

    def read(self, n=1):
        out, self.buf = self.buf[:n], self.buf[n:]
        if not out:
            time.sleep(min(self.timeout, 0.002))
        return out

    def reset_input_buffer(self):
        self.buf = b""

    def close(self):
        self.is_open = False

    # --- adapter behaviour ------------------------------------------------
    def reply(self, *lines):
        for l in lines:
            self.buf += (l + "\r").encode()
        self.buf += b"\r>"

    def data(self, *lines):
        """Reply with hex data lines, spaced like a real adapter when ATS1 is active."""
        if self.spaces:
            out = []
            for l in lines:
                if ":" in l:
                    head, rest = l.split(":", 1)
                    out.append(head + ": " + " ".join(rest[i:i + 2] for i in range(0, len(rest), 2)))
                elif all(c in "0123456789ABCDEF" for c in l) and len(l) > 3:
                    if self.headers and len(l) % 2 == 1:
                        out.append(l[:3] + " " + " ".join(l[i:i + 2] for i in range(3, len(l), 2)))
                    else:
                        out.append(" ".join(l[i:i + 2] for i in range(0, len(l), 2)))
                else:
                    out.append(l)
            lines = out
        self.reply(*lines)

    def handle(self, cmd):
        self.sent.append(cmd)
        u = cmd.upper().replace(" ", "")
        if self.echo and cmd:
            self.buf += (cmd + "\r").encode()
        if not u:
            self.reply()
            return
        if u.startswith("AT"):
            return self.handle_at(u)
        return self.handle_obd(u)

    def handle_at(self, u):
        if u == "ATZ":
            self.echo, self.headers, self.header, self.cra = True, False, 0x7DF, None
            self.spaces = True
            self.reply("", "ELM327 v1.5")
        elif u.startswith("ATS") and u[3:] in ("0", "1"):
            self.spaces = u.endswith("1"); self.reply("OK")
        elif u in ("ATI",):
            self.reply("ELM327 v1.5")
        elif u == "AT@1":
            self.reply("OBDII to RS232 Interpreter")
        elif u == "ATRV":
            self.reply("14.2V")
        elif u.startswith("ATE"):
            self.echo = u.endswith("1"); self.reply("OK")
        elif u.startswith("ATH"):
            self.headers = u.endswith("1"); self.reply("OK")
        elif u.startswith("ATCAF"):
            self.caf = u.endswith("1"); self.reply("OK")
        elif u.startswith("ATSP") or u.startswith("ATTP"):
            self.proto = u[4:] or "0"; self.reply("OK")
        elif u == "ATDP":
            self.reply("AUTO, ISO 15765-4 (CAN 11/500)")
        elif u == "ATDPN":
            self.reply("A6")
        elif u.startswith("ATSH"):
            h = u[4:]
            self.header = int(h, 16) if len(h) == 3 else h
            self.reply("OK")
        elif u.startswith("ATCRA"):
            self.cra = int(u[5:], 16) if len(u) > 5 else None
            self.reply("OK")
        elif u in ("ATFI", "ATSI"):
            self.reply("BUS INIT: ERROR" if self.genuine else "?")
        elif u == "ATPC":
            self.reply("OK")
        elif u.startswith("ATBRD"):
            self.reply("OK")
        elif u == "ATMA":
            self.reply("11100", "7E8017E", "STOPPED")
        else:
            self.reply("OK")

    def handle_obd(self, u):
        if self.proto in ("4", "5"):            # K-line: nothing on this car
            self.reply("BUS INIT: ERROR")
            return
        if isinstance(self.header, int) and self.header not in (0x7DF, 0x7E0, 0x7E1, 0x700):
            self.reply("NO DATA")
            return
        if self.cra is not None and self.genuine and self.cra != RESPONDER:
            self.reply("NO DATA")
            return
        ans = self.ecm.get(u)
        if ans is None:
            ans = "7F" + u[:2] + "11" if not u.startswith("01") else "NO DATA"
        if ans == "NO DATA":
            self.reply("NO DATA")
            return
        if ans.startswith("MULTI:"):
            data = ans[6:]
            n = len(data) // 2
            if self.headers:
                first = f"{RESPONDER:03X}10{n:02X}{data[:12]}"
                rest = [f"{RESPONDER:03X}2{i:X}{data[12 + 14 * i: 12 + 14 * (i + 1)]:0<14}" for i in range((n - 6 + 6) // 7)]
                self.data("7E8037F" + u[:2] + "78", first, *rest)
            else:
                # real adapters print the total length alone ("012") and then the bare data in 7-byte segments
                segs = [f"{i}:{data[14 * i: 14 * (i + 1)]}" for i in range((len(data) + 13) // 14)]
                self.data("7F" + u[:2] + "78", f"{n:03X}", *segs)
            return
        if self.headers:
            self.data(f"{RESPONDER:03X}{len(ans) // 2:02X}{ans}")
        else:
            self.data(ans)


def install(monkeypatch, **kw):
    """Patch serial.Serial so every script talks to the fake ECM."""
    import serial

    def factory(port, baudrate=38400, timeout=0.05, **_):
        return FakeSerial(port, baudrate, timeout, **kw)
    monkeypatch.setattr(serial, "Serial", factory)
    return factory
