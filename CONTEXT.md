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

## Build/run status (2026-09-06): confirmed working end to end on real hardware

The C++ toolchain is already installed on this machine (Visual Studio 2026
Build Tools, `C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools`)
even though it's not on PATH — `vswhere.exe` finds it, `cl.exe`/bundled
`cmake.exe` are under that tree. To build from a plain shell (not a
Developer Prompt), call `vcvars64.bat` then the bundled cmake, e.g.:
```
call "...\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
"...\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe" -S vendor/ApexSenseBridge -B build -G "NMake Makefiles" -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
"...\cmake.exe" --build build --target ApexSenseBridge
```
(`scripts/build.sh` does the same thing but needs `cmake`/`cl` already on
PATH, i.e. a Developer Command Prompt — the above is the from-scratch
version.) **Compiles clean, no errors, no warnings** — confirmed for
`asb_core`, `ApexSenseBridge`, and `ApexSenseBridgeControl`.

Confirmed live with a real Apex 5: buttons/sticks work via `xinput-fallback`
exactly as before, and gyro/accel flow correctly at the same time — no
XInput freeze, matching what the `controller_data=1` fix promised.

### The Tray app (`ApexSenseBridgeTray`) — also in this same submodule, C#/WPF

Automatic per-game detection (the "206+ supported games" feature) lives
**entirely in the Tray app, not the CLI** — `ApexSenseBridge.exe
bridge-triggers` never does game detection on its own, you always give it an
explicit index. The Tray's source is right here too, just not C++:
`vendor/ApexSenseBridge/ApexSenseBridgeTray/` (WPF, .NET Framework 4.6.2),
built via `scripts/build-tray-app.ps1` (calls MSBuild, then runs
`tests/ApexSenseBridgeTray.LearningTests.csproj` as a regression check).
Confirmed `EngineSessionManager.cs` just shells out to `ApexSenseBridge.exe
bridge-triggers ...` as a subprocess — **our patched CLI is a drop-in
replacement, the Tray itself needed zero gyro-related changes**.

