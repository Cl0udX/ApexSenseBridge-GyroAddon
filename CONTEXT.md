# Context notes (for resuming this project in a new chat)

Paste this file's content (or just point at it) at the start of a new
conversation with Claude to resume exactly where this left off.

## Goal

Add gyroscope/accelerometer support to the virtual DualSense that
[ApexSenseBridge](https://github.com/ReynArts/ApexSenseBridge) exposes for a
Flydigi Apex 5 Elite controller, **without forking** the upstream project —
tracked as a git submodule + patches (same pattern ApexSenseBridge itself
uses for its VIIPER dependency).

## Hardware / software context

- Controller: Flydigi Apex 5 Elite (`VID_37D7&PID_2501`), connected via its
  own USB dongle/hub (not directly by cable) — confirmed working in full PS5
  DualSense mode via the dongle.
- ApexSenseBridge requires `usbip-win2 0.9.7.7` exactly (WHLK-certified;
  `0.9.7.8` is explicitly rejected upstream for BSOD risk) and `HidHide
  1.5.230`. Version mismatches from other software that also installs
  USBip cause the "USBip driver remnants... ambiguous driver upgrade"
  installer error — fixed by using the Portable package's
  `Install-Drivers.cmd`, or `ApexSenseBridgeControl.exe`'s full dependency
  removal, when the normal installer gets stuck.
- Flydigi's own official software (Flydigi Space Station) *can* do full
  DualSense including gyro when it force-activates DualSense mode for
  specific supported games (confirmed working for GTA V), but only for a
  short list of games, and it has its own bug: the controller stays stuck in
  DualSense mode after exiting the game, until a Windows restart.
- ApexSenseBridge (the open-source tool) supports far more games via its
  automatic detection, but its virtual DualSense **does not implement motion
  data at all** — confirmed by reading the actual source (see below), not
  just the docs.

## What's confirmed by reading ApexSenseBridge's source

- `src/dualsense/DualSenseInput.h` / `.cpp`: the intermediate wire format
  ApexSenseBridge hands to VIIPER is a 33-byte struct with **no gyro/accel
  fields at all**. Bytes 19–30 were unused/zero-padded — exactly enough room
  for 6× `int16_t` (gx,gy,gz,ax,ay,az).
- The VIIPER Go backend (vendored + patched under
  `third_party/viiper-patches/*.patch` inside ApexSenseBridge) already builds
  a full **64-byte real DualSense USB report** — the standard size that
  includes motion at the usual offsets. So the "pipe" to the game already
  has room for motion; only ApexSenseBridge's own capture/forwarding side is
  missing it.
- For the Apex 5 specifically, `WindowsPhysicalInputSource.cpp::parseReport`
  doesn't read any Flydigi vendor-defined HID interface at all — it falls
  back entirely to generic HID axes / XInput
  (`"backend": "xinput-fallback"` in ApexSenseBridge's own "Test APEX"
  diagnostic, documented in its `TROUBLESHOOTING.md`). Nothing in the
  codebase currently reads the vendor channel that presumably carries gyro
  data (the same channel Flydigi's own official app must be reading, since
  it works there).

## What's already built (this repo)

- `vendor/ApexSenseBridge` — git submodule, pinned to the upstream commit
  that was `HEAD` of `main` on 2026-09-05 (`73ba504...`).
- `patches/asb/0001-gyro-plumbing.patch` — scaffold. Adds:
  - `gyroX/Y/Z`, `accelX/Y/Z` (`int16_t`) to `DualSenseInputState`.
  - Writes them into bytes 19–30 of `buildViiperInput()`'s output.
  - New isolated file `src/motion/ApexMotionSource.{h,cpp}` (originally a
    no-op stub — see 0002 below, which replaces its body with the real
    implementation).
  - Registered the new `.cpp` in `CMakeLists.txt`.
- `patches/asb/0002-gyro-motion-read.patch` (2026-09-06) — the real
  implementation, once the protocol was fully reverse-engineered (see
  "Reverse-engineered" section below). Only touches files this project
  already owns/created, plus one line in `WindowsPhysicalInputSource.cpp`:
  - Rewrites `ApexMotionSource.cpp` to actually open the Apex 5's vendor HID
    interface (`usage page 0xFFA0`), send the ENABLE report once, and decode
    gyro/accel out of the continuous `0xEF` report stream each call — see
    "Reverse-engineered" section for the exact protocol. Self-contained: a
    `MotionChannel` class (RAII, function-local `static` singleton) owns the
    HID handle and sends the DISABLE report from its destructor at process
    exit, so this can never leave the pad stuck in raw-data mode. Depends
    only on unmodified upstream headers (`platform/HidTransport.h`,
    `flydigi/Apex5Protocol.h` — read-only use of existing constants, no
    edits to either file).
  - **Bug fix in 0001's hook placement**: 0001 had called
    `applyApex5Motion()` from `HidPhysicalInputSource::parseReport` (the
    `apex-hid-event` backend, for a generic HID Game Pad usage-page/usage-5
    collection). That backend is never selected for the Apex 5 in practice
    — the Apex5 has no such collection, so it always falls through to the
    `xinput-fallback` backend (`XInputPhysicalInputSource`) instead. This
    meant the original hook call was dead code and gyro would never actually
    have been populated. 0002 removes that call and adds the correct one in
    `XInputPhysicalInputSource::waitForState`, right after
    `gamepad_->poll(state, error)` succeeds.
- `scripts/apply-patches.sh`, `sync-upstream.sh`, `build.sh` — tested;
  `apply-patches.sh` resets the submodule to clean upstream HEAD and applies
  all patches in `patches/asb/` **in filename order** (0001 then 0002).

## Reverse-engineered (2026-09-05/06): the Apex 5's raw sensor protocol

Fully decoded by sniffing USB traffic (USBPcap + Wireshark/tshark) between the
Apex 5 dongle and the host, across several captures, then confirmed by
sending raw HID reports directly from a Python (`hidapi`) test script. See
"How this was captured" below for the method if it ever needs redoing (e.g.
for a firmware update or a different Flydigi model).

### Device layout (VID `0x37D7`, PID `0x2501`)

Composite USB device, 3 interfaces:
- **Interface 0**: Vendor-Specific (`0xFF`/`0x5D`/`0x01` — the well-known
  "XUSB"/Xbox-360-compatible signature). EP `0x81` IN / `0x02` OUT. This is
  what ApexSenseBridge's existing `xinput-fallback` backend already reads.
  Standard 20-byte Xbox 360 report, confirmed **no motion data ever appears
  here**, regardless of mode (see below).
- **Interface 1**: HID, Boot Interface, 1 endpoint (`0x82` IN). Stays
  completely silent/unused in every capture — not relevant to motion.
- **Interface 2**: HID, 2 endpoints (`0x83` IN, `0x03` OUT). **This is the
  one that carries the sensor data**, but only after being switched into
  "extended" mode (see below). Windows exposes it as two HID collections
  (`MI_02&Col01` usage page `0xFFA0`, `MI_02&Col02` usage page `0xFFEE`);
  only **Col01** is actually readable/writable via `hid.enumerate()`/
  `hid.device()` — Col02 errors on both read and write, ignore it.

### The mode switch — fully solved, no Flydigi Space Station needed, XInput stays alive

Interface 2 has two modes, both using the same 32-byte report on endpoint
`0x83`/`0x03`:
- **Normal/idle mode** (default on power-up): report ID `0x04` with `byte[3]`
  cycling through a handful of values (`0x01,0x04,0x07,0xa3,0xa7,0x51,...`) —
  just a slow "profile/config dump" channel (saw literal UTF-16 text
  `"Normal"` and curve tables in it once), **not** motion.
- **Extended/sensor mode**: report ID `0x04`, `byte[3] == 0xEF` on almost
  every packet, streamed continuously (measured ~1kHz on Windows; the
  reference project below measured ~300Hz on Linux — probably just a
  polling-rate difference between OSes, not a hardware limit). **This is
  where gyro/accel/sticks live.**

Initially found the trigger empirically by capturing what Flydigi Space
Station sends before the switch, but **the whole protocol turned out to
already be independently reverse-engineered and documented** by
[OpenFlydigi](https://github.com/mkaliaha/openflydigi) (MIT-licensed; already
credited in this repo's own vendored copy at
`vendor/ApexSenseBridge/THIRD_PARTY_NOTICES.md`, and its command framing
constants already exist unused in `vendor/ApexSenseBridge/src/flydigi/
Apex5Protocol.h` — `kReportIdOut=0x03`, `kReportIdIn=0x04`, `kMagic0=0x5A`,
`kMagic1=0xA5`, `kReportSize=32`, matching byte-for-byte). Its
`flydigi/motion.py` (reference commit `8477300f1bd0cdd0e4a277a544aa9b151c623e62`)
is the authoritative source for all of this — cloned locally once to check,
not vendored in this repo. Command packet framing (`flydigi/device.py:build`):
`[0]=0x03, [1]=0x5A, [2]=0xA5, [3]=cmd id, [4]=payload len, [5..]=payload`,
32 bytes total.

**The command is `CMD_ENABLE_RAW = 17` (0x11)**, a 5-flag payload
(`controller_data, raw, keyboard, mouse, third_party`, each `1`/`0`/`0xFF`
for "leave alone"), length byte hardcoded to `7`, checksum = 8-bit sum of
bytes `[3:10)` (i.e. cmd + len + the 5 flags):

```
ENABLE:  03 5a a5 11 07 01 01 ff ff ff 17  (+ 21 zero bytes = 32 total)
         controller_data=1, raw=1, keyboard/mouse/third_party unchanged
DISABLE: 03 5a a5 11 07 ff 00 ff ff ff 14  (+ 21 zero bytes = 32 total)
         controller_data unchanged, raw=0
```

Both are plain 32-byte HID **output reports**, written to the `MI_02&Col01`
HID collection (`hid.device().write(cmd)`, Python `hidapi`/`cython-hidapi`).
Confirmed reliable over multiple enable/disable cycles: no USB
re-enumeration, no Space Station involvement at all.

**Critical fix vs. the first empirical attempt:** replaying Space Station's
own captured command verbatim (`controller_data=0, raw=1` — byte 5 `=0x00`)
made **interface 0 (XUSB/XInput) go completely stale** while extended mode
was on (`XInputGetState`'s packet counter froze). Sending the command with
**`controller_data=1`** instead (leave it on, per OpenFlydigi's own
`motion.enable()`) fixes this entirely — **confirmed by test: `XInputGetState`
kept incrementing normally (buttons/sticks responsive) for the whole time
the sensor report was also streaming `0xEF` frames.** OpenFlydigi's own code
comment says exactly this ("Verified on hardware: enabling raw data does NOT
disturb the xpad node") — their Linux relay reads buttons/sticks from evdev
(the Linux equivalent of XInput) and motion from the vendor interface
*simultaneously*, never from the same report. **So: no need to decode
buttons/sticks/dpad from the extended report at all — keep using
ApexSenseBridge's existing `xinput-fallback` backend unchanged for those, and
only add the interface-2 HID read for gyro/accel.** (OpenFlydigi's
`motion.py` does document sticks at offsets 4/6/8/10 too, as a curiosity/
future-proofing note, in case a future ApexSenseBridge design wants to read
everything from one source — see byte layout below — but it's not needed
for this project's goal.)

A disable-good-citizenship note: Flydigi Space Station's own stuck-in-
DualSense-mode bug (needs a full Windows restart to clear) is presumably
because it never sends an equivalent of this DISABLE command on exit —
ApexSenseBridge sending DISABLE on cleanup avoids replicating that bug.

### Sensor report byte layout (32-byte payload, extended mode, `byte[3]==0xEF`)

Per OpenFlydigi's `flydigi/motion.py` (offsets confirmed independently for
gyro/accel by this project's own isolated-axis/gravity tests too):

```
byte 0        : report ID, 0x04
byte 1        : constant 0x5a
byte 2        : constant 0xa5
byte 3        : 0xef (mode marker)
bytes 4,6,8,10: int16 LE each — left X, left Y, right X, right Y sticks
                (documented by OpenFlydigi; NOT needed by this project since
                sticks/buttons keep coming from interface 0/XInput — see above)
bytes 12-17   : buttons/dpad/triggers, presumably (not documented by
                OpenFlydigi either — "nothing reads them yet" per their
                comment — and not needed here for the same reason)
bytes 18-19   : int16 LE — Pitch gyro rate  (this project's own finding:
                dominant signal when nodding the trigger-edge toward/away
                from you)
bytes 20-21   : int16 LE — Roll gyro rate   (dominant signal when rocking
                left-edge/right-edge up-down, control held normally)
bytes 22-23   : int16 LE — Yaw gyro rate    (dominant signal when swiveling
                flat, keeping it level, left/right)
bytes 24-25   : int16 LE — Accel, left/right body axis. Sign: positive when
                tilted with the RIGHT edge down. ~4096 LSB = 1g
bytes 26-27   : int16 LE — Accel, trigger/grip body axis. Sign: positive
                when triggers point down / grips point up. ~4096 LSB = 1g
bytes 28-29   : int16 LE — Accel, button-face body axis. Sign: positive when
                the button face points up. ~4096 LSB = 1g (reads ~+4096 at
                rest in the normal "sitting on a desk, buttons up" pose)
bytes 30-31   : not analyzed, low-variance (likely padding/checksum-ish)
```

Gyro sign convention (which rotation direction is positive on each axis) was
**not** determined by this project — OpenFlydigi's own comment says the same
("Gyro is left at 1.0 by default... there is no reference to check Flydigi's
LSB-per-deg/s against, so this is the one value worth tuning by feel").
Should be tuned empirically once wired into `applyApex5Motion()` (flip if
camera/aim moves opposite of expected in-game).

**Accel scale** (from OpenFlydigi, matches this project's own ~4096-per-g
measurement): Apex 5 reports ~4096 raw units per g; the real DualSense
calibration blob implies ~10000 raw units per g (`hid-playstation`/`inputtino`
convention). So to make a game that expects real-DualSense-scaled input see
the right g-force: `accel_out = accel_raw * (10000.0 / 4096.0)` (≈ `* 2.441`).
Gyro scale: no authoritative reference on either side; OpenFlydigi defaults
to `1.0` (passthrough) and says to tune by feel — same recommendation here.

### How this was captured (in case it needs redoing)

1. USBPcap (bundled with Wireshark for Windows) capturing on the interface
   the Apex 5 dongle enumerates under (found by trial: capture on each
   `USBPcapN`, power the controller on/off, see which one shows traffic).
2. `tshark` (same Wireshark install, `C:\Program Files\Wireshark\tshark.exe`,
   not on PATH) was used for all analysis instead of the Wireshark GUI —
   much faster to filter/extract than manual clicking. Useful filters:
   `usb.device_address==N`, `usb.endpoint_address==0x83`,
   `usb.transfer_type==0x01` (interrupt). USBPcap's own packet header is 27
   bytes (`headerLen` field at byte 0-1 of the raw frame) before the actual
   USB payload starts — `usb.capdata` doesn't populate for this device's
   HID-classified endpoints (Wireshark's HID dissector swallows it trying to
   apply the report descriptor), so raw `-x` hex dump + manual header-strip
   was used instead of relying on dissected fields.
3. To find which axis is which: isolated single-axis-at-a-time motion tests
   (described in body-frame terms — "tilt the trigger edge toward/away from
   you", not room-frame terms like "yaw" — an earlier attempt using
   room-frame descriptions and asking to reorient the controller flat first
   produced ambiguous/overlapping results, since reorienting the device
   itself is a rotation that contaminates the next measurement).
4. For gravity/accel axis identification: hold the controller still for ~2s
   in each of the 6 cube-face orientations (button-face up/down, left/right
   edge down, trigger-edge up/down) and see which raw axis reads ±4096.
5. To test the enable/disable commands directly: Python + `pip install
   hidapi` (the `cython-hidapi`/trezor package — NOT the pure-python `hid`
   PyPI package, which needs a separate native `hidapi.dll` this machine
   doesn't have; if both get installed, `pip uninstall hid` to stop it
   shadowing the working one). `hid.enumerate()` /
   `hid.device().open_path(...)` / `.write(bytes)` / `.read(64,
   timeout_ms=...)`. HidHide does *not* need to be disabled for this — it
   wasn't cloaking the device since ApexSenseBridge wasn't running.

## What's NOT done yet (the actual remaining work)

The C++ implementation is **written** (`patches/asb/0002-gyro-motion-read.patch`,
2026-09-06) and both patches apply cleanly in sequence
(`scripts/apply-patches.sh` verified end to end). It has **not** been
compiled or run yet — no C++ toolchain (no `cmake`/`cl`/`g++`) was available
in the environment this was written in. What's left:

1. **Build it.** Run `scripts/build.sh` (or open the CMake project in Visual
   Studio) and fix whatever the compiler flags — the code was written
   carefully against the exact existing APIs (`platform::HidTransport`,
   `flydigi::Apex5Protocol.h` constants) but is unverified by a real
   compiler. Likely trouble spots if something's off: the
   `std::array`→`std::span<const std::uint8_t>` implicit conversion in
   `writeOutputReport(buildRawDataReport(...), ...)` calls, or
   `std::vector<std::uint8_t>`→`std::span<std::uint8_t>` for
   `readInputReport(buffer_, ...)`.
2. **Test on real hardware**: run the bridge, confirm
   `src/motion/ApexMotionSource.cpp`'s `MotionChannel` actually finds and
   opens the vendor interface (it should — no Space Station needed), and
   that XInput buttons/sticks keep working exactly as before (this was the
   whole point of using `controller_data=1` in the enable command).
3. **Decide the gyro sign convention empirically**, in an actual game with
   gyro-aim: if camera/aim rotation feels inverted on an axis, flip the sign
   of that axis in `applyApex5Motion()` (or right in `decodeMotionReport()`).
   Nothing else here should need to change for that tuning.
4. Nothing else — buttons/sticks/dpad/triggers were already fine via the
   existing `xinput-fallback` backend and needed no changes.

### Item 4 (old) — RESOLVED (2026-09-05): VIIPER already forwards bytes 19-30 end to end

Confirmed by cloning the pinned upstream VIIPER source (tag `v0.7.0`, commit
`6b71b148a2243fab77ee1a46f4e22e00bd7d5a04`, matches
`scripts/build-viiper-070-windows.ps1`) and reading it directly — **no
additional VIIPER-side patch is needed**:

- `device/dualsense/state.go`: the base (unpatched, upstream) `InputState`
  struct already has `GyroX/Y/Z`, `AccelX/Y/Z int16` fields — this predates
  ASB's patch entirely.
- `third_party/viiper-patches/viiper-v0.7.0-asb.patch`'s
  `unmarshalASBInputState()` reads those 6 `int16`s from bytes 19-30 of
  ApexSenseBridge's 33-byte intermediate report and populates exactly those
  `InputState` fields.
- `device/dualsense/device.go::buildUSBInputReport()` (upstream, untouched by
  the ASB patch) unconditionally packs `s.GyroX/Y/Z` into bytes 16-21 and
  `s.AccelX/Y/Z` into bytes 22-27 of the real 64-byte USB DualSense input
  report — the standard motion offsets.

So the full pipe already exists end to end: bytes 19-30 written by
`applyApex5Motion()` → VIIPER's `InputState` → real DualSense report bytes
16-27. **The only remaining file to change for the whole feature is
`src/motion/ApexMotionSource.cpp`** (steps 1-3 above).

## How to resume

Paste this file into a new chat and say what you want to do next — e.g.
"I have a capture, help me decode it" or "let's check whether VIIPER already
forwards bytes 19-30". No need to re-explain the USBip/HidHide saga or the
controller mode-switching issues above; they're resolved and not relevant to
the gyro work.
