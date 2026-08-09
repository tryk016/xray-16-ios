#!/usr/bin/env python3
"""Summarize one process from an exported Activity Monitor live-table XML."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import sys
import xml.etree.ElementTree as ET


def finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("must be finite")
    return parsed


def nonnegative_float(value: str) -> float:
    parsed = finite_float(value)
    if parsed < 0.0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", type=Path, help="activity-monitor-process-live XML exported by xctrace")
    parser.add_argument("--process", default="xr_3da", help="exact process name before the '(pid)' suffix")
    parser.add_argument("--warmup-seconds", type=nonnegative_float, default=0.0)
    parser.add_argument("--min-duration-seconds", type=nonnegative_float, default=0.0)
    parser.add_argument(
        "--min-samples",
        type=positive_int,
        default=2,
        help="minimum number of post-warmup samples (default: 2)",
    )
    parser.add_argument(
        "--max-gap-seconds",
        "--max-sample-gap-seconds",
        dest="max_gap_seconds",
        type=nonnegative_float,
        default=5.0,
        help="maximum allowed gap between consecutive post-warmup samples (default: 5)",
    )
    parser.add_argument("--max-footprint-mib", type=nonnegative_float)
    parser.add_argument("--max-growth-mib", type=finite_float)
    parser.add_argument("--require-single-pid", action="store_true")
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def main() -> int:
    args = parse_args()
    try:
        root = ET.parse(args.xml).getroot()
    except (OSError, ET.ParseError) as error:
        print(f"FAIL: could not read Activity Monitor XML: {error}", file=sys.stderr)
        return 2

    node = root.find("node")
    schema = node.find("schema") if node is not None else None
    if node is None or schema is None or schema.get("name") != "activity-monitor-process-live":
        print("FAIL: XML is not an activity-monitor-process-live export", file=sys.stderr)
        return 2

    columns = [column.findtext("mnemonic", "") for column in schema.findall("col")]
    required = {"start", "process", "pid", "cpu-percent", "memory-physical-footprint"}
    if not required.issubset(columns):
        print(f"FAIL: Activity Monitor schema is missing columns: {sorted(required - set(columns))}", file=sys.stderr)
        return 2
    if len(columns) != len(set(columns)):
        print("FAIL: Activity Monitor schema has duplicate columns", file=sys.stderr)
        return 2

    id_map: dict[str, ET.Element] = {}
    for element in root.iter():
        identifier = element.get("id")
        if not identifier:
            continue
        if identifier in id_map:
            print(f"FAIL: malformed Activity Monitor data: duplicate XML id: {identifier}", file=sys.stderr)
            return 2
        id_map[identifier] = element

    def resolve(element: ET.Element) -> ET.Element:
        seen: set[str] = set()
        while "ref" in element.attrib:
            reference = element.get("ref", "")
            if reference in seen or reference not in id_map:
                raise ValueError(f"invalid XML reference: {reference}")
            seen.add(reference)
            element = id_map[reference]
        return element

    def number(element: ET.Element, label: str, *, required: bool) -> float | None:
        element = resolve(element)
        if element.tag == "sentinel" or element.text is None:
            return None
        try:
            value = float(element.text)
        except ValueError as error:
            if required:
                raise ValueError(f"invalid {label} for process '{args.process}'") from error
            return None
        if not math.isfinite(value):
            if required:
                raise ValueError(f"non-finite {label} for process '{args.process}'")
            return None
        return value

    samples: list[dict[str, float | int]] = []
    try:
        for row in node.findall("row"):
            values = list(row)
            if len(values) != len(columns):
                raise ValueError(
                    f"row has {len(values)} values but schema has {len(columns)} columns"
                )
            fields = dict(zip(columns, values))
            process_element = resolve(fields["process"])
            process_format = process_element.get("fmt", "")
            process_name = process_format.rsplit(" (", 1)[0]
            if process_name != args.process:
                continue
            start = number(fields["start"], "timestamp", required=True)
            pid = number(fields["pid"], "PID", required=True)
            footprint = number(
                fields["memory-physical-footprint"], "footprint", required=True
            )
            cpu = number(fields["cpu-percent"], "CPU", required=False)
            if start is None or pid is None or footprint is None:
                continue
            if start < 0.0:
                raise ValueError(f"negative timestamp for process '{args.process}'")
            if footprint < 0.0:
                raise ValueError(f"negative footprint for process '{args.process}'")
            if pid <= 0.0 or not pid.is_integer():
                raise ValueError(f"PID must be a positive integer for process '{args.process}'")
            samples.append(
                {
                    "seconds": start / 1_000_000_000.0,
                    "pid": int(pid),
                    "footprint_mib": footprint / (1024.0 * 1024.0),
                    "cpu_percent": cpu if cpu is not None else math.nan,
                }
            )
    except ValueError as error:
        print(f"FAIL: malformed Activity Monitor data: {error}", file=sys.stderr)
        return 2

    if not samples:
        print(f"FAIL: no Activity Monitor samples for process '{args.process}'", file=sys.stderr)
        return 1

    samples.sort(key=lambda sample: float(sample["seconds"]))
    trace_start = float(samples[0]["seconds"])
    samples = [sample for sample in samples if float(sample["seconds"]) - trace_start >= args.warmup_seconds]
    if not samples:
        print("FAIL: warmup removed every matching sample", file=sys.stderr)
        return 1

    footprints = [float(sample["footprint_mib"]) for sample in samples]
    cpus = [float(sample["cpu_percent"]) for sample in samples if math.isfinite(float(sample["cpu_percent"]))]
    pids = sorted({int(sample["pid"]) for sample in samples})
    duration = float(samples[-1]["seconds"]) - float(samples[0]["seconds"])
    sample_gaps = [
        float(current["seconds"]) - float(previous["seconds"])
        for previous, current in zip(samples, samples[1:])
    ]
    max_gap = max(sample_gaps, default=0.0)

    summary: dict[str, object] = {
        "process": args.process,
        "samples": len(samples),
        "duration_seconds": round(duration, 3),
        "max_gap_seconds": round(max_gap, 3),
        "pids": pids,
        "footprint_mib": {
            "first": round(footprints[0], 3),
            "last": round(footprints[-1], 3),
            "min": round(min(footprints), 3),
            "mean": round(statistics.fmean(footprints), 3),
            "p90": round(percentile(footprints, 0.9), 3),
            "max": round(max(footprints), 3),
            "growth": round(footprints[-1] - footprints[0], 3),
        },
    }
    if cpus:
        summary["cpu_percent"] = {
            "min": round(min(cpus), 3),
            "mean": round(statistics.fmean(cpus), 3),
            "p90": round(percentile(cpus, 0.9), 3),
            "max": round(max(cpus), 3),
        }

    failures: list[str] = []
    if len(samples) < args.min_samples:
        failures.append(f"samples {len(samples)} < {args.min_samples}")
    if duration < args.min_duration_seconds:
        failures.append(f"duration {duration:.1f}s < {args.min_duration_seconds:.1f}s")
    if max_gap > args.max_gap_seconds:
        failures.append(f"max sample gap {max_gap:.1f}s > {args.max_gap_seconds:.1f}s")
    if args.require_single_pid and len(pids) != 1:
        failures.append(f"process continuity failed: pids={pids}")
    if args.max_footprint_mib is not None and max(footprints) > args.max_footprint_mib:
        failures.append(f"max footprint {max(footprints):.1f} MiB > {args.max_footprint_mib:.1f} MiB")
    growth = footprints[-1] - footprints[0]
    if args.max_growth_mib is not None and growth > args.max_growth_mib:
        failures.append(f"growth {growth:.1f} MiB > {args.max_growth_mib:.1f} MiB")

    summary["result"] = "FAIL" if failures else "PASS"
    summary["failures"] = failures
    rendered = json.dumps(summary, indent=2, sort_keys=True)
    print(rendered)
    if args.json_output:
        try:
            args.json_output.write_text(rendered + "\n", encoding="utf-8")
        except OSError as error:
            print(f"FAIL: could not write JSON summary: {error}", file=sys.stderr)
            return 2
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
