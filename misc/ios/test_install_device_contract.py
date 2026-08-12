#!/usr/bin/env python3
"""Hermetic regression coverage for install_device.sh preflight contracts."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "misc/ios/install_device.sh"
BUILD_CHECK_SCRIPT = REPO / "misc/ios/build_check.sh"
GATE_HASH_SCRIPT = REPO / "misc/ios/gate_hash.py"
GATE_HASH_SPEC = importlib.util.spec_from_file_location("openxray_gate_hash", GATE_HASH_SCRIPT)
assert GATE_HASH_SPEC is not None and GATE_HASH_SPEC.loader is not None
GATE_HASH = importlib.util.module_from_spec(GATE_HASH_SPEC)
GATE_HASH_SPEC.loader.exec_module(GATE_HASH)
LEASE_DEPENDENCIES = (
    "misc/ios/device_lease.sh",
    "misc/ios/command_timeout.py",
)
TEXTURE_BC_FALLBACK_TEST = "misc/ios/texture_bc_fallback_test.cpp"
SHADER_RESOURCE_CONTRACT_TEST = "misc/ios/test_shader_resource_contract.py"
RETAIL_CLONE_STAGING_ARTIFACT_INPUTS = (
    "misc/ios/retail_simulator.sh",
    "misc/ios/retail_simulator_guard.py",
    "misc/ios/retail_import.py",
    "misc/ios/test_retail_import.py",
    "misc/ios/test_retail_clone_staging.py",
    "misc/ios/simulator_quickload_evidence.py",
    "misc/ios/test_simulator_quickload_evidence.py",
    "misc/ios/test_retail_simulator.py",
)
ARTIFACT_HASH_DEPENDENCIES = LEASE_DEPENDENCIES + (
    TEXTURE_BC_FALLBACK_TEST,
    SHADER_RESOURCE_CONTRACT_TEST,
) + RETAIL_CLONE_STAGING_ARTIFACT_INPUTS
ARTIFACT_INPUT_DIRECTORIES = {
    "src",
    "res",
    "Externals",
    "cmake",
    "misc/ios/Assets.xcassets",
    "misc/ios/shadercheck",
    "misc/ios/ui_automation",
}
TEAM = "RMJWWPF379"
BUNDLE = "io.github.tryk016.openxray.RMJWWPF379"
SOURCE = "a" * 64
UUID = "11111111-2222-3333-4444-555555555555"
HASH = "b" * 64
OPENAL = "c" * 64


def executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


class InstallDeviceContract(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="openxray-install-contract-")
        base = Path(os.path.realpath(self.tmp.name))
        self.root, self.home, self.tools = base / "repo", base / "home", base / "tools"
        self.log = base / "calls.log"
        self.security_state = base / "security-state"
        (self.root / "misc/ios/provisioning-stub").mkdir(parents=True)
        self.home.mkdir()
        self.tools.mkdir()
        self.profile_dir = self.home / "Library/Developer/Xcode/UserData/Provisioning Profiles"
        self.profile_dir.mkdir(parents=True)
        self.profile = self.profile_dir / "openxray.mobileprovision"
        self.write_profile()
        self.app = self.root / "bin/aarch64/Release/xr_3da.app"
        self.stamp = self.root / "build/ios-engine-iphoneos/.ios_device_gate_ok"
        self.make_app(self.app)
        self.write_stamp(self.stamp)
        self.fast_app = self.root / "bin/aarch64/FastDevice/xr_3da.app"
        self.fast_stamp = self.root / "build/ios-engine-fastdevice-iphoneos/.ios_fast_device_gate_ok"
        self.make_app(self.fast_app)
        self.write_stamp(self.fast_stamp)
        self.fake_repo()
        self.fake_tools()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fields(self) -> dict[str, str]:
        return {
            "source_sha256": SOURCE, "app_uuid": UUID, "bundle_sha256": HASH,
            "openal_provider": "OpenALSoft-1.25.2-static", "openal_sha256": OPENAL,
            "platform": "IOS", "minos": "16.4",
        }

    def write_profile(self, **changed: str) -> None:
        values = {"app_id": f"{TEAM}.{BUNDLE}", "team": TEAM,
                  "expiry": "Sun Jan 01 00:00:00 GMT 2099", "cert": "Y2VydA=="}
        values.update(changed)
        self.profile.write_text("".join(f"{k}={v}\n" for k, v in values.items()))

    def make_app(self, path: Path, bundle: str = "io.github.tryk016.openxray") -> None:
        path.mkdir(parents=True, exist_ok=True)
        binary = path / "xr_3da"
        binary.write_text("fixture")
        binary.chmod(0o755)
        (path / "Info.plist").write_text(f"bundle={bundle}\nversion=42\n")

    def write_stamp(self, path: Path, values: dict[str, str] | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"{k}={v}\n" for k, v in (values or self.fields()).items()))

    def fake_repo(self) -> None:
        executable(self.root / "misc/ios/gate_hash.py",
                   "#!/usr/bin/env python3\nimport sys\nprint('" + SOURCE +
                   "' if 'ios-device-artifact-v2' in sys.argv else '" + HASH + "')\n")
        executable(self.root / "misc/ios/openal_provider_contract.py",
                   "#!/usr/bin/env python3\nprint('openal_provider=OpenALSoft-1.25.2-static')\n"
                   "print('openal_sha256=" + OPENAL + "')\n")
        executable(self.root / "misc/ios/device_lease.sh",
                   "#!/usr/bin/env bash\n"
                   "echo helper-source >> \"$TEST_LOG\"\n"
                   "ios_device_lease_acquire(){ echo lease >> \"$TEST_LOG\"; return 91; }\n"
                   "ios_device_lease_release(){ echo release >> \"$TEST_LOG\"; }\n"
                   "ios_run_with_timeout(){ shift; \"$@\"; }\n")

    def fake_tools(self) -> None:
        sources = {
            "security": """#!/usr/bin/env bash
