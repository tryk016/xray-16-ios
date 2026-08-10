#!/usr/bin/env python3
"""Produce a stable content hash for iOS gate inputs."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import subprocess
import sys

IOS_ARTIFACT_INPUTS = (
    "src",
    "res",
    "Externals",
    "CMakeLists.txt",
    ".gitmodules",
    "cmake",
    "misc/ios/Info.plist.in",
    "misc/ios/Assets.xcassets",
    "misc/ios/build_check.sh",
    "misc/ios/build_fast_device.sh",
    "misc/ios/install_device.sh",
    "misc/ios/device_lease.sh",
    "misc/ios/command_timeout.py",
    "misc/ios/gate_hash.py",
    "misc/ios/input.sh",
    "misc/ios/shot.sh",
    "misc/ios/lighting_ab_capture.sh",
    "misc/ios/lighting_ab_evidence.py",
    "misc/ios/test_lighting_ab_evidence.py",
    "misc/ios/test_capture_v2_host_tools.py",
    "misc/ios/test_capture_state_source_contract.py",
    "misc/ios/ios_capture_state_v2_test.cpp",
    "misc/ios/ios_diagnostic_input_state_test.cpp",
    "misc/ios/openal_provider_contract.py",
    "misc/ios/test_openal_provider_contract.py",
    "misc/ios/test_install_device_contract.py",
    "misc/ios/test_sdl2_scene_contract.py",
    "misc/ios/sync_app_resources.sh",
    "misc/ios/ui_contract_check.py",
    "misc/ios/test_ui_contract_check.py",
    "misc/ios/activity_trace_summary.py",
    "misc/ios/test_activity_trace_summary.py",
    "misc/ios/retail_simulator.sh",
    "misc/ios/retail_simulator_guard.py",
    "misc/ios/test_retail_simulator.py",
    "misc/ios/simulator_ui_navigation.py",
    "misc/ios/test_simulator_ui_navigation.py",
    "misc/ios/ui_capture_evidence.py",
    "misc/ios/test_ui_capture_evidence.py",
    "misc/ios/test_pda_map_hotkey_contract.py",
    "misc/ios/test_ui_state_marker_contract.py",
    "misc/ios/test_lifecycle_marker_contract.py",
    "misc/ios/test_locator_registration_contract.py",
    "misc/ios/test_shader_macro_contract.py",
    "misc/ios/ui_automation/OpenXRayUITests.m",
    "misc/ios/ui_automation/log_oracles.sh",
    "misc/ios/ui_automation/test_log_oracles.sh",
    "misc/ios/graphics_profile_policy_test.cpp",
    "misc/ios/audio_interruption_policy_test.cpp",
    "misc/ios/lifecycle_policy_test.cpp",
    "misc/ios/sector_fallback_policy_test.cpp",
    "misc/ios/sector_startup_oracle.py",
    "misc/ios/test_sector_startup_oracle.py",
    "misc/ios/test_sector_marker_contract.py",
    "misc/ios/ui_focus_geometry_policy_test.cpp",
    "misc/ios/texture_bc_fallback_test.cpp",
    "misc/ios/texture_memory_policy_test.cpp",
    "misc/ios/shadercheck",
    "build/ios-prefix-iphoneos/lib/libopenal.a",
    "build/ios-prefix-iphoneos/include/AL/al.h",
    "build/ios-prefix-iphoneos/include/AL/alc.h",
    "build/ios-prefix-iphoneos/include/AL/alext.h",
    "build/ios-prefix-iphoneos/lib/libSDL2.a",
    "build/ios-prefix-iphoneos/lib/libSDL2main.a",
    "cmake/ios/apply_sdl2_scene_patch.py",
    "cmake/ios/sdl2_scene_patch_manifest.json",
    "cmake/ios/patches/sdl2-2.32.10-uikit-scene.patch",
)

CMAKE_INPUT_NAMES = ("CMakeLists.txt",)
CMAKE_INPUT_SUFFIXES = (".cmake", ".in")
CMAKE_IGNORED_DIRS = {".git", ".Codex", "bin", "build", "CMakeFiles"}
BUILD_CONTEXT_ENV = (
    "CI",
    "GITHUB_ACTIONS",
    "GITHUB_REPOSITORY",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_NUMBER",
)


def add_field(digest, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def collect(paths: list[str]) -> list[str]:
    files: set[str] = set()
    for input_path in paths:
        path = os.path.normpath(input_path)
        if os.path.isfile(path):
            files.add(path)
            continue
        if not os.path.isdir(path):
            raise FileNotFoundError(path)
        for directory, dirnames, filenames in os.walk(path):
            dirnames.sort()
            for filename in sorted(filenames):
                candidate = os.path.join(directory, filename)
                if os.path.isfile(candidate):
                    files.add(candidate)
    return sorted(files)


def collect_cmake_inputs(root: str) -> list[str]:
    files: list[str] = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            dirname
            for dirname in dirnames
            if dirname not in CMAKE_IGNORED_DIRS
            and not dirname.startswith("cmake-build-")
        )
        for filename in sorted(filenames):
            if filename in CMAKE_INPUT_NAMES or filename.endswith(CMAKE_INPUT_SUFFIXES):
                files.append(os.path.join(directory, filename))
    return files


def hash_file(path: str) -> bytes:
    before = os.stat(path)
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    after = os.stat(path)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise OSError(f"input changed while hashing: {path}")
    return digest.digest()


def git_value(root: str, *arguments: str) -> bytes:
    result = subprocess.run(
        ("git", "-C", root, *arguments),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--salt", required=True)
    parser.add_argument("--ios-artifact-root")
    parser.add_argument("--cmake-project-root")
    parser.add_argument(
        "--build-context-root",
        help="include Git HEAD/branch, local build date and CMake CI environment",
    )
    parser.add_argument(
        "--relative-root",
        help="hash file names relative to this root so copied trees compare equally",
    )
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    paths = list(args.paths)
    if args.ios_artifact_root:
        paths.extend(
            os.path.join(args.ios_artifact_root, relative)
            for relative in IOS_ARTIFACT_INPUTS
        )
    if args.cmake_project_root:
        paths.extend(collect_cmake_inputs(args.cmake_project_root))
    if not paths:
        parser.error("provide paths or --ios-artifact-root")

    digest = hashlib.sha256()
    add_field(digest, args.salt.encode("utf-8"))
    try:
        if args.build_context_root:
            add_field(digest, b"build-context")
            add_field(
                digest,
                git_value(args.build_context_root, "rev-parse", "--verify", "HEAD"),
            )
            add_field(
                digest,
                git_value(args.build_context_root, "rev-parse", "--abbrev-ref", "HEAD"),
            )
            add_field(digest, datetime.date.today().isoformat().encode("ascii"))
            for name in BUILD_CONTEXT_ENV:
                add_field(digest, name.encode("ascii"))
                add_field(
                    digest,
                    os.environ.get(name, "").encode(
                        "utf-8", errors="surrogateescape"
                    ),
                )
        files = collect(paths)
        relative_root = (
            os.path.abspath(args.relative_root) if args.relative_root else None
        )
        for path in files:
            digest_path = path
            if relative_root:
                digest_path = os.path.relpath(os.path.abspath(path), relative_root)
                if digest_path == ".." or digest_path.startswith(f"..{os.sep}"):
                    raise OSError(f"input is outside --relative-root: {path}")
            add_field(digest, b"file")
            add_field(digest, digest_path.encode("utf-8", errors="surrogateescape"))
            add_field(digest, hash_file(path))
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"gate hash failed: {error}", file=sys.stderr)
        return 1

    print(digest.hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
