#!/usr/bin/env python3
"""Mutation-backed source contract for iOS startup-sector evidence wiring."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
PATHS = {
    "policy": REPO_ROOT / "src/Layers/xrRender_R2/ios_sector_fallback_policy.h",
    "render_header": REPO_ROOT / "src/Layers/xrRender_R2/r2.h",
    "loader": REPO_ROOT / "src/Layers/xrRender_R2/r2_loader.cpp",
    "calculate": REPO_ROOT / "src/Layers/xrRender_R2/r2_R_calculate.cpp",
    "device_header": REPO_ROOT / "src/xrEngine/device.h",
    "camera_manager": REPO_ROOT / "src/xrEngine/CameraManager.cpp",
    "interface": REPO_ROOT / "src/xrEngine/Render.h",
    "game": REPO_ROOT / "src/xrGame/GamePersistent.cpp",
}
CAMERA_MOVED = "const bool cameraMoved = !Device.vCameraPositionSaved.similar(Device.vCameraPosition, EPS_L);"
SHOULD_DETECT_CONDITION = "if (shouldDetectSector)"
MARKER_GRAMMAR = (
    "* iOS sector startup v1 pid=%d epoch=%llu frame=%u level=%s trigger=%s "
    "status=%s method=%s "
)


def function_body(source: str, signature_pattern: str) -> str:
    signature = re.search(signature_pattern, source)
    if signature is None:
        raise AssertionError(f"function not found: {signature_pattern}")
    opening = source.find("{", signature.end())
    if opening < 0:
        raise AssertionError(f"function body not found: {signature_pattern}")
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
    raise AssertionError(f"unbalanced function body: {signature_pattern}")


def balanced_if_body(source: str, condition: str) -> str:
    start = source.find(condition)
    if start < 0:
        raise AssertionError(f"condition not found: {condition}")
    opening = source.find("{", start + len(condition))
    if opening < 0:
        raise AssertionError(f"condition body not found: {condition}")
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
    raise AssertionError(f"unbalanced condition body: {condition}")


def ios_regions(source: str) -> list[str]:
    regions: list[str] = []
    lines = source.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        if not re.match(r"\s*#\s*if\s+defined\(XR_PLATFORM_APPLE_IOS\)", lines[index]):
            index += 1
            continue
        start = index
        depth = 0
        while index < len(lines):
            line = lines[index]
            if re.match(r"\s*#\s*(if|ifdef|ifndef)\b", line):
                depth += 1
            elif re.match(r"\s*#\s*endif\b", line):
                depth -= 1
                if depth == 0:
                    regions.append("".join(lines[start:index + 1]))
                    break
            index += 1
        else:
            raise AssertionError("XR_PLATFORM_APPLE_IOS guard is not balanced")
        index += 1
    return regions


def contract_errors(sources: dict[str, str]) -> list[str]:
    errors: list[str] = []
    try:
        calculate = function_body(sources["calculate"], r"void\s+CRender::Calculate\s*\(\s*\)")
        camera_branch = balanced_if_body(calculate, SHOULD_DETECT_CONDITION)
        load = function_body(sources["loader"], r"void\s+CRender::level_Load\s*\(")
        unload = function_body(sources["loader"], r"void\s+CRender::level_Unload\s*\(")
        quick_begin = function_body(
            sources["loader"], r"void\s+CRender::ios_begin_quick_load_sector_startup_epoch\s*\("
        )
        event = function_body(sources["game"], r"void\s+CGamePersistent::OnEvent\s*\(")
        build_payload = function_body(
            sources["calculate"], r"bool\s+BuildIosSectorStartupPayload\s*\("
        )
        retry_pending = function_body(
            sources["calculate"], r"void\s+RetryIosSectorStartupPending\s*\("
        )
        camera_apply = function_body(
            sources["camera_manager"], r"void\s+CCameraManager::ApplyDevice\s*\("
        )
        note_camera = function_body(
            sources["device_header"], r"void\s+ios_note_camera_applied\s*\("
        )
        calculate_ios = ios_regions(sources["calculate"])
        loader_ios = ios_regions(sources["loader"])
        device_ios = ios_regions(sources["device_header"])
        camera_ios = ios_regions(sources["camera_manager"])
        game_ios = ios_regions(sources["game"])
        interface_ios = ios_regions(sources["interface"])
        header_ios = ios_regions(sources["render_header"])
    except AssertionError as error:
        return [str(error)]

    if CAMERA_MOVED not in calculate:
        errors.append("camera movement must remain an explicit, unmodified input to the policy")
    if "const bool shouldDetectSector = ios_sector_fallback::ShouldDetectSector(cameraMoved," not in calculate:
        errors.append("Calculate must delegate the iOS sector trigger to the pure policy")
    if camera_branch.count("dsgraph_main.detect_sector(Device.vCameraPosition)") != 1:
        errors.append("camera branch must issue exactly one original exact-sector query")
    if camera_branch.count("dsgraph_main.detect_sector(probePosition)") != 1:
        errors.append("camera branch must retain exactly one fallback callback query")
    if "Device.vCameraPositionSaved =" in calculate or "Device.vCameraPositionSaved.set(" in calculate:
        errors.append("Calculate telemetry must not mutate saved camera position")
    if re.search(r"last_sector_id\s*=\s*IRender_Sector::INVALID_SECTOR_ID", calculate):
        errors.append("Calculate telemetry must not invalidate the retained sector")

    resolve = calculate.find("ios_sector_fallback::Resolve(")
    sector_commit = calculate.find("ios_sector_fallback::CommitValidSector(")
    prepare_detected = calculate.find("ios_sector_startup_evidence.PrepareDetected(committed)")
    store_detected = calculate.find("StoreIosSectorStartupPending(", prepare_detected)
    level_disarm = calculate.find("ios_level_load_camera_barrier.Disarm();")
    retry_detected = calculate.find("RetryIosSectorStartupPending(", store_detected)
    quick_barrier = calculate.find("ios_quick_load_camera_barrier.Passed(")
    prepare_none = calculate.find("ios_sector_startup_evidence.PrepareNoDetection(")
    store_none = calculate.find("StoreIosSectorStartupPending(", prepare_none)
    quick_disarm = calculate.find("ios_quick_load_camera_barrier.Disarm();", store_none)
    retry_none = calculate.find("RetryIosSectorStartupPending(", store_none)
    lights = calculate.find("Lights.Update();")
    if min(resolve, sector_commit, prepare_detected, store_detected, level_disarm, retry_detected) < 0 or not (
        resolve < sector_commit < prepare_detected < store_detected < level_disarm < retry_detected
    ):
        errors.append("detected outcome must Resolve, commit sector, Prepare, store, disarm LevelLoad, then retry")
    if min(quick_barrier, prepare_none, store_none, quick_disarm, retry_none, lights) < 0 or not (
        retry_detected < quick_barrier < prepare_none < store_none < quick_disarm < retry_none < lights
    ):
        errors.append("QuickLoad no-detection must pass its barrier, store, disarm, then retry")
    no_detection_else = calculate.find("else\n    {", calculate.find(SHOULD_DETECT_CONDITION))
    if no_detection_else < 0 or prepare_none < no_detection_else:
        errors.append("QuickLoad no-detection preparation must stay after the sector-detection branch")
    if "if (quickLoadEpoch && quickLoadCameraBarrierPassed && !ios_sector_startup_pending_report.active)" not in calculate:
        errors.append("QuickLoad no-detection must require its barrier and no pending report")

    policy_call_end = calculate.find(SHOULD_DETECT_CONDITION)
    policy_call = calculate[calculate.find("ShouldDetectSector(cameraMoved,"):policy_call_end]
    required_policy_call = (
        "ios_sector_startup_evidence.active()",
        "ios_sector_startup_evidence.trigger()",
        "ios_sector_startup_evidence.phase()",
        "last_sector_id != IRender_Sector::INVALID_SECTOR_ID",
        "ios_sector_startup_pending_report.active",
        "levelLoadCameraBarrierPassed",
    )
    if any(token not in policy_call for token in required_policy_call):
        errors.append("stationary LevelLoad policy call must provide active trigger phase sector pending and barrier state")
    if "ios_level_load_camera_barrier.Passed(Device.ios_camera_apply_generation())" not in calculate:
        errors.append("stationary LevelLoad detection must pass its dedicated camera barrier")

    first_retry = calculate.find("RetryIosSectorStartupPending(")
    context = calculate.find("auto& dsgraph_main = get_imm_context();")
    if first_retry < 0 or context < 0 or first_retry > context:
        errors.append("pending evidence must retry before accepting a new camera observation")
    payload_build = retry_pending.find("BuildIosSectorStartupPayload(")
    evidence_commit = retry_pending.find("evidence.CommitPrepared(")
    success_clear = retry_pending.rfind("pending = {};")
    message = retry_pending.find('Msg("%s", payload.text);')
    flush = retry_pending.find("FlushLog();")
    if min(payload_build, evidence_commit, success_clear, message, flush) < 0 or not (
        payload_build < evidence_commit < success_clear < message < flush
    ):
        errors.append("validated payload must precede CommitPrepared, Msg and FlushLog")
    elif "return" in retry_pending[success_clear:]:
        errors.append("successful CommitPrepared path must not return before Msg and FlushLog")

    required_payload_validation = (
        "report.transition.valid()",
        "std::isfinite(report.radius)",
        "IsFiniteIosSectorPosition(report.camera)",
        "getpid()",
        "process_id <= 0",
        "BuildIosSectorLevelToken",
        "std::snprintf",
    )
    if any(token not in build_payload for token in required_payload_validation):
        errors.append("payload must be fully validated and formatted before evidence commit")

    loaded = load.find("b_loaded = TRUE;")
    begin_level = load.find("BeginEpoch(ios_sector_fallback::StartupTrigger::LevelLoad)")
    if loaded < 0 or begin_level < 0 or begin_level < loaded:
        errors.append("level-load epoch must begin after b_loaded becomes true")
    deactivate = unload.find("ios_sector_startup_evidence.Deactivate();")
    first_return = unload.find("return;")
    if deactivate < 0 or (first_return >= 0 and deactivate > first_return):
        errors.append("level unload must deactivate evidence before an early return")
    if "BeginEpoch(ios_sector_fallback::StartupTrigger::QuickLoad)" not in quick_begin:
        errors.append("renderer quick-load hook must begin a quick_load epoch")
    quick_level_disarm = quick_begin.find("ios_level_load_camera_barrier.Disarm();")
    quick_disarm = quick_begin.find("ios_quick_load_camera_barrier.Disarm();")
    quick_clear = quick_begin.find("ios_sector_startup_pending_report = {};")
    quick_begin_epoch = quick_begin.find("BeginEpoch(ios_sector_fallback::StartupTrigger::QuickLoad)")
    quick_generation = quick_begin.find("Device.ios_camera_apply_generation()")
    quick_arm = quick_begin.find("ios_quick_load_camera_barrier.Arm(")
    if min(quick_level_disarm, quick_disarm, quick_clear, quick_begin_epoch, quick_generation, quick_arm) < 0 or not (
        quick_level_disarm < quick_disarm < quick_clear < quick_begin_epoch < quick_generation < quick_arm
    ):
        errors.append("QuickLoad must disarm stale barriers, clear pending, then arm its post-restart baseline")

    load_level_disarm = load.find("ios_level_load_camera_barrier.Disarm();")
    load_quick_disarm = load.find("ios_quick_load_camera_barrier.Disarm();")
    load_clear = load.find("ios_sector_startup_pending_report = {};")
    load_generation = load.find("Device.ios_camera_apply_generation()")
    load_arm = load.find("ios_level_load_camera_barrier.Arm(")
    if min(loaded, load_level_disarm, load_quick_disarm, load_clear, begin_level, load_generation, load_arm) < 0 or not (
        loaded < load_level_disarm < load_quick_disarm < load_clear < begin_level < load_generation < load_arm
    ):
        errors.append("LevelLoad must disarm both barriers, clear pending, then arm a post-epoch baseline")
    if "if (ios_sector_startup_evidence.BeginEpoch(ios_sector_fallback::StartupTrigger::LevelLoad))" not in load:
        errors.append("LevelLoad must arm its barrier only after BeginEpoch succeeds")
    unload_level_disarm = unload.find("ios_level_load_camera_barrier.Disarm();")
    unload_quick_disarm = unload.find("ios_quick_load_camera_barrier.Disarm();")
    unload_clear = unload.find("ios_sector_startup_pending_report = {};")
    if min(unload_level_disarm, unload_quick_disarm, unload_clear, deactivate) < 0 or not (
        unload_level_disarm < unload_quick_disarm < unload_clear < deactivate
    ):
        errors.append("level unload must disarm both barriers and clear pending before deactivation")

    restart = event.find("game->restart_simulator(saved_name);")
    hook = event.find("GEnv.Render->ios_begin_quick_load_sector_startup_epoch();")
    release = event.find("xr_free(saved_name);")
    if min(restart, hook, release) < 0 or not restart < hook < release:
        errors.append("QuickLoad hook must run after restart_simulator and before xr_free")
    if not any("ios_begin_quick_load_sector_startup_epoch" in region for region in game_ios):
        errors.append("QuickLoad hook call must be iOS-only")
    if not any(
        "virtual void ios_begin_quick_load_sector_startup_epoch() {}" in region
        for region in interface_ios
    ):
        errors.append("IRender must provide an iOS-only default QuickLoad hook")
    if not any(
        "ios_sector_fallback::StartupEvidence ios_sector_startup_evidence;" in region
        and "ios_sector_fallback::CameraApplyBarrier ios_level_load_camera_barrier;" in region
        and "ios_sector_fallback::CameraApplyBarrier ios_quick_load_camera_barrier;" in region
        and "IosSectorStartupPendingReport ios_sector_startup_pending_report;" in region
        for region in header_ios
    ) or not any(
        "ios_begin_quick_load_sector_startup_epoch() override;" in region
        for region in header_ios
    ):
        errors.append("one CRender instance must own iOS-only evidence, both barriers, pending report and hook")
    if not any(
        "CRender::ios_begin_quick_load_sector_startup_epoch" in region
        and "StartupTrigger::QuickLoad" in region
        for region in loader_ios
    ):
        errors.append("renderer QuickLoad hook implementation must be iOS-only")

    if not any("#include <unistd.h>" in region for region in calculate_ios) or not any(
        "BuildIosSectorStartupPayload" in region
        and "IosSectorStartupMethod::Retained" in region
        and "IosSectorStartupMethod::None" in region
        for region in calculate_ios
    ):
        errors.append("marker implementation and method vocabulary must be iOS-only")

    generation_state = (
        any("u64 m_iosCameraApplyGeneration{};" in region for region in device_ios)
        and any(
            "ios_note_camera_applied" in region
            and "ios_camera_apply_generation" in region
            for region in device_ios
        )
    )
    if not generation_state:
        errors.append("camera-apply generation state and accessors must be iOS-only")
    if "std::numeric_limits<u64>::max()" not in note_camera or "++m_iosCameraApplyGeneration" not in note_camera:
        errors.append("camera-apply generation must increment saturatingly")
    all_generation_calls = sources["camera_manager"].count("Device.ios_note_camera_applied();")
    if all_generation_calls != 1 or not any("Device.ios_note_camera_applied();" in region for region in camera_ios):
        errors.append("camera generation must increment exactly once in the iOS ApplyDevice tail")
    apply_offset = camera_apply.find("Device.mProject._32 = -m_cam_info.offsetY;")
    apply_note = camera_apply.find("Device.ios_note_camera_applied();")
    if apply_offset < 0 or apply_note < apply_offset:
        errors.append("camera generation must advance after Device camera and projection writes")

    marker_source = sources["calculate"].replace('"\n        "', "")
    if MARKER_GRAMMAR not in marker_source:
        errors.append("startup-sector marker grammar must be exact and versioned")
    pid = build_payload.find("getpid()")
    positive_pid = re.search(r"process_id\s*<=\s*0", build_payload)
    marker = build_payload.find("* iOS sector startup v1")
    if pid < 0 or positive_pid is None or marker < 0 or not pid < positive_pid.start() < marker:
        errors.append("marker must require a positive current-process PID before Msg")
    if "BuildIosSectorLevelToken" not in build_payload:
        errors.append("marker must validate the bounded level token before Msg")

    policy = sources["policy"]
    required_policy = (
        "StartupPhase::Awaiting",
        "StartupPhase::UnresolvedReported",
        "StartupPhase::Resolved",
        "constexpr bool ShouldDetectSector",
        "trigger_ != StartupTrigger::QuickLoad",
        "std::numeric_limits<std::uint64_t>::max()",
        "PreparedTransition PrepareDetected",
        "PreparedTransition PrepareNoDetection",
        "bool CommitPrepared",
        "transition.expectedPhase != phase_",
    )
    if any(token not in policy for token in required_policy):
        errors.append("pure evidence policy must retain trigger policy, two-phase transitions, QuickLoad guard and overflow fail-close")
    try:
        should_detect_policy = function_body(
            policy, r"\[\[nodiscard\]\]\s+constexpr\s+bool\s+ShouldDetectSector\s*\("
        )
    except AssertionError:
        should_detect_policy = ""
    required_trigger_policy = (
        "return cameraMoved\n        || (",
        "trigger == StartupTrigger::LevelLoad",
        "phase == StartupPhase::Awaiting",
        "!lastSectorValid",
        "!pendingReportActive",
        "levelLoadCameraBarrierPassed",
    )
    if any(token not in should_detect_policy for token in required_trigger_policy):
        errors.append("pure evidence policy must retain trigger policy, two-phase transitions, QuickLoad guard and overflow fail-close")
    if "ObserveDetected" in policy or "ObserveNoDetection" in policy:
        errors.append("evidence policy must not mutate state during observation preparation")
    return errors


class SectorMarkerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sources = {name: path.read_text(encoding="utf-8") for name, path in PATHS.items()}

    def assert_fails(self, sources: dict[str, str], expected: str) -> None:
        errors = contract_errors(sources)
        self.assertIn(expected, errors, errors)

    def mutated(self, name: str, old: str, new: str) -> dict[str, str]:
        sources = dict(self.sources)
        self.assertIn(old, sources[name])
        sources[name] = sources[name].replace(old, new, 1)
        return sources

    def test_baseline(self) -> None:
        self.assertEqual([], contract_errors(self.sources))

    def test_quick_load_hook_order_mutations_fail(self) -> None:
        event = self.sources["game"]
        hook_block = (
            "#if defined(XR_PLATFORM_APPLE_IOS)\n"
            "        if (GEnv.Render)\n"
            "            GEnv.Render->ios_begin_quick_load_sector_startup_epoch();\n"
            "#endif\n"
        )
        for replacement in ("", hook_block.replace("#if", "xr_free(saved_name);\n#if")):
            with self.subTest(replacement=replacement[:20]):
                sources = dict(self.sources)
                sources["game"] = event.replace(hook_block, replacement, 1)
                self.assert_fails(
                    sources,
                    "QuickLoad hook must run after restart_simulator and before xr_free",
                )

    def test_level_begin_before_loaded_and_unload_deactivate_removal_fail(self) -> None:
        sources = self.mutated(
            "loader",
            "    b_loaded = TRUE;\n#if defined(XR_PLATFORM_APPLE_IOS)",
            "#if defined(XR_PLATFORM_APPLE_IOS)",
        )
        self.assert_fails(sources, "level-load epoch must begin after b_loaded becomes true")
        sources = self.mutated(
            "loader", "    ios_sector_startup_evidence.Deactivate();\n", ""
        )
        self.assert_fails(sources, "level unload must deactivate evidence before an early return")

    def test_detected_prepare_store_disarm_and_retry_order_mutations_fail(self) -> None:
        sources = self.mutated(
            "calculate",
            "ios_sector_startup_evidence.PrepareDetected(committed)",
            "ios_sector_startup_evidence.PrepareDetected(false)",
        )
        self.assert_fails(
            sources,
            "detected outcome must Resolve, commit sector, Prepare, store, disarm LevelLoad, then retry",
        )
        sources = self.mutated(
            "calculate",
            "        const bool stored = StoreIosSectorStartupPending(ios_sector_startup_pending_report, prepared,",
            "        ios_level_load_camera_barrier.Disarm();\n"
            "        const bool stored = StoreIosSectorStartupPending(ios_sector_startup_pending_report, prepared,",
        )
        self.assert_fails(
            sources,
            "detected outcome must Resolve, commit sector, Prepare, store, disarm LevelLoad, then retry",
        )

    def test_dedicated_barrier_and_quickload_guard_mutations_fail(self) -> None:
        sources = self.mutated(
            "calculate",
            "ios_level_load_camera_barrier.Passed(Device.ios_camera_apply_generation())",
            "true",
        )
        self.assert_fails(
            sources,
            "stationary LevelLoad detection must pass its dedicated camera barrier",
        )
        sources = self.mutated(
            "calculate",
            "ios_quick_load_camera_barrier.Passed(Device.ios_camera_apply_generation())",
            "true",
        )
        self.assert_fails(
            sources,
            "QuickLoad no-detection must pass its barrier, store, disarm, then retry",
        )
        sources = self.mutated(
            "calculate",
            "if (quickLoadEpoch && quickLoadCameraBarrierPassed && !ios_sector_startup_pending_report.active)",
            "if (quickLoadEpoch && quickLoadCameraBarrierPassed)",
        )
        self.assert_fails(sources, "QuickLoad no-detection must require its barrier and no pending report")

    def test_payload_commit_and_pending_retry_mutations_fail(self) -> None:
        sources = self.mutated(
            "calculate",
            "if (!BuildIosSectorStartupPayload(pending, payload))",
            "if (false)",
        )
        self.assert_fails(
            sources,
            "validated payload must precede CommitPrepared, Msg and FlushLog",
        )
        sources = self.mutated(
            "calculate",
            "    RetryIosSectorStartupPending(ios_sector_startup_evidence, ios_sector_startup_pending_report);\n#endif\n\n    auto& dsgraph_main",
            "#endif\n\n    auto& dsgraph_main",
        )
        self.assert_fails(
            sources,
            "pending evidence must retry before accepting a new camera observation",
        )
        sources = self.mutated(
            "calculate",
            "ios_sector_startup_pending_report.active, levelLoadCameraBarrierPassed",
            "false, levelLoadCameraBarrierPassed",
        )
        self.assert_fails(
            sources,
            "stationary LevelLoad policy call must provide active trigger phase sector pending and barrier state",
        )

    def test_camera_generation_and_lifecycle_baseline_mutations_fail(self) -> None:
        sources = self.mutated(
            "camera_manager", "    Device.ios_note_camera_applied();\n", ""
        )
        self.assert_fails(
            sources,
            "camera generation must increment exactly once in the iOS ApplyDevice tail",
        )
        sources = self.mutated(
            "device_header",
            "m_iosCameraApplyGeneration < std::numeric_limits<u64>::max()",
            "true",
        )
        self.assert_fails(sources, "camera-apply generation must increment saturatingly")
        sources = self.mutated(
            "loader", "        ios_quick_load_camera_barrier.Arm(cameraApplyGeneration);\n", ""
        )
        self.assert_fails(
            sources,
            "QuickLoad must disarm stale barriers, clear pending, then arm its post-restart baseline",
        )
        sources = self.mutated(
            "loader", "        ios_level_load_camera_barrier.Arm(cameraApplyGeneration);\n", ""
        )
        self.assert_fails(
            sources,
            "LevelLoad must disarm both barriers, clear pending, then arm a post-epoch baseline",
        )
        sources = self.mutated(
            "render_header", "ios_sector_fallback::CameraApplyBarrier ios_level_load_camera_barrier;\n", ""
        )
        self.assert_fails(
            sources,
            "one CRender instance must own iOS-only evidence, both barriers, pending report and hook",
        )

    def test_camera_behavior_mutations_fail(self) -> None:
        sources = self.mutated("calculate", CAMERA_MOVED, "const bool cameraMoved = false;")
        self.assert_fails(sources, "camera movement must remain an explicit, unmodified input to the policy")
        sources = self.mutated("calculate", SHOULD_DETECT_CONDITION, "if (true)")
        self.assert_fails(sources, "condition not found: " + SHOULD_DETECT_CONDITION)
        sources = self.mutated(
            "calculate",
            "auto sector_id = dsgraph_main.detect_sector(Device.vCameraPosition);",
            "auto sector_id = dsgraph_main.detect_sector(Device.vCameraPosition);\n"
            "        (void)dsgraph_main.detect_sector(Device.vCameraPosition);",
        )
        self.assert_fails(sources, "camera branch must issue exactly one original exact-sector query")
        sources = self.mutated(
            "calculate",
            "// Detect camera-sector",
            "last_sector_id = IRender_Sector::INVALID_SECTOR_ID;\n    // Detect camera-sector",
        )
        self.assert_fails(sources, "Calculate telemetry must not invalidate the retained sector")
        sources = self.mutated(
            "calculate",
            "// Detect camera-sector",
            "Device.vCameraPositionSaved.set(0.f, 0.f, 0.f);\n    // Detect camera-sector",
        )
        self.assert_fails(sources, "Calculate telemetry must not mutate saved camera position")

    def test_marker_pid_grammar_and_flush_mutations_fail(self) -> None:
        sources = self.mutated("calculate", "process_id <= 0", "process_id < 0")
        self.assert_fails(sources, "marker must require a positive current-process PID before Msg")
        sources = self.mutated("calculate", "* iOS sector startup v1", "* iOS sector startup")
        self.assert_fails(sources, "startup-sector marker grammar must be exact and versioned")
        sources = self.mutated("calculate", "    FlushLog();\n", "")
        self.assert_fails(sources, "validated payload must precede CommitPrepared, Msg and FlushLog")

    def test_trigger_policy_and_two_phase_policy_mutations_fail(self) -> None:
        sources = self.mutated(
            "policy", "[[nodiscard]] bool CommitPrepared", "[[nodiscard]] bool CommitTransition"
        )
        self.assert_fails(
            sources,
            "pure evidence policy must retain trigger policy, two-phase transitions, QuickLoad guard and overflow fail-close",
        )
        sources = self.mutated(
            "policy", "transition.expectedPhase != phase_", "false"
        )
        self.assert_fails(
            sources,
            "pure evidence policy must retain trigger policy, two-phase transitions, QuickLoad guard and overflow fail-close",
        )
        for old, new in (
            ("return cameraMoved\n        || (", "return cameraMoved && ("),
            ("startupActive && trigger == StartupTrigger::LevelLoad", "startupActive && true"),
            ("phase == StartupPhase::Awaiting", "true"),
            ("!lastSectorValid", "true"),
            ("!pendingReportActive", "true"),
            ("&& levelLoadCameraBarrierPassed);", "&& true);"),
            ("trigger_ != StartupTrigger::QuickLoad", "false"),
        ):
            with self.subTest(old=old):
                sources = self.mutated("policy", old, new)
                self.assert_fails(
                    sources,
                    "pure evidence policy must retain trigger policy, two-phase transitions, QuickLoad guard and overflow fail-close",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
