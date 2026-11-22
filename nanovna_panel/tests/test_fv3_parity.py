"""NanoVNA-F V3 parity: the ORIGINAL vna_device.py and the patched one must talk to an F-V3 identically.

usage: test_fv3_parity.py <patched panel dir> <original panel dir>
A fake F-V3 shell (ST VID 0483 / PID 5740) logs every byte it receives; both workers run the same
scenario (commands, 401-point sweep in 201-point segments, screen capture, 101-point fallback).
"""
import importlib.util
import os
import sys
import time
import types

import numpy as np

PANEL, ORIG = sys.argv[1], sys.argv[2]
sys.path.insert(0, PANEL)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_saa2_stub import install_qt_stub  # noqa: E402

install_qt_stub()


class FakeFV3:
    """NanoVNA-F V3 text shell: echo, answer, prompt ('ch> ', or 'ch>' on firmware 0.6.0);
    scan ... 7 prints freq s11re s11im s21re s21im."""
    timeout = 0.05

    def __init__(self, max_points=201, prompt=b"ch> "):
        self.max_points = max_points
        self.prompt = prompt
        self.inbuf, self.out, self.rx_log = b"", b"", b""

    @property
    def in_waiting(self):
        return len(self.out)

    def reset_input_buffer(self):
        self.out = b""

    def read(self, n=1):
        if not self.out:
            time.sleep(0.002)
        o, self.out = self.out[:n], self.out[n:]
        return o

    def close(self):
        pass

    def write(self, d):
        self.rx_log += d
        self.inbuf += d
        while b"\r" in self.inbuf:
            line, self.inbuf = self.inbuf.split(b"\r", 1)
            self._line(line.decode())
        return len(d)

    def _line(self, line):
        a = line.split()
        self.out += line.encode() + b"\r\n"            # echo
        if a[:1] == ["version"]:
            self.out += b"NanoVNA-F V3 0.5.1\r\n"
        elif a[:1] == ["scan"]:
            f0, f1, n = int(a[1]), int(a[2]), int(a[3])
            if n > self.max_points:
                self.out += b"usage: scan {start(Hz)} {stop(Hz)} [points] [outmask]\r\n"
            else:
                for f in np.round(np.linspace(f0, f1, n)):
                    s11, s21 = 0.3 * np.exp(1j * f / 1e9), 0.5 * np.exp(-1j * f / 2e9)
                    self.out += b"%d %.9f %.9f %.9f %.9f\r\n" % (f, s11.real, s11.imag, s21.real, s21.imag)
        elif a[:1] == ["capture"]:
            self.out += bytes(range(256)) * (800 * 480 * 2 // 256)
        self.out += self.prompt


class Port:
    device, vid, pid, hwid, description = "COM9", 0x0483, 0x5740, "USB VID:PID=0483:5740", "STM VCP"


DEV = {}
serial_mod = types.ModuleType("serial")
tools = types.ModuleType("serial.tools")
lp = types.ModuleType("serial.tools.list_ports")
lp.comports = lambda: [Port()]
serial_mod.Serial = lambda port, *a, **k: DEV[port]
serial_mod.tools, tools.list_ports = tools, lp
sys.modules.update({"serial": serial_mod, "serial.tools": tools, "serial.tools.list_ports": lp})


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


orig = load("vna_device_orig", ORIG + "/vna_device.py")
new = load("vna_device_new", PANEL + "/vna_device.py")


def scenario(mod, max_points, prompt):
    DEV["COM9"] = dev = FakeFV3(max_points, prompt)
    w = mod.VNAWorker("COM9")
    out = dict(sweeps=[], status=[], replies={}, ver=None, disc=None)
    w.connected.connect(lambda v: out.__setitem__("ver", v))
    w.disconnected.connect(lambda r: out.__setitem__("disc", r))
    w.status.connect(out["status"].append)
    w.reply.connect(lambda t, r: out["replies"].__setitem__(t, r))
    w.sweep_ready.connect(lambda f, a, b, t: out["sweeps"].append((f, a, b)))
    w.set_sweep(1e6, 6e9, 401)
    w.set_chunk(201)
    for i, c in enumerate(["cal off", "save 1", "recall 1", "cal on"]):
        w.send(c, i)
    w.capture(9)
    w.set_running(False)                 # Hold + two Single sweeps: same number of scans in both runs
    w.single_sweep()
    w.start()
    t0 = time.time()
    while len(out["sweeps"]) < 1 and time.time() - t0 < 20:
        time.sleep(0.01)
    w.single_sweep()
    while len(out["sweeps"]) < 2 and time.time() - t0 < 20:
        time.sleep(0.01)
    time.sleep(0.2)
    w.stop()
    w.wait(5000)
    out["log"] = dev.rx_log
    return out


ok_all = []


def check(name, cond, detail=""):
    ok_all.append(bool(cond))
    print("%s  %s  %s" % ("PASS" if cond else "FAIL", name, detail))


for prompt, mp in ((b"ch> ", 201), (b"ch> ", 101), (b"ch>", 201)):
    a, b = scenario(orig, mp, prompt), scenario(new, mp, prompt)
    tag = "F-V3 (prompt %r, %d-pt scan limit)" % (prompt.decode(), mp)
    check(tag + ": identical bytes sent to the device", a["log"] == b["log"],
          "%d bytes, commands: %s" % (len(b["log"]), sorted(set(x.split(" ")[0] for x in b["log"].decode().split("\r") if x))))
    same = all(np.array_equal(x[k], y[k]) for x, y in zip(a["sweeps"], b["sweeps"]) for k in range(3))
    check(tag + ": identical sweep data", len(a["sweeps"]) == len(b["sweeps"]) == 2 and same and len(b["sweeps"][0][0]) == 401)
    cap_a, cap_b = a["replies"].get(9), b["replies"].get(9)
    check(tag + ": screen capture identical", cap_a is not None and cap_b is not None and cap_a == cap_b
          and len(cap_b[2]) == 800 * 480 * 2)
    check(tag + ": same version / status messages", a["ver"] == b["ver"] and a["status"] == b["status"],
          "%r %s" % (b["ver"], b["status"]))

check("patched worker reports F-V3 limits 1 MHz - 6 GHz, device cal, slots, capture",
      new.SHELL_CAPS["fmin"] == 1e6 and new.SHELL_CAPS["fmax"] == 6e9 and new.SHELL_CAPS["device_cal"]
      and new.SHELL_CAPS["slots"] and new.SHELL_CAPS["capture"] and not new.SHELL_CAPS["power"])
check("F-V3 port label unchanged ('NanoVNA')", new.list_ports() == [("COM9", "STM VCP", "NanoVNA")])

print("\n%d/%d passed" % (sum(ok_all), len(ok_all)))
sys.exit(0 if all(ok_all) else 1)
