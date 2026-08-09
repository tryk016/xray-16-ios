#!/usr/bin/env python3
"""Fixture-only regression coverage for the shared OpenAL provider contract."""

from __future__ import annotations

from pathlib import Path
import importlib.util
import subprocess
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("openal_provider_contract.py")
SPEC = importlib.util.spec_from_file_location("openal_provider_contract", MODULE_PATH)
assert SPEC and SPEC.loader
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)

GATE_HASH_PATH = Path(__file__).with_name("gate_hash.py")
GATE_HASH_SPEC = importlib.util.spec_from_file_location("gate_hash", GATE_HASH_PATH)
assert GATE_HASH_SPEC and GATE_HASH_SPEC.loader
gate_hash = importlib.util.module_from_spec(GATE_HASH_SPEC)
GATE_HASH_SPEC.loader.exec_module(gate_hash)


class Runner:
    def __init__(self, prefix: Path, *, bad: str = "") -> None:
        self.prefix = prefix
        self.bad = bad

    def __call__(self, arguments: tuple[str, ...] | list[str]) -> subprocess.CompletedProcess[bytes]:
        args = tuple(arguments)
        text = ""
        payload = b"member"
        code = 0
        if args[:2] == ("xcodebuild", "-project"):
            archive = self.prefix / "lib/libopenal.a"
            include = self.prefix / "include"
            include_al = include / "AL"
            escaped_include_al = str(include_al).replace(" ", "\\ ")
            target = args[args.index("-target") + 1]
            if target in ("xrSound", "xrEngine"):
                lines = [
                    f'    SYSTEM_HEADER_SEARCH_PATHS = "{include}" {escaped_include_al}',
                    "    HEADER_SEARCH_PATHS = /unrelated/include",
                    "    OTHER_CFLAGS = -DAL_LIBTYPE_STATIC",
                ]
                if self.bad == "header-search-include":
                    lines[0] = "    SYSTEM_HEADER_SEARCH_PATHS ="
                    lines[1] = f'    HEADER_SEARCH_PATHS = "{include}" "{include_al}"'
                if self.bad == "sound-missing-include" and target == "xrSound":
                    lines[0] = "    SYSTEM_HEADER_SEARCH_PATHS ="
                    lines[1] = "    HEADER_SEARCH_PATHS = /unrelated/include"
                if self.bad in ("include-prefix", "include-suffix", "include-shadow") and target == "xrSound":
                    mutation = self.bad.removeprefix("include-")
                    mutated = []
                    for path in (include, include_al):
                        if mutation == "prefix": mutated.append(f"/shadow{path}")
                        elif mutation == "suffix": mutated.append(f"{path}-shadow")
                        else: mutated.append(f"{path}/shadow")
                    lines[0] = f'    SYSTEM_HEADER_SEARCH_PATHS = "{mutated[0]}" "{mutated[1]}"'
                if self.bad == "engine-missing-define" and target == "xrEngine":
                    lines[2] = "    OTHER_CFLAGS ="
                if self.bad == "compile-framework" and target == "xrSound":
                    lines[2] += " -framework OpenAL"
                if self.bad == "compile-generic-link" and target == "xrEngine":
                    lines[2] += " -lopenal"
                forbidden_mutations = {
                    "compile-quoted-lopenal": '"-lopenal"',
                    "compile-escaped-latomic": "\\-latomic",
                    "compile-quoted-framework-pair": '"-framework" "OpenAL"',
                    "compile-quoted-framework-single": '"-framework OpenAL"',
                    "compile-quoted-framework-path": '"/System/Library/Frameworks/OpenAL.framework/OpenAL"',
                    "compile-quoted-openal-dylib": '"/other/libOpenAL.dylib"',
                    "compile-malformed-quote": '"-lopenal',
                    "compile-wl-lopenal": "-Wl,-lopenal",
                    "compile-wl-split-openal": "-Wl,-l,openal",
                    "compile-split-openal": "-l openal",
                    "compile-xlinker-lopenal": "-Xlinker -lopenal",
                    "compile-xlinker-split-atomic": "-Xlinker -l -Xlinker atomic",
                    "compile-response-file": "@unreviewed-linker.rsp",
                }
                if self.bad in forbidden_mutations and target == "xrSound":
                    lines[2] += f" {forbidden_mutations[self.bad]}"
                if self.bad == "duplicate-header" and target == "xrSound":
                    lines.append(f"    SYSTEM_HEADER_SEARCH_PATHS = {include}")
            else:
                lines = [
                    f'    OTHER_LDFLAGS = "{archive}" -framework AudioToolbox -framework CoreFoundation -framework CoreAudio',
                ]
                if self.bad == "final-missing-archive":
                    lines[0] = lines[0].replace(f'"{archive}"', "")
                if self.bad in ("archive-prefix", "archive-suffix", "archive-shadow"):
                    mutation = self.bad.removeprefix("archive-")
                    if mutation == "prefix": mutated = f"/shadow{archive}"
                    elif mutation == "suffix": mutated = f"{archive}-shadow"
                    else: mutated = f"{archive}/shadow"
                    lines[0] = lines[0].replace(f'"{archive}"', f'"{mutated}"')
                if self.bad == "archive-alternative-before":
                    lines[0] = lines[0].replace(" = ", ' = "/other/libOpenAL.a" ', 1)
                if self.bad == "archive-alternative-after":
                    lines[0] += ' "/other/LIBOPENAL.A"'
                if self.bad == "archive-duplicate-expected":
                    lines[0] += f' "{archive}"'
                if self.bad == "final-missing-framework":
                    lines[0] = lines[0].replace("-framework CoreAudio", "")
                if self.bad == "final-dylib":
                    lines[0] += " /usr/local/lib/libopenal.dylib"
                if self.bad == "final-atomic-link":
                    lines[0] += " -latomic"
                linker_mutations = {
                    "final-wl-lopenal": "-Wl,-lopenal",
                    "final-wl-split-openal": "-Wl,-l,openal",
                    "final-split-atomic": "-l atomic",
                    "final-xlinker-lopenal": "-Xlinker -lopenal",
                    "final-xlinker-equals-atomic": "-Xlinker=-latomic",
                    "final-response-file": "@unreviewed-linker.rsp",
                    "final-forwarded-colliding-archive": "-Wl,-force_load,/other/libOpenAL.a",
                    "final-forwarded-expected-archive": f"-Wl,-force_load,{archive}",
                    "final-xlinker-colliding-archive": "-Xlinker /other/libOpenAL.a",
                    "final-xlinker-expected-archive": f"-Xlinker {archive}",
                }
                if self.bad in linker_mutations:
                    lines[0] += f" {linker_mutations[self.bad]}"
                if self.bad == "duplicate-link":
                    lines.append(f'    OTHER_LDFLAGS = "{archive}"')
            text = "\n".join(lines)
        elif args[:2] == ("ar", "-t"):
            text = "autowah.cpp.o\nautowah.cpp.o\n" if self.bad == "duplicate-archive-members" else "a.o\nb.o\n"
        elif args[:2] == ("lipo", "-archs"):
            text = "arm64\n"
        elif args[:2] == ("otool", "-l"):
            members = ["autowah.cpp.o", "autowah.cpp.o"] if self.bad == "duplicate-archive-members" else ["a.o", "b.o"]
            header = f"Archive : {args[-1]}\n"
            if self.bad == "malformed-archive-header":
                header = f"Archive : /unexpected/{Path(args[-1]).name}\n"
            sections = [header]
            for index, member in enumerate(members):
                if self.bad == "missing-member-build" and index == 1:
                    body = "Load command 0\n      cmd LC_SEGMENT_64\n"
                else:
                    platform = "7" if self.bad == "wrong-member-build" and index == 1 else "2"
                    body = f"Load command 0\n      cmd LC_BUILD_VERSION\n      platform {platform}\n      minos 16.4\n"
                sections.append(f"{args[-1]}({member}):\n{body}")
            text = "".join(sections)
        elif args[:3] == ("xcrun", "vtool", "-show-build"):
            platform = "IOS" if self.bad != "wrong-platform" else "IOSSIMULATOR"
            text = f"cmd LC_BUILD_VERSION\nplatform {platform}\nminos 16.4\n"
        elif len(args) == 2 and args[0] == "nm":
            symbol_type = "T" if Path(args[-1]).name == "libopenal.a" else "t"
            text = "\n".join(f"00000000 {symbol_type} _{symbol}" for symbol in contract.REQUIRED_SYMBOLS) + "\n"
            if self.bad == "missing-symbol": text = text.replace("_alcGetProcAddress", "_wrong")
            if self.bad == "data-only-symbol": text = text.replace(
                f"{symbol_type} _alcGetProcAddress", "D _alcGetProcAddress"
            )
        elif args[:2] == ("nm", "-u"):
            if self.bad == "atomic": text = "                 U ___atomic_load_8\n"
            elif self.bad == "libcpp-atomic": text = "                 U __ZNSt3__120__libcpp_atomic_waitEPVKvi\n"
            elif self.bad == "archive-required-reference" and Path(args[-1]).name == "libopenal.a":
                text = "                 U _alcGetProcAddress\n"
            elif self.bad == "imported-final" and Path(args[-1]).name != "libopenal.a":
                text = "                 U _alcDevicePauseSOFT\n"
        elif args[:2] == ("otool", "-L"):
            weak_dependencies = (
                "/System/Library/Frameworks/GameController.framework/GameController",
                "/System/Library/Frameworks/CoreHaptics.framework/CoreHaptics",
            )
            dependencies = [
                "/usr/lib/libSystem.B.dylib",
                *contract.REQUIRED_SYSTEM_DEPENDENCIES,
                *weak_dependencies,
            ]
            missing = {
                "otool-missing-audiotoolbox": contract.REQUIRED_SYSTEM_DEPENDENCIES[0],
                "otool-missing-corefoundation": contract.REQUIRED_SYSTEM_DEPENDENCIES[1],
                "otool-missing-coreaudio": contract.REQUIRED_SYSTEM_DEPENDENCIES[2],
            }
            if self.bad in missing:
                dependencies.remove(missing[self.bad])
            weak_required = {
                "otool-weak-audiotoolbox": contract.REQUIRED_SYSTEM_DEPENDENCIES[0],
                "otool-weak-corefoundation": contract.REQUIRED_SYSTEM_DEPENDENCIES[1],
                "otool-weak-coreaudio": contract.REQUIRED_SYSTEM_DEPENDENCIES[2],
            }
            if self.bad == "otool-openal":
                dependencies.append("/System/Library/Frameworks/OpenAL.framework/OpenAL")
            if self.bad == "otool-dylib":
                dependencies.append("/usr/local/lib/libopenal.dylib")
            entries = []
            for dependency in dependencies:
                qualifier = ", weak" if (dependency in weak_dependencies or
                                          weak_required.get(self.bad) == dependency) else ""
                if self.bad == "otool-arbitrary-qualifier" and dependency.endswith("/GameController"):
                    qualifier = ", reexport"
                entries.append(
                    f"\t{dependency} (compatibility version 1.0.0, current version 14.0.21{qualifier})\n"
                )
            text = f"{args[-1]}:\n" + "".join(entries)
            if self.bad == "otool-malformed":
                text += "\tmalformed dependency\n"
        else:
            code = 1
        return subprocess.CompletedProcess(args, code, stdout=payload if args[:2] == ("ar", "-p") else text.encode(), stderr=b"")


class OpenALProviderContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="openal-provider-contract-", dir="/tmp")
        self.root = Path(self.temp.name)
        self.prefix = (self.root / "prefix with space").resolve()
        (self.prefix / "lib").mkdir(parents=True)
        (self.prefix / "include/AL").mkdir(parents=True)
        (self.prefix / "lib/libopenal.a").write_bytes(b"fixture-openal")
        for header in contract.REQUIRED_HEADERS:
            (self.prefix / "include/AL" / header).write_text("fixture\n")
        self.binary = self.root / "xr_3da"
        self.binary.write_bytes(b"fixture")
        self.project = self.root / "OpenXRay.xcodeproj"
        self.project.mkdir()
        self.cache = self.root / "CMakeCache.txt"
        self.write_cache()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_cache(self, **replace: str) -> None:
        values = {
            "CMAKE_PREFIX_PATH": str(self.prefix), "CMAKE_FIND_ROOT_PATH": str(self.prefix),
            "OPENAL_INCLUDE_DIR": str(self.prefix / "include"), "OPENAL_LIBRARY": str(self.prefix / "lib/libopenal.a"),
            "XRAY_OPENAL_PREFIX": str(self.prefix), "XRAY_OPENAL_LIBRARY": str(self.prefix / "lib/libopenal.a"),
            "XRAY_OPENAL_INCLUDE_ROOT": str(self.prefix / "include"),
            "XRAY_OPENAL_PROVIDER": contract.PROVIDER,
        }
        values.update(replace)
        self.cache.write_text("".join(f"{key}:STRING={value}\n" for key, value in values.items()))

    def configured(self, bad: str = "") -> None:
        contract.validate_configured(self.cache, self.prefix, self.project, "iphoneos", Runner(self.prefix, bad=bad))

    def artifact(self, bad: str = "") -> None:
        contract.validate_artifact(self.prefix, self.binary, "iphoneos", Runner(self.prefix, bad=bad))

    def test_happy_path_and_runtime_record(self) -> None:
        self.configured()
        fields = contract.validate_artifact(self.prefix, self.binary, "iphoneos", Runner(self.prefix))
        self.assertEqual(fields["openal_provider"], contract.PROVIDER)
        log = self.root / "runtime.log"
        log.write_text('iOS OpenAL provider v1 vendor="OpenAL Community" renderer="OpenAL Soft" version="1.1 ALSOFT 1.25.2" extension=1 pause_proc=1 resume_proc=1\n')
        self.assertEqual(contract.validate_runtime_log(log)["openal_provider"], contract.PROVIDER)

    def test_cache_rejects_double_mismatched_or_outside_prefix(self) -> None:
        for mutation in ("double", "mismatched", "outside"):
            with self.subTest(mutation=mutation):
                self.write_cache()
                if mutation == "double":
                    self.cache.write_text(self.cache.read_text() + f"CMAKE_PREFIX_PATH:STRING={self.prefix}\n")
                elif mutation == "mismatched": self.write_cache(CMAKE_FIND_ROOT_PATH=str(self.prefix / "other"))
                else: self.write_cache(OPENAL_LIBRARY="/usr/local/lib/libopenal.dylib")
                with self.assertRaises(contract.ContractError): self.configured()

    def test_compile_targets_require_their_own_include_and_static_define(self) -> None:
        self.configured("header-search-include")
        for bad in ("sound-missing-include", "include-prefix", "include-suffix", "include-shadow",
                    "engine-missing-define"):
            with self.subTest(bad=bad):
                with self.assertRaises(contract.ContractError): self.configured(bad)

    def test_final_target_requires_its_own_archive_and_frameworks(self) -> None:
        for bad in ("final-missing-archive", "archive-prefix", "archive-suffix", "archive-shadow",
                    "archive-alternative-before", "archive-alternative-after", "archive-duplicate-expected",
                    "final-missing-framework"):
            with self.subTest(bad=bad):
                with self.assertRaises(contract.ContractError): self.configured(bad)

    def test_all_targets_reject_forbidden_provider_link_forms(self) -> None:
        for bad in ("compile-framework", "compile-generic-link", "compile-quoted-lopenal",
                    "compile-escaped-latomic", "compile-quoted-framework-pair",
                    "compile-quoted-framework-single", "compile-quoted-framework-path",
                    "compile-quoted-openal-dylib", "compile-malformed-quote",
                    "compile-wl-lopenal", "compile-wl-split-openal", "compile-split-openal",
                    "compile-xlinker-lopenal", "compile-xlinker-split-atomic",
                    "compile-response-file", "final-dylib", "final-atomic-link",
                    "final-wl-lopenal", "final-wl-split-openal", "final-split-atomic",
                    "final-xlinker-lopenal", "final-xlinker-equals-atomic",
                    "final-response-file", "final-forwarded-colliding-archive",
                    "final-forwarded-expected-archive", "final-xlinker-colliding-archive",
                    "final-xlinker-expected-archive"):
            with self.subTest(bad=bad):
                with self.assertRaises(contract.ContractError): self.configured(bad)

    def test_gate_hash_covers_audio_interruption_policy_test(self) -> None:
        self.assertIn("misc/ios/audio_interruption_policy_test.cpp", gate_hash.IOS_ARTIFACT_INPUTS)

    def test_used_settings_reject_duplicate_keys(self) -> None:
        for bad in ("duplicate-header", "duplicate-link"):
            with self.subTest(bad=bad):
                with self.assertRaises(contract.ContractError): self.configured(bad)

    def test_artifact_rejects_wrong_platform_symbols_and_invalid_dependencies(self) -> None:
        for bad in ("wrong-platform", "malformed-archive-header", "missing-member-build", "wrong-member-build", "missing-symbol", "data-only-symbol", "atomic", "otool-missing-audiotoolbox", "otool-missing-corefoundation", "otool-missing-coreaudio", "otool-weak-audiotoolbox", "otool-weak-corefoundation", "otool-weak-coreaudio", "otool-openal", "otool-dylib", "otool-malformed", "otool-arbitrary-qualifier", "imported-final"):
            with self.subTest(bad=bad):
                with self.assertRaises(contract.ContractError): self.artifact(bad)

    def test_archive_accepts_duplicate_member_names(self) -> None:
        self.artifact("duplicate-archive-members")

    def test_archive_allows_internal_required_references_but_final_binary_does_not(self) -> None:
        self.artifact("archive-required-reference")
        with self.assertRaises(contract.ContractError): self.artifact("imported-final")

    def test_artifact_accepts_libcpp_symbols_that_only_contain_atomic(self) -> None:
        self.artifact("libcpp-atomic")

    def test_artifact_rejects_missing_header_or_archive(self) -> None:
        (self.prefix / "include/AL/alext.h").unlink()
        with self.assertRaises(contract.ContractError): self.artifact()
        (self.prefix / "include/AL/alext.h").write_text("fixture\n")
        (self.prefix / "lib/libopenal.a").unlink()
        with self.assertRaises(contract.ContractError): self.artifact()

    def test_runtime_log_rejects_all_provider_mutations(self) -> None:
        good = 'iOS OpenAL provider v1 vendor="OpenAL Community" renderer="OpenAL Soft" version="1.1 ALSOFT 1.25.2" extension=1 pause_proc=1 resume_proc=1\n'
        for payload in ("", good + good, good.replace("OpenAL Community", "Apple"),
                        good.replace("ALSOFT 1.25.2", "ALSOFT 1.24.0"),
                        good.replace('version="1.1 ALSOFT 1.25.2"', 'version="illegal 1.1 ALSOFT 1.25.2"'),
                        good.replace('version="1.1 ALSOFT 1.25.2"', 'version="1.1 ALSOFT 1.25.2 illegal"'),
                        good.replace("pause_proc=1", "pause_proc=0"),
                        "* iOS OpenAL provider vendor=bad\n"):
            with self.subTest(payload=payload):
                log = self.root / "runtime.log"; log.write_text(payload)
                with self.assertRaises(contract.ContractError): contract.validate_runtime_log(log)

    def test_runtime_log_rejects_prefixed_record_as_malformed(self) -> None:
        log = self.root / "runtime.log"
        log.write_text('* iOS OpenAL provider v1 vendor="OpenAL Community" renderer="OpenAL Soft" version="1.1 ALSOFT 1.25.2" extension=1 pause_proc=1 resume_proc=1\n')
        with self.assertRaisesRegex(contract.ContractError, r"matches=0, malformed=1"):
            contract.validate_runtime_log(log)


if __name__ == "__main__":
    unittest.main()
