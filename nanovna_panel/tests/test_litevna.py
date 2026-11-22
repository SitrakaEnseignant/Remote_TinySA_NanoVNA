"""LiteVNA 64 (litevna64_device.py, on the SAA2 protocol layer) - LiteVNA User Guide appendix + NanoVNA-Saver.

usage: test_litevna.py <panel dir>
"""
import os
import sys
import time

import numpy as np

PANEL = sys.argv[1]
sys.path.insert(0, PANEL)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_saa2_stub import install_qt_stub  # noqa: E402

install_qt_stub()
import saa2_device as sd  # noqa: E402
import litevna64_device as lv  # noqa: E402
import vna_device as vd  # noqa: E402

ok_all = []


def check(name, cond, detail=""):
    ok_all.append(bool(cond))
    print("%s  %s  %s" % ("PASS" if cond else "FAIL", name, detail))


def model(f):
    f = np.asarray(f, float)
    return 0.4 * np.exp(1j * f / 3e7), 0.6 * np.exp(-1j * f / 5e7)


def err(out):
    f, a, b = out
    m11, m21 = model(f)
    return max(np.max(np.abs(a - m11)), np.max(np.abs(b - m21)))


def config_writes(emu):
    return sum(1 for x in emu.log if x == (sd.CMD_WRITE8, sd.REG_START))


# 0. module split: LiteVNA code lives in litevna64_device.py only
src = open(os.path.join(PANEL, "saa2_device.py")).read()
check("saa2_device.py has no LiteVNA settings (range, battery, profile)",
      not any(x in src for x in ("6.3e9", "0x5C", "profile", '"litevna"')) and not hasattr(sd, "LITE_FMAX"))
check("LiteVNA64Transport / LiteVNA64Emulator subclass the SAA2 classes",
      issubclass(lv.LiteVNA64Transport, sd.SAA2Transport) and issubclass(lv.LiteVNA64Emulator, sd.SAA2Emulator))

# 1. identification (vna_device.open_v2 reads the identity registers, then picks the class)
emu = lv.LiteVNA64Emulator(model)
tr = vd.open_v2(ser=emu)
c = tr.caps
check("firmware 2.2 on variant 2 -> LiteVNA64Transport", type(tr) is lv.LiteVNA64Transport and c["kind"] == "litevna"
      and tr.version_text() == "LiteVNA 64  FW 2.2", tr.version_text())
check("capabilities: 50 kHz - 6.3 GHz, 1024-pt segments, capture, no device cal / slots / power",
      (c["fmin"], c["fmax"], c["max_points"]) == (50e3, 6.3e9, 1024) and c["capture"]
      and not (c["device_cal"] or c["slots"] or c["power"]) and c["detail"] == "FW 2.2")
saa2 = vd.open_v2(ser=sd.SAA2Emulator(model, fw=(1, 3)))
check("SAA2 firmware 1.3 -> SAA2Transport", type(saa2) is sd.SAA2Transport and saa2.caps["fmax"] == 4.4e9)
forced = vd.open_v2(ser=sd.SAA2Emulator(model, fw=(1, 3)), model="litevna")
check("--litevna forces LiteVNA64Transport", type(forced) is lv.LiteVNA64Transport and forced.caps["fmax"] == 6.3e9)

# 2. frequency control up to 6.3 GHz
out = tr.scan(5e9, 6.3e9, 201)
s, st, n = emu.sweep()
check("5 - 6.3 GHz: registers and data", s == 5e9 and n == 201 and abs(s + st * 200 - 6.3e9) <= 100 and err(out) < 1e-6,
      "start %.0f step %.0f points %d, err %.1e" % (s, st, n, err(out)))
out = tr.scan(50e3, 6.3e9, 1024)
check("Full span 1024 points", err(out) < 1e-6 and abs(out[0][-1] - 6.3e9) <= 1024)
try:
    tr.scan(1e6, 6.3e9, 1025)
    check("1025 points -> PointsError (panel splits)", False)
