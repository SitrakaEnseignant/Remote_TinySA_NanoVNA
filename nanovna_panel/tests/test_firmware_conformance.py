"""SAA2 transport vs. the firmware behaviour (NanoVNA2-firmware main2.cpp / command_parser.cpp, UG1101 App. II).

usage: test_firmware_conformance.py <panel dir>
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
import vna_device as vd  # noqa: E402

ok_all = []


def check(name, cond, detail=""):
    ok_all.append(bool(cond))
    print("%s  %s  %s" % ("PASS" if cond else "FAIL", name, detail))


def model(f):
    """Strongly frequency-dependent, so a point measured at the wrong frequency is obvious."""
    f = np.asarray(f, float)
    return 0.4 * np.exp(1j * f / 3e7), 0.6 * np.exp(-1j * f / 5e7)


def err(out, f_expected=None):
    f, a, b = out
    m11, m21 = model(f if f_expected is None else f_expected)
    return max(np.max(np.abs(a - m11)), np.max(np.abs(b - m21)))


class LoggingEmu(sd.SAA2Emulator):
    """Records the sweep registers every time the device applies them (setVNASweepToUSB)."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.applied = []

    def _config_written(self, old):
        super()._config_written(old)
        if self.log[-1] == (sd.CMD_WRITE2, sd.REG_VPF):     # last of the four writes: complete config
            self.applied.append(self.sweep())


# 1. frequency control: registers written == requested sweep, data measured at start + i*step
emu = LoggingEmu(model)
tr = sd.SAA2Transport(ser=emu)
worst_f, worst_d = 0.0, 0.0
for start, stop, n in [(50e3, 4.4e9, 201), (1e6, 3e9, 101), (3.0e9, 4.4e9, 51), (433.92e6, 433.93e6, 11),
                       (10e6, 20e6, 1024)]:
    out = tr.scan(start, stop, n)
    s, st, p = emu.sweep()
    f_dev = s + st * np.arange(p)                       # what the firmware sweeps
    want = np.linspace(start, stop, n)
    worst_f = max(worst_f, np.max(np.abs(f_dev - want)), np.max(np.abs(out[0] - f_dev)))
    worst_d = max(worst_d, err(out, f_dev))
check("sweepStartHz/StepHz/Points match the requested sweep", worst_f <= 0.5 * 1024,
      "max frequency deviation %.1f Hz (step rounded to 1 Hz)" % worst_f)
check("returned data are the points measured at start + i*step", worst_d < 1e-6, "max err %.1e" % worst_d)

ops = [x for x in emu.log if x[0] in (sd.CMD_WRITE8, sd.CMD_WRITE2, sd.CMD_WRITE)][-6:]
check("write order = NanoVNA-QT set_autosweep (start, step, points, values) then FIFO clear",
      ops[:4] == [(0x23, 0x00), (0x23, 0x10), (0x21, 0x20), (0x21, 0x22)] and ops[4] == (0x20, 0x30),
      ["%02X@%02X" % o for o in ops])
check("valuesPerFrequency written as 1", emu._get(sd.REG_VPF, 2) == 1)

# 2. whole panel sweep through the worker: 801 points in 201-point segments up to 4.4 GHz
emu2 = LoggingEmu(model)
real = vd.SAA2Emulator
vd.SAA2Emulator = lambda **k: emu2
w = vd.VNAWorker(vd.SIM_SAA2_PORT)
sweeps = []
w.sweep_ready.connect(lambda f, a, b, t: sweeps.append((f, a, b)))
w.set_sweep(1e6, 4.4e9, 801)
w.set_chunk(201)
w.start()
t0 = time.time()
while not sweeps and time.time() - t0 < 20:
    time.sleep(0.01)
w.stop()
w.wait(5000)
vd.SAA2Emulator = real
f, a, b = sweeps[0]
segs = emu2.applied[:4]                                # segments of the first sweep
covered = np.concatenate([s + st * np.arange(p) for s, st, p in segs])
check("801-pt panel sweep: 4 segments cover exactly the panel frequencies",
      len(segs) == 4 and len(covered) == 801 and np.max(np.abs(covered - f)) <= 100,
      "segments %s, max dev %.0f Hz" % ([(round(s / 1e6, 3), p) for s, st, p in segs], np.max(np.abs(covered - f))))
check("801-pt panel sweep: data correct at every frequency", err((f, a, b), covered) < 1e-6)
check("top of sweep reaches 4.4 GHz", abs(covered[-1] - 4.4e9) < 1e3, "%.6f GHz" % (covered[-1] / 1e9))

# 3. checksum (byte 31) - corrupted record is caught, link recovers after resync
orig_records = emu._records


def corrupt_once(idxs, start, step, n, state={"count": 0}):
    """Flip one bit in the 80th record generated: well inside the 201-point read (the points
    measured before the FIFO clears are discarded anyway)."""
    recs = orig_records(idxs, start, step, n)
    for i in range(len(recs)):
        state["count"] += 1
        if state["count"] == 80:
            r = bytearray(recs[i])
            r[9] ^= 0x40                               # one flipped bit in rev0
            recs[i] = bytes(r)
    return recs


