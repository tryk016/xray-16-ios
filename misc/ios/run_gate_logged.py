#!/usr/bin/env python3
"""Allowlisted, private full-log runner for OpenXRay iOS gates."""
from __future__ import annotations
import argparse, fcntl, hashlib, importlib.util, json, os, re, secrets, signal, stat, subprocess, sys, time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_DIR = Path("/Users/patryk/openxray-handoff/gate-logs")
@dataclass(frozen=True)
class GateSpec: name: str; command: tuple[str,...]; build_tree: Path; stamp: Path | None
GATES = {
 "fast": GateSpec("fast", ("misc/ios/build_fast_device.sh",), ROOT/"build/ios-engine-fastdevice-iphoneos", ROOT/"build/ios-engine-fastdevice-iphoneos/.ios_fast_device_gate_ok"),
 "device": GateSpec("device", ("misc/ios/build_check.sh",), ROOT/"build/ios-engine-iphoneos", ROOT/"build/ios-engine-iphoneos/.ios_device_gate_ok"),
 "full": GateSpec("full", ("misc/ios/build_check.sh", "--full"), ROOT/"build/ios-engine-iphoneos", ROOT/"build/ios-engine-iphoneos/.ios_full_gate_ok"),
 "engine": GateSpec("engine", ("misc/ios/build_check.sh", "--engine"), ROOT/"build/ios-engine-iphoneos", None),
 "shaders": GateSpec("shaders", ("misc/ios/build_check.sh", "--shaders"), ROOT/"build/ios-engine-iphoneos", None), }
def sha(path: Path) -> str:
 h=hashlib.sha256()
 with path.open("rb") as f:
  for block in iter(lambda:f.read(1<<20),b""): h.update(block)
 return h.hexdigest()
def git(root:Path,*args:str)->str:
 r=subprocess.run(("git","-C",str(root),*args),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL); return r.stdout.decode(errors="replace").strip() if r.returncode==0 else "unavailable"
def state(root:Path=ROOT)->dict[str,object]:
 status=subprocess.run(("git","-C",str(root),"status","--porcelain=v2","-z"),stdout=subprocess.PIPE,check=True).stdout
 diff=subprocess.run(("git","-C",str(root),"diff","--binary","HEAD"),stdout=subprocess.PIPE,check=True).stdout
 names=subprocess.run(("git","-C",str(root),"ls-files","--others","--exclude-standard","-z"),stdout=subprocess.PIPE,check=True).stdout.split(b"\0")
 untracked=[]
 for raw in names:
  if not raw: continue
  name=raw.decode("utf-8",errors="surrogateescape"); path=root/name; detail=path.lstat()
  if stat.S_ISLNK(detail.st_mode) or not stat.S_ISREG(detail.st_mode): raise RuntimeError(f"unsafe untracked path: {name}")
  untracked.append({"path":name,"sha256":sha(path)})
 return {"head":git(root,"rev-parse","--verify","HEAD"),"status_sha256":hashlib.sha256(status).hexdigest(),"diff_binary_head_sha256":hashlib.sha256(diff).hexdigest(),"untracked":sorted(untracked,key=lambda item:item["path"])}
def private_dir(path:Path)->None:
 target=Path(os.path.abspath(path))
 current=Path(target.anchor)
 for component in target.parts[1:]:
  current=current/component
  try: detail=current.lstat()
  except FileNotFoundError:
   current.mkdir(mode=0o700)
   detail=current.lstat()
  if stat.S_ISLNK(detail.st_mode) or not stat.S_ISDIR(detail.st_mode): raise RuntimeError("log directory is unsafe")
 detail=target.lstat()
 if stat.S_IMODE(detail.st_mode)!=0o700: raise RuntimeError("log directory must already be mode 0700")
def reserve_unique(path:Path,suffix:str)->tuple[Path,int]:
 for number in range(10000):
  candidate=path/f"gate-{time.time_ns()}-{os.getpid()}-{number}{suffix}"
  try: fd=os.open(candidate,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600); return candidate,fd
  except FileExistsError: continue
 raise RuntimeError("could not reserve unique log path")
def write_atomic(path:Path,data:bytes)->None:
 """Publish once: link() gives no-clobber final-name semantics."""
 temp,fd=reserve_unique(path.parent,".metadata-pending")
 try:
  offset=0
  while offset<len(data): offset+=os.write(fd,data[offset:])
  os.fsync(fd); os.close(fd); fd=-1
  os.link(temp,path)  # fails rather than overwriting an existing metadata path
 finally:
  if fd>=0: os.close(fd)
  try: temp.unlink()
  except FileNotFoundError: pass
def fd_sha256(descriptor:int)->str:
 h=hashlib.sha256(); offset=0
 while True:
  block=os.pread(descriptor,1<<20,offset)
  if not block: return h.hexdigest()
  h.update(block); offset+=len(block)
