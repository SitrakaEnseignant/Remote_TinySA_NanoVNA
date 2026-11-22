"""Start-up miss on the NanoVNA V2: first FIFO read returns nothing -> silent retry, no error message."""
import os
import sys
import time

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


def run(drop, want_sweeps, limit=25):
    real = sd.SAA2Emulator
    vd.SAA2Emulator = lambda **k: real(drop_fifo=drop, **k)     # used when the worker opens the port
    w = vd.VNAWorker(vd.SIM_SAA2_PORT)
    msgs, sweeps, disc = [], [], []
    w.status.connect(msgs.append)
    w.sweep_ready.connect(lambda *a: sweeps.append(time.time()))
    w.disconnected.connect(disc.append)
    w.set_sweep(1e6, 3e9, 201)
    t0 = time.time()
    w.start()
    while len(sweeps) < want_sweeps and not disc and time.time() - t0 < limit:
        time.sleep(0.01)
    first = (sweeps[0] - t0) if sweeps else None
    w.stop()
    w.wait(5000)
    vd.SAA2Emulator = real
    return w, msgs, sweeps, disc, first


# the case seen on the SAA2: first 201-point FIFO read gets 0 bytes, the retry works
w, msgs, sweeps, disc, first = run(drop=1, want_sweeps=3)
check("start-up miss: no message on screen", msgs == [], msgs)
check("start-up miss: sweeps arrive", len(sweeps) >= 3, "first trace after %.1f s" % (first or -1))
check("start-up miss: kept in Device Info", w.retries == 1 and "6432 bytes (got 0)" in w.last_error,
      "retries=%d last=%r" % (w.retries, w.last_error))

# two misses in a row: the user is told, then told it recovered
w, msgs, sweeps, disc, first = run(drop=2, want_sweeps=2)
check("2 misses: warned, then 'recovered'",
      len(msgs) == 2 and "2 in a row" in msgs[0] and "recovered" in msgs[1] and sweeps, msgs)

# device that never answers: messages from the 2nd failure, then disconnect after 6
w, msgs, sweeps, disc, first = run(drop=99, want_sweeps=1, limit=40)
check("dead FIFO: warnings then disconnect", not sweeps and disc and len(msgs) == 5,
      "%d messages, %s" % (len(msgs), disc[:1]))

print("\n%d/%d passed" % (sum(ok_all), len(ok_all)))
sys.exit(0 if all(ok_all) else 1)