Building it needs one extra component beyond the C++ workload: the **.NET
Framework 4.6.2 targeting pack** (Visual Studio Installer → Modify →
Individual Components → search ".NET Framework 4.6.2" — easy to
mis-click "4.6" or "4.6.1" instead, they're listed right next to each
other; the reference assemblies land at `C:\Program Files
(x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.6.2\` when
it's actually installed).

`ApexSenseBridgeTray`'s own `InstallLocator.cs` looks for
`ApexSenseBridge.exe`/`ApexSenseBridgeControl.exe` in (in order): the
registry (`HKLM\Software\ApexSenseBridge`), `%ProgramFiles%\ApexSenseBridge`,
**its own directory**, then sibling `build-win\Release`/`build-verify\Release`/
`dist` folders relative to its own location. So the simplest way to run our
patched build end-to-end: put `ApexSenseBridge.exe`,
`ApexSenseBridgeControl.exe`, `ApexSenseBridgeTray.exe`, and (from the
official Portable download, no need to build VIIPER from source)
`viiper.exe` + `libVIIPER.dll`, all in one folder, and run the Tray from
there. **Don't use `vendor/ApexSenseBridge/dist/` as that folder long-term**
— `apply-patches.sh` runs `git clean -fd` on the submodule and will delete
anything untracked living inside it, gyro build included. Copy the whole
set somewhere outside the submodule instead (this session used
`C:\Users\Santiago\Documents\ApexSenseBridge-GyroBuild\`).

### Two issues found testing the Tray, both diagnosed from its own log files

The Tray writes `tray_detection.log` / `tray_bridge.log` / `tray_crash.log`
next to itself — read these first for any future issue, they're detailed.

1. **Fixed** (`patches/asb/0003-tray-title-match-boundary-fix.patch`):
   `CloudGameListService.cs::GetMatchScore` matched game titles as a plain
   substring anywhere inside a candidate process name, with no word-boundary
   check (both strings are pre-normalized by stripping all
   non-alphanumeric characters, so no separators survive to check anyway).
   Observed for real: a Visual Studio Code background process
   (`...ServiceController.exe`) false-matched the game **"Control"**
   (`"...servicecontroller".Contains("control")`), and the activation
   policy then ignored every other real game detected afterward because it
   believed "Control" was already the active session. Fix: require the
   fragment to match at the **start or end** of the candidate/game string
   (`StartsWith`/`EndsWith` instead of `Contains`), which rejects
   mid-word matches like this one while keeping legitimate prefix/suffix
   matches (verified with a standalone reflection-based harness against 6
   cases — the bug case, a similar mid-word case, and 4 legitimate
   prefix/suffix matches — all correct; also reran the existing 80-assertion
   regression suite, still green). This is pure upstream Tray logic, unrelated
   to gyro — patched anyway since the user asked, kept as its own patch file
   for the same reason 0002 is separate from 0001.
2. **Not a bug — expected behavior**: forcing the bridge on while a game is
   *already running* still shows "Xbox" to that game, not "DualSense", until
   the game is restarted. Most games only enumerate controllers once at
   their own startup; HidHide swapping which device is visible mid-session
   doesn't make an already-running game re-scan. Confirmed this is a game-side
   limitation, not fixable here: even with two physical controllers connected,
   the second one did nothing once the first was hidden mid-session either.
   Even Flydigi Space Station's own official DualSense-forcing has the exact
   same constraint (activates on game *launch*, never mid-session) — so this
   is the expected, unavoidable workflow: **start the bridge (auto-detect or
   forced) before launching the game, not after.**
3. **Actionable, not yet applied**: `tray_crash.log` showed `WMI StartWatcher
   unavailable: Acceso denegado`. `ProcessMonitorService.cs` prefers an
   instant WMI process-start event (`Win32_ProcessStartTrace`, needs
   Administrator) and falls back to slower polling when that's denied
   (confirmed matching detections logged `Source: poll`, not `Source: WMI`).
   Since some games scan for controllers within their first second alive,
   losing time to polling meaningfully hurts the odds of the bridge being
   ready before that scan happens. **Run `ApexSenseBridgeTray.exe` as
   Administrator** to get the instant path — not guaranteed to win the race
   for every game (bridge init still takes ~1s), but removes one real,
   avoidable source of lag. Not yet confirmed whether the user tried this.
4. **Fixed** (`patches/asb/0004-tray-learning-exact-only.patch`): found while
   investigating why "Control" kept appearing detected by default — the
   Tray's persistent "Learned executables" cache (`ExecutableLearningService`)
   had an old entry mapping a third-party `XBOX360_Controller.exe` (an
   unrelated tool the user has, at `C:\Users\Santiago\Documents\
   XBOX360_Controller-2.8\`) to the game "Control", learned before patch 0003
   existed (`"XBOX360_Controller"` normalizes to `"...controller"`, which
   genuinely does start with `"control"`, so it still passes the 0003
   boundary check — a real fuzzy match, just not the *right* game). Once
   "learned" (stable for a while), a mapping is trusted forever with no
   re-evaluation, so this one silently blocked all other detection via the
   same "one active game at a time" policy as issue 1, and the only fix was
   opening the Tray's own "Learned executables" screen and deleting the row
   by hand.
   Root cause fixed at the source: `TryResolveGame` in
   `ProcessMonitorService.cs` now also returns whether the match was
   high-confidence (exact title/executable identity) or the fuzzy
   prefix/suffix fallback (`CloudGameListService.TryFindGame`), and
   `HandleGameDetected` only calls `learningService.BeginObservation(...)`
   (the entry point into the permanent cache) when it was exact. A fuzzy
   match still activates the bridge for that session same as before — it
   just never gets memorized, so a wrong guess can't outlive its own
   session. Verified: still compiles clean, still passes the existing
   80-assertion regression suite (no test exercises this specific gate, so
   nothing to update there). The stale "Control" entry itself still needs a
   one-time manual delete from "Learned executables" — the fix only stops
   *new* bad entries from being created.

## Item 3 revisited: "run as Administrator" didn't actually fix the race, and here's why

Tested directly on real hardware (Tray running elevated, GTA V fully closed
first, Tray left running ~6 minutes before launching the game fresh — the
clean test case, confirmed via `Get-CimInstance Win32_Process` timestamps
that this really was a fresh process launched well after the Tray started
watching). Result: **still `Source: poll` in `tray_detection.log`, and the
game still started in Xbox mode.**

Root cause: `tray_crash.log` had **no new WMI failure for that session** —
WMI genuinely was enabled this time (admin did fix the "Acceso denegado").
But `ProcessMonitorService.cs` also runs a plain 250ms poll timer
unconditionally (`pollTimer = new Timer(OnPollTick, null, 100, 250)`,
constructor around line 52), and both mechanisms funnel into the same
`CheckCandidateProcess`. `Win32_ProcessStartTrace` (the WMI event class used)
is ETW-backed and its own delivery latency is not sub-250ms in practice, so
the poll timer keeps winning the race to notice the new process even with
WMI fully working. **"Run as Administrator" is not the fix it looked like —
it doesn't move the needle on this specific latency problem.** (It may still
be worth leaving enabled for its own sake, just don't expect it to change
detection speed.)

The dominant, actually-provable cost is the ~900ms-1s of genuine bridge
*startup* work that happens **after** a candidate is detected — see the next
section. That's the real lever, and unlike detection speed, it's provably
reducible.

## Startup-latency investigation and fix (2026-09-06, patch 0005)

Ran a parallel investigation (4 agents, one per cost center) against the
real measured breakdown (`initialization_physical_input_ms=334`,
`initialization_virtual_input_ms=122`, `initialization_firmware_ms=422`,
`initialization_isolation_ms=33`, in `vendor/ApexSenseBridge/src/cli/
BridgeCommand.cpp`'s own end-of-session stats). Key findings, most
important first:

- **`firmware_ms=422` (the single biggest chunk) is 100% diagnostic, zero
  correctness impact.** Confirmed by grepping every consumer of
  `virtualFirmware`/`DualSenseFirmwareInfo` in the whole codebase: only two,
  both just `std::cout` lines ("firmware verified/obsolete" banner, and an
  end-of-session stats dump). Nothing branches on it. It was blocking
  `physicalIsolation.activate()` (the step that actually flips HidHide and
  determines what the game sees) for no reason — the two are fully
  independent (disjoint HID paths/handles).
- `virtual_input_ms=122` is mostly a **measurement artifact**: the actual
  libVIIPER device-creation call is ~7ms (matches the separately-logged
  `backend_initialization_*_us` counters, which sum to ~6.7ms); the rest of
  that bucket is `snapshotDualSensePaths()` (a full HID re-enumeration) and
  `audioProtection.capture()` folded in by where the timestamps happen to
  fall, not anything intrinsic to "creating the virtual DualSense."
  Not touched by this patch — real but lower-value, left for later.
- `isolation_ms=33` includes a **synchronous watchdog process spawn**
  (`CreateProcessW`, fatal-on-failure) that's redundant with an
  already-written `RunOnce` registry recovery marker. Real, but moot once
  firmware runs in parallel — see below. Not touched by this patch.
- Confirmed (with code citations, not just assumption) that the physical
  controller **must never be permanently hidden** — `HidHide`'s
  whitelist model blocks *every* non-whitelisted process/API path, so an
  always-hidden physical controller would break unsupported games, Steam
  Input, Windows Game Bar, and even Flydigi Space Station's own config UI.
  The class is deliberately named `TemporaryPhysicalControllerIsolation`
  with an RAII auto-restore destructor — this is a hard constraint, not
  caution to relax.
- The "keep everything pre-warmed" big redesign (persistent process owning
  a continuously-open virtual DualSense, handed off per game session) is
  technically *feasible* (proven safe to sit open+idle by the existing
  `virtual-ds` diagnostic command) but needs a real feedback-handler rebind
  API (currently bind-once at `open()`), splitting device-lifetime stats
  from session-lifetime stats, and reworking the firmware "new device"
  diffing to run once instead of per-launch — a genuine mini-redesign
  touching many files, for a marginal gain **on top of** what the
  parallelization fix below already captures. Decided against it for now,
  consistent with keeping upstream-conflict risk low; revisit only if
  the fix below still isn't enough to reliably beat a specific game.

**Fix applied** (`patches/asb/0005-parallelize-firmware-check.patch`,
`src/cli/BridgeCommand.cpp` only): `readNewVirtualDualSenseFirmware(...)` is
now launched via `std::async` (mirroring the existing
`audioProtectionFuture` pattern already in this same function) immediately
after `audioProtectionFuture`, instead of blocking right there. Everything
that used to wait for it — the `verifyVirtualInput` check,
`physicalIsolation.activate()`, and the Playnite "Ready" signal — now runs
without waiting on it at all. The future is only joined (`.get()`) well
after "Ready" has already been signaled, right before the console summary
needs `virtualFirmware`'s value. Net effect: **the entire ~422ms firmware
wait, and by extension the ~33ms isolation step that used to sit strictly
after it, are no longer on the critical path to "the game sees DualSense
instead of Xbox" at all** — that path is now bounded by
`physical_input_ms + virtual_input_ms + isolation_ms` alone (roughly
334+122+33 ≈ 489ms of the original ~911ms), a reduction of very roughly
**half**, achieved by reordering/overlapping already-independent existing
work — no new processes, no IPC, no persistent state, same per-game-launch
process model as always. Also had to fix the since-stale
`firmwareInitializationMilliseconds`/`isolationInitializationMilliseconds`
diagnostics math (they used to be a simple subtractive chain against
`firmwareCheckedAt`, which now happens later than `isolationReadyAt` instead
of earlier — each is now measured against its own start point instead, see
the comment in the diff).

Verified: compiles clean (0 warnings) from a full reset + all 5 patches
reapplied in sequence via `apply-patches.sh`. **Not yet re-tested against a
real GTA V race** — the user should re-run the same "close game, leave Tray
running elevated a while, launch fresh" test and check whether it now starts
in DualSense mode, and whether `tray_bridge.log`'s
`initialization_firmware_ms`/`initialization_isolation_ms` numbers still
make sense (they're independent measurements now, not a chain, so their sum
can legitimately exceed the isolation-to-ready wall time).

## How to resume

Paste this file into a new chat and say what you want to do next. Status as
of 2026-09-06: gyro/accel + XInput coexistence is implemented, compiled, and
confirmed working live; the Tray app builds and runs too, with two upstream
Tray bugs found and fixed (title-match false positives, learned-cache
persistence of low-confidence guesses); the "wins the game-launch race"
problem was investigated in depth (WMI-vs-poll red herring debunked with
real evidence, real ~1s startup-latency breakdown obtained, the single
biggest lever — the diagnostic-only firmware check — parallelized in patch
0005) but **not yet re-verified against real GTA V launches** after that
fix. One hard game-side limitation (mid-session Xbox↔DualSense toggling)
is understood and documented as genuinely not fixable, matching even
Flydigi Space Station's own behavior. No need to re-explain the
USBip/HidHide saga, the controller mode-switching issues from early on, the
protocol reverse-engineering, or the WMI investigation — all resolved and
documented above. Next open items: confirm patch 0005 actually helps in
practice, tune the gyro sign convention by feel in an actual game, and decide
whether the bigger "keep it warm" redesign is still worth it if 0005 alone
isn't enough.
