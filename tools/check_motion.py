#!/usr/bin/env python3
"""Passively watches the Apex 5's vendor HID interface and reports whether
motion (gyro/accel) mode is currently active -- without sending anything
itself. Safe to run alongside ApexSenseBridge, Flydigi Space Station, or
nothing at all.

Requires: pip install hidapi   (the compiled cython-hidapi package, which
provides an `import hid` module backed by a native hidapi.dll -- NOT the
pure-python `hid` package, which needs a hidapi.dll this script can't
provide.)

Usage:
    python tools/check_motion.py            # watch for ~5 seconds
    python tools/check_motion.py --seconds 15
"""
import argparse
import struct
import sys
import time

try:
    import hid
except ImportError:
    print("Missing dependency. Run: pip install hidapi", file=sys.stderr)
    sys.exit(1)

VENDOR_ID = 0x37D7
PRODUCT_ID = 0x2501
VENDOR_USAGE_PAGE = 0xFFA0
MOTION_REPORT_ID = 0x04
MOTION_MARKER = 0xEF


def find_vendor_collection():
    for info in hid.enumerate():
        if (info["vendor_id"] == VENDOR_ID and
                info["product_id"] == PRODUCT_ID and
                info["usage_page"] == VENDOR_USAGE_PAGE):
            return info
    return None


def decode(report: bytes):
    if len(report) < 30 or report[0] != MOTION_REPORT_ID or report[3] != MOTION_MARKER:
        return None
    gyro = struct.unpack_from("<3h", report, 18)
    accel = struct.unpack_from("<3h", report, 24)
    return gyro, accel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args()

    info = find_vendor_collection()
    if not info:
        print("Apex 5 vendor HID collection (37D7:2501, usage page 0xFFA0) not found.")
        print("Is the controller connected and powered on?")
        sys.exit(1)

    print(f"Opening {info['path']!r} ...")
    handle = hid.device()
    handle.open_path(info["path"])
    handle.set_nonblocking(1)

    motion_reports = 0
    other_reports = 0
    last_sample = None
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        data = handle.read(64, timeout_ms=100)
        if not data:
            continue
        sample = decode(bytes(data))
        if sample:
            motion_reports += 1
            last_sample = sample
        else:
            other_reports += 1
    handle.close()

    print(f"Motion (0xEF) reports seen: {motion_reports}")
    print(f"Other reports seen:         {other_reports}")
    if last_sample:
        gyro, accel = last_sample
        print(f"Last sample -- gyro: {gyro}  accel: {accel}")
        print("MOTION MODE IS ACTIVE.")
    else:
        print("No motion reports seen -- motion mode is NOT active right now.")


if __name__ == "__main__":
    main()
