"""SAA2 left in "No ECAL"/"CW" mode: detection in the transport, worker and panel.

usage: test_ecal_mode.py <panel dir>
"""
import argparse
import os
import sys
import tempfile
import time

import numpy as np

PANEL = sys.argv[1]
sys.path.insert(0, PANEL)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_headless as H  # noqa: E402

H.install()
import saa2_device as sd  # noqa: E402
import vna_device as vd  # noqa: E402
import vna_panel as vp  # noqa: E402

ok_all = []


def check(name, cond, detail=""):
    ok_all.append(bool(cond))
    print("%s  %s  %s" % ("PASS" if cond else "FAIL", name, detail))


class Bench:
    """Port 1 open or with a 2.45 GHz Wi-Fi antenna (series RLC, Q = 6), seen through mild raw errors."""
    def __init__(self):
        self.dut = "open"

    def __call__(self, f):
        f = np.asarray(f, float)
        if self.dut == "antenna":
            w, w0 = 2 * np.pi * f, 2 * np.pi * 2.45e9
            L = 6 * 50 / w0
            C = 1 / (w0 ** 2 * L)
            z = 50 + 1j * w * L + 1 / (1j * w * C)
            g = (z - 50) / (z + 50)
        else:
            g = np.ones_like(f, complex)
        m11 = 0.04 + 0.92 * g * np.exp(-1j * f / 4e8) / (1 - 0.05 * g)
        return m11, 0.001 * np.ones_like(f, complex)


# 1. transport: stuck points counted only when the device is not in ECAL mode
b = Bench()
good = sd.SAA2Transport(ser=sd.SAA2Emulator(b))
bad = sd.SAA2Transport(ser=sd.SAA2Emulator(b, no_ecal_points=101))
good.scan(1e6, 4.4e9, 201)
bad.scan(1e6, 4.4e9, 201)
check("ECAL mode: no stuck points", good.stuck_points == 0 and not sd.ecal_problem(good.stuck_points, 201))
check("No ECAL mode: points beyond 101 stuck at S11 = 1", bad.stuck_points == 100 and sd.ecal_problem(bad.stuck_points, 201),
      "%d of 201" % bad.stuck_points)
check("Device Info flags it", "not in ECAL mode" in bad.command("info") and "(OK)" in good.command("info"))
f, s11, _ = bad.scan(1e6, 4.4e9, 201)
i24 = int(np.argmin(np.abs(f - 2.45e9)))
b.dut = "antenna"
f, s11b, _ = bad.scan(1e6, 4.4e9, 201)
check("reproduces the symptom: 2.45 GHz is point %d, unchanged by the antenna" % i24,
      i24 >= 101 and abs(s11b[i24] - s11[i24]) < 1e-6)
f, s11c, _ = good.scan(1e6, 4.4e9, 201)
b.dut = "open"
f, s11d, _ = good.scan(1e6, 4.4e9, 201)
check("   while in ECAL mode the antenna shows at 2.45 GHz", 20 * np.log10(abs(s11c[i24]) / abs(s11d[i24])) < -10,
      "%.1f dB" % (20 * np.log10(abs(s11c[i24]) / abs(s11d[i24]))))

# 2. worker: one status message, then quiet; stuck count exposed
for no_ecal, want in ((101, True), (None, False)):
    real = vd.SAA2Emulator
    vd.SAA2Emulator = lambda **k: real(no_ecal_points=no_ecal, **k)
    w = vd.VNAWorker(vd.SIM_SAA2_PORT)
    msgs, sw = [], []
    w.status.connect(msgs.append)
    w.sweep_ready.connect(lambda *x: sw.append(1))
    w.set_sweep(1e6, 4.4e9, 201)
    w.start()
    t0 = time.time()
    while len(sw) < 3 and time.time() - t0 < 20:
        H.pump()
        time.sleep(0.01)
    w.stop()
    w.wait(5000)
    H.pump()
    vd.SAA2Emulator = real
    hints = [m for m in msgs if m.startswith("SAA2:")]
    check("worker, %s: %s" % ("No ECAL" if no_ecal else "ECAL", "one warning" if want else "no warning"),
          (len(hints) == 1 and "CFG SWEEP > ECAL" in hints[0] and w.stuck_points == 100) if want
          else (not hints and w.stuck_points == 0), hints[:1])

# 3. panel: long flash + red status-bar tag
real = vd.SAA2Emulator
vd.SAA2Emulator = lambda **k: real(no_ecal_points=101, **k)
P = vp.VNAPanel(argparse.Namespace(sim=False, sim_saa2=False, sim_lite=False, port=None, saa2=False, litevna=False, screenshot=None))
flashes, status = [], []
P.flash = lambda text, ms=4000: flashes.append((text, ms))
P.st_left.setText = lambda t: status.append(t)
P.port_combo.currentData = lambda: vd.SIM_SAA2_PORT
P.connect_device()
t0 = time.time()
while not any(t.startswith("SAA2:") for t, _ in flashes) and time.time() - t0 < 20:
    H.pump()
    time.sleep(0.01)
H.pump()
check("panel: warning shown for 12 s", any(t.startswith("SAA2:") and ms == 12000 for t, ms in flashes))
check("panel: red status-bar tag", status and "not in ECAL mode" in status[-1], status[-1][-80:] if status else "")
P.disconnect_device()
vd.SAA2Emulator = real

print("\n%d/%d passed" % (sum(ok_all), len(ok_all)))
sys.exit(0 if all(ok_all) else 1)
