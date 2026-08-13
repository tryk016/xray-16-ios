#!/usr/bin/env python3
"""Host-only contract tests for the controlled SDL2 UIKit UIScene backport."""

from __future__ import annotations

import os as _test_feedback_os
if _test_feedback_os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import sys as _test_feedback_sys
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment("python::misc/ios/test_sdl2_scene_contract.py", _test_feedback_sys.argv)
    except BaseException:
        pass

import hashlib
import json
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APPLIER = ROOT / "cmake/ios/apply_sdl2_scene_patch.py"
MANIFEST_PATH = ROOT / "cmake/ios/sdl2_scene_patch_manifest.json"
PATCH = ROOT / "cmake/ios/patches/sdl2-2.32.10-uikit-scene.patch"
CMAKE = ROOT / "cmake/ios/deps/CMakeLists.txt"
INFO = ROOT / "misc/ios/Info.plist.in"
URL = "https://github.com/libsdl-org/SDL/releases/download/release-2.32.10/SDL2-2.32.10.tar.gz"
ARCHIVE_SHA256 = "5f5993c530f084535c65a6879e9b26ad441169b3e25d789d83287040a9ca5165"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_archive() -> Path:
    candidates = (
        ROOT / "build/ios-deps-iphoneos/dep_SDL2-prefix/src/SDL2-2.32.10.tar.gz",
        ROOT / "build/ios-deps-iphonesimulator/dep_SDL2-prefix/src/SDL2-2.32.10.tar.gz",
    )
    for candidate in candidates:
        if candidate.is_file() and sha256(candidate) == ARCHIVE_SHA256:
            return candidate
    raise AssertionError("pristine SDL2 2.32.10 archive is unavailable or has the wrong SHA-256")


def extract_pristine(target: Path) -> Path:
    archive = find_archive()
    target.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        for member in members:
            resolved = (target / member.name).resolve()
            try:
                resolved.relative_to(target.resolve())
            except ValueError as error:
                raise AssertionError(f"unsafe SDL2 archive member: {member.name}") from error
        source.extractall(target, members=members)
    roots = [path for path in target.iterdir() if path.is_dir() and path.name == "SDL2-2.32.10"]
    if roots != [target / "SDL2-2.32.10"]:
        raise AssertionError("SDL2 archive root is not SDL2-2.32.10")
    return roots[0]


def run_applier(source: Path, *, patch: Path = PATCH, url: str = URL, archive_sha: str = ARCHIVE_SHA256, verify_only: bool = False) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(APPLIER),
        "--source",
        str(source),
        "--manifest",
        str(MANIFEST_PATH),
        "--patch",
        str(patch),
        "--url",
        url,
        "--url-sha256",
        archive_sha,
    ]
    if verify_only:
        command.append("--verify-only")
    return subprocess.run(command, check=False, text=True, capture_output=True)


