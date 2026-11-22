"""The REAL vna_panel.py, headless, driving a device through the real worker + transport.

usage: test_panel.py <panel dir> saa2|lite|fv3|h4
  h4: NanoVNA-H4 chosen in the Model list, on the text-shell simulator
  saa2: SAA2 byte-level emulator    lite: LiteVNA 64 emulator (same binary protocol)    fv3: NanoVNA-F V3 shell simulator
Every softkey of every menu is pressed (every option of every choice), plus the hard keys, and the
device-related functions are checked one by one against what the device received.
"""
import argparse
import os
import sys
import tempfile
import time
import traceback

import numpy as np

PANEL, MODE = sys.argv[1], sys.argv[2]
sys.path.insert(0, PANEL)
sys.path.insert(0, os.path.join(PANEL, "tests"))
import qt_headless as H  # noqa: E402

H.install()
import saa2_device as sd  # noqa: E402
import litevna64_device as lv  # noqa: E402
import vna_device as vd  # noqa: E402
import vna_panel as vp  # noqa: E402

ok_all = []


def check(name, cond, detail=""):
    ok_all.append(bool(cond))
    print("%s  %s  %s" % ("PASS" if cond else "FAIL", name, detail))


# ---- capture the SAA2 emulator the worker creates; log every complete sweep configuration
emus = []


class LogMixin:
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.applied = []

    def _config_written(self, old):
        super()._config_written(old)
        if self.log[-1] == (sd.CMD_WRITE2, sd.REG_VPF):
            self.applied.append(self.sweep())


class LoggingSAA2(LogMixin, sd.SAA2Emulator):
    pass


class LoggingLite(LogMixin, lv.LiteVNA64Emulator):
    pass


def maker(cls):
    def make(**k):
        emus.append(cls(**k))
        return emus[-1]
    return make


vd.SAA2Emulator = maker(LoggingSAA2)
vd.LiteVNA64Emulator = maker(LoggingLite)
V2 = MODE in ("saa2", "lite")                         # binary-protocol instruments
PORT = {"saa2": vd.SIM_SAA2_PORT, "lite": vd.SIM_LITE_PORT, "fv3": vd.SIM_PORT, "h4": vd.SIM_PORT}[MODE]

# ---- the panel, instrumented
P = vp.VNAPanel(argparse.Namespace(sim=False, sim_saa2=False, sim_lite=False, port=None, saa2=False, litevna=False, screenshot=None))
flashes, dialogs, sent, captures, brand, titles = [], [], [], [], [], []
P.flash = lambda text, ms=4000: flashes.append(text)
P.text_dialog = lambda title, text: dialogs.append((title, text))
_send = P.send
P.send = lambda cmd, cb=None: (sent.append(cmd), _send(cmd, cb))[1]
_show = P.show_capture
P.show_capture = lambda res: (captures.append(res), _show(res))[1]
P.model_lbl.setText = lambda t: brand.append(("model", t))
P.brand_lbl.setText = lambda t: brand.append(("name", t))
P.logo_lbl.setText = lambda t: brand.append(("logo", t))
P.range_lbl.setText = lambda t: brand.append(("range", t))
P.setWindowTitle = lambda t: titles.append(t)
P.port_combo.currentData = lambda: PORT
tmp = tempfile.mkdtemp()


def run(seconds=0.0, until=None, timeout=30.0):
    t0 = time.time()
    while True:
        H.pump()
        if until is not None and until():
            return True
        if until is None and time.time() - t0 >= seconds:
            return True
        if time.time() - t0 > timeout:
            return False
        time.sleep(0.005)


def sweeps(n):
    target = P.sweeps + n
    return run(until=lambda: P.sweeps >= target)


def emu():
    return emus[-1]


# ================================================================== name: startup, port chosen, connected
check("startup: generic name only", P.brand is None and P.device_title() == "NanoVNA Front Panel"
      and vp.APP_NAME == "NanoVNA Front Panel")
if MODE == "h4":
    P.set_model("NanoVNA-H4")                          # Model list (upstream PR #2)
    check("Model list: NanoVNA-H4 -> name, 50 kHz - 1.5 GHz, preset 1 MHz - 1.5 GHz",
          P.brand == "NanoVNA-H4" and (P.fmin, P.fmax, P.start, P.stop) == (50e3, 1.5e9, 1e6, 1.5e9)
          and titles[-1] == "NanoVNA-H4 Front Panel" and brand[-2:] == [("model", "-H4"), ("range", "50kHz-1.5GHz")],
          "%s | %s" % (brand[-2:], titles[-1]))
