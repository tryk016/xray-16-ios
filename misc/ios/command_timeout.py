#!/usr/bin/env python3
"""Run one command with a wall-clock timeout and terminate its process group."""

from __future__ import annotations

import os
import signal
import subprocess
import sys


def stop_group(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def main() -> int:
    if len(sys.argv) < 3:
        print(f"usage: {sys.argv[0]} <seconds> <command> [args...]", file=sys.stderr)
        return 2
    try:
        timeout = float(sys.argv[1])
    except ValueError:
        print("timeout must be a number", file=sys.stderr)
        return 2
    if not 0.1 <= timeout <= 3600.0:
        print("timeout must be 0.1..3600 seconds", file=sys.stderr)
        return 2

    try:
        process = subprocess.Popen(sys.argv[2:], start_new_session=True)
    except OSError as error:
        print(f"FAIL: could not start {' '.join(sys.argv[2:])}: {error}", file=sys.stderr)
        return 127

    def forward_signal(signum: int, _frame: object) -> None:
        stop_group(process, signal.Signals(signum))
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            stop_group(process, signal.SIGKILL)
            process.wait()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, forward_signal)
    signal.signal(signal.SIGTERM, forward_signal)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_group(process, signal.SIGTERM)
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            stop_group(process, signal.SIGKILL)
            process.wait()
        print(f"TIMEOUT after {timeout:g}s: {' '.join(sys.argv[2:])}", file=sys.stderr)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