def excerpt_fd(descriptor:int)->str:
 pattern=re.compile(rb"\b(error|fatal|fail(?:ed|ure)?|exception|traceback|assert(?:ion)?|undefined symbols)\b",re.I); ring=[]
 offset=0; pending=b""
 while True:
  block=os.pread(descriptor,1<<20,offset)
  if not block: break
  offset+=len(block); pending+=block
  rows=pending.split(b"\n"); pending=rows.pop()
  for raw in rows:
   line=raw.decode(errors="replace").rstrip(); ring=(ring+[line])[-5:]
   if pattern.search(raw) and not re.search(r"^\s*(?:0 (?:failures?|errors?)|.*\bPASS\b.*)\s*$",line,re.I): return "\n".join(ring[-3:])[:1600]
 if pending: ring=(ring+[pending.decode(errors="replace").rstrip()])[-5:]
 return "\n".join(ring[:3])[:1600]
def bound_log_path(path:Path,descriptor:int)->bool:
 try: named=path.lstat(); opened=os.fstat(descriptor)
 except FileNotFoundError: return False
 return stat.S_ISREG(named.st_mode) and (named.st_dev,named.st_ino)==(opened.st_dev,opened.st_ino)
def feedback_module():
 """Load observation support without allowing a .pyc side effect in the repo."""
 path=ROOT/"misc/ios/test_feedback.py"; spec=importlib.util.spec_from_file_location(f"_openxray_feedback_{secrets.token_hex(8)}",path)
 if spec is None or spec.loader is None: raise RuntimeError("test feedback loader unavailable")
 prior=sys.dont_write_bytecode
 try:
  sys.dont_write_bytecode=True; module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
 finally: sys.dont_write_bytecode=prior
def feedback_context_fd(directory:Path, profile:str, run_id:str, nonce:str)->int:
 """Create an unlinked read-only mode-0600 context for one top-level Bash."""
 path=directory/f".gate-context-{os.getpid()}-{secrets.token_hex(12)}"
 descriptor=-1; reader=-1
 try:
  descriptor=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  payload=(f"{directory}\n{nonce}\n{run_id}\n{profile}\n").encode("utf-8")
  offset=0
  while offset<len(payload):
   written=os.write(descriptor,payload[offset:])
   if written<=0: raise OSError("zero context write")
   offset+=written
  os.fsync(descriptor)
  written_details=os.fstat(descriptor)
  named=path.lstat()
  reader=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
  read_details=os.fstat(reader)
  if (not stat.S_ISREG(written_details.st_mode) or written_details.st_uid!=os.getuid()
      or stat.S_IMODE(written_details.st_mode)!=0o600 or written_details.st_size!=len(payload)
      or (written_details.st_dev,written_details.st_ino)!=(named.st_dev,named.st_ino)
      or (read_details.st_dev,read_details.st_ino)!=(named.st_dev,named.st_ino)):
   raise RuntimeError("unsafe telemetry context file")
  os.unlink(path)
  os.close(descriptor); descriptor=-1
  return reader
 except BaseException:
  if reader>=0: os.close(reader)
  if descriptor>=0: os.close(descriptor)
  try: path.unlink()
  except FileNotFoundError: pass
  raise