else:
    P.preview_brand()                                  # what choosing the port in the list does (Model = Auto)
    want_brand = {"saa2": "NanoVNA SAA2", "lite": "LiteVNA 64", "fv3": "NanoVNA-F V3"}[MODE]
    check("port chosen in the list: name of that instrument", P.brand == want_brand
          and titles[-1] == {"saa2": "NanoVNA SAA2 Front Panel", "lite": "LiteVNA 64 Front Panel",
                             "fv3": "NanoVNA-F V3 Front Panel"}[MODE],
          "%s | %s" % (brand[-2:], titles[-1]))

# ================================================================== connect
P.connect_device()
run(until=lambda: getattr(P, "version", None) and P.sweeps >= 1)
if MODE == "saa2":
    check("connect: identified as SAA2", P.version.startswith("NanoVNA SAA2 (V2_2)") and P.caps["kind"] == "saa2", P.version)
    check("connect: correction switched to Off (raw USB data)", P.correction == "Off" and any("raw USB data" in f for f in flashes))
    check("connect: range 50 kHz - 4.4 GHz, one 1024-pt segment", (P.fmin, P.fmax, P.chunk) == (50e3, 4.4e9, 1024))
    check("connect: brand shows SAA2 and its range", brand[-2:] == [("model", " SAA2"), ("range", "50kHz-4.4GHz")]
          and titles[-1] == "NanoVNA SAA2 Front Panel - V2_2, FW 1.3", "%s | %s" % (brand[-2:], titles[-1]))
elif MODE == "lite":
    check("connect: identified as LiteVNA 64", P.version == "LiteVNA 64  FW 2.2" and P.caps["kind"] == "litevna", P.version)
    check("connect: correction switched to Off (raw USB data)", P.correction == "Off" and any("raw USB data" in f for f in flashes))
    check("connect: range 50 kHz - 6.3 GHz, one 1024-pt segment", (P.fmin, P.fmax, P.chunk) == (50e3, 6.3e9, 1024))
    check("connect: logo LiteVNA 64, range box, title", brand[-4:] == [("name", "LiteVNA"), ("logo", "LiteVNA"),
          ("model", " 64"), ("range", "50kHz-6.3GHz")] and titles[-1] == "LiteVNA 64 Front Panel - FW 2.2",
          "%s | %s" % (brand[-4:], titles[-1]))
elif MODE == "h4":
    check("connect: text-shell NanoVNA, Model list choice kept", P.caps["kind"] == "sim" and P.brand == "NanoVNA-H4"
          and P.worker.screen == (480, 320), P.version)
    check("connect: correction stays Device", P.correction == "Device" and "cal on" in sent)
    check("connect: range 50 kHz - 1.5 GHz, 201-pt segments", (P.fmin, P.fmax, P.chunk) == (50e3, 1.5e9, 201))
    check("connect: NanoVNA-H4 brand and title", brand[-2:] == [("model", "-H4"), ("range", "50kHz-1.5GHz")]
          and titles[-1] == "NanoVNA-H4 Front Panel", titles[-1])
else:
    check("connect: identified as F-V3 shell (Auto, from the version reply)", P.version.startswith("NanoVNA-F V3")
          and P.caps["kind"] == "sim" and P.brand == "NanoVNA-F V3" and P.model == "Auto", P.version)
    check("connect: correction stays Device", P.correction == "Device" and "cal on" in sent)
    check("connect: range 1 MHz - 6 GHz, 201-pt segments", (P.fmin, P.fmax, P.chunk) == (1e6, 6e9, 201))
    check("connect: NanoVNA-F V3 brand and title", brand[-2:] == [("model", "-F V3"), ("range", "1MHz-6GHz")]
          and titles[-1] == "NanoVNA-F V3 Front Panel", titles[-1])

# ================================================================== Model list while connected
flashes.clear()
if V2:
    shown = P.brand
    P.set_model("NanoVNA-H4")
    check("Model list: a SAA2 / LiteVNA cannot be renamed H4", P.brand == shown and P.fmax == P.caps["fmax"]
          and "identified itself" in flashes[-1], flashes[-1:])
