"""Detection -> connection path with a fake pyserial: USB ids, Windows hwid quirk, probing, worker connect."""
import sys
import threading
import time
import types

PANEL = sys.argv[1]
sys.path.insert(0, PANEL)

# ---- PyQt6.QtCore stub
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
        return self if obj is None else obj.__dict__.setdefault(self.name, _Sig())


class QThread:
    def start(self):
        self._t = threading.Thread(target=self.run, daemon=True)
        self._t.start()

    def wait(self, ms=None):
        self._t.join(ms / 1000 if ms else None)


qtcore.QThread, qtcore.pyqtSignal = QThread, pyqtSignal
pyqt6 = types.ModuleType("PyQt6")
pyqt6.QtCore = qtcore
sys.modules["PyQt6"], sys.modules["PyQt6.QtCore"] = pyqt6, qtcore


# ---- fake pyserial: ports + devices
class Port:
    def __init__(self, device, vid, pid, hwid, desc):
        self.device, self.vid, self.pid, self.hwid, self.description = device, vid, pid, hwid, desc


class FakeShell:
    """Text-shell NanoVNA: echoes the command, answers, then the ch> prompt."""
    timeout = 0.05

    def __init__(self):
        self.out = b""

    @property
    def in_waiting(self):
        return len(self.out)

    def reset_input_buffer(self):
        self.out = b""

    def write(self, d):
        for line in d.split(b"\r")[:-1]:
            if line == b"version":
                self.out += b"version\r\nNanoVNA-F V3 FAKE\r\nch> "
            else:
                self.out += line + b"\r\nch> "
        return len(d)

    def read(self, n=1):
        if not self.out:
            time.sleep(0.005)
        o, self.out = self.out[:n], self.out[n:]
        return o

    def close(self):
        pass


DEVICES, PORTS, OPENS = {}, [], []
serial_mod = types.ModuleType("serial")
tools = types.ModuleType("serial.tools")
lp = types.ModuleType("serial.tools.list_ports")
lp.comports = lambda: list(PORTS)


def Serial(port, *a, **k):
    OPENS.append(port)
    return DEVICES[port]


serial_mod.Serial, serial_mod.tools, tools.list_ports = Serial, tools, lp
sys.modules.update({"serial": serial_mod, "serial.tools": tools, "serial.tools.list_ports": lp})

import numpy as np  # noqa: E402
import saa2_device as sd  # noqa: E402
import litevna64_device as lv  # noqa: E402
import vna_device as vd  # noqa: E402

model = lambda f: (0.3 * np.ones(len(f), complex), 0.5 * np.ones(len(f), complex))
DEVICES.update({
    "COM3": FakeShell(),                                   # unrelated / shell
    "COM5": sd.SAA2Emulator(model),                        # SAA2, normal USB ids
    "COM6": sd.SAA2Emulator(model),                        # SAA2, Windows hwid quirk (no vid/pid)
    "COM7": sd.SAA2Emulator(model),                        # V2 clone with an unknown USB id
    "COM8": lv.LiteVNA64Emulator(model),                   # LiteVNA 64: same USB id as the SAA2
    "COM9": FakeShell(),                                   # NanoVNA-F V3, ST ids
})
PORTS += [Port("COM3", 0x1A86, 0x7523, "USB VID:PID=1A86:7523", "USB-SERIAL CH340"),
          Port("COM9", 0x0483, 0x5740, "USB VID:PID=0483:5740", "STMicroelectronics Virtual COM Port"),
          Port("COM6", None, None, r"PORTS\VID_04B4&PID_0008\DEMO", "USB Serial Device"),
          Port("COM5", 0x04B4, 0x0008, "USB VID:PID=04B4:0008", "USB Serial Device"),
          Port("COM7", 0x1209, 0x4242, "USB VID:PID=1209:4242", "USB Serial Device"),
          Port("COM8", 0x04B4, 0x0008, "USB VID:PID=04B4:0008", "USB Serial Device")]

ok_all = []


def check(name, cond, detail=""):
    ok_all.append(cond)
    print("%s  %s  %s" % ("PASS" if cond else "FAIL", name, detail))


ports = vd.list_ports()
print("list_ports():", [(d, l) for d, _, l in ports])
check("SAA2 ports labelled and listed first",
      [l for _, _, l in ports][:4] == [sd.LABEL_SAA2] * 3 + ["NanoVNA"] and ports[0][0] == "COM5")
check("Windows hwid quirk (COM6) recognised", vd.port_label("COM6") == sd.LABEL_SAA2)
check("startup auto-connect condition true", any(t for _, _, t in ports))

for port, want, probe in (("COM5", "saa2", False), ("COM6", "saa2", False), ("COM9", "shell", False),
                          ("COM7", "saa2", True), ("COM3", "shell", True)):
    OPENS.clear()
    tr = vd.open_transport(port)
    check("open_transport(%s) -> %s" % (port, want), tr.kind == want and (len(OPENS) == 2) == probe,
          "%s, port opened %dx (%s)" % (type(tr).__name__, len(OPENS), "probe + connect" if probe else "direct"))

OPENS.clear()
lite = vd.open_transport("COM8")
check("open_transport(COM8, LiteVNA on the SAA2 USB id) -> identified as LiteVNA",
      isinstance(lite, lv.LiteVNA64Transport) and lite.caps["kind"] == "litevna" and len(OPENS) == 1,
      lite.version_text())
lite.close()

# what the panel does at startup: first labelled port, then VNAWorker.connect
first = ports[0][0]
w = vd.VNAWorker(first)
got = {}
w.connected.connect(lambda v: got.setdefault("ver", v))
w.disconnected.connect(lambda r: got.setdefault("disc", r))
w.sweep_ready.connect(lambda f, a, b, t: got.setdefault("sweep", len(f)))
w.set_sweep(1e6, 3e9, 201)
w.start()
t0 = time.time()
while "sweep" not in got and "disc" not in got and time.time() - t0 < 10:
    time.sleep(0.01)
check("worker on %s: connected as SAA2 and sweeping" % first,
      got.get("ver", "").startswith("NanoVNA SAA2") and w.caps["kind"] == "saa2" and got.get("sweep") == 201,
      "%s | caps fmax %.1f GHz" % (got.get("ver") or got.get("disc"), w.caps["fmax"] / 1e9))
w.stop()
w.wait(3000)
print("\n%d/%d passed" % (sum(ok_all), len(ok_all)))
sys.exit(0 if all(ok_all) else 1)
