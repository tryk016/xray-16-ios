#!/usr/bin/env python3
"""Mocked regression coverage for the retail Simulator isolation workflow."""

from __future__ import annotations

import hashlib
from pathlib import Path
import os
import shutil
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
import importlib.util
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "misc/ios/retail_simulator.sh"
GUARD = REPO_ROOT / "misc/ios/retail_simulator_guard.py"
OPENAL_CONTRACT = REPO_ROOT / "misc/ios/openal_provider_contract.py"
GUARD_SPEC = importlib.util.spec_from_file_location("retail_simulator_guard", GUARD)
assert GUARD_SPEC is not None and GUARD_SPEC.loader is not None
GUARD_MODULE = importlib.util.module_from_spec(GUARD_SPEC)
GUARD_SPEC.loader.exec_module(GUARD_MODULE)
REQUIRED_ARCHIVES = (
    *(f"resources/resources.db{index}" for index in range(5)),
    *(f"levels/levels.db{index}" for index in range(2)),
)
LARGE_FILES = (
    *REQUIRED_ARCHIVES,
    "localization/base_sounds.db",
    "localization/xefis_movies.db",
    "_appdata_/cdb_cache/zaton/objspace.bin",
    "_appdata_/savedgames/save.scop",
    "_appdata_/savedgames/other.scop",
)
STAMP_PATHS = (
    "build/ios-engine-iphoneos/.ios_device_gate_ok",
    "build/ios-engine-iphoneos/.ios_full_gate_ok",
    "build/ios-engine-iphoneos/.ios_cmake_inputs.sha256",
    "build/ios-engine-fastdevice-iphoneos/.ios_fast_device_gate_ok",
    "build/ios-engine-fastdevice-iphoneos/.ios_cmake_inputs.sha256",
)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    result.update(path.read_bytes())
    return result.hexdigest()


def tree_bytes(root: Path, excluded_top: tuple[str, ...] = ()) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root)
        top = relative.parts[0]
        if top in excluded_top or any(
            pattern.endswith("*") and top.startswith(pattern[:-1]) for pattern in excluded_top
        ):
            continue
        result[relative.as_posix()] = path.read_bytes()
    return result


class RetailSimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="openxray-retail-sim-", dir="/tmp")
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        (self.repo / "misc/ios").mkdir(parents=True)
        shutil.copy2(RUNNER, self.repo / "misc/ios/retail_simulator.sh")
        shutil.copy2(GUARD, self.repo / "misc/ios/retail_simulator_guard.py")
        shutil.copy2(OPENAL_CONTRACT, self.repo / "misc/ios/openal_provider_contract.py")
        (self.repo / "cmake/toolchains").mkdir(parents=True)
        (self.repo / "cmake/toolchains/ios.toolchain.cmake").write_text("# fixture\n")
        for path in (
            "bin/aarch64/Release/xr_3da.app/device", "bin/aarch64/FastDevice/xr_3da.app/device",
            *STAMP_PATHS, "build/ios-engine-iphoneos/object.o",
            "build/ios-engine-fastdevice-iphoneos/object.o", "build/ios-engine-iphonesimulator/cache",
            "build/ios-prefix-iphonesimulator/lib/dependency.a",
        ):
            candidate = self.repo / path
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text("fixture\n")
        prefix = self.repo / "build/ios-prefix-iphonesimulator"
        (prefix / "lib/libopenal.a").write_bytes(b"fixture-openal")
        for header in ("al.h", "alc.h", "alext.h"):
            target = prefix / "include/AL" / header
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fixture\n")
        pkgconfig = prefix / "lib/pkgconfig"
        pkgconfig.mkdir(parents=True)
        (pkgconfig / "fixture.pc").write_text(f"prefix={prefix}\nlibdir={prefix}/lib\n")
        self.backup = self.root / "backup"
        self.backup.mkdir()
        self.manifest = self.root / "backup.manifest"
        self.manifest.mkdir()
        for index, relative in enumerate(REQUIRED_ARCHIVES):
            self._retail_file(relative, f"archive-{index}".encode())
        self._retail_file("localization/xenglish.db", b"language")
        self._retail_file("localization/base_sounds.db", b"base-sounds")
        self._retail_file("localization/xefis_movies.db", b"xefis-movies")
        self._retail_file("patches/xpatch_02.db", b"patch")
        self._retail_file("_appdata_/cdb_cache/zaton/objspace.bin", b"cdb-cache")
        self._retail_file("_appdata_/savedgames/save.scop", b"save")
        self._retail_file("_appdata_/savedgames/other.scop", b"other-save")
        self._retail_file("_appdata_/user.ltx", b"must-not-copy")
        self._write_retail_manifests()
        self.work_base = self.root / "handoff"
        self.work_base.mkdir()
        self.simulator_uuid = "00000000-0000-0000-0000-000000000001"
        self.mock_home = self.root / "home"
        self.simulator_application_root = (
            self.mock_home / "Library/Developer/CoreSimulator/Devices" / self.simulator_uuid
            / "data/Containers/Data/Application"
        )
        self.simulator_application_root.mkdir(parents=True)
        self.sim_data = self.simulator_application_root / "11111111-1111-1111-1111-111111111111"
        self.commands = self.root / "commands.log"
        self.mocks = self.root / "mocks"
        self.mocks.mkdir()
        self._write_navigation_controller()
        self._write_mocks()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _retail_file(self, relative: str, content: bytes) -> None:
        target = self.backup / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def _write_retail_manifests(self) -> None:
        paths = sorted(path for path in self.backup.rglob("*") if path.is_file())
        with (self.manifest / "files.tsv").open("w") as output:
            output.write("bytes\tpath\n")
            for path in paths:
                output.write(f"{path.stat().st_size}\t{path.relative_to(self.backup).as_posix()}\n")
        required = [self.backup / relative for relative in REQUIRED_ARCHIVES]
        with (self.manifest / "required-archives.tsv").open("w") as output:
            output.write("bytes\tsha256\tpath\tstatus\n")
            for path in required:
                output.write(f"{path.stat().st_size}\t{digest(path)}\t{path.relative_to(self.backup).as_posix()}\tOK\n")
        with (self.manifest / "large-files-sha256.tsv").open("w") as output:
            output.write("sha256\tbytes\tpath\n")
            for relative in LARGE_FILES:
                path = self.backup / relative
                output.write(f"{digest(path)}\t{path.stat().st_size}\t{relative}\n")

    def _mock(self, name: str, content: str) -> None:
        path = self.mocks / name
        path.write_text("#!/usr/bin/env python3\n" + content, encoding="utf-8")
        path.chmod(0o755)

    def _write_navigation_controller(self) -> None:
        """A deterministic source-snapshot controller, never a Simulator helper.

        The real controller has its own exhaustive semantic tests.  This fixture
        exercises only runner wiring, ordering and fail-closed finalization.
        """
        controller = self.repo / "misc/ios/simulator_ui_navigation.py"
        controller.write_text(textwrap.dedent("""
            import os, pathlib, sys
            args=sys.argv[1:]
            log_file=pathlib.Path(os.environ['MOCK_LOG'])
            log_file.open('a').write('navigation-'+args[0]+' '+sys.argv[0]+' '+' '.join(args[1:])+'\\n')
            def value(name): return args[args.index(name)+1]
            if args[0] == 'run':
                if os.environ.get('MOCK_NAV_FAILURE'):
                    pathlib.Path(value('--log')).open('a').write('navigation fixture failure\\n')
                    sys.exit(7)
                documents=pathlib.Path(value('--documents'))
                if 'ios_autoinput 1\\n' not in (documents/'_appdata_/user.ltx').read_text(): sys.exit(8)
                log=pathlib.Path(value('--log'))
                pid=value('--expected-pid')
                states=('world','inventory','world','pda_tasks','other','world','pda_tasks','world')
                with log.open('a') as output:
                    for index,state in enumerate(states, 1):
                        output.write(f'* iOS UI state v1 pid={pid} seq={index} frame={index*10} state={state}\\n')
                payload=log.read_bytes()
                pathlib.Path(value('--snapshot')).write_bytes(payload)
                pathlib.Path(value('--report')).write_text(f'{log.stat().st_ino}\\n{pid}\\n')
            elif args[0] == 'finalize':
                if os.environ.get('MOCK_NAV_FINALIZE_FAILURE'): sys.exit(9)
                log=pathlib.Path(value('--log'))
                snapshot=pathlib.Path(value('--snapshot'))
                expected_inode=int(pathlib.Path(value('--pre-report')).read_text().splitlines()[0])
                payload=log.read_bytes()
                if log.stat().st_ino != expected_inode or not payload.startswith(snapshot.read_bytes()): sys.exit(10)
                if b'FATAL:' in payload or payload.count(b'* iOS UI state v1 ') != 8: sys.exit(11)
                pathlib.Path(value('--final-report')).write_text('PASS\\n')
            else: sys.exit(12)
        """), encoding="utf-8")

    def _write_mocks(self) -> None:
        self._mock("rsync", textwrap.dedent("""
            import os, shutil, sys
            log=os.environ['MOCK_LOG']; open(log,'a').write('rsync '+ ' '.join(sys.argv[1:])+'\\n')
            source, destination=sys.argv[-2:]
            shutil.copytree(source.rstrip('/'), destination.rstrip('/'), dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('.git','.Codex','build*','bin'))
        """))
        self._mock("cmake", textwrap.dedent("""
            import os, pathlib, sys
            log=os.environ['MOCK_LOG']; open(log,'a').write('cmake '+ ' '.join(sys.argv[1:])+'\\n')
            args=sys.argv[1:]
            if '--build' in args:
                build=pathlib.Path(args[args.index('--build')+1]); source=pathlib.Path((build/'source.txt').read_text())
                app=source/'bin/aarch64/Release/xr_3da.app'; app.mkdir(parents=True); (app/'xr_3da').write_text('binary'); (app/'xr_3da').chmod(0o755)
                (app/'Info.plist').write_bytes(b'<?xml version="1.0"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict><key>CFBundleIdentifier</key><string>io.github.tryk016.openxray</string></dict></plist>')
                if os.environ.get('MOCK_MUTATE_PROTECTED'):
                    pathlib.Path(os.environ['MOCK_PROTECTED_FILE']).write_text('mutated')
            else:
                source=pathlib.Path(args[args.index('-S')+1]); build=pathlib.Path(args[args.index('-B')+1]); build.mkdir(parents=True); (build/'source.txt').write_text(str(source))
                prefix=[arg.split('=',1)[1] for arg in args if arg.startswith('-DCMAKE_PREFIX_PATH=')][0]
                find_root=[arg.split('=',1)[1] for arg in args if arg.startswith('-DCMAKE_FIND_ROOT_PATH=')][0]
                (build/'OpenXRay.xcodeproj').mkdir()
                openal_cache={'OPENAL_INCLUDE_DIR':prefix+'/include','OPENAL_LIBRARY':prefix+'/lib/libopenal.a','XRAY_OPENAL_PREFIX':prefix,'XRAY_OPENAL_LIBRARY':prefix+'/lib/libopenal.a','XRAY_OPENAL_INCLUDE_ROOT':prefix+'/include','XRAY_OPENAL_PROVIDER':'OpenALSoft-1.25.2-static'}
                (build/'CMakeCache.txt').write_text(f'CMAKE_HOME_DIRECTORY:INTERNAL={source}\\nCMAKE_PREFIX_PATH:UNINITIALIZED={prefix}\\nCMAKE_FIND_ROOT_PATH:UNINITIALIZED={find_root}\\n')
                cache=build/'CMakeCache.txt'; cache.write_text(cache.read_text()+''.join(f'{key}:STRING={value}{chr(10)}' for key,value in openal_cache.items()))
        """))
        self._mock("xcrun", textwrap.dedent("""
            import os, pathlib, subprocess, sys
            log=os.environ['MOCK_LOG']; args=sys.argv[1:]; open(log,'a').write('xcrun '+ ' '.join(args)+'\\n')
            if args[:2] == ['vtool','-show-build']:
                mode=os.environ.get('MOCK_VTOOL_MODE','normal')
                if mode == 'mixed': print('cmd LC_BUILD_VERSION\\nplatform IOSSIMULATOR\\nminos 16.4\\ncmd LC_BUILD_VERSION\\nplatform IOS\\nminos 16.4')
                elif mode == 'platform': print('cmd LC_BUILD_VERSION\\nplatform IOS\\nminos 16.4')
                else: print('cmd LC_BUILD_VERSION\\nplatform IOSSIMULATOR\\nminos 16.4')
                sys.exit(0)
            if args[:2] != ['simctl','create'] and args[:1] != ['simctl']:
                sys.exit(1)
            if args[1] == 'create': print('00000000-0000-0000-0000-000000000001')
            elif args[1] == 'get_app_container':
                mode=os.environ.get('MOCK_CONTAINER_MODE','normal')
                root=pathlib.Path(os.environ['MOCK_REPO'] if mode == 'repo' else os.environ['MOCK_OUTSIDE'] if mode == 'outside' else os.environ['MOCK_SIM_DATA'])
                root.mkdir(parents=True, exist_ok=True); print(root)
            elif args[1] == 'launch':
                if args[-1] == 'com.apple.mobilesafari':
                    state_file=pathlib.Path(os.environ['MOCK_APP_PID_STATE'])
                    if not state_file.exists(): sys.exit(2)
                    initial_pid=int(state_file.read_text())
                    root=pathlib.Path(os.environ['MOCK_SIM_DATA'])/'Documents'
                    mode=os.environ.get('MOCK_FOREGROUND_LOG_MODE','normal')
                    anchor_seq=int(os.environ.get('MOCK_INITIAL_LIFECYCLE_SEQ','1'))
                    deactivate_seq=anchor_seq+1
                    if mode in ('missing', 'missing-deactivate'): suffix=''
                    elif mode in ('reordered', 'activate-first'): suffix=f'* iOS lifecycle v1 pid={initial_pid} seq={deactivate_seq} event=activate\\n'
                    elif mode in ('duplicate', 'duplicate-deactivate'): suffix=(f'* iOS lifecycle v1 pid={initial_pid} seq={deactivate_seq} event=deactivate\\n'
                                                                           f'* iOS lifecycle v1 pid={initial_pid} seq={deactivate_seq + 1} event=deactivate\\n')
                    elif mode == 'bad-marker': suffix=f'* iOS lifecycle v1 pid={initial_pid} seq={deactivate_seq} event=deactivate extra\\n'
                    elif mode == 'zero-seq': suffix=f'* iOS lifecycle v1 pid={initial_pid} seq=0 event=deactivate\\n'
                    elif mode == 'nonincreasing': suffix=f'* iOS lifecycle v1 pid={initial_pid} seq={anchor_seq} event=deactivate\\n'
                    elif mode == 'wrong-pid': suffix=f'* iOS lifecycle v1 pid={initial_pid + 1} seq={deactivate_seq} event=deactivate\\n'
                    elif mode == 'noscene': suffix=f'NoSceneLifecycleAdoption\\n* iOS lifecycle v1 pid={initial_pid} seq={deactivate_seq} event=deactivate\\n'
                    else: suffix=f'* iOS lifecycle v1 pid={initial_pid} seq={deactivate_seq} event=deactivate\\n'
                    if mode == 'delayed':
                        subprocess.Popen([sys.executable, '-c',
                            'import pathlib,sys,time; time.sleep(0.03); pathlib.Path(sys.argv[1]).open("a").write(sys.argv[2])',
                            str(root/'xr_boot.log'), suffix], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        (root/'xr_boot.log').open('a').write(suffix)
                    print('com.apple.mobilesafari: 4242')
                    sys.exit(0)
                state_file=pathlib.Path(os.environ['MOCK_APP_PID_STATE'])
                if state_file.exists():
                    initial_pid=int(state_file.read_text())
                    if os.environ.get('MOCK_PID_REPLACEMENT'):
                        replacement=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)'])
                        initial_pid=replacement.pid
                    root=pathlib.Path(os.environ['MOCK_SIM_DATA'])/'Documents'
                    mode=os.environ.get('MOCK_FOREGROUND_LOG_MODE','normal')
                    anchor_seq=int(os.environ.get('MOCK_INITIAL_LIFECYCLE_SEQ','1'))
                    activate_seq=anchor_seq+2
                    if mode == 'missing-activate': suffix=''
                    elif mode == 'duplicate-activate': suffix=(f'* iOS lifecycle v1 pid={initial_pid} seq={activate_seq} event=activate\\n'
                                                               f'* iOS lifecycle v1 pid={initial_pid} seq={activate_seq + 1} event=activate\\n')
                    elif mode == 'extra': suffix=(f'* iOS lifecycle v1 pid={initial_pid} seq={activate_seq} event=activate\\n'
                                                  f'* iOS lifecycle v1 pid={initial_pid} seq={activate_seq + 1} event=deactivate\\n')
                    elif mode == 'second-engine': suffix=f'Starting engine...\\n* iOS lifecycle v1 pid={initial_pid} seq={activate_seq} event=activate\\n'
                    elif mode == 'second-menu': suffix=(f'* iOS main menu frame v1 pid={initial_pid} frame=2\\n'
                                                        f'* iOS lifecycle v1 pid={initial_pid} seq={activate_seq} event=activate\\n')
                    elif mode == 'log-truncate':
                        (root/'xr_boot.log').write_text('truncated during foreground recovery\\n')
                        suffix=''
                    elif mode == 'log-rotate':
                        old=root/'xr_boot.before-foreground.log'
                        (root/'xr_boot.log').rename(old)
                        (root/'xr_boot.log').write_text('replacement during foreground recovery\\n')
                        suffix=''
                    else: suffix=f'* iOS lifecycle v1 pid={initial_pid} seq={activate_seq} event=activate\\n'
                    if mode == 'delayed':
                        subprocess.Popen([sys.executable, '-c',
                            'import pathlib,sys,time; time.sleep(0.03); pathlib.Path(sys.argv[1]).open("a").write(sys.argv[2])',
                            str(root/'xr_boot.log'), suffix], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        (root/'xr_boot.log').open('a').write(suffix)
                    print(f'{args[-1]}: {initial_pid}')
                    sys.exit(0)
                if not os.environ.get('MOCK_NO_BOOT_LOG'):
                    root=pathlib.Path(os.environ['MOCK_SIM_DATA'])/'Documents'; root.mkdir(parents=True, exist_ok=True)
                    mode=os.environ.get('MOCK_BOOT_LOG_MODE','normal')
                    autoload=os.environ.get('MOCK_AUTOLOAD')
                    name=os.environ.get('MOCK_AUTOLOAD_NAME','save')
                    if autoload:
                        prefix=('FS: 789 files cached 12 archives, 21936Kb memory used.\\n'
                            +r'-----loading \\private\\tmp\\Documents\\gamedata\\configs\\system.ltx'+'\\n'
                            +'Executing config-script "user.ltx"...\\n'
                            +r'[\\private\\tmp\\Documents\\_appdata_\\user.ltx] successfully loaded.'+'\\n'
                            +'Starting engine...\\n')
                        gameplay=(f"* Game {name} is successfully loaded from file '\\\\private\\\\tmp\\\\Documents\\\\_appdata_\\\\savedgames\\\\{name}.scop' (0.001s)\\n"
                            +'* client : connection accepted - <All Ok>\\n'
                            +r'* Loading HOM: \\private\\tmp\\Documents\\gamedata\\levels\\zaton\\level.hom'+'\\n'
                            +'* End of synchronization A[1] R[1]\\n'
                            +'* iOS memory after_load: resident_current=1 K\\n')
                        variant=os.environ.get('MOCK_AUTOLOAD_LOG_MODE','normal')
                        if variant == 'missing-user': text=prefix.replace('Executing config-script "user.ltx"...\\n'+r'[\\private\\tmp\\Documents\\_appdata_\\user.ltx] successfully loaded.'+'\\n','')+gameplay
                        elif variant == 'misordered': text=prefix.replace('Starting engine...\\n','')+gameplay+'Starting engine...\\n'
                        elif variant == 'mismatch': text=prefix+gameplay.replace(f'Game {name}', 'Game wrong')
                        elif variant == 'new-game': text=prefix+'* Creating new game...\\n'+gameplay
                        elif variant == 'fatal': text=prefix+'FATAL: fixture failure\\n'+gameplay
                        elif variant == 'missing-accepted': text=prefix+gameplay.replace('* client : connection accepted - <All Ok>\\n','')
                        elif variant == 'missing-hom': text=prefix+gameplay.replace(r'* Loading HOM: \\private\\tmp\\Documents\\gamedata\\levels\\zaton\\level.hom'+'\\n','')
                        elif variant == 'misordered-hom': text=prefix+gameplay.replace(r'* Loading HOM: \\private\\tmp\\Documents\\gamedata\\levels\\zaton\\level.hom'+'\\n','').replace('* End of synchronization A[1] R[1]\\n', '* End of synchronization A[1] R[1]\\n'+r'* Loading HOM: \\private\\tmp\\Documents\\gamedata\\levels\\zaton\\level.hom'+'\\n')
                        elif variant == 'missing-save': text=prefix+gameplay.replace(f"* Game {name} is successfully loaded from file ", "* omitted saved-game success from file ")
                        elif variant == 'missing-sync': text=prefix+gameplay.replace('* End of synchronization A[1] R[1]\\n','')
                        elif variant == 'multiple-levels': text=prefix+gameplay.replace('* End of synchronization', r'* Loading HOM: \\private\\tmp\\Documents\\gamedata\\levels\\jupiter\\level.hom'+'\\n* End of synchronization')
                        elif variant == 'missing-memory': text=prefix+gameplay.replace('* iOS memory after_load: resident_current=1 K\\n','')
                        else: text=prefix+gameplay
                    elif mode == 'missing': text='ERROR: system.ltx not found\\n'
                    elif mode == 'archives11': text='FS: 789 files cached 11 archives, 1Kb memory used.\\n'+r'-----loading \\private\\tmp\\Documents\\gamedata\\configs\\system.ltx'+'\\nStarting engine...\\n'
                    elif mode in ('noscene', 'sigtrap'): text=('FS: 789 files cached 12 archives, 21936Kb memory used.\\n'
                                                     +r'-----loading \\private\\tmp\\Documents\\gamedata\\configs\\system.ltx'+'\\n'
                                                     +'Starting engine...\\n'
                                                     +('NoSceneLifecycleAdoption\\n' if mode == 'noscene' else 'SIGTRAP\\n'))
                    else: text='FS: 789 files cached 12 archives, 21936Kb memory used.\\n'+r'-----loading \\private\\tmp\\Documents\\gamedata\\configs\\system.ltx'+'\\nERROR: unrelated renderer probe failed\\nStarting engine...\\n'
                    provider='iOS OpenAL provider v1 vendor="OpenAL Community" renderer="OpenAL Soft" version="1.1 ALSOFT 1.25.2" extension=1 pause_proc=1 resume_proc=1\\n'
                    provider_mutation=os.environ.get('MOCK_OPENAL_LOG_MUTATION','')
                    if provider_mutation == 'missing': provider=''
                    elif provider_mutation == 'duplicate': provider += provider
                    elif provider_mutation == 'bad-proc': provider=provider.replace('pause_proc=1','pause_proc=0')
                    text += provider
                    (root/'xr_boot.log').write_text(text)
                    (root/'fsgame.ltx').write_text('engine-owned')
                    (root/'gamedata/configs').mkdir(parents=True, exist_ok=True)
                    (root/'gamedata/configs/system.ltx').write_text('engine-owned')
                    (root/'_appdata_').mkdir(exist_ok=True)
                    (root/'_appdata_/runtime.log').write_text('engine-owned')
                staged=os.environ.get('MOCK_STAGED_FILE')
                if staged:
                    if os.environ.get('MOCK_STAGED_ACTION') == 'delete': pathlib.Path(staged).unlink()
                    elif os.environ.get('MOCK_STAGED_ACTION') == 'same-size-corrupt':
                        payload=pathlib.Path(staged).read_bytes()
                        pathlib.Path(staged).write_bytes(bytes([payload[0] ^ 0x01])+payload[1:])
                    else: pathlib.Path(staged).write_bytes(b'mutated-during-launch')
                death_trigger=os.environ.get('MOCK_DEFERRED_DEATH_TRIGGER','')
                late_log_trigger=os.environ.get('MOCK_LATE_LOG_TRIGGER','')
                child_code=(
                    "import os,pathlib,time; death=os.environ.get('MOCK_DEFERRED_DEATH_TRIGGER',''); "
                    "late_trigger=os.environ.get('MOCK_LATE_LOG_TRIGGER',''); trigger=death or late_trigger; "
                    "(exec('while not os.path.exists(trigger): time.sleep(0.001)') if trigger else time.sleep(float(os.environ.get('MOCK_APP_LIFETIME','2')))); "
                    "late=os.environ.get('MOCK_LATE_LOG_FILE',''); payload=os.environ.get('MOCK_LATE_LOG_PAYLOAD',''); "
                    "(open(late,'a').write(payload) if late else None); "
                    "time.sleep(0.02 if death else float(os.environ.get('MOCK_APP_LIFETIME','2')) if late_trigger else 0)"
                )
                child=subprocess.Popen(
                    [sys.executable, '-c', child_code], env=os.environ.copy(),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                )
                state_file.write_text(str(child.pid))
                if not autoload and not os.environ.get('MOCK_NO_BOOT_LOG'):
                    marker_mode=os.environ.get('MOCK_MENU_MARKER_MODE','normal')
                    lifecycle_seq=int(os.environ.get('MOCK_INITIAL_LIFECYCLE_SEQ','1'))
                    lifecycle=f'* iOS lifecycle v1 pid={child.pid} seq={lifecycle_seq} event=activate\\n'
                    marker=(f'* iOS main menu frame v1 pid={child.pid} frame=1\\n'
                            if marker_mode == 'normal' else
                            (f'* iOS main menu frame v1 pid={child.pid} frame=1\\n'
                             f'* iOS main menu frame v1 pid={child.pid} frame=2\\n') if marker_mode == 'duplicate' else
                            f'* iOS main menu frame v1 pid=0 frame=1\\n' if marker_mode == 'zero-pid' else
                            f'* iOS main menu frame v1 pid={child.pid} frame=-1\\n' if marker_mode == 'negative-frame' else
                            f'* iOS main menu frame v1 pid={child.pid} frame=1 extra\\n' if marker_mode == 'malformed' else '')
                    (pathlib.Path(os.environ['MOCK_SIM_DATA'])/'Documents/xr_boot.log').open('a').write(lifecycle+marker)
                if os.environ.get('MOCK_LAUNCH_CRASH'): child.wait()
                if os.environ.get('MOCK_LAUNCH_PID_FILE'): pathlib.Path(os.environ['MOCK_LAUNCH_PID_FILE']).write_text(str(child.pid))
                output=os.environ.get('MOCK_LAUNCH_OUTPUT', f'{args[-1]}: {child.pid}')
                print(output)
            elif args[1] == 'io' and args[3] == 'screenshot':
                empty = (os.environ.get('MOCK_EMPTY_SCREENSHOT')
                         or (os.environ.get('MOCK_EMPTY_RECOVERY_SCREENSHOT')
                             and 'after-foreground' in args[4]))
                pathlib.Path(args[4]).write_bytes(b'' if empty else b'\\x89PNG\\r\\n\\x1a\\nfixture')
                trigger=os.environ.get('MOCK_DEFERRED_DEATH_TRIGGER') or os.environ.get('MOCK_LATE_LOG_TRIGGER')
                if trigger: pathlib.Path(trigger).write_text('screenshot captured')
                if os.environ.get('MOCK_CRASH_DURING_SCREENSHOT'):
                    import time
                    time.sleep(0.05)
            elif args[1] == 'terminate':
                mode=os.environ.get('MOCK_POST_LAUNCH_LOG_MODE','')
                if mode:
                    pid=int(pathlib.Path(os.environ['MOCK_LAUNCH_PID_FILE']).read_text())
                    os.kill(pid, 0)
                    target=pathlib.Path(os.environ['MOCK_POST_LAUNCH_LOG_FILE'])
                    if mode == 'append-fatal': target.open('a').write('FATAL: post-launch fixture failure\\n')
                    elif mode == 'truncate': target.write_text('truncated after launch-proof\\n')
                    elif mode == 'rotate':
                        target.rename(target.with_suffix('.rotated'))
                        target.write_text('replacement after launch-proof\\n')
                    elif mode == 'symlink':
                        old=target.with_name('old.log')
                        target.rename(old)
                        target.symlink_to(old.name)
                    elif mode == 'append-ui-marker': target.open('a').write('* iOS UI state v1 pid=1 seq=8 frame=80 state=world\\n')
                    else: sys.exit(2)
                if os.environ.get('MOCK_TERMINATE_FAILURE'): sys.exit(1)
            elif args[1] == 'delete' and os.environ.get('MOCK_DELETE_FAILURE'): sys.exit(1)
            sys.exit(0)
        """))
        self._mock("lipo", textwrap.dedent("""
            import os, sys
            open(os.environ['MOCK_LOG'], 'a').write('lipo '+ ' '.join(sys.argv[1:])+'\\n')
            if sys.argv[1:2] != ['-archs'] or len(sys.argv) != 3: sys.exit(2)
            print(os.environ.get('MOCK_LIPO_ARCHS','arm64'))
        """))
        self._mock("xcodebuild", textwrap.dedent("""
            import os, pathlib, sys
            open(os.environ['MOCK_LOG'], 'a').write('xcodebuild '+ ' '.join(sys.argv[1:])+'\\n')
            args=sys.argv[1:]; project=pathlib.Path(args[args.index('-project')+1]); prefix=project.parent.parent/'ios-prefix-iphonesimulator'
            target=args[args.index('-target')+1]
            if args[-1] != '-showBuildSettings' or target not in ('xrSound', 'xrEngine', 'xr_3da'): sys.exit(2)
            if target in ('xrSound', 'xrEngine'):
                print(f'    HEADER_SEARCH_PATHS = "{prefix}/include" "{prefix}/include/AL"')
                print('    OTHER_CFLAGS = -DAL_LIBTYPE_STATIC')
                print('    OTHER_CPLUSPLUSFLAGS = -DAL_LIBTYPE_STATIC')
                print('    GCC_PREPROCESSOR_DEFINITIONS = AL_LIBTYPE_STATIC=1')
            else:
                print(f'    OTHER_LDFLAGS = {prefix}/lib/libopenal.a -framework AudioToolbox -framework CoreFoundation -framework CoreAudio')
        """))
        self._mock("ar", textwrap.dedent("""
            import os, sys
            open(os.environ['MOCK_LOG'], 'a').write('ar '+ ' '.join(sys.argv[1:])+'\\n')
            if sys.argv[1] == '-t' and len(sys.argv) == 3: print('__.SYMDEF SORTED\\na.o\\nb.o')
            elif sys.argv[1] == '-p': sys.stdout.buffer.write(b'fixture-member')
            else: sys.exit(2)
        """))
        self._mock("nm", textwrap.dedent("""
            import os, pathlib, sys
            open(os.environ['MOCK_LOG'], 'a').write('nm '+ ' '.join(sys.argv[1:])+'\\n')
            args=sys.argv[1:]
            if len(args) == 1:
                name=pathlib.Path(args[0]).name
                symbol_type='T' if name == 'libopenal.a' else 't' if name == 'xr_3da' else None
                if symbol_type is None: sys.exit(2)
                for symbol in ('alcDevicePauseSOFT', 'alcDeviceResumeSOFT', 'alGetString', 'alcGetProcAddress'):
                    print(f'00000000 {symbol_type} _{symbol}')
            elif len(args) == 2 and args[0] == '-u' and pathlib.Path(args[1]).name in ('libopenal.a', 'xr_3da'):
                pass
            else: sys.exit(2)
        """))
        self._mock("otool", textwrap.dedent("""
            import os, pathlib, sys
            open(os.environ['MOCK_LOG'], 'a').write('otool '+ ' '.join(sys.argv[1:])+'\\n')
            if len(sys.argv) != 3: sys.exit(2)
            mode, subject = sys.argv[1:]
            path = pathlib.Path(subject)
            if not path.is_absolute(): sys.exit(2)
            if mode == '-l':
                if path.name != 'libopenal.a': sys.exit(2)
                print(f'Archive : {path}')
                for member in ('a.o', 'b.o'):
                    print(f'{path}({member}):')
                    print('Load command 0')
                    print('      cmd LC_BUILD_VERSION')
                    print('  cmdsize 32')
                    print(' platform 7')
                    print('    minos 16.4')
                sys.exit(0)
            if mode == '-L':
                if path.name != 'xr_3da': sys.exit(2)
                print(f'{path}:')
                print('\\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1351.0.0)')
                print('\\t/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox (compatibility version 1.0.0, current version 1.0.0)')
                print('\\t/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation (compatibility version 1.0.0, current version 1.0.0)')
                print('\\t/System/Library/Frameworks/CoreAudio.framework/CoreAudio (compatibility version 1.0.0, current version 1.0.0)')
                sys.exit(0)
            sys.exit(2)
        """))

    def runner_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update({"PATH": f"{self.mocks}:{environment['PATH']}", "MOCK_LOG": str(self.commands),
                            "MOCK_SIM_DATA": str(self.sim_data), "MOCK_REPO": str(self.repo),
                            "MOCK_OUTSIDE": str(self.root / "outside-container"), "HOME": str(self.mock_home),
                            "PYTHONDONTWRITEBYTECODE": "1"})
        environment["RETAIL_SIMULATOR_POLL_INTERVAL"] = "0.01"
        environment["MOCK_APP_PID_STATE"] = str(self.root / "mock-openxray.pid")
        return environment

    def run_runner(self, *extra: str, mutate_protected: bool = False,
                   no_boot_log: bool = False, delete_failure: bool = False,
                   terminate_failure: bool = False,
                   build_jobs: str | None = None,
                   container_mode: str | None = None,
                   autoload_mode: str | None = None,
                   post_launch_log_mode: str | None = None,
                   navigation_failure: bool = False,
                   navigation_finalize_failure: bool = False,
                   openal_log_mutation: str | None = None,
                   foreground_log_mode: str | None = None,
                   pid_replacement: bool = False,
                   menu_marker_mode: str | None = None,
                   empty_recovery_screenshot: bool = False,
                   boot_log_mode: str | None = None,
                   initial_lifecycle_seq: int | None = None,
                   launch_timeout: str = "0.1") -> subprocess.CompletedProcess[str]:
        environment = self.runner_environment()
        Path(environment["MOCK_APP_PID_STATE"]).unlink(missing_ok=True)
        shutil.rmtree(self.sim_data / "Documents", ignore_errors=True)
        if mutate_protected:
            environment["MOCK_MUTATE_PROTECTED"] = "1"
            environment["MOCK_PROTECTED_FILE"] = str(self.repo / "bin/aarch64/Release/xr_3da.app/device")
        if no_boot_log:
            environment["MOCK_NO_BOOT_LOG"] = "1"
        if delete_failure:
            environment["MOCK_DELETE_FAILURE"] = "1"
        if terminate_failure:
            environment["MOCK_TERMINATE_FAILURE"] = "1"
        if build_jobs is not None:
            environment["IOS_BUILD_JOBS"] = build_jobs
        if container_mode is not None:
            environment["MOCK_CONTAINER_MODE"] = container_mode
        if autoload_mode is not None:
            environment["MOCK_AUTOLOAD"] = "1"
            environment["MOCK_AUTOLOAD_LOG_MODE"] = autoload_mode
        if post_launch_log_mode is not None:
            environment["MOCK_POST_LAUNCH_LOG_MODE"] = post_launch_log_mode
            environment["MOCK_POST_LAUNCH_LOG_FILE"] = str(self.sim_data / "Documents/xr_boot.log")
            environment["MOCK_LAUNCH_PID_FILE"] = str(self.root / "runner-launch.pid")
        if "--ui-navigation" in extra:
            environment["MOCK_LAUNCH_PID_FILE"] = str(self.root / "runner-launch.pid")
        if navigation_failure:
            environment["MOCK_NAV_FAILURE"] = "1"
        if navigation_finalize_failure:
            environment["MOCK_NAV_FINALIZE_FAILURE"] = "1"
        if openal_log_mutation is not None:
            environment["MOCK_OPENAL_LOG_MUTATION"] = openal_log_mutation
        if foreground_log_mode is not None:
            environment["MOCK_FOREGROUND_LOG_MODE"] = foreground_log_mode
        if pid_replacement:
            environment["MOCK_PID_REPLACEMENT"] = "1"
        if menu_marker_mode is not None:
            environment["MOCK_MENU_MARKER_MODE"] = menu_marker_mode
        if empty_recovery_screenshot:
            environment["MOCK_EMPTY_RECOVERY_SCREENSHOT"] = "1"
        if boot_log_mode is not None:
            environment["MOCK_BOOT_LOG_MODE"] = boot_log_mode
        if initial_lifecycle_seq is not None:
            environment["MOCK_INITIAL_LIFECYCLE_SEQ"] = str(initial_lifecycle_seq)
        return subprocess.run(("bash", str(self.repo / "misc/ios/retail_simulator.sh"), "--backup", str(self.backup),
                               "--manifest", str(self.manifest), "--work-base", str(self.work_base),
                               "--launch-timeout", launch_timeout, *extra),
                              text=True, capture_output=True, env=environment, check=False)

    def run_guard(self, *arguments: str, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (sys.executable, str(self.repo / "misc/ios/retail_simulator_guard.py"), *arguments),
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )

    def run_launch_proof(self, *, mode: str = "normal", crash: bool = False,
                         launch_output: str | None = None, deferred_death: bool = False,
                         empty_screenshot: bool = False, crash_during_screenshot: bool = False,
                         staged_file: Path | None = None,
                         staged_action: str = "mutate", autoload_save: str | None = None,
                         autoload_mode: str = "normal",
                         late_log_payload: str | None = None,
                         snapshot_output_mode: str | None = None,
                         menu_marker_mode: str | None = None,
                         foreground_cycle: bool = False,
                         preexisting_recovery_screenshot: bool = False) -> subprocess.CompletedProcess[str]:
        environment = self.runner_environment()
        Path(environment["MOCK_APP_PID_STATE"]).unlink(missing_ok=True)
        environment["MOCK_BOOT_LOG_MODE"] = mode
        if menu_marker_mode is not None:
            environment["MOCK_MENU_MARKER_MODE"] = menu_marker_mode
        if crash:
            environment["MOCK_LAUNCH_CRASH"] = "1"
        if launch_output is not None:
            environment["MOCK_LAUNCH_OUTPUT"] = launch_output
        if empty_screenshot:
            environment["MOCK_EMPTY_SCREENSHOT"] = "1"
        pid_file = self.root / "mock-launch.pid"
        environment["MOCK_LAUNCH_PID_FILE"] = str(pid_file)
        if crash_during_screenshot:
            environment["MOCK_CRASH_DURING_SCREENSHOT"] = "1"
        if staged_file is not None:
            environment["MOCK_STAGED_FILE"] = str(staged_file)
            environment["MOCK_STAGED_ACTION"] = staged_action
        evidence = Path(tempfile.mkdtemp(prefix="evidence-", dir=self.root))
        self.last_evidence = evidence
        snapshot_manifest = evidence / "runtime-log-snapshot.txt"
        initial_pid = evidence / "initial-pid.txt"
        recovery_screenshot = evidence / "screenshot-after-foreground.png"
        if preexisting_recovery_screenshot:
            recovery_screenshot.write_bytes(b"stale screenshot")
        if snapshot_output_mode == "symlink":
            target = evidence / "snapshot-target.txt"
            target.write_text("must stay unchanged\n")
            snapshot_manifest.symlink_to(target.name)
        elif snapshot_output_mode == "existing":
            snapshot_manifest.write_text("must stay unchanged\n")
        if autoload_save is not None:
            environment["MOCK_AUTOLOAD"] = "1"
            environment["MOCK_AUTOLOAD_NAME"] = autoload_save
            environment["MOCK_AUTOLOAD_LOG_MODE"] = autoload_mode
        timeout = "3.0" if autoload_save is not None and autoload_mode == "normal" else "1.0" if autoload_save else "0.1"
        if deferred_death:
            environment["MOCK_DEFERRED_DEATH_TRIGGER"] = str(evidence / "screenshot.trigger")
        if late_log_payload is not None:
            environment["MOCK_LATE_LOG_TRIGGER"] = str(evidence / "screenshot.trigger")
            environment["MOCK_LATE_LOG_FILE"] = str(self.sim_data / "Documents/xr_boot.log")
            environment["MOCK_LATE_LOG_PAYLOAD"] = late_log_payload
        return self.run_guard(
            "launch-proof", "--timeout", timeout, "--poll", "0.01",
            "--stdout", str(evidence / "stdout.log"), "--stderr", str(evidence / "stderr.log"),
            "--copied-log", str(evidence / "xr_boot.log"),
            "--source-log", str(self.sim_data / "Documents/xr_boot.log"),
            "--screenshot", str(evidence / "screenshot.png"),
            "--udid", self.simulator_uuid,
            "--bundle", "io.github.tryk016.openxray",
            "--snapshot-manifest", str(snapshot_manifest),
            *( ("--autoload-save", autoload_save) if autoload_save else () ),
            *( ("--initial-pid", str(initial_pid), "--recovery-screenshot", str(recovery_screenshot))
               if foreground_cycle else () ),
            environment=environment,
        )

    def run_finalize_log(self, *, autoload_save: str | None = None) -> subprocess.CompletedProcess[str]:
        return self.run_guard(
            "finalize-log",
            "--source-log", str(self.sim_data / "Documents/xr_boot.log"),
            "--copied-log", str(self.last_evidence / "xr_boot.log"),
            "--snapshot-manifest", str(self.last_evidence / "runtime-log-snapshot.txt"),
            "--proof-metadata", str(self.last_evidence / "runtime-proof.txt"),
            *( ("--autoload-save", autoload_save) if autoload_save else () ),
        )

    def stage_runtime_fixture(self, *, with_saves: bool = False) -> tuple[Path, Path]:
        documents = self.sim_data / "Documents"
        staged_manifest = self.root / f"staged-{len(list(self.root.glob('staged-*.tsv')))}.tsv"
        arguments = [
            "stage", "--backup", str(self.backup), "--manifest", str(self.manifest),
            "--repo", str(self.repo), "--destination", str(documents),
            "--output", str(staged_manifest),
        ]
        if with_saves:
            arguments.append("--with-saves")
        result = self.run_guard(
            *arguments,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return documents, staged_manifest

    def autoload_config(self, documents: Path, name: str = "save") -> tuple[Path, Path, subprocess.CompletedProcess[str]]:
        evidence = self.root / f"generated-{len(list(self.root.glob('generated-*.ltx')))}.ltx"
        manifest = evidence.with_suffix(".tsv")
        result = self.run_guard(
            "autoload-config", "--documents", str(documents), "--name", name,
            "--evidence", str(evidence), "--manifest", str(manifest),
        )
        return evidence, manifest, result

    def test_mocked_happy_path_isolated_and_deletes_only_own_uuid(self) -> None:
        before = digest(self.repo / "bin/aarch64/Release/xr_3da.app/device")
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, digest(self.repo / "bin/aarch64/Release/xr_3da.app/device"))
        work = next(self.work_base.glob("simulator-work-*"))
        self.assertTrue((work / "report.txt").is_file())
        self.assertTrue((work / "staged-files.tsv").is_file())
        self.assertTrue((work / "xr_boot.log").is_file())
        self.assertGreater((work / "screenshot.png").stat().st_size, 0)
        self.assertGreater((work / "screenshot-after-foreground.png").stat().st_size, 0)
        initial_pid = (work / "initial-pid.txt").read_text().strip()
        self.assertRegex(initial_pid, r"^[1-9][0-9]*$")
        report = (work / "report.txt").read_text()
        self.assertIn("runtime_boundary=rendered_main_menu_frame", report)
        self.assertIn(f"pid={initial_pid}", report)
        self.assertIn("openal_provider=OpenALSoft-1.25.2-static", report)
        self.assertRegex(report, r"openal_sha256=[0-9a-f]{64}")
        self.assertIn("cleanup=deleted", report)
        boot_log = (work / "xr_boot.log").read_text()
        self.assertIn(r"gamedata\configs\system.ltx", boot_log)
        self.assertIn("FS: 789 files cached 12 archives", boot_log)
        self.assertIn("ERROR: unrelated renderer probe failed", boot_log)
        self.assertIn("Starting engine...", boot_log)
        staged = (work / "staged-files.tsv").read_text()
        self.assertNotIn("user.ltx", staged)
        self.assertNotIn("savedgames", staged)
        commands = self.commands.read_text()
        self.assertIn("simctl create OpenXRay Retail iOS-26.5 com.apple.CoreSimulator.SimRuntime.iOS-26-5", commands)
        self.assertIn("com.apple.CoreSimulator.SimRuntime.iOS-26-5", commands)
        self.assertIn("com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro-Max", commands)
        self.assertIn("simctl erase 00000000-0000-0000-0000-000000000001", commands)
        self.assertIn("simctl delete 00000000-0000-0000-0000-000000000001", commands)
        self.assertNotIn(" erase all", commands)
        self.assertNotIn(" booted", commands)
        self.assertIn("simctl launch --stdout=", commands)
        self.assertIn("simctl launch 00000000-0000-0000-0000-000000000001 com.apple.mobilesafari", commands)
        app_launches = [line for line in commands.splitlines()
                        if line.startswith("xcrun simctl launch ") and "io.github.tryk016.openxray" in line]
        self.assertEqual(len(app_launches), 2)
        self.assertIn("--stderr=", commands)
        self.assertNotIn("--console", commands)

        source_expected = tree_bytes(self.repo, (".git", ".Codex", "build*", "bin"))
        self.assertEqual(source_expected, tree_bytes(work / "source", (".git", ".Codex", "build*", "bin")))
        source_prefix = self.repo / "build/ios-prefix-iphonesimulator"
        snapshot_prefix = work / "ios-prefix-iphonesimulator"
        expected_prefix = {
            relative: content.replace(str(source_prefix).encode(), str(snapshot_prefix).encode())
            for relative, content in tree_bytes(source_prefix).items()
        }
        self.assertEqual(expected_prefix, tree_bytes(snapshot_prefix))

        cmake_commands = [shlex.split(line) for line in commands.splitlines() if line.startswith("cmake ")]
        configure, build = cmake_commands
        self.assertEqual(Path(configure[configure.index("-S") + 1]), work / "source")
        self.assertEqual(Path(configure[configure.index("-B") + 1]), work / "build")
        self.assertIn(f"-DCMAKE_PREFIX_PATH={snapshot_prefix}", configure)
        self.assertIn(f"-DCMAKE_FIND_ROOT_PATH={snapshot_prefix}", configure)
        self.assertEqual(Path(build[build.index("--build") + 1]), work / "build")
        self.assertTrue((work / "source/bin/aarch64/Release/xr_3da.app/xr_3da").is_file())
        cache = (work / "build/CMakeCache.txt").read_text()
        self.assertIn(f"CMAKE_HOME_DIRECTORY:INTERNAL={work / 'source'}", cache)
        self.assertNotIn(str(self.repo / "build"), cache)
        archive = snapshot_prefix / "lib/libopenal.a"
        binary = work / "source/bin/aarch64/Release/xr_3da.app/xr_3da"
        self.assertIn(f"otool -l {archive}", commands)
        self.assertIn(f"otool -L {binary}", commands)
        for target in ("xrSound", "xrEngine", "xr_3da"):
            self.assertIn(
                f"xcodebuild -project {work / 'build/OpenXRay.xcodeproj'} -target {target} "
                "-configuration Release -showBuildSettings",
                commands,
            )

    def test_runtime_allowlist_is_exact_and_rejects_before_work_or_simctl(self) -> None:
        for value in ("26", "26.5.0", "iOS-26.5", "27", "27.0 ", "27.0;echo nope", ""):
            with self.subTest(value=value):
                result = self.run_runner("--runtime", value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--runtime must be exactly 26.5 or 27.0", result.stderr)
                self.assertFalse(list(self.work_base.iterdir()))
                self.assertFalse(self.commands.exists())

    def test_runtime_270_uses_only_its_exact_id_and_reports_both_fields(self) -> None:
        result = self.run_runner("--runtime", "27.0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        work = next(self.work_base.glob("simulator-work-*"))
        report = (work / "report.txt").read_text()
        self.assertIn("runtime_label=27.0", report)
        self.assertIn("runtime_id=com.apple.CoreSimulator.SimRuntime.iOS-27-0", report)
        commands = self.commands.read_text()
        self.assertIn("OpenXRay Retail iOS-27.0 com.apple.CoreSimulator.SimRuntime.iOS-27-0", commands)
        self.assertIn("com.apple.CoreSimulator.SimRuntime.iOS-27-0", commands)
        self.assertNotIn("com.apple.CoreSimulator.SimRuntime.iOS-26-5", commands)
        self.assertEqual(
            tree_bytes(self.repo, (".git", ".Codex", "build*", "bin")),
            tree_bytes(work / "source", (".git", ".Codex", "build*", "bin")),
        )
        self.assertIn(
            f"-DCMAKE_PREFIX_PATH={work / 'ios-prefix-iphonesimulator'}", commands,
        )

    def test_menu_marker_grammar_and_pid_are_fail_closed(self) -> None:
        for mode, expected in (("missing", "before runtime proof"),
                               ("duplicate", "exactly one main-menu frame marker"),
                               ("zero-pid", "main-menu frame marker"),
                               ("negative-frame", "main-menu frame marker"),
                               ("malformed", "main-menu frame marker")):
            with self.subTest(mode=mode):
                result = self.run_launch_proof(menu_marker_mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_lifecycle_state_machine_accepts_only_the_two_phase_prefix(self) -> None:
        pid = 4242
        pre_cycle = (
            f"* iOS lifecycle v1 pid={pid} seq=7 event=deactivate\n"
            f"* iOS lifecycle v1 pid={pid} seq=41 event=activate\n"
        ).encode()
        self.assertEqual(GUARD_MODULE.lifecycle_pre_cycle_anchor(pre_cycle, pid), 41)
        self.assertEqual(
            GUARD_MODULE.lifecycle_recovery_state(b"", pid, 41),
            GUARD_MODULE.WAITING_FOR_DEACTIVATE,
        )
        deactivated = f"* iOS lifecycle v1 pid={pid} seq=42 event=deactivate\n".encode()
        self.assertEqual(
            GUARD_MODULE.lifecycle_recovery_state(deactivated, pid, 41),
            GUARD_MODULE.BACKGROUND_CONFIRMED,
        )
        self.assertEqual(
            GUARD_MODULE.lifecycle_recovery_state(
                deactivated + f"* iOS lifecycle v1 pid={pid} seq=43 event=activate\n".encode(), pid, 41,
            ),
            GUARD_MODULE.RECOVERY_COMPLETE,
        )
        for mutated, expected in (
            (b"", "does not contain an active lifecycle marker"),
            (f"* iOS lifecycle v1 pid={pid} seq=41 event=deactivate\n".encode(), "must end with activate"),
            (pre_cycle + f"* iOS lifecycle v1 pid={pid} seq=41 event=activate\n".encode(), "not strictly increasing"),
            (f"* iOS lifecycle v1 pid={pid + 1} seq=41 event=activate\n".encode(), "PID does not match"),
            (f"* iOS lifecycle v1 pid={pid} seq=41 event=activate extra\n".encode(), "invalid grammar"),
        ):
            with self.subTest(pre_cycle=mutated):
                with self.assertRaisesRegex(GUARD_MODULE.GuardError, expected):
                    GUARD_MODULE.lifecycle_pre_cycle_anchor(mutated, pid)
        for mutated in (
            f"* iOS lifecycle v1 pid={pid} seq=42 event=activate\n".encode(),
            deactivated + f"* iOS lifecycle v1 pid={pid} seq=42 event=activate\n".encode(),
            deactivated + f"* iOS lifecycle v1 pid={pid} seq=43 event=activate\n".encode()
            + f"* iOS lifecycle v1 pid={pid} seq=44 event=deactivate\n".encode(),
            f"* iOS lifecycle v1 pid={pid + 1} seq=42 event=deactivate\n".encode(),
            f"* iOS lifecycle v1 pid={pid} seq=42 event=deactivate extra\n".encode(),
        ):
            with self.subTest(appended=mutated):
                with self.assertRaisesRegex(GUARD_MODULE.GuardError, "foreground lifecycle markers|PID does not match|invalid grammar"):
                    GUARD_MODULE.lifecycle_recovery_state(mutated, pid, 41)

    def test_foreground_cycle_waits_for_background_before_recovery_launch(self) -> None:
        result = self.run_runner(foreground_log_mode="missing-deactivate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MobileSafari did not confirm OpenXRay background transition", result.stderr)
        commands = self.commands.read_text().splitlines()
        app_launches = [line for line in commands if line.startswith("xcrun simctl launch ")
                        and "io.github.tryk016.openxray" in line]
        self.assertEqual(len(app_launches), 1)
        self.assertIn(
            f"xcrun simctl launch {self.simulator_uuid} com.apple.mobilesafari", commands,
        )

    def test_foreground_cycle_uses_two_phase_timeouts_and_same_pid(self) -> None:
        self.work_base = self.root / "handoff-no-activate"
        self.work_base.mkdir()
        missing_activate = self.run_runner(foreground_log_mode="missing-activate")
        self.assertNotEqual(missing_activate.returncode, 0)
        self.assertIn("did not confirm foreground recovery after verified background", missing_activate.stderr)
        launches = [line for line in self.commands.read_text().splitlines()
                    if line.startswith("xcrun simctl launch ") and "io.github.tryk016.openxray" in line]
        self.assertEqual(len(launches), 2)

        self.work_base = self.root / "handoff-pid-replacement"
        self.work_base.mkdir()
        replacement = self.run_runner(pid_replacement=True)
        self.assertNotEqual(replacement.returncode, 0)
        self.assertIn("PID changed during the controlled foreground cycle", replacement.stderr)

    def test_foreground_cycle_rejects_invalid_lifecycle_prefixes_before_or_after_recovery(self) -> None:
        for mode, expected, launches in (
            ("activate-first", "foreground lifecycle markers must be exactly", 1),
            ("duplicate-deactivate", "foreground lifecycle markers must be exactly", 1),
            ("bad-marker", "lifecycle marker has an invalid grammar", 1),
            ("zero-seq", "lifecycle marker has an invalid grammar", 1),
            ("nonincreasing", "foreground lifecycle markers must be exactly", 1),
            ("wrong-pid", "lifecycle marker PID does not match", 1),
            ("duplicate-activate", "foreground lifecycle markers must be exactly", 2),
            ("extra", "foreground lifecycle markers must be exactly", 2),
        ):
            with self.subTest(mode=mode):
                self.work_base = self.root / f"handoff-{mode}"
                self.work_base.mkdir()
                self.commands.unlink(missing_ok=True)
                result = self.run_runner(foreground_log_mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                app_launches = [line for line in self.commands.read_text().splitlines()
                                if line.startswith("xcrun simctl launch ")
                                and "io.github.tryk016.openxray" in line]
                self.assertEqual(len(app_launches), launches)

    def test_foreground_cycle_accepts_delayed_split_polling_and_non_one_anchor(self) -> None:
        result = self.run_runner(
            foreground_log_mode="delayed", initial_lifecycle_seq=41, launch_timeout="0.5",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        work = next(self.work_base.glob("simulator-work-*"))
        pid = int((work / "initial-pid.txt").read_text())
        boot_log = (work / "xr_boot.log").read_text()
        self.assertIn(f"* iOS lifecycle v1 pid={pid} seq=41 event=activate\n", boot_log)
        self.assertIn(f"* iOS lifecycle v1 pid={pid} seq=42 event=deactivate\n", boot_log)
        self.assertIn(f"* iOS lifecycle v1 pid={pid} seq=43 event=activate\n", boot_log)

    def test_foreground_cycle_accepts_one_shot_menu_marker_without_repeating_it(self) -> None:
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        work = next(self.work_base.glob("simulator-work-*"))
        boot_log = (work / "xr_boot.log").read_text()
        pid = int((work / "initial-pid.txt").read_text())
        self.assertEqual(boot_log.count("* iOS main menu frame v1 "), 1)
        self.assertIn(f"* iOS lifecycle v1 pid={pid} seq=1 event=activate\n", boot_log)
        self.assertIn(f"* iOS lifecycle v1 pid={pid} seq=2 event=deactivate\n", boot_log)
        self.assertIn(f"* iOS lifecycle v1 pid={pid} seq=3 event=activate\n", boot_log)

        without_initial_menu = "\n".join(
            line for line in boot_log.splitlines()
            if not line.startswith("* iOS main menu frame v1 ")
        )
        ready, _ = GUARD_MODULE.runtime_log_state(without_initial_menu, None, pid)
        self.assertFalse(ready)

    def test_foreground_cycle_rejects_second_engine_menu_and_log_mutation(self) -> None:
        self.work_base = self.root / "handoff-second-engine"
        self.work_base.mkdir()
        second_engine = self.run_runner(foreground_log_mode="second-engine")
        self.assertNotEqual(second_engine.returncode, 0)
        self.assertIn("exactly one engine start", second_engine.stderr)

        for mode, expected in (("second-menu", "exactly one main-menu frame marker"),
                               ("log-truncate", "runtime log was truncated or rewritten"),
                               ("log-rotate", "runtime log rotated during proof")):
            with self.subTest(mode=mode):
                self.work_base = self.root / f"handoff-{mode}"
                self.work_base.mkdir()
                result = self.run_runner(foreground_log_mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_ios27_forbidden_scene_marker_and_recovery_screenshot_fail_closed(self) -> None:
        scene_failure = self.run_runner("--runtime", "27.0", foreground_log_mode="noscene")
        self.assertNotEqual(scene_failure.returncode, 0)
        self.assertIn("forbidden failure marker", scene_failure.stderr)
        self.assertIn("com.apple.CoreSimulator.SimRuntime.iOS-27-0", self.commands.read_text())

        self.work_base = self.root / "handoff-sigtrap"
        self.work_base.mkdir()
        trap_failure = self.run_runner("--runtime", "27.0", boot_log_mode="sigtrap")
        self.assertNotEqual(trap_failure.returncode, 0)
        self.assertIn("forbidden failure marker", trap_failure.stderr)

        self.work_base = self.root / "handoff-recovery-shot"
        self.work_base.mkdir()
        shot_failure = self.run_runner(empty_recovery_screenshot=True)
        self.assertNotEqual(shot_failure.returncode, 0)
        self.assertIn("screenshot is missing, empty, or non-regular", shot_failure.stderr)

    def test_foreground_cycle_rejects_a_preexisting_recovery_screenshot(self) -> None:
        result = self.run_launch_proof(
            foreground_cycle=True, preexisting_recovery_screenshot=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("foreground-recovery screenshot destination already exists", result.stderr)
        commands = self.commands.read_text().splitlines()
        self.assertNotIn(
            f"xcrun simctl launch {self.simulator_uuid} com.apple.mobilesafari", commands,
        )
        app_launches = [line for line in commands if line.startswith("xcrun simctl launch ")
                        and "io.github.tryk016.openxray" in line]
        self.assertEqual(len(app_launches), 1)

    def test_save_is_opt_in(self) -> None:
        without = self.root / "without-saves"
        result = self.run_guard(
            "stage", "--backup", str(self.backup), "--manifest", str(self.manifest),
            "--repo", str(self.repo),
            "--destination", str(without), "--output", str(self.root / "without.tsv"),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((without / "_appdata_").exists())
        with_saves = self.root / "with-saves"
        result = self.run_guard(
            "stage", "--backup", str(self.backup), "--manifest", str(self.manifest),
            "--repo", str(self.repo),
            "--destination", str(with_saves), "--output", str(self.root / "with.tsv"),
            "--with-saves",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((with_saves / "_appdata_/savedgames/save.scop").is_file())
        self.assertFalse((with_saves / "_appdata_/user.ltx").exists())

    def test_openal_runtime_provider_mutations_never_publish_a_pass_report(self) -> None:
        for mutation in ("missing", "duplicate", "bad-proc"):
            with self.subTest(mutation=mutation):
                shutil.rmtree(self.sim_data / "Documents", ignore_errors=True)
                result = self.run_runner(openal_log_mutation=mutation)
                self.assertNotEqual(result.returncode, 0)
                work = next(self.work_base.glob("simulator-work-*"))
                self.assertFalse((work / "report.txt").exists())
                self.assertIn("OpenAL runtime provider contract failed", result.stderr)

    def test_large_save_policy_tracks_with_saves_and_protects_ordinary_save(self) -> None:
        without = self.root / "large-without-saves"
        without_manifest = self.root / "large-without-saves.tsv"
        staged = self.run_guard(
            "stage", "--backup", str(self.backup), "--manifest", str(self.manifest),
            "--repo", str(self.repo), "--destination", str(without),
            "--output", str(without_manifest),
        )
        self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)
        self.assertNotIn("_appdata_/savedgames/save.scop", without_manifest.read_text())
        compared = self.run_guard(
            "staged-compare", "--root", str(without), "--manifest", str(without_manifest),
            "--required", str(self.manifest / "required-archives.tsv"),
            "--large", str(self.manifest / "large-files-sha256.tsv"),
        )
        self.assertEqual(compared.returncode, 0, compared.stdout + compared.stderr)

        with_saves = self.root / "large-with-saves"
        with_manifest = self.root / "large-with-saves.tsv"
        staged = self.run_guard(
            "stage", "--backup", str(self.backup), "--manifest", str(self.manifest),
            "--repo", str(self.repo), "--destination", str(with_saves),
            "--output", str(with_manifest), "--with-saves",
        )
        self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)
        protected = with_saves / "_appdata_/savedgames/other.scop"
        protected.write_bytes(b"ordinary-large-save-mutated")
        compared = self.run_guard(
            "staged-compare", "--root", str(with_saves), "--manifest", str(with_manifest),
            "--required", str(self.manifest / "required-archives.tsv"),
            "--large", str(self.manifest / "large-files-sha256.tsv"), "--with-saves",
        )
        self.assertNotEqual(compared.returncode, 0)
        self.assertIn("staged file integrity mismatch: _appdata_/savedgames/other.scop", compared.stderr)

    def test_autoload_happy_path_generates_exact_private_config_and_sync_complete_proof(self) -> None:
        result = self.run_runner(
            "--with-saves", "--autoload-save", "save", "--launch-timeout", "0.5",
            autoload_mode="normal",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        work = next(self.work_base.glob("simulator-work-*"))
        expected = (
            "keypress_on_start 0\n"
            "ios_diagnostics 0\n"
            "ios_autoinput 0\n"
            "start server(save/single/alife/load) client(localhost)\n"
        ).encode()
        self.assertEqual((work / "generated-user.ltx").read_bytes(), expected)
        generated_manifest = (work / "generated-user.ltx.manifest.tsv").read_text()
        self.assertIn(digest(work / "generated-user.ltx"), generated_manifest)
        report = (work / "report.txt").read_text()
        self.assertIn("runtime_boundary=saved_game_sync_complete", report)
        self.assertIn("autoload_save=save", report)
        self.assertIn("level=zaton", report)
        self.assertIn("status=unchanged", (work / "autoload-save-mutation.txt").read_text())
        staged = (work / "staged-files.tsv").read_text()
        self.assertNotIn("_appdata_/user.ltx", staged)
        self.assertEqual((self.backup / "_appdata_/user.ltx").read_bytes(), b"must-not-copy")

    def test_ui_navigation_requires_save_and_autoload_and_enables_autoinput_only_there(self) -> None:
        for arguments in (("--ui-navigation",), ("--with-saves", "--ui-navigation")):
            with self.subTest(arguments=arguments):
                result = self.run_runner(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--ui-navigation requires both --with-saves and --autoload-save", result.stderr)
        documents, _ = self.stage_runtime_fixture(with_saves=True)
        evidence = self.root / "ui-generated-user.ltx"
        manifest = self.root / "ui-generated-user.tsv"
        result = self.run_guard(
            "autoload-config", "--documents", str(documents), "--name", "save",
            "--evidence", str(evidence), "--manifest", str(manifest), "--ios-autoinput",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ios_autoinput 1\n", evidence.read_text())
        self.assertNotIn("ios_autoinput=", evidence.read_text())

    def test_ui_navigation_happy_path_uses_one_launch_exact_pid_and_correct_order(self) -> None:
        result = self.run_runner(
            "--with-saves", "--autoload-save", "save", "--ui-navigation", "--launch-timeout", "0.5",
            autoload_mode="normal",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        work = next(self.work_base.glob("simulator-work-*"))
        report = (work / "report.txt").read_text()
        self.assertIn("ui_navigation=1", report)
        navigation_scope = [
            line for line in report.splitlines()
            if line.startswith("ui_navigation_scope=")
        ]
        self.assertEqual(navigation_scope, [
            "ui_navigation_scope=semantic-ui-navigation-only; CoP eptTasks is the combined tasks/map surface; "
            "not pixel, readability, performance, or physical-device proof"
        ])
        self.assertTrue((work / "ui-navigation-log-snapshot.txt").is_file())
        self.assertTrue((work / "ui-navigation-pre-termination.json").is_file())
        self.assertTrue((work / "ui-navigation-post-termination.json").is_file())
        self.assertIn("ios_autoinput 1\n", (work / "generated-user.ltx").read_text())
        commands = self.commands.read_text().splitlines()
        launch = [line for line in commands if line.startswith("xcrun simctl launch ")]
        self.assertEqual(len(launch), 1)
        navigation_run = next(index for index, line in enumerate(commands) if line.startswith("navigation-run "))
        screenshot = next(index for index, line in enumerate(commands) if "simctl io " in line and " screenshot " in line)
        terminate = next(index for index, line in enumerate(commands) if line.startswith("xcrun simctl terminate "))
        navigation_finalize = next(index for index, line in enumerate(commands) if line.startswith("navigation-finalize "))
        self.assertLess(navigation_run, screenshot)
        self.assertLess(terminate, navigation_finalize)
        passed_pid = commands[navigation_run].split("--expected-pid ", 1)[1].split()[0]
        self.assertEqual(passed_pid, (self.root / "runner-launch.pid").read_text())
        self.assertIn(str(work / "source/misc/ios/simulator_ui_navigation.py"), commands[navigation_run])
        self.assertNotIn(str(REPO_ROOT / "misc/ios/simulator_ui_navigation.py"), commands[navigation_run])
        self.assertIn("--timeout-seconds 60.0", commands[navigation_run])

    def test_ui_navigation_failure_refreshes_log_with_exact_production_budget(self) -> None:
        navigation_script = self.repo / "misc/ios/simulator_ui_navigation.py"
        documents = self.root / "navigation-documents"
        (documents / "_appdata_").mkdir(parents=True)

        for mode, expected in (("exit", "semantic Simulator UI navigation failed"),
                               ("timeout", "semantic Simulator UI navigation timed out")):
            with self.subTest(mode=mode):
                evidence = self.root / f"navigation-{mode}"
                evidence.mkdir()
                source_log = evidence / "source.log"
                copied_log = evidence / "copied.log"
                source_log.write_text("before navigation failure\n")
                navigation_calls: list[tuple[tuple[str, ...], float]] = []

                def fake_run(command: tuple[str, ...], **kwargs: object) -> SimpleNamespace:
                    if command[:3] == ("xcrun", "simctl", "launch"):
                        return SimpleNamespace(
                            returncode=0,
                            stdout=b"io.github.tryk016.openxray: 4242\n",
                            stderr=b"",
                        )
                    self.assertEqual(command[:3], (sys.executable, str(navigation_script), "run"))
                    navigation_calls.append((command, float(kwargs["timeout"])))
                    source_log.write_text(source_log.read_text() + f"{mode} navigation failure\n")
                    if mode == "timeout":
                        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                    return SimpleNamespace(returncode=7, stdout=b"", stderr=b"")

                with mock.patch.object(GUARD_MODULE, "require_live_app_pid"), \
                        mock.patch.object(GUARD_MODULE, "runtime_log_state", return_value=(True, "zaton")), \
                        mock.patch.object(GUARD_MODULE.subprocess, "run", side_effect=fake_run):
                    with self.assertRaisesRegex(GUARD_MODULE.GuardError, expected):
                        GUARD_MODULE.prove_launch(
                            1.0, 0.01, evidence / "stdout.log", evidence / "stderr.log",
                            copied_log, evidence / "screenshot.png", source_log,
                            self.simulator_uuid, "io.github.tryk016.openxray", "save",
                            evidence / "runtime-log-snapshot.txt", navigation_script,
                            documents, evidence / "navigation-snapshot.txt",
                            evidence / "navigation-pre-report.json",
                        )

                self.assertEqual(len(navigation_calls), 1)
                command, child_timeout = navigation_calls[0]
                self.assertEqual(command[-2:], ("--timeout-seconds", "60.0"))
                self.assertEqual(child_timeout, 450.0)
                self.assertIn(f"{mode} navigation failure\n", copied_log.read_text())

    def test_launch_proof_rejects_incomplete_ui_navigation_arguments_before_launch(self) -> None:
        result = self.run_guard(
            "launch-proof", "--timeout", "1", "--poll", "0.1",
            "--stdout", str(self.root / "stdout"), "--stderr", str(self.root / "stderr"),
            "--copied-log", str(self.root / "copied"), "--source-log", str(self.root / "source"),
            "--screenshot", str(self.root / "screenshot"), "--udid", self.simulator_uuid,
            "--bundle", "io.github.tryk016.openxray", "--autoload-save", "save",
            "--snapshot-manifest", str(self.root / "snapshot"),
            "--navigation-script", str(self.repo / "misc/ios/simulator_ui_navigation.py"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("complete set", result.stderr)
        self.assertFalse(self.commands.exists())

    def test_ui_navigation_controller_or_post_stop_finalizer_failure_never_reports_pass(self) -> None:
        for keyword, options in (
            ("navigation_failure", {"navigation_failure": True}),
            ("navigation_finalize_failure", {"navigation_finalize_failure": True}),
        ):
            with self.subTest(mode=keyword):
                result = self.run_runner(
                    "--with-saves", "--autoload-save", "save", "--ui-navigation", "--launch-timeout", "0.5",
                    autoload_mode="normal", **options,
                )
                self.assertNotEqual(result.returncode, 0)
                work = next(self.work_base.glob("simulator-work-*"))
                self.assertFalse((work / "report.txt").exists())
                if keyword == "navigation_failure":
                    self.assertIn("navigation fixture failure\n", (work / "xr_boot.log").read_text())

    def test_ui_navigation_post_stop_guards_reject_late_fatal_marker_and_rotation(self) -> None:
        cases = (
            ("append-fatal", "post-stop Simulator log did not preserve"),
            ("append-ui-marker", "post-stop semantic Simulator UI navigation proof failed"),
            ("rotate", "post-stop Simulator log did not preserve"),
        )
        for mode, expected in cases:
            with self.subTest(mode=mode):
                shutil.rmtree(self.sim_data, ignore_errors=True)
                result = self.run_runner(
                    "--with-saves", "--autoload-save", "save", "--ui-navigation", "--launch-timeout", "0.5",
                    autoload_mode="normal", post_launch_log_mode=mode,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                work = next(self.work_base.glob("simulator-work-*"))
                self.assertFalse((work / "report.txt").exists())

    def test_ui_navigation_termination_failure_prevents_both_finalizers_and_staged_mutation_report(self) -> None:
        result = self.run_runner(
            "--with-saves", "--autoload-save", "save", "--ui-navigation", "--launch-timeout", "0.5",
            autoload_mode="normal", terminate_failure=True,
        )
        self.assertNotEqual(result.returncode, 0)
        work = next(self.work_base.glob("simulator-work-*"))
        self.assertFalse((work / "runtime-proof.txt").exists())
        self.assertFalse((work / "ui-navigation-post-termination.json").exists())
        self.assertFalse((work / "autoload-save-after.tsv").exists())
        self.assertFalse((work / "report.txt").exists())

    def test_autoload_requires_saves_and_rejects_unsafe_or_uppercase_names(self) -> None:
        required = self.run_runner("--autoload-save", "save")
        self.assertNotEqual(required.returncode, 0)
        self.assertIn("requires --with-saves", required.stderr)
        documents, _ = self.stage_runtime_fixture(with_saves=True)
        for name in ("Save", "save.scop", "../save", "save/other", "save;quit", "", "żsave"):
            with self.subTest(name=name):
                _, _, result = self.autoload_config(documents, name)
                self.assertNotEqual(result.returncode, 0)

    def test_autoload_save_must_be_exact_direct_nonempty_regular_and_case_unique(self) -> None:
        documents, _ = self.stage_runtime_fixture(with_saves=True)
        save = documents / "_appdata_/savedgames/save.scop"
        save.unlink()
        _, _, missing = self.autoload_config(documents)
        self.assertNotEqual(missing.returncode, 0)
        save.symlink_to(documents / "_appdata_/savedgames/other.scop")
        _, _, linked = self.autoload_config(documents)
        self.assertNotEqual(linked.returncode, 0)
        save.unlink()
        save.write_bytes(b"")
        _, _, empty = self.autoload_config(documents)
        self.assertNotEqual(empty.returncode, 0)
        save.write_bytes(b"save")
        # APFS test volumes are normally case-insensitive, so a physical
        # SAVE.scop would alias save.scop.  Exercise the pure collision policy
        # directly rather than relying on host volume semantics.
        self.assertTrue(GUARD_MODULE.has_casefold_collision("save.scop", ("SAVE.scop",)))
        self.assertFalse(GUARD_MODULE.has_casefold_collision("save.scop", ("other.scop",)))

    def test_autoload_config_is_exact_and_never_copies_backup_user_ltx(self) -> None:
        documents, _ = self.stage_runtime_fixture(with_saves=True)
        evidence, manifest, result = self.autoload_config(documents)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        expected = (
            "keypress_on_start 0\n"
            "ios_diagnostics 0\n"
            "ios_autoinput 0\n"
            "start server(save/single/alife/load) client(localhost)\n"
        ).encode()
        generated = documents / "_appdata_/user.ltx"
        self.assertEqual(generated.read_bytes(), expected)
        self.assertEqual(evidence.read_bytes(), expected)
        self.assertEqual(stat_mode := (generated.stat().st_mode & 0o777), 0o600, oct(stat_mode))
        self.assertIn(digest(evidence), manifest.read_text())
        self.assertNotEqual(generated.read_bytes(), (self.backup / "_appdata_/user.ltx").read_bytes())

    def test_autoload_rejects_symlinked_generated_config_parent(self) -> None:
        documents, _ = self.stage_runtime_fixture(with_saves=True)
        appdata = documents / "_appdata_"
        saves = appdata / "savedgames"
        for child in saves.iterdir():
            child.unlink()
        saves.rmdir()
        appdata.rmdir()
        appdata.symlink_to(self.root / "outside-appdata", target_is_directory=True)
        _, _, result = self.autoload_config(documents)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required directory", result.stderr)

    def test_autoload_rejects_symlinked_evidence_parent(self) -> None:
        documents, _ = self.stage_runtime_fixture(with_saves=True)
        outside = self.root / "outside-evidence"
        outside.mkdir()
        linked_parent = self.root / "linked-evidence"
        linked_parent.symlink_to(outside, target_is_directory=True)
        result = self.run_guard(
            "autoload-config", "--documents", str(documents), "--name", "save",
            "--evidence", str(linked_parent / "generated-user.ltx"),
            "--manifest", str(linked_parent / "generated-user.tsv"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required directory", result.stderr)
        self.assertFalse(any(outside.iterdir()))

    def test_autoload_oracle_requires_ordered_sync_complete_markers(self) -> None:
        passed = self.run_launch_proof(autoload_save="save")
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        for mode in (
            "missing-user", "misordered", "mismatch", "new-game", "fatal", "missing-accepted",
            "missing-hom", "misordered-hom", "missing-save", "missing-sync", "multiple-levels",
            "missing-memory",
        ):
            with self.subTest(mode=mode):
                rejected = self.run_launch_proof(autoload_save="save", autoload_mode=mode)
                self.assertNotEqual(rejected.returncode, 0)

    def test_selected_save_is_the_only_allowed_runtime_mutation(self) -> None:
        documents, staged_manifest = self.stage_runtime_fixture(with_saves=True)
        selected = documents / "_appdata_/savedgames/save.scop"
        before = self.root / "save-before.tsv"
        state = self.run_guard("selected-save-state", "--file", str(selected), "--output", str(before))
        self.assertEqual(state.returncode, 0, state.stdout + state.stderr)
        unchanged = self.run_guard(
            "selected-save-mutation", "--file", str(selected), "--before", str(before),
            "--after", str(self.root / "save-after-unchanged.tsv"),
            "--report", str(self.root / "save-unchanged.txt"),
        )
        self.assertEqual(unchanged.returncode, 0, unchanged.stdout + unchanged.stderr)
        selected.write_bytes(b"changed-save")
        changed = self.run_guard(
            "selected-save-mutation", "--file", str(selected), "--before", str(before),
            "--after", str(self.root / "save-after-changed.tsv"),
            "--report", str(self.root / "save-changed.txt"),
        )
        self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
        self.assertIn("status=modified", (self.root / "save-changed.txt").read_text())
        compared = self.run_guard(
            "staged-compare", "--root", str(documents), "--manifest", str(staged_manifest),
            "--required", str(self.manifest / "required-archives.tsv"),
            "--large", str(self.manifest / "large-files-sha256.tsv"),
            "--with-saves", "--mutable-save", "save",
        )
        self.assertEqual(compared.returncode, 0, compared.stdout + compared.stderr)
        other = documents / "_appdata_/savedgames/other.scop"
        other.write_bytes(b"mutated-other")
        rejected = self.run_guard(
            "staged-compare", "--root", str(documents), "--manifest", str(staged_manifest),
            "--required", str(self.manifest / "required-archives.tsv"),
            "--large", str(self.manifest / "large-files-sha256.tsv"),
            "--with-saves", "--mutable-save", "save",
        )
        self.assertNotEqual(rejected.returncode, 0)

    def test_selected_save_deletion_or_empty_state_fails_closed(self) -> None:
        documents, _ = self.stage_runtime_fixture(with_saves=True)
        selected = documents / "_appdata_/savedgames/save.scop"
        before = self.root / "save-before.tsv"
        self.assertEqual(
            self.run_guard("selected-save-state", "--file", str(selected), "--output", str(before)).returncode,
            0,
        )
        selected.unlink()
        deleted = self.run_guard(
            "selected-save-mutation", "--file", str(selected), "--before", str(before),
            "--after", str(self.root / "after-deleted.tsv"), "--report", str(self.root / "deleted.txt"),
        )
        self.assertNotEqual(deleted.returncode, 0)
        selected.write_bytes(b"")
        empty = self.run_guard(
            "selected-save-mutation", "--file", str(selected), "--before", str(before),
            "--after", str(self.root / "after-empty.tsv"), "--report", str(self.root / "empty.txt"),
        )
        self.assertNotEqual(empty.returncode, 0)

    def test_autoload_requires_process_stop_before_save_hash_and_pass_report(self) -> None:
        result = self.run_runner(
            "--with-saves", "--autoload-save", "save", "--launch-timeout", "0.5",
            autoload_mode="normal", terminate_failure=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not stop Simulator app", result.stderr)
        work = next(self.work_base.glob("simulator-work-*"))
        self.assertFalse((work / "autoload-save-after.tsv").exists())
        self.assertFalse((work / "report.txt").exists())

    def test_stage_destination_inside_repo_is_rejected(self) -> None:
        result = self.run_guard(
            "stage", "--backup", str(self.backup), "--manifest", str(self.manifest),
            "--repo", str(self.repo), "--destination", str(self.repo / "forbidden-stage"),
            "--output", str(self.root / "forbidden-stage.tsv"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disjoint from protected root", result.stderr)

    def test_snapshot_manifest_rejects_symlinks(self) -> None:
        link = self.repo / "source-link"
        link.symlink_to(self.repo / "cmake/toolchains/ios.toolchain.cmake")
        result = self.run_guard(
            "tree-manifest", "--root", str(self.repo), "--output", str(self.root / "repo.tsv"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink is not allowed", result.stderr)

    def test_manifest_tampering_is_rejected_before_tools(self) -> None:
        (self.manifest / "files.tsv").write_text("bytes\tpath\n1\t../escape\n")
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe manifest path", result.stderr)
        self.assertFalse(self.commands.exists())

    def test_backup_mutation_is_detected(self) -> None:
        (self.backup / "resources/resources.db0").write_bytes(b"changed")
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("backup size mismatch", result.stderr)

    def test_large_manifest_rejects_same_size_noncore_backup_corruption(self) -> None:
        victim = self.backup / "localization/xefis_movies.db"
        original = victim.read_bytes()
        victim.write_bytes(bytes([original[0] ^ 0x01]) + original[1:])
        self.assertEqual(victim.stat().st_size, len(original))
        result = self.run_guard(
            "retail-verify", "--backup", str(self.backup), "--manifest", str(self.manifest),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("large retail file mismatch: localization/xefis_movies.db", result.stderr)

    def test_required_archive_set_rejects_one_missing_entry(self) -> None:
        required = self.manifest / "required-archives.tsv"
        lines = required.read_text().splitlines()
        required.write_text("\n".join(line for line in lines if "resources/resources.db4" not in line) + "\n")
        result = self.run_guard("retail-verify", "--backup", str(self.backup), "--manifest", str(self.manifest))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required archive set mismatch", result.stderr)

    def test_every_protected_stamp_uses_a_real_single_file_contract(self) -> None:
        runner_source = (self.repo / "misc/ios/retail_simulator.sh").read_text()
        for index, relative in enumerate(STAMP_PATHS):
            with self.subTest(stamp=relative):
                stamp = self.repo / relative
                stamp_manifest = self.root / f"stamp-{index}.tsv"
                created = self.run_guard("file-manifest", "--file", str(stamp), "--output", str(stamp_manifest))
                self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
                original = stamp.read_bytes()
                stamp.write_bytes(original + b"tampered")
                rejected = self.run_guard("file-compare", "--file", str(stamp), "--manifest", str(stamp_manifest))
                self.assertNotEqual(rejected.returncode, 0)
                stamp.write_bytes(original)
                self.assertIn(relative.split("/")[-1], runner_source)

    def test_protected_artifact_mutation_is_detected_after_build(self) -> None:
        result = self.run_runner(mutate_protected=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("protected input changed", result.stderr)
        commands = self.commands.read_text()
        self.assertNotIn("simctl create", commands)

    def test_launch_timeout_is_failure_and_cleans_own_simulator(self) -> None:
        result = self.run_runner(no_boot_log=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not prove its required runtime boundary", result.stderr)
        commands = self.commands.read_text()
        self.assertIn("simctl delete 00000000-0000-0000-0000-000000000001", commands)

    def test_real_backslash_log_with_unrelated_error_passes(self) -> None:
        result = self.run_launch_proof()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_missing_system_ltx_fails(self) -> None:
        result = self.run_launch_proof(mode="missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("before runtime proof", result.stderr)

    def test_archive_count_must_be_exactly_twelve(self) -> None:
        result = self.run_launch_proof(mode="archives11")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("before runtime proof", result.stderr)

    def test_process_exit_after_valid_markers_fails(self) -> None:
        result = self.run_launch_proof(crash=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not alive immediately after launch", result.stderr)

    def test_malformed_or_ambiguous_launch_output_fails_closed(self) -> None:
        bundle = "io.github.tryk016.openxray"
        for output in ("", f"{bundle}: 0", f"{bundle}: 123\\nextra"):
            with self.subTest(output=output):
                result = self.run_launch_proof(launch_output=output)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("malformed simctl launch output", result.stderr)

    def test_deferred_app_death_during_stability_interval_fails(self) -> None:
        result = self.run_launch_proof(deferred_death=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not alive during post-screenshot stability interval", result.stderr)

    def test_final_log_snapshot_rejects_late_fatal_while_pid_remains_alive(self) -> None:
        result = self.run_launch_proof(
            autoload_save="save", late_log_payload="FATAL: late fixture failure\n",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime log contains a forbidden failure marker", result.stderr)
        self.assertIn("FATAL: late fixture failure", (self.last_evidence / "xr_boot.log").read_text())
        self.assertFalse((self.last_evidence / "runtime-proof.txt").exists())

    def test_final_log_snapshot_positive_path_preserves_runtime_boundary(self) -> None:
        launched = self.run_launch_proof(autoload_save="save")
        self.assertEqual(launched.returncode, 0, launched.stdout + launched.stderr)
        self.assertFalse((self.last_evidence / "runtime-proof.txt").exists())
        result = self.run_finalize_log(autoload_save="save")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("runtime_boundary=saved_game_sync_complete",
                      (self.last_evidence / "runtime-proof.txt").read_text())

    def test_launch_rejects_preexisting_snapshot_output(self) -> None:
        for mode, expected in (("symlink", "exists as a symlink"), ("existing", "exists as a regular file")):
            with self.subTest(mode=mode):
                result = self.run_launch_proof(snapshot_output_mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertEqual(
                    (self.last_evidence / "runtime-log-snapshot.txt").read_text(),
                    "must stay unchanged\n",
                )

    def assert_runner_rejects_post_stop_log_change(self, mode: str, expected: str) -> None:
        result = self.run_runner(post_launch_log_mode=mode)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(expected, result.stderr)
        work = next(self.work_base.glob("simulator-work-*"))
        self.assertFalse((work / "runtime-proof.txt").exists())
        self.assertFalse((work / "report.txt").exists())

    def test_runner_post_stop_log_check_rejects_late_fatal(self) -> None:
        self.assert_runner_rejects_post_stop_log_change("append-fatal", "forbidden failure marker")

    def test_runner_post_stop_log_check_rejects_rotation(self) -> None:
        self.assert_runner_rejects_post_stop_log_change("rotate", "rotated during proof")

    def test_runner_post_stop_log_check_rejects_truncation(self) -> None:
        self.assert_runner_rejects_post_stop_log_change("truncate", "truncated or rewritten during proof")

    def test_runner_post_stop_log_check_rejects_same_target_symlink(self) -> None:
        self.assert_runner_rejects_post_stop_log_change(
            "symlink", "runtime log must be a regular non-symlink file",
        )

    def test_finalize_rejects_symlinked_snapshot_manifest(self) -> None:
        launched = self.run_launch_proof(autoload_save="save")
        self.assertEqual(launched.returncode, 0, launched.stdout + launched.stderr)
        manifest = self.last_evidence / "runtime-log-snapshot.txt"
        real_manifest = self.last_evidence / "runtime-log-snapshot-real.txt"
        manifest.rename(real_manifest)
        manifest.symlink_to(real_manifest.name)
        result = self.run_finalize_log(autoload_save="save")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime snapshot manifest must be a regular non-symlink file", result.stderr)
        self.assertFalse((self.last_evidence / "runtime-proof.txt").exists())

    def test_finalize_rejects_symlinked_proof_output(self) -> None:
        launched = self.run_launch_proof(autoload_save="save")
        self.assertEqual(launched.returncode, 0, launched.stdout + launched.stderr)
        proof = self.last_evidence / "runtime-proof.txt"
        target = self.last_evidence / "runtime-proof-target.txt"
        target.write_text("must stay unchanged\n")
        proof.symlink_to(target.name)
        result = self.run_finalize_log(autoload_save="save")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("new regular-file destination already exists as a symlink", result.stderr)
        self.assertEqual(target.read_text(), "must stay unchanged\n")

    def test_finalize_rejects_existing_proof_output(self) -> None:
        launched = self.run_launch_proof(autoload_save="save")
        self.assertEqual(launched.returncode, 0, launched.stdout + launched.stderr)
        proof = self.last_evidence / "runtime-proof.txt"
        proof.write_text("must stay unchanged\n")
        result = self.run_finalize_log(autoload_save="save")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("new regular-file destination already exists as a regular file", result.stderr)
        self.assertEqual(proof.read_text(), "must stay unchanged\n")

    def test_empty_screenshot_fails(self) -> None:
        result = self.run_launch_proof(empty_screenshot=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("screenshot is missing, empty, or non-regular", result.stderr)

    def test_crash_during_screenshot_fails_stability_check(self) -> None:
        # The mock ends the app naturally once the screenshot command starts;
        # the guard itself never signals it.
        result = self.run_launch_proof(
            deferred_death=True, crash_during_screenshot=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not alive after screenshot", result.stderr)

    def test_runtime_mutation_of_staged_archive_is_rejected(self) -> None:
        documents, staged_manifest = self.stage_runtime_fixture()
        victim = documents / "resources/resources.db4"
        launched = self.run_launch_proof(staged_file=victim)
        self.assertEqual(launched.returncode, 0, launched.stdout + launched.stderr)
        compared = self.run_guard(
            "staged-compare", "--root", str(documents), "--manifest", str(staged_manifest),
            "--required", str(self.manifest / "required-archives.tsv"),
            "--large", str(self.manifest / "large-files-sha256.tsv"),
        )
        self.assertNotEqual(compared.returncode, 0)
        self.assertIn("staged file integrity mismatch", compared.stderr)

    def test_runtime_same_size_mutation_of_noncore_large_file_is_rejected(self) -> None:
        documents, staged_manifest = self.stage_runtime_fixture()
        victim = documents / "localization/xefis_movies.db"
        original_size = victim.stat().st_size
        launched = self.run_launch_proof(staged_file=victim, staged_action="same-size-corrupt")
        self.assertEqual(launched.returncode, 0, launched.stdout + launched.stderr)
        self.assertEqual(victim.stat().st_size, original_size)
        compared = self.run_guard(
            "staged-compare", "--root", str(documents), "--manifest", str(staged_manifest),
            "--required", str(self.manifest / "required-archives.tsv"),
            "--large", str(self.manifest / "large-files-sha256.tsv"),
        )
        self.assertNotEqual(compared.returncode, 0)
        self.assertIn("staged file integrity mismatch: localization/xefis_movies.db", compared.stderr)

    def test_runtime_deletion_of_staged_archive_is_rejected(self) -> None:
        documents, staged_manifest = self.stage_runtime_fixture()
        victim = documents / "levels/levels.db1"
        launched = self.run_launch_proof(staged_file=victim, staged_action="delete")
        self.assertEqual(launched.returncode, 0, launched.stdout + launched.stderr)
        compared = self.run_guard(
            "staged-compare", "--root", str(documents), "--manifest", str(staged_manifest),
            "--required", str(self.manifest / "required-archives.tsv"),
            "--large", str(self.manifest / "large-files-sha256.tsv"),
        )
        self.assertNotEqual(compared.returncode, 0)
        self.assertIn("staged file is missing or non-regular", compared.stderr)

    def test_successful_path_rejects_delete_failure_and_never_prints_pass(self) -> None:
        result = self.run_runner(delete_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("PASS — isolated retail Simulator workflow", result.stdout)
        self.assertIn("could not delete dedicated Simulator", result.stderr)

    def test_failure_cleanup_records_delete_failure_without_masking_status(self) -> None:
        result = self.run_runner(no_boot_log=True, delete_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cleanup-error: could not delete dedicated Simulator", result.stderr)
        work = next(self.work_base.glob("simulator-work-*"))
        self.assertIn("cleanup-error:", (work / "cleanup-error.txt").read_text())
        self.assertFalse((work / "report.txt").exists())

    def test_data_container_equal_to_repo_is_rejected(self) -> None:
        result = self.run_runner(container_mode="repo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("data container failed isolation contract", result.stderr)

    def test_data_container_outside_coresimulator_is_rejected(self) -> None:
        result = self.run_runner(container_mode="outside")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("data container failed isolation contract", result.stderr)

    def test_binary_contract_rejects_fat_mixed_and_device_platforms(self) -> None:
        binary = self.repo / "bin/aarch64/Release/xr_3da.app/device"
        cases = (
            ({"MOCK_LIPO_ARCHS": "arm64 x86_64"}, "exactly one arm64 slice"),
            ({"MOCK_VTOOL_MODE": "mixed"}, "platform/minOS entries"),
            ({"MOCK_VTOOL_MODE": "platform"}, "platform/minOS entries"),
        )
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                environment = self.runner_environment()
                environment.update(overrides)
                result = self.run_guard(
                    "binary-contract", "--binary", str(binary), environment=environment,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_staging_rejects_symlinked_source_file(self) -> None:
        victim = self.backup / "resources/resources.db4"
        victim.unlink()
        victim.symlink_to(self.backup / "resources/resources.db3")
        result = self.run_guard(
            "stage", "--backup", str(self.backup), "--manifest", str(self.manifest),
            "--repo", str(self.repo), "--destination", str(self.root / "symlink-stage"),
            "--output", str(self.root / "symlink-stage.tsv"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink is not allowed", result.stderr)

    def test_ios_build_jobs_must_be_positive_integer(self) -> None:
        result = self.run_runner(build_jobs="bogus")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("IOS_BUILD_JOBS must be a positive integer", result.stderr)

    def test_work_base_may_be_parent_of_backup_and_manifest_but_work_root_is_disjoint(self) -> None:
        shared = self.root / "shared"
        shared.mkdir()
        backup = shared / "backup"
        manifest = shared / "backup.manifest"
        backup.mkdir()
        manifest.mkdir()
        accepted = self.run_guard(
            "paths", "--repo", str(self.repo), "--backup", str(backup),
            "--manifest", str(manifest), "--work-base", str(shared),
        )
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        work = shared / "simulator-work-fixture"
        work.mkdir()
        accepted = self.run_guard(
            "work-root", "--repo", str(self.repo), "--backup", str(backup),
            "--manifest", str(manifest), "--work-root", str(work),
        )
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    def test_source_contains_no_device_escape_hatches(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        for forbidden in ("devicectl", "install_device.sh", "device_lease"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("erase all", source)
        self.assertNotIn("delete all", source)
        self.assertIn("com.apple.CoreSimulator.SimRuntime.iOS-26-5", source)
        self.assertIn("com.apple.CoreSimulator.SimRuntime.iOS-27-0", source)
        self.assertIn("com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro-Max", source)
        self.assertIn("trap cleanup EXIT HUP INT TERM", source)
        self.assertIn("build/ios-engine-fastdevice-iphoneos", source)
        self.assertNotIn("--copy-links", source)
        guard_source = GUARD.read_text(encoding="utf-8")
        self.assertNotIn("--console", guard_source)
        self.assertIn('f"--stdout={stdout_path}"', guard_source)
        self.assertIn('f"--stderr={stderr_path}"', guard_source)

    def test_runner_is_executable(self) -> None:
        self.assertTrue(os.access(RUNNER, os.X_OK))

    def test_required_result_writes_are_fail_closed(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn(
            '''printf '%s\\n' "$device_uuid" > "$work_root/simulator-uuid.txt" \\
    || fail "could not write dedicated Simulator UUID"''',
            source,
        )
        self.assertIn(
            '''} > "$report" || fail "could not write retail Simulator report"''',
            source,
        )


if __name__ == "__main__":
    unittest.main()
