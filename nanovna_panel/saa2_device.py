"""
NanoVNA SAA2 (NanoVNA V2 family: V2_2, V2 Plus, V2 Plus4) device layer: binary register protocol over USB-CDC.

Checked against the device's own sources:
  [FW]  NanoVNA2-firmware  command_parser.cpp (opcodes), main2.cpp cmdReadFIFO / setVNASweepToUSB /
        cmdRegisterWrite (registers, FIFO, capture), common.hpp (FREQUENCY_MAX), ili9341.hpp (RGB565)
  [DOC] UG1101 user manual, Appendix II - USB data interface
  [QT]  NanoVNA-QT libxavna/xavna.C set_autosweep / read_autosweep (the vendor's own host code)

Commands (host -> device, no delimiter) [FW command_parser.cpp]:
  0x00 NOP (8 of them reset the parser)     0x0D INDICATE -> ASCII '2'
  0x10/0x11/0x12/0x13 aa   READ 1/2/4/8 bytes        0x18 aa nn  READFIFO nn records (streamed as measured)
  0x20/0x21/0x22/0x23 aa d WRITE 1/2/4/8 bytes LE    0x28 aa nn data  WRITEFIFO (unused)
Registers [FW main2.cpp]:
  0x00 sweepStartHz u64, 0x10 sweepStepHz u64, 0x20 sweepPoints u16, 0x22 valuesPerFrequency u16
      -> any write applies all four (setVNASweepToUSB), restarts the sweep at index 0 and enters USB mode.
      Point i is measured at start + i*step (above 140 MHz rounded to the ADF4350 grid, 6-12.5 kHz).
      sweepPoints is capped at 1024 (V2_2 / V2 Plus) or 65536 (Plus4).
  0x26 dataMode: 0 VNA data, 1 raw samples, 2 exit USB data mode (firmware >= 1.1)
  0x30 valuesFIFO: write anything = clear.  The device queue holds 127 points and drops NEW points
      when full, so the host clears it before an on-demand sweep [DOC].
  0x40 averaging (on-device UI only, ignored in USB mode) - not used
  0x42 ADF4350 TX power 0..3 (3 = max, used above 140 MHz; builds from 2020-11-22, i.e. firmware >= 1.2)
      Note: the register-map comment in main2.cpp swaps 0x40 / 0x42; the code and the commit use 0x42.
  0xEE write -> screen capture: <u16 width, u16 height, u8 16> then width*height RGB565 pixels,
      high byte first (firmware >= 1.2, builds from 2020-10-28).  Never write 0xEF (DFU restart).
  0xF0 variant (2)  0xF1 protocol (1)  0xF2 board rev (0 V2_0, 1 V2_1, 2 V2_2, 3 V2 Plus, 4 V2 Plus4)
  0xF3 / 0xF4 firmware major / minor  (0xFF major = DFU bootloader)
FIFO record, 32 bytes LE: fwd0 re/im, rev0 re/im, rev1 re/im (int32), freqIndex u16, 5 reserved,
  checksum u8 at byte 31 (seed 0x46; c = (c ^ ((c << 1) | 1)) ^ byte, over bytes 0..30).
  S11 = rev0 / fwd0, S21 = rev1 / fwd0.  Data are NOT corrected by the user's on-device cal [DOC].
Device measurement mode (MENU > STIMULUS > CFG SWEEP: ECAL / No ECAL / CW): S11 sent over USB is corrected
  with the device's internal "ecal" terms (ecalApplyReflection, indexed by point number).  In USB mode they are
  re-measured only when the device is in ECAL mode; in "No ECAL" or "CW" mode (CW FREQ on the device selects CW
  and it stays) the terms are stale - measured at other frequencies - or zero, and zero terms make the firmware
  return S11 = 1 whatever is connected.  The transport counts such points (stuck_points) so the panel can say so.
Frequency range: firmware accepts 10 kHz - 4.4 GHz (FREQUENCY_MIN/MAX).  UG1101 specifies
  50 kHz - 3 GHz for V2_2 / V2 Plus and 50 kHz - 4.4 GHz for V2 Plus4.  This panel offers 50 kHz - 4.4 GHz.

Layers: V2Link = register access + identification, shared by every instrument of this protocol family;
SAA2Transport(V2Link) = the NanoVNA SAA2.  The LiteVNA 64 speaks the same protocol: litevna64_device.py
subclasses SAA2Transport and SAA2Emulator; vna_device.open_v2() picks the class from the identity registers.
SAA2Transport exposes the same interface as vna_device.SerialTransport (command / scan / capture /
resync / close), so VNAWorker and the panel use it unchanged; command() emulates the shell commands
the panel sends.  SAA2Emulator is a byte-level fake of the device, built from the firmware code above.
"""
import collections
import struct
import time