elif MODE == "fv3":
    P.set_model("NanoVNA-H4")
    sweeps(2)
    h4 = (P.brand, P.fmax, P.stop, P.worker.screen) == ("NanoVNA-H4", 1.5e9, 1.5e9, (480, 320)) and P.freqs[-1] == 1.5e9
    P.set_model("NanoVNA SAA2")
    refused = P.brand == "NanoVNA-H4" and "detected by themselves" in flashes[-1]
    P.set_model("NanoVNA-F V3")
    back = (P.brand, P.fmax, P.worker.screen) == ("NanoVNA-F V3", 6e9, (800, 480))
    P.set_model("Auto")
    auto = P.brand == "NanoVNA-F V3" and P.model == "Auto"
    sweeps(1)
    check("Model list on a text NanoVNA: H4 / SAA2 refused / F-V3 / Auto", h4 and refused and back and auto,
          "h4=%s refused=%s back=%s auto=%s" % (h4, refused, back, auto))

# ================================================================== frequency through the panel controls
P.params["start"].set(100e6)
P.params["stop"].set(P.fmax)
sweeps(2)
check("Start/Stop softkeys -> sweep", abs(P.freqs[0] - 100e6) < 1 and abs(P.freqs[-1] - P.fmax) < 1,
      "%.3f MHz .. %.3f GHz" % (P.freqs[0] / 1e6, P.freqs[-1] / 1e9))
if V2:
    s, st, n = emu().applied[-1]
    check("   device registers = start %.0f Hz, step %.0f Hz, %d points" % (s, st, n),
          s == 100e6 and n == 201 and abs(s + st * (n - 1) - P.fmax) <= 200)
P.params["stop"].set(9e9)
check("Stop above the device limit is clamped", P.stop == P.fmax, "%.2f GHz" % (P.stop / 1e9))
P.open_menu("FREQ")
P.softkey(4)                                           # Full Span
sweeps(2)
check("Full Span", (P.start, P.stop) == (P.fmin, P.fmax) and abs(P.freqs[-1] - P.fmax) < 1)
if V2:
    s, st, n = emu().applied[-1]
    check("   device registers for Full Span", s == 50e3 and abs(s + st * (n - 1) - P.fmax) <= 200, (s, st, n))
P.params["span"].set(200e6)                            # span first: a centre on a full span gets clamped
P.params["center"].set(1e9)
sweeps(2)
check("Span/Center", abs(P.freqs[0] - 900e6) < 1 and abs(P.freqs[-1] - 1.1e9) < 1)

# ================================================================== points / segments
P.set_points(801)
sweeps(2)
n_cfg = len(emu().applied) if V2 else 0
n_sw = P.sweeps
sweeps(3)
if MODE == "saa2":
    check("801 pts: one segment, registers not rewritten between sweeps",
          len(P.freqs) == 801 and len(emu().applied) == n_cfg and emu().applied[-1][2] == 801,
          "%d config writes during 3 sweeps" % (len(emu().applied) - n_cfg))
elif MODE == "lite":
    writes = len(emu().applied) - n_cfg
    check("801 pts: one segment, registers rewritten every sweep (LiteVNA forgets them)",
          len(P.freqs) == 801 and emu().applied[-1][2] == 801 and P.sweeps - n_sw - 1 <= writes <= P.sweeps - n_sw + 1,
          "%d config writes during %d sweeps" % (writes, P.sweeps - n_sw))
if V2:
    P.set_points(1601)
    sweeps(2)
    check("1601 pts: 1024 + 577 segments", len(P.freqs) == 1601 and sorted(c[2] for c in emu().applied[-2:]) == [577, 1024],
          [c[2] for c in emu().applied[-2:]])
else:
    check("801 pts on F-V3: 201-pt segments", len(P.freqs) == 801 and P.chunk == 201)
P.set_points(201)
sweeps(2)

# ================================================================== calibration through the Cal menu
P.cal_avg = 2
P.set_range(100e6, 3e9)
sweeps(1)
for std in ("open", "short", "load", "thru"):
    P.measure_std(std)
    run(until=lambda: P.acq is None)
P.apply_cal()
residual = {}
for dut, want11, want21 in (("load", 0, None), ("open", 1, None), ("short", -1, None), ("thru", None, 1)):
    P.set_sim_dut(dut)
    sweeps(2)
    d = np.abs(P.s["S11"] - want11).max() if want11 is not None else np.abs(P.s["S21"] - want21).max()
    residual[dut] = d