echo "security $*" >> "$TEST_LOG"
if [ "$1" = cms ]; then
  for last; do :; done
  cat "$last" || exit 1
  if [ "$FAKE_MUTATE_LIVE_PROFILE" = 1 ] && [ ! -e "$FAKE_SECURITY_STATE" ]; then
    printf mutated > "$FAKE_SECURITY_STATE"
    printf 'app_id=WRONG\nteam=WRONG\nexpiry=Sun Jan 01 00:00:00 GMT 2099\ncert=Y2VydA==\n' > "$FAKE_LIVE_PROFILE"
  fi
  exit 0
fi
if [ "$1" = find-identity ] && [ "$FAKE_IDENTITY" = ok ]; then echo '  1) AABB "Apple Development"'; fi
""",
            "PlistBuddy": """#!/usr/bin/env bash
cmd=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = -c ]; then cmd="$2"; shift 2; continue; fi
  if [ "$1" = -x ]; then shift; continue; fi
  file="$1"; shift
done
read_key(){ value=$(awk -F= -v key="$1" '$1==key{sub(/^[^=]*=/,"");print;exit}' "$file"); [ -n "$value" ] || exit 1; printf '%s\n' "$value"; }
case "$cmd" in
  'Print :Entitlements:application-identifier') read_key app_id ;;
  'Print :Entitlements:com.apple.developer.team-identifier') read_key team ;;
  'Print :ExpirationDate') read_key expiry ;;
  'Print :CFBundleIdentifier') read_key bundle ;;
  'Print :CFBundleVersion') read_key version ;;
  'Print :application-identifier') read_key application-identifier ;;
  'Print :com.apple.developer.team-identifier') read_key com.apple.developer.team-identifier ;;
  'Print :Entitlements') printf 'application-identifier=%s\ncom.apple.developer.team-identifier=%s\n' "$(awk -F= '$1=="app_id"{print $2}' "$file")" "$(awk -F= '$1=="team"{print $2}' "$file")" ;;
  Set*) value=$(printf '%s' "$cmd" | sed 's/.*CFBundleIdentifier //'); sed -i.bak "s/^bundle=.*/bundle=$value/" "$file"; rm -f "$file.bak" ;;
  *) exit 1 ;;