import numpy as np

try:
    import serial
except ImportError:  # pragma: no cover
    serial = None

SAA2_VID, SAA2_PID = 0x04B4, 0x0008
SAA2_FMIN, SAA2_FMAX = 50e3, 4.4e9
LABEL_SAA2 = "NanoVNA SAA2 / LiteVNA"      # USB id shared with the LiteVNA: which one is known once connected
SAA2_MAX_POINTS = 1024
FIFO_CHUNK = 255
DEVICE_QUEUE = 127

CMD_NOP, CMD_INDICATE = 0x00, 0x0D
CMD_READ, CMD_READ2, CMD_READ4, CMD_READ8, CMD_READFIFO = 0x10, 0x11, 0x12, 0x13, 0x18
CMD_WRITE, CMD_WRITE2, CMD_WRITE4, CMD_WRITE8, CMD_WRITEFIFO = 0x20, 0x21, 0x22, 0x23, 0x28

REG_START, REG_STEP, REG_POINTS, REG_VPF = 0x00, 0x10, 0x20, 0x22
REG_DATA_MODE, REG_FIFO, REG_TXPOWER, REG_CAPTURE = 0x26, 0x30, 0x42, 0xEE
REG_VARIANT, REG_PROTO, REG_HWREV, REG_FW_MAJOR, REG_FW_MINOR = 0xF0, 0xF1, 0xF2, 0xF3, 0xF4
DATA_MODE_EXIT_USB = 2
STUCK_TOL = 1e-4             # |S11 - 1| below this on a point = ecal terms are zero (see module doc)
ECAL_HINT = ("SAA2: %d of %d points return S11 = 1 whatever is connected - the device is not in ECAL mode, so its "
             "internal correction is not measured over USB. On the SAA2: MENU > STIMULUS > CFG SWEEP > ECAL, "
             "then reconnect.")


