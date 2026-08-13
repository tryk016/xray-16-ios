#!/usr/bin/env python3
"""Mock-only contracts for capture-v2 host tools.

No test in this file invokes a real ``devicectl`` binary, acquires the shared
lock, or touches a phone.  Every external endpoint is a private temporary fake.
"""

from __future__ import annotations

import os as _test_feedback_os
if _test_feedback_os.environ.get("XRAY_FEEDBACK_RAW_EVENT_FD"):
    try:
        import sys as _test_feedback_sys
        import test_feedback_unittest as _test_feedback_unittest
        _test_feedback_unittest.install_from_environment("python::misc/ios/test_capture_v2_host_tools.py", _test_feedback_sys.argv)
    except BaseException:
        pass

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
IOS = ROOT / "misc/ios"
SHOT = IOS / "shot.sh"
INPUT = IOS / "input.sh"
AB = IOS / "lighting_ab_capture.sh"
EVIDENCE = IOS / "lighting_ab_evidence.py"
SESSION = "0123456789abcdef0123456789abcdef"
OLD_REQUEST = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def capture(sequence: int, request: str | None = None) -> dict:
    frame = {1: 10, 2: 20, 3: 30, 4: 40, 10: 100, 13: 200, 16: 300}.get(sequence, sequence * 10)
    continual = {1: 100, 2: 200, 3: 300, 4: 400, 10: 1000, 13: 16000, 16: 31000}.get(sequence, sequence * 100)
    game_time = {10: 10000, 13: 160000, 16: 310000}.get(sequence, sequence * 1000)
    input_value: dict = {"generation": 0, "state": "none", "request_id": None, "key": None,
        "scancode": None, "duration_ms": 0, "accepted": None, "released": None}
    if sequence in (10, 13):
        input_value = {"generation": 8, "state": "released", "request_id": OLD_REQUEST, "key": "escape",
            "scancode": 41, "duration_ms": 100,
            "accepted": {"frame": 50, "continual_ms": 500, "sdl_ms": 100},
            "released": {"frame": 51, "continual_ms": 600, "sdl_ms": 200}}
    if sequence == 16:
        assert request
        input_value = {"generation": 9, "state": "released", "request_id": request, "key": "w",
            "scancode": 26, "duration_ms": 12000,
            "accepted": {"frame": 201, "continual_ms": 16001, "sdl_ms": 100},
            "released": {"frame": 250, "continual_ms": 28001, "sdl_ms": 12100}}
    return {
        "schema": "openxray.capture.v2",
        "capture": {"token": f"{SESSION}:{sequence}", "session": SESSION, "sequence": sequence, "pid": 42,
            "frame": frame, "continual_ms": continual, "width": 1864, "height": 860, "period_ms": 5000,
            "scene": "gameplay", "paused": False},
        "view": {"position": [0.0 if sequence != 13 else 0.1 if sequence != 16 else 5.0, 0.0, 0.0],
            "direction": [0.0, 0.0, 1.0], "fov": 67.5},
        "world": {"level": "zaton", "epoch": 2, "sector": 115},
        "environment": {"game_time_ms": game_time, "day_time_s": 43200.0, "time_factor": 10.0,
            "cycle": "default", "weather": "default", "weather_fx": False, "descriptor0": "12:00:00",
            "descriptor1": "13:00:00", "weight": 0.1 if sequence == 10 else 0.2 if sequence == 13 else 0.3,
            "ambient": [0.1, 0.2, 0.3], "hemi": [0.4, 0.5, 0.6, 0.7], "sun": [0.8, 0.9, 1.0],
            "sun_direction": [0.0, -1.0, 0.0]},
        "input": input_value,
    }


class HostToolTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="capture-v2-host-"))
        self.fakebin = self.work / "bin"
        self.fakebin.mkdir()
        self.log = self.work / "calls.log"
        self.lock = self.work / "lock"
        write_executable(self.lock, """#!/usr/bin/env bash
set -eu
printf '%s %s %s %s\\n' \"$1\" \"${2:-}\" \"${3:-}\" \"${4:-}\" >> \"$MOCK_LOG\"
case \"$1\" in
  status) printf '%s\\n' \"${MOCK_LOCK_STATUS:-{\\\"state\\\":\\\"free\\\"}}\" ;;
  acquire) printf '{\"token\":\"11111111-1111-4111-8111-111111111111\"}\\n' ;;
  renew-token) [ \"${MOCK_RENEW_FAIL:-0}\" = 0 ] ;;
  release-token) : ;;
  *) exit 2 ;;
esac
""")
        write_executable(self.fakebin / "sleep", "#!/usr/bin/env bash\nexit 0\n")
        self.env = os.environ.copy()
        self.env.pop("OPENXRAY_DEVICE_LEASE_TOKEN", None)
        self.env.update({"PATH": f"{self.fakebin}:{self.env['PATH']}", "OPENXRAY_DEVICE_LOCK": str(self.lock),
            "MOCK_LOG": str(self.log), "MOCK_ROOT": str(self.work), "OPENXRAY_EVIDENCE_TOOL": str(EVIDENCE)})

    def tearDown(self) -> None:
        shutil.rmtree(self.work)

    def log_lines(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def assert_one_outer_lease(self) -> None:
        calls = self.log_lines()
        self.assertEqual(sum(line.startswith("acquire") for line in calls), 1, calls)
        self.assertEqual(sum(line.startswith("release-token") for line in calls), 1, calls)

    def assert_exact_renewals(self, count: int) -> None:
        renewals = [line.split() for line in self.log_lines() if line.startswith("renew-token")]
        self.assertEqual(len(renewals), count, renewals)
        self.assertTrue(all(parts == ["renew-token", "openxray", "11111111-1111-4111-8111-111111111111", "8"]
            for parts in renewals), renewals)


class ShotToolContracts(HostToolTestCase):
    def install_xcrun(self, mode: str = "race") -> None:
        script = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["MOCK_ROOT"])
log = pathlib.Path(os.environ["MOCK_LOG"])
args = sys.argv[1:]
source = args[args.index("--source") + 1]
destination = pathlib.Path(args[args.index("--destination") + 1])
log.open("a").write("xcrun " + source + "\n")
state_path = root / "xcrun-state"
state = json.loads(state_path.read_text()) if state_path.exists() else {"meta": 0, "ppm": 0}
session = "0123456789abcdef0123456789abcdef"
def metadata(sequence):
    return {"schema":"openxray.capture.v2","capture":{"token":f"{session}:{sequence}","session":session,"sequence":sequence,"pid":42,"frame":sequence*10,"continual_ms":sequence*100,"width":2,"height":1,"period_ms":5000,"scene":"gameplay","paused":False},"view":{"position":[0,0,0],"direction":[0,0,1],"fov":67.5},"world":{"level":"zaton","epoch":2,"sector":115},"environment":{"game_time_ms":sequence*1000,"day_time_s":1,"time_factor":1,"cycle":"default","weather":"default","weather_fx":False,"descriptor0":"a","descriptor1":"b","weight":0,"ambient":[0,0,0],"hemi":[0,0,0,0],"sun":[0,0,0],"sun_direction":[0,-1,0]},"input":{"generation":0,"state":"none","request_id":None,"key":None,"scancode":None,"duration_ms":0,"accepted":None,"released":None}}
mode = os.environ.get("MOCK_XCRUN_MODE", "race")
if source.endswith("user.ltx"):
    destination.write_text("ios_diagnostics 1\n")
elif source.endswith("xr_shot_meta.txt"):
    state["meta"] += 1
    if mode == "empty":
        destination.write_bytes(b"")
    else:
        sequence = 4 if mode == "expected" else 5 if mode == "overshoot" \
            else [1, 2, 3, 3, 4, 4][min(state["meta"] - 1, 5)]
        destination.write_text(json.dumps(metadata(sequence), separators=(",", ":")))
elif source.endswith("xr_shot.ppm"):
    state["ppm"] += 1
    sequence = 4 if mode == "expected" else 3 if state["ppm"] == 1 else 4
    raw = f"P6\n# openxray-capture-v2 token={session}:{sequence}\n2 1\n255\n".encode() + b"\x01\x02\x03\x04\x05\x06"
    destination.write_bytes(raw[:-1] if mode == "partial" else raw)
else:
    raise SystemExit(2)