emu._records = corrupt_once
try:
    tr.scan(1e6, 1e9, 201)
    check("corrupted record rejected by checksum", False)
except ValueError as e:
    check("corrupted record rejected by checksum", "checksum" in str(e), str(e))
emu._records = orig_records
tr.resync()
check("after resync the next sweep is clean", err(tr.scan(1e6, 1e9, 201)) < 1e-6)

# 4. device queue: 127 points, new points dropped when full -> clear-then-read still correct
time.sleep(1.0)                                         # ~1500 points measured, queue saturates
e = err(tr.scan(1e6, 1e9, 201))
check("127-point device queue overflow handled (clear before read)", emu.dropped > 1000 and e < 1e-6,
      "%d points dropped by the device while idle" % emu.dropped)

# 5. formats: fw >= 1.2 normalised fwd = 2^30, fw 1.1 random-phase raw waves
for fw in ((1, 1), (1, 2), (1, 3)):
    t = sd.SAA2Transport(ser=sd.SAA2Emulator(model, fw=fw))
    check("FW %d.%d record format decoded" % fw, err(t.scan(100e6, 2e9, 201)) < 1e-5)

# 6. exit USB data mode on close (dataMode = 2, firmware >= 1.1)
for fw, want in (((1, 3), False), ((1, 1), False), ((1, 0), True)):
    e_ = sd.SAA2Emulator(model, fw=fw)
    t = sd.SAA2Transport(ser=e_)
    t.scan(1e6, 1e9, 11)
    t.close()
    wrote = (sd.CMD_WRITE, sd.REG_DATA_MODE) in e_.log
    check("close() on FW %d.%d: device %s USB mode" % (fw + ("left" if not want else "stays in (no exit cmd)",)),
          e_.usb_mode == want and wrote == (fw >= (1, 1)))

# 7. TX power register 0x42
e_ = sd.SAA2Emulator(model, fw=(1, 3))
e_.regs[sd.REG_TXPOWER] = 1                             # level chosen on the device
t = sd.SAA2Transport(ser=e_)
check("TX power read from the device at connect", t.caps["tx_power"] == 1 and t.caps["power"])
t.command("power 2")
check("power 2 -> register 0x42 = 2, sweep untouched", e_.regs[0x42] == 2 and t.command("power").startswith("power 2"))
check("power 7 refused", t.command("power 7").startswith("usage") and e_.regs[0x42] == 2)
t11 = sd.SAA2Transport(ser=sd.SAA2Emulator(model, fw=(1, 1)))
check("FW 1.1: power control reported unavailable", not t11.caps["power"] and "1.2" in t11.command("power 3"))

# 8. screen capture 0xEE
for screen in ((320, 240), (480, 320)):
    e_ = sd.SAA2Emulator(model, fw=(1, 3), screen=screen)
    t = sd.SAA2Transport(ser=e_)
    wdt, hgt, data = t.capture()
    px = np.frombuffer(data, "<u2").reshape(hgt, wdt)
    check("capture %dx%d decoded (RGB565 high byte first -> panel format)" % screen,
          (wdt, hgt) == screen and np.array_equal(px, e_.screen_pixels()))
e_ = sd.SAA2Emulator(model, fw=(1, 2), capture=False)    # 1.2 build from before 2020-10-28
t = sd.SAA2Transport(ser=e_)
try:
    t.capture()
    check("1.2 build without capture: clean error", False)
except NotImplementedError as ex:
    check("1.2 build without capture: clean error, sweeps still fine", err(t.scan(1e6, 1e9, 101)) < 1e-6, str(ex))
try:
    sd.SAA2Transport(ser=sd.SAA2Emulator(model, fw=(1, 1))).capture()
    check("FW 1.1: capture refused without touching the device", False)
except NotImplementedError:
    check("FW 1.1: capture refused without touching the device", True)
check("wreg 0xEF refused (DFU restart register)", "refused" in tr.command("wreg 0xEF 1"))

# 9. identification
for rev, name in ((2, "V2_2"), (3, "V2 Plus"), (4, "V2 Plus4")):
    t = sd.SAA2Transport(ser=sd.SAA2Emulator(model, hwrev=rev))
    check("board rev %d -> %s, 4.4 GHz panel limit" % (rev, name),
          t.board == name and t.version_text().startswith("NanoVNA SAA2 (%s)" % name) and t.fmax == 4.4e9)
try:
    sd.SAA2Transport(ser=sd.SAA2Emulator(model, fw=(0xFF, 0), hwrev=0))
    check("DFU mode (FW major 0xFF) detected", False)
except RuntimeError as ex:
    check("DFU mode (FW major 0xFF) detected", "DFU" in str(ex), str(ex))

print("\n%d/%d passed" % (sum(ok_all), len(ok_all)))
sys.exit(0 if all(ok_all) else 1)
