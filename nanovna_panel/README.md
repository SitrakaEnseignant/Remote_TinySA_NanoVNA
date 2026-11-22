# NanoVNA Front Panel (NanoVNA-F V3 / NanoVNA-H4 / NanoVNA SAA2 / LiteVNA 64)

![screenshot](../docs/nanovna.png)

Bench-VNA style Windows remote panel for the SYSJOINT NanoVNA-F V3 and the NanoVNA-H4 (and other NanoVNA-shell
devices), and for the NanoVNA SAA2 and the LiteVNA 64 through their binary protocol (see below).

## Run
The panel starts as "NanoVNA" with **Model = Auto**. The instrument is then shown in the title, the logo and the
range box:
- **Model list** (below the USB port; NanoVNA-H4 support from upstream PR #2 by StrontiumD): choosing NanoVNA-F V3,
  NanoVNA-H4, NanoVNA SAA2 or LiteVNA 64 applies its name, frequency range, preset sweep and screen size.
- **On connection**: a SAA2 or a LiteVNA identifies itself (same USB id, port list "NanoVNA SAA2 / LiteVNA") and the
  Model list follows it. The F-V3 and the H4 share a USB id and a protocol: the Model list decides; on Auto the
  `version` reply does (F-V3 when it does not say).

1. Install Python 3.10+ (tick "Add to PATH").
2. `run.bat` connects to the NanoVNA (auto-detected, VID 0483 / PID 5740).
   `run_simulator.bat` runs without hardware (a 1 GHz 3-pole bandpass DUT with realistic port errors).
   `build_exe.bat` makes `dist\NanoVNA_Panel.exe`.

Command line: `python vna_panel.py [--sim | --sim-saa2 | --sim-lite] [--port COM9] [--saa2 | --litevna]`

## Panel
- **Keys**: System / Auto (autoscale all) / Preset; CONTROL: Freq, Avg BW, Trace, Display, Port Ext, Sweep, Trigger, Scale;
  MEASUREMENT: Meas, Marker, Format, Search; CAL & MATH: Cal, Math. Menus appear on the right of the screen (F1-F8 too).
- **Knob**: hover + mouse wheel or drag. ◀ ▶ = fine, ▲ ▼ = coarse. Keypad or PC keyboard + unit softkey / Enter.
- **Traces**: up to 4, each S11 or S21 in LogMag, Phase, Group Delay, Smith, Polar, SWR, LinMag, Real, Imag,
  Unwrapped Phase, |Z|, R, X, TDR. Click a panel to select it; click its plot to drop the active marker.
- **Markers**: 4, readout per trace (Smith shows R+jX and equivalent L/C). Search: max/min/peak next/target,
  tracking, and -N dB bandwidth with center frequency and Q.
- **Math**: Data→Mem, Data/Mem, display Data/Mem/both, Touchstone file into memory, smoothing.
- **Port Ext**: electrical delay (Auto Delay from phase slope), phase offset, velocity factor for TDR.
- **Files**: S1P, S2P (S12/S22 written as 0), CSV, PNG, device screen capture, raw console.

## Calibration
- **Correction = Device**: uses the NanoVNA's own calibration (`cal on`). Recall/save device slots 0-6 from the Cal menu.
- **Correction = App**: the device is put in `cal off` and the app corrects raw data:
  Open, Short, Load on port 1; Thru port 1→2; optional Isolation (load both ports); then Done/Apply.
  Each standard averages *Cal Averages* sweeps. Ideal standards are assumed (no cal-kit coefficients).
  Narrowing the sweep inside the cal range interpolates the error terms (status shows "interp").
  Save/Load Cal stores the raw standards (.npz).
- Error model: 3-term one-port + enhanced-response transmission (the NanoVNA is a 1.5-port instrument,
  so port-2 match is only partly corrected; that is the same limit the firmware has).

## Protocol notes
- `scan start stop points 7` → text lines `freq s11re s11im s21re s21im`. Older firmware without the mask
  falls back to `data 0` / `data 1` / `frequencies`.
- Sweeps longer than the device allows are split into segments (Sweep → Segment, default 201;
  it drops to 101 automatically if the device rejects 201).
- `capture` is read as 800x480 RGB565 little-endian.

## NanoVNA SAA2 (NanoVNA V2 family: V2_2, V2 Plus, V2 Plus4)
The SAA2 has no text shell: `saa2_device.py` speaks its binary register protocol and presents the same
interface as the shell transport, so the whole panel works on top of it. Checked against the NanoVNA2
firmware source (`command_parser.cpp`, `main2.cpp`), the UG1101 manual (Appendix II) and NanoVNA-QT's `libxavna`.
- **Detection**: USB VID 04B4 / PID 0008 (also the Windows `PORTS\VID_04B4&PID_0008` quirk), shown as
  "NanoVNA SAA2". Unknown ports are probed (CR = INDICATE answers `2`). `--saa2 --port COMx` forces it.
- **Frequency**: registers sweepStartHz (0x00), sweepStepHz (0x10), sweepPoints (0x20), valuesPerFrequency (0x22),
  written in the same order as NanoVNA-QT; point i is measured at start + i*step (above 140 MHz the ADF4350
  rounds to a 6-12.5 kHz grid). Up to 1024 points per segment; longer sweeps are split.
- **Range**: 50 kHz - 4.4 GHz (Full Span) = the firmware limit. UG1101 specifies 3 GHz for V2_2 / V2 Plus
  and 4.4 GHz for V2 Plus4 (Device Info shows the board). The NanoVNA-F V3 keeps its own 1 MHz - 6 GHz.
- **Data**: FIFO 0x30 cleared before each sweep (the device queue holds 127 points), 32-byte records,
  S11 = rev0/fwd0, S21 = rev1/fwd0, placed by freqIndex, checksum byte verified (out-of-step stream -> resync).
- **Calibration**: the on-device cal is never applied over USB, so Correction = Device is disabled:
  Cal → Open / Short / Load / Thru (+ Isolation) → Done/Apply, then Save Cal.
- **Power**: Port Ext → Power cycles the ADF4350 level (register 0x42: max, -3, -6, -9 dB, above 140 MHz);
  the level set on the device is read at connect (firmware 1.2+).
- **Screen capture**: System → Dev Screen (register 0xEE, firmware 1.2+, 320x240 or 480x320).
- **Disconnect**: the panel writes dataMode = 2 so the device returns to its own screen (firmware 1.1+).
- **Not in the USB protocol**: device save/recall slots, serial number.
- **Console**: `help`, `info`, `power 0..3`, `reg <addr> [size]`, `wreg <addr> <value> [size]`.
- `run_simulator_saa2.bat` (or `--sim-saa2`) runs the panel on a byte-level SAA2 emulator built from the
  firmware behaviour, with the same DUT and port-error model as the shell simulator.

## LiteVNA 64 (and 62)
Same binary protocol as the SAA2 ("for compatibility with NanoVNA-App", LiteVNA User Guide, USB appendix). The code is
in `litevna64_device.py`: `LiteVNA64Transport` and `LiteVNA64Emulator` subclass the SAA2 classes of `saa2_device.py`
and override only what differs; `vna_device.open_v2()` reads the identity registers and picks the class.
Checked against that appendix and NanoVNA-Saver's `LiteVNA64` driver.
- **Identification**: same USB id as the SAA2; told apart at connection by its firmware registers (variant 2,
  firmware 2.x - the SAA2 firmware stops at 1.x). `--litevna --port COMx` forces it.
- **Range**: 50 kHz - 6.3 GHz (Full Span); 1024 points per segment (the guide's USB limit), longer sweeps are split.
- **Sweep registers are written for every sweep**: the LiteVNA forgets them when it goes idle.
- **FIFO checksum**: bytes 26..31 are "reserved" in the LiteVNA guide; the SAA2 checksum is used only if the
  firmware sends it (decided on the first block).
- **Calibration**: USB data are raw (Correction = Device disabled) - calibrate in the panel.
- **Device Info** shows the battery voltage (register 0x5C). **Dev Screen** captures the 480x320 screen.
- **Not offered**: TX power control (no register in the guide), device save/recall slots.
- The panel shows "LiteVNA 64" (a LiteVNA 62 cannot be told apart over USB and gets the same name).
- `run_simulator_litevna.bat` (or `--sim-lite`) runs the panel on the emulator with the LiteVNA profile.

## Troubleshooting (SAA2): the DUT does not change the trace
- **Connector first.** The VNA ports are female (SMA jack). A female antenna (SMA jack, or RP-SMA jack) cannot make
  contact: use a male-male adapter of the right type, and calibrate at the free end of that adapter.
- **Device not in ECAL mode.** Over USB the SAA2 corrects S11 with its internal "ecal" terms and re-measures them for
  the USB sweep only in ECAL mode. In "No ECAL" or "CW" mode (CW FREQ on the device selects CW, and it stays) part of
  the sweep returns S11 = 1 whatever is connected. The panel detects it (12 s message, red "SAA2 not in ECAL mode"
  tag, count in Device Info). Fix on the SAA2: **MENU > STIMULUS > CFG SWEEP > ECAL**, then reconnect.

## Tests (no hardware, no Qt needed)
`python tests/test_fv3_parity.py . <original nanovna_panel dir>` checks that a NanoVNA-F V3 receives exactly the same bytes
as with the original code (prompts "ch> " and firmware 0.6.0's "ch>"); `test_firmware_conformance.py`, `test_saa2.py`, `test_detect.py` and `test_startup_retry.py`
(argument: this folder) cover the SAA2 protocol against the firmware behaviour, port detection and the silent start-up retry.
`test_ecal_mode.py .` covers the ECAL-mode detection, `test_litevna.py .` the LiteVNA profile.
`test_panel.py . saa2`, `. lite`, `. fv3` and `. h4` (H4 chosen in the Model list) run the real panel headless (Qt replaced by `tests/qt_headless.py`) on the
SAA2 or LiteVNA emulator, or the F-V3 simulator: every softkey and option, calibration, exports, trigger, power, capture, disconnect.