def assert_scene_mechanism(appdelegate: str, events: str, window: str) -> None:
    scene_start = appdelegate.index("@implementation SDLUIKitSceneDelegate")
    scene_end = appdelegate.index("@implementation SDLUIKitDelegate", scene_start)
    scene = appdelegate[scene_start:scene_end]
    claim_start = appdelegate.index("static BOOL SDL_ClaimMainRun(void)")
    run_start = appdelegate.index("static void SDL_RunMainOnce(void)", claim_start)
    run_end = appdelegate.index("\n}\n\nint SDL_UIKitRunApp", run_start) + 2
    claim = appdelegate[claim_start:run_start]
    run = appdelegate[run_start:run_end]
    assert "__block BOOL claimed = NO;" in claim
    assert claim.count("dispatch_once(&forward_main_once") == 1
    assert "claimed = YES;" in claim
    assert "return claimed;" in claim
    assert "forward_main(" not in claim
    assert "SDL_iPhoneSetEventPump" not in claim
    assert "if (!SDL_ClaimMainRun())" in run
    assert "dispatch_once" not in run
    assert run.index("if (!SDL_ClaimMainRun())") < run.index("forward_main(")
    assert appdelegate.count("SDL_RunMainOnce();") == 1
    assert "[SDLUIKitSceneDelegate getSceneDelegateClassName]" in appdelegate
    will_connect_start = scene.index("scene:(UIScene *)scene willConnectToSession:")
    will_connect_end = scene.index("scene:(UIScene *)scene openURLContexts:", will_connect_start)
    will_connect = scene[will_connect_start:will_connect_end]
    assert "SDL_SetMainReady();" in will_connect
    assert "performSelector:@selector(postFinishLaunch)" in will_connect
    assert "initWithWindowScene:windowScene" in scene
    callbacks = {
        "sceneDidBecomeActive": "SDL_OnApplicationDidBecomeActive();",
        "sceneWillResignActive": "SDL_OnApplicationWillResignActive();",
        "sceneWillEnterForeground": "SDL_OnApplicationWillEnterForeground();",
        "sceneDidEnterBackground": "SDL_OnApplicationDidEnterBackground();",
    }
    for method, callback in callbacks.items():
        assert method in scene
        assert scene.count(callback) == 1
        assert events.count(callback) == 0
    for notification in (
        "UIApplicationDidBecomeActiveNotification",
        "UIApplicationWillResignActiveNotification",
        "UIApplicationDidEnterBackgroundNotification",
        "UIApplicationWillEnterForegroundNotification",
    ):
        assert notification not in events
    assert "UIApplicationWillTerminateNotification" in events
    assert "UIApplicationDidReceiveMemoryWarningNotification" in events

    selector_start = window.index("static UIWindowScene *UIKit_GetActiveWindowScene(void)")
    selector_end = window.index("\n}\n\nint UIKit_CreateWindow", selector_start) + 2
    selector = window[selector_start:selector_end]
    active_index = selector.find("UISceneActivationStateForegroundActive")
    inactive_index = selector.find("UISceneActivationStateForegroundInactive")
    last_resort_index = selector.rfind("for (UIScene *scene in connectedScenes)")
    assert 0 <= active_index < inactive_index < last_resort_index
    assert selector.count("for (UIScene *scene in connectedScenes)") == 3
    assert selector.count("return (UIWindowScene *)scene;") == 3

    active_start = window.index("if (@available(iOS 13.0, *))", selector_end)
    active_end = window.index("        } else {", active_start)
    active_branch = window[active_start:active_end]
    assert "UIKit_GetActiveWindowScene" in window
    assert "initWithWindowScene:windowScene" in active_branch
    assert "initWithFrame" not in active_branch
    assert "No active UIWindowScene for UIKit window creation." in active_branch


def assert_cmake_contract(cmake: str) -> None:
    assert "ExternalProject_Add(dep_SDL2" in cmake
    assert cmake.count(URL) == 3
    assert cmake.count(f"SHA256={ARCHIVE_SHA256}") == 1
    assert f"sha256={ARCHIVE_SHA256}" in cmake
    assert "ExternalProject_Add_Step(dep_SDL2 openxray_uikit_scene_patch" in cmake
    assert "--source <SOURCE_DIR>" in cmake
    assert "--manifest \"${_sdl2_scene_patch_manifest}\"" in cmake
    assert "--patch \"${_sdl2_scene_patch}\"" in cmake
    assert "DEPENDEES update" in cmake
    assert "DEPENDERS configure" in cmake
    assert "\"${CMAKE_CURRENT_LIST_FILE}\"" in cmake
    assert "${_sdl2_scene_patch_command_file}" in cmake


def assert_manifest_contract(plist: dict[str, object]) -> None:
    assert "UIMainStoryboardFile" not in plist
    manifest = plist["UIApplicationSceneManifest"]
    assert isinstance(manifest, dict)
    assert manifest == {
        "UIApplicationSupportsMultipleScenes": False,
        "UISceneConfigurations": {
            "UIWindowSceneSessionRoleApplication": [
                {
                    "UISceneClassName": "UIWindowScene",
                    "UISceneConfigurationName": "OpenXRayScene",
                    "UISceneDelegateClassName": "SDLUIKitSceneDelegate",
                }
            ]
        },
    }
    assert "MinimumOSVersion" in plist


