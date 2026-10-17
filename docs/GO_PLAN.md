# Plan: port the OBD tools to Go

Status: planned, not started (2026-09-19). The Python scripts under `scripts/` stay the reference
implementation and the source of truth for protocol details until the Go version reaches parity.

## Goal

One static Linux binary, `obd`, with the same features as `scripts/obd.py` and the standalone scripts:

- live watch with colours, bar gauges, sparklines, 1 or 3 columns by terminal width
- error codes: MIL, readiness, stored / pending / permanent, freeze frame, clear with confirmation
- CSV recording and the idle analysis report
- Suzuki module access (ABS/ESP, HVAC, BCM, ...) over CAN and K-line, using the SZ Viewer tables
- adapter check and discovery scans

Cross-compiled for amd64 and arm64 so it also runs on a Raspberry Pi or a small head unit.

## Why Go

- `go.bug.st/serial` gives portable serial ports with no cgo.
- Static binary of a few MB, no interpreter, no virtualenv, trivial `GOOS=linux GOARCH=arm64` builds.
- Goroutines separate the adapter polling loop from key handling and rendering cleanly.

## Module layout

```
cmd/obd/               main: flag parsing, mode dispatch (watch is the default)
internal/elm/          adapter transport: open, ATZ/init, raw command, timeouts, baud switch (ATBRD)
internal/obd/          mode 01 PID table + decoders, mode 03/07/0A/02 DTC parsing, readiness bits
internal/kwp/          KWP2000 helpers: 1A identifiers, 18 xx groups, 3E, ISO-TP segment reassembly
internal/suzuki/       module table (CAN ids, K-line addresses), connect logic, reply-id rules
internal/ui/           terminal rendering: colours, bars, sparklines, column layout, hotkeys
internal/logcsv/       CSV writer and reader
internal/analysis/     idle report (port of analyze_log.py)
internal/fake/         simulated adapter + ECM for tests (port of tests/fake_elm.py)
```

## Protocol details to carry over (all verified on the car)

Adapter init, in this order: `ATZ ATE0 ATL0 ATS0 ATH0 ATAL ATIB10 ATKW0 ATSW00 ATAT0 ATCAF1 ATCFC1 ATFCSM0`,
then `ATSP0`; first `0100` triggers the protocol search; `ATDP` reports it.

Engine ECM: CAN 11-bit 500 kbaud, request 7E0 (also answers 7E1, 700, 7DF), reply 7E8.
Supported PIDs: bitmaps from `0100/0120/0140/0160`. `3E` → `7E`. `1A 90` VIN, `1A 9A` part number.
Services 10, 12, 17, 18, 21, 22 are rejected by this ECM; codes come from OBD modes 03/07 only.

Multi-frame answers: keep `ATFCSM 0` (custom flow control breaks on clones). With headers off the
adapter prints `013` then `0: 13 5A 90 ...`, `1: ...`; join the segments, first byte is the length.
With headers on, each frame is printed separately and is not reassembled.

Suzuki modules (from SZ Viewer, see NOTES.md): CAN request ids 261 ABS, 262 4WD, 263 PS, 264 CCM,
271 BCM, 272 HVAC, 273 SRS, 274 IPC, 275 KLS, 276 ALC, 277 ICM, 7E0 engine, 7E1 AT.
Reply id = request + 0x400 for 0x200-0x2FF, request | 8 for 0x7E0+, request + 0x10 for the UDS
ranges. Connect: `ATSP6 ATSH <id> ATCRA <reply> ATFCSH <id> ATFCSD 30 00 00 ATFCSM 0`, then `3E`,
accept only when the reply comes from the expected id (the ECM answers other ids on 7E8).
K-line: `ATSP5 ATSH 81 <addr> F1 ATFI` (fast), `ATSP4 ATIIA <addr> ATSI` (5-baud). Addresses:
29 ABS, 09 AC, 99 HVAC, 98 HVAC-2, 5A BCM, 21 4WD, 59 SRS, 58 SRS-H, 61 meter, 5D immo, 31 PS,
5C TPMS, 5B OCM, 19 AT, 11 engine, 01 powertrain. Suzuki DTC read: `18 00 <group>` with groups
0000 4000 8000 C000 FF00 FFFF; clear: `14 <group>`.

Adapter quirks to handle: clones answer OK to filter commands but ignore them; ATFI/ATSI may return
`?` (then send the request and let auto-init happen); ATBRD needs the CR sent right after the
`ELM327 vX.X` hello at the new speed; STN/OBDLink adapters identify as `STN...` or `OBDLink`.

## Milestones

1. **Transport + watch** (parity with `elm327_live.py`): serial open, init, PID table with all
   decoders (numeric, status strings, O2 tuples), colour rules, bars, sparklines, 1/3 columns,
   `--wide` (default 150), `--history` (default 10). Fake adapter and decoder tests first.
2. **Errors**: DTC modes, readiness, freeze frame, description table, clear with `--yes`.
3. **Recording + analysis**: CSV with the same 25 columns and header names as the Python logger so old
   logs stay readable; port of the analysis rules and thresholds unchanged.
4. **Console**: hotkeys 1-6 and q on top of raw terminal mode (`golang.org/x/term`); keys polled
   between PID requests; result screens wait for a key. Consider `tcell` only if raw mode proves
   awkward.
5. **Suzuki modules**: CAN then K-line connect, identification, DTC groups, `21 xx` data dump.
   Needs the vLinker FS (ordered 2026-09-19) to validate ABS/ESP and HVAC on the car.
6. **Discovery tools**: probe, module scan, K-line sweep, baud switch.
7. **Packaging**: `make build` for amd64/arm64, `-ldflags "-s -w"`, version from git tag.

## Testing

- Port `tests/fake_elm.py` first; every milestone gets tests against it before touching the car.
- Keep `scripts/selftest.sh` as the hardware acceptance test and add the Go binary to it.
- Golden files: decode the recorded `logs/idle_5min.csv` with both implementations and diff the reports.

## Open points

- Whether to keep Python `analyze_log.py` as a plugin or port it (plan: port, it is small).
- HVAC/ABS live data identifiers (`21 xx`) are unknown until the vLinker arrives; capture the raw
  answers with the Python tool first, then add decoders to both implementations.
