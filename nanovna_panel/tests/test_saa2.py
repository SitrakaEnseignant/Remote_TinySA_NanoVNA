"""Tests for the SAA2 transport against the byte-level emulator (no Qt, no hardware)."""
import sys
import threading
import time
import types

import numpy as np

PANEL = sys.argv[1]
sys.path.insert(0, PANEL)

# ---- minimal PyQt6.QtCore stub (QThread + pyqtSignal) so vna_device imports without Qt
qtcore = types.ModuleType("PyQt6.QtCore")


class _Sig:
    def __init__(self):
        self.cbs = []

    def connect(self, cb):
        self.cbs.append(cb)

    def emit(self, *a):
        for cb in list(self.cbs):
            cb(*a)


class pyqtSignal:
    def __init__(self, *t):
        pass

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return obj.__dict__.setdefault(self.name, _Sig())


class QThread:
    def __init__(self):
        self._t = None

    def start(self):
        self._t = threading.Thread(target=self.run, daemon=True)
        self._t.start()

    def wait(self, ms=None):
        self._t.join(ms / 1000 if ms else None)


qtcore.QThread, qtcore.pyqtSignal = QThread, pyqtSignal
pyqt6 = types.ModuleType("PyQt6")
pyqt6.QtCore = qtcore
sys.modules["PyQt6"], sys.modules["PyQt6.QtCore"] = pyqt6, qtcore

import saa2_device as sd  # noqa: E402
import vna_device as vd  # noqa: E402
from vna_cal import Calibration  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print("%s  %s  %s" % ("PASS" if cond else "FAIL", name, detail))


def model(f):
    """Deterministic raw model, strongly frequency dependent so misplaced points show up."""
    f = np.asarray(f, float)
    return 0.4 * np.exp(1j * f / 7e7) * (1 + f / 6e9), 0.6 * np.exp(-1j * f / 1.1e8)


def err(tr_out):
    f, a, b = tr_out
    m11, m21 = model(f)
    return max(np.max(np.abs(a - m11)), np.max(np.abs(b - m21)))


# 1. handshake / capabilities
emu = sd.SAA2Emulator(model, fw=(1, 3), hwrev=2)
tr = sd.SAA2Transport(ser=emu)
check("handshake fw1.3 hw2", tr.fw == (1, 3) and tr.caps["power"] and not tr.s21_hack, tr.command("version"))
tr4 = sd.SAA2Transport(ser=sd.SAA2Emulator(model, fw=(1, 3), hwrev=4))
check("SAA2 range 50 kHz - 4.4 GHz (any hw rev)", tr.fmin == 50e3 and tr.fmax == 4.4e9 and tr4.fmax == 4.4e9
      and tr.caps["fmax"] == 4.4e9)
e = err(tr.scan(3.0e9, 4.4e9, 101))
check("scan up to 4.4 GHz not clipped", e < 1e-5, "err %.2e" % e)
print("   info:", tr.command("info").replace("\n", " | "))

# 2. raw values, several sweeps (FIFO read starts mid-sweep: index wrap)
e = max(err(tr.scan(1e6, 3e9, 201)) for _ in range(5))
check("201-pt scans match model", e < 1e-5, "max err %.2e" % e)

# 3. config change with a stale in-flight point, then different sizes
worst = 0
for (a, b, n) in [(50e3, 900e6, 101), (100e6, 2.9e9, 255), (1e6, 3e9, 11), (300e6, 400e6, 51)]:
    worst = max(worst, err(tr.scan(a, b, n)))
check("config changes / stale point rejected", worst < 1e-5, "max err %.2e" % worst)

# 4. > 255 points -> several FIFO reads; limit -> PointsError
t0 = time.perf_counter()
e = err(tr.scan(1e6, 3e9, 1024))
check("1024 pts (5 FIFO reads)", e < 1e-5, "err %.2e, %.2f s at 1500 pt/s" % (e, time.perf_counter() - t0))
try:
    tr.scan(1e6, 3e9, 1025)
    check("1025 pts rejected", False)
except sd.PointsError:
    check("1025 pts rejected", True)

# 5. firmware <= 1.0.1: first-point S21 bug handled by the extra point
emu_old = sd.SAA2Emulator(model, fw=(1, 1))
tr_old = sd.SAA2Transport(ser=emu_old)
e = err(tr_old.scan(200e6, 1e9, 101))
check("fw 1.0.1 S21 first-point workaround", tr_old.s21_hack and e < 1e-5, "err %.2e" % e)
emu_bug = sd.SAA2Emulator(model, fw=(1, 1))
tr_bug = sd.SAA2Transport(ser=emu_bug)
tr_bug.s21_hack = False
f, a, b = tr_bug.scan(200e6, 1e9, 101)
check("   (control: without workaround point 0 is wrong)", abs(b[0] - model(f[:1])[1][0]) > 0.1)

# 6. aborted FIFO read, then resync and scan again
tr.ser.write(bytes([sd.CMD_READFIFO, sd.REG_FIFO, 255]))
time.sleep(0.05)
tr.ser.read(100)                       # partial read, rest is still streaming
tr.resync()
e = err(tr.scan(10e6, 1e9, 201))
check("resync after aborted FIFO read", e < 1e-5, "err %.2e" % e)