check("Cal SOLT via the panel, then Correction = App", P.correction == "App" and P.cal.kind() == "SOLT")
check("   corrected standards: |error| < 0.01", max(residual.values()) < 0.01,
      " ".join("%s %.1e" % kv for kv in residual.items()))
P.set_sim_dut("filter")
sweeps(2)
fi = np.argmin(np.abs(P.freqs - 1e9))
ideal = vd.SimTransport()._dut(P.freqs[fi:fi + 1])[1][0]
meas_db, ideal_db = 20 * np.log10(abs(P.s["S21"][fi])), 20 * np.log10(abs(ideal))
check("   filter at 1 GHz after cal = simulated DUT", abs(meas_db - ideal_db) < 0.1,
      "measured %.2f dB, DUT %.2f dB" % (meas_db, ideal_db))
H.DIALOG["save"] = os.path.join(tmp, "cal.npz")
P.save_cal()
P.clear_cal()
H.DIALOG["open"] = H.DIALOG["save"]
P.load_cal()
sweeps(2)
check("Save Cal / Clear / Load Cal", P.correction == "App" and P.cal.kind() == "SOLT" and os.path.exists(H.DIALOG["save"]))

# ================================================================== device-specific functions
flashes.clear()
P.set_correction("Device")
if V2:
    check("Correction = Device refused (no device cal over USB)", P.correction == "App" and "raw data" in flashes[-1])
else:
    check("Correction = Device accepted", P.correction == "Device" and sent[-1] == "cal on")
    P.set_correction("App")

n_sent = len(sent)
H.DIALOG["int"] = 2
P.device_recall()
P.device_save()
run(0.2)
if V2:
    check("Dev Recall/Save: refused, nothing sent", not any(c.split()[0] in ("save", "recall") for c in sent[n_sent:])
          and "save/recall" in flashes[-1])
else:
    check("Dev Recall/Save: 'recall 2' / 'save 2' sent", sent[n_sent:] == ["recall 2", "save 2"])
    P.set_correction("App")

if MODE == "saa2":
    lvl0 = emu().regs[sd.REG_TXPOWER]
    P.cycle_power()
    run(until=lambda: emu().regs[sd.REG_TXPOWER] != lvl0, timeout=5)
    check("Power softkey -> ADF4350 register 0x42", (lvl0, emu().regs[sd.REG_TXPOWER]) == (3, 2) and P.power_label() == "-3 dB")
    for _ in range(3):
        P.cycle_power()
    run(until=lambda: emu().regs[sd.REG_TXPOWER] == 3, timeout=5)
    check("   4 presses: back to max", emu().regs[sd.REG_TXPOWER] == 3 and P.power_label() == "max")
else:
    flashes.clear()
    P.cycle_power()
    check("Power softkey: fixed on this instrument, nothing sent", "fixed" in flashes[-1] and P.power_label() == "fixed"
          and not any(c.startswith("power") for c in sent))

P.device_capture()
run(until=lambda: captures, timeout=10)
want = {"saa2": (320, 240), "lite": (480, 320), "fv3": (800, 480), "h4": (480, 320)}[MODE]
check("Dev Screen: capture received and decoded", captures and captures[-1][:2] == want and len(captures[-1][2]) == want[0] * want[1] * 2,
      captures[-1][:2] if captures else None)

P.device_info()
run(until=lambda: dialogs and dialogs[-1][0] == "Device info", timeout=10)
info = dialogs[-1][1] if dialogs else ""
check("Device Info", {"saa2": "NanoVNA SAA2 (V2_2)" in info and "Board revision 2" in info,
                      "lite": "LiteVNA 64  FW 2.2" in info and "Battery: 3.95 V" in info,
                      "fv3": "NanoVNA-F V3" in info, "h4": "NanoVNA-F V3" in info}[MODE],
      info.splitlines()[0] if info else "")

H.DIALOG["text"] = "help"
P.console()
run(until=lambda: dialogs and dialogs[-1][0] == "> help", timeout=10)
check("Console", dialogs[-1][0] == "> help" and ("binary protocol" in dialogs[-1][1] if V2 else True))

