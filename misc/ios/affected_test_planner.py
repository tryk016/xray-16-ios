#!/usr/bin/env python3
"""Pure, non-authoritative mapper for explicit change manifests.

This prototype never discovers changes, scans a checkout, starts a process, or
changes a gate.  The public CLI treats every external manifest as untrusted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import sys
import unicodedata
from pathlib import Path
from typing import Any

SCHEMA = "openxray.affected-test-plan.v1"
MANIFEST_SCHEMA = "openxray.change-manifest.v1"
MAX_BYTES = 1 << 20
MAX_RECORDS = 4096
MAX_PATH_BYTES = 1024
STATUSES = frozenset({"A", "M", "D", "R", "T", "U"})
SPECIALIZED = frozenset({"host-fast", "shader-affected", "engine-affected", "retail-integration"})

# This shadow-only integration is intentionally bound to one reviewed
# post-integration catalog snapshot. These are byte hashes, not self-declared
# manifest fields.
ASSET_SHA256 = {
    "affected_test_mapping_v1.json": "15928ad02ce2939e61f42e4ef4ce32f251f3a3b89f43f34f63b91d997319203f",
    "test_feedback_inventory_v1.json": "064596dc006986aa895b7c5a592b4fd699c59aaeecce02dd4612a0d8c5007036",
    "test_feedback_catalog.json": "2ca736499fb6c49189ccf3a22243e4a1accd68afdbac6142603bedd673b681e3",
}
EXPECTED_PARITY = {
    "entrypoints": (57, "73cf7874aa1b473adf4209988165971a345c27bea6bacee81d8d09b501757551"),
    "build_stage_exclusions": (18, "3d29c524c51b60723a09b8dc5a042b165de13bba8891ad1e9c7bd9205a6c7011"),
    "catalog_complete": (1361, "bf53c06625e63824396e1636549f6a657c05018d88db10394e9877d883932efb"),
}
EXPECTED_PROFILES = {
    "host-fast": (877, "68a4ceadd9f74db0962f80269810a216b271028b934d8bab9fae356fca9915bc", 56, "feb499af0aac9ecb4d5aa58018636bd53f68086193d8a1b28c5028b805f85f83"),
    "shader-affected": (527, "dcc0ab427166c1b371ab5047f158332c3b1217a8c60cb732a732884f43d0decf", 54, "ab7c872706043ec3bbd8dc2fd81a29f7e4072877e9db0ed80e928efcccc6088a"),
    "engine-affected": (658, "c275cbf96aee72569b7a228aaca926f2f7efc6a17be4b5fb681e04d7c4c048f6", 53, "f5733a788a075fe64077c887e6e258084901295811e3adc73ad57a9e184a3345"),
    "retail-integration": (924, "44c3bd2781c77ea8702a360927ec7f2ce4bf9f3fd59fc2b77a7b5a046b84ac64", 56, "feb499af0aac9ecb4d5aa58018636bd53f68086193d8a1b28c5028b805f85f83"),
    "full-release": (1361, "bf53c06625e63824396e1636549f6a657c05018d88db10394e9877d883932efb", 63, "9d3ad59748905510cf386f91ae5c03081b0cbb8fc39f6ad271f6e1a2320a582b"),
}
ERROR_ASSET_BINDING = "ASSET_BINDING_INVALID"
ERROR_MANIFEST_INVALID = "MANIFEST_INVALID"
ERROR_MANIFEST_UNAVAILABLE = "MANIFEST_UNAVAILABLE"
ERROR_MANIFEST_MISSING = "MANIFEST_MISSING"


class MapperError(ValueError):
    """An input or a bound prototype asset is unsafe or malformed."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical_digest(value: Any) -> str:
    return hashlib.sha256((canonical(value) + "\n").encode("utf-8")).hexdigest()


def stable_id_digest(values: list[str]) -> str:
    if values != sorted(values) or len(values) != len(set(values)):
        raise MapperError("stable IDs must be sorted and unique")
    if any(not isinstance(value, str) or not value or "\n" in value or "\r" in value for value in values):
        raise MapperError("stable ID is malformed")
    return hashlib.sha256(("\n".join(values) + "\n").encode("utf-8")).hexdigest()