def ecal_problem(stuck, n):
    """True when enough points are stuck at S11 = 1 to mean the device is not in ECAL mode."""
    return stuck >= max(3, n // 20)

BOARD_NAMES = {0: "V2_0", 1: "V2_1", 2: "V2_2", 3: "V2 Plus", 4: "V2 Plus4"}

RECORD = np.dtype([("fwd", "<i4", (2,)), ("rev0", "<i4", (2,)), ("rev1", "<i4", (2,)),
                   ("idx", "<u2"), ("res", "u1", (5,)), ("cs", "u1")])
assert RECORD.itemsize == 32

POWER_DESC = {3: "max", 2: "-3 dB", 1: "-6 dB", 0: "-9 dB"}


class PointsError(Exception):
    """Sweep length rejected by the device (VNAWorker then retries with shorter segments)."""


def fifo_checksum(raw):
    """Checksum of FIFO records, raw = uint8 array (k, 32); same loop as cmdReadFIFO in the firmware."""
    c = np.full(raw.shape[0], 0b01000110, np.uint8)
    for i in range(31):
        c = (c ^ ((c << np.uint8(1)) | np.uint8(1))) ^ raw[:, i]
    return c


def is_saa2_port(p):
    """True for a pyserial ListPortInfo that looks like a NanoVNA SAA2 (incl. the Windows hwid quirk)."""
    if p.vid == SAA2_VID and p.pid == SAA2_PID:
        return True
    return "VID_04B4&PID_0008" in (getattr(p, "hwid", "") or "").upper()


def _answer(ser, payload, wait):
    ser.reset_input_buffer()
    ser.write(payload)
    t0, data = time.monotonic(), b""
    while time.monotonic() - t0 < wait:
        data += ser.read(max(1, ser.in_waiting))
        if data:
            time.sleep(0.05)
            data += ser.read(ser.in_waiting)
            break
    return data


def probe_kind(ser, wait=0.4):
    """'saa2', 'shell' or None.  CR is INDICATE for a SAA2 (answer '2') and an empty line for a shell
    device (answer: prompt).  A SAA2 whose parser is mid-command may swallow the CR, so a silent port
    gets a second try with the 8-byte NOP reset first (a shell device would have answered already)."""
    data = _answer(ser, b"\r", wait)
    if data[:1] == b"2":
        return "saa2"
    if data:
        return "shell"
    if _answer(ser, bytes(8) + b"\r", wait)[:1] == b"2":
        return "saa2"
    return None


def open_serial(port):
    if serial is None:
        raise RuntimeError("pyserial is not installed")
    return serial.Serial(port, 115200, timeout=0.05, write_timeout=2)


# --------------------------------------------------------------------------
class V2Link:
    """Register access and identification - common to every instrument of the SAA2 protocol family."""

    def __init__(self, ser):
        self.ser = ser
        self.rx = b""
        self.cfg = None

    def identify(self):
        """Reset the parser, check INDICATE, read the identity registers -> (variant, protocol, hw rev, (fw major, minor))."""
        time.sleep(0.05)
        self.resync()
        self.ser.write(bytes([CMD_INDICATE]))
        if self._read_exact(1, 1.0) != b"2":
            raise RuntimeError("no NanoVNA SAA2 / LiteVNA answer to INDICATE")
        self.variant = self.read_reg(REG_VARIANT)
        self.proto = self.read_reg(REG_PROTO)
        self.hwrev = self.read_reg(REG_HWREV)
        self.fw = (self.read_reg(REG_FW_MAJOR), self.read_reg(REG_FW_MINOR))
        if self.fw[0] == 0xFF:
            raise RuntimeError("the instrument is in DFU (bootloader) mode - restart it normally")
        if self.variant != 2:
            raise RuntimeError("unexpected device variant %d (NanoVNA SAA2 / LiteVNA report 2)" % self.variant)
        return self.variant, self.proto, self.hwrev, self.fw

    def _fill(self):
        chunk = self.ser.read(max(1, self.ser.in_waiting))
        if chunk:
            self.rx += chunk
            return True
        return False

    def _read_exact(self, n, idle):
        last = time.monotonic()
        while len(self.rx) < n:
            if self._fill():
                last = time.monotonic()
            elif time.monotonic() - last > idle:
                raise TimeoutError("timeout reading %d bytes (got %d)" % (n, len(self.rx)))
        out, self.rx = self.rx[:n], self.rx[n:]
        return out

    def resync(self):
        """Reset the parser and throw away anything still streaming from an aborted FIFO read."""
        self.ser.write(bytes(8))
        time.sleep(0.05)
        self.ser.reset_input_buffer()
        t0 = last = time.monotonic()
        while time.monotonic() - last < 0.1 and time.monotonic() - t0 < 2.0:
            if self.ser.read(max(1, self.ser.in_waiting)):
                last = time.monotonic()
        self.rx = b""
        self.cfg = None

    def read_reg(self, addr, size=1):
        op = {1: CMD_READ, 2: CMD_READ2, 4: CMD_READ4, 8: CMD_READ8}[size]
        self.ser.write(bytes([op, addr]))
        return int.from_bytes(self._read_exact(size, 1.0), "little")

    def write_reg(self, addr, value, size=1):
        op, fmt = {1: (CMD_WRITE, "<B"), 2: (CMD_WRITE2, "<H"), 4: (CMD_WRITE4, "<I"), 8: (CMD_WRITE8, "<Q")}[size]
        self.ser.write(bytes([op, addr]) + struct.pack(fmt, int(value)))



# --------------------------------------------------------------------------
class SAA2Transport(V2Link):
    """NanoVNA SAA2 (V2_2, V2 Plus, V2 Plus4).  Subclasses override _setup() and the *_text / *_command hooks."""
    kind = "saa2"            # protocol family (the instrument is caps["kind"])
    model = "saa2"

    def __init__(self, port=None, ser=None):
        super().__init__(ser if ser is not None else open_serial(port))
        self.checksum_errors = 0
        self.stuck_points, self.last_points = 0, 0
        self.tx_power = None
        self.identify()
        self._setup()

    def _setup(self):
        self.fmin = SAA2_FMIN
        self.board = BOARD_NAMES.get(self.hwrev, "rev %d" % self.hwrev)
        # firmware <= 1.0.1 returns a bad S21 for the first point: sweep one extra point and drop it
        self.s21_hack = self.fw <= (1, 1)
        self.check_cs = self.fw >= (1, 0)                    # checksum byte 31 in every versioned firmware
        self.fmax = SAA2_FMAX
        self.spec_fmax = 4.4e9 if self.hwrev >= 4 else 3.0e9  # UG1101 specification
        self.cache_cfg = True                                # registers kept until changed
        self.ecal_check = True
        power = capture = self.fw >= (1, 2)
        if power:
            lvl = self.read_reg(REG_TXPOWER)                 # firmware mirrors its setting here at boot
            self.tx_power = lvl if lvl in POWER_DESC else None
        self._make_caps("%s, FW %d.%d" % ((self.board,) + self.fw), capture, power)

    def _make_caps(self, detail, capture, power):
        self.max_points = SAA2_MAX_POINTS - int(self.s21_hack)
        self.caps = dict(kind=self.model, board=self.board, fw="%d.%d" % self.fw, detail=detail,
                         device_cal=False, slots=False, capture=capture,
                         power=power, tx_power=self.tx_power, fmin=self.fmin, fmax=self.fmax,
                         max_points=self.max_points)

    def close(self):
        """Give the device its own UI back (dataMode = 2, firmware >= 1.1), then close the port."""
        try:
            if self.fw >= (1, 1):
                self.write_reg(REG_DATA_MODE, DATA_MODE_EXIT_USB)
                time.sleep(0.05)
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass

    def _clear_fifo(self):
        self.write_reg(REG_FIFO, 0)

    # ---------------------------------------------------------------- sweep
    def sweep_registers(self, start, stop, n):
        """(sweepStartHz, sweepStepHz, sweepPoints) written to the device for a start..stop, n-point sweep."""
        start = int(round(min(max(start, self.fmin), self.fmax)))
        stop = int(round(min(max(stop, self.fmin), self.fmax)))
        step = max(1, int(round((stop - start) / (n - 1))))
        extra = int(self.s21_hack)
        return max(int(self.fmin), start - extra * step), step, n + extra

    def scan(self, start, stop, n):
        n = int(n)
        if n < 2 or n > self.max_points:
            raise PointsError("NanoVNA SAA2 sweeps 2..%d points per segment" % self.max_points)
        cfg = self.sweep_registers(start, stop, n)
        dev_start, step, npts = cfg
        extra = npts - n
        if cfg != self.cfg or not self.cache_cfg:
            # same order as NanoVNA-QT set_autosweep: start, step, points, valuesPerFrequency
            self.write_reg(REG_START, dev_start, 8)
            self.write_reg(REG_STEP, step, 8)
            self.write_reg(REG_POINTS, npts, 2)
            self.write_reg(REG_VPF, 1, 2)
            self.cfg = cfg
            # a point measured with the old settings may still land in the FIFO: clear twice
            self._clear_fifo()
            time.sleep(0.02)
        self._clear_fifo()

        rev0 = np.zeros(npts, complex)
        rev1 = np.zeros(npts, complex)
        got = np.zeros(npts, bool)
        budget = 3 * npts                      # records we accept to read before giving up
        while not got.all():
            if budget <= 0:
                raise ValueError("FIFO did not deliver all %d points (%d missing)" % (npts, (~got).sum()))
            k = int(min(FIFO_CHUNK, (~got).sum(), budget))
            self.ser.write(bytes([CMD_READFIFO, REG_FIFO, k]))
            raw = self._read_exact(32 * k, 2.0)
            rec = np.frombuffer(raw, RECORD)
            if self.check_cs:
                bad = fifo_checksum(np.frombuffer(raw, np.uint8).reshape(k, 32)) != rec["cs"]
                if self.check_cs == "auto":                  # (subclass) does this firmware send the checksum?
                    if not bad.any():
                        self.check_cs = True                 # every record matches: it does
                    elif bad.mean() > 0.9:
                        self.check_cs = False                # byte 31 is not a checksum (1/256 match by chance)
                        bad = bad & False
                    # in between: stream out of step -> error below, decided again on the next block
                if bad.any():
                    self.checksum_errors += int(bad.sum())
                    raise ValueError("FIFO checksum error on %d of %d records (stream out of step)" % (bad.sum(), k))
            budget -= k
            fwd = rec["fwd"][:, 0] + 1j * rec["fwd"][:, 1].astype(float)
            ok = (rec["idx"] < npts) & (fwd != 0)
            idx = rec["idx"][ok].astype(int)
            f0 = fwd[ok]
            rev0[idx] = (rec["rev0"][ok, 0] + 1j * rec["rev0"][ok, 1].astype(float)) / f0
            rev1[idx] = (rec["rev1"][ok, 0] + 1j * rec["rev1"][ok, 1].astype(float)) / f0
            got[idx] = True
        freqs = dev_start + step * np.arange(npts, dtype=float)
        self.stuck_points = int(np.count_nonzero(np.abs(rev0[extra:] - 1) < STUCK_TOL)) if self.ecal_check else 0
        self.last_points = n
        return freqs[extra:], rev0[extra:], rev1[extra:]

    # ---------------------------------------------------------------- screen
    def capture(self, w=None, h=None):
        """Device screen as (width, height, RGB565 little-endian bytes) - the panel's capture format."""
        if not self.caps["capture"]:
            raise NotImplementedError("Screen capture needs SAA2 firmware 1.2 or later (this one is %d.%d)" % self.fw)
        self.write_reg(REG_CAPTURE, 0)
        try:
            width, height, depth = struct.unpack("<HHB", self._read_exact(5, 2.0))
        except TimeoutError:
            self.resync()
            raise NotImplementedError("No screen-capture answer: this firmware build predates USB capture")
        if depth != 16 or not (0 < width <= 1024 and 0 < height <= 1024):
            self.resync()
            raise NotImplementedError("Unexpected screen format %dx%d, %d bpp" % (width, height, depth))
        data = self._read_exact(width * height * 2, 3.0)
        return width, height, np.frombuffer(data, ">u2").astype("<u2").tobytes()

    # ---------------------------------------------------------------- shell emulation
    def version_text(self):
        return "NanoVNA SAA2 (%s)  FW %d.%d" % (self.board, self.fw[0], self.fw[1])

    def info_text(self):
        return "\n".join([
                self.version_text(),
                "Board revision %d, device variant %d, protocol %d" % (self.hwrev, self.variant, self.proto),
                "Panel range %.0f kHz - %.1f GHz (firmware limit 4.4 GHz); UG1101 spec for %s: %.0f kHz - %.1f GHz"
                % (self.fmin / 1e3, self.fmax / 1e9, self.board, SAA2_FMIN / 1e3, self.spec_fmax / 1e9),
                "Frequency grid above 140 MHz: 6 - 12.5 kHz (ADF4350); up to %d points per segment" % self.max_points,
                "USB data are not corrected by the on-device cal: use Cal -> Correction = App",
                "TX power control: %s   Screen capture: %s"
                % ("yes" if self.caps["power"] else "no (firmware < 1.2)", "yes" if self.caps["capture"] else "no (firmware < 1.2)"),
                "Record checksum: %s   First-point S21 workaround: %s"
                % ("checked" if self.check_cs else "not available", "on" if self.s21_hack else "off"),
                "Last segment: %d of %d points stuck at S11 = 1%s"
                % (self.stuck_points, self.last_points,
                   "  <-- device not in ECAL mode: MENU > STIMULUS > CFG SWEEP > ECAL"
                   if ecal_problem(self.stuck_points, self.last_points) else " (OK)"),
            ])

    def power_command(self, a):
        if not self.caps["power"]:
            return "TX power control needs firmware 1.2 or later"
        if len(a) > 1:
            lvl = int(a[1], 0)
            if lvl not in POWER_DESC:
                return "usage: power 0..3 (3 = max)"
            self.write_reg(REG_TXPOWER, lvl)
            self.tx_power = lvl
            return ""
        lvl = self.read_reg(REG_TXPOWER)
        return "power %d = %s (used above 140 MHz)" % (lvl, POWER_DESC.get(lvl, "?"))

    def command(self, cmd, idle=3.0):
        a = cmd.split()
        if not a:
            return ""
        c = a[0].lower()
        if c == "version":
            return self.version_text()
        if c == "info":
            return self.info_text()
        if c == "sn":
            return "n/a (not exposed by the SAA2 protocol)"
        if c == "cal":
            return "" if len(a) > 1 else "raw data only - the SAA2 USB protocol never applies the device cal"
        if c in ("pause", "resume"):
            return ""
        if c == "power":
            return self.power_command(a)
        if c == "reg" and len(a) > 1:          # reg <addr> [size]
            size = int(a[2]) if len(a) > 2 else 1
            return "0x%0*X" % (2 * size, self.read_reg(int(a[1], 0), size))
        if c == "wreg" and len(a) > 2:         # wreg <addr> <value> [size]
            addr = int(a[1], 0)
            if addr in (0xEF, REG_CAPTURE, REG_DATA_MODE):
                return "wreg 0x%02X refused (use capture / disconnect instead)" % addr
            self.write_reg(addr, int(a[2], 0), int(a[3]) if len(a) > 3 else 1)
            self.cfg = None
            return ""
        if c == "simdut" and len(a) > 1 and hasattr(self.ser, "simdut"):
            self.ser.simdut(a[1])
            return ""
        if c in ("save", "recall"):
            return "%s: not available over the USB protocol of this instrument" % c
        if c == "help":
            return ("NanoVNA SAA2 / LiteVNA binary protocol - emulated commands:\n"
                    "  version  info  SN  cal  pause  resume\n"
                    "  power [0..3]           ADF4350 output level (3 = max, above 140 MHz)\n"
                    "  reg <addr> [1|2|4|8]   read a register, e.g. reg 0xF3\n"
                    "  wreg <addr> <val> [n]  write a register")
        return "%s?  (type help)" % c


# --------------------------------------------------------------------------
class SAA2Emulator:
    """pyserial-like fake NanoVNA SAA2, modelled on the firmware (main2.cpp / command_parser.cpp).

    model(f) -> (m11, m21): reflection / transmission ratios (no user cal) for an array of Hz.
    Sweeps continuously at `rate` points/s into a 127-point queue that drops new points when full;
    READFIFO streams records as they are measured; a config write restarts the sweep at index 0 and
    leaves one stale point in flight for 5 ms.  Records carry the firmware checksum.  Firmware >= 1.2
    sends fwd0 = 2^30 (normalised values, as since 2020-09-29), older ones random-phase raw waves.
    """

    def __init__(self, model, fw=(1, 3), hwrev=2, rate=1500.0, dut_setter=None, drop_fifo=0,
                 screen=(320, 240), capture=None, no_ecal_points=None, checksum=True):
        self.model = model
        self.checksum = checksum                 # firmware writes the byte-31 checksum
        # device left in "No ECAL"/"CW" mode: ecal terms only exist for the first no_ecal_points indices
        # (measured by the on-screen sweep, at other frequencies); beyond them they are zero -> S11 = 1
        self.no_ecal_points = no_ecal_points
        self.drop_fifo = drop_fifo           # ignore this many READFIFO requests (start-up miss seen on hardware)
        self.dut_setter = dut_setter
        self.rate = rate
        self.timeout = 0.05
        self.fw = tuple(fw)
        self.screen = screen
        self.has_capture = self.fw >= (1, 2) if capture is None else capture
        self.regs = bytearray(256)
        self.regs[REG_VARIANT], self.regs[REG_PROTO], self.regs[REG_HWREV] = 2, 1, hwrev
        self.regs[REG_FW_MAJOR], self.regs[REG_FW_MINOR] = fw
        self.regs[REG_TXPOWER] = 3 if self.fw >= (1, 2) else 0
        self._put(REG_START, 200e6, 8)
        self._put(REG_STEP, 1e6, 8)
        self._put(REG_POINTS, 101, 2)
        self._put(REG_VPF, 1, 2)
        self.s21_bug = self.fw <= (1, 1)
        self.usb_mode = False
        self.inbuf = bytearray()
        self.out = bytearray()
        self.fifo = collections.deque()
        self.pending = 0
        self.idx = 0
        self.t_gen = time.monotonic()
        self.inflight = None
        self.dropped = 0
        self.rng = np.random.default_rng(1)
        self.log = []                        # (opcode, address) seen, for tests

    # registers
    def _put(self, addr, value, size):
        self.regs[addr:addr + size] = int(value).to_bytes(size, "little")

    def _get(self, addr, size):
        return int.from_bytes(self.regs[addr:addr + size], "little")

    def sweep(self):
        return self._get(REG_START, 8), self._get(REG_STEP, 8), self._get(REG_POINTS, 2)

    def simdut(self, name):
        if self.dut_setter:
            self.dut_setter(name)

    def _on_exit_usb(self):
        """Hook: what the instrument does with the USB sweep registers when it goes back to its own screen."""

    def screen_pixels(self):
        w, h = self.screen
        y, x = np.mgrid[0:h, 0:w]
        return (((x * 31 // w) << 11) | ((y * 63 // h) << 5) | 7).astype(np.uint16)

    # measurement engine
    def _records(self, idxs, start, step, n):
        f = start + step * idxs.astype(float)
        m11, m21 = self.model(f)
        m11, m21 = np.asarray(m11, complex), np.asarray(m21, complex)
        if self.s21_bug:
            m21 = np.where(idxs == 0, m21 * 0.3 * np.exp(1j), m21)
        if self.no_ecal_points is not None:
            stale = 0.15 * np.exp(1j * idxs * 0.2) + (0.9 + 0.05j) * m11 / (1 - 0.2 * m11)   # wrong-frequency terms
            m11 = np.where(idxs >= self.no_ecal_points, 1.0 + 0j, stale)
        k = len(idxs)
        if self.fw >= (1, 2):
            fwd = np.full(k, 2.0 ** 30, complex)
        else:
            fwd = 2.0e6 * np.exp(1j * self.rng.uniform(0, 2 * np.pi, k))
        rec = np.zeros(k, RECORD)
        for name, v in (("fwd", fwd), ("rev0", m11 * fwd), ("rev1", m21 * fwd)):
            rec[name][:, 0] = np.clip(np.round(v.real), -2**31, 2**31 - 1)
            rec[name][:, 1] = np.clip(np.round(v.imag), -2**31, 2**31 - 1)
        rec["idx"] = idxs
        if self.checksum:
            rec["cs"] = fifo_checksum(rec.view(np.uint8).reshape(k, 32))
        return [r.tobytes() for r in rec]

    def _push(self, recs):
        for r in recs:
            if len(self.fifo) < DEVICE_QUEUE:
                self.fifo.append(r)
            else:
                self.dropped += 1           # firmware: overflow -> the new point is not queued

    def _advance(self):
        now = time.monotonic()
        if self.inflight and now >= self.inflight[0]:
            self._push([self.inflight[1]])
            self.inflight = None
        k = int((now - self.t_gen) * self.rate)
        if k > 0:
            self.t_gen += k / self.rate
            start, step, n = self.sweep()
            n = max(1, min(n, SAA2_MAX_POINTS))
            k = min(k, 4096)
            idxs = (self.idx + np.arange(k)) % n
            self.idx = int((self.idx + k) % n)
            self._push(self._records(idxs, start, step, n))
        while self.pending and self.fifo:
            self.out += self.fifo.popleft()
            self.pending -= 1

    def _config_written(self, old):
        start, step, n = old
        self.inflight = (time.monotonic() + 0.005,
                         self._records(np.array([min(self.idx, max(n - 1, 0))]), start, step, n)[0])
        self.idx = 0
        self.t_gen = time.monotonic()

    def _parse(self):
        b = self.inbuf
        while b:
            op = b[0]
            need = {CMD_NOP: 1, CMD_INDICATE: 1, CMD_READ: 2, CMD_READ2: 2, CMD_READ4: 2, CMD_READ8: 2,
                    CMD_READFIFO: 3, CMD_WRITE: 3, CMD_WRITE2: 4, CMD_WRITE4: 6, CMD_WRITE8: 10,
                    CMD_WRITEFIFO: 3}.get(op, 3)        # command_parser.cpp: unknown opcode eats 3 bytes
            if op == CMD_WRITEFIFO and len(b) >= 3:
                need = 3 + b[2]
            if len(b) < need:
                return
            pkt = bytes(b[:need])
            del b[:need]
            self.log.append((op, pkt[1] if len(pkt) > 1 else None))
            if op == CMD_INDICATE:
                self.out += b"2"
            elif op in (CMD_READ, CMD_READ2, CMD_READ4, CMD_READ8):
                size = {CMD_READ: 1, CMD_READ2: 2, CMD_READ4: 4, CMD_READ8: 8}[op]
                self.out += bytes(self.regs[pkt[1]:pkt[1] + size])
            elif op == CMD_READFIFO and pkt[1] == REG_FIFO:
                self.usb_mode = True
                if self.drop_fifo > 0:
                    self.drop_fifo -= 1
                    continue
                self.pending += pkt[2] or self._get(REG_POINTS, 2)
            elif op in (CMD_WRITE, CMD_WRITE2, CMD_WRITE4, CMD_WRITE8):
                addr, data = pkt[1], pkt[2:]
                if addr == REG_CAPTURE and self.has_capture:
                    w, h = self.screen
                    self.out += struct.pack("<HHB", w, h, 16) + self.screen_pixels().astype(">u2").tobytes()
                    continue
                if addr == REG_TXPOWER:
                    self.regs[REG_TXPOWER] = min(3, data[0])
                    continue
                old = self.sweep()
                self.regs[addr:addr + len(data)] = data
                self.usb_mode = True
                if addr == REG_FIFO:
                    self._advance()
                    self.fifo.clear()
                elif addr in (REG_START, REG_STEP, REG_POINTS, REG_VPF):
                    self._config_written(old)
                elif addr == REG_DATA_MODE and data[0] == DATA_MODE_EXIT_USB and self.fw >= (1, 1):
                    self.usb_mode = False
                    self._on_exit_usb()

    # pyserial API
    @property
    def in_waiting(self):
        self._advance()
        return len(self.out)

    def write(self, data):
        self.inbuf += data
        self._parse()
        return len(data)

    def read(self, n=1):
        self._advance()
        if not self.out:
            time.sleep(min(self.timeout, 0.005))
            self._advance()
        out = bytes(self.out[:n])
        del self.out[:n]
        return out

    def reset_input_buffer(self):
        self.out.clear()

    def close(self):
        pass
