# Suzuki Grand Vitara V6 2009 - diagnostic notes (2026-09-19)

## Adapter
ELM327 v1.5 clone, Prolific PL2303 USB, /dev/ttyUSB0, 38400 baud.
Limits found by testing:
- ATCRA / ATCF / ATCM are answered "OK" but ignored. During a request only IDs 7E8-7EF are shown.
- ATFI and ATSI (explicit K-line init) answer "?" (not implemented). Auto K-line init fails on every address.
- Raw CAN mode (ATCAF0) prints nothing. ATMA drops/corrupts frames on a busy bus.

## Engine ECM (works)
CAN 11-bit 500 kbaud, request 7E0 (also 7DF), reply 7E8. KWP2000 over CAN.
- 3E -> 7E (tester present), 1A 90 -> VIN, 1A 9A -> part number, OBD mode 01 PIDs (40 supported), mode 09 not supported.
- Scripts: scripts/elm327_live.py, scripts/elm327_probe.py, scripts/check_elm327.sh, scripts/elm327_scan.py

## Other modules (from SZ Viewer L1-2025-01-08 decompiled source)
CAN request IDs (reply = request + 0x400, flow control ID = request ID):
  261 ABS/ESP   262 4WD   263 PS    264 CCM
  271 BCM       272 HVAC  273 SRS   274 IPC meter  275 KLS  276 ALC  277 ICM
  7E0 Engine    7E1 AT    (reply = id | 8)
K-line ISO 14230 addresses (header 81 <addr> F1, tester F1, then ATFI; 5-baud: ATIIA <addr>, ATSI):
  29 ABS/ESP  09 AC  99 HVAC  98 HVAC-2  5A BCM  21 4WD  59 SRS  58 SRS-H
  61 Meter    5D Immo  31 PS  5C TPMS  5B OCM  19 AT  11 Engine  01 Powertrain
SZ Viewer ELM init: ATD ATE0 ATL0 ATS0 ATH0 ATD0 ATAL ATIB10 ATKW0 ATSW00 ATAT0 ATCAF1 ATCFC1 ATFCSM0,
then per module ATTP6 ATSH <id> ATCRA <reply> ATFCSH <id> ATFCSD 30 00 00 ATFCSM 1, tester present 3E.

Result on this car with this adapter: none of the CAN or K-line module addresses answered.
The CAN replies (661, 672, ...) fall outside the clone's fixed 7E8-7EF window, and the clone has no
usable K-line init, so the adapter cannot reach them. A genuine-behaviour ELM327 (v1.4b+, working
ATCRA/ATFI/ATSI, e.g. OBDLink SX) is needed. SZ Viewer Linux is installed at ~/opt/sz-viewer/bin/sz-viewer.sh.

## Extra adapter tests (same session, later)
- Unknown commands get "?", so ATCRA/ATCF/ATCM are recognised but non-functional in every syntax and order tried
  (with/without space, before/after ATSH, re-ATSP, with flow-control setup). The 7E8 reply always passes.
- ATPPS works. ATBRD 23 switches the serial link to 115200 (confirm with CR right after the "ELM327 v1.5" hello).
- ATMA remains unusable even at 115200: ~11 frames/s of a ~50+ frames/s bus, most IDs shown as a single "00" byte.
  Passive sniffing of ABS/HVAC broadcasts is therefore not possible with this adapter.
- Purchase target: OBDLink EX (successor of the discontinued SX) or Vgate vLinker FS (USB).

## Adapter purchase options for Brazil (checked 2026-09-19)
- OBDLink EX, Amazon.com.br (Amazon Global, taxes included): https://www.amazon.com.br/dp/B081VQVD3F  R$ 601,45 + R$ 8,90
- Vgate vLinker FS USB, Amazon.com.br (Amazon Global, taxes included): https://www.amazon.com.br/dp/B094Z7PBLS  R$ 287,16 + R$ 8,90
  (FTDI VCP bridge -> /dev/ttyUSB0 on Linux; Vgate suggests updating firmware to >= 2.2.78 for K-line cars, Windows tool)
- Mercado Livre listings exist for both but could not be verified (blocked); clones are common there.

