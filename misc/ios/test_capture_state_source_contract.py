#!/usr/bin/env python3
"""Mutation-backed source contract for the iOS capture-v2 producer.

This is deliberately textual: it protects architectural ordering and isolation
that a host unit test cannot observe without launching the engine.  Every
accepted anchor is independently removed from an in-memory copy to prove the
checker would reject a regression.
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PATHS = {
    "gl": "src/Layers/xrRenderGL/glHW.cpp",
    "capture": "src/Layers/xrRenderGL/ios_capture_state_v2.h",
    "r2": "src/Layers/xrRender_R2/r2.h",
    "input": "src/xrEngine/xr_input.cpp",
    "input_header": "src/xrEngine/xr_input.h",
    "input_state": "src/xrEngine/ios/ios_diagnostic_input_state.h",
    "render_cmake": "src/Layers/xrRenderPC_GL/CMakeLists.txt",
    "engine_cmake": "src/xrEngine/CMakeLists.txt",
    "device": "src/xrEngine/device.cpp",
    "render_base": "src/Layers/xrRender/D3DXRenderBase.cpp",
    "irender": "src/xrEngine/Render.h",
}


def read_sources() -> dict[str, str]:
    return {name: (ROOT / path).read_text(encoding="utf-8") for name, path in PATHS.items()}


def require(source: str, needle: str, label: str, errors: list[str]) -> int:
    position = source.find(needle)
    if position < 0:
        errors.append(label)
    return position


def contract_errors(sources: dict[str, str]) -> list[str]:
    errors: list[str] = []
    gl = sources["gl"]
    capture = sources["capture"]
    r2 = sources["r2"]
    input_cpp = sources["input"]
    input_header = sources["input_header"]
    input_state = sources["input_state"]
    device = sources["device"]
    render_base = sources["render_base"]

    # iOS-only producer and tokenized state are unavailable to non-iOS builds.
    require(gl, "#if defined(XR_PLATFORM_APPLE_IOS)\n#include <SDL_syswm.h>", "gl iOS include guard", errors)
    require(gl, "#if defined(XR_PLATFORM_APPLE_IOS)\nnamespace", "gl iOS producer guard", errors)
    require(capture, "PpmHeader", "tokenized PPM-header implementation", errors)
    require(capture, "openxray-capture-v2 token=", "PPM token literal", errors)

    reserve = require(gl, "const u64 sequence = ++s_ios_shot_sequence;", "sequence reserve", errors)
    freeze = require(gl, "FreezeIosCaptureSnapshot(snapshot, sequence, w, h)", "snapshot freeze", errors)
    serialize = require(gl, "ios_capture_v2::Serialize(snapshot, sidecar, serializeError)", "snapshot serialize", errors)
    readback_prepare = require(gl, "PrepareIosCaptureReadback(sequence)", "readback preparation gate", errors)
    stale_drain = require(gl, "while ((staleError = glGetError()) != GL_NO_ERROR)", "pre-readback GL-error drain", errors)
    framebuffer_status = require(gl, "glCheckFramebufferStatus(GL_READ_FRAMEBUFFER)", "read framebuffer status", errors)
    framebuffer_error = require(gl, "const GLenum framebufferError = glGetError();", "read framebuffer error check", errors)
    zero_initialize = require(gl, "std::memset(rgba, 0, rgbaBytes);", "readback buffer zero initialization", errors)
    readback = require(gl, "glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, rgba);", "GL readback", errors)
    readback_error = require(gl, "const GLenum readbackError = glGetError();", "GL readback error check", errors)
    fail_closed = require(
        gl,
        "if (readbackError != GL_NO_ERROR)\n"
        "                            {\n"
        "                                Msg(\"! iOS diag: glReadPixels failed for sequence=%llu (error=0x%x); capture not published\",\n"
        "                                    static_cast<unsigned long long>(sequence), static_cast<unsigned>(readbackError));\n"
        "                            }\n"
        "                            else\n"
        "                            {\n"
        "                                const char* home = getenv(\"HOME\");",
        "GL readback fail-closed publication gate",
        errors,
    )
    header = require(gl, "ios_capture_v2::PpmHeader(snapshot)", "token header emission", errors)
    ppm_rename = require(gl, "rename(tmp, dst)", "PPM atomic rename", errors)
    json_rename = require(gl, "rename(meta_tmp, meta_dst)", "JSON atomic rename", errors)
    if min(reserve, freeze, serialize, readback) >= 0 and not (reserve < freeze < serialize < readback):
        errors.append("sequence/freeze/serialize/readback ordering")
    if (min(stale_drain, framebuffer_status, framebuffer_error, readback_prepare, zero_initialize, readback,
            readback_error, fail_closed, header) >= 0
        and not (stale_drain < framebuffer_status < framebuffer_error < readback_prepare < zero_initialize
                 < readback < readback_error < fail_closed < header)):
        errors.append("GL readback fail-closed ordering")
    if min(ppm_rename, json_rename) >= 0 and ppm_rename >= json_rename:
        errors.append("PPM rename must precede JSON rename")

    accessor = require(r2, "IosCaptureWorldState ios_capture_world_state() const", "CRender capture accessor", errors)
    b_loaded = require(r2, "return { b_loaded, last_sector_id != IRender_Sector::INVALID_SECTOR_ID,", "b_loaded and sector validity", errors)
    epoch = require(r2, "ios_sector_startup_evidence.epoch()", "sector epoch", errors)
    if min(accessor, b_loaded, epoch) >= 0 and not (accessor < b_loaded < epoch):
        errors.append("CRender capture accessor ordering")
    if "ios_capture_world_state" in input_header or "ios_diagnostic_input" in input_header:
        errors.append("xr_input.h leaked iOS diagnostic protocol")
    require(input_state, "namespace ios_diagnostic_input\n{", "isolated input-state header", errors)
    require(input_state, "Snapshot snapshot();", "POD snapshot accessor", errors)
    if "xrRender" in input_state or "#include \"r2.h\"" in input_state:
        errors.append("isolated input-state header depends on renderer")
    if "ios_capture_world_state" in sources["irender"]:
        errors.append("IRender virtual capture API leak")

    begin = require(input_cpp, "ios_diagnostic_input::begin(requestId, key, mapped_key, ms", "hold begin wiring", errors)
    accept = require(input_cpp, "ios_diagnostic_input::accept(Device.dwFrame, Device.dwTimeContinual, now);", "accept wiring", errors)
    release = require(input_cpp, "ios_diagnostic_input::release(Device.dwFrame, Device.dwTimeContinual, now);", "release wiring", errors)
    cancel = require(input_cpp, "ios_diagnostic_input::cancel(Device.dwFrame, Device.dwTimeContinual, SDL_GetTicks());", "cancel wiring", errors)
    lifecycle = input_cpp.find("void CInput::OnAppDeactivate(void)")
    lifecycle_cancel = input_cpp.find("ios_diagnostic_input::cancel(Device.dwFrame, Device.dwTimeContinual, SDL_GetTicks());", lifecycle)
    accept_after_begin = input_cpp.find("ios_diagnostic_input::accept(Device.dwFrame, Device.dwTimeContinual, now);", begin)
    if min(begin, accept_after_begin) >= 0 and begin >= accept_after_begin:
        errors.append("begin must precede accept")
    if min(accept, release) >= 0 and accept >= release:
        errors.append("accept/release ordering")
    if cancel < 0 or lifecycle_cancel < 0:
        errors.append("lifecycle cancel is absent")

    require(sources["render_cmake"], "../xrRenderGL/ios_capture_state_v2.h", "capture header CMake registration", errors)
    require(sources["engine_cmake"], "ios/ios_diagnostic_input_state.h", "input-state CMake registration", errors)

    seq_registration = require(input_cpp, "Device.seqFrame.Add(this, REG_PRIORITY_HIGH);", "CInput seqFrame registration", errors)
    frame_move = require(device, "FrameMove();", "frame loop FrameMove", errors)
    do_render = require(device, "DoRender();", "frame loop DoRender", errors)
    seq_process = require(device, "seqFrame.Process();", "FrameMove seqFrame processing", errors)
    present = require(render_base, "HW.Present();", "render End Present", errors)
    if min(frame_move, do_render) >= 0 and frame_move >= do_render:
        errors.append("FrameMove must happen before DoRender")
    if seq_registration < 0 or seq_process < 0 or present < 0:
        errors.append("main-thread input-to-present anchor is incomplete")
    return errors


class CaptureStateSourceContractTests(unittest.TestCase):
    def test_current_source_satisfies_contract(self) -> None:
        self.assertEqual(contract_errors(read_sources()), [])

    def test_each_critical_anchor_is_mutation_backed(self) -> None:
        mutations = (
            ("gl iOS include guard", "gl", "#if defined(XR_PLATFORM_APPLE_IOS)\n#include <SDL_syswm.h>"),
            ("tokenized PPM-header implementation", "capture", "PpmHeader"),
            ("sequence reserve", "gl", "const u64 sequence = ++s_ios_shot_sequence;"),
            ("snapshot freeze", "gl", "FreezeIosCaptureSnapshot(snapshot, sequence, w, h)"),
            ("snapshot serialize", "gl", "ios_capture_v2::Serialize(snapshot, sidecar, serializeError)"),
            ("readback preparation gate", "gl", "PrepareIosCaptureReadback(sequence)"),
            ("pre-readback GL-error drain", "gl", "while ((staleError = glGetError()) != GL_NO_ERROR)"),
            ("read framebuffer status", "gl", "glCheckFramebufferStatus(GL_READ_FRAMEBUFFER)"),
            ("read framebuffer error check", "gl", "const GLenum framebufferError = glGetError();"),
            ("readback buffer zero initialization", "gl", "std::memset(rgba, 0, rgbaBytes);"),
            ("GL readback", "gl", "glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, rgba);"),
            ("GL readback error check", "gl", "const GLenum readbackError = glGetError();"),
            ("GL readback fail-closed publication gate", "gl", "if (readbackError != GL_NO_ERROR)\n"
             "                            {\n"
             "                                Msg(\"! iOS diag: glReadPixels failed for sequence=%llu (error=0x%x); capture not published\",\n"
             "                                    static_cast<unsigned long long>(sequence), static_cast<unsigned>(readbackError));\n"
             "                            }\n"
             "                            else\n"
             "                            {\n"
             "                                const char* home = getenv(\"HOME\");"),
            ("PPM atomic rename", "gl", "rename(tmp, dst)"),
            ("JSON atomic rename", "gl", "rename(meta_tmp, meta_dst)"),
            ("CRender capture accessor", "r2", "IosCaptureWorldState ios_capture_world_state() const"),
            ("b_loaded and sector validity", "r2", "return { b_loaded, last_sector_id != IRender_Sector::INVALID_SECTOR_ID,"),
            ("sector epoch", "r2", "ios_sector_startup_evidence.epoch()"),
            ("isolated input-state header", "input_state", "namespace ios_diagnostic_input\n{"),
            ("hold begin wiring", "input", "ios_diagnostic_input::begin(requestId, key, mapped_key, ms"),
            ("accept wiring", "input", "ios_diagnostic_input::accept(Device.dwFrame, Device.dwTimeContinual, now);"),
            ("release wiring", "input", "ios_diagnostic_input::release(Device.dwFrame, Device.dwTimeContinual, now);"),
            ("cancel wiring", "input", "ios_diagnostic_input::cancel(Device.dwFrame, Device.dwTimeContinual, SDL_GetTicks());"),
            ("capture header CMake registration", "render_cmake", "../xrRenderGL/ios_capture_state_v2.h"),
            ("input-state CMake registration", "engine_cmake", "ios/ios_diagnostic_input_state.h"),
            ("CInput seqFrame registration", "input", "Device.seqFrame.Add(this, REG_PRIORITY_HIGH);"),
            ("frame loop FrameMove", "device", "FrameMove();"),
            ("frame loop DoRender", "device", "DoRender();"),
            ("FrameMove seqFrame processing", "device", "seqFrame.Process();"),
            ("render End Present", "render_base", "HW.Present();"),
        )
        source = read_sources()
        for label, file_name, needle in mutations:
            with self.subTest(label=label):
                mutated = copy.deepcopy(source)
                count = -1 if label in {"release wiring", "cancel wiring"} else 1
                mutated[file_name] = mutated[file_name].replace(needle, "REMOVED", count)
                self.assertTrue(contract_errors(mutated), label)

        mutated = copy.deepcopy(source)
        mutated["irender"] += "\nvirtual void ios_capture_world_state();\n"
        self.assertTrue(contract_errors(mutated), "IRender virtual API leak")


if __name__ == "__main__":
    unittest.main(verbosity=2)
