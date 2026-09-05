# ApexSenseBridge-GyroAddon

Layers gyroscope/accelerometer support on top of [ReynArts/ApexSenseBridge](https://github.com/ReynArts/ApexSenseBridge)
without forking it — the upstream project is tracked as a git submodule, and
our changes live as reviewable `.patch` files applied on top. This is the same
strategy ApexSenseBridge itself uses for its own VIIPER dependency
(`third_party/viiper-patches/`).

## Why this structure

- `vendor/ApexSenseBridge` is **never committed to directly**. It's a git
  submodule pinned to a specific upstream commit. Editing it and leaving the
  edits there is a mistake — always turn edits into a patch (see below) and
  reset the submodule to clean.
- `patches/asb/*.patch` is the actual source of truth for our changes. Small,
  numbered, one concern per file where possible.
- Upstream updates = move the submodule pointer + re-apply patches. Conflicts
  get resolved once, in one place, instead of living with a permanently
  diverged fork.

## Layout

```
vendor/ApexSenseBridge/   git submodule -> https://github.com/ReynArts/ApexSenseBridge
patches/asb/              our patches, applied on top of a clean vendor checkout
scripts/
  apply-patches.sh        reset vendor to clean HEAD + apply all patches/asb/*.patch
  sync-upstream.sh        move submodule to a new upstream ref, then re-apply patches
  build.sh                apply-patches.sh + cmake configure/build
```

## First-time setup (on your Windows dev machine)

```powershell
git clone <your-repo-url> ApexSenseBridge-GyroAddon
cd ApexSenseBridge-GyroAddon
git submodule update --init --recursive
```

Then build (needs the same toolchain ApexSenseBridge itself requires — see
its own README for the exact WDK/MSVC version):

```powershell
bash scripts/build.sh
# or manually:
bash scripts/apply-patches.sh
cmake -S vendor/ApexSenseBridge -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

## What patch `0001-gyro-plumbing.patch` does

Pure plumbing, no real motion data yet:

1. Adds `gyroX/Y/Z` and `accelX/Y/Z` (`int16_t`) to `DualSenseInputState`
   (`src/dualsense/DualSenseInput.h`).
2. Writes them into bytes 19–30 of the 33-byte intermediate report handed to
   VIIPER (`buildViiperInput` in `DualSenseInput.cpp`) — those 12 bytes were
   previously unused/zero-padded.
3. Adds a new, isolated file `src/motion/ApexMotionSource.{h,cpp}` with a
   single hook function `applyApex5Motion(state)`, currently a no-op that
   leaves motion at neutral (0,0,0 / 0,0,0).
4. Patches `WindowsPhysicalInputSource.cpp` with a 2-line hook call to
   `applyApex5Motion()` right after each report is decoded — nothing else in
   that file changes, to keep future upstream merges cheap.
5. Registers the new file in `CMakeLists.txt`.

**This does not read real gyro data yet.** The Apex 5's official Flydigi
Space Station app can clearly read it (confirmed: full DualSense incl. gyro
works when it forces DualSense mode for e.g. GTA V), but the wire format is
proprietary and undocumented — ApexSenseBridge's own code doesn't decode it
either (it falls back to XInput/generic HID axes for the Apex 5 entirely, see
`WindowsPhysicalInputSource.cpp::parseReport`).

## Remaining work: decoding the real protocol

1. Capture raw HID traffic between Flydigi Space Station and the physical
   Apex 5 while GTA V (or another game that gets it to force full DualSense
   mode) is running — Wireshark + USBPcap, or a dedicated HID sniffer.
2. Rotate the controller on isolated axes (X only, Y only, Z only, then each
   accel axis) to identify which bytes move and how (report ID, byte offset,
   scale, endianness, sign).
3. Replace the body of `applyApex5Motion()` in
   `src/motion/ApexMotionSource.cpp` with the real decode. No other file
   needs to change.
4. The VIIPER-side Go backend (patched separately under
   `vendor/ApexSenseBridge/third_party/viiper-patches/`) already builds a
   full 64-byte real DualSense USB report, which has room for motion data at
   the standard offsets — that part does not need to change, only needs the
   bytes we're now placing at 19–30 to be threaded through to it if they
   aren't already (check the existing viiper patch's report builder).

## Making further changes to the vendor code

```bash
# 1. Make sure vendor is clean and patches are applied
./scripts/apply-patches.sh

# 2. Edit files inside vendor/ApexSenseBridge directly

# 3. Turn your edit into a patch
git -C vendor/ApexSenseBridge diff --binary > patches/asb/0002-my-change.patch

# 4. Reset vendor back to clean (the patch is now the source of truth)
git -C vendor/ApexSenseBridge reset --hard HEAD
git -C vendor/ApexSenseBridge clean -fd
```

## Updating to a newer upstream ApexSenseBridge release

```bash
./scripts/sync-upstream.sh v0.7.0   # or a branch/commit
```

If a patch fails to apply, it stops there. Resolve manually: check out the
new upstream ref, re-apply the failing patch with `git apply -3` (3-way,
gives you conflict markers), fix, then regenerate that one `.patch` file as
in step 3 above.