state_path.write_text(json.dumps(state))
'''
        write_executable(self.fakebin / "xcrun", script)
        self.env["MOCK_XCRUN_MODE"] = mode

    def test_shot_retries_old_meta_new_ppm_then_publishes_exclusive_evidence(self) -> None:
        self.install_xcrun()
        image, metadata = self.work / "evidence.png", self.work / "evidence.json"
        result = subprocess.run([str(SHOT), "--metadata-out", str(metadata), str(image)], env=self.env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{SESSION}:4", metadata.read_text(encoding="utf-8"))
        self.assertEqual(sum("xr_shot.ppm" in line for line in self.log_lines()), 2)
        self.assert_one_outer_lease()

    def test_expected_token_can_already_be_the_baseline_and_overshoot_fails(self) -> None:
        image, metadata = self.work / "expected.png", self.work / "expected.json"
        self.install_xcrun("expected")
        result = subprocess.run([str(SHOT), "--expect-token", f"{SESSION}:4", "--metadata-out",
            str(metadata), str(image)], env=self.env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_one_outer_lease()

        self.log.unlink(missing_ok=True)
        (self.work / "xcrun-state").unlink(missing_ok=True)
        self.install_xcrun("overshoot")
        result = subprocess.run([str(SHOT), "--expect-token", f"{SESSION}:4",
            str(self.work / "overshoot.png")], env=self.env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("xr_shot.ppm" in line for line in self.log_lines()))
        self.assert_one_outer_lease()

    def test_empty_partial_and_dangling_outputs_fail_without_clobber(self) -> None:
        for mode in ("empty", "partial"):
            with self.subTest(mode=mode):
                self.install_xcrun(mode)
                result = subprocess.run([str(SHOT), str(self.work / f"{mode}.png")], env=self.env,
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assert_one_outer_lease()
                self.log.unlink(missing_ok=True)
        image, metadata = self.work / "dangling.png", self.work / "dangling.json"
        image.symlink_to("missing-image")
        self.log.unlink(missing_ok=True)
        result = subprocess.run([str(SHOT), "--metadata-out", str(metadata), str(image)], env=self.env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(line.startswith("xcrun") for line in self.log_lines()))

        image.unlink()
        metadata.symlink_to("missing-metadata")
        result = subprocess.run([str(SHOT), "--metadata-out", str(metadata), str(image)], env=self.env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(line.startswith("xcrun") for line in self.log_lines()))

    def test_exclusive_metadata_copy_never_replaces_an_existing_file(self) -> None:
        source, destination = self.work / "source.json", self.work / "destination.json"
        source.write_text("first", encoding="utf-8")
        result = subprocess.run([sys.executable, str(EVIDENCE), "copy-file-exclusive", "--source", str(source),
            "--destination", str(destination)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        source.write_text("second", encoding="utf-8")
        result = subprocess.run([sys.executable, str(EVIDENCE), "copy-file-exclusive", "--source", str(source),
            "--destination", str(destination)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(destination.read_text(encoding="utf-8"), "first")


class InputToolContracts(HostToolTestCase):
    def install_xcrun(self) -> None:
        script = r'''#!/usr/bin/env python3
import os, pathlib, sys
root = pathlib.Path(os.environ["MOCK_ROOT"]); log = pathlib.Path(os.environ["MOCK_LOG"]); args = sys.argv[1:]
source = args[args.index("--source") + 1]; destination = args[args.index("--destination") + 1]
log.open("a").write("xcrun " + source + " -> " + destination + "\n")
if "copy to" in " ".join(args):
    payload = pathlib.Path(source).read_text()
    root.joinpath("trigger").write_text(payload)
elif source.endswith("user.ltx"):
    pathlib.Path(destination).write_text("ios_autoinput 1\n")
elif source.endswith("autoinput_ack.txt"):
    request = root.joinpath("trigger").read_text().split()[1]
    pathlib.Path(destination).write_text(request + " accepted\n")
else:
    raise SystemExit(2)