class SDL2SceneContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def new_pristine(self, directory: Path) -> Path:
        return extract_pristine(directory / "source")

    def test_repository_contract_is_pinned_and_complete(self) -> None:
        self.assertEqual(self.manifest["schema"], 1)
        self.assertEqual(self.manifest["source"], {"version": "2.32.10", "url": URL, "sha256": ARCHIVE_SHA256})
        self.assertEqual(self.manifest["upstream"]["sdl3_commit"], "b46e26e65aac8f7d5b5c83d43ea99a507c093930")
        self.assertEqual(sha256(PATCH), self.manifest["patch_sha256"])
        files = self.manifest["files"]
        self.assertEqual(
            set(files),
            {
                "CMakeLists.txt",
                "src/video/uikit/SDL_uikitappdelegate.h",
                "src/video/uikit/SDL_uikitappdelegate.m",
                "src/video/uikit/SDL_uikitevents.m",
                "src/video/uikit/SDL_uikitwindow.h",
                "src/video/uikit/SDL_uikitwindow.m",
            },
        )
        changed = {path for path, entry in files.items() if entry["preimage"] != entry["postimage"]}
        patch_paths = set(re.findall(r"^--- a/(.+)$", PATCH.read_text(encoding="utf-8"), flags=re.MULTILINE))
        self.assertEqual(patch_paths, changed)

    def test_pristine_postimage_mixed_and_foreign_states_are_controlled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="openxray-sdl2-scene-") as temporary:
            root = Path(temporary)
            pristine = self.new_pristine(root / "pristine")
            result = run_applier(pristine, verify_only=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("preimage", result.stdout)
            result = run_applier(pristine)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("patched", result.stdout)
            result = run_applier(pristine)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("postimage", result.stdout)

            mixed = self.new_pristine(root / "mixed")
            original_header = (mixed / "src/video/uikit/SDL_uikitappdelegate.h").read_bytes()
            self.assertEqual(run_applier(mixed).returncode, 0)
            (mixed / "src/video/uikit/SDL_uikitappdelegate.h").write_bytes(original_header)
            result = run_applier(mixed)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("mixed, foreign, or only partially patched", result.stderr)

            foreign = self.new_pristine(root / "foreign")
            header = foreign / "src/video/uikit/SDL_uikitwindow.h"
            header.write_bytes(header.read_bytes() + b"/* foreign mutation */\n")
            result = run_applier(foreign)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("mixed, foreign, or only partially patched", result.stderr)

    def test_wrong_patch_hash_and_changed_source_pin_fail_before_writing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="openxray-sdl2-scene-") as temporary:
            root = Path(temporary)
            pristine = self.new_pristine(root / "pristine")
            wrong_patch = root / "wrong.patch"
            shutil.copy2(PATCH, wrong_patch)
            wrong_patch.write_bytes(wrong_patch.read_bytes() + b"\n")
            result = run_applier(pristine, patch=wrong_patch)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("patch SHA-256", result.stderr)
            self.assertEqual(run_applier(pristine, url="https://invalid.example/SDL2.tar.gz").returncode, 1)
            self.assertEqual(run_applier(pristine, archive_sha="0" * 64).returncode, 1)
            self.assertIn("preimage", run_applier(pristine, verify_only=True).stdout)

    def test_postimage_has_exact_scene_ownership(self) -> None:
        with tempfile.TemporaryDirectory(prefix="openxray-sdl2-scene-") as temporary:
            source = self.new_pristine(Path(temporary))
            result = run_applier(source)
            self.assertEqual(result.returncode, 0, result.stderr)
            appdelegate = (source / "src/video/uikit/SDL_uikitappdelegate.m").read_text(encoding="utf-8")
            events = (source / "src/video/uikit/SDL_uikitevents.m").read_text(encoding="utf-8")
            window = (source / "src/video/uikit/SDL_uikitwindow.m").read_text(encoding="utf-8")
            assert_scene_mechanism(appdelegate, events, window)

    def test_mutations_of_lifecycle_ownership_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="openxray-sdl2-scene-") as temporary:
            source = self.new_pristine(Path(temporary))
            self.assertEqual(run_applier(source).returncode, 0)
            appdelegate = (source / "src/video/uikit/SDL_uikitappdelegate.m").read_text(encoding="utf-8")
            events = (source / "src/video/uikit/SDL_uikitevents.m").read_text(encoding="utf-8")
            window = (source / "src/video/uikit/SDL_uikitwindow.m").read_text(encoding="utf-8")

            with self.assertRaises(AssertionError):
                assert_scene_mechanism(appdelegate.replace("dispatch_once(&forward_main_once", "dispatch_missing(&forward_main_once", 1), events, window)
            with self.assertRaises(AssertionError):
                assert_scene_mechanism(appdelegate.replace("claimed = YES;", "claimed = YES;\n        exit_status = forward_main(forward_argc, forward_argv);", 1), events, window)
            with self.assertRaises(AssertionError):
                assert_scene_mechanism(appdelegate.replace("if (!SDL_ClaimMainRun())", "if (SDL_ClaimMainRun())", 1), events, window)
            with self.assertRaises(AssertionError):
                assert_scene_mechanism(appdelegate, events + "\nUIApplicationDidBecomeActiveNotification\n", window)
            with self.assertRaises(AssertionError):
                assert_scene_mechanism(appdelegate, events, window.replace("initWithWindowScene:windowScene", "initWithFrame:data.uiscreen.bounds", 1))
            inactive_branch = """    for (UIScene *scene in connectedScenes) {
        if ([scene isKindOfClass:[UIWindowScene class]] &&
            scene.activationState == UISceneActivationStateForegroundInactive) {
            return (UIWindowScene *)scene;
        }
    }

"""
            with self.assertRaises(AssertionError):
                assert_scene_mechanism(appdelegate, events, window.replace(inactive_branch, "", 1))
            last_resort_branch = """    for (UIScene *scene in connectedScenes) {
        if ([scene isKindOfClass:[UIWindowScene class]]) {
            return (UIWindowScene *)scene;
        }
    }
"""
            with self.assertRaises(AssertionError):
                assert_scene_mechanism(appdelegate, events, window.replace(last_resort_branch, "", 1))

    def test_manifest_cmake_and_gate_inputs_are_exact(self) -> None:
        assert_manifest_contract(plistlib.loads(INFO.read_bytes()))
        cmake = CMAKE.read_text(encoding="utf-8")
        assert_cmake_contract(cmake)
        gate_hash = (ROOT / "misc/ios/gate_hash.py").read_text(encoding="utf-8")
        for required in (
            "cmake/ios/apply_sdl2_scene_patch.py",
            "cmake/ios/sdl2_scene_patch_manifest.json",
            "cmake/ios/patches/sdl2-2.32.10-uikit-scene.patch",
            "misc/ios/test_sdl2_scene_contract.py",
            "build/ios-prefix-iphoneos/lib/libSDL2.a",
            "build/ios-prefix-iphoneos/lib/libSDL2main.a",
        ):
            self.assertIn(required, gate_hash)

    def test_mutated_manifest_and_cmake_contracts_are_rejected(self) -> None:
        plist = plistlib.loads(INFO.read_bytes())
        broken_manifest = dict(plist)
        broken_manifest["UIApplicationSceneManifest"] = {"UIApplicationSupportsMultipleScenes": True}
        with self.assertRaises(AssertionError):
            assert_manifest_contract(broken_manifest)
        with self.assertRaises(AssertionError):
            assert_cmake_contract(CMAKE.read_text(encoding="utf-8").replace("DEPENDERS configure", "DEPENDERS build", 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
