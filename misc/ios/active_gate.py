#!/usr/bin/env python3
"""Generate/check a deterministic, fail-closed OpenXRay active-gate cache."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = "openxray.active-gate.v2"
DEFAULT_BYTE_LIMIT, MIN_ESTIMATED_TOKENS, TARGET_ESTIMATED_TOKENS, MAX_ESTIMATED_TOKENS = 36_000, 6_000, 7_500, 9_000
SOURCES = ("doc/iOS-Port-Resume.md", "doc/iOS-Port-Plan.md", "doc/iOS-Port.md",
           "doc/iOS-Port-Journal.md", ".Codex/session-log.md")


def digest(value: bytes) -> str: return hashlib.sha256(value).hexdigest()
def stable(value: Any) -> bytes: return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
def estimate(value: bytes) -> int: return (len(value) + 3) // 4  # deterministic size estimate, not BPE
def fail(message: str) -> RuntimeError: return RuntimeError(message)


def require_mode(detail: os.stat_result, expected: int, label: str) -> None:
    actual = stat.S_IMODE(detail.st_mode)
    if actual != expected:
        raise fail(f"{label} mode must be {expected:04o}, got {actual:04o}")


def relative(root: Path, candidate: Path) -> tuple[str, ...]:
    root_text, candidate_text = os.path.abspath(root), os.path.abspath(candidate)
    if os.path.commonpath((root_text, candidate_text)) != root_text:
        raise fail(f"path is outside repo root: {candidate}")
    parts = Path(os.path.relpath(candidate_text, root_text)).parts
    if not parts or parts == (".",) or any(part in ("", ".", "..") for part in parts):
        raise fail(f"invalid repo-relative path: {candidate}")
    return parts


def open_root(root: Path) -> int:
    return os.open(os.path.abspath(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def secure_read(root: Path, rel: str) -> bytes:
    parts = Path(rel).parts
    if rel not in SOURCES or any(part in ("", ".", "..") for part in parts):
        raise fail(f"source is not allowlisted: {rel}")
    directory = open_root(root)
    try:
        for index, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if index < len(parts) - 1 else 0)
            descriptor = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = descriptor
        before = os.fstat(directory)
        if not stat.S_ISREG(before.st_mode): raise fail(f"source is not a regular file: {rel}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(directory, 1 << 20)
            if not chunk: break
            chunks.append(chunk)
        after = os.fstat(directory)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise fail(f"source changed while reading: {rel}")
        return b"".join(chunks)
    finally:
        os.close(directory)


def secure_parent(root: Path, parts: tuple[str, ...]) -> tuple[int, str]:
    if len(parts) < 2: raise fail("output must have a repo-relative parent")
    directory = open_root(root)
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, 0o700, dir_fd=directory)
                created = True
            except FileExistsError:
                created = False
            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory); directory = descriptor
            if created:
                os.fchmod(directory, 0o700)
        if not stat.S_ISDIR(os.fstat(directory).st_mode): raise fail(f"output parent is not a directory: {'/'.join(parts[:-1])}")
        require_mode(os.fstat(directory), 0o700, f"output parent {'/'.join(parts[:-1])}")
        try:
            existing = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            if stat.S_ISLNK(existing.st_mode): raise fail(f"output is a symlink: {'/'.join(parts)}")
            if not stat.S_ISREG(existing.st_mode): raise fail(f"output is not regular: {'/'.join(parts)}")
        except FileNotFoundError: pass
        return directory, parts[-1]
    except BaseException:
        os.close(directory); raise


def atomic_write(root: Path, parts: tuple[str, ...], value: bytes) -> None:
    directory, name = secure_parent(root, parts)
    temporary = f".{name}.tmp.{os.getpid()}.{os.urandom(6).hex()}"
    descriptor = -1
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(value): offset += os.write(descriptor, value[offset:])
        os.fsync(descriptor); os.close(descriptor); descriptor = -1
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        if descriptor >= 0: os.close(descriptor)
        try: os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError: pass
        os.close(directory)


def git_bytes(root: Path, *args: str) -> bytes:
    result = subprocess.run(("git", "-C", str(root), *args), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode: raise fail(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def untracked(root: Path) -> list[dict[str, str]]:
    paths = git_bytes(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    records: list[dict[str, str]] = []
    for raw in paths:
        if not raw: continue
        rel = raw.decode("utf-8", errors="surrogateescape")
        parts = Path(rel).parts
        # secure_read is intentionally source-only; untracked freshness permits
        # regular files but applies the same no-symlink-component traversal.
        directory = open_root(root)
        try:
            for index, part in enumerate(parts):
                fd = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if index < len(parts)-1 else 0), dir_fd=directory)
                os.close(directory); directory = fd
            detail = os.fstat(directory)
            if not stat.S_ISREG(detail.st_mode): raise fail(f"untracked path is not regular: {rel}")
            value = b""
            while True:
                chunk = os.read(directory, 1 << 20)
                if not chunk: break
                value += chunk
            records.append({"path": rel, "sha256": digest(value)})
        finally: os.close(directory)
    return sorted(records, key=lambda item: item["path"])


def freshness(root: Path) -> dict[str, Any]:
    status = git_bytes(root, "status", "--porcelain=v2", "-z")
    diff = git_bytes(root, "diff", "--binary", "HEAD")
    display = [row.decode("utf-8", errors="replace") for row in status.split(b"\0") if row]
    return {"branch": git_bytes(root, "rev-parse", "--abbrev-ref", "HEAD").strip().decode(),
            "head": git_bytes(root, "rev-parse", "--verify", "HEAD").strip().decode(),
            "status_porcelain_v2_sha256": digest(status), "diff_binary_head_sha256": digest(diff),
            "untracked_regular_files": untracked(root), "dirty_scope": display}


def clip(text: str, maximum: int) -> str:
    if len(text) <= maximum: return text.rstrip() + "\n"
    return text[:maximum - 31].rstrip() + "\n[bounded selection truncated]\n"
def tail_clip(text: str, maximum: int) -> str:
    """Keep complete newest lines within a bounded session-log selection."""
    normalized = text.rstrip() + "\n"
    if len(normalized) <= maximum:
        return normalized
    marker = "[bounded selection truncated; earlier session-log lines omitted]\n"
    if maximum <= len(marker):
        raise fail("session-tail byte limit is too small for truncation marker")
    selected: list[str] = []
    used = len(marker)
    for line in reversed(normalized.splitlines(keepends=True)):
        if used + len(line) > maximum:
            break
        selected.append(line)
        used += len(line)
    return marker + "".join(reversed(selected))
def section(text: str, pattern: str, maximum: int) -> str:
    lines = text.splitlines(keepends=True); start = next((i for i, line in enumerate(lines) if re.match(pattern, line)), None)
    if start is None: return "[no matching bounded section]\n"
    end = next((i for i in range(start+1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return clip("".join(lines[start:end]), maximum)
def active(plan: str) -> tuple[str, str, str]:
    lines = plan.splitlines(keepends=True); start = next((i for i, line in enumerate(lines) if re.match(r"^## [0-9]+\. ", line)), None)
    if start is None: raise fail("active Plan has no numbered task")
    title = lines[start][3:].strip(); found = re.search(r"\b(IOS-[A-Z0-9-]+)\b", title)
    if not found: raise fail(f"active task has no IOS task id: {title}")
    end = next((i for i in range(start+1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return found.group(1), title, clip("".join(lines[start:end]), 9_000)
def focus(plan: str) -> str:
    return section(plan, r"^\*\*Current focus:\*\*", 3_500)
def match(text: str, task: str, title: str, maximum: int, fallback: str) -> str:
    terms = [task.lower()] + [w.lower() for w in re.findall(r"[A-Za-z]{5,}", title)[:3]]; lines = text.splitlines(keepends=True)
    hit = next((i for i, line in enumerate(lines) if any(term in line.lower() for term in terms)), None)
    if hit is None: return section(text, fallback, maximum)
    start = max(0, hit - 2); end = min(len(lines), hit + 35)
    return clip("".join(lines[start:end]), maximum)
def journal_match(text: str, task: str, title: str) -> str:
    """Prefer the newest exact task-ID Journal entry over generic title words."""
    lines = text.splitlines(keepends=True)
    exact = [i for i, line in enumerate(lines) if re.search(r"(?<![A-Z0-9-])" + re.escape(task) + r"(?![A-Z0-9-])", line)]
    if not exact:
        return match(text, task, title, 4_500, r"^$")
    hit = exact[-1]
    start = next((i for i in range(hit, -1, -1) if lines[i].startswith("## ")), max(0, hit - 2))
    end = next((i for i in range(hit + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return clip("".join(lines[start:end]), 4_500)
def resume(text: str) -> str:
    return clip("".join(text.splitlines(keepends=True)[:72]) + "\n" + section(text, r"^## Still pending\s*$", 1_800) + section(text, r"^## Next slice\s*$", 1_800) + section(text, r"^## Safety\s*$", 1_800), 11_000)


def render(root: Path, output: tuple[str, ...], manifest: tuple[str, ...], limit: int) -> tuple[bytes, dict[str, Any]]:
    if not 0 < limit <= MAX_ESTIMATED_TOKENS * 4: raise fail(f"byte limit must be 1..{MAX_ESTIMATED_TOKENS * 4}")
    # Exactly one secure read per source: all selectors and source hashes use it.
    fresh_before = freshness(root)
    raw = {path: secure_read(root, path) for path in SOURCES}
    docs = {path: raw[path].decode("utf-8") for path in SOURCES}
    task, title, task_text = active(docs["doc/iOS-Port-Plan.md"])
    selected = {"resume": resume(docs["doc/iOS-Port-Resume.md"]), "plan_focus": focus(docs["doc/iOS-Port-Plan.md"]),
                "active_task": task_text, "current_facts": match(docs["doc/iOS-Port.md"], task, title, 5_000, r"^## Current product state\s*$"),
                "journal": journal_match(docs["doc/iOS-Port-Journal.md"], task, title),
                "session_tail": tail_clip(docs[".Codex/session-log.md"], 2_500),
                "gate_contracts": section(docs["doc/iOS-Port-Resume.md"], r"^## Validation contracts\s*$", 2_500)}
    source_map = {"resume":"doc/iOS-Port-Resume.md", "plan":"doc/iOS-Port-Plan.md", "canonical":"doc/iOS-Port.md", "journal":"doc/iOS-Port-Journal.md", "session":".Codex/session-log.md"}
    select_map = {"resume":selected["resume"], "plan":selected["plan_focus"]+selected["active_task"], "canonical":selected["current_facts"], "journal":selected["journal"], "session":selected["session_tail"]}
    source_manifest = [{"path": source_map[key], "source_sha256": digest(raw[source_map[key]]), "selected_sha256":digest(select_map[key].encode()), "selected_bytes":len(select_map[key].encode())} for key in ("resume","plan","canonical","journal","session")]
    fresh = freshness(root)
    if fresh != fresh_before: raise fail("repository changed while taking source snapshot")
    source_digest = digest(stable(source_manifest))
    body = ["# OpenXRay iOS active gate capsule", "", "Deterministic cache; run `active_gate.py check` before relying on it.", "", "## Identity", f"- Active task: `{task}` — {title}", f"- Branch: `{fresh['branch']}`", f"- HEAD: `{fresh['head']}`", f"- Source manifest SHA-256: `{source_digest}`", "", "## Dirty scope", *([f"- `{line}`" for line in fresh["dirty_scope"]] or ["- clean"]), ""]
    for label, key in (("Resume selection","resume"),("Plan current focus","plan_focus"),("Active task","active_task"),("Matching current facts","current_facts"),("Matching Journal evidence","journal"),("Session-log tail","session_tail"),("Gate contracts","gate_contracts")):
        body += [f"## {label}", selected[key].rstrip(), ""]
    body += ["## Evidence boundary", "- Local gates establish only their named host/static contract.", "- Simulator evidence is not iPhone, pixel, or performance proof.", "- Rendering correctness requires an on-device frame or numeric probe; never install after a stale matching gate.", "", "## Next step", "- Follow the first unresolved next action in the active Plan and preserve its phone/lease boundary.", ""]
    rendered = "\n".join(body).encode(); tokens = estimate(rendered)
    if len(rendered) > limit: raise fail(f"capsule exceeds byte limit: {len(rendered)} > {limit}")
    if not MIN_ESTIMATED_TOKENS <= tokens <= MAX_ESTIMATED_TOKENS:
        raise fail(f"capsule estimated-token limit must be {MIN_ESTIMATED_TOKENS}..{MAX_ESTIMATED_TOKENS}, got {tokens}")
    metadata = {"schema":SCHEMA, "token_estimator":"ceil(utf8_bytes/4); not a tokenizer", "min_estimated_tokens":MIN_ESTIMATED_TOKENS, "target_estimated_tokens":TARGET_ESTIMATED_TOKENS, "max_estimated_tokens":MAX_ESTIMATED_TOKENS, "byte_limit":limit, "output_bytes":len(rendered), "estimated_tokens":tokens, "output_sha256":digest(rendered), "source_manifest":source_manifest, "source_manifest_sha256":source_digest, "freshness":fresh, "active_task":{"id":task,"title":title}}
    return rendered, metadata


def choose(root: Path, optional: str | None, default: str) -> tuple[str, ...]:
    return relative(root, Path(optional) if optional else root / default)
def secure_unlink(root: Path, parts: tuple[str, ...]) -> None:
    directory, name = secure_parent(root, parts)
    try: os.unlink(name, dir_fd=directory)
    except FileNotFoundError: pass
    finally: os.close(directory)
def previous(root: Path, parts: tuple[str, ...]) -> bytes | None:
    try: return secure_read_output(root, parts)
    except FileNotFoundError: return None
def publish_pair(root: Path, output: tuple[str, ...], manifest: tuple[str, ...], rendered: bytes, metadata: bytes) -> None:
    """Restore the complete previous pair if the second publication fails."""
    old_output, old_manifest = previous(root, output), previous(root, manifest)
    try:
        atomic_write(root, output, rendered)
        atomic_write(root, manifest, metadata)
    except BaseException:
        try:
            if old_output is None: secure_unlink(root, output)
            else: atomic_write(root, output, old_output)
            if old_manifest is None: secure_unlink(root, manifest)
            else: atomic_write(root, manifest, old_manifest)
        except BaseException as rollback:
            raise fail(f"capsule pair publication and rollback failed: {rollback}") from rollback
        raise
def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[2]); parser.add_argument("--output"); parser.add_argument("--manifest"); parser.add_argument("--byte-limit", type=int, default=DEFAULT_BYTE_LIMIT); parser.add_argument("command", choices=("generate","check")); args = parser.parse_args()
    try:
        root = Path(args.repo_root); output = choose(root, args.output, ".Codex/runtime/active-gate.md"); manifest = choose(root, args.manifest, ".Codex/runtime/active-gate.manifest.json")
        if output == manifest: raise fail("output and manifest must differ")
        if args.command == "generate":
            rendered, metadata = render(root, output, manifest, args.byte_limit)
            # Render fully before publishing either file: a failed generation cannot clobber a good cache.
            publish_pair(root, output, manifest, rendered, stable(metadata))
            print(f"ACTIVE_GATE PASS generated={root.joinpath(*output)} manifest={root.joinpath(*manifest)} bytes={len(rendered)} estimated_tokens={estimate(rendered)}"); return 0
        manifest_raw = secure_read_output(root, manifest); stored = json.loads(manifest_raw)
        if stored.get("schema") != SCHEMA or not isinstance(stored.get("byte_limit"), int): raise fail("unsupported or malformed capsule manifest")
        rendered, expected = render(root, output, manifest, stored["byte_limit"]); actual = secure_read_output(root, output)
        if actual != rendered or stored != expected or digest(actual) != stored.get("output_sha256"): raise fail("capsule is stale: source, freshness, output hash, or manifest differs")
        if len(actual) > stored["byte_limit"] or not MIN_ESTIMATED_TOKENS <= estimate(actual) <= MAX_ESTIMATED_TOKENS: raise fail("capsule size limit mismatch")
        print(f"ACTIVE_GATE PASS checked={root.joinpath(*output)} manifest={root.joinpath(*manifest)} bytes={len(actual)} estimated_tokens={estimate(actual)}"); return 0
    except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"ACTIVE_GATE FAIL: {error}", file=sys.stderr); return 1


def secure_read_output(root: Path, parts: tuple[str, ...]) -> bytes:
    if len(parts) < 2: raise fail("capsule output must have a repo-relative parent")
    directory = open_root(root)
    try:
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory); directory = fd
        parent_before = os.fstat(directory)
        if not stat.S_ISDIR(parent_before.st_mode): raise fail("capsule output parent is not a directory")
        require_mode(parent_before, 0o700, f"capsule output parent {'/'.join(parts[:-1])}")
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        os.close(directory); directory = fd
        before = os.fstat(directory)
        if not stat.S_ISREG(before.st_mode): raise fail("capsule output is not regular")
        require_mode(before, 0o600, "capsule output")
        value = b""
        while True:
            chunk = os.read(directory, 1 << 20)
            if not chunk: break
            value += chunk
        after = os.fstat(directory)
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns): raise fail("capsule changed while reading")
        return value
    finally: os.close(directory)
if __name__ == "__main__": sys.exit(main())
