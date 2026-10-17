#!/usr/bin/env python3
"""Analyse an idle log written by elm327_log.py and print an engine health report.

Usage: analyze_log.py logs/idle_5min.csv [--displacement 3.2] [--md REPORT.md]
"""
import argparse
import csv
import statistics as st
import sys


def col(rows, name):
    key = next((k for k in rows[0] if k.startswith(name)), None)
    if key is None:
        return []
    out = []
    for r in rows:
        try:
            out.append(float(r[key]))
        except (ValueError, TypeError):
            pass
    return out


def stats(v):
    if not v:
        return None
    return {"n": len(v), "mean": st.mean(v), "min": min(v), "max": max(v),
            "std": st.pstdev(v) if len(v) > 1 else 0.0, "first": v[0], "last": v[-1]}


def fmt(s, unit="", d=1):
    if not s:
        return "n/a"
    return f"mean {s['mean']:.{d}f}{unit}, min {s['min']:.{d}f}, max {s['max']:.{d}f}, sd {s['std']:.{d}f}"


def crossings(v, level=0.45):
    return sum(1 for a, b in zip(v, v[1:]) if (a - level) * (b - level) < 0)


def trend(v, n=5):
    if len(v) < 2 * n:
        return 0.0
    return st.mean(v[-n:]) - st.mean(v[:n])


