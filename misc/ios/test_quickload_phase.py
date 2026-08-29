#!/usr/bin/env python3
"""Regression and source-contract tests for iOS QuickLoad phase diagnostics."""

from __future__ import annotations

import errno
import json
import os as _test_feedback_os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

if _test_feedback_os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment(
            "python::misc/ios/test_quickload_phase.py", sys.argv,
        )
    except BaseException:
        pass

import quickload_phase_oracle as oracle


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "misc/ios/quickload_phase_oracle.py"
PHASES = (
    "deferred", "event_begin", "objects_removed", "restart_begin", "old_alife_destroyed",
    "ids_cleared", "alife_construct_begin", "new_alife_constructed", "precache_complete",
    "restart_complete", "event_complete",
)
MEMORY_PHASES = frozenset((
    "event_begin", "objects_removed", "old_alife_destroyed", "alife_construct_begin",
    "new_alife_constructed", "precache_complete", "restart_complete",
))


def marker(phase: str, *, pid: int = 4242, request: int = 7, frame: int = 100,
           memory: str | None = None, values: tuple[int, int, int, int, int, int] | None = None) -> str:
    actual_memory = memory if memory is not None else ("task_vm_info" if phase in MEMORY_PHASES else "none")
    actual_values = values if values is not None else ((1, 2, 3, 4, 5, 6) if actual_memory == "task_vm_info" else (0, 0, 0, 0, 0, 0))
    fields = " ".join(f"{name}={value}" for name, value in zip((
        "phys_current_k", "phys_peak_k", "resident_current_k", "resident_peak_k",
        "compressed_current_k", "compressed_peak_k",
    ), actual_values))
    return (f"* iOS quickload phase v1 pid={pid} request={request} frame={frame} "
            f"phase={phase} memory={actual_memory} {fields}")


class QuickLoadPhaseOracleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_cli(self, lines: list[str], *arguments: str, pid: int = 4242,
                request: int = 7) -> subprocess.CompletedProcess[str]:
        source = self.temp / "xr_boot.log"
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(source), "--expected-pid", str(pid),
             "--expected-request", str(request), *arguments],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def assert_fail(self, lines: list[str], text: str, *arguments: str,
                    pid: int = 4242, request: int = 7) -> None:
        result = self.run_cli(lines, *arguments, pid=pid, request=request)
        self.assertEqual(2, result.returncode, result.stderr + result.stdout)
        self.assertIn(text, result.stderr)
        self.assertEqual("", result.stdout)

    def test_complete_sequence_is_deterministic(self) -> None:
        lines = [marker(phase, frame=100 + index) for index, phase in enumerate(PHASES)]
        first = self.run_cli(lines)
        second = self.run_cli(lines)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        report = json.loads(first.stdout)
        self.assertEqual("PASS", report["result"])
        self.assertEqual(list(PHASES), [item["phase"] for item in report["markers"]])
        self.assertIsNone(report["next_expected"])

    def test_truncation_requires_explicit_opt_in(self) -> None:
        lines = [marker(phase) for phase in PHASES[:4]]
        self.assert_fail(lines, "incomplete phase sequence")
        result = self.run_cli(lines, "--allow-truncated")
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("TRUNCATED", report["result"])
        self.assertEqual("restart_begin", report["last_completed"])
        self.assertEqual("old_alife_destroyed", report["next_expected"])

    def test_order_duplicate_and_skip_fail_closed(self) -> None:
        lines = [marker(phase) for phase in PHASES]
        duplicate = lines[:3] + [lines[2]] + lines[3:]
        swapped = lines[:4] + [lines[5], lines[4]] + lines[6:]
        skipped = lines[:5] + lines[6:]
        for mutated in (duplicate, swapped, skipped):
            with self.subTest(mutated=mutated):
                self.assert_fail(mutated, "phase order mismatch")

    def test_identity_grammar_and_memory_consistency_fail_closed(self) -> None:
        complete = [marker(phase) for phase in PHASES]
        wrong_pid = [marker(phase, pid=4243) for phase in PHASES]
        wrong_request = [marker(phase, request=8) for phase in PHASES]
        malformed = complete.copy(); malformed[0] = complete[0].replace("pid=4242", "pid=04242")
        bad_none = complete.copy(); bad_none[0] = marker("deferred", values=(1, 0, 0, 0, 0, 0))
        bad_capture = complete.copy(); bad_capture[1] = marker("event_begin", memory="none")
        for mutated, message in ((wrong_pid, "no markers match"), (wrong_request, "no markers match"),
                                 (malformed, "malformed"), (bad_none, "memory=none"),
                                 (bad_capture, "must capture")):
            with self.subTest(message=message):
                self.assert_fail(mutated, message)

    def test_selects_one_well_formed_attempt_from_a_multi_attempt_log(self) -> None:
        other = [marker(phase, pid=99, request=123, frame=10 + index)
                 for index, phase in enumerate(PHASES)]
        selected = [marker(phase, frame=100 + index) for index, phase in enumerate(PHASES)]
        result = self.run_cli(other + selected)
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(4242, report["pid"])
        self.assertEqual(7, report["request"])
        self.assertEqual(list(PHASES), [item["phase"] for item in report["markers"]])
        self.assert_fail(other, "no markers match")

    def test_selected_frames_are_nondecreasing_and_may_be_equal(self) -> None:
        same_frame = [marker(phase, frame=100) for phase in PHASES]
        result = self.run_cli(same_frame)
        self.assertEqual(0, result.returncode, result.stderr)
        regressed = [marker(phase, frame=100 + index) for index, phase in enumerate(PHASES)]
        regressed[5] = marker(PHASES[5], frame=99)
        self.assert_fail(regressed, "frame regression")

    def test_unsigned_boundaries_and_unavailable_memory_are_accepted(self) -> None:
        lines = [marker(phase, pid=(1 << 31) - 1, request=(1 << 64) - 1,
                        frame=(1 << 32) - 1)
                 for phase in PHASES]
        lines[1] = marker("event_begin", pid=(1 << 31) - 1, request=(1 << 64) - 1,
                          frame=(1 << 32) - 1, memory="unavailable")
        result = self.run_cli(lines, pid=(1 << 31) - 1, request=(1 << 64) - 1)
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("unavailable", report["markers"][1]["memory"])
        too_large_request = lines.copy()
        too_large_request[0] = marker("deferred", pid=(1 << 31) - 1,
                                       request=1 << 64, frame=(1 << 32) - 1)
        self.assert_fail(too_large_request, "request exceeds", pid=(1 << 31) - 1,
                         request=(1 << 64) - 1)

    def test_unknown_version_and_malformed_prefix_fail_closed(self) -> None:
        complete = [marker(phase) for phase in PHASES]
        unknown_version = complete.copy()
        unknown_version[0] = unknown_version[0].replace("phase v1", "phase v2", 1)
        malformed_prefix = complete.copy()
        malformed_prefix[0] = "* iOS quickload phase v1 pid=4242 request=7"
        self.assert_fail(unknown_version, "malformed")
        self.assert_fail(malformed_prefix, "malformed")

    def test_empty_bounded_and_output_contracts(self) -> None:
        self.assert_fail(["ordinary log line"], "no iOS quickload")
        too_large_pid = [marker(phase) for phase in PHASES]
        too_large_pid[0] = marker("deferred", pid=1 << 31)
        self.assert_fail(too_large_pid, "pid exceeds")
        output = self.temp / "report.json"
        result = self.run_cli([marker(phase) for phase in PHASES], "--json-output", str(output))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(result.stdout, output.read_text(encoding="utf-8"))
        self.assert_fail([marker(phase) for phase in PHASES], "cannot create json output", "--json-output", str(output))

    def test_output_writer_retries_partial_writes(self) -> None:
        output = self.temp / "partial.json"
        payload = b'{"partial":true}\n'
        original_write = oracle.os.write
        writes: list[int] = []

        def partial_write(descriptor: int, data: bytes) -> int:
            writes.append(len(data))
            return original_write(descriptor, data[:max(1, len(data) // 3)])

        with mock.patch.object(oracle.os, "write", side_effect=partial_write):
            oracle.write_new_output(output, payload)
        self.assertGreater(len(writes), 1)
        self.assertEqual(payload, output.read_bytes())

    def test_output_writer_removes_its_partial_file_after_write_or_fsync_error(self) -> None:
        for operation in ("write", "fsync"):
            with self.subTest(operation=operation):
                output = self.temp / f"{operation}-error.json"
                failure = OSError(errno.EIO, operation)
                with mock.patch.object(oracle.os, operation, side_effect=failure):
                    with self.assertRaisesRegex(oracle.OracleError, "cannot write json output"):
                        oracle.write_new_output(output, b"{}\n")
                self.assertFalse(output.exists())


class QuickLoadPhaseSourceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_writer_header = (ROOT / "src/xrCore/ios_private_save_writer.h").read_text(encoding="utf-8")
        self.private_writer = (ROOT / "src/xrCore/ios_private_save_writer.cpp").read_text(encoding="utf-8")
        self.file_writer = (ROOT / "src/xrCore/FS_internal.h").read_text(encoding="utf-8")
        self.locator_header = (ROOT / "src/xrCore/LocatorAPI.h").read_text(encoding="utf-8")
        self.locator = (ROOT / "src/xrCore/LocatorAPI.cpp").read_text(encoding="utf-8")
        self.storage = (ROOT / "src/xrGame/alife_storage_manager.cpp").read_text(encoding="utf-8")
        self.core_cmake = (ROOT / "src/xrCore/CMakeLists.txt").read_text(encoding="utf-8")
        self.helper_header = (ROOT / "src/xrGame/ios_quickload_phase.h").read_text(encoding="utf-8")
        self.helper = (ROOT / "src/xrGame/ios_quickload_phase.cpp").read_text(encoding="utf-8")
        self.network = (ROOT / "src/xrGame/Level_network_messages.cpp").read_text(encoding="utf-8")
        self.game = (ROOT / "src/xrGame/GamePersistent.cpp").read_text(encoding="utf-8")
        self.single_header = (ROOT / "src/xrGame/game_sv_single.h").read_text(encoding="utf-8")
        self.single = (ROOT / "src/xrGame/game_sv_single.cpp").read_text(encoding="utf-8")
        self.cmake = (ROOT / "src/xrGame/CMakeLists.txt").read_text(encoding="utf-8")

    def test_helper_is_ios_guarded_and_has_exact_marker_fields(self) -> None:
        self.assertIn("#if defined(XR_PLATFORM_APPLE_IOS)", self.helper_header)
        self.assertIn("#if defined(XR_PLATFORM_APPLE_IOS)", self.helper)
        for field in ("* iOS quickload phase v1 pid=%d request=%llu frame=%u phase=%s memory=%s",
                      "phys_current_k=%llu", "phys_peak_k=%llu", "resident_current_k=%llu",
                      "resident_peak_k=%llu", "compressed_current_k=%llu", "compressed_peak_k=%llu"):
            self.assertIn(field, self.helper)
        self.assertIn("psIOSDiagnostics != 1", self.helper)
        self.assertIn("ios_memory::Capture()", self.helper)
        self.assertNotIn("stat_memory", self.helper)
        self.assertNotIn("mem_compact", self.helper)

    def test_source_registers_all_phases_and_selected_memory_boundaries(self) -> None:
        for phase in ("Deferred", "EventBegin", "ObjectsRemoved", "RestartBegin", "OldAlifeDestroyed",
                      "IdsCleared", "AlifeConstructBegin", "NewAlifeConstructed", "PrecacheComplete",
                      "RestartComplete", "EventComplete"):
            self.assertIn(f"IosQuickLoadPhase::{phase}", self.helper)
        for phase in ("EventBegin", "ObjectsRemoved", "OldAlifeDestroyed", "AlifeConstructBegin",
                      "NewAlifeConstructed", "PrecacheComplete", "RestartComplete"):
            self.assertIn(f"case IosQuickLoadPhase::{phase}:", self.helper)
        self.assertIn("const bool capture_memory = captures_memory(phase);", self.helper)
        self.assertIn("if (capture_memory)\n        snapshot = ios_memory::Capture();", self.helper)

    def test_p2_propagation_and_phase_order_are_exact(self) -> None:
        defer = self.network.index('Engine.Event.Defer("Game:QuickLoad", size_t(xr_strdup(saved_name)), quick_load_request);')
        deferred = self.network.index("IosQuickLoadPhase::Deferred", defer)
        self.assertLess(defer, deferred)
        self.assertIn("const u64 quick_load_request = P2;", self.game)
        event_begin = self.game.index("IosQuickLoadPhase::EventBegin")
        removed = self.game.index("IosQuickLoadPhase::ObjectsRemoved")
        restart = self.game.index("game->restart_simulator(saved_name, P2);")
        complete = self.game.index("IosQuickLoadPhase::EventComplete")
        self.assertLess(event_begin, removed)
        self.assertLess(removed, restart)
        self.assertLess(restart, complete)
        self.assertIn("void restart_simulator(LPCSTR saved_game_name, u64 quick_load_request);", self.single_header)

    def test_restart_order_and_no_memory_fix_are_preserved(self) -> None:
        restart_body = self.single[self.single.index("void game_sv_Single::restart_simulator"):]
        sequence = (
            "IosQuickLoadPhase::RestartBegin", "delete_data(m_alife_simulator);",
            "IosQuickLoadPhase::OldAlifeDestroyed", "server().clear_ids();",
            "IosQuickLoadPhase::IdsCleared", "IosQuickLoadPhase::AlifeConstructBegin",
            "m_alife_simulator = xr_new<CALifeSimulator>", "IosQuickLoadPhase::NewAlifeConstructed",
            "Device.PreCache(60, true);", "IosQuickLoadPhase::PrecacheComplete",
            "g_pGamePersistent->LoadEnd();", "IosQuickLoadPhase::RestartComplete",
        )
        positions = [restart_body.index(token) for token in sequence]
        self.assertEqual(positions, sorted(positions))
        for source in (self.network, self.game, self.single):
            self.assertIn("#if defined(XR_PLATFORM_APPLE_IOS)", source)
            self.assertNotIn("Memory.mem_compact", source)
        self.assertIn("ios_quickload_phase.cpp", self.cmake)
        self.assertIn("ios_quickload_phase.h", self.cmake)

        self.assertIn("#if defined(XR_PLATFORM_APPLE_IOS)", self.private_writer_header)
        self.assertIn("FILE* open_for_rewrite(const char* path);", self.private_writer_header)
        self.assertIn("bool finalize(FILE*& stream);", self.private_writer_header)
        for token in ("O_NOFOLLOW", "O_CREAT | O_EXCL", "fstat(descriptor, &status)",
                      "S_ISREG(status.st_mode)", "status.st_uid == getuid()",
                      "status.st_nlink == 1", "fchmod(descriptor, private_save_mode)",
                      'fdopen(descriptor, "wb")', "ftruncate(fileno(stream), 0)",
                      "const int flush_result = finalize_flush(stream);",
                      "const int close_result = finalize_close(stream);", "stream = nullptr;"):
            self.assertIn(token, self.private_writer)
        self.assertLess(self.private_writer.index("fstat(descriptor, &status)"),
                        self.private_writer.index("fchmod(descriptor, private_save_mode)"))
        self.assertLess(self.private_writer.index("fchmod(descriptor, private_save_mode)"),
                        self.private_writer.index('fdopen(descriptor, "wb")'))
        self.assertLess(self.private_writer.index('fdopen(descriptor, "wb")'),
                        self.private_writer.index("ftruncate(fileno(stream), 0)"))
        self.assertNotIn("chflags", self.private_writer)
        self.assertNotIn("fchflags", self.private_writer)
        self.assertIn("ios_private_save_writer.cpp", self.core_cmake)
        self.assertIn("ios_private_save_writer.h", self.core_cmake)
        self.assertIn("w_open_private", self.locator_header)
        self.assertIn("bool w_close_private(IWriter*& S);", self.locator_header)
        private_open = self.locator.index("IWriter* CLocatorAPI::w_open_private")
        self.assertIn("CFileWriter>(fname, false, true)", self.locator[private_open:])
        self.assertIn("if (!W->valid())", self.locator[private_open:])
        private_close = self.locator.index("bool CLocatorAPI::w_close_private")
        private_close_body = self.locator[private_close:]
        self.assertIn("const bool finalized = private_writer->close_private_save();", private_close_body)
        self.assertIn("const int finalization_error = errno;", private_close_body)
        self.assertIn("! Cannot securely finalize save file", private_close_body)
        self.assertIn("if (!finalized)\n    {", private_close_body)
        self.assertIn("string_path filesystem_name;", private_close_body)
        self.assertIn("convert_path_separators(filesystem_name);", private_close_body)
        self.assertIn("::stat(filesystem_name, &st)", private_close_body)
        self.assertIn("! Cannot register securely finalized save file", private_close_body)
        self.assertIn("Register(fname, VFS_STANDARD_FILE", private_close_body)
        self.assertLess(private_close_body.index("if (!finalized)"),
                        private_close_body.index("convert_path_separators(filesystem_name);"))
        self.assertLess(private_close_body.index("convert_path_separators(filesystem_name);"),
                        private_close_body.index("::stat(filesystem_name, &st)"))
        self.assertLess(private_close_body.index("::stat(filesystem_name, &st)"),
                        private_close_body.index("Register(fname, VFS_STANDARD_FILE"))
        self.assertIn("ios_private_save_writer::open_for_rewrite(conv_fn)", self.file_writer)
        private_call = self.storage.index("FS.w_open_private(temp)")
        failure = self.storage.index("if (!writer)", private_call)
        first_write = self.storage.index("writer->w_u32", failure)
        success = self.storage.index("successfully saved", first_write)
        self.assertLess(private_call, failure)
        self.assertLess(failure, first_write)
        self.assertLess(first_write, success)
        self.assertIn("! Cannot securely create saved game", self.storage[failure:first_write])
        self.assertIn("xr_strcpy(m_save_name, saveBackup);", self.storage[failure:first_write])
        finalization = self.storage.index("if (!FS.w_close_private(writer))", first_write)
        self.assertLess(finalization, success)
        self.assertIn("! Cannot securely finalize saved game", self.storage[finalization:success])
        self.assertIn("xr_strcpy(m_save_name, saveBackup);", self.storage[finalization:success])


class IOSPrivateSaveWriterFunctionalTests(unittest.TestCase):
    def test_production_helper_enforces_private_safe_rewrites(self) -> None:
        harness = r'''
#include "ios_private_save_writer.h"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
void require(const bool value, const char* message)
{
    if (!value)
    {
        std::fprintf(stderr, "%s: %s\\n", message, std::strerror(errno));
        std::exit(1);
    }
}

void write_all(const int descriptor, const char* value)
{
    const size_t length = std::strlen(value);
    require(write(descriptor, value, length) == static_cast<ssize_t>(length), "write failed");
}

void write_stream(FILE* stream, const char* value)
{
    const size_t length = std::strlen(value);
    require(std::fwrite(value, 1, length, stream) == length, "stream write failed");
}

int fail_flush(FILE*)
{
    errno = ENOSPC;
    return EOF;
}

int close_normally(FILE* stream)
{
    return std::fclose(stream);
}

int fail_close(FILE* stream)
{
    const int result = std::fclose(stream);
    if (result != 0)
        return result;
    errno = EIO;
    return EOF;
}

void create_file(const char* path, const mode_t mode, const char* value)
{
    const int descriptor = open(path, O_WRONLY | O_CREAT | O_TRUNC, mode);
    require(descriptor != -1, "create failed");
    write_all(descriptor, value);
    require(close(descriptor) == 0, "create close failed");
    require(chmod(path, mode) == 0, "chmod failed");
}

bool contents_equal(const char* path, const char* expected)
{
    const int descriptor = open(path, O_RDONLY);
    if (descriptor == -1)
        return false;
    char actual[128] = {};
    const ssize_t count = read(descriptor, actual, sizeof(actual) - 1);
    const int close_result = close(descriptor);
    return count == static_cast<ssize_t>(std::strlen(expected)) && close_result == 0 &&
        std::memcmp(actual, expected, static_cast<size_t>(count)) == 0;
}

std::string join(const char* directory, const char* name)
{
    return std::string(directory) + "/" + name;
}
}

int main()
{
    char directory[] = "/tmp/openxray-private-save-XXXXXX";
    require(mkdtemp(directory) != nullptr, "mkdtemp failed");
    const mode_t original_umask = umask(0777);

    const std::string created = join(directory, "created.scop");
    FILE* stream = ios_private_save_writer::open_for_rewrite(created.c_str());
    require(stream != nullptr, "new private open failed");
    int descriptor = fileno(stream);
    write_stream(stream, "new");
    require(fclose(stream) == 0, "new close failed");
    struct stat created_status;
    require(stat(created.c_str(), &created_status) == 0, "new stat failed");
    require((created_status.st_mode & 0777) == 0600, "new save mode is not 0600");
    require(contents_equal(created.c_str(), "new"), "new save content changed");
    require(umask(original_umask) == 0777, "restrictive umask was not active");

    const std::string existing = join(directory, "existing.scop");
    create_file(existing.c_str(), 0644, "previous");
#if defined(__APPLE__) && defined(UF_TRACKED)
    require(chflags(existing.c_str(), UF_TRACKED) == 0, "UF_TRACKED set failed");
#endif
    struct stat before_existing;
    require(stat(existing.c_str(), &before_existing) == 0, "existing pre-stat failed");
    stream = ios_private_save_writer::open_for_rewrite(existing.c_str());
    require(stream != nullptr, "existing private open failed");
    descriptor = fileno(stream);
    struct stat protected_existing;
    require(fstat(descriptor, &protected_existing) == 0, "existing fstat failed");
    require((protected_existing.st_mode & 0777) == 0600, "existing mode was not repaired before rewrite");
    require(protected_existing.st_size == 0, "existing save was not truncated after protection");
    write_stream(stream, "replacement");
    require(fclose(stream) == 0, "existing close failed");
    struct stat after_existing;
    require(stat(existing.c_str(), &after_existing) == 0, "existing post-stat failed");
    require(before_existing.st_ino == after_existing.st_ino, "existing save inode changed");
    require((after_existing.st_mode & 0777) == 0600, "existing save mode changed");
    require(contents_equal(existing.c_str(), "replacement"), "existing save content changed");
#if defined(__APPLE__) && defined(UF_TRACKED)
    require((after_existing.st_flags & UF_TRACKED) != 0, "UF_TRACKED was not preserved");
#endif

    const std::string target = join(directory, "target.scop");
    const std::string symlink_path = join(directory, "symlink.scop");
    create_file(target.c_str(), 0600, "symlink-target");
    require(symlink(target.c_str(), symlink_path.c_str()) == 0, "symlink create failed");
    struct stat target_before;
    require(stat(target.c_str(), &target_before) == 0, "symlink target pre-stat failed");
    errno = 0;
    require(ios_private_save_writer::open_for_rewrite(symlink_path.c_str()) == nullptr, "symlink was accepted");
    struct stat target_after;
    require(stat(target.c_str(), &target_after) == 0, "symlink target post-stat failed");
    require(target_before.st_ino == target_after.st_ino && target_before.st_size == target_after.st_size,
            "symlink target metadata changed");
    require(contents_equal(target.c_str(), "symlink-target"), "symlink target content changed");

    const std::string hard_source = join(directory, "hard-source.scop");
    const std::string hard_link = join(directory, "hard-link.scop");
    create_file(hard_source.c_str(), 0600, "hardlink-target");
    require(link(hard_source.c_str(), hard_link.c_str()) == 0, "hardlink create failed");
    errno = 0;
    require(ios_private_save_writer::open_for_rewrite(hard_link.c_str()) == nullptr, "hardlink was accepted");
    struct stat hard_status;
    require(stat(hard_source.c_str(), &hard_status) == 0, "hardlink stat failed");
    require(hard_status.st_nlink == 2, "hardlink count changed");
    require(contents_equal(hard_source.c_str(), "hardlink-target"), "hardlink target content changed");

    const std::string flush_failure = join(directory, "flush-failure.scop");
    stream = ios_private_save_writer::open_for_rewrite(flush_failure.c_str());
    require(stream != nullptr, "flush failure open failed");
    write_stream(stream, "flush-failure");
    const ios_private_save_writer::FinalizeOperations flush_failure_operations = {fail_flush, close_normally};
    ios_private_save_writer::set_finalize_operations_for_testing(&flush_failure_operations);
    errno = 0;
    require(!ios_private_save_writer::finalize(stream), "flush failure was accepted");
    require(stream == nullptr && errno == ENOSPC, "flush failure did not close and report ENOSPC");
    ios_private_save_writer::reset_finalize_operations_for_testing();

    const std::string close_failure = join(directory, "close-failure.scop");
    stream = ios_private_save_writer::open_for_rewrite(close_failure.c_str());
    require(stream != nullptr, "close failure open failed");
    write_stream(stream, "close-failure");
    const ios_private_save_writer::FinalizeOperations close_failure_operations = {std::fflush, fail_close};
    ios_private_save_writer::set_finalize_operations_for_testing(&close_failure_operations);
    errno = 0;
    require(!ios_private_save_writer::finalize(stream), "close failure was accepted");
    require(stream == nullptr && errno == EIO, "close failure did not close and report EIO");
    ios_private_save_writer::reset_finalize_operations_for_testing();

    require(unlink(hard_link.c_str()) == 0, "hardlink unlink failed");
    require(unlink(hard_source.c_str()) == 0, "hard source unlink failed");
    require(unlink(symlink_path.c_str()) == 0, "symlink unlink failed");
    require(unlink(target.c_str()) == 0, "target unlink failed");
    require(unlink(existing.c_str()) == 0, "existing unlink failed");
    require(unlink(created.c_str()) == 0, "created unlink failed");
    require(unlink(flush_failure.c_str()) == 0, "flush failure unlink failed");
    require(unlink(close_failure.c_str()) == 0, "close failure unlink failed");
    require(rmdir(directory) == 0, "directory unlink failed");
    std::puts("PASS tracked=checked finalize-failures=checked");
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "private_save_writer_harness.cpp"
            binary = work / "private_save_writer_harness"
            source.write_text(harness, encoding="utf-8")
            compiler = subprocess.run(
                ["xcrun", "--sdk", "macosx", "--find", "clang++"], text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(0, compiler.returncode, compiler.stderr)
            sdk = subprocess.run(
                ["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(0, sdk.returncode, sdk.stderr)
            build = subprocess.run(
                [compiler.stdout.strip(), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                 "-isysroot", sdk.stdout.strip(), "-DXR_PLATFORM_APPLE_IOS",
                 "-DXR_IOS_PRIVATE_SAVE_WRITER_TESTING",
                 "-I", str(ROOT / "src/xrCore"),
                 str(ROOT / "src/xrCore/ios_private_save_writer.cpp"), str(source),
                 "-o", str(binary)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, build.returncode, build.stdout + build.stderr)
            result = subprocess.run([str(binary)], text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, check=False)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual("PASS tracked=checked finalize-failures=checked\n", result.stdout)


if __name__ == "__main__":
    unittest.main()
