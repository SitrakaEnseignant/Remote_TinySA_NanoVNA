"""
LiteVNA 64 (and 62) device layer - Zeenko / hugen79.

The LiteVNA speaks the NanoVNA SAA2 binary register protocol "for compatibility with NanoVNA-App"
[LiteVNA User Guide, Appendix: USB data interface] and enumerates with the same USB id (04B4:0008),
so this module reuses saa2_device (V2Link / SAA2Transport / SAA2Emulator) and only overrides what differs.
vna_device.open_v2() reads the identity registers and picks LiteVNA64Transport when is_litevna() says so.

Differences from the SAA2:
  - identification: NanoVNA-Saver (Hardware/LiteVNA64.py, is_lite_vna_64) expects device variant 2,
    hardware revision 2 and firmware 2.2; SAA2 firmware stops at 1.x, so firmware major >= 2 = LiteVNA;
  - range 50 kHz - 6.3 GHz; up to 1024 points per USB sweep in the guide (later firmware: 65535),
    so the panel sweeps in 1024-point segments;
  - FIFO bytes 26..31 are "reserved" in the guide: the SAA2 checksum (byte 31) is used only if this
    firmware sends it - decided on the first FIFO block;
  - the device loses the sweep registers when it goes idle (NanoVNA-Saver note): written for every sweep;
  - battery voltage: register 0x5C, READ2, millivolts (NanoVNA-Saver);
  - screen capture: register 0xEE as on the SAA2 (480x320 on a LiteVNA 64);
  - no TX power register in the guide: power control is not offered; the SAA2 "ecal" check does not apply.
A LiteVNA 62 cannot be told from a 64 over USB; both are shown as "LiteVNA 64".
"""
import numpy as np

import saa2_device as sd

LITE_FMIN, LITE_FMAX = 50e3, 6.3e9
REG_VBAT = 0x5C
LITE_FW = (2, 2)                     # firmware NanoVNA-Saver expects from a LiteVNA 64


def is_litevna(identity):
    """identity = V2Link.identify() -> (variant, protocol, hw rev, (fw major, minor))."""
    variant, _proto, _hwrev, fw = identity
    return variant == 2 and fw[0] >= 2


class LiteVNA64Transport(sd.SAA2Transport):
    model = "litevna"

    def _setup(self):
        self.fmin, self.fmax = LITE_FMIN, LITE_FMAX
        self.spec_fmax = LITE_FMAX
        self.board = "LiteVNA 64"
        self.s21_hack = False
        self.check_cs = "auto"               # decided by the first FIFO block
        self.cache_cfg = False               # sweep registers are lost when the device idles
        self.ecal_check = False
        self.tx_power = None
        self._make_caps("FW %d.%d" % self.fw, capture=True, power=False)

    def version_text(self):
        return "LiteVNA 64  FW %d.%d" % self.fw

    def battery_volts(self):
        try:
            return self.read_reg(REG_VBAT, 2) / 1000.0
        except TimeoutError:
            self.resync()
            return None

    def info_text(self):
        v = self.battery_volts()
        cs = {True: "present, checked", False: "not sent by this firmware", "auto": "not seen yet"}[self.check_cs]
        return "\n".join([
            self.version_text(),
            "Device variant %d, protocol %d, hardware revision %d" % (self.variant, self.proto, self.hwrev),
            "Range %.0f kHz - %.1f GHz; up to %d points per segment" % (self.fmin / 1e3, self.fmax / 1e9, self.max_points),
            "USB data are not corrected by the on-device cal: use Cal -> Correction = App",
            "Battery: %s" % ("%.2f V" % v if v else "n/a"),
            "Screen capture: yes   TX power control: not documented for the LiteVNA",
            "Record checksum: %s" % cs,
        ])

    def power_command(self, a):
        return "TX power control is not documented for the LiteVNA"


class LiteVNA64Emulator(sd.SAA2Emulator):
    """SAA2 emulator with the LiteVNA 64 differences: firmware 2.2, 480x320 screen, no checksum by default,
    battery register, and USB sweep registers lost when the device goes back to its own screen."""

    def __init__(self, model, **k):
        k.setdefault("fw", LITE_FW)
        k.setdefault("screen", (480, 320))
        k.setdefault("capture", True)
        k.setdefault("checksum", False)
        super().__init__(model, **k)
        self.regs[sd.REG_TXPOWER] = 0
        self._put(REG_VBAT, 3950, 2)

    def _on_exit_usb(self):
        self._put(sd.REG_START, 50e3, 8)
        self._put(sd.REG_STEP, 63e6, 8)
        self._put(sd.REG_POINTS, 101, 2)