esac
""",
            "plutil": "#!/usr/bin/env bash\ncase \"$*\" in *DeveloperCertificates.0*) [ \"$FAKE_CERT\" = ok ] && printf Y2VydA== || exit 1 ;; *) exit 1 ;; esac\n",
            "base64": "#!/usr/bin/env bash\ncat\n",
            "openssl": "#!/usr/bin/env bash\necho 'sha1 Fingerprint=AA:BB'\n",
            "codesign": """#!/usr/bin/env bash
echo "codesign $*" >> "$TEST_LOG"
case "$*" in
  *"-dvv "*) [ "$FAKE_CODESIGN_VERIFY" = ok ] || exit 1; printf 'TeamIdentifier=%s\n' "$FAKE_SIGN_TEAM" ;;
  *"--entitlements :-"*)
    [ "$FAKE_OMIT_APP_ENTITLEMENT" = 1 ] || printf '%s=%s\n' "$FAKE_SIGN_APP_KEY" "$FAKE_SIGN_APP_ID"
    [ "$FAKE_OMIT_TEAM_ENTITLEMENT" = 1 ] || printf '%s=%s\n' "$FAKE_SIGN_TEAM_KEY" "$FAKE_SIGN_ENT_TEAM"
    ;;
  *"-vv "*) [ "$FAKE_CODESIGN_VERIFY" = ok ] || exit 1 ;;
  *) [ "$FAKE_CODESIGN_SIGN" = ok ] || exit 1 ;;
esac
""",
            "xcrun": """#!/usr/bin/env bash
echo "xcrun $*" >> "$TEST_LOG"
case "$1" in
  dwarfdump) echo 'UUID: 11111111-2222-3333-4444-555555555555 (arm64)' ;;
  vtool) printf '    platform IOS\n    minos 16.4\n' ;;
  *) exit 91 ;;
esac
""",
            "xcodebuild": "#!/usr/bin/env bash\necho xcodebuild >> \"$TEST_LOG\"\nexit 91\n",
            "cp": """#!/usr/bin/env bash
echo "cp $*" >> "$TEST_LOG"
previous=""; last=""
for argument in "$@"; do previous="$last"; last="$argument"; done
/bin/cp "$@" || exit $?
if [ "$FAKE_APP_SNAPSHOT_SYMLINK" = 1 ] && [ "${previous##*.}" = app ]; then
  /bin/ln -s xr_3da "$last/snapshot-link"