def analyze(path, displacement=3.2):
    """Return the report text for a CSV written by elm327_log.py."""
    class A:      # mimics the argparse namespace used below
        pass
    a = A(); a.csv = path; a.displacement = displacement
    with open(a.csv, newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) < 3:
        return "not enough rows in the log yet"
    elapsed = col(rows, "elapsed_s")
    duration = elapsed[-1] if elapsed else 0
    period = duration / max(1, len(rows) - 1)

    rpm, load, aload = col(rows, "Engine RPM"), col(rows, "Engine load"), col(rows, "Absolute load")
    ect, iat, amb = col(rows, "Coolant temp"), col(rows, "Intake air temp"), col(rows, "Ambient air temp")
    maf, tps, rtps, cta = col(rows, "MAF"), col(rows, "Throttle position"), col(rows, "Relative throttle"), col(rows, "Cmd throttle")
    adv = col(rows, "Timing advance")
    stft1, ltft1, stft2, ltft2 = col(rows, "Short fuel trim B1"), col(rows, "Long fuel trim B1"), col(rows, "Short fuel trim B2"), col(rows, "Long fuel trim B2")
    o2_11, o2_12, o2_21, o2_22 = col(rows, "O2 B1S1"), col(rows, "O2 B1S2"), col(rows, "O2 B2S1"), col(rows, "O2 B2S2")
    lam, purge = col(rows, "Commanded equiv"), col(rows, "Commanded evap")
    cat1, cat2, volt = col(rows, "Catalyst temp B1S1"), col(rows, "Catalyst temp B2S1"), col(rows, "Control module voltage")

    lines, flags = [], []
    def out(s=""):
        lines.append(s)
    def flag(level, text):
        flags.append((level, text))

    out(f"# Idle analysis of {a.csv}")
    out(f"{len(rows)} samples over {duration:.0f} s, one full pass every {period:.1f} s")
    out()

    # --- RPM ---------------------------------------------------------------
    s = stats(rpm)
    out("## Engine speed")
    out(f"RPM: {fmt(s, ' rpm', 0)}")
    if s:
        if s["std"] > 40 or s["max"] - s["min"] > 150:
            flag("warn", f"idle unstable: RPM spread {s['max'] - s['min']:.0f} rpm, sd {s['std']:.0f}")
        else:
            out("Idle is steady.")
        if s["mean"] > 900:
            flag("info", f"idle speed high at {s['mean']:.0f} rpm (fast idle, A/C load or cold engine)")
    out()

    # --- temperatures ------------------------------------------------------
    out("## Temperatures")
    se, si, sa = stats(ect), stats(iat), stats(amb)
    out(f"Coolant: {fmt(se, ' °C', 0)}, change over log {trend(ect):+.0f} °C")
    out(f"Intake air: {fmt(si, ' °C', 0)}")
    out(f"Ambient: {fmt(sa, ' °C', 0)}")
    if se:
        if se["max"] > 108:
            flag("warn", f"coolant reached {se['max']:.0f} °C, above the normal idle band 85-105")
        elif se["min"] < 80:
            flag("info", "engine not fully warm for part of the log")
        else:
            out("Coolant stays in the normal warm band, thermostat and fan are regulating.")
        if trend(ect) > 6:
            flag("info", f"coolant still climbing ({trend(ect):+.0f} °C), watch the fan cut-in")
    if si and sa and si["mean"] - sa["mean"] > 35:
        flag("info", f"intake air {si['mean'] - sa['mean']:.0f} °C above ambient: heat soak at idle, normal when stationary")
    out()

    # --- air and load ------------------------------------------------------
    out("## Air flow and load")
    sm, sl, sal = stats(maf), stats(load), stats(aload)
    out(f"MAF: {fmt(sm, ' g/s', 2)}")
    out(f"Calculated load: {fmt(sl, ' %', 1)}, absolute load: {fmt(sal, ' %', 1)}")
    if sm and s:
        g_per_rev = sm["mean"] / (s["mean"] / 60)
        # theoretical air per rev for a 4-stroke at 1.2 g/L and 100 % VE
        theo = a.displacement / 2 * 1.2
        ve = g_per_rev / theo * 100
        out(f"Air per revolution {g_per_rev:.3f} g, about {ve:.0f} % volumetric efficiency for {a.displacement:.1f} L (15-30 % is normal at closed-throttle idle)")
        if ve > 35:
            flag("warn", "air flow high for idle: possible vacuum leak, high idle or MAF over-reading")
        if ve < 10:
            flag("warn", "air flow low for idle: possible MAF under-reading or restricted intake")
    st_tps, st_r, st_c = stats(tps), stats(rtps), stats(cta)
    out(f"Throttle: absolute {fmt(st_tps, ' %', 1)}; relative {fmt(st_r, ' %', 1)}; commanded actuator {fmt(st_c, ' %', 1)}")
    out()

    # --- fuelling ----------------------------------------------------------
    out("## Fuel trims and mixture")
    for bank, s_st, s_lt in ((1, stats(stft1), stats(ltft1)), (2, stats(stft2), stats(ltft2))):
        if s_st and s_lt:
            total = s_st["mean"] + s_lt["mean"]
            out(f"Bank {bank}: STFT {fmt(s_st, ' %')}; LTFT {fmt(s_lt, ' %')}; total correction {total:+.1f} %")
            if abs(total) > 20:
                flag("warn", f"bank {bank} total fuel trim {total:+.1f} % is outside ±20 %")
            elif abs(total) > 10:
                flag("info", f"bank {bank} total fuel trim {total:+.1f} %: ECU adds fuel, mild lean tendency (small vacuum leak, low fuel pressure, dirty MAF or injectors)")
    if ltft1 and ltft2:
        diff = st.mean(ltft1) - st.mean(ltft2)
        if abs(diff) > 6:
            flag("info", f"long-term trims differ by {abs(diff):.1f} % between banks: bank-specific cause (injector, leak, O2 sensor)")
    slam = stats(lam)
    out(f"Commanded lambda: {fmt(slam, '', 3)}")
    if slam and abs(slam["mean"] - 1.0) > 0.03:
        flag("info", f"commanded lambda averages {slam['mean']:.3f}, not stoichiometric")
    sp = stats(purge)
    out(f"Evap purge duty: {fmt(sp, ' %')}")
    out()

    # --- oxygen sensors ----------------------------------------------------
    out("## Oxygen sensors")
    minutes = max(duration / 60, 1e-6)
    for name, v in (("B1S1 (upstream)", o2_11), ("B2S1 (upstream)", o2_21)):
        sv = stats(v)
        if sv:
            cr = crossings(v) / minutes
            out(f"{name}: {fmt(sv, ' V', 3)}, {cr:.1f} rich/lean crossings per minute at this sample rate")
            if sv["max"] - sv["min"] < 0.3:
                flag("warn", f"{name} barely switches (range {sv['max'] - sv['min']:.2f} V): lazy or stuck sensor, or open loop")
            if sv["mean"] < 0.3:
                flag("info", f"{name} averages lean ({sv['mean']:.2f} V)")
            if sv["mean"] > 0.6:
                flag("info", f"{name} averages rich ({sv['mean']:.2f} V)")
    for name, v in (("B1S2 (downstream)", o2_12), ("B2S2 (downstream)", o2_22)):
        sv = stats(v)
        if sv:
            out(f"{name}: {fmt(sv, ' V', 3)}")
            if sv["std"] > 0.15 or sv["max"] - sv["min"] > 0.5:
                flag("warn", f"{name} swings like the upstream sensor: catalyst may have low oxygen storage")
            elif 0.45 <= sv["mean"] <= 0.85:
                out(f"  {name} is steady and rich-biased: catalyst is storing oxygen as expected.")
    out(f"Note: with one sample every {period:.1f} s the upstream switching rate is under-sampled; a healthy sensor switches several times per second.")
    out()

    # --- ignition, catalyst, electrical ----------------------------------
    out("## Ignition, catalyst, electrical")
    sadv = stats(adv)
    out(f"Timing advance: {fmt(sadv, ' °', 1)}")
    if sadv and sadv["std"] > 3:
        flag("info", "timing advance moving a lot at idle: ECU is actively correcting idle speed")
    sc1, sc2 = stats(cat1), stats(cat2)
    out(f"Catalyst B1S1: {fmt(sc1, ' °C', 0)}; B2S1: {fmt(sc2, ' °C', 0)}")
    if sc1 and sc2 and abs(sc1["mean"] - sc2["mean"]) > 60:
        flag("info", f"catalyst temperatures differ by {abs(sc1['mean'] - sc2['mean']):.0f} °C between banks")
    if (sc1 and sc1["max"] > 800) or (sc2 and sc2["max"] > 800):
        flag("warn", "catalyst above 800 °C at idle: misfire or rich running")
    svv = stats(volt)
    out(f"Module voltage: {fmt(svv, ' V', 2)}")
    if svv:
        if svv["min"] < 13.0:
            flag("warn", f"voltage dipped to {svv['min']:.2f} V: charging weak or heavy load")
        elif svv["max"] > 15.0:
            flag("warn", f"voltage peaked at {svv['max']:.2f} V: regulator high")
        else:
            out("Charging system is within 13.0-15.0 V.")
    out()

    # --- verdict -----------------------------------------------------------
    out("## Findings")
    warns = [t for l, t in flags if l == "warn"]
    infos = [t for l, t in flags if l == "info"]
    if not warns:
        out("No warning-level findings. The engine idles like a healthy, warm, closed-loop engine.")
    for t in warns:
        out(f"- WARNING: {t}")
    for t in infos:
        out(f"- note: {t}")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv")
    ap.add_argument("--displacement", type=float, default=3.2, help="engine size in litres")
    ap.add_argument("--md", help="also write the report to this markdown file")
    a = ap.parse_args()
    text = analyze(a.csv, a.displacement)
    print(text)
    if a.md:
        with open(a.md, "w") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