## Entry point
scripts/obd.py  - interactive console, starts in watch mode. Hotkeys: 1 watch, 2 errors, 3 record CSV on/off,
4 analyse latest recording, 5 Suzuki module scan, 6 clear DTC (asks y), q quit.
Other scripts: elm327_live.py (watch only), elm327_log.py (CSV logger), analyze_log.py, elm327_dtc.py, suzuki_modules.py.

## Tests
- Unit tests (no car, simulated adapter in tests/fake_elm.py):  .venv/bin/python -m pytest tests/ -q
- Hardware self-test (car + adapter, ~3 min):                   scripts/selftest.sh [PORT]
- Go port plan: docs/GO_PLAN.md

## vLinker FS arrived (2026-10-17)
Vgate vLinker FS USB: FTDI 0403:6015, /dev/serial/by-id/usb-www.vgatemall.com_vLinker_FS_*-port0 -> /dev/ttyUSB0.
Serial speed is 115200 (38400/9600 give garbage; the auto-baud in elm327_probe.py leaves junk in the
adapter's line buffer, so a CR flush is needed before the first command at 115200).
ATI = ELM327 v2.3, STI = STN1170 v4.3.2 (real STN chip, so ST commands, ATCRA, ATMA, K-line init should work).
Engine ECM via ISO 15765-4 CAN 11/500 works as before (0100 -> 41 00 BF 9F B9 93). Mode 09 still NO DATA.
First fuel reading at idle, warm (68 C coolant, 720 rpm): STFT ~0 % both banks, LTFT +7.8 % B1 / +10.9 % B2,
lambda 1.00, MAF 4.9 g/s, front O2 switching 0.07-0.77 V, rear O2 steady ~0.69 V, MIL off, 0 DTC.
Next: scripts/suzuki_modules.py / obd.py key 5 with --baud 115200 to try ABS/ESP, HVAC etc.

## Module scan with the vLinker FS (2026-10-17, logs/module_*_2026-10-17.txt)
Reachable: ENGINE, AT (7E1/7E9), ABS (261/661), BCM (271/671), HVAC (272/672), IPC (274/674) on CAN;
4WD (21) and SRS (59) only answer the K-line fast init (BUS INIT: OK) but return NO DATA to every KWP request.
No answer: PS, CCM, HVAC2, AC(09), SRSH, KLS, ALC, ICM, IMMO, TPMS, OCM, POWERTRAIN.
Services accepted by ABS/HVAC/IPC: 3E, 1A 8A (part number), 18 00 FF 00 (DTCs), 21 xx (local id). 10 xx, 13, 17, 19, 22 rejected.
Part numbers (1A 8A): ABS 56100-79K1*, HVAC 39520-79K0*, IPC 34110-79K1*, AT 5A 9A FF FF.
DTC status byte (SZ Viewer SuzukiDTCStatus): bits 6-5 = 0 no failure / 1 history / 2 pending / 3 current;
bit7 MIL, bit4 test not complete, low nibble symptom 1 high / 2 low / 4 no signal / 8 invalid.
DTCs found (all status 20 = history unless noted):
  ABS : C1018 brake fluid level switch, C1021 RF wheel speed sensor, C1025 LF wheel speed sensor,
        C1057 ESP module power supply, C1091 ECM data in CAN line (JB416 manual)
  HVAC: B1531 air intake actuator (history), B1504 sunload sensor (status 61 = CURRENT, symptom high; read at night)
  BCM : B1150 (unknown), IPC: B1810? (unknown), U0100 ECM, U0121 ABS, U0140 BCM, U0164 HVAC lost communication
HVAC live data: 21 08 (also 21 80 similar, 21 20/21/A1 static). Layout = SZ Viewer HVAC_CAN_272_39530_61M0_08,
offsets counted from the byte after "61 08" (first 7 bytes are the DTC list):
  [7] temperature switch: 0 = max cool, 2..36 -> (v-6)/2+17 C, 40 = max heat  (verified: user changed the dial)
  [8] mode switch 0 face/2 bi-level/4 foot/6 def-foot/8 def/32 auto   [9] fan switch   [10] intake 0 rec/2 fresh/32 auto
  [14] rpm*32  [15] speed km/h  [16] coolant-40  [22] outside (v/2-40)  [23] cabin (v/2-40)  [24] sun load (W/m2, x5)
  [25] evaporator (v/2-40)  [26] refrigerant pressure (v*14 kPa)  [33] air mix pos V (x0.02)  [39] mode pos V
  [45] fresh air pos V  [49] fan desired V (x0.1)  [60] bits: 0 A/C signal, 1 blower, 2 rear defog, 3 compressor clutch, 6 engine run request
SZ Viewer decoders: ~/opt/sz-viewer/lib/sz-viewer.jar, package com/malykh/szviewer/common/sdlmod/data/local/<module>/,
decompile with CFR (java -jar cfr.jar). ABS/IPC/BCM layouts not decoded yet.

## Engine actuator control attempt (2026-10-17, logs/engine_control_probe_2026-10-17.txt)
SZ Viewer request formats (Mode$ / control classes): IO control = 30 <id> 07 <mask> <value> 00 00, release = 30 <id> 00;
Suzuki K-line control = A5 <id> <4 bytes>, idle rpm = A5 00 80 00 <rpm*255/2000> 00 (K-line engines only, id "IAC rpm").
SZ Viewer has NO idle-rpm control for CAN engines; its CAN list is 30 01 fix advance, 02 fuel pump, 04 EVAP, 06 MIL,
07 radiator fans, 09 OCV/VVT, 0D wastegate/air bypass, 0E HP fuel pump, injectors 1-4.
On this V6 ECM (7E0): all 10 xx sessions -> 7F 10 12; all A5 variants -> 7F A5 12; 30 06 (MIL) -> 7F 30 12.
Enumeration of 30 <id> 00 for 00..FF: only id A0 is recognised (7F 30 22 conditions not correct); meaning unknown,
not actuated. Idle rpm cannot be commanded with any known request. Engine idled 620-665 rpm throughout.

## HVAC control attempt (2026-10-17, logs/hvac_control_probe_2026-10-17.txt)
SZ Viewer has no HVAC controls (ControlSupport covers engine, AT, BCM, ABS/ESP only).
HVAC 272 services: 30 and 3B exist (7F xx 12), 10/27/2E/2F/31/3D/A5/B0-BF -> 11 (unknown).
IO ids answering 30 <id> 00: 81 and A0 (70 xx 00). Only control parameter 00 and 07 recognised.
30 81 07 needs exactly 4 data bytes: byte0 must be 00 or 80, byte1 must be 00, bytes 2-3 free -> always 7F 30 22
(conditions not correct) with engine running, panel in AUTO. Same for A0. No request moved the setpoint byte.
Setpoint (21 08 data byte 7) is a panel input; no diagnostic write for it found. Not written via 3B (not attempted).

## HVAC IO control, full condition matrix (2026-10-17, logs/hvac_control_probe*, hvac_io_retry*, hvac_a0_retry*)
Requests: 30 81 07 b0 00 b2 b3  (b0: 00 ok, 80 -> 31 out of range, else 12; b1 must be 00; b2/b3 free, exactly 4 bytes)
          30 A0 08 b0 00 00 00  (b0 in 00/40/80/C0, other bytes must be 00, exactly 4 bytes; no value field)
Conditions tested -> response:
  engine running, panel AUTO : 81 -> 22, A0 -> 22
  engine running, panel OFF  : 81 -> 22, A0 -> 22
  key ON engine off, panel OFF: 81 -> 70 (accepted), A0 -> 22
  key ON engine off, panel ON : 81 -> 70 (accepted), A0 -> 22
  key ACC: nothing on the bus answers (only 3D0/3D1 keep-alive frames).
81 accepted but no byte of record 21 08 changed for any b2/b3 value; 81 is probably a display/relay self-test.
A0 looks like an actuator learn routine (selector, no value); its conditions were never met.
Conclusion: the HVAC setpoint (21 08 data byte 7) cannot be written with services 30; 3B writes were not attempted.
Panel display confirmed 23.5 C = byte 0x13 -> formula (v-6)/2+17 verified.

## HVAC 3B write probe (2026-10-17, logs/hvac_3b_probe_2026-10-17.txt, backup in logs/hvac_backup_2026-10-17.json)
3B 20 / 3B 21 with their own 11 bytes unchanged -> 7F 3B 22 (format accepted, conditions not correct; engine running).
3B A1 (30 bytes) -> 12. 3B 08 any length -> 12. 70-byte requests are refused by the adapter ("?").
Blind sweep 3B <lid> with 1..4 zero bytes for all other lids -> 12 everywhere. Nothing was written.
Records 20 and 21 never changed when the setpoint dial was moved, so they are not the setpoint (likely coding/calibration).
Final: the HVAC setpoint has no diagnostic write path on this module (39520-79K0).

## scripts/suzuki_modules.py changes (2026-10-17)
- Module.request() now reassembles ISO-TP multi-frame answers when headers are on (661 10 .. / 661 21 ..);
  before, ABS/HVAC/IPC identification, DTCs and records were silently dropped.
- Decoded live data for HVAC (21 08) and ABS/ESP (21 00): printed automatically by --module, refreshed by --watch.
  Tables HVAC_RECORD_08 / ABS_RECORD_00, decoders decode_hvac_08 / decode_abs_00, registry RECORDS.
- DTC status byte decoded (history/pending/current + high/low/no signal/invalid + MIL).
- Default baud order is now 115200 then 38400 (vLinker FS).
- Tests: test_hvac_record_decoder, test_abs_record_decoder, test_dtc_status_text, test_request_reassembles_headers_on_multiframe.
ABS record 21 00 layout (data after 61 00): [7..10] wheel speeds km/h FR FL RR RL, [11] long. G (v*0.02-2.56),
[12] battery (v*0.0784 V), [13] pump motor V (v*0.0804), [14] steering offset (v/2-64), [16] booster kPa (v/2),
[17] master cyl. MPa (v/10-2), [18] lateral G, [19-20] yaw rate (w*0.05-1638.4 deg/s), [22-23] rpm (w/4),
[24-25] steering angle signed deg, [39] bit1 hill hold active, [40] bit0 brake switch, [41] bits 1/2/3 ESP/TCS brake/TCS engine,
[42] bit3 ESP OFF switch, bit6 steering angle signal, [47] bit1 hill hold fitted, bit5 IPC comm abnormal.
SZ Viewer has ABS actuator tests only for K-line ABS (ABSESPControl kwpCases), none for CAN ABS.

## BCM and IPC records (2026-10-17, logs/bcm_ipc_data_2026-10-17.txt)
BCM part 36770-76K9, DTC B1150 history. Record 21 08 = SZ Viewer BCM_CAN_GV32_08_Local, decoded by decode_bcm_08:
[0] outside temp -40 (same sensor the HVAC reads), [1] battery (v*0.0784), [4] auto light sensor V, [20] speed, [21-22] rpm/4,
[39] b0 driver door b1 passenger door, [41] b0 auto light sw b1 fog sw, [44] b0/b1 door key lock/unlock b2/b3 lock/unlock sw,
[45] b7 key in, [46] b0 headlight b1 tail light, [47] b0 seatbelt fastened, [48] b0 brake fluid LOW b1 parking brake
b2 charge light b3 oil pressure sw b4 A/C sw, [49] b0 ignition b2 ACC, [50] b0 rear wiper ON b1 INT b7 washer, [57] b5 alarm.
Verified key-on/engine-off: ignition+ACC on, charge+oil lamps on, parking brake on, driver door open. Other LID: 21 C0 = 70 47 19 02.
IPC part 34110-79K1, record 21 01 is 30 bytes (SZ Viewer generic IPC_CAN_01_Local expects 39+): only [0] battery and
[21-22] rpm/4 verified; [23] changed 7A (engine warm, running) -> A5 (engine off), meaning unknown.
Other IPC LIDs: 21 02 = A1 F9 79 F6 1F 00 00 0A, 21 20/21 = same bytes as HVAC 21 21/20 (shared config block?),
21 22 = 27 10 00.., 21 23 = 00 01 04 00.., 21 A1 = 0A 04 08 08 14 14 3D 02 ... (HVAC A1 was 0A 04 08 08 14 08 3C FF).
BCM verification 2 (door closed, belt on, headlights on, parking brake released): all four bits flipped as decoded.
IPC [1] went E6 -> 4D when the headlights came on: panel illumination/dimmer level (raw). Odometer 202063 km
(0x3154F, x10 0x1ED516, BCD 20 20 63) is not present in any IPC record 21 01/02/20-23/A1.