fi
""",
        }
        for name, source in sources.items():
            executable(self.tools / name, source)

    def invoke(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        values = os.environ.copy()
        for name in tuple(values):
            if name.startswith("OPENXRAY_INSTALL_") or name == "OPENXRAY_DEVICE_UDID":
                values.pop(name)
        self.log.unlink(missing_ok=True)
        values.update({
            "HOME": str(self.home), "TEST_LOG": str(self.log),
            "OPENXRAY_INSTALL_TEST_MODE": "1",
            "OPENXRAY_INSTALL_TEST_REPO_ROOT": str(self.root),
            "OPENXRAY_INSTALL_TEST_BIN": str(self.tools),
            "FAKE_IDENTITY": "ok", "FAKE_CERT": "ok", "FAKE_CODESIGN_SIGN": "ok",
            "FAKE_CODESIGN_VERIFY": "ok", "FAKE_SIGN_TEAM": TEAM,
            "FAKE_SIGN_APP_ID": f"{TEAM}.{BUNDLE}",
            "FAKE_SIGN_APP_KEY": "application-identifier",
            "FAKE_SIGN_TEAM_KEY": "com.apple.developer.team-identifier",
            "FAKE_SIGN_ENT_TEAM": TEAM,
            "FAKE_OMIT_APP_ENTITLEMENT": "0", "FAKE_OMIT_TEAM_ENTITLEMENT": "0",
            "FAKE_APP_SNAPSHOT_SYMLINK": "0",
            "FAKE_MUTATE_LIVE_PROFILE": "0",
            "FAKE_LIVE_PROFILE": str(self.profile),
            "FAKE_SECURITY_STATE": str(self.security_state),
        })
        if env:
            values.update(env)
        result = subprocess.run(["bash", str(SCRIPT), *args], cwd=self.root, env=values,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log = self.log.read_text() if self.log.exists() else ""
        calls = log.splitlines()
        self.assertNotIn("lease", calls)
        self.assertNotIn("release", calls)
        self.assertNotIn("xcodebuild", calls)
        self.assertFalse(any("devicectl" in call for call in calls), calls)
        return result

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def passes(self, *args: str, env: dict[str, str] | None = None) -> str:
        result = self.invoke("--preflight", *args, env=env)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("PASS — hermetic signing preflight", result.stdout)
        return result.stdout

    def fails(self, *args: str, env: dict[str, str] | None = None, code: int = 1) -> str:
        result = self.invoke("--preflight", *args, env=env)
        self.assertEqual(result.returncode, code, result.stdout)
        return result.stdout

    def pair(self) -> tuple[str, str]:
        app, stamp = self.root / "custom/approved.app", self.root / "custom/approved.stamp"
        self.make_app(app)
        self.write_stamp(stamp)
        return str(app), str(stamp)

    def artifact_digest(self, root: Path) -> str:
        result = subprocess.run(
            [
                sys.executable,
                str(GATE_HASH_SCRIPT),
                "--salt",
                "ios-device-artifact-v2",
                "--ios-artifact-root",
                str(root),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def make_artifact_input_root(self, root: Path) -> None:
        for relative in GATE_HASH.IOS_ARTIFACT_INPUTS:
            path = root / relative
            if relative in ARTIFACT_INPUT_DIRECTORIES:
                path.mkdir(parents=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"fixture {relative}\n")

    def test_clone_staging_gate_is_invoked_and_hashed(self) -> None:
        clone_test = "misc/ios/test_retail_clone_staging.py"
        self.assertIn(clone_test, RETAIL_CLONE_STAGING_ARTIFACT_INPUTS)
        self.assertIn(clone_test, GATE_HASH.IOS_ARTIFACT_INPUTS)
        source = BUILD_CHECK_SCRIPT.read_text()
        self.assertIn(f"python3 {clone_test} \\", source)
        self.assertIn("retail clone-staging regression tests failed", source)

    def test_artifact_dependencies_invalidate_device_artifact_digest(self) -> None:
        self.assertTrue(GATE_HASH_SCRIPT.is_file())
        for path in ARTIFACT_HASH_DEPENDENCIES:
            with self.subTest(dependency=path):
                self.assertTrue((REPO / path).is_file())
                self.assertIn(path, GATE_HASH.IOS_ARTIFACT_INPUTS)

        with tempfile.TemporaryDirectory(prefix="openxray-artifact-inputs-") as temporary:
            root = Path(temporary)
            self.make_artifact_input_root(root)
            baseline = self.artifact_digest(root)
            for relative in ARTIFACT_HASH_DEPENDENCIES:
                with self.subTest(dependency=relative):
                    dependency = root / relative
                    original = dependency.read_bytes()
                    dependency.write_bytes(original + b"# deterministic mutation\n")
                    self.assertNotEqual(
                        baseline,
                        self.artifact_digest(root),
                        f"{relative} must invalidate ios-device-artifact-v2",
                    )
                    dependency.write_bytes(original)
                    self.assertEqual(baseline, self.artifact_digest(root))

    def test_default_env_cli_and_precedence(self) -> None:
        self.passes()
        self.passes("--fast")
        app, stamp = self.pair()
        self.passes(env={"OPENXRAY_INSTALL_APP": app, "OPENXRAY_INSTALL_STAMP": stamp})
        self.passes("--app", app, "--stamp", stamp,
                    env={"OPENXRAY_INSTALL_APP": "/bad.app", "OPENXRAY_INSTALL_STAMP": "/bad.stamp"})
        output = self.passes("--device", "CLI-UDID", env={"OPENXRAY_DEVICE_UDID": "ENV-UDID"})
        self.assertIn("preflight target: CLI-UDID", output)
        output = self.passes(env={"OPENXRAY_DEVICE_UDID": "ENV-UDID"})
        self.assertIn("preflight target: ENV-UDID", output)

    def test_parser_and_combinations_precede_test_mode_injection(self) -> None:
        raw_invalid = [
            ("--device", "--preflight"),
            ("--app", "--preflight"),
            ("--stamp", "--preflight"),
            (),
            ("--renew",),
            ("--launch",),
            ("--preflight", "--launch"),
        ]
        for args in raw_invalid:
            result = self.invoke(*args)
            self.assertEqual(result.returncode, 2, (args, result.stdout))
            self.assertEqual(self.calls(), [], args)
        result = self.invoke("--preflight", env={"OPENXRAY_INSTALL_TEST_MODE": "2"})
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(self.calls(), [])

    def test_path_inputs_reject_crlf_before_source(self) -> None:
        app, stamp = self.pair()
        for args in (
            ("--preflight", "--app", app + "\nshadow", "--stamp", stamp),
            ("--preflight", "--app", app, "--stamp", stamp + "\rshadow"),
        ):
            result = self.invoke(*args)
            self.assertEqual(result.returncode, 2, (args, result.stdout))
            self.assertEqual(self.calls(), [], args)

    def test_usage_atomic_pairs_and_conflicts(self) -> None:
        app, stamp = self.pair()
        invalid = [
            ("--app", app), ("--stamp", stamp), ("--app", app, "--stamp"),
            ("--device",), ("--wat",), ("payload.ipa",),
            ("--app", "/tmp/payload.ipa", "--stamp", stamp),
            ("--fast", "--app", app, "--stamp", stamp),
            ("--launch",), ("--autoinput",), ("--diagnostics",), ("--renew",), ("--preflight",),
            ("--app", app, "--app", app, "--stamp", stamp),
            ("--stamp", stamp, "--stamp", stamp, "--app", app),
            ("--device", "A", "--device", "B"), ("--fast", "--fast"),
        ]
        for args in invalid:
            result = self.invoke("--preflight", *args)
            self.assertEqual(result.returncode, 2, (args, result.stdout))
        self.fails(env={"OPENXRAY_INSTALL_APP": app}, code=2)
        self.fails(env={"OPENXRAY_INSTALL_STAMP": stamp}, code=2)
        result = self.invoke("--preflight", "--fast", env={
            "OPENXRAY_INSTALL_APP": app, "OPENXRAY_INSTALL_STAMP": stamp,
        })
        self.assertEqual(result.returncode, 2, result.stdout)

    def test_symlinks_and_input_immutability(self) -> None:
        app, stamp = self.pair()
        before = (hashlib.sha256(Path(app, "Info.plist").read_bytes()).digest(),
                  hashlib.sha256(Path(stamp).read_bytes()).digest())
        self.passes("--app", app, "--stamp", stamp)
        self.assertEqual(before, (hashlib.sha256(Path(app, "Info.plist").read_bytes()).digest(),
                                  hashlib.sha256(Path(stamp).read_bytes()).digest()))
        app_link = self.root / "app-link.app"; app_link.symlink_to(app, target_is_directory=True)
        stamp_link = self.root / "stamp-link"; stamp_link.symlink_to(stamp)
        self.fails("--app", str(app_link), "--stamp", stamp)
        self.fails("--app", app, "--stamp", str(stamp_link))
        parent = self.root / "ancestor-real"; parent.mkdir()
        ancestor_app = parent / "approved.app"; self.make_app(ancestor_app)
        ancestor = self.root / "ancestor-link"; ancestor.symlink_to(parent, target_is_directory=True)
        self.fails("--app", str(ancestor / "approved.app"), "--stamp", stamp)
        stamp_parent = self.root / "stamp-ancestor-real"; stamp_parent.mkdir()
        ancestor_stamp = stamp_parent / "approved.stamp"; self.write_stamp(ancestor_stamp)
        stamp_ancestor = self.root / "stamp-ancestor-link"
        stamp_ancestor.symlink_to(stamp_parent, target_is_directory=True)
        self.fails("--app", app, "--stamp", str(stamp_ancestor / "approved.stamp"))
        (Path(app) / "internal-link").symlink_to("xr_3da")
        self.fails("--app", app, "--stamp", stamp)

    def test_default_and_private_snapshot_symlinks(self) -> None:
        (self.app / "default-internal-link").symlink_to("xr_3da")
        self.fails()
        (self.app / "default-internal-link").unlink()

        real_stamp = self.stamp.with_name("real-device-gate-stamp")
        self.stamp.rename(real_stamp)
        self.stamp.symlink_to(real_stamp)
        self.fails()
        self.stamp.unlink()
        real_stamp.rename(self.stamp)

        real_app = self.app.with_name("real-xr_3da.app")
        self.app.rename(real_app)
        self.app.symlink_to(real_app, target_is_directory=True)
        self.fails()
        self.app.unlink()
        real_app.rename(self.app)

        self.fails(env={"FAKE_APP_SNAPSHOT_SYMLINK": "1"})

    def test_descriptor_snapshot_source_contract(self) -> None:
        source = SCRIPT.read_text()
        required = (
            "os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW",
            "source_parts[-1], os.O_RDONLY | os.O_NOFOLLOW",
            "os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW",
            "0o600,",
            "if not stat.S_ISREG(before.st_mode)",
            "identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)",
            "identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)",
            "if identity_before != identity_after",
            "if (current.st_dev, current.st_ino) == destination_identity",
            "os.unlink(destination_parts[-1], dir_fd=destination_parent)",
            'safe_snapshot_regular_file "$f" "$candidate"',
            'safe_snapshot_regular_file "$GATE_STAMP" "$WORK/gate-stamp"',
        )
        for fragment in required:
            self.assertIn(fragment, source)
        self.assertNotIn('"$COPY" -P "$GATE_STAMP"', source)
        self.assertLess(
            source.index('safe_snapshot_regular_file "$f" "$candidate"'),
            source.index('"$SECURITY" cms -D -i "$candidate"'),
        )

    def test_profile_snapshot_survives_live_source_mutation(self) -> None:
        self.passes(env={"FAKE_MUTATE_LIVE_PROFILE": "1"})
        calls = self.calls()
        security_decodes = [call for call in calls if call.startswith("security cms -D -i ")]
        self.assertGreaterEqual(len(security_decodes), 2)
        self.assertTrue(all("profile-candidate-" in call for call in security_decodes), security_decodes)
        self.assertTrue(all(str(self.profile) not in call for call in security_decodes), security_decodes)
        embeds = [call for call in calls if "embedded.mobileprovision" in call]
        self.assertEqual(len(embeds), 1, calls)
        self.assertIn("profile-candidate-", embeds[0])
        self.assertNotIn(str(self.profile), embeds[0])
        self.assertIn("app_id=WRONG", self.profile.read_text())

    def test_profile_final_and_ancestor_symlinks_fail_without_renew(self) -> None:
        real_profile = self.profile.with_name("profile-source")
        self.profile.rename(real_profile)
        self.profile.symlink_to(real_profile)
        self.fails()
        self.assertFalse(any(call.startswith("security cms") for call in self.calls()), self.calls())
        self.profile.unlink()
        real_profile.rename(self.profile)

        real_directory = self.profile_dir.with_name("Provisioning Profiles real")
        self.profile_dir.rename(real_directory)
        self.profile_dir.symlink_to(real_directory, target_is_directory=True)
        self.fails()
        self.assertFalse(any(call.startswith("security cms") for call in self.calls()), self.calls())

    def test_stamp_schema_and_mutations(self) -> None:
        app, stamp = self.pair()
        values = self.fields(); del values["source_sha256"]
        self.write_stamp(Path(stamp), values)
        output = self.fails("--app", app, "--stamp", stamp)
        self.assertIn("FAIL: Custom gate is missing required field source_sha256", output)
        self.write_stamp(Path(stamp))
        with Path(stamp).open("a") as duplicate:
            duplicate.write(f"source_sha256={SOURCE}\n")
        output = self.fails("--app", app, "--stamp", stamp)
        self.assertIn("FAIL: Custom gate contains duplicate field source_sha256", output)
        for key in self.fields():
            values = self.fields(); del values[key]; self.write_stamp(Path(stamp), values)
            self.fails("--app", app, "--stamp", stamp)
        bad = {"source_sha256": "d" * 64, "app_uuid": "BAD", "bundle_sha256": "d" * 64,
               "openal_provider": "AppleOpenAL", "openal_sha256": "e" * 64,
               "platform": "IOSSIMULATOR", "minos": "15.0"}
        for key, value in bad.items():
            values = self.fields(); values[key] = value; self.write_stamp(Path(stamp), values)
            self.fails("--app", app, "--stamp", stamp)
        self.write_stamp(Path(stamp))
        for key, value in self.fields().items():
            self.write_stamp(Path(stamp))
            with Path(stamp).open("a") as output:
                output.write(f"{key}={value}\n")
            self.fails("--app", app, "--stamp", stamp)

    def test_profile_identity_and_signing_failures(self) -> None:
        app, stamp = self.pair()
        for changed in ({"app_id": "WRONG." + BUNDLE},
                        {"app_id": f"{TEAM}.{BUNDLE}.EXTRA"},
                        {"team": "WRONG"}, {"team": TEAM + "EXTRA"},
                        {"expiry": "Sun Jan 01 00:00:00 GMT 2000"}):
            self.write_profile(**changed)
            self.fails("--app", app, "--stamp", stamp)
        self.write_profile()
        self.fails("--app", app, "--stamp", stamp, env={"FAKE_IDENTITY": "none"})
        self.fails("--app", app, "--stamp", stamp, env={"FAKE_CERT": "none"})
        for env in ({"FAKE_CODESIGN_SIGN": "bad"}, {"FAKE_CODESIGN_VERIFY": "bad"},
                    {"FAKE_SIGN_TEAM": "WRONG"}, {"FAKE_SIGN_APP_ID": "WRONG"}):
            self.fails("--app", app, "--stamp", stamp, env=env)
        for env in (
            {"FAKE_SIGN_APP_KEY": "wrong-key"},
            {"FAKE_SIGN_TEAM_KEY": "wrong-key"},
            {"FAKE_OMIT_APP_ENTITLEMENT": "1"},
            {"FAKE_OMIT_TEAM_ENTITLEMENT": "1"},
            {"FAKE_SIGN_ENT_TEAM": "WRONG"},
        ):
            self.fails("--app", app, "--stamp", stamp, env=env)

    def test_base_bundle_and_missing_profile_do_not_renew(self) -> None:
        app, stamp = self.pair()
        self.make_app(Path(app), "wrong.bundle")
        self.fails("--app", app, "--stamp", stamp)
        self.make_app(Path(app))
        self.profile.unlink()
        self.fails("--app", app, "--stamp", stamp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