def run(spec:GateSpec, log_dir:Path, command:Sequence[str]|None=None,
        telemetry:bool=True)->int:
 private_dir(log_dir); lock_path=log_dir/f".{hashlib.sha256(str(spec.build_tree).encode()).hexdigest()}.lock"; lock_fd=os.open(lock_path,os.O_WRONLY|os.O_CREAT,0o600); inspect_fd=-1
 try:
  try: fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError: print(f"GATE_LOG BUSY gate={spec.name} build_tree={spec.build_tree}",file=sys.stderr); return 75
  log,log_fd=reserve_unique(log_dir,".log"); inspect_fd=os.dup(log_fd); meta=log.with_suffix(".json"); before=state(); detail=log_dir/f"detail-{log.stem}"; private_dir(detail)
  selected=tuple(command) if command is not None else tuple(str(ROOT/item) if index == 0 and item.startswith("misc/") else item for index,item in enumerate(spec.command))
  if selected and selected[0].endswith(".sh"):
   selected=("/bin/bash",*selected)
  started=time.time()
  telemetry_dir=detail/"test-feedback"; telemetry_run=f"gate-{time.time_ns()}-{os.getpid()}"; telemetry_nonce=secrets.token_hex(32); feedback=None
  telemetry_ready=False
  if telemetry:
   try:
    feedback=feedback_module(); telemetry_ready=feedback.runtime_initialize(telemetry_dir,spec.name,telemetry_run,telemetry_nonce) is True
   except BaseException:
    feedback=None; telemetry_ready=False
  # Both modes remove Bash startup/tracing controls before Bash starts.  That
  # is deterministic gate safety rather than telemetry behaviour: a startup
  # hook or inherited ``allexport:xtrace`` state must never see an observer
  # context, and OFF has the identical startup environment.
  child_env={**os.environ,"OPENXRAY_GATE_DETAIL_DIR":str(detail)}
  for key in ("SHELLOPTS","BASHOPTS","PS4","BASH_XTRACEFD","BASH_ENV","ENV"):
   child_env.pop(key,None)
  for key in tuple(child_env):
   # Bash imports ``BASH_FUNC_name%%`` before the first line of a script.
   # They are executable startup state, not ordinary command environment, so
   # drop every dynamically named exported function in both telemetry modes.
   if (key.startswith("OPENXRAY_TEST_FEEDBACK_")
       or key in {"XRAY_FEEDBACK_CONTEXT_FD","XRAY_FEEDBACK_RAW_EVENT_FD"}
       or (key.startswith("BASH_FUNC_") and key.endswith("%%"))):
    child_env.pop(key,None)
  context_fd=-1
  trusted_shells={str(ROOT/"misc/ios/build_check.sh"),str(ROOT/"misc/ios/build_fast_device.sh")}
  if telemetry_ready and len(selected)>=2 and selected[0]=="/bin/bash" and selected[1] in trusted_shells:
   try:
    context_fd=feedback_context_fd(telemetry_dir,spec.name,telemetry_run,telemetry_nonce)
    child_env["XRAY_FEEDBACK_CONTEXT_FD"]=str(context_fd)
   except BaseException:
    telemetry_ready=False; feedback=None
  log_handle=os.fdopen(log_fd,"wb")
  try:
   child=subprocess.Popen(selected,cwd=ROOT,stdout=log_handle,stderr=subprocess.STDOUT,start_new_session=True,env=child_env,close_fds=True,pass_fds=(() if context_fd<0 else (context_fd,)))
  finally:
   if context_fd>=0: os.close(context_fd)
  forwarded={"code":None}
  def forward(signum,frame):
   forwarded["code"]=130 if signum==signal.SIGINT else 143
   try: os.killpg(child.pid,signum)
   except ProcessLookupError: pass
  old_int,old_term=signal.signal(signal.SIGINT,forward),signal.signal(signal.SIGTERM,forward)
  try: returned=child.wait()
  finally:
   log_handle.close()
   signal.signal(signal.SIGINT,old_int); signal.signal(signal.SIGTERM,old_term)
  code=forwarded["code"] if forwarded["code"] is not None else (128+(-returned) if returned<0 else returned); after=state(); mismatch=not bound_log_path(log,inspect_fd)
  stamp=None
  if mismatch: code=1
  if code==0 and spec.stamp is not None and spec.stamp.is_file() and not spec.stamp.is_symlink(): stamp={"path":str(spec.stamp),"sha256":sha(spec.stamp)}
  if telemetry_ready:
   try:
    feedback.runtime_finalize(telemetry_dir,spec.name,telemetry_run,telemetry_nonce,code)
   except BaseException: pass
  # Receipt v2 is an archival authorization contract.  Telemetry discovery is
  # deliberately private at detail_dir/test-feedback/run.json, never a field here.
  payload={"schema":"openxray.gate-log.v2","gate":spec.name,"started_unix":started,"ended_unix":time.time(),"exit_code":code,"before":before,"after":after,"log_path":None if mismatch else str(log),"log_sha256":None if mismatch else fd_sha256(inspect_fd),"log_identity_mismatch":mismatch,"detail_dir":str(detail),"underlying_stamp":stamp}
  write_atomic(meta,json.dumps(payload,sort_keys=True,separators=(",",":" )).encode()+b"\n")
  if code==0: print(f"GATE_LOG PASS gate={spec.name} log={log} metadata={meta}")
  elif mismatch: print(f"GATE_LOG FAIL gate={spec.name} exit={code} log=unavailable metadata={meta}",file=sys.stderr)
  else: print(f"GATE_LOG FAIL gate={spec.name} exit={code} log={log} metadata={meta}\n--- causal excerpt ---\n{excerpt_fd(inspect_fd)}",file=sys.stderr)
  return code
 finally:
  if inspect_fd>=0: os.close(inspect_fd)
  os.close(lock_fd)
def main()->int:
 parser=argparse.ArgumentParser(); parser.add_argument("--log-dir",default=os.environ.get("OPENXRAY_GATE_LOG_DIR",str(DEFAULT_LOG_DIR))); parser.add_argument("--telemetry",choices=("on","off"),default="on"); parser.add_argument("gate",choices=sorted(GATES)); args=parser.parse_args(); return run(GATES[args.gate],Path(args.log_dir),telemetry=args.telemetry=="on")
if __name__=="__main__": sys.exit(main())