# ================================================================== exports
sweeps(2)
for ports in (1, 2):
    fn = os.path.join(tmp, "x.s%dp" % ports)
    H.DIALOG["save"] = fn
    P.save_touchstone(ports)
    lines = open(fn).read().splitlines()
    rows = np.array([[float(x) for x in l.split()] for l in lines if l and l[0] not in "!#"])
    head = lines[0]
    good = (len(rows) == len(P.freqs) and np.allclose(rows[:, 0], P.freqs) and np.allclose(rows[:, 1], P.s["S11"].real, atol=1e-8))
    want_head = {"saa2": "! NanoVNA SAA2 Front Panel - V2_2", "lite": "! LiteVNA 64 Front Panel - FW 2.2",
                 "fv3": "! NanoVNA-F V3 Front Panel  ", "h4": "! NanoVNA-H4 Front Panel  "}[MODE]
    check("Save S%dP: data + header" % ports, good and head.startswith(want_head), head[:60])
H.DIALOG["save"] = os.path.join(tmp, "x.csv")
P.save_csv()
csv = np.loadtxt(H.DIALOG["save"], delimiter=",", skiprows=1)
check("Save CSV", csv.shape[0] == len(P.freqs) and np.allclose(csv[:, 0], P.freqs))
H.DIALOG["open"] = os.path.join(tmp, "x.s1p")
P.select_trace(0)
P.load_touchstone_mem()
check("Load S1P -> memory", P.traces[0].mem is not None and len(P.traces[0].mem[0]) == len(P.freqs))

# ================================================================== trigger
P.set_running(False)
settle = 2 * P.swt + 0.3                             # let the sweep in progress finish
run(settle)
n0 = P.sweeps
run(settle)
held = P.sweeps == n0
P.single_sweep()
run(until=lambda: P.sweeps == n0 + 1, timeout=10)
run(settle)
single = P.sweeps == n0 + 1
P.set_running(True)
resumed = sweeps(2)
check("Hold / Single / Continuous", held and single and resumed,
      "sweep time %.2f s, held=%s single=%s resumed=%s" % (P.swt, held, single, resumed))

# ================================================================== every softkey of every menu + hard keys
errors = []
skip = {"Connect"}
pressed = 0
for name in list(P.menus):
    items = P.menus[name][1]
    for idx, it in enumerate(items):
        if it.label in skip:
            continue
        try:
            P.open_menu(name)
            P.menu_page = idx // vp.SLOTS
            if it.kind == "param":
                P.softkey(idx % vp.SLOTS)
                p = P.params[it.param]
                P.knob_turned(1)
                P.knob_turned(-1)
                P.arrow(True)
                P.arrow(False)
                P.entry = repr(float(p.get()))
                P.apply_entry(p.units[-1][1])
                P.active = None
                P.show_active()
            elif it.kind == "toggle":
                n_opts = len(it.value()[0]) if it.value else 2
                for _ in range(n_opts):
                    P.softkey(idx % vp.SLOTS)
            else:
                P.softkey(idx % vp.SLOTS)
            pressed += 1
            run(0.03)
        except Exception:
            errors.append("%s / %s:\n%s" % (name, it.label, traceback.format_exc()))
for fn in (P.preset, P.next_trace, P.cycle_layout, P.auto_scale_all, P.toggle_hold, P.toggle_hold,
           P.menu_next_page, P.menu_return, P.key_back, P.key_esc, lambda: P.knob_turned(1),
           lambda: P.set_maximize(True), lambda: P.set_maximize(False), lambda: P.panel_clicked(1, None)):
    try:
        fn()
        pressed += 1
        run(0.03)
    except Exception:
        errors.append("hard key %s:\n%s" % (getattr(fn, "__name__", fn), traceback.format_exc()))
P.set_running(True)
alive = sweeps(2)
check("every softkey / option pressed without error (%d presses)" % pressed, not errors, "%d errors" % len(errors))
for e in errors[:5]:
    print(e)
check("link still up and sweeping afterwards", alive and P.worker is not None)

# ================================================================== disconnect / reconnect
P.disconnect_device()
run(0.3)
if V2:
    check("Disconnect: device left USB mode (dataMode = 2)", P.worker is None and emu().usb_mode is False)
else:
    check("Disconnect", P.worker is None)
P.connect_device()
check("Reconnect", run(until=lambda: P.worker is not None and P.sweeps > 0 and P.freqs is not None, timeout=20) and sweeps(1))
P.disconnect_device()

print("\n%d/%d passed" % (sum(ok_all), len(ok_all)))
sys.exit(0 if all(ok_all) else 1)