# 7. shell-command emulation
check("cal on/off are silent no-ops", tr.command("cal off") == "" and tr.command("cal on") == "")
tr.command("power 1")
check("power 1 -> reg 0x42", emu.regs[sd.REG_TXPOWER] == 1 and tr.command("reg 0x42") == "0x01")
check("reg read FW major", tr.command("reg 0xF3") == "0x01")
cap = tr.capture(800, 480)
check("capture (FW 1.3) returns the device screen size", cap[:2] == (320, 240) and len(cap[2]) == 320 * 240 * 2)

# 8. protocol probe
check("probe: V2 -> saa2", sd.probe_kind(sd.SAA2Emulator(model)) == "saa2")
stuck = sd.SAA2Emulator(model)
stuck.write(bytes([sd.CMD_WRITE8, 0x50]))       # parser now waits for 8 data bytes
check("probe: V2 with parser mid-command -> saa2", sd.probe_kind(stuck) == "saa2")


class FakeShell:
    in_waiting = 0

    def __init__(self):
        self.out = b""

    def reset_input_buffer(self):
        self.out = b""

    def write(self, d):
        self.out += b"\r\nch> "
        self.in_waiting = len(self.out)

    def read(self, n=1):
        o, self.out = self.out[:n], self.out[n:]
        self.in_waiting = len(self.out)
        return o


check("probe: shell device -> shell", sd.probe_kind(FakeShell()) == "shell")


# 9. end to end through VNAWorker with the SAA2 simulator port (segments, commands, capture)
def run_worker(port, setup, n_sweeps):
    w = vd.VNAWorker(port)
    sweeps, msgs, replies = [], [], {}
    w.sweep_ready.connect(lambda f, a, b, t: sweeps.append((f, a, b, t)))
    w.status.connect(msgs.append)
    w.reply.connect(lambda tok, r: replies.__setitem__(tok, r))
    w.disconnected.connect(lambda r: msgs.append("DISC " + r))
    setup(w)
    w.start()
    t0 = time.time()
    while len(sweeps) < n_sweeps and time.time() - t0 < 30:
        time.sleep(0.01)
    return w, sweeps, msgs, replies


def setup(w):
    w.set_sweep(1e6, 3e9, 801)
    w.set_chunk(201)
    w.send("version", 1)
    w.capture(2)


w, sweeps, msgs, replies = run_worker(vd.SIM_SAA2_PORT, setup, 2)
f, a, b, t = sweeps[-1]
sim = vd.SimTransport()
ref11, ref21 = sim.measure(f, False)
d = max(np.max(np.abs(a - ref11)), np.max(np.abs(b - ref21)))
check("worker: 801 pts in 201-pt segments via SAA2 sim", len(f) == 801 and d < 5e-3,
      "max dev from raw model %.1e, sweep %.2f s" % (d, t))
check("worker: caps reported", w.caps["kind"] == "saa2" and not w.caps["device_cal"])
check("worker: version reply", replies.get(1, "").startswith("NanoVNA SAA2"), replies.get(1))
check("worker: capture reply has the SAA2 screen size, link stays up",
      replies.get(2, (0, 0))[:2] == (320, 240) and not any(m.startswith("DISC") for m in msgs), msgs[:2])
w.stop()
w.wait(3000)

# 10. app calibration on SAA2 sim data (raw -> corrected)
w, sweeps, msgs, replies = run_worker(vd.SIM_SAA2_PORT, lambda w: w.set_sweep(1e6, 3e9, 201), 1)


def grab(dut, n=2):
    w.send("simdut %s" % dut)
    k = len(sweeps)
    while len(sweeps) < k + 1 + n:        # skip the sweep in progress
        time.sleep(0.01)
    s = sweeps[k + 1:k + 1 + n]
    return s[0][0], np.mean([x[1] for x in s], 0), np.mean([x[2] for x in s], 0)


cal = Calibration()
for std in ("open", "short", "load", "thru"):
    f, a, b = grab(std)
    cal.add(std, f, a, b)
kind = cal.solve()
worst = 0
for std, ideal11, ideal21 in (("open", 1, 0), ("short", -1, 0), ("load", 0, 0), ("thru", 0, 1)):
    f, a, b = grab(std, 1)
    c11, c21 = cal.apply(f, a, b)
    # thru: only S21 (its corrected S11 is the port-2 match, which a 1.5-port cal cannot remove)
    worst = max(worst, np.max(np.abs(c21 - ideal21)) if std == "thru" else np.max(np.abs(c11 - ideal11)))
f, a, b = grab("load", 1)
raw_err = np.max(np.abs(a))
check("app SOLT on raw SAA2 data", kind == "SOLT" and worst < 0.01,
      "%s: residual %.1e (raw load |S11| was %.2f)" % (kind, worst, raw_err))
w.stop()
w.wait(3000)

print("\n%d/%d passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
