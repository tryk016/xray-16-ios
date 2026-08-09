#!/usr/bin/env python3
"""Fail-closed OpenAL Soft provider proof shared by all iOS gates.

The checks deliberately consume tool output rather than guessing from CMake
source.  Unit tests inject ``runner`` and therefore do not need Xcode, a
Simulator, or a connected device.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Callable, Sequence


PROVIDER = "OpenALSoft-1.25.2-static"
MINOS = "16.4"
PLATFORMS = {"iphoneos": "IOS", "iphonesimulator": "IOSSIMULATOR"}
ARCHIVE_PLATFORM_NUMBERS = {"iphoneos": "2", "iphonesimulator": "7"}
REQUIRED_HEADERS = ("al.h", "alc.h", "alext.h")
REQUIRED_SYMBOLS = ("alcDevicePauseSOFT", "alcDeviceResumeSOFT", "alGetString", "alcGetProcAddress")
REQUIRED_SYSTEM_DEPENDENCIES = (
    "/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox",
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
    "/System/Library/Frameworks/CoreAudio.framework/CoreAudio",
)
OTOOL_DEPENDENCY = re.compile(
    r"^\t(?P<path>/\S+) "
    r"\(compatibility version \d+\.\d+\.\d+, "
    r"current version \d+\.\d+\.\d+(?P<weak>, weak)?\)$"
)
RUNTIME_RECORD = re.compile(
    r'^iOS OpenAL provider v1 vendor="(?P<vendor>[^"]+)" '
    r'renderer="(?P<renderer>[^"]+)" version="(?P<version>[^"]+)" '
    r'extension=(?P<extension>[01]) pause_proc=(?P<pause>[01]) '
    r'resume_proc=(?P<resume>[01])$'
)


class ContractError(RuntimeError):
    """The provider is not exactly the reviewed static OpenAL Soft contract."""


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[bytes]]


def fail(message: str) -> None:
    raise ContractError(message)


def command(arguments: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(tuple(arguments), check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def run(runner: Runner, *arguments: str) -> bytes:
    result = runner(arguments)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        fail(f"command failed: {' '.join(arguments)}{': ' + detail if detail else ''}")
    return result.stdout


def regular(path: Path, description: str) -> Path:
    if path.is_symlink() or not path.is_file():
        fail(f"{description} must be a regular non-symlink file: {path}")
    return path.resolve(strict=True)


def exact_platform(platform: str) -> str:
    try:
        return PLATFORMS[platform]
    except KeyError:
        fail(f"unsupported iOS platform: {platform}")
        raise AssertionError("unreachable")


def parse_cache(cache: Path) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    try:
        rows = cache.read_text(encoding="utf-8", errors="strict").splitlines()
    except OSError as error:
        fail(f"cannot read CMake cache: {error}")
    for row in rows:
        if not row or row.startswith("//") or row.startswith("#") or ":" not in row or "=" not in row:
            continue
        left, value = row.split("=", 1)
        key, _type = left.split(":", 1)
        values.setdefault(key, []).append(value)
    return values


def one_cache_value(values: dict[str, list[str]], key: str) -> str:
    matches = values.get(key, [])
    if len(matches) != 1:
        fail(f"CMakeCache must contain exactly one {key}")
    return matches[0]


def exact_path(value: str, expected: Path, key: str) -> None:
    if value != str(expected):
        fail(f"CMakeCache {key} must be exactly {expected}, got {value!r}")


def validate_cache(cache: Path, prefix: Path) -> None:
    cache = regular(cache, "CMakeCache")
    prefix = prefix.resolve(strict=True)
    values = parse_cache(cache)
    for key in ("CMAKE_PREFIX_PATH", "CMAKE_FIND_ROOT_PATH"):
        exact_path(one_cache_value(values, key), prefix, key)
    expected = {
        "OPENAL_INCLUDE_DIR": prefix / "include",
        "OPENAL_LIBRARY": prefix / "lib" / "libopenal.a",
        "XRAY_OPENAL_PREFIX": prefix,
        "XRAY_OPENAL_LIBRARY": prefix / "lib" / "libopenal.a",
        "XRAY_OPENAL_INCLUDE_ROOT": prefix / "include",
    }
    for key, path in expected.items():
        exact_path(one_cache_value(values, key), path, key)
    if one_cache_value(values, "XRAY_OPENAL_PROVIDER") != PROVIDER:
        fail("XRAY_OPENAL_PROVIDER is not OpenALSoft-1.25.2-static")
    for key, matches in values.items():
        if key.startswith(("OPENAL_", "XRAY_OPENAL_")):
            if len(matches) != 1:
                fail(f"CMakeCache must contain exactly one {key}")
            value = matches[0]
            if "OpenAL.framework" in value or ".dylib" in value or "-lopenal" in value or "-latomic" in value:
                fail(f"CMakeCache {key} contains a forbidden OpenAL/atomic value")


def optional_setting_value(settings: str, key: str) -> str | None:
    matches = re.findall(rf"(?m)^\s*{re.escape(key)}\s*=\s*(.*)$", settings)
    if len(matches) > 1:
        fail(f"xcodebuild settings must not contain duplicate {key}")
    return matches[0] if matches else None


def setting_tokens(settings: str, keys: Sequence[str]) -> list[str]:
    tokens: list[str] = []
    for key in keys:
        value = optional_setting_value(settings, key)
        if value is None:
            continue
        try:
            tokens.extend(shlex.split(value, posix=True))
        except ValueError as error:
            fail(f"xcodebuild {key} has malformed quoting or escaping: {error}")
    return tokens


def reject_linker_arguments(arguments: Sequence[str], target: str, archive_token: str,
                            *, allow_expected_archive: bool) -> None:
    """Reject provider substitutions after shell/linker argument expansion.

    ``xcodebuild -showBuildSettings`` emits shell-like values.  The compiler
    driver can then forward a second argument language to ld through ``-Wl``
    and ``-Xlinker``.  Treat response files as opaque: accepting one would
    make the reviewed settings depend on unchecked external content.
    """
    folded = [argument.casefold() for argument in arguments]
    for index, (argument, lower) in enumerate(zip(arguments, folded)):
        split = tuple(lower.split())
        if argument.startswith("@"):
            fail(f"{target} contains an unchecked linker response file")
        if split in (("-l", "openal"), ("-l", "atomic"), ("-framework", "openal")):
            fail(f"{target} contains forbidden OpenAL/atomic library selection")
        if lower in {"-lopenal", "-latomic"}:
            fail(f"{target} contains forbidden OpenAL/atomic library selection")
        if lower.startswith("-l") and lower[2:] in {"openal", "atomic"}:
            fail(f"{target} contains forbidden OpenAL/atomic library selection")
        if index and folded[index - 1] == "-l" and lower in {"openal", "atomic"}:
            fail(f"{target} contains forbidden OpenAL/atomic library selection")
        if index and folded[index - 1] == "-framework" and lower == "openal":
            fail(f"{target} contains forbidden OpenAL framework selection")
        if "openal.framework" in lower or lower.endswith(".dylib"):
            fail(f"{target} contains a forbidden OpenAL framework or dylib")
        filename = Path(argument).name.casefold()
        if filename == "libatomic.a" or (
                filename == "libopenal.a" and (not allow_expected_archive or argument != archive_token)):
            fail(f"{target} contains a colliding OpenAL/atomic archive: {argument}")


def reject_forbidden_provider_settings(settings: str, target: str, keys: Sequence[str],
                                       archive_token: str) -> list[str]:
    tokens = setting_tokens(settings, keys)
    direct: list[str] = []
    forwarded: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-Wl,"):
            forwarded.extend(token[4:].split(","))
        elif token == "-Xlinker":
            if index + 1 == len(tokens):
                fail(f"{target} has a dangling -Xlinker")
            forwarded.append(tokens[index + 1])
            index += 1
        elif token.startswith("-Xlinker="):
            forwarded.append(token.removeprefix("-Xlinker="))
        else:
            direct.append(token)
        index += 1
    reject_linker_arguments(direct, target, archive_token, allow_expected_archive=True)
    reject_linker_arguments(forwarded, target, archive_token, allow_expected_archive=False)
    return tokens


def validate_settings(project: Path, prefix: Path, runner: Runner) -> None:
    project = regular(project, "Xcode project") if project.is_file() else project.resolve(strict=True)
    archive = prefix / "lib" / "libopenal.a"
    include_root = prefix / "include"
    include_al = include_root / "AL"
    compile_keys = (
        "SYSTEM_HEADER_SEARCH_PATHS",
        "HEADER_SEARCH_PATHS",
        "OTHER_CFLAGS",
        "OTHER_CPLUSPLUSFLAGS",
        "GCC_PREPROCESSOR_DEFINITIONS",
    )
    relevant_keys = (*compile_keys, "OTHER_LDFLAGS")
    for target in ("xrSound", "xrEngine", "xr_3da"):
        output = run(runner, "xcodebuild", "-project", str(project), "-target", target,
                     "-configuration", "Release", "-showBuildSettings").decode("utf-8", errors="strict")
        reject_forbidden_provider_settings(output, target, relevant_keys, str(archive))
        if target in ("xrSound", "xrEngine"):
            compile_tokens = setting_tokens(output, compile_keys)
            required_includes = {str(include_root), str(include_al)}
            if not required_includes.issubset(compile_tokens):
                fail(f"{target} does not include exact OpenAL roots {include_root} and {include_al}")
            if not any(token in ("AL_LIBTYPE_STATIC", "AL_LIBTYPE_STATIC=1",
                                 "-DAL_LIBTYPE_STATIC", "-DAL_LIBTYPE_STATIC=1")
                       for token in compile_tokens):
                fail(f"{target} lacks AL_LIBTYPE_STATIC")
            continue

        link_tokens = setting_tokens(output, ("OTHER_LDFLAGS",))
        archive_token = str(archive)
        colliding_archives = [token for token in link_tokens
                              if Path(token).name.casefold() == "libopenal.a" and token != archive_token]
        if link_tokens.count(archive_token) != 1 or colliding_archives:
            fail(f"xr_3da must link exactly one exact OpenAL archive {archive}; collisions={colliding_archives}")
        for framework in ("AudioToolbox", "CoreFoundation", "CoreAudio"):
            if not any(pair == ("-framework", framework) for pair in zip(link_tokens, link_tokens[1:])):
                fail(f"xr_3da lacks required {framework} framework")


def parse_build(output: bytes, platform: str, subject: str) -> None:
    lines = output.decode("utf-8", errors="strict").splitlines()
    platforms = [line.strip().split(maxsplit=1)[1] for line in lines if line.strip().startswith("platform ")]
    minos = [line.strip().split(maxsplit=1)[1] for line in lines if line.strip().startswith("minos ")]
    build_versions = [line.strip() for line in lines if line.strip() == "cmd LC_BUILD_VERSION"]
    legacy = [line.strip() for line in lines if line.strip().startswith("cmd LC_VERSION_MIN_")]
    expected = exact_platform(platform)
    if platforms != [expected] or minos != [MINOS] or build_versions != ["cmd LC_BUILD_VERSION"] or legacy:
        fail(f"{subject} must be arm64 {expected} LC_BUILD_VERSION minOS {MINOS}; got platforms={platforms}, minos={minos}")


def validate_macho(path: Path, platform: str, subject: str, runner: Runner) -> None:
    archs = run(runner, "lipo", "-archs", str(path)).decode("utf-8", errors="strict").strip()
    if archs != "arm64":
        fail(f"{subject} must contain exactly one arm64 slice, got {archs!r}")
    parse_build(run(runner, "xcrun", "vtool", "-show-build", str(path)), platform, subject)


def nm_symbols(path: Path, runner: Runner) -> tuple[set[str], set[str]]:
    output = run(runner, "nm", str(path)).decode("utf-8", errors="strict")
    defined: set[str] = set()
    for row in output.splitlines():
        fields = row.split()
        if len(fields) < 2:
            continue
        symbol = strip_macho_leading_underscore(fields[-1])
        symbol_type = fields[-2]
        if symbol_type in ("T", "t"):
            defined.add(symbol)
    undefined_output = run(runner, "nm", "-u", str(path)).decode("utf-8", errors="strict")
    undefined = {
        strip_macho_leading_underscore(row.split()[-1])
        for row in undefined_output.splitlines()
        if row.split()
    }
    return defined, undefined


def strip_macho_leading_underscore(symbol: str) -> str:
    """Remove only the one underscore Mach-O adds to an external symbol."""
    return symbol[1:] if symbol.startswith("_") else symbol


def validate_symbols(path: Path, runner: Runner, subject: str, *, reject_required_imports: bool = True) -> None:
    defined, undefined = nm_symbols(path, runner)
    missing = sorted(set(REQUIRED_SYMBOLS) - defined)
    imported = sorted(set(REQUIRED_SYMBOLS) & undefined)
    if missing or (reject_required_imports and imported):
        fail(f"{subject} must define required OpenAL symbols; missing={missing}, imported={imported}")
    atomic = sorted(symbol for symbol in undefined if symbol.startswith("__atomic_"))
    if atomic:
        fail(f"{subject} imports forbidden atomic symbols: {atomic}")


def archive_members(archive: Path, runner: Runner) -> list[str]:
    members = run(runner, "ar", "-t", str(archive)).decode("utf-8", errors="strict").splitlines()
    if not members or any(not member or "/" in member or "\\" in member for member in members):
        fail("OpenAL archive has an invalid or empty member list")
    return members


def parse_archive_build_records(output: bytes, archive: Path, platform: str, member_count: int) -> None:
    expected_platform = ARCHIVE_PLATFORM_NUMBERS[platform]
    expected_header = f"Archive : {archive}"
    sections: list[list[str]] = []
    current: list[str] | None = None
    saw_header = False
    for line in output.decode("utf-8", errors="strict").splitlines():
        if re.fullmatch(r".+\(.+\):", line):
            if current is not None:
                sections.append(current)
            current = []
        elif current is None:
            if line == expected_header and not saw_header:
                saw_header = True
            elif line.strip():
                fail("otool archive output has content before its first member section")
        else:
            current.append(line)
    if not saw_header:
        fail(f"otool archive output must begin with exactly {expected_header!r}")
    if current is not None:
        sections.append(current)
    if len(sections) != member_count:
        fail(f"otool archive member section count must be {member_count}, got {len(sections)}")
    for index, section in enumerate(sections, start=1):
        stripped = [line.strip() for line in section]
        build_commands = [line for line in stripped if line == "cmd LC_BUILD_VERSION"]
        legacy = [line for line in stripped if line.startswith("cmd LC_VERSION_MIN_")]
        platforms = [match.group(1) for line in section if (match := re.fullmatch(r"\s*platform\s+(\d+)\s*", line))]
        minos = [match.group(1) for line in section if (match := re.fullmatch(r"\s*minos\s+(\S+)\s*", line))]
        if build_commands != ["cmd LC_BUILD_VERSION"] or legacy or platforms != [expected_platform] or minos != [MINOS]:
            fail(
                f"OpenAL archive member section {index} must contain exactly one "
                f"LC_BUILD_VERSION platform {expected_platform} minOS {MINOS}; "
                f"got builds={len(build_commands)}, platforms={platforms}, minos={minos}, legacy={legacy}"
            )


def validate_archive(archive: Path, platform: str, runner: Runner) -> None:
    members = [member for member in archive_members(archive, runner) if not member.startswith("__.SYMDEF")]
    if not members:
        fail("OpenAL archive has no object members")
    archs = run(runner, "lipo", "-archs", str(archive)).decode("utf-8", errors="strict").strip()
    if archs != "arm64":
        fail(f"OpenAL archive must contain exactly one arm64 slice, got {archs!r}")
    parse_archive_build_records(run(runner, "otool", "-l", str(archive)), archive, platform, len(members))
    validate_symbols(archive, runner, "OpenAL archive", reject_required_imports=False)


def parse_otool_dependencies(output: bytes, binary: Path) -> list[tuple[str, bool]]:
    lines = output.decode("utf-8", errors="strict").splitlines()
    if not lines or lines[0] != f"{binary}:":
        fail(f"final Mach-O otool -L header must be exactly {binary}:")
    dependencies: list[tuple[str, bool]] = []
    for line in lines[1:]:
        match = OTOOL_DEPENDENCY.fullmatch(line)
        if match is None:
            fail(f"final Mach-O otool -L has malformed dependency entry: {line!r}")
        dependencies.append((match.group("path"), match.group("weak") is not None))
    return dependencies


def validate_artifact(prefix: Path, binary: Path, platform: str, runner: Runner = command) -> dict[str, str]:
    prefix = prefix.resolve(strict=True)
    archive = regular(prefix / "lib" / "libopenal.a", "OpenAL archive")
    for header in REQUIRED_HEADERS:
        regular(prefix / "include" / "AL" / header, f"OpenAL header {header}")
    validate_archive(archive, platform, runner)
    binary = regular(binary, "final Mach-O")
    validate_macho(binary, platform, "final Mach-O", runner)
    validate_symbols(binary, runner, "final Mach-O")
    dependencies = parse_otool_dependencies(run(runner, "otool", "-L", str(binary)), binary)
    for dependency in REQUIRED_SYSTEM_DEPENDENCIES:
        occurrences = [weak for path, weak in dependencies if path == dependency]
        if occurrences != [False]:
            fail(f"final Mach-O must link exactly one strong required system dependency {dependency}")
    if any(re.search(r"OpenAL\.framework|libopenal[^\s]*\.dylib", path, re.IGNORECASE)
           for path, _weak in dependencies):
        fail("final Mach-O links forbidden OpenAL framework or dylib")
    return {"openal_provider": PROVIDER, "openal_sha256": sha256(archive)}


def validate_configured(cache: Path, prefix: Path, project: Path, platform: str,
                         runner: Runner = command) -> dict[str, str]:
    exact_platform(platform)
    prefix = prefix.resolve(strict=True)
    validate_cache(cache, prefix)
    validate_settings(project, prefix, runner)
    return {"openal_provider": PROVIDER, "openal_sha256": sha256(prefix / "lib" / "libopenal.a")}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with regular(path, "hash input").open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def validate_runtime_log(log: Path) -> dict[str, str]:
    log = regular(log, "runtime log")
    records = [RUNTIME_RECORD.fullmatch(row) for row in log.read_text(encoding="utf-8", errors="strict").splitlines()]
    matches = [record for record in records if record is not None]
    malformed = [row for row in log.read_text(encoding="utf-8", errors="strict").splitlines()
                 if "iOS OpenAL provider" in row and RUNTIME_RECORD.fullmatch(row) is None]
    if malformed or len(matches) != 1:
        fail(f"runtime log must contain exactly one well-formed OpenAL provider record; matches={len(matches)}, malformed={len(malformed)}")
    values = matches[0].groupdict()
    if values["vendor"] != "OpenAL Community" or values["renderer"] != "OpenAL Soft":
        fail("runtime OpenAL vendor/renderer does not identify OpenAL Soft")
    if values["version"] != "1.1 ALSOFT 1.25.2":
        fail("runtime OpenAL version must be exactly 1.1 ALSOFT 1.25.2")
    if any(values[name] != "1" for name in ("extension", "pause", "resume")):
        fail("runtime OpenAL extension/proc proof is incomplete")
    return {"openal_provider": PROVIDER}


def emit(fields: dict[str, str]) -> None:
    for key, value in fields.items():
        print(f"{key}={value}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    configured = commands.add_parser("configured")
    configured.add_argument("--cache", required=True)
    configured.add_argument("--prefix", required=True)
    configured.add_argument("--project", required=True)
    configured.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    artifact = commands.add_parser("artifact")
    artifact.add_argument("--prefix", required=True)
    artifact.add_argument("--binary", required=True)
    artifact.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    runtime = commands.add_parser("runtime-log")
    runtime.add_argument("--log", required=True)
    digest = commands.add_parser("sha256")
    digest.add_argument("--archive", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "configured":
            emit(validate_configured(Path(args.cache), Path(args.prefix), Path(args.project), args.platform))
        elif args.command == "artifact":
            emit(validate_artifact(Path(args.prefix), Path(args.binary), args.platform))
        elif args.command == "runtime-log":
            emit(validate_runtime_log(Path(args.log)))
        else:
            emit({"openal_sha256": sha256(Path(args.archive))})
    except (ContractError, OSError, UnicodeError) as error:
        print(f"openal provider contract: FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