def _reject_constant(_: str) -> None:
    raise MapperError("JSON_NONFINITE")


def _reject_number(_: str) -> None:
    raise MapperError("JSON_NUMBER")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise MapperError("JSON_NONFINITE")
    return parsed


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MapperError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def strict_json(raw: bytes, label: str, *, forbid_numbers: bool = False) -> Any:
    if len(raw) > MAX_BYTES:
        raise MapperError(f"{label} exceeds 1 MiB")
    try:
        text = raw.decode("utf-8", "strict")
        kwargs: dict[str, Any] = {"object_pairs_hook": _unique_object, "parse_constant": _reject_constant}
        if forbid_numbers:
            kwargs.update(parse_int=_reject_number, parse_float=_reject_number)
        else:
            kwargs["parse_float"] = _finite_float
        return json.loads(text, **kwargs)
    except (UnicodeDecodeError, json.JSONDecodeError, MapperError, OverflowError, ValueError) as error:
        raise MapperError(f"{label}_JSON_INVALID") from error


def _snapshot(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (value.st_dev, value.st_ino, stat.S_IMODE(value.st_mode), value.st_size,
            value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)


def _safe_regular(value: os.stat_result, label: str) -> None:
    if not stat.S_ISREG(value.st_mode):
        raise MapperError(f"{label} is not a regular file")
    if value.st_uid != os.geteuid() or value.st_nlink != 1:
        raise MapperError(f"{label} ownership or link count is unsafe")
    if stat.S_IMODE(value.st_mode) & 0o022:
        raise MapperError(f"{label} is writable by group or other")


def read_bound_file(path: Path, label: str) -> bytes:
    """Read one regular file through a parent descriptor with stable identity."""
    try:
        if not path.is_absolute():
            path = Path(os.path.abspath(path))
        parent = path.parent
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except (OSError, ValueError) as error:
        raise MapperError(f"{label}_UNAVAILABLE") from error
    file_fd = -1
    try:
        before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        _safe_regular(before, label)
        if before.st_size > MAX_BYTES:
            raise MapperError(f"{label} exceeds 1 MiB")
        file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        opened = os.fstat(file_fd)
        rebound = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if _snapshot(before) != _snapshot(opened) or _snapshot(before) != _snapshot(rebound):
            raise MapperError(f"{label} changed before read")
        chunks: list[bytes] = []
        remaining = MAX_BYTES + 1
        while remaining:
            block = os.read(file_fd, min(65536, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        if remaining == 0:
            raise MapperError(f"{label} exceeds 1 MiB")
        finished = os.fstat(file_fd)
        final = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if _snapshot(opened) != _snapshot(finished) or _snapshot(opened) != _snapshot(final):
            raise MapperError(f"{label} changed while reading")
        return b"".join(chunks)
    except (OSError, ValueError) as error:
        raise MapperError(f"{label}_UNAVAILABLE") from error
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(parent_fd)


def read_bounded_stdin() -> bytes:
    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise MapperError("stdin manifest exceeds 1 MiB")
    return raw


def safe_path(value: Any, label: str = "path") -> str:
    if not isinstance(value, str) or not value:
        raise MapperError(f"{label} must be a non-empty string")
    if unicodedata.normalize("NFC", value) != value:
        raise MapperError(f"{label} must be NFC-normalized")
    # Category C covers controls, formats, surrogates, private-use and
    # unassigned code points.  Reject before UTF-8 encoding to avoid a
    # surrogate-triggered UnicodeEncodeError escaping this validation path.
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise MapperError(f"{label} has a forbidden Unicode character")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise MapperError(f"{label} is not UTF-8 encodable") from error
    if len(encoded) > MAX_PATH_BYTES:
        raise MapperError(f"{label} exceeds 1024 UTF-8 bytes")
    if value.startswith("/") or "\\" in value or "//" in value or "\x00" in value:
        raise MapperError(f"{label} is not repository-relative")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise MapperError(f"{label} has an unsafe component")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise MapperError(f"{label} has a control character")
    return value


def validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "producer", "complete", "base", "head", "records"}:
        raise MapperError("manifest schema is not exact")
    if value["schema"] != MANIFEST_SCHEMA or value["producer"] != "external-untrusted":
        raise MapperError("manifest schema or producer is unsupported")
    if not isinstance(value["complete"], bool) or value["base"] is not None or value["head"] is not None:
        raise MapperError("manifest completeness metadata is invalid")
    records = value["records"]
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise MapperError("manifest record count is invalid")
    normalized: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    prior: tuple[str, str, str] | None = None
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not isinstance(record.get("status"), str):
            raise MapperError(f"record {index} is malformed")
        status = record["status"]
        expected = {"status", "path", "old_path"} if status == "R" else {"status", "path"}
        if status not in STATUSES or set(record) != expected:
            raise MapperError(f"record {index} has an invalid status or shape")
        path = safe_path(record["path"], f"record {index} path")
        old_path = ""
        if status == "R":
            old_path = safe_path(record["old_path"], f"record {index} old_path")
            if old_path == path:
                raise MapperError(f"record {index} rename is ambiguous")
        if path in seen_paths or (old_path and old_path in seen_paths):
            raise MapperError(f"record {index} duplicates a path")
        seen_paths.add(path)
        if old_path:
            seen_paths.add(old_path)
        key = (path, status, old_path)
        if prior is not None and key <= prior:
            raise MapperError("manifest records are not strictly sorted")
        prior = key
        normalized.append({"status": status, "path": path, **({"old_path": old_path} if old_path else {})})
    return {"schema": MANIFEST_SCHEMA, "producer": "external-untrusted", "complete": value["complete"],
            "base": None, "head": None, "records": normalized}


def _validate_inventory(inventory: Any) -> dict[str, Any]:
    if not isinstance(inventory, dict) or inventory.get("schema") != "openxray.test-feedback-inventory.v1":
        raise MapperError("inventory schema is invalid")
    profiles = inventory.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != {"host-fast", "shader-affected", "engine-affected", "retail-integration", "full-release"}:
        raise MapperError("inventory profile vocabulary is invalid")
    for name, record in profiles.items():
        if not isinstance(record, dict) or set(record) != {"stable_ids", "stage_ids"}:
            raise MapperError(f"inventory profile {name} is malformed")
        for key in ("stable_ids", "stage_ids"):
            values = record[key]
            if not isinstance(values, list) or not values or values != sorted(values) or len(values) != len(set(values)):
                raise MapperError(f"inventory {name} {key} is not a sorted unique non-empty list")
            if not all(isinstance(item, str) and item for item in values):
                raise MapperError(f"inventory {name} {key} has an invalid item")
        expected = EXPECTED_PROFILES[name]
        if (len(record["stable_ids"]), stable_id_digest(record["stable_ids"]),
                len(record["stage_ids"]), stable_id_digest(record["stage_ids"])) != expected:
            raise MapperError("INVENTORY_PROFILE_DRIFT")
    parity = inventory.get("parity")
    if not isinstance(parity, dict) or set(parity) != set(EXPECTED_PARITY):
        raise MapperError("INVENTORY_PARITY_DRIFT")
    for name, expected in EXPECTED_PARITY.items():
        record = parity.get(name)
        if not isinstance(record, dict) or (record.get("count"), record.get("sha256")) != expected:
            raise MapperError("INVENTORY_PARITY_DRIFT")
    return inventory


def _validate_mapping(mapping: Any) -> dict[str, Any]:
    if not isinstance(mapping, dict) or mapping.get("schema") != "openxray.affected-test-mapping.v1":
        raise MapperError("mapping schema is invalid")
    if set(mapping) != {"schema", "global_rules", "path_rules", "projected_post_integration"}:
        raise MapperError("mapping shape is invalid")
    for key in ("global_rules", "path_rules"):
        if not isinstance(mapping[key], list) or not mapping[key]:
            raise MapperError(f"mapping {key} is invalid")
    for record in mapping["global_rules"]:
        if not isinstance(record, dict) or set(record) != {"kind", "value"} or record["kind"] not in {"exact", "prefix"}:
            raise MapperError("global rule is invalid")
        _safe_mapping_value(record["value"], "global rule")
    names: set[str] = set()
    for record in mapping["path_rules"]:
        if not isinstance(record, dict) or set(record) != {"name", "kind", "value", "profile"}:
            raise MapperError("path rule is invalid")
        if not isinstance(record["name"], str) or record["name"] in names or record["kind"] not in {"exact", "prefix"} or record["profile"] not in SPECIALIZED:
            raise MapperError("path rule fields are invalid")
        names.add(record["name"])
        _safe_mapping_value(record["value"], "path rule")
    return mapping


def _catalog_names(catalog: dict[str, Any], key: str, field: str) -> list[str]:
    values = catalog.get(key)
    if not isinstance(values, list):
        raise MapperError("CATALOG_SHAPE_INVALID")
    names = [record.get(field) for record in values if isinstance(record, dict)]
    if len(names) != len(values) or not all(isinstance(name, str) and name for name in names):
        raise MapperError("CATALOG_SHAPE_INVALID")
    return sorted(names)


def _validate_catalog(catalog: Any) -> dict[str, Any]:
    if not isinstance(catalog, dict):
        raise MapperError("CATALOG_SHAPE_INVALID")
    if (catalog.get("schema") != "openxray.test-feedback-catalog.v1" or
            catalog.get("selection_authority") != "NONE" or
            catalog.get("cache_authority") != "NONE"):
        raise MapperError("CATALOG_CONTRACT_DRIFT")
    entrypoints = _catalog_names(catalog, "entrypoints", "entrypoint_id")
    exclusions = _catalog_names(catalog, "build_stage_exclusions", "stage_id")
    if (len(entrypoints), stable_id_digest(entrypoints)) != EXPECTED_PARITY["entrypoints"]:
        raise MapperError("CATALOG_ENTRYPOINT_DRIFT")
    if (len(exclusions), stable_id_digest(exclusions)) != EXPECTED_PARITY["build_stage_exclusions"]:
        raise MapperError("CATALOG_EXCLUSION_DRIFT")
    return catalog


def _safe_mapping_value(value: Any, label: str) -> str:
    """Validate a literal exact/prefix rule, where a prefix may end in '/'."""
    if not isinstance(value, str) or not value or unicodedata.normalize("NFC", value) != value:
        raise MapperError(f"{label} is invalid")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise MapperError(f"{label} has a forbidden Unicode character")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise MapperError(f"{label} is not UTF-8 encodable") from error
    if len(encoded) > MAX_PATH_BYTES or value.startswith("/") or "\\" in value or "\x00" in value:
        raise MapperError(f"{label} is unsafe")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise MapperError(f"{label} has a control character")
    if any(part in {".", ".."} for part in value.rstrip("/").split("/")):
        raise MapperError(f"{label} has an unsafe component")
    return value


def _matches(path: str, rule: dict[str, str]) -> bool:
    return path == rule["value"] if rule["kind"] == "exact" else path.startswith(rule["value"])


def evaluate_complete_fixture(mapping: dict[str, Any], inventory: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Test-only mapping API.  The public CLI never calls it for authority."""
    _validate_mapping(mapping)
    _validate_inventory(inventory)
    normalized = validate_manifest(manifest)
    if not normalized["complete"]:
        return {"profile": "full-release", "fallback": True, "reasons": ["incomplete manifest"], "runtime_profiles": []}
    records = normalized["records"]
    if not records:
        return {"profile": "host-fast", "fallback": False, "reasons": ["complete empty fixture"], "runtime_profiles": []}
    profiles: set[str] = set()
    reasons: list[str] = []
    for record in records:
        if record["status"] in {"D", "R", "T", "U"}:
            return {"profile": "full-release", "fallback": True, "reasons": ["unsafe manifest status"], "runtime_profiles": []}
        path = record["path"]
        if any(_matches(path, rule) for rule in mapping["global_rules"]):
            return {"profile": "full-release", "fallback": True, "reasons": ["global/config mapping"], "runtime_profiles": []}
        matches = [rule for rule in mapping["path_rules"] if _matches(path, rule)]
        if len(matches) != 1:
            return {"profile": "full-release", "fallback": True, "reasons": ["unknown or ambiguous mapping"], "runtime_profiles": []}
        profiles.add(matches[0]["profile"])
        reasons.append(f"{path}:{matches[0]['name']}")
    if len(profiles) != 1:
        return {"profile": "full-release", "fallback": True, "reasons": ["mixed specialized profiles"], "runtime_profiles": []}
    return {"profile": profiles.pop(), "fallback": False, "reasons": reasons, "runtime_profiles": []}


def build_report(mapping: dict[str, Any], inventory: dict[str, Any], manifest_raw: bytes | None,
                 manifest_error: str | None) -> dict[str, Any]:
    """Build the inert production report; actual selection is always full-release."""
    mapping = _validate_mapping(mapping)
    inventory = _validate_inventory(inventory)
    advisory: dict[str, Any] | None = None
    reason = manifest_error if manifest_error in {ERROR_MANIFEST_INVALID, ERROR_MANIFEST_UNAVAILABLE, ERROR_MANIFEST_MISSING} else None
    manifest_sha256: str | None = None
    if manifest_raw is not None:
        manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
        try:
            manifest = validate_manifest(strict_json(manifest_raw, "manifest", forbid_numbers=True))
            advisory = {"status": "untrusted-not-evaluated", "record_count": len(manifest["records"])}
            if not manifest["complete"]:
                reason = "external manifest is incomplete"
            else:
                reason = "external completeness is unauthenticated"
        except MapperError:
            reason = ERROR_MANIFEST_INVALID
    if reason is None:
        reason = ERROR_MANIFEST_MISSING
    full = inventory["profiles"]["full-release"]
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "non-authoritative",
        "execution_allowed": False,
        "runtime_claims_allowed": False,
        "selection_authority": "NONE",
        "cache_authority": "NONE",
        "profile": "full-release",
        "fallback": True,
        "fallback_reasons": [reason],
        "selected_stable_ids": full["stable_ids"],
        "selected_count": len(full["stable_ids"]),
        "selected_sha256": stable_id_digest(full["stable_ids"]),
        "selected_stage_ids": full["stage_ids"],
        "stage_count": len(full["stage_ids"]),
        "stage_sha256": stable_id_digest(full["stage_ids"]),
        "mapping_sha256": canonical_digest(mapping),
        "inventory_sha256": canonical_digest(inventory),
        "manifest_sha256": manifest_sha256,
        "advisory": advisory,
    }
    report["decision_sha256"] = canonical_digest(report)
    return report


def _bound_asset(base: Path, name: str) -> Any:
    raw = read_bound_file(base / name, "asset")
    if hashlib.sha256(raw).hexdigest() != ASSET_SHA256[name]:
        raise MapperError(ERROR_ASSET_BINDING)
    return strict_json(raw, "asset")


def load_assets(base: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load only the three byte-pinned, post-integration shadow assets.

    Any binding or metadata drift returns no plan: the frozen full-release IDs
    themselves are no longer trustworthy in that state.
    """
    try:
        mapping = _validate_mapping(_bound_asset(base, "affected_test_mapping_v1.json"))
        inventory = _validate_inventory(_bound_asset(base, "test_feedback_inventory_v1.json"))
        catalog = _validate_catalog(_bound_asset(base, "test_feedback_catalog.json"))
        binding = inventory.get("catalog_binding")
        if not isinstance(binding, dict) or binding.get("sha256") != ASSET_SHA256["test_feedback_catalog.json"]:
            raise MapperError("CATALOG_BINDING_DRIFT")
        # Keep the validated catalog local to this function; public reports do
        # not expose its source path or mutable filesystem metadata.
        del catalog
        return mapping, inventory
    except (MapperError, OSError, ValueError) as error:
        raise MapperError(ERROR_ASSET_BINDING) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--manifest", type=Path)
    source.add_argument("--stdin", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        mapping, inventory = load_assets(Path(__file__).resolve().parent)
    except MapperError:
        print(f"pure mapper asset error: {ERROR_ASSET_BINDING}", file=sys.stderr)
        return 2
    raw: bytes | None = None
    error: str | None = None
    if arguments.manifest is not None:
        try:
            raw = read_bound_file(arguments.manifest, "manifest")
        except (OSError, MapperError):
            error = ERROR_MANIFEST_UNAVAILABLE
    elif arguments.stdin:
        try:
            raw = read_bounded_stdin()
        except MapperError:
            error = ERROR_MANIFEST_INVALID
    print(canonical(build_report(mapping, inventory, raw, error)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
