#!/usr/bin/env python3
"""Host-mocked order and failure contracts for the physical XCUITest runner."""

from __future__ import annotations

import os as _test_feedback_os
if _test_feedback_os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import sys as _test_feedback_sys
        _test_feedback_sys.path.insert(
            0, _test_feedback_os.path.dirname(_test_feedback_os.path.dirname(__file__)))
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment(
            "python::misc/ios/ui_automation/test_runner_contract.py",
            _test_feedback_sys.argv,
        )
    except BaseException:
        pass

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = Path(__file__).with_name("run.sh")


MOCK = r"""#!/usr/bin/env bash
set -euo pipefail

append_call() {
    printf '%s\n' "$1" >> "$MOCK_CALLS"
}

mock_case_has() {
    [[ ",${MOCK_CASE:-normal}," == *",$1,"* ]]
}

baseline_log() {
    printf '%s\n' '* iOS lifecycle v1 pid=4242 seq=1 event=activate'
    [ "${MOCK_CASE:-normal}" = missing-sector ] || printf '%s\n' \
        '* iOS sector startup v1 pid=4242 epoch=1 frame=35 level=zaton trigger=level_load status=resolved method=exact sector=115 camera=(1.000,2.000,3.000) probe=(1.000,2.000,3.000) radius=0.0'
}

post_test_log() {
    baseline_log
    sequence=1
    for _ in 1 2 3 4 5; do
        printf '%s\n' '* iOS: app deactivate'
        sequence=$((sequence + 1))
        printf '* iOS lifecycle v1 pid=4242 seq=%s event=deactivate\n' "$sequence"
        printf '%s\n' '* iOS: app activate'
        printf '%s\n' '* iOS: foreground drawable 1864x860 (engine 1864x860)'
        sequence=$((sequence + 1))
        printf '* iOS lifecycle v1 pid=4242 seq=%s event=activate\n' "$sequence"
    done
    printf '%s\n' \
        'iOS audio: interruption began, suspending sound, wasSuspended=0' \
        'iOS audio: interruption ended (shouldResume), sound resumed'
}

case "$(basename "$0")" in
security)
    printf '  1) MOCK Apple Development: OpenXRay\n'
    ;;
cmake|prepare-scheme|codesign)
    ;;
sleep)
    [ "${1:-}" = 1 ] || exit 86
    append_call sleep-1
    ;;
otool)
    printf '\t/System/Library/Frameworks/AVFAudio.framework/AVFAudio\n'
    ;;
xcodebuild)
    case " $* " in
    *" clean build-for-testing "*) append_call xcodebuild-build-for-testing ;;
    *" test-without-building "*)
        append_call xcodebuild-test-without-building
        if mock_case_has xctest-status-27; then
            exit 27
        fi
        ;;
    *) append_call "xcodebuild-unexpected:$*"; exit 97 ;;
    esac
    ;;
lock)
    case "${1:-}" in
    status) printf '%s\n' '{"state":"free"}' ;;
    acquire)
        [ "${2:-}" = openxray ] && [ "${3:-}" = 10 ] || exit 98
        append_call lease-acquire
        printf '%s\n' '{"token":"mock-lease-token"}'
        ;;
    renew-token)
        [ "${2:-}" = openxray ] && [ "${3:-}" = mock-lease-token ] \
            && [ "${4:-}" = 20 ] || exit 97
        append_call lease-renew
        if mock_case_has renew-failure; then
            exit 5
        fi
        ;;
    release-token)
        [ "${2:-}" = openxray ] && [ "${3:-}" = mock-lease-token ] || exit 99
        append_call lease-release
        printf '%s\n' "$3" > "$MOCK_RELEASE_TOKEN"
        if mock_case_has release-failure; then
            exit 4
        fi
        ;;
    *) exit 96 ;;
    esac
    ;;
xcrun)
    if [ "${1:-}" != devicectl ] || [ "${2:-}" != device ]; then
        exit 95
    fi
    case "${3:-}" in
    copy)
        source_path=''
        destination_path=''
        shift 4
        while [ "$#" -gt 0 ]; do
            case "$1" in
            --source) source_path="$2"; shift 2 ;;
            --destination) destination_path="$2"; shift 2 ;;
            *) shift ;;
            esac
        done
        case "$source_path" in
        Documents/_appdata_/user.ltx)
            append_call config-copy
            printf 'ios_diagnostics 0\nios_autoinput 0\n' > "$destination_path"
            if mock_case_has legacy-indented-autoload; then
                printf '\t  %s\n' 'start server(mobile-user/single/alife/load) client(localhost)' \
                    >> "$destination_path"
            elif mock_case_has legacy-autoload; then
                printf '%s\n' 'start server(mobile-user/single/alife/load) client(localhost)' \
                    >> "$destination_path"
            fi
            ;;
        Documents/xr_boot.log)
            count=0
            [ ! -e "$MOCK_BOOT_COPIES" ] || count=$(<"$MOCK_BOOT_COPIES")
            count=$((count + 1))
            printf '%s\n' "$count" > "$MOCK_BOOT_COPIES"
            if [ "$MOCK_MODE" = --test-options ] || [ "$MOCK_MODE" = --test ]; then
                printf '%s\n' 'options test relaunched OpenXRay and collected its final log' \
                    > "$destination_path"
            elif [ "${MOCK_CASE:-normal}" = wrong-readiness ]; then
                printf '%s\n' \
                    '* iOS lifecycle v1 pid=99 seq=1 event=activate' \
                    '* iOS sector startup v1 pid=99 epoch=1 frame=35 level=zaton trigger=level_load status=resolved method=exact sector=115 camera=(1.000,2.000,3.000) probe=(1.000,2.000,3.000) radius=0.0' \
                    > "$destination_path"
            elif [ "$count" -ge 3 ] && [ "${MOCK_CASE:-normal}" = rewritten-prefix ]; then
                printf '%s\n' 'rewritten after UI test' > "$destination_path"
            elif [ "$count" -ge 3 ]; then
                post_test_log > "$destination_path"
            else
                baseline_log > "$destination_path"
            fi
            ;;
        *) exit 94 ;;
        esac
        ;;
    process)
        case "${4:-}" in
        launch)
            printf '%s\n' "$@" > "$MOCK_LAUNCH_ARGUMENTS"
            device=''
            json=''
            terminate_existing=0
            shift 4
            while [ "$#" -gt 0 ]; do
                case "$1" in
                --device) device="$2"; shift 2 ;;
                --json-output) json="$2"; shift 2 ;;
                --terminate-existing) terminate_existing=1; shift ;;
                --)
                    shift
                    break
                    ;;
                *) exit 92 ;;
                esac
            done
            [ "$device" = "00008130-000564403E12001C" ] \
                && [ -n "$json" ] \
                && [ "$terminate_existing" = 1 ] \
                && [ "$#" = 3 ] \
                && [ "$1" = 'io.github.tryk016.openxray.RMJWWPF379' ] \
                && [ "$2" = '-start' ] \
                && [ "$3" = 'server(mobile user - beginning of the game/single/alife/load) client(localhost)' ] \
                || exit 92
            append_call devicectl-launch
            if [[ "${MOCK_CASE:-normal}" == launch-json-invalid* ]]; then
                printf '%s\n' '{not valid JSON' > "$json"
            else
                printf '%s\n' '{"info":{"outcome":"success"},"result":{"process":{"processIdentifier":4242}}}' > "$json"
            fi
            ;;
        terminate)
            pid=''
            force_kill=0
            shift 5
            while [ "$#" -gt 0 ]; do
                case "$1" in
                --pid) pid="$2"; shift 2 ;;
                --kill) force_kill=1; shift ;;
                *) shift ;;
                esac
            done
            [ "$pid" = 4242 ] || exit 91
            if [ "$force_kill" = 1 ]; then
                append_call devicectl-kill-pid-4242
                if mock_case_has kill-failure-eventual-gone; then
                    exit 4
                fi
            else
                append_call devicectl-terminate-pid-4242
                if ! mock_case_has cleanup-delayed-gone \
                        && ! mock_case_has kill-failure-eventual-gone \
                        && ! mock_case_has cleanup-exhaustion; then
                    printf '%s\n' 1 > "$MOCK_TERMINATED"
                fi
                if mock_case_has terminate-already-stopped; then
                    exit 4
                fi
            fi
            ;;
        *) exit 93 ;;
        esac
        ;;
    info)
        [ "${4:-}" = processes ] || exit 90
        json=''
        shift 5
        while [ "$#" -gt 0 ]; do
            case "$1" in
            --json-output) json="$2"; shift 2 ;;
            *) shift ;;
            esac
        done
        [ -n "$json" ] || exit 89
        append_call devicectl-info-processes
        count=0
        [ ! -e "$MOCK_PROCESS_QUERIES" ] || count=$(<"$MOCK_PROCESS_QUERIES")
        count=$((count + 1))
        printf '%s\n' "$count" > "$MOCK_PROCESS_QUERIES"
        case ",${MOCK_CASE:-normal}," in
        *,cleanup-query-failure,*)
            [ "$count" -lt 2 ] || exit 6
            printf '%s\n' '{"info":{"outcome":"success"},"result":{"runningProcesses":[{"processIdentifier":4242,"executable":"file:///private/var/containers/Bundle/Application/mock/xr_3da.app/xr_3da"}]}}' > "$json"
            ;;
        *,cleanup-pid-gone,*)
            if [ "$count" -ge 2 ]; then
                printf '%s\n' '{"info":{"outcome":"success"},"result":{"runningProcesses":[]}}' > "$json"
            else
                printf '%s\n' '{"info":{"outcome":"success"},"result":{"runningProcesses":[{"processIdentifier":4242,"executable":"file:///private/var/containers/Bundle/Application/mock/xr_3da.app/xr_3da"}]}}' > "$json"
            fi
            ;;
        *,cleanup-pid-reused,*)
            if [ "$count" -ge 2 ]; then
                printf '%s\n' '{"info":{"outcome":"success"},"result":{"runningProcesses":[{"processIdentifier":4242,"executable":"file:///usr/libexec/not-openxray"}]}}' > "$json"
            else
                printf '%s\n' '{"info":{"outcome":"success"},"result":{"runningProcesses":[{"processIdentifier":4242,"executable":"file:///private/var/containers/Bundle/Application/mock/xr_3da.app/xr_3da"}]}}' > "$json"
            fi
            ;;
        *,cleanup-delayed-gone,*)
            if [ "$count" -ge 4 ]; then
                printf '%s\n' '{"info":{"outcome":"success"},"result":{"runningProcesses":[]}}' > "$json"
            else
                printf '%s\n' '{"info":{"outcome":"success"},"result":{"runningProcesses":[{"processIdentifier":4242,"executable":"file:///private/var/containers/Bundle/Application/mock/xr_3da.app/xr_3da"}]}}' > "$json"
            fi
            ;;
        *,kill-failure-eventual-gone,*)
            if [ "$count" -ge 4 ]; then
                printf '%s\n' '{"info":{"outcome":"success"},"result":{"runningProcesses":[]}}' > "$json"
            else
                printf '%s\n' '{"info":{"outcome":"success"},"result":{"runningProcesses":[{"processIdentifier":4242,"executable":"file:///private/var/containers/Bundle/Application/mock/xr_3da.app/xr_3da"}]}}' > "$json"
            fi
            ;;
        *,cleanup-exhaustion,*)
            printf '%s\n' '{"info":{"outcome":"success"},"result":{"runningProcesses":[{"processIdentifier":4242,"executable":"file:///private/var/containers/Bundle/Application/mock/xr_3da.app/xr_3da"}]}}' > "$json"
            ;;
        *,wrong-running-pid,*)
            printf '%s\n' '{"info":{"outcome":"success"},"result":{"runningProcesses":[{"processIdentifier":99,"executable":"file:///private/var/containers/Bundle/Application/mock/xr_3da.app/xr_3da"}]}}' > "$json"
            ;;
        *,launch-json-invalid-duplicate,*)
            printf '%s\n' '{"info":{"outcome":"success"},"result":{"runningProcesses":[{"processIdentifier":4242,"executable":"file:///private/var/containers/Bundle/Application/one/xr_3da.app/xr_3da"},{"processIdentifier":4343,"executable":"file:///private/var/containers/Bundle/Application/two/xr_3da.app/xr_3da"}]}}' > "$json"
            ;;
        *)
            if [ -e "$MOCK_TERMINATED" ]; then
                printf '%s\n' '{"info":{"outcome":"success"},"result":{"runningProcesses":[]}}' > "$json"
            else
                printf '%s\n' '{"info":{"outcome":"success"},"result":{"runningProcesses":[{"processIdentifier":4242,"executable":"file:///private/var/containers/Bundle/Application/mock/xr_3da.app/xr_3da"}]}}' > "$json"
            fi
            ;;
        esac
        ;;
    *) exit 88 ;;
    esac
    ;;
*)
    exit 87
    ;;
esac
"""


class RunnerContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.run_index = 0
        for name in (
            "security", "cmake", "prepare-scheme", "codesign", "otool",
            "xcodebuild", "lock", "xcrun", "sleep",
        ):
            path = self.bin / name
            path.write_text(MOCK, encoding="utf-8")
            path.chmod(0o700)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_runner(
        self, case: str = "normal", mode: str = "--test-reliability",
    ) -> subprocess.CompletedProcess[str]:
        self.run_index += 1
        run_name = f"run-{self.run_index}"
        trace = self.root / f"{run_name}.trace"
        calls = self.root / f"{run_name}.calls"
        release_token = self.root / f"{run_name}.released.token"
        environment = os.environ.copy()
        environment.update({
            "MOCK_CASE": case,
            "MOCK_MODE": mode,
            "MOCK_CALLS": str(calls),
            "MOCK_RELEASE_TOKEN": str(release_token),
            "MOCK_BOOT_COPIES": str(self.root / f"{run_name}.boot-copies"),
            "MOCK_LAUNCH_ARGUMENTS": str(self.root / f"{run_name}.launch-arguments"),
            "MOCK_PROCESS_QUERIES": str(self.root / f"{run_name}.process-queries"),
            "MOCK_TERMINATED": str(self.root / f"{run_name}.terminated"),
            "OPENXRAY_UI_AUTOMATION_BUILD_DIR": str(self.root / "build"),
            "OPENXRAY_UI_AUTOMATION_RESULT": str(self.root / f"{run_name}.xcresult"),
            "OPENXRAY_UI_AUTOMATION_LOG": str(self.root / f"{run_name}-xr_boot.log"),
            "OPENXRAY_UI_AUTOMATION_TRACE": str(trace),
            "OPENXRAY_DEVICE_LOCK": str(self.bin / "lock"),
            "OPENXRAY_UI_AUTOMATION_SECURITY": str(self.bin / "security"),
            "OPENXRAY_UI_AUTOMATION_CMAKE": str(self.bin / "cmake"),
            "OPENXRAY_UI_AUTOMATION_PREPARE_SCHEME": str(self.bin / "prepare-scheme"),
            "OPENXRAY_UI_AUTOMATION_CODESIGN": str(self.bin / "codesign"),
            "OPENXRAY_UI_AUTOMATION_OTOOL": str(self.bin / "otool"),
            "OPENXRAY_UI_AUTOMATION_XCODEBUILD": str(self.bin / "xcodebuild"),
            "OPENXRAY_UI_AUTOMATION_XCRUN": str(self.bin / "xcrun"),
            "OPENXRAY_UI_AUTOMATION_SLEEP": str(self.bin / "sleep"),
            "OPENXRAY_UI_AUTOMATION_READY_TIMEOUT_SECONDS": "1",
            "OPENXRAY_UI_AUTOMATION_READY_POLL_SECONDS": "1",
        })
        result = subprocess.run(
            [str(RUNNER), mode], cwd=REPO_ROOT, env=environment,
            text=True, capture_output=True, timeout=15, check=False,
        )
        self.trace = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
        self.calls = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
        launch_arguments = self.root / f"{run_name}.launch-arguments"
        self.launch_arguments = (
            launch_arguments.read_text(encoding="utf-8").splitlines()
            if launch_arguments.exists() else []
        )
        self.release_token = release_token.read_text(encoding="utf-8") if release_token.exists() else ""
        return result

    def assert_release_is_last(self) -> None:
        self.assertEqual(self.calls[-1], "lease-release")
        self.assertEqual(self.release_token, "mock-lease-token\n")

    def assert_no_success_marker(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotIn("PASS — UI automation", result.stdout)

    def assert_single_prelease_build(self) -> None:
        self.assertEqual(self.calls.count("xcodebuild-build-for-testing"), 1)
        self.assertLess(
            self.calls.index("xcodebuild-build-for-testing"),
            self.calls.index("lease-acquire"),
        )

    def assert_bound_cleanup_proves_gone(self) -> None:
        self.assertEqual(self.trace[-5:], [
            "cleanup-query-initial",
            "terminate-pid=4242",
            "cleanup-query-poll-1",
            "cleanup-confirmed-gone",
            "lease-release",
        ])
        self.assertEqual(self.calls[-5:], [
            "devicectl-info-processes",
            "devicectl-terminate-pid-4242",
            "sleep-1",
            "devicectl-info-processes",
            "lease-release",
        ])
        self.assertNotIn("devicectl-kill-pid-4242", self.calls)

    def test_autonomous_order_includes_gameplay_ready_baseline_terminate_and_release(self) -> None:
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.trace, [
            "build-for-testing-complete",
            "lease-acquired",
            "config-validated",
            "launch-pid=4242",
            "ready-pid=4242",
            "running-process-pid=4242",
            "pre-log-baseline",
            "lease-renewed",
            "test-without-building",
            "post-log-collected",
            "cleanup-query-initial",
            "terminate-pid=4242",
            "cleanup-query-poll-1",
            "cleanup-confirmed-gone",
            "lease-release",
        ])
        self.assertEqual(self.calls, [
            "xcodebuild-build-for-testing",
            "lease-acquire",
            "config-copy",
            "devicectl-launch",
            "devicectl-info-processes",
            "lease-renew",
            "xcodebuild-test-without-building",
            "devicectl-info-processes",
            "devicectl-terminate-pid-4242",
            "sleep-1",
            "devicectl-info-processes",
            "lease-release",
        ])
        self.assert_single_prelease_build()
        self.assert_release_is_last()
        self.assertEqual(self.launch_arguments[:8], [
            "devicectl", "device", "process", "launch", "--device",
            "00008130-000564403E12001C", "--terminate-existing", "--json-output",
        ])
        self.assertTrue(self.launch_arguments[8])
        self.assertEqual(self.launch_arguments[9:], [
            "--", "io.github.tryk016.openxray.RMJWWPF379", "-start",
            "server(mobile user - beginning of the game/single/alife/load) client(localhost)",
        ])
        self.assertIn("PASS — UI automation and scoped device-log oracle passed", result.stdout)

    def test_options_builds_validates_tests_and_collects_without_autonomous_contract(self) -> None:
        runner_source = RUNNER.read_text(encoding="utf-8")
        self.assertIn("trap 'cleanup_and_exit \"$?\"' EXIT", runner_source)
        wrapper = runner_source.split("cleanup_and_exit()", 1)[1].split(
            '[ "$#" -le 1 ]', 1,
        )[0]
        self.assertLess(wrapper.index("trap - EXIT"), wrapper.index("set +e"))
        self.assertLess(wrapper.index("set +e"), wrapper.index('cleanup "$original_status"'))
        self.assertLess(wrapper.index('final_status=$?'), wrapper.index('exit "$final_status"'))
        self.assertLess(wrapper.index('cleanup "$original_status"'), wrapper.index('echo "$SUCCESS_MESSAGE"'))
        self.assertNotIn('echo "PASS — UI automation', runner_source)

        result = self.run_runner(mode="--test-options")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.trace, [
            "build-for-testing-complete",
            "lease-acquired",
            "config-validated",
            "lease-renewed",
            "test-without-building",
            "post-log-collected",
            "lease-release",
        ])
        self.assertEqual(self.calls, [
            "xcodebuild-build-for-testing",
            "lease-acquire",
            "config-copy",
            "lease-renew",
            "xcodebuild-test-without-building",
            "lease-release",
        ])
        self.assert_single_prelease_build()
        self.assert_release_is_last()
        # Options does not launch a runner-owned game session, therefore there
        # is no possible autonomous `-start` argument vector to pass through.
        self.assertEqual(self.launch_arguments, [])
        self.assertIn("PASS — UI automation and post-test device log collection passed", result.stdout)

        with self.subTest("release failure turns an otherwise-successful run into failure"):
            result = self.run_runner("release-failure", mode="--test-options")
            self.assertEqual(result.returncode, 1)
            self.assertIn("could not release the exact OpenXRay lease token", result.stderr)
            self.assert_no_success_marker(result)
            self.assertEqual(self.calls[-1], "lease-release")
            self.assertTrue(any(event.startswith("cleanup-failed=could not release") for event in self.trace))

    def test_lifecycle_readiness_does_not_require_sector_marker(self) -> None:
        result = self.run_runner(case="missing-sector", mode="--test-lifecycle")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pre-log-baseline", self.trace)
        self.assertLess(self.trace.index("pre-log-baseline"), self.trace.index("lease-renewed"))
        self.assertLess(self.trace.index("lease-renewed"), self.trace.index("test-without-building"))
        self.assert_bound_cleanup_proves_gone()
        self.assert_release_is_last()

    def test_renew_failure_is_closed_after_baseline_and_before_xctest(self) -> None:
        result = self.run_runner("renew-failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not renew the exact OpenXRay iPhone lease", result.stderr)
        self.assertEqual(self.trace[:7], [
            "build-for-testing-complete",
            "lease-acquired",
            "config-validated",
            "launch-pid=4242",
            "ready-pid=4242",
            "running-process-pid=4242",
            "pre-log-baseline",
        ])
        self.assertNotIn("lease-renewed", self.trace)
        self.assertNotIn("test-without-building", self.trace)
        self.assertNotIn("xcodebuild-test-without-building", self.calls)
        self.assertEqual(self.calls[-6:], [
            "lease-renew", "devicectl-info-processes",
            "devicectl-terminate-pid-4242", "sleep-1",
            "devicectl-info-processes", "lease-release",
        ])
        self.assert_release_is_last()

    def test_options_renew_failure_never_starts_xctest(self) -> None:
        result = self.run_runner("renew-failure", mode="--test-options")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not renew the exact OpenXRay iPhone lease", result.stderr)
        self.assertEqual(self.calls[-2:], ["lease-renew", "lease-release"])
        self.assertNotIn("xcodebuild-test-without-building", self.calls)
        self.assertNotIn("devicectl-info-processes", self.calls)
        self.assert_release_is_last()

    def test_reliability_missing_sector_fails_before_baseline_or_test(self) -> None:
        result = self.run_runner("missing-sector")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required newline-complete readiness", result.stderr)
        self.assertNotIn("pre-log-baseline", self.trace)
        self.assertNotIn("test-without-building", self.trace)
        self.assertIn("terminate-pid=4242", self.trace)
        self.assertNotIn("xcodebuild-test-without-building", self.calls)
        self.assert_bound_cleanup_proves_gone()
        self.assert_release_is_last()

    def test_wrong_readiness_pid_fails_then_terminates_before_release(self) -> None:
        result = self.run_runner("wrong-readiness")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required newline-complete readiness", result.stderr)
        self.assertNotIn("pre-log-baseline", self.trace)
        self.assertNotIn("test-without-building", self.trace)
        self.assert_bound_cleanup_proves_gone()
        self.assert_release_is_last()

    def test_wrong_running_process_pid_fails_and_cleanup_skips_unbound_pid(self) -> None:
        result = self.run_runner("wrong-running-pid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runningProcesses did not prove", result.stderr)
        self.assertNotIn("pre-log-baseline", self.trace)
        self.assertNotIn("test-without-building", self.trace)
        self.assertEqual(self.trace[-3:], [
            "cleanup-query-initial", "cleanup-confirmed-gone", "lease-release",
        ])
        self.assertNotIn("devicectl-terminate-pid-4242", self.calls)
        self.assertNotIn("devicectl-kill-pid-4242", self.calls)
        self.assert_release_is_last()

    def test_missing_autoload_fails_config_before_launch_and_still_releases_last(self) -> None:
        # Stable historical ID: retain it while its subtests cover the inverted
        # config contract introduced by command-line autonomous startup.
        with self.subTest("missing config autoload is accepted and start arg is present"):
            result = self.run_runner("missing-autoload")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("devicectl-launch", self.calls)
            self.assertEqual(self.launch_arguments[-4:], [
                "--", "io.github.tryk016.openxray.RMJWWPF379", "-start",
                "server(mobile user - beginning of the game/single/alife/load) client(localhost)",
            ])
            self.assert_bound_cleanup_proves_gone()
            self.assert_release_is_last()

        with self.subTest("legacy config autoload is rejected before launch and releases token"):
            result = self.run_runner("legacy-autoload")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("zero active start server", result.stderr)
            self.assertNotIn("devicectl-launch", self.calls)
            self.assertEqual(self.launch_arguments, [])
            self.assertNotIn("devicectl-terminate-pid-4242", self.calls)
            self.assert_release_is_last()

        with self.subTest("legacy indented autoload is rejected before launch and releases token"):
            result = self.run_runner("legacy-indented-autoload")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("zero active start server", result.stderr)
            self.assertNotIn("devicectl-launch", self.calls)
            self.assertEqual(self.launch_arguments, [])
            self.assertNotIn("devicectl-terminate-pid-4242", self.calls)
            self.assert_release_is_last()

    def test_rewritten_post_log_prefix_fails_after_test_without_rebuild(self) -> None:
        result = self.run_runner("rewritten-prefix")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not preserve the exact PRE_LOG prefix", result.stderr)
        baseline_index = self.trace.index("pre-log-baseline")
        self.assertNotIn("build-for-testing-complete", self.trace[baseline_index + 1:])
        self.assertEqual(self.calls.count("xcodebuild-build-for-testing"), 1)
        self.assert_bound_cleanup_proves_gone()
        self.assert_release_is_last()

    def test_launch_json_parse_failure_recovers_one_exact_pid_for_cleanup(self) -> None:
        result = self.run_runner("launch-json-invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not return a valid OpenXRay PID", result.stderr)
        self.assertIn("refusing name-based termination", result.stderr)
        self.assertNotIn("devicectl-info-processes", self.calls)
        self.assertNotIn("devicectl-terminate-pid-4242", self.calls)
        self.assertNotIn("devicectl-kill-pid-4242", self.calls)
        self.assert_release_is_last()

    def test_launch_json_cleanup_rejects_ambiguous_executable_matches(self) -> None:
        result = self.run_runner("launch-json-invalid-duplicate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing name-based termination", self.trace[-2])
        self.assertNotIn("devicectl-terminate-pid-4242", self.calls)
        self.assertNotIn("devicectl-kill-pid-4242", self.calls)
        self.assert_release_is_last()

    def test_already_stopped_termination_is_accepted_before_release(self) -> None:
        result = self.run_runner("terminate-already-stopped")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("terminate-command-failed-pid=4242", self.trace)
        self.assertEqual(self.trace[-6:], [
            "cleanup-query-initial",
            "terminate-pid=4242",
            "terminate-command-failed-pid=4242",
            "cleanup-query-poll-1",
            "cleanup-confirmed-gone",
            "lease-release",
        ])
        self.assert_release_is_last()

        with self.subTest("delayed gone is accepted only after a second exact-PID query"):
            result = self.run_runner("cleanup-delayed-gone")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.trace[-7:], [
                "cleanup-query-initial",
                "terminate-pid=4242",
                "cleanup-query-poll-1",
                "kill-pid=4242",
                "cleanup-query-poll-2",
                "cleanup-confirmed-gone",
                "lease-release",
            ])
            self.assertEqual(self.calls[-8:], [
                "devicectl-info-processes",
                "devicectl-terminate-pid-4242",
                "sleep-1",
                "devicectl-info-processes",
                "devicectl-kill-pid-4242",
                "sleep-1",
                "devicectl-info-processes",
                "lease-release",
            ])
            self.assertEqual(self.calls.count("sleep-1"), 2)
            self.assert_release_is_last()

        with self.subTest("kill command failure is decided by later exact-PID state"):
            result = self.run_runner("kill-failure-eventual-gone")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.trace[-8:], [
                "cleanup-query-initial",
                "terminate-pid=4242",
                "cleanup-query-poll-1",
                "kill-pid=4242",
                "kill-command-failed-pid=4242",
                "cleanup-query-poll-2",
                "cleanup-confirmed-gone",
                "lease-release",
            ])
            self.assertEqual(self.calls.count("devicectl-kill-pid-4242"), 1)
            self.assert_release_is_last()

        with self.subTest("exhaustion fails cleanup but does not omit exact-token release"):
            result = self.run_runner("cleanup-exhaustion")
            self.assertEqual(result.returncode, 1)
            self.assertIn("remained exact-bound after five cleanup polls", result.stderr)
            self.assert_no_success_marker(result)
            self.assertEqual(self.calls.count("sleep-1"), 5)
            self.assertEqual(self.calls.count("devicectl-kill-pid-4242"), 1)
            self.assertLess(
                self.calls.index("devicectl-info-processes", 8),
                self.calls.index("devicectl-kill-pid-4242"),
            )
            self.assertEqual(self.trace[-2:], [
                "cleanup-failed=PID 4242 remained exact-bound after five cleanup polls",
                "lease-release",
            ])
            self.assert_release_is_last()

        with self.subTest("existing nonzero result survives cleanup failure"):
            result = self.run_runner("xctest-status-27,cleanup-exhaustion")
            self.assertEqual(result.returncode, 27)
            self.assertIn("XCUITest failed with status 27", result.stderr)
            self.assertIn("remained exact-bound after five cleanup polls", result.stderr)
            self.assertEqual(self.calls.count("sleep-1"), 5)
            self.assertEqual(self.calls.count("devicectl-kill-pid-4242"), 1)
            self.assert_release_is_last()
        self.assert_release_is_last()

    def test_cleanup_skips_termination_when_remembered_pid_is_already_gone(self) -> None:
        result = self.run_runner("cleanup-pid-gone")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.trace[-3:], [
            "cleanup-query-initial", "cleanup-confirmed-gone", "lease-release",
        ])
        self.assertNotIn("devicectl-terminate-pid-4242", self.calls)
        self.assertNotIn("devicectl-kill-pid-4242", self.calls)
        self.assertEqual(self.calls[-2:], ["devicectl-info-processes", "lease-release"])
        self.assert_release_is_last()

    def test_cleanup_skips_termination_when_remembered_pid_was_reused(self) -> None:
        result = self.run_runner("cleanup-pid-reused")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.trace[-3:], [
            "cleanup-query-initial", "cleanup-confirmed-reused", "lease-release",
        ])
        self.assertNotIn("devicectl-terminate-pid-4242", self.calls)
        self.assertNotIn("devicectl-kill-pid-4242", self.calls)
        self.assertEqual(self.calls[-2:], ["devicectl-info-processes", "lease-release"])
        self.assert_release_is_last()

    def test_cleanup_skips_termination_when_fresh_process_query_fails(self) -> None:
        result = self.run_runner("cleanup-query-failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fresh process query/classification failed", result.stderr)
        self.assertTrue(any(
            event.startswith("cleanup-failed=fresh process query/classification failed")
            for event in self.trace
        ))
        self.assertNotIn("devicectl-terminate-pid-4242", self.calls)
        self.assertNotIn("devicectl-kill-pid-4242", self.calls)
        self.assertEqual(self.calls[-2:], ["devicectl-info-processes", "lease-release"])
        self.assert_release_is_last()


if __name__ == "__main__":
    unittest.main()