except sd.PointsError:
    check("1025 points -> PointsError (panel splits)", True)

# 3. sweep registers rewritten for every sweep (the LiteVNA loses them when it idles)
n0 = config_writes(emu)
for _ in range(3):
    tr.scan(1e9, 2e9, 101)
check("registers written for each of 3 identical sweeps", config_writes(emu) - n0 == 3, config_writes(emu) - n0)
n1 = config_writes(saa2.ser)
for _ in range(3):
    saa2.scan(1e9, 2e9, 101)
check("   (SAA2 keeps them: written once)", config_writes(saa2.ser) - n1 == 1)
tr.write_reg(sd.REG_DATA_MODE, sd.DATA_MODE_EXIT_USB)   # device back to its own screen: registers lost
check("after the device dropped the USB sweep, next sweep is still right", err(tr.scan(1e9, 2e9, 101)) < 1e-6)

# 4. FIFO checksum: used only if the firmware sends it
check("no checksum in the records -> not checked, data fine", tr.check_cs is False)
emu_cs = lv.LiteVNA64Emulator(model, checksum=True)
tcs = lv.LiteVNA64Transport(ser=emu_cs)
tcs.scan(1e6, 3e9, 201)
check("checksum present -> checked", tcs.check_cs is True)
orig = emu_cs._records
state = {"n": 0}


def corrupt(idxs, start, step, n):
    recs = orig(idxs, start, step, n)
    for i in range(len(recs)):
        state["n"] += 1
        if state["n"] == 80:
            r = bytearray(recs[i])
            r[9] ^= 0x40
            recs[i] = bytes(r)
    return recs


emu_cs._records = corrupt
try:
    tcs.scan(1e6, 3e9, 201)
    check("   corrupted record rejected", False)
except ValueError as e:
    check("   corrupted record rejected", "checksum" in str(e))

# 5. extras: battery, capture 480x320, power, no SAA2 ecal check, exit USB mode on close
info = tr.command("info")
check("Device Info: battery voltage (register 0x5C)", "Battery: 3.95 V" in info, info.splitlines()[4])
wd, ht, data = tr.capture()
check("screen capture 480x320", (wd, ht) == (480, 320)
      and np.array_equal(np.frombuffer(data, "<u2").reshape(ht, wd), emu.screen_pixels()))
check("power command: not offered", "not documented" in tr.command("power 2") and emu.regs[sd.REG_TXPOWER] == 0)
one = lv.LiteVNA64Transport(ser=lv.LiteVNA64Emulator(lambda f: (np.ones(len(f), complex), np.zeros(len(f), complex))))
one.scan(1e6, 6e9, 201)
check("S11 exactly 1 is not taken for the SAA2 ecal problem", one.stuck_points == 0)
tr.close()
check("close(): device back to its own screen", emu.usb_mode is False)

# 6. through the worker with the LiteVNA simulator port
w = vd.VNAWorker(vd.SIM_LITE_PORT)
got, msgs = {}, []
w.connected.connect(lambda v: got.setdefault("ver", v))
w.sweep_ready.connect(lambda f, a, b, t: got.setdefault("f", f))
w.status.connect(msgs.append)
w.set_sweep(50e3, 6.3e9, 801)
w.set_chunk(1024)
w.start()
t0 = time.time()
while "f" not in got and time.time() - t0 < 20:
    time.sleep(0.01)
w.stop()
w.wait(5000)
check("worker on the LiteVNA simulator: 801 pts up to 6.3 GHz, no warning",
      got.get("ver") == "LiteVNA 64  FW 2.2" and w.caps["kind"] == "litevna" and len(got.get("f", [])) == 801
      and abs(got["f"][-1] - 6.3e9) < 1 and not msgs, "%s %s" % (got.get("ver"), msgs[:1]))

print("\n%d/%d passed" % (sum(ok_all), len(ok_all)))
sys.exit(0 if all(ok_all) else 1)