'''
        write_executable(self.fakebin / "xcrun", script)

    def test_explicit_request_id_is_canonical_and_backward_compatible(self) -> None:
        self.install_xcrun()
        request = "12345678-1234-4abc-8def-1234567890ab"
        result = subprocess.run([str(INPUT), "--request-id", request, "w", "12000"], env=self.env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.work.joinpath("trigger").read_text(), f"id {request} w 12000\n")
        self.assert_one_outer_lease()
        self.log.unlink(missing_ok=True)
        invalid = subprocess.run([str(INPUT), "--request-id", request.upper(), "w", "1"], env=self.env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(invalid.returncode, 2)
        self.assertFalse(self.log.exists())


class ABOrchestrationContracts(HostToolTestCase):
    def install_tools(self) -> None:
        shot = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
root = pathlib.Path(os.environ["MOCK_ROOT"]); log = pathlib.Path(os.environ["MOCK_LOG"])
args = sys.argv[1:]; state = root / "shot-count"; count = int(state.read_text()) if state.exists() else 0; count += 1; state.write_text(str(count))
log.open("a").write(f"shot {count} token={os.environ.get('OPENXRAY_DEVICE_LEASE_TOKEN','')} args={' '.join(args)}\n")
if os.environ.get("MOCK_SHOT_MODE") == "wait":
    (root / "shot-waiting").write_text("1"); time.sleep(30)
if os.environ.get("MOCK_SHOT_MODE") == "fail" and count == 2: raise SystemExit(19)
if os.environ.get("MOCK_SHOT_MODE") == "overshoot" and count == 2:
    # A real shot.sh rejects an observed token later than --expect-token. This
    # fake checks that the outer orchestrator supplied the exact N+3 oracle.
    if "--expect-token" not in args or args[args.index("--expect-token") + 1] != "0123456789abcdef0123456789abcdef:13": raise SystemExit(22)
    raise SystemExit(23)
seq = (10, 13, 16)[count - 1]
expected = None
if "--expect-token" in args: expected = args[args.index("--expect-token") + 1]
session = "0123456789abcdef0123456789abcdef"
if count == 1 and expected is not None: raise SystemExit(20)
if count > 1 and expected != f"{session}:{seq}": raise SystemExit(21)
meta = pathlib.Path(args[args.index("--metadata-out") + 1]); image = pathlib.Path(args[-1])
request_path = root / "request"; request = request_path.read_text() if request_path.exists() else None
frames={10:100,13:200,16:300}; continual={10:1000,13:16000,16:31000}; game={10:10000,13:160000,16:310000}
input_value={"generation":0,"state":"none","request_id":None,"key":None,"scancode":None,"duration_ms":0,"accepted":None,"released":None}
if seq in (10, 13): input_value={"generation":8,"state":"released","request_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","key":"escape","scancode":41,"duration_ms":100,"accepted":{"frame":50,"continual_ms":500,"sdl_ms":100},"released":{"frame":51,"continual_ms":600,"sdl_ms":200}}
if seq == 16: input_value={"generation":9,"state":"released","request_id":request,"key":"w","scancode":26,"duration_ms":12000,"accepted":{"frame":201,"continual_ms":16001,"sdl_ms":100},"released":{"frame":250,"continual_ms":28001,"sdl_ms":12100}}
value={"schema":"openxray.capture.v2","capture":{"token":f"{session}:{seq}","session":session,"sequence":seq,"pid":42,"frame":frames[seq],"continual_ms":continual[seq],"width":1864,"height":860,"period_ms":5000,"scene":"gameplay","paused":False},"view":{"position":[0 if seq == 10 else .1 if seq == 13 else 5,0,0],"direction":[0,0,1],"fov":67.5},"world":{"level":"zaton","epoch":2,"sector":115},"environment":{"game_time_ms":game[seq],"day_time_s":43200,"time_factor":10,"cycle":"default","weather":"default","weather_fx":False,"descriptor0":"12:00:00","descriptor1":"13:00:00","weight":.1 if seq == 10 else .2 if seq == 13 else .3,"ambient":[.1,.2,.3],"hemi":[.4,.5,.6,.7],"sun":[.8,.9,1],"sun_direction":[0,-1,0]},"input":input_value}
meta.write_text(json.dumps(value,separators=(",",":")))
ppm=meta.with_suffix(".ppm"); ppm.write_bytes(f"P6\n# openxray-capture-v2 token={session}:{seq}\n1864 860\n255\n".encode()+b"\0"*(1864*860*3))
subprocess.run([sys.executable, os.environ["OPENXRAY_EVIDENCE_TOOL"], "convert-ppm", "--ppm", str(ppm), "--output", str(image), "--no-clobber"], check=True, stdout=subprocess.DEVNULL)
'''
        input_tool = r'''#!/usr/bin/env python3
import os, pathlib, sys
root=pathlib.Path(os.environ["MOCK_ROOT"]); log=pathlib.Path(os.environ["MOCK_LOG"]); args=sys.argv[1:]
log.open("a").write(f"input token={os.environ.get('OPENXRAY_DEVICE_LEASE_TOKEN','')} args={' '.join(args)}\n")
if os.environ.get("MOCK_INPUT_FAIL") == "1": raise SystemExit(18)
root.joinpath("request").write_text(args[1])
'''
        self.shot_tool = self.work / "fake-shot"
        self.input_tool = self.work / "fake-input"
        write_executable(self.shot_tool, shot)
        write_executable(self.input_tool, input_tool)

    def run_ab(self, output: Path, **environment: str) -> subprocess.CompletedProcess[str]:
        self.install_tools()
        env = self.env | {"OPENXRAY_SHOT_TOOL": str(self.shot_tool), "OPENXRAY_INPUT_TOOL": str(self.input_tool)} | environment
        return subprocess.run([str(AB), str(output)], env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False)

    def test_success_uses_exact_renewed_lease_and_exact_n_plus_three(self) -> None:
        output = self.work / "batch"
        result = self.run_ab(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "READY_FOR_DEVICE_VISUAL_COMPARISON\n")
        calls = self.log_lines()
        self.assert_one_outer_lease()
        self.assert_exact_renewals(3)
        self.assertEqual(sum("token=11111111-1111-4111-8111-111111111111" in line
            for line in calls if line.startswith(("shot", "input"))), 4)
        shots = [line for line in calls if line.startswith("shot")]
        self.assertIn(f"--expect-token {SESSION}:13", shots[1])
        self.assertIn(f"--expect-token {SESSION}:16", shots[2])

    def test_failures_release_once_and_busy_lock_never_calls_tools(self) -> None:
        for label, extra, renewals in (("shot", {"MOCK_SHOT_MODE": "fail"}, 1),
                                       ("overshoot", {"MOCK_SHOT_MODE": "overshoot"}, 1),
                                       ("input", {"MOCK_INPUT_FAIL": "1"}, 2),
                                       ("renew", {"MOCK_RENEW_FAIL": "1"}, 1)):
            with self.subTest(label=label):
                self.log.unlink(missing_ok=True)
                self.work.joinpath("shot-count").unlink(missing_ok=True)
                self.work.joinpath("request").unlink(missing_ok=True)
                result = self.run_ab(self.work / label, **extra)
                self.assertNotEqual(result.returncode, 0)
                self.assert_one_outer_lease()
                self.assert_exact_renewals(renewals)
                if label == "renew":
                    calls = self.log_lines()
                    self.assertEqual(sum(line.startswith("shot") for line in calls), 1, calls)
                    self.assertFalse(any(line.startswith("input") for line in calls), calls)
        self.log.unlink(missing_ok=True)
        busy_env = self.env | {"MOCK_LOCK_STATUS": '{"state":"busy","owner":"opengothic"}',
            "OPENXRAY_SHOT_TOOL": str(self.work / "never-shot"), "OPENXRAY_INPUT_TOOL": str(self.work / "never-input")}
        result = subprocess.run([str(AB), str(self.work / "busy")], env=busy_env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(result.returncode, 3)
        calls = self.log_lines()
        self.assertFalse(any(line.startswith(("shot", "input", "xcrun")) for line in calls), calls)
        self.assertFalse(any(line.startswith("release-token") for line in calls), calls)

    def test_interrupts_release_exactly_once(self) -> None:
        self.install_tools()
        env = self.env | {"OPENXRAY_SHOT_TOOL": str(self.shot_tool), "OPENXRAY_INPUT_TOOL": str(self.input_tool),
            "MOCK_SHOT_MODE": "wait"}
        for name, signum in (("INT", signal.SIGINT), ("TERM", signal.SIGTERM)):
            with self.subTest(signal=name):
                self.log.unlink(missing_ok=True)
                self.work.joinpath("shot-waiting").unlink(missing_ok=True)
                process = subprocess.Popen([str(AB), str(self.work / name.lower())], env=env, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                deadline = time.monotonic() + 5
                while not (self.work / "shot-waiting").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((self.work / "shot-waiting").exists())
                os.killpg(process.pid, signum)
                process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()
                self.assert_one_outer_lease()


if __name__ == "__main__":
    unittest.main(verbosity=2)
