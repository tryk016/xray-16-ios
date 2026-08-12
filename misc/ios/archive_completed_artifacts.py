#!/usr/bin/env python3
"""Crash-safe, descriptor-bound archival of completed OpenXRay artifacts.

Production paths, the eight reviewed candidates, the exact APFS copy overlay,
the immutable 009a baseline and the one exact 009b pretruncate-open recovery
are fixed.  Mutation is enabled only when the allowlist and all complete
overlay/recovery tuples match the hard-coded Sol-reviewed SHA-256 digest; the
public CLI remains dry-run unless ``--apply`` is explicit.  One
descriptor-bound OS lock serializes cooperating OpenXRay archive tools across
source binding, quarantine, retirement and durable receipts.  This boundary
does not claim protection from arbitrary same-UID or root processes that
deliberately ignore the lock.  Tests inject private roots without touching the
real DevArchive.  The separate historical command has one independently
digested, read-only prepared-cache exception for the reviewed removal of the
root ``.DS_Store``; no mutation-capable verifier shares that exception.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import hmac
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Callable


SCHEMA = "openxray-devarchive-v2"
REVIEW_POLICY_VERSION = 1
ALLOWLIST_VERSION = 2
RETAIL_INVENTORY_VERSION = 1
RETAIL_INVENTORY_COUNT = 790
RETAIL_INVENTORY_PATHS_SHA256 = "d132d7a7073208e2fec5a2f566c1fdcf08e5cfd00d07cb11a6574c90d65fe139"
RETAIL_INVENTORY_CLASSIFIED_SHA256 = "7e5cd964fa7101b877291f03b9ab919f27fbe031b134c9b4167205a1e67a8ea3"
PROVENANCE_OVERLAY_POLICY_VERSION = 1
PROVENANCE_OVERLAY_XATTR = "com.apple.provenance"
PROVENANCE_OVERLAY_VALUE = "AQIAEL8iEDENGM4="
PROVENANCE_OVERLAY_CANDIDATE_ID = "backup-device-retail-20260808-185559"
PROVENANCE_OVERLAY_ENTRY_COUNT = 827
PROVENANCE_OVERLAY_FILE_COUNT = 790
PROVENANCE_OVERLAY_DIRECTORY_COUNT = 37
PROVENANCE_OVERLAY_INHERITED_COUNT = 1
PROVENANCE_OVERLAY_ADDED_COUNT = 826
PROVENANCE_OVERLAY_ADDED_PATHS_SHA256 = \
    "4c98aa93328f80bc7c12d43075ce79c6cac636c9bb8f65d56bb445cb02698772"
PROVENANCE_OVERLAY_SOURCE_TREE_SHA256 = \
    "c35d1919c17b5164b0174e475c118e1039f0aa6238b2be4951b31885811f1f85"
PROVENANCE_OVERLAY_PHYSICAL_TREE_SHA256 = \
    "929ddd05bd7b6a33f2729074be2847c38d05b5f54e230d08e4f06b694af110ac"
PROVENANCE_OVERLAY_RECOVERY_TRANSACTION_ID = "5beca2ef4f034286be6299d6d9a2bdba"
PROVENANCE_OVERLAY_RECOVERY_CANDIDATE_SHA256 = \
    "595ace2d91fa81deac1ebc82be6ba118bb6aae1d769576e9b130628426f68a15"
PROVENANCE_OVERLAY_RECOVERY_COPY_COMPLETE_SHA256 = \
    "16933a61ca03665a57136b72762a30169ddfd4be5503d02fb42b80b3414b4134"
PROVENANCE_OVERLAY_RECOVERY_PHYSICAL_MANIFEST_SHA256 = \
    "3b457eba2ab83f5d6709ac9dba687d2b6e00a560ad0e3de11ed87880fcf15cd1"
SOURCE_ROOT_OVERLAY_POLICY_VERSION = 1
SOURCE_ROOT_OVERLAY_TRANSACTION_ID = "5beca2ef4f034286be6299d6d9a2bdba"
SOURCE_ROOT_OVERLAY_CANDIDATE_ID = "backup-device-retail-20260808-185559"
SOURCE_ROOT_OVERLAY_PATH = \
    "/Users/patryk/openxray-handoff/device-retail-backup-20260808-185559"
SOURCE_ROOT_OVERLAY_IDENTITY = (16777234, 11967414)
SOURCE_ROOT_OVERLAY_PARENT_IDENTITY = (16777234, 2412323)
SOURCE_ROOT_OVERLAY_ENTRY_COUNT = 827
SOURCE_ROOT_OVERLAY_FILE_COUNT = 790
SOURCE_ROOT_OVERLAY_DIRECTORY_COUNT = 37
SOURCE_ROOT_OVERLAY_ADDED_COUNT = 1
SOURCE_ROOT_OVERLAY_ADDED_PATHS_SHA256 = \
    "3c3ad4c60a7837b229ff40c20b386b83eec75635f6fc5c3b8a717420b288f2aa"
SOURCE_ROOT_OVERLAY_SEMANTIC_TREE_SHA256 = \
    "c35d1919c17b5164b0174e475c118e1039f0aa6238b2be4951b31885811f1f85"
SOURCE_ROOT_OVERLAY_SEMANTIC_MANIFEST_SHA256 = \
    "d318d2965bd448f5cd796ff916c6e00f248cb3c5ffaad323f516a593fe564fc9"
SOURCE_ROOT_OVERLAY_PHYSICAL_TREE_SHA256 = \
    "01204b23867dba9103f76f8cc622de7ea4ae54df8a431238ba3a6e4c8676c69f"
SOURCE_ROOT_OVERLAY_PHYSICAL_MANIFEST_SHA256 = \
    "eb27e257160d5763e0becd8b45c0149322ea8cf5c2373b7e07f7f906f6320a5c"
SOURCE_ROOT_OVERLAY_LEGACY_DESTINATION_AUTHORIZATION_SHA256 = \
    "d9390886fa20331a0d9562bac6522d3315b09567cc78ac9a4954f9633e5b0d77"
SOURCE_ROOT_OVERLAY_HISTORICAL_AUTHORIZATION_SHA256 = \
    "d0f459529a8abb9217e59de8094cc94e3dc198d315a0a62607695e048771191f"
SOURCE_ROOT_OVERLAY_LEGACY_PREFIX = (
    ("000-candidate.json", "595ace2d91fa81deac1ebc82be6ba118bb6aae1d769576e9b130628426f68a15"),
    ("001-copy-started.json", "0fff1402028f0c9bbd8eac8634e28d5ce3df71f9ef0b0a4c376cdfd4e57c1dfa"),
    ("002-copy-complete.json", "16933a61ca03665a57136b72762a30169ddfd4be5503d02fb42b80b3414b4134"),
    ("003-verified.json", "a7a6acea4dc0edb9b0400459cb4a7b24325b6065052de79f668246d50bb604ed"),
    ("004-published.json", "8ac78d8d30f512be968e1977d0815218609d498eac1ce5cd9a799efea0a579a7"),
    ("005-manifest-published.json", "e80ef1246a280846366401c95869f8aa50bc571d9ca3bbfb95699451b50c9d1c"),
    ("006-second-copy-proof.json", "ddc6505275ae4639e789208eca48b02434d7ae1024fdf057c2feb92132ca164a"),
    ("007-delete-intent.json", "0331f1d91ec12b1f723fa49d28634809aa70769a5a766ae3587aea677f17cdfd"),
)
SOURCE_ROOT_OVERLAY_LEGACY_PREFIX_SHA256 = \
    "126d465f070117b4f4980790c59f28bd64f1b1bbc7628a469dfb0b66f0822f39"
SOURCE_ROOT_OVERLAY_LEGACY_005 = {
    "schema": SCHEMA,
    "stage": "manifest-published",
    "transaction_id": SOURCE_ROOT_OVERLAY_TRANSACTION_ID,
    "external_manifest": f"{SOURCE_ROOT_OVERLAY_TRANSACTION_ID}.json",
    "external_manifest_sha256":
        "92c542d4ebae5f725c23fa7523804b080dd45c71c00af408a3d446214f7ae9e5",
}
SOURCE_ROOT_OVERLAY_LEGACY_006_CANONICAL = (
    '{"external_proof":"5beca2ef4f034286be6299d6d9a2bdba-second-copy-proof.json",'
    '"external_proof_sha256":"86c0de066dd45c51e1250a078de171ecbf5f3df101d79a4cd2f21872bf45799a",'
    '"proof_hash":"5a9eea54d9004f91c9ccc8aca22b3a08a24ddda397ce2bd314035debb2687a56",'
    '"proof_kind":"fixed-prepared-retail","schema":"openxray-devarchive-v2",'
    '"stage":"second-copy-proof","transaction_id":"5beca2ef4f034286be6299d6d9a2bdba"}\n'
).encode("ascii")
RETIREMENT_OVERLAY_BASELINE_POLICY_VERSION = 1
RETIREMENT_OVERLAY_BASELINE_SCHEMA = \
    "openxray.retirement-overlay-baseline.v1"
RETIREMENT_OVERLAY_BASELINE_RECORD = \
    "009a-retirement-overlay-baseline.json"
RETIREMENT_OVERLAY_BASELINE_008_SHA256 = \
    "9b0693a18c51d95ca90b08a4ccc4df1b692d6a7c32b15332bae99426a44d6b25"
RETIREMENT_OVERLAY_BASELINE_009_SHA256 = \
    "30de606ee732db5a8b578e7a838c5da311138ae323b412d74ce2de88944d6f15"
RETIREMENT_OVERLAY_IMMUTABLE_MANIFEST_SHA256 = \
    "eb27e257160d5763e0becd8b45c0149322ea8cf5c2373b7e07f7f906f6320a5c"
RETIREMENT_OVERLAY_IMMUTABLE_TREE_SHA256 = \
    "01204b23867dba9103f76f8cc622de7ea4ae54df8a431238ba3a6e4c8676c69f"
RETIREMENT_OVERLAY_CURRENT_MANIFEST_SHA256 = \
    "5d10c29acce200c78433006c9e39003e383c805f99a7e05036baee4d58f9cad4"
RETIREMENT_OVERLAY_CURRENT_TREE_SHA256 = \
    "d129322749104af3ddcdfb23fc5f9d5306e6c87f68715805db632f43964a8895"
RETIREMENT_OVERLAY_CHANGED_PATH = ".DS_Store"
RETIREMENT_OVERLAY_CHANGED_IDENTITY = (16777234, 25412239)
RETIREMENT_OVERLAY_FINDERINFO_VALUE = \
    "ICAgICAgICBAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
RETIREMENT_OVERLAY_CHANGED_PATHS_SHA256 = \
    "958f6a3ef1561f4a7c48e00d9f31a7ef7ef451f082e67da2dca1d78eab7b4632"
RETIREMENT_OVERLAY_ELIGIBILITY_VERSION = 1
RETIREMENT_OVERLAY_ELIGIBLE_COUNT = 788
RETIREMENT_OVERLAY_ELIGIBLE_PATHS_SHA256 = \
    "777ecef351ba95482138a0d175185213a075f971f4e63b5032bd34d6c837b08c"
RETIREMENT_OVERLAY_INHERITED_PATHS_SHA256 = \
    "b6ecc67e2e8ca0030b89b37a5c2e8c5b800da07e6accedf2c12f31f1c71b527e"
RETIREMENT_PRETRUNCATE_OPEN_POLICY_VERSION = 1
RETIREMENT_PRETRUNCATE_OPEN_SCHEMA = \
    "openxray.retirement-pretruncate-open-overlay.v1"
RETIREMENT_PRETRUNCATE_OPEN_RECORD = \
    "009b-retirement-pretruncate-open-overlay.json"
RETIREMENT_PRETRUNCATE_OPEN_008_SHA256 = \
    "9b0693a18c51d95ca90b08a4ccc4df1b692d6a7c32b15332bae99426a44d6b25"
RETIREMENT_PRETRUNCATE_OPEN_009_SHA256 = \
    "30de606ee732db5a8b578e7a838c5da311138ae323b412d74ce2de88944d6f15"
RETIREMENT_PRETRUNCATE_OPEN_009A_SHA256 = \
    "ec677fb39ba5fff06d9dc82964ad1874917d61522a3a60bc0dc41095c4804653"
RETIREMENT_PRETRUNCATE_OPEN_009A_MANIFEST_SHA256 = \
    "5d10c29acce200c78433006c9e39003e383c805f99a7e05036baee4d58f9cad4"
RETIREMENT_PRETRUNCATE_OPEN_009A_TREE_SHA256 = \
    "d129322749104af3ddcdfb23fc5f9d5306e6c87f68715805db632f43964a8895"
RETIREMENT_PRETRUNCATE_OPEN_HISTORICAL_009A_AUTH_SHA256 = \
    "114ae1731dfcf921b79ff096602d7f12219b3083e03d8c358dab792d20f6107e"
RETIREMENT_PRETRUNCATE_OPEN_GATE_B_SOURCE_SHA256 = \
    "7728fb7a14bfea604374a0aab8fb61e436f921026aa372b395c477e4991cf5b5"
RETIREMENT_PRETRUNCATE_OPEN_GATE_B_STAMP_SHA256 = \
    "d6ab2b77ca4d6cab9d09863cd66def26285c9ff1903de04e73357dece11c277a"
RETIREMENT_PRETRUNCATE_OPEN_BASE_MANIFEST_SHA256 = \
    "d882cd09d874eeda63e31b5c8ef71568c5f73851e545290294949e77c6ff529b"
RETIREMENT_PRETRUNCATE_OPEN_BASE_TREE_SHA256 = \
    "bfd450867421647baec5453864b42ab2f762249bfc311769162e60fe2e4b6623"
RETIREMENT_PRETRUNCATE_OPEN_CHANGED_PATH = "_appdata_/autoinput_ack.txt"
RETIREMENT_PRETRUNCATE_OPEN_CHANGED_IDENTITY = (16777234, 11969264)
RETIREMENT_PRETRUNCATE_OPEN_CHANGED_SIZE = 46
RETIREMENT_PRETRUNCATE_OPEN_CHANGED_SHA256 = \
    "6aee24911d1acd2de7bd092461007f2ca66d3689b5c6600579428a2861901638"
RETIREMENT_PRETRUNCATE_OPEN_CHANGED_MODE = 0o644
RETIREMENT_PRETRUNCATE_OPEN_CHANGED_MTIME_NS = 1785877310000000000
RETIREMENT_PRETRUNCATE_OPEN_CHANGED_PATHS_SHA256 = \
    "6fff2effcd446d05d0ae67a8facf0fb78e782f1f717977b111aed9077aa06d81"
RETIREMENT_PRETRUNCATE_OPEN_ELIGIBLE_COUNT = 787
RETIREMENT_PRETRUNCATE_OPEN_ELIGIBLE_PATHS_SHA256 = \
    "dc8d7a5b2c5b995979699e7f55628f5de0a1e18e8167804852929a600fb73da4"
RETIREMENT_PRETRUNCATE_OPEN_INHERITED_COUNT = 4
RETIREMENT_PRETRUNCATE_OPEN_INHERITED_PATHS_SHA256 = \
    "16e954228ab3ecc6df6afc4e61c9cb1f834a1cd0cafde88a9e84e176ed49afeb"
HISTORICAL_COMPLETED_POLICY_VERSION = 1
HISTORICAL_COMPLETED_STAGE_SHA256 = (
    ("000-candidate.json",
     "595ace2d91fa81deac1ebc82be6ba118bb6aae1d769576e9b130628426f68a15"),
    ("001-copy-started.json",
     "0fff1402028f0c9bbd8eac8634e28d5ce3df71f9ef0b0a4c376cdfd4e57c1dfa"),
    ("002-copy-complete.json",
     "16933a61ca03665a57136b72762a30169ddfd4be5503d02fb42b80b3414b4134"),
    ("003-verified.json",
     "a7a6acea4dc0edb9b0400459cb4a7b24325b6065052de79f668246d50bb604ed"),
    ("004-published.json",
     "8ac78d8d30f512be968e1977d0815218609d498eac1ce5cd9a799efea0a579a7"),
    ("005-manifest-published.json",
     "e80ef1246a280846366401c95869f8aa50bc571d9ca3bbfb95699451b50c9d1c"),
    ("006-second-copy-proof.json",
     "ddc6505275ae4639e789208eca48b02434d7ae1024fdf057c2feb92132ca164a"),
    ("007-delete-intent.json",
     "0331f1d91ec12b1f723fa49d28634809aa70769a5a766ae3587aea677f17cdfd"),
    ("008-source-quarantined.json",
     "9b0693a18c51d95ca90b08a4ccc4df1b692d6a7c32b15332bae99426a44d6b25"),
    ("009-retirement-started.json",
     "30de606ee732db5a8b578e7a838c5da311138ae323b412d74ce2de88944d6f15"),
    ("009a-retirement-overlay-baseline.json",
     "ec677fb39ba5fff06d9dc82964ad1874917d61522a3a60bc0dc41095c4804653"),
    ("009b-retirement-pretruncate-open-overlay.json",
     "c98e6f3d75da7d7bd39edceb169c86034729298e09d3feaaa26af966c26c87c4"),
    ("010-source-deleted.json",
     "c6663df763f5a7b7a237e6a2b998cf9a9e207216b5f88ea81ce2aa498cd10812"),
    ("011-deletion-receipt.json",
     "8da8e9c44f41b595c943c6ef550cc90afae596ff1d7ecaf7aa39b7ffbcff5bb6"),
)
HISTORICAL_COMPLETED_GATE_PROOFS = (
    ("gate-1786541795853451000-5335-0.json",
     "723355598ddbb043d1c37e09f7b5dd8a332a20e7483780522a61538a5f3e29fb",
     1786542482.8174772,
     "d2d39c2693b8d9ed810a1d90ab70a3649c14d7c3bedac0eb78b52b5cb293b830",
     "c633b0cd5c7c199d6dafe1927bc1e71b0a8958c00316dfa2df9ec8c148b1a098",
     "54ad2d9662230750bb000c07451b2a576013e1ce123fd9f73dd4ffdc08a689d8"),
    ("gate-1786553042910942000-1324-0.json",
     "6098d6509d7049354736a17e01284f792544cc7e0eb71b28ffd95d35cd7b87ff",
     1786553786.230761,
     "25c4bd3f357ecef708a5c081da23e25bcc4225966503b2e7829be82adb0a79a0",
     "6ff212df8c0c74890ed5e376381bd2f57d8258c9253a130a23439c5920048f82",
     "5c85b843042e0e1e1ac0de17ac761d84d739ca1d1ce6ee633a69d323c1aa0536"),
    ("gate-1786558567116907000-55791-0.json",
     "ca314eb3612fd1c9496403aa1ca6fe34839f3decdb37195a7f89b79d76dcd13c",
     1786559274.418171,
     "7728fb7a14bfea604374a0aab8fb61e436f921026aa372b395c477e4991cf5b5",
     "d6ab2b77ca4d6cab9d09863cd66def26285c9ff1903de04e73357dece11c277a",
     "153ca510aebe1a369e53bcee059e04f2ec03b82ede93db80ffa7d7b3069f9fb7"),
    ("gate-1786563440297027000-96172-0.json",
     "ccf74ad89ad6203521289eba79bb94b98f0759887f4cb6e25676d1a9e6caaeca",
     1786564226.6601942,
     "f8727806510005248fb9b92e2c7075fd5c91f66a82af451adfe8baa2d37e587f",
     "d9cfba1057e29a05fa10937c48da0c43bc7cb896a0b315af44634521d4cc0906",
     "16f14682fce0287d842108347fbddc8f28b2708dba59eaebf2ff2242ba0b2894"),
)
HISTORICAL_COMPLETED_POLICY_AUTHORIZATION_SHA256 = \
    "26e50fc0f283ab489d5a2f7d3351f26cafee388e793da8a75335eea705d9c512"
HISTORICAL_PREPARED_OVERLAY_POLICY_VERSION = 1
HISTORICAL_PREPARED_OVERLAY_OLD_MANIFEST_SHA256 = \
    "1f38846d7a9a50d9e9ea567bb304bedb7d3da6fb6bf5675121d85b8a61363db8"
HISTORICAL_PREPARED_OVERLAY_OLD_TREE_SHA256 = \
    "9b4eacff01f6e0c7f528c0252e208641388fe35124ebea69c90c4cc8219b2db8"
HISTORICAL_PREPARED_OVERLAY_NEW_MANIFEST_SHA256 = \
    "d394d33081c27952f48f0104dd45bb620df39d00cab8eec0ed5d2af8fa492d33"
HISTORICAL_PREPARED_OVERLAY_NEW_TREE_SHA256 = \
    "a1ad94aba8b7b661e1ac06d81bb7e6f97578c29962c9c0d933b7d5476693f6c4"
HISTORICAL_PREPARED_OVERLAY_REMOVED_PATH = ".DS_Store"
HISTORICAL_PREPARED_OVERLAY_REMOVED_RECORD_SHA256 = \
    "b9fd2b91c14faf8fd177203ed08ca8712aebf27dafc76eaf05b04eb23ffc6fcf"
HISTORICAL_PREPARED_OVERLAY_REMOVED_CONTENT_SHA256 = \
    "bc04efaeb5796541b3b2c140daffe12da95facc5d65d08911504c49da7c7d463"
HISTORICAL_PREPARED_OVERLAY_REMOVED_FINDERINFO = \
    "ICAgICAgICBAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
HISTORICAL_PREPARED_OVERLAY_OLD_ROOT_RECORD_SHA256 = \
    "2e95424962edee2e14582cd362aab57fdcbc02c074d9e90b639dd579797cd65f"
HISTORICAL_PREPARED_OVERLAY_NEW_ROOT_RECORD_SHA256 = \
    "1ab74e4f4875987069a15f66135199935f0a1bab99ea055a6bbac9e1cf4cb39f"
HISTORICAL_PREPARED_OVERLAY_UNCHANGED_RECORDS_SHA256 = \
    "0e3c8d4cefc811cbc2be15115967c64d72062b9a7ba18ca07673d9bf2f6263c9"
# Filled only by an explicit Sol-reviewed policy update.  It binds the exact
# metadata-only historical exception below to the independently authorized
# completed-retirement capability; it is not a mutation authorization.
HISTORICAL_PREPARED_OVERLAY_AUTHORIZATION_SHA256 = \
    "6b721efc3c0d409871caf2c50d40ffb5ebae8c03501ed610d734b17f828cd595"
_NO_BYTECODE_IMPORT_LOCK = threading.RLock()
REPO_ROOT = Path(__file__).resolve().parents[2]
QUEUE_ROOT = Path("/Users/patryk/openxray-handoff/archive-queue")
GATE_LOG_ROOT = Path("/Users/patryk/openxray-handoff/gate-logs")
FULL_GATE_STAMP = REPO_ROOT / "build/ios-engine-iphoneos/.ios_full_gate_ok"
GATE_MAX_AGE_SECONDS = 24 * 60 * 60
RETAIL_BACKUP_PATH = Path(
    "/Users/patryk/openxray-handoff/device-retail-backup-20260808-185559")
RETAIL_BACKUP_MANIFEST_PATH = Path(
    "/Users/patryk/openxray-handoff/device-retail-backup-20260808-185559.manifest")
RETAIL_PREPARED_ROOT = Path(
    "/Users/patryk/openxray-handoff/retail-prepared-20260810-211114")
VOLUME_MOUNT = Path("/Volumes/DevArchive")
VOLUME_NAME = "DevArchive"
VOLUME_UUID = "08C7CE36-A537-47DD-B8CF-401A6A8A89E1"
ARCHIVE_ROOT = "OpenXRay"
MANIFEST_ROOT = "transfer-manifests"
RECORD_RESIDUE = ".record-residue"
CATEGORY_ROOTS = {
    "completed-evidence": "completed-evidence",
    "archives": "archives",
    "old-projects": "old-projects",
    "backups": "backups",
}
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
O_SYMLINK = getattr(os, "O_SYMLINK", 0x00200000 if sys.platform == "darwin" else 0)
RENAME_EXCL = 0x00000004
ACL_TYPE_EXTENDED = 0x00000100
RECORD_STAGES = (
    "candidate", "copy-started", "copy-complete", "verified", "published",
    "manifest-published", "second-copy-proof", "delete-intent", "source-quarantined",
    "retirement-started", "source-deleted", "deletion-receipt",
)
RECORD_NAMES = {stage: f"{index:03d}-{stage}.json"
                for index, stage in enumerate(RECORD_STAGES)}
TRANSACTION_RECORD_NAMES = frozenset(
    (*RECORD_NAMES.values(), RETIREMENT_OVERLAY_BASELINE_RECORD,
     RETIREMENT_PRETRUNCATE_OPEN_RECORD))
PARTIAL_RECORD_RE = re.compile(r"^\.partial-([a-z0-9][a-z0-9.-]*)-([0-9a-f]{32})$")
EXTERNAL_RECORD_RE = re.compile(
    r"^[0-9a-f]{32}(?:-source-deletion|-second-copy-proof)?\.json$")
COPY_RESIDUE_RE = re.compile(r"^\.copy-residue-([0-9a-f]{32})$")
GATE_RECEIPT_RE = re.compile(r"^gate-[0-9]+-[0-9]+-[0-9]+\.json$")
CURRENT_GATE_RECEIPT_SCHEMA = "openxray.gate-log.v2"
CURRENT_GATE_RECEIPT_FIELDS = frozenset({
    "schema", "gate", "started_unix", "ended_unix", "exit_code",
    "before", "after", "log_path", "log_sha256",
    "log_identity_mismatch", "detail_dir", "underlying_stamp",
})
ARCHIVER_LOCK_CONTRACT = (
    "The OS lock coordinates cooperating OpenXRay archive tools. Arbitrary "
    "same-UID or root mutation outside that lock is outside the threat model."
)


class ArchiveError(RuntimeError):
    pass


class ArchiveBusy(ArchiveError):
    """A cooperating archiver already owns the shared threat boundary."""


class DeferredVolume(ArchiveError):
    pass


class InjectedCrash(ArchiveError):
    """Test-only interruption at an explicitly named durable boundary."""


@dataclass(frozen=True)
class CandidateSpec:
    ident: str
    source: Path
    category: str
    data_class: str
    deletion_rule: str


@dataclass(frozen=True)
class ProvenanceOverlayPolicy:
    version: int
    xattr_name: str
    xattr_value: str
    candidate_id: str
    allowlist_version: int
    entry_count: int
    file_count: int
    directory_count: int
    inherited_count: int
    added_count: int
    added_paths_sha256: str
    source_tree_sha256: str
    physical_tree_sha256: str


@dataclass(frozen=True)
class SourceRootOverlayPolicy:
    version: int
    xattr_name: str
    xattr_value: str
    transaction_id: str
    candidate_id: str
    source_path: str
    source_identity: tuple[int, int]
    source_parent_identity: tuple[int, int]
    allowlist_version: int
    entry_count: int
    file_count: int
    directory_count: int
    added_count: int
    added_paths_sha256: str
    semantic_tree_sha256: str
    semantic_manifest_sha256: str
    physical_tree_sha256: str
    physical_manifest_sha256: str


@dataclass(frozen=True)
class RetirementOverlayBaselinePolicy:
    version: int
    schema: str
    record_name: str
    transaction_id: str
    candidate_id: str
    source_path: str
    source_identity: tuple[int, int]
    source_parent_identity: tuple[int, int]
    allowlist_version: int
    source_quarantined_sha256: str
    retirement_started_sha256: str
    immutable_manifest_sha256: str
    immutable_tree_sha256: str
    current_manifest_sha256: str
    current_tree_sha256: str
    changed_path: str
    changed_identity: tuple[int, int]
    changed_old_xattrs: tuple[tuple[str, str], ...]
    changed_new_xattrs: tuple[tuple[str, str], ...]
    changed_count: int
    changed_paths_sha256: str
    entry_count: int
    file_count: int
    directory_count: int
    symlink_count: int
    eligibility_version: int
    eligible_count: int
    eligible_paths_sha256: str
    inherited_paths_sha256: str
    runtime_xattr_name: str
    runtime_xattr_value: str


@dataclass(frozen=True)
class RetirementPretruncateOpenPolicy:
    version: int
    schema: str
    record_name: str
    transaction_id: str
    candidate_id: str
    source_path: str
    source_identity: tuple[int, int]
    source_parent_identity: tuple[int, int]
    allowlist_version: int
    source_quarantined_sha256: str
    retirement_started_sha256: str
    retirement_overlay_baseline_sha256: str
    baseline_manifest_sha256: str
    baseline_tree_sha256: str
    historical_009a_authorization_sha256: str
    gate_b_source_sha256: str
    gate_b_stamp_sha256: str
    pre_open_manifest_sha256: str
    pre_open_tree_sha256: str
    changed_path: str
    changed_identity: tuple[int, int]
    changed_size: int
    changed_sha256: str
    changed_mode: int
    changed_mtime_ns: int
    changed_paths_sha256: str
    entry_count: int
    file_count: int
    directory_count: int
    symlink_count: int
    eligible_count: int
    eligible_paths_sha256: str
    inherited_count: int
    inherited_paths_sha256: str
    runtime_xattr_name: str
    runtime_xattr_value: str


@dataclass(frozen=True)
class HistoricalCompletedRetirementPolicy:
    """One immutable, read-only capability for an already closed transaction."""

    version: int
    transaction_id: str
    candidate_id: str
    source_path: str
    source_identity: tuple[int, int]
    source_parent_identity: tuple[int, int]
    allowlist_version: int
    category: str
    data_class: str
    deletion_rule: str
    source_manifest_sha256: str
    source_tree_sha256: str
    stage_sha256: tuple[tuple[str, str], ...]
    historical_gate_proofs: tuple[
        tuple[str, str, float, str, str, str], ...]
    final_name: str
    final_identity: tuple[int, int]
    destination_manifest_sha256: str
    destination_tree_sha256: str
    destination_files: int
    destination_directories: int
    destination_symlinks: int
    destination_logical_bytes: int
    external_manifest_name: str
    external_manifest_sha256: str
    external_second_copy_name: str
    external_second_copy_sha256: str
    second_copy_proof_hash: str
    prepared_root_identity: tuple[int, int]
    prepared_manifest_sha256: str
    prepared_tree_sha256: str
    external_receipt_name: str
    external_receipt_sha256: str
    tombstone_name: str
    tombstone_identity: tuple[int, int]
    tombstone_manifest_sha256: str
    tombstone_tree_sha256: str
    tombstone_files: int
    tombstone_directories: int
    tombstone_symlinks: int
    tombstone_logical_bytes: int
    tombstone_allocated_bytes: int
    production_authorization_sha256: str


@dataclass(frozen=True)
class HistoricalPreparedMetadataOverlayPolicy:
    """One read-only metadata overlay for the reviewed prepared cache."""

    version: int
    transaction_id: str
    candidate_id: str
    external_second_copy_name: str
    external_second_copy_sha256: str
    prepared_root_identity: tuple[int, int]
    old_manifest_sha256: str
    old_tree_sha256: str
    new_manifest_sha256: str
    new_tree_sha256: str
    removed_path: str
    removed_record_sha256: str
    removed_content_sha256: str
    removed_logical_bytes: int
    removed_allocated_bytes: int
    removed_flags: int
    removed_xattrs: tuple[tuple[str, str], ...]
    old_root_record_sha256: str
    new_root_record_sha256: str
    unchanged_records_sha256: str
    old_file_count: int
    new_file_count: int
    directory_count: int
    symlink_count: int
    logical_bytes_delta: int
    allocated_bytes_delta: int
    historical_policy_authorization_sha256: str


PRODUCTION_SOURCE_ROOT_OVERLAY = SourceRootOverlayPolicy(
    SOURCE_ROOT_OVERLAY_POLICY_VERSION,
    PROVENANCE_OVERLAY_XATTR,
    PROVENANCE_OVERLAY_VALUE,
    SOURCE_ROOT_OVERLAY_TRANSACTION_ID,
    SOURCE_ROOT_OVERLAY_CANDIDATE_ID,
    SOURCE_ROOT_OVERLAY_PATH,
    SOURCE_ROOT_OVERLAY_IDENTITY,
    SOURCE_ROOT_OVERLAY_PARENT_IDENTITY,
    ALLOWLIST_VERSION,
    SOURCE_ROOT_OVERLAY_ENTRY_COUNT,
    SOURCE_ROOT_OVERLAY_FILE_COUNT,
    SOURCE_ROOT_OVERLAY_DIRECTORY_COUNT,
    SOURCE_ROOT_OVERLAY_ADDED_COUNT,
    SOURCE_ROOT_OVERLAY_ADDED_PATHS_SHA256,
    SOURCE_ROOT_OVERLAY_SEMANTIC_TREE_SHA256,
    SOURCE_ROOT_OVERLAY_SEMANTIC_MANIFEST_SHA256,
    SOURCE_ROOT_OVERLAY_PHYSICAL_TREE_SHA256,
    SOURCE_ROOT_OVERLAY_PHYSICAL_MANIFEST_SHA256,
)


PRODUCTION_RETIREMENT_OVERLAY_BASELINE = RetirementOverlayBaselinePolicy(
    RETIREMENT_OVERLAY_BASELINE_POLICY_VERSION,
    RETIREMENT_OVERLAY_BASELINE_SCHEMA,
    RETIREMENT_OVERLAY_BASELINE_RECORD,
    SOURCE_ROOT_OVERLAY_TRANSACTION_ID,
    SOURCE_ROOT_OVERLAY_CANDIDATE_ID,
    SOURCE_ROOT_OVERLAY_PATH,
    SOURCE_ROOT_OVERLAY_IDENTITY,
    SOURCE_ROOT_OVERLAY_PARENT_IDENTITY,
    ALLOWLIST_VERSION,
    RETIREMENT_OVERLAY_BASELINE_008_SHA256,
    RETIREMENT_OVERLAY_BASELINE_009_SHA256,
    RETIREMENT_OVERLAY_IMMUTABLE_MANIFEST_SHA256,
    RETIREMENT_OVERLAY_IMMUTABLE_TREE_SHA256,
    RETIREMENT_OVERLAY_CURRENT_MANIFEST_SHA256,
    RETIREMENT_OVERLAY_CURRENT_TREE_SHA256,
    RETIREMENT_OVERLAY_CHANGED_PATH,
    RETIREMENT_OVERLAY_CHANGED_IDENTITY,
    (("com.apple.FinderInfo", RETIREMENT_OVERLAY_FINDERINFO_VALUE),),
    (("com.apple.FinderInfo", RETIREMENT_OVERLAY_FINDERINFO_VALUE),
     (PROVENANCE_OVERLAY_XATTR, PROVENANCE_OVERLAY_VALUE)),
    1,
    RETIREMENT_OVERLAY_CHANGED_PATHS_SHA256,
    SOURCE_ROOT_OVERLAY_ENTRY_COUNT,
    SOURCE_ROOT_OVERLAY_FILE_COUNT,
    SOURCE_ROOT_OVERLAY_DIRECTORY_COUNT,
    0,
    RETIREMENT_OVERLAY_ELIGIBILITY_VERSION,
    RETIREMENT_OVERLAY_ELIGIBLE_COUNT,
    RETIREMENT_OVERLAY_ELIGIBLE_PATHS_SHA256,
    RETIREMENT_OVERLAY_INHERITED_PATHS_SHA256,
    PROVENANCE_OVERLAY_XATTR,
    PROVENANCE_OVERLAY_VALUE,
)


PRODUCTION_RETIREMENT_PRETRUNCATE_OPEN = RetirementPretruncateOpenPolicy(
    RETIREMENT_PRETRUNCATE_OPEN_POLICY_VERSION,
    RETIREMENT_PRETRUNCATE_OPEN_SCHEMA,
    RETIREMENT_PRETRUNCATE_OPEN_RECORD,
    SOURCE_ROOT_OVERLAY_TRANSACTION_ID,
    SOURCE_ROOT_OVERLAY_CANDIDATE_ID,
    SOURCE_ROOT_OVERLAY_PATH,
    SOURCE_ROOT_OVERLAY_IDENTITY,
    SOURCE_ROOT_OVERLAY_PARENT_IDENTITY,
    ALLOWLIST_VERSION,
    RETIREMENT_PRETRUNCATE_OPEN_008_SHA256,
    RETIREMENT_PRETRUNCATE_OPEN_009_SHA256,
    RETIREMENT_PRETRUNCATE_OPEN_009A_SHA256,
    RETIREMENT_PRETRUNCATE_OPEN_009A_MANIFEST_SHA256,
    RETIREMENT_PRETRUNCATE_OPEN_009A_TREE_SHA256,
    RETIREMENT_PRETRUNCATE_OPEN_HISTORICAL_009A_AUTH_SHA256,
    RETIREMENT_PRETRUNCATE_OPEN_GATE_B_SOURCE_SHA256,
    RETIREMENT_PRETRUNCATE_OPEN_GATE_B_STAMP_SHA256,
    RETIREMENT_PRETRUNCATE_OPEN_BASE_MANIFEST_SHA256,
    RETIREMENT_PRETRUNCATE_OPEN_BASE_TREE_SHA256,
    RETIREMENT_PRETRUNCATE_OPEN_CHANGED_PATH,
    RETIREMENT_PRETRUNCATE_OPEN_CHANGED_IDENTITY,
    RETIREMENT_PRETRUNCATE_OPEN_CHANGED_SIZE,
    RETIREMENT_PRETRUNCATE_OPEN_CHANGED_SHA256,
    RETIREMENT_PRETRUNCATE_OPEN_CHANGED_MODE,
    RETIREMENT_PRETRUNCATE_OPEN_CHANGED_MTIME_NS,
    RETIREMENT_PRETRUNCATE_OPEN_CHANGED_PATHS_SHA256,
    SOURCE_ROOT_OVERLAY_ENTRY_COUNT,
    SOURCE_ROOT_OVERLAY_FILE_COUNT,
    SOURCE_ROOT_OVERLAY_DIRECTORY_COUNT,
    0,
    RETIREMENT_PRETRUNCATE_OPEN_ELIGIBLE_COUNT,
    RETIREMENT_PRETRUNCATE_OPEN_ELIGIBLE_PATHS_SHA256,
    RETIREMENT_PRETRUNCATE_OPEN_INHERITED_COUNT,
    RETIREMENT_PRETRUNCATE_OPEN_INHERITED_PATHS_SHA256,
    PROVENANCE_OVERLAY_XATTR,
    PROVENANCE_OVERLAY_VALUE,
)


PRODUCTION_PROVENANCE_OVERLAY = ProvenanceOverlayPolicy(
    PROVENANCE_OVERLAY_POLICY_VERSION,
    PROVENANCE_OVERLAY_XATTR,
    PROVENANCE_OVERLAY_VALUE,
    PROVENANCE_OVERLAY_CANDIDATE_ID,
    ALLOWLIST_VERSION,
    PROVENANCE_OVERLAY_ENTRY_COUNT,
    PROVENANCE_OVERLAY_FILE_COUNT,
    PROVENANCE_OVERLAY_DIRECTORY_COUNT,
    PROVENANCE_OVERLAY_INHERITED_COUNT,
    PROVENANCE_OVERLAY_ADDED_COUNT,
    PROVENANCE_OVERLAY_ADDED_PATHS_SHA256,
    PROVENANCE_OVERLAY_SOURCE_TREE_SHA256,
    PROVENANCE_OVERLAY_PHYSICAL_TREE_SHA256,
)


RETAIL_BACKUP_CANDIDATE = CandidateSpec(
    "backup-device-retail-20260808-185559", RETAIL_BACKUP_PATH, "backups",
    "redundant", "requires-fixed-prepared-proof")
RETAIL_BACKUP_MANIFEST_CANDIDATE = CandidateSpec(
    "backup-device-retail-20260808-185559-manifest", RETAIL_BACKUP_MANIFEST_PATH,
    "backups", "reproducible-noncritical", "after-main-backup-pass")


INITIAL_CANDIDATES = (
    CandidateSpec("archives-openxray-backups", Path("/Users/patryk/openxray-backups"),
                  "archives", "reproducible-noncritical", "delete-after-full-pass"),
    CandidateSpec("evidence-simulator-autoload-20260808-extended",
                  Path("/Users/patryk/openxray-handoff/simulator-autoload-20260808-extended.log"),
                  "completed-evidence", "reproducible-noncritical", "delete-after-full-pass"),
    CandidateSpec("evidence-simulator-autoload-20260809-postreview",
                  Path("/Users/patryk/openxray-handoff/simulator-autoload-20260809-postreview.log"),
                  "completed-evidence", "reproducible-noncritical", "delete-after-full-pass"),
    CandidateSpec("evidence-uiscene-full-gate-20260809-141312-36282",
                  Path("/Users/patryk/openxray-handoff/uiscene-full-gate-20260809-141312-36282.log"),
                  "completed-evidence", "reproducible-noncritical", "delete-after-full-pass"),
    CandidateSpec("evidence-uiscene-runtime-26-5-rerun-20260809-135905-18424",
                  Path("/Users/patryk/openxray-handoff/uiscene-runtime-26.5-rerun-20260809-135905-18424.log"),
                  "completed-evidence", "reproducible-noncritical", "delete-after-full-pass"),
    CandidateSpec("evidence-uiscene-runtime-27-0-rerun-20260809-140604-26901",
                  Path("/Users/patryk/openxray-handoff/uiscene-runtime-27.0-rerun-20260809-140604-26901.log"),
                  "completed-evidence", "reproducible-noncritical", "delete-after-full-pass"),
    RETAIL_BACKUP_CANDIDATE,
    RETAIL_BACKUP_MANIFEST_CANDIDATE,
)
CANDIDATE_AUTHORIZATION_FIELDS = (
    "ident", "source", "category", "data_class", "deletion_rule")
REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256 = \
    "cb06555226d191a86f4ac550443fef95d6445fda638c6834dca6145f9bdf87b5"


def provenance_overlay_authorization_payload() -> tuple[Any, ...]:
    policy = PRODUCTION_PROVENANCE_OVERLAY
    return (
        "openxray.apfs-provenance-overlay-authorization.v1",
        policy.version, policy.xattr_name, policy.xattr_value,
        policy.candidate_id, policy.allowlist_version,
        policy.entry_count, policy.file_count, policy.directory_count,
        policy.inherited_count, policy.added_count, policy.added_paths_sha256,
        policy.source_tree_sha256, policy.physical_tree_sha256,
        PROVENANCE_OVERLAY_RECOVERY_TRANSACTION_ID,
        PROVENANCE_OVERLAY_RECOVERY_CANDIDATE_SHA256,
        PROVENANCE_OVERLAY_RECOVERY_COPY_COMPLETE_SHA256,
        PROVENANCE_OVERLAY_RECOVERY_PHYSICAL_MANIFEST_SHA256,
    )


def source_root_overlay_authorization_payload() -> tuple[Any, ...]:
    policy = PRODUCTION_SOURCE_ROOT_OVERLAY
    return (
        "openxray.source-root-provenance-overlay-authorization.v1",
        policy.version, policy.xattr_name, policy.xattr_value,
        policy.transaction_id, policy.candidate_id, policy.source_path,
        policy.source_identity, policy.source_parent_identity,
        policy.allowlist_version, policy.entry_count, policy.file_count,
        policy.directory_count, policy.added_count, policy.added_paths_sha256,
        policy.semantic_tree_sha256, policy.semantic_manifest_sha256,
        policy.physical_tree_sha256, policy.physical_manifest_sha256,
        SOURCE_ROOT_OVERLAY_LEGACY_DESTINATION_AUTHORIZATION_SHA256,
        SOURCE_ROOT_OVERLAY_HISTORICAL_AUTHORIZATION_SHA256,
        SOURCE_ROOT_OVERLAY_LEGACY_PREFIX,
        SOURCE_ROOT_OVERLAY_LEGACY_PREFIX_SHA256,
        # This authorization payload is evaluated while the module constants
        # are initialized, before the general sha256_bytes helper is defined.
        # Bind the exact immutable 006 bytes directly with hashlib here rather
        # than weakening or delaying the production authorization decision.
        hashlib.sha256(SOURCE_ROOT_OVERLAY_LEGACY_006_CANONICAL).hexdigest(),
        tuple(sorted(SOURCE_ROOT_OVERLAY_LEGACY_005.items())),
    )


def retirement_overlay_baseline_authorization_payload() -> tuple[Any, ...]:
    policy = PRODUCTION_RETIREMENT_OVERLAY_BASELINE
    return (
        "openxray.retirement-overlay-baseline-authorization.v1",
        tuple(getattr(policy, field_name)
              for field_name in policy.__dataclass_fields__),
    )


def retirement_pretruncate_open_authorization_payload() -> tuple[Any, ...]:
    policy = PRODUCTION_RETIREMENT_PRETRUNCATE_OPEN
    return (
        "openxray.retirement-pretruncate-open-authorization.v1",
        tuple(getattr(policy, field_name)
              for field_name in policy.__dataclass_fields__),
    )


def allowlist_authorization_payload(
        version: int, candidates: tuple[CandidateSpec, ...]) -> tuple[Any, ...]:
    candidate_rows = tuple(
        (candidate.ident, str(candidate.source), candidate.category,
         candidate.data_class, candidate.deletion_rule)
        for candidate in candidates)
    return ("openxray.archive-production-authorization.v3", version,
            CANDIDATE_AUTHORIZATION_FIELDS, candidate_rows,
            provenance_overlay_authorization_payload(),
            source_root_overlay_authorization_payload(),
            retirement_overlay_baseline_authorization_payload(),
            retirement_pretruncate_open_authorization_payload())


def allowlist_authorization_sha256(
        version: int, candidates: tuple[CandidateSpec, ...]) -> str:
    encoded = (json.dumps(
        allowlist_authorization_payload(version, candidates), ensure_ascii=True,
        separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def reviewed_allowlist_authorized(
        version: int, candidates: tuple[CandidateSpec, ...]) -> bool:
    return hmac.compare_digest(
        allowlist_authorization_sha256(version, candidates),
        REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)


PRODUCTION_REVIEW_AUTHORIZED = reviewed_allowlist_authorized(
    ALLOWLIST_VERSION, INITIAL_CANDIDATES)
PRODUCTION_HISTORICAL_COMPLETED = HistoricalCompletedRetirementPolicy(
    HISTORICAL_COMPLETED_POLICY_VERSION,
    SOURCE_ROOT_OVERLAY_TRANSACTION_ID,
    SOURCE_ROOT_OVERLAY_CANDIDATE_ID,
    SOURCE_ROOT_OVERLAY_PATH,
    SOURCE_ROOT_OVERLAY_IDENTITY,
    SOURCE_ROOT_OVERLAY_PARENT_IDENTITY,
    ALLOWLIST_VERSION,
    "backups",
    "redundant",
    "requires-fixed-prepared-proof",
    SOURCE_ROOT_OVERLAY_SEMANTIC_MANIFEST_SHA256,
    SOURCE_ROOT_OVERLAY_SEMANTIC_TREE_SHA256,
    HISTORICAL_COMPLETED_STAGE_SHA256,
    HISTORICAL_COMPLETED_GATE_PROOFS,
    "5beca2ef4f034286be6299d6d9a2bdba-device-retail-backup-20260808-185559",
    (16777256, 20830),
    "3b457eba2ab83f5d6709ac9dba687d2b6e00a560ad0e3de11ed87880fcf15cd1",
    "929ddd05bd7b6a33f2729074be2847c38d05b5f54e230d08e4f06b694af110ac",
    790, 37, 0, 4761061526,
    "5beca2ef4f034286be6299d6d9a2bdba.json",
    "92c542d4ebae5f725c23fa7523804b080dd45c71c00af408a3d446214f7ae9e5",
    "5beca2ef4f034286be6299d6d9a2bdba-second-copy-proof.json",
    "86c0de066dd45c51e1250a078de171ecbf5f3df101d79a4cd2f21872bf45799a",
    "5a9eea54d9004f91c9ccc8aca22b3a08a24ddda397ce2bd314035debb2687a56",
    (16777234, 23449127),
    "1f38846d7a9a50d9e9ea567bb304bedb7d3da6fb6bf5675121d85b8a61363db8",
    "9b4eacff01f6e0c7f528c0252e208641388fe35124ebea69c90c4cc8219b2db8",
    "5beca2ef4f034286be6299d6d9a2bdba-source-deletion.json",
    "bf032741b03cd30879037943fcffd673d6decb60da966d51df04ed88e4968304",
    ".retired-de0851374e2047deb725c478cff21b5d",
    SOURCE_ROOT_OVERLAY_IDENTITY,
    "ad4a460a6e6b51aae1e8ad91ef87fb311115befe6349470064026bdee514b1e6",
    "8bdc659811c8e2c1b67948a6e0c99bf6e23e6aef6358977e859fe7036f6b4f7c",
    790, 37, 0, 0, 0,
    REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256,
)

PRODUCTION_HISTORICAL_PREPARED_OVERLAY = \
    HistoricalPreparedMetadataOverlayPolicy(
        HISTORICAL_PREPARED_OVERLAY_POLICY_VERSION,
        SOURCE_ROOT_OVERLAY_TRANSACTION_ID,
        SOURCE_ROOT_OVERLAY_CANDIDATE_ID,
        "5beca2ef4f034286be6299d6d9a2bdba-second-copy-proof.json",
        "86c0de066dd45c51e1250a078de171ecbf5f3df101d79a4cd2f21872bf45799a",
        (16777234, 23449127),
        HISTORICAL_PREPARED_OVERLAY_OLD_MANIFEST_SHA256,
        HISTORICAL_PREPARED_OVERLAY_OLD_TREE_SHA256,
        HISTORICAL_PREPARED_OVERLAY_NEW_MANIFEST_SHA256,
        HISTORICAL_PREPARED_OVERLAY_NEW_TREE_SHA256,
        HISTORICAL_PREPARED_OVERLAY_REMOVED_PATH,
        HISTORICAL_PREPARED_OVERLAY_REMOVED_RECORD_SHA256,
        HISTORICAL_PREPARED_OVERLAY_REMOVED_CONTENT_SHA256,
        6148, 8192, 0x8000,
        (("com.apple.FinderInfo",
          HISTORICAL_PREPARED_OVERLAY_REMOVED_FINDERINFO),),
        HISTORICAL_PREPARED_OVERLAY_OLD_ROOT_RECORD_SHA256,
        HISTORICAL_PREPARED_OVERLAY_NEW_ROOT_RECORD_SHA256,
        HISTORICAL_PREPARED_OVERLAY_UNCHANGED_RECORDS_SHA256,
        21, 20, 9, 0, -6148, -8192,
        HISTORICAL_COMPLETED_POLICY_AUTHORIZATION_SHA256,
    )


def historical_completed_policy_authorization_payload(
        policy: HistoricalCompletedRetirementPolicy) -> tuple[Any, ...]:
    return (
        "openxray.historical-completed-retirement-readonly.v1",
        tuple(getattr(policy, field_name)
              for field_name in policy.__dataclass_fields__),
    )


def historical_completed_policy_authorization_sha256(
        policy: HistoricalCompletedRetirementPolicy) -> str:
    encoded = (json.dumps(
        historical_completed_policy_authorization_payload(policy),
        ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ) + "\n").encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def historical_prepared_overlay_authorization_payload(
        policy: HistoricalPreparedMetadataOverlayPolicy) -> tuple[Any, ...]:
    return (
        "openxray.historical-prepared-metadata-overlay-readonly.v1",
        policy.historical_policy_authorization_sha256,
        tuple(getattr(policy, field_name)
              for field_name in policy.__dataclass_fields__),
    )


def historical_prepared_overlay_authorization_sha256(
        policy: HistoricalPreparedMetadataOverlayPolicy) -> str:
    encoded = (json.dumps(
        historical_prepared_overlay_authorization_payload(policy),
        ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ) + "\n").encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


SPEC_BY_ID = {candidate.ident: candidate for candidate in INITIAL_CANDIDATES}
PROTECTED_PREFIXES = ("retail-prepared-", "device-retail-backup-", "device-backup-",
                      "simulator-work-", "resources.db", "levels.db")
PROTECTED_COMPONENTS = {".codex", "cache", "model", "models", "build", "bin",
                        "documents", "save", "saves", "gate-logs", "local-gates"}

SECOND_COPY_MAPPINGS = tuple((source, f"Documents/{source}") for source in (
    "_appdata_/savedgames/mobile user - beginning of the game.scop",
    "resources/configs.db",
    "resources/resources.db0",
    "resources/resources.db1",
    "resources/resources.db2",
    "resources/resources.db3",
    "resources/resources.db4",
    "levels/levels.db0",
    "levels/levels.db1",
    "localization/base_sounds.db",
    "localization/xefis_movies.db",
    "localization/xenglish.db",
    "patches/xpatch_02.db",
))
MAPPED_SOURCE_PATHS = {source for source, _ in SECOND_COPY_MAPPINGS}
MAPPED_PREPARED_PATHS = {prepared for _, prepared in SECOND_COPY_MAPPINGS}
PREPARED_METADATA_PATHS = {
    "manifest/files.tsv", "manifest/large-files-sha256.tsv",
    "manifest/prepared-files.tsv", "manifest/prepared-manifest-files.tsv",
    "manifest/required-archives.tsv", "manifest/summary.txt",
    "manifest/zero-byte-files.txt",
}
SIBLING_MANIFEST_PATHS = {
    "files.tsv", "large-files-sha256.tsv", "required-archives.tsv",
    "summary.txt", "zero-byte-files.txt",
}
HISTORICAL_EXACT_PATHS = {
    ".DS_Store", "fsgame.ltx", "xr_boot.log", "openxray_mobile user.log",
    "xr_shot.ppm", "xr_shot_meta.txt", "_appdata_/user.ltx",
    "_appdata_/tmp.ltx", "_appdata_/autoinput_ack.txt", "_appdata_/imgui.ini",
    "_appdata_/cdb_cache/zaton/objspace.bin",
    "_appdata_/cdb_cache/zaton/hom.bin",
    "_appdata_/cdb_cache/zaton/portals.bin",
}
HISTORICAL_BIN_PATHS = {
    "bin/BugTrap.dll", "bin/Microsoft.VC80.CRT.manifest", "bin/OpenAL32.dll",
    "bin/dbghelp.dll", "bin/eax.dll", "bin/lua.JIT.1.1.4.dll",
    "bin/luabind.beta7-devel.rc4.dll", "bin/msvcr80.dll", "bin/ode.dll",
    "bin/pctrlchk.dll", "bin/wrap_oal.dll", "bin/xrAPI.dll", "bin/xrCDB.dll",
    "bin/xrCPU_Pipe.dll", "bin/xrCore.dll", "bin/xrD3D9-Null.dll",
    "bin/xrEngine.exe", "bin/xrGame.dll", "bin/xrGameSpy.dll",
    "bin/xrNetServer.dll", "bin/xrParticles.dll", "bin/xrPhysics.dll",
    "bin/xrRender_R1.dll", "bin/xrRender_R2.dll", "bin/xrRender_R3.dll",
    "bin/xrRender_R4.dll", "bin/xrSound.dll", "bin/xrXMLParser.dll",
    "bin/dedicated/Microsoft.VC80.CRT.manifest",
    "bin/dedicated/OpenAL32.dll", "bin/dedicated/eax.dll",
    "bin/dedicated/msvcr80.dll", "bin/dedicated/wrap_oal.dll",
    "bin/dedicated/xrEngine.exe",
}
ARCHIVE_SUFFIXES = {
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".ipa",
    ".dmg", ".pkg", ".xcarchive",
}


@dataclass
class Settings:
    queue_root: Path = QUEUE_ROOT
    mount: Path = VOLUME_MOUNT
    volume_name: str = VOLUME_NAME
    volume_uuid: str = VOLUME_UUID
    production: bool = True
    review_authorized: bool = PRODUCTION_REVIEW_AUTHORIZED
    repo_root: Path = REPO_ROOT
    gate_log_root: Path = GATE_LOG_ROOT
    full_gate_stamp: Path = FULL_GATE_STAMP
    gate_max_age_seconds: int = GATE_MAX_AGE_SECONDS
    retail_prepared_root: Path = RETAIL_PREPARED_ROOT
    test_retail_roles: tuple[tuple[str, str], ...] = ()
    test_retail_inventory: tuple[int, str, str] | None = None
    candidates: tuple[CandidateSpec, ...] = INITIAL_CANDIDATES


@dataclass
class SourceBinding:
    parent_fd: int
    root_fd: int
    name: str
    kind: str
    parent_identity: tuple[int, int]
    root_identity: tuple[int, int]

    def close(self) -> None:
        for attribute in ("root_fd", "parent_fd"):
            descriptor = getattr(self, attribute)
            if descriptor >= 0:
                os.close(descriptor)
                setattr(self, attribute, -1)


@dataclass
class RelativeFileBinding:
    parent_fd: int
    file_fd: int
    name: str
    file_identity: tuple[int, int]

    def close(self) -> None:
        for attribute in ("file_fd", "parent_fd"):
            descriptor = getattr(self, attribute)
            if descriptor >= 0:
                os.close(descriptor)
                setattr(self, attribute, -1)


@dataclass
class VolumeBinding:
    fd: int
    identity: tuple[int, int]
    attrs: dict[str, str]

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


@dataclass
class ExternalRoots:
    archive_fd: int
    category_fd: int
    manifests_fd: int

    def close(self) -> None:
        for descriptor in (self.manifests_fd, self.category_fd, self.archive_fd):
            if descriptor >= 0:
                os.close(descriptor)


@dataclass
class RetirementRegular:
    fd: int
    parent_fd: int | None
    name: str | None
    path: str
    expected_dev: int
    expected_ino: int
    expected_size: int
    prepared_mode: int
    admitted: dict[str, Any]
    prepared_snapshot: dict[str, Any]
    expected_original_mtime_ns: int
    expected_tombstone: dict[str, Any]

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


@dataclass
class RetirementDirectory:
    fd: int
    path: str
    prepared_snapshot: dict[str, Any]

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


@dataclass
class RetirementSymlink:
    fd: int
    parent_fd: int
    name: str
    path: str
    expected_identity: tuple[int, int]
    prepared_snapshot: dict[str, Any]

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


@dataclass
class RetirementPlan:
    regulars: list[RetirementRegular]
    directories: list[RetirementDirectory]
    symlinks: list[RetirementSymlink] = field(default_factory=list)
    root_fd: int = -1
    root_parent_fd: int = -1
    root_name: str | None = None
    root_aliases: set[str] = field(default_factory=set)
    root_kind: str = ""
    root_identity: tuple[int, int] = (-1, -1)
    prepared_manifest: dict[str, Any] | None = None
    expected_tombstone_manifest: dict[str, Any] | None = None

    def close(self) -> None:
        for regular in self.regulars:
            regular.close()
        for symlink in self.symlinks:
            symlink.close()
        for directory in self.directories:
            directory.close()
        self.directories.clear()
        self.symlinks.clear()
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1
        if self.root_parent_fd >= 0:
            os.close(self.root_parent_fd)
            self.root_parent_fd = -1


@dataclass
class MetadataRestoreEntry:
    fd: int
    path: str
    admitted: dict[str, Any]

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


@dataclass
class MetadataRestorePlan:
    regulars: list[MetadataRestoreEntry]
    directories: list[MetadataRestoreEntry]

    def close(self) -> None:
        for entry in (*self.regulars, *self.directories):
            entry.close()
        self.regulars.clear()
        self.directories.clear()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True,
                       separators=(",", ":"), allow_nan=False)
            + "\n").encode("ascii")


def reject_nonfinite_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def reject_nonfinite_json_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number is forbidden")
    if isinstance(value, list):
        for item in value:
            reject_nonfinite_json_numbers(item)
    elif isinstance(value, dict):
        for item in value.values():
            reject_nonfinite_json_numbers(item)


def strict_json_loads(value: str) -> Any:
    """Parse RFC-compatible JSON without Python's NaN/Infinity extension."""
    parsed = json.loads(value, parse_constant=reject_nonfinite_json_constant)
    # CPython may overflow a valid numeric token such as 1e999 to ``inf``;
    # parse_constant does not see that path, so reject it recursively as well.
    reject_nonfinite_json_numbers(parsed)
    return parsed


def load_retail_import_prepared_verifier(
        path: Path) -> Callable[[int], Any]:
    """Load retail_import's complete local dependency chain without bytecode.

    ``retail_import.py`` dynamically executes ``retail_simulator_guard.py``,
    which in turn dynamically executes ``openal_provider_contract.py``.  The
    historical verifier is a read-only capability, so even implicit ``.pyc``
    publication is forbidden.  Serialize cooperating loads, force CPython's
    process-global bytecode switch for the entire nested execution, and restore
    the exact prior value after success or any import exception.
    """
    if not path.is_absolute():
        raise ArchiveError("retail importer module path must be absolute")
    with _NO_BYTECODE_IMPORT_LOCK:
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location(
                "openxray_retail_import_historical_verify", path)
            if spec is None or spec.loader is None:
                raise ArchiveError("retail importer verifier cannot be loaded")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            verifier = getattr(module, "verify_prepared", None)
            if not callable(verifier):
                raise ArchiveError("retail importer prepared verifier is absent")
            return verifier
        except ArchiveError:
            raise
        except Exception as error:
            raise ArchiveError(
                f"retail importer dependency chain cannot be loaded: {error}") \
                from error
        finally:
            sys.dont_write_bytecode = previous


def finite_timestamp(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return isinstance(value, int) or math.isfinite(value)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_from_unix(value: float) -> str:
    return (datetime.fromtimestamp(value, timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def allocated_bytes(info: os.stat_result) -> int:
    return int(getattr(info, "st_blocks", 0)) * 512


def bsd_flags(info: os.stat_result) -> int:
    return int(getattr(info, "st_flags", 0))


def directory_open_flags() -> int:
    return os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW


def require_leaf(value: str, label: str) -> str:
    if (not value or value in {".", ".."} or Path(value).name != value
            or "/" in value or "\\" in value or "\n" in value or "\r" in value):
        raise ArchiveError(f"{label} is not a confined leaf")
    return value


def partial_record_canonical(name: str) -> str:
    match = PARTIAL_RECORD_RE.fullmatch(name)
    if match is None or match.group(1).endswith(".json"):
        raise ArchiveError(f"unknown/foreign partial record pattern: {name}")
    canonical = f"{match.group(1)}.json"
    require_leaf(canonical, "partial canonical record")
    if canonical == name or canonical.startswith(".partial-"):
        raise ArchiveError("partial record prefix could alias a canonical name")
    return canonical


def open_absolute_directory(path: Path, label: str) -> int:
    if not path.is_absolute() or path != Path(os.path.normpath(str(path))):
        raise ArchiveError(f"{label} must be a normalized absolute path")
    descriptor = os.open("/", directory_open_flags())
    try:
        for component in path.parts[1:]:
            following = os.open(component, directory_open_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_leaf(parent_fd: int, name: str, kind: str, *, writable: bool = False) -> int:
    require_leaf(name, "leaf")
    flags = (directory_open_flags() if kind == "directory"
             else (os.O_RDWR if writable else os.O_RDONLY) | O_NOFOLLOW)
    descriptor = os.open(name, flags, dir_fd=parent_fd)
    info = os.fstat(descriptor)
    expected = stat.S_ISDIR(info.st_mode) if kind == "directory" else stat.S_ISREG(info.st_mode)
    if not expected:
        os.close(descriptor)
        raise ArchiveError(f"{name!r} is not a {kind}")
    return descriptor


def stable_rebind(parent_fd: int, name: str, expected: tuple[int, int], kind: str,
                  *, writable: bool = False) -> int:
    descriptor = open_leaf(parent_fd, name, kind, writable=writable)
    try:
        linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if identity(os.fstat(descriptor)) != expected or identity(linked) != expected:
            raise ArchiveError(f"{name!r} identity changed")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def complete_write(fd: int, contents: bytes, writer: Callable[[int, bytes], int] = os.write) -> None:
    offset = 0
    while offset < len(contents):
        written = writer(fd, contents[offset:])
        if written <= 0:
            raise ArchiveError("write returned no progress")
        offset += written


def sha256_fd(fd: int) -> str:
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        digest.update(block)
    os.lseek(fd, 0, os.SEEK_SET)
    return digest.hexdigest()


def _darwin_xattr_functions() -> tuple[Any, Any, Any]:
    library = ctypes.CDLL(None, use_errno=True)
    listing = library.flistxattr
    listing.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int)
    listing.restype = ctypes.c_ssize_t
    getting = library.fgetxattr
    getting.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
                        ctypes.c_uint32, ctypes.c_int)
    getting.restype = ctypes.c_ssize_t
    setting = library.fsetxattr
    setting.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
                        ctypes.c_uint32, ctypes.c_int)
    setting.restype = ctypes.c_int
    return listing, getting, setting


def xattrs_fd(fd: int) -> dict[str, str]:
    if sys.platform != "darwin":
        return {}
    listing, getting, _ = _darwin_xattr_functions()
    size = listing(fd, None, 0, 0)
    if size < 0:
        number = ctypes.get_errno()
        raise ArchiveError(f"flistxattr failed: {os.strerror(number)}")
    if size == 0:
        return {}
    names_buffer = ctypes.create_string_buffer(size)
    if listing(fd, names_buffer, size, 0) != size:
        number = ctypes.get_errno()
        raise ArchiveError(f"flistxattr changed: {os.strerror(number)}")
    result: dict[str, str] = {}
    for encoded in sorted(filter(None, names_buffer.raw.split(b"\0"))):
        value_size = getting(fd, encoded, None, 0, 0, 0)
        if value_size < 0:
            number = ctypes.get_errno()
            raise ArchiveError(f"fgetxattr size failed: {os.strerror(number)}")
        value = ctypes.create_string_buffer(value_size)
        if value_size and getting(fd, encoded, value, value_size, 0, 0) != value_size:
            number = ctypes.get_errno()
            raise ArchiveError(f"fgetxattr failed: {os.strerror(number)}")
        result[encoded.decode("utf-8", "strict")] = base64.b64encode(value.raw).decode("ascii")
    return result


def apply_xattrs_fd(destination_fd: int, values: dict[str, str],
                    guard: Callable[[], None] | None = None) -> None:
    if not values:
        return
    if sys.platform != "darwin":
        raise ArchiveError("xattrs cannot be represented on this platform")
    _, _, setting = _darwin_xattr_functions()
    for name, encoded in sorted(values.items()):
        if guard is not None:
            guard()
        raw = base64.b64decode(encoded, validate=True)
        buffer = ctypes.create_string_buffer(raw)
        if setting(destination_fd, os.fsencode(name), buffer, len(raw), 0, 0) != 0:
            number = ctypes.get_errno()
            raise ArchiveError(f"fsetxattr failed for {name!r}: {os.strerror(number)}")


def restore_xattrs_fd(fd: int, values: dict[str, str]) -> None:
    """Replace, rather than merge, the descriptor's extended attributes."""
    current = xattrs_fd(fd)
    if sys.platform != "darwin":
        if current or values:
            raise ArchiveError("xattrs cannot be restored on this platform")
        return
    library = ctypes.CDLL(None, use_errno=True)
    removing = library.fremovexattr
    removing.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int)
    removing.restype = ctypes.c_int
    for name in sorted(set(current) - set(values)):
        if removing(fd, os.fsencode(name), 0) != 0:
            number = ctypes.get_errno()
            raise ArchiveError(f"fremovexattr failed for {name!r}: {os.strerror(number)}")
    apply_xattrs_fd(fd, values)
    if xattrs_fd(fd) != values:
        raise ArchiveError("restored xattrs differ from the admitted manifest")


def acl_text_fd(fd: int) -> str:
    if sys.platform != "darwin":
        return ""
    library = ctypes.CDLL(None, use_errno=True)
    get_acl = library.acl_get_fd_np
    get_acl.argtypes = (ctypes.c_int, ctypes.c_int)
    get_acl.restype = ctypes.c_void_p
    to_text = library.acl_to_text
    to_text.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t))
    to_text.restype = ctypes.c_void_p
    free_acl = library.acl_free
    free_acl.argtypes = (ctypes.c_void_p,)
    free_acl.restype = ctypes.c_int
    acl = get_acl(fd, ACL_TYPE_EXTENDED)
    if not acl:
        number = ctypes.get_errno()
        if number in {0, errno.ENOENT, errno.EINVAL, errno.ENOTSUP}:
            return ""
        raise ArchiveError(f"acl_get_fd_np failed: {os.strerror(number)}")
    text_pointer = ctypes.c_void_p()
    try:
        length = ctypes.c_ssize_t()
        text_pointer = ctypes.c_void_p(to_text(acl, ctypes.byref(length)))
        if not text_pointer.value:
            number = ctypes.get_errno()
            raise ArchiveError(f"acl_to_text failed: {os.strerror(number)}")
        return base64.b64encode(ctypes.string_at(text_pointer, length.value)).decode("ascii")
    finally:
        if text_pointer.value:
            free_acl(text_pointer)
        free_acl(acl)


def copy_acl_fd(source_fd: int, destination_fd: int,
                guard: Callable[[], None] | None = None) -> None:
    if sys.platform != "darwin":
        if acl_text_fd(source_fd):
            raise ArchiveError("ACL cannot be preserved on this platform")
        return
    library = ctypes.CDLL(None, use_errno=True)
    get_acl = library.acl_get_fd_np
    get_acl.argtypes = (ctypes.c_int, ctypes.c_int)
    get_acl.restype = ctypes.c_void_p
    set_acl = library.acl_set_fd_np
    set_acl.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_int)
    set_acl.restype = ctypes.c_int
    free_acl = library.acl_free
    free_acl.argtypes = (ctypes.c_void_p,)
    acl = get_acl(source_fd, ACL_TYPE_EXTENDED)
    if not acl:
        number = ctypes.get_errno()
        if number in {0, errno.ENOENT, errno.EINVAL, errno.ENOTSUP}:
            return
        raise ArchiveError(f"acl_get_fd_np failed: {os.strerror(number)}")
    try:
        if guard is not None:
            guard()
        if set_acl(destination_fd, acl, ACL_TYPE_EXTENDED) != 0:
            number = ctypes.get_errno()
            raise ArchiveError(f"acl_set_fd_np failed: {os.strerror(number)}")
    finally:
        free_acl(acl)


def restore_acl_text_fd(fd: int, encoded: str) -> None:
    """Restore the canonical descriptor ACL captured by ``acl_text_fd``."""
    if sys.platform != "darwin":
        if encoded:
            raise ArchiveError("ACL cannot be restored on this platform")
        return
    library = ctypes.CDLL(None, use_errno=True)
    acl_init = library.acl_init
    acl_init.argtypes = (ctypes.c_int,)
    acl_init.restype = ctypes.c_void_p
    acl_from_text = library.acl_from_text
    acl_from_text.argtypes = (ctypes.c_char_p,)
    acl_from_text.restype = ctypes.c_void_p
    set_acl = library.acl_set_fd_np
    set_acl.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_int)
    set_acl.restype = ctypes.c_int
    free_acl = library.acl_free
    free_acl.argtypes = (ctypes.c_void_p,)
    free_acl.restype = ctypes.c_int
    if encoded:
        raw = base64.b64decode(encoded, validate=True)
        if not raw or b"\0" in raw:
            raise ArchiveError("admitted ACL text is invalid")
        acl = acl_from_text(raw)
    else:
        acl = acl_init(0)
    if not acl:
        number = ctypes.get_errno()
        raise ArchiveError(f"cannot reconstruct admitted ACL: {os.strerror(number)}")
    try:
        if set_acl(fd, acl, ACL_TYPE_EXTENDED) != 0:
            number = ctypes.get_errno()
            raise ArchiveError(f"cannot restore admitted ACL: {os.strerror(number)}")
    finally:
        free_acl(acl)
    if acl_text_fd(fd) != encoded:
        raise ArchiveError("restored ACL differs from the admitted manifest")


def clear_delete_protection_fd(fd: int, *, directory: bool,
                               guard: Callable[[], None] | None = None) -> None:
    """Remove deletion barriers only after an entry is in private quarantine.

    This is intentionally descriptor-only: the public source pathname is never
    used to weaken metadata.  Source ACLs and flags remain intact until the
    expected inode has been exclusively moved and rebound inside the private
    quarantine namespace.
    """
    if sys.platform == "darwin":
        library = ctypes.CDLL(None, use_errno=True)
        fchflags = library.fchflags
        fchflags.argtypes = (ctypes.c_int, ctypes.c_uint)
        fchflags.restype = ctypes.c_int
        if guard is not None:
            guard()
        if fchflags(fd, 0) != 0:
            number = ctypes.get_errno()
            raise ArchiveError(f"fchflags before quarantine deletion failed: {os.strerror(number)}")

        acl_init = library.acl_init
        acl_init.argtypes = (ctypes.c_int,)
        acl_init.restype = ctypes.c_void_p
        set_acl = library.acl_set_fd_np
        set_acl.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_int)
        set_acl.restype = ctypes.c_int
        free_acl = library.acl_free
        free_acl.argtypes = (ctypes.c_void_p,)
        free_acl.restype = ctypes.c_int
        empty_acl = acl_init(0)
        if not empty_acl:
            number = ctypes.get_errno()
            raise ArchiveError(f"acl_init before quarantine deletion failed: {os.strerror(number)}")
        try:
            if guard is not None:
                guard()
            if set_acl(fd, empty_acl, ACL_TYPE_EXTENDED) != 0:
                number = ctypes.get_errno()
                raise ArchiveError(
                    f"acl_set_fd_np before quarantine deletion failed: {os.strerror(number)}")
        finally:
            free_acl(empty_acl)

    if directory:
        current_mode = stat.S_IMODE(os.fstat(fd).st_mode)
        if guard is not None:
            guard()
        os.fchmod(fd, current_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


def apply_bsd_flags_fd(fd: int, value: int,
                       guard: Callable[[], None] | None = None) -> None:
    if value == 0:
        return
    if sys.platform != "darwin":
        raise ArchiveError("BSD flags cannot be preserved on this platform")
    function = ctypes.CDLL(None, use_errno=True).fchflags
    function.argtypes = (ctypes.c_int, ctypes.c_uint)
    function.restype = ctypes.c_int
    if guard is not None:
        guard()
    if function(fd, value) != 0:
        number = ctypes.get_errno()
        raise ArchiveError(f"fchflags failed: {os.strerror(number)}")


def restore_bsd_flags_fd(fd: int, value: int) -> None:
    """Set the exact admitted flag word, including an exact zero."""
    if sys.platform != "darwin":
        if value:
            raise ArchiveError("BSD flags cannot be restored on this platform")
        return
    function = ctypes.CDLL(None, use_errno=True).fchflags
    function.argtypes = (ctypes.c_int, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(fd, value) != 0:
        number = ctypes.get_errno()
        raise ArchiveError(f"cannot restore BSD flags: {os.strerror(number)}")
    if bsd_flags(os.fstat(fd)) != value:
        raise ArchiveError("restored BSD flags differ from the admitted manifest")


def entry_metadata(fd: int) -> dict[str, Any]:
    info = os.fstat(fd)
    return {
        "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid,
        "mtime_ns": info.st_mtime_ns, "flags": bsd_flags(info),
        "xattrs": xattrs_fd(fd), "acl": acl_text_fd(fd),
        "allocated_bytes": allocated_bytes(info),
    }


_RETIREMENT_STAT_FIELDS = (
    "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
    "st_size", "st_mtime_ns", "st_ctime_ns", "st_blocks", "st_flags",
)


def retirement_stat_guard(info: os.stat_result) -> dict[str, int]:
    """Capture every stat field that can expose post-snapshot inode drift."""
    return {field: int(getattr(info, field, 0)) for field in _RETIREMENT_STAT_FIELDS}


def stable_retirement_snapshot(fd: int, kind: str, path: str, *,
                               target: str | None = None) -> dict[str, Any]:
    """Take a bounded adjacent double snapshot of one pinned inode.

    Darwin has no single syscall that atomically returns stat, xattrs and ACLs.
    Both metadata reads therefore bracket the optional content hash with stat
    snapshots.  Any identity, link, content, xattr, ACL, flag or stat drift is
    rejected before the caller may mutate the inode.
    """
    first = os.fstat(fd)
    first_guard = retirement_stat_guard(first)
    first_xattrs = xattrs_fd(fd)
    first_acl = acl_text_fd(fd)
    digest = sha256_fd(fd) if kind == "regular" else None
    middle = os.fstat(fd)
    middle_guard = retirement_stat_guard(middle)
    second_xattrs = xattrs_fd(fd)
    second_acl = acl_text_fd(fd)
    final = os.fstat(fd)
    final_guard = retirement_stat_guard(final)
    if (first_guard != middle_guard or middle_guard != final_guard
            or first_xattrs != second_xattrs or first_acl != second_acl):
        raise ArchiveError(f"retirement inode drifted during metadata snapshot: {path}")
    if kind == "regular":
        if not stat.S_ISREG(final.st_mode) or final.st_nlink != 1:
            raise ArchiveError(f"retirement regular is foreign or linked: {path}")
    elif kind == "directory":
        if not stat.S_ISDIR(final.st_mode):
            raise ArchiveError(f"retirement directory is foreign: {path}")
    elif kind == "symlink":
        if not stat.S_ISLNK(final.st_mode) or target is None:
            raise ArchiveError(f"retirement symlink is foreign: {path}")
    else:
        raise ArchiveError(f"unsupported retirement entry kind: {path}")
    row: dict[str, Any] = {
        "path": path, "kind": kind,
        "mode": stat.S_IMODE(final.st_mode), "uid": final.st_uid,
        "gid": final.st_gid, "mtime_ns": final.st_mtime_ns,
        "flags": bsd_flags(final), "xattrs": second_xattrs,
        "acl": second_acl, "allocated_bytes": allocated_bytes(final),
        "local_identity": list(identity(final)),
        "logical_bytes": final.st_size if kind == "regular" else 0,
        "_stat_guard": final_guard,
    }
    if kind == "regular":
        row["sha256"] = digest
    elif kind == "symlink":
        row["logical_bytes"] = len(os.fsencode(target))
        row["target"] = target
    return row


def retirement_manifest_row(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if key != "_stat_guard"}


def symlink_metadata(parent_fd: int, name: str, info: os.stat_result) -> dict[str, Any]:
    if O_SYMLINK == 0:
        raise ArchiveError("symlink metadata cannot be safely represented")
    descriptor = os.open(name, os.O_RDONLY | O_SYMLINK, dir_fd=parent_fd)
    try:
        linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if identity(os.fstat(descriptor)) != identity(info) or identity(linked) != identity(info):
            raise ArchiveError("symlink changed during metadata capture")
        attributes = xattrs_fd(descriptor)
        acl = acl_text_fd(descriptor)
        # Darwin O_SYMLINK permits descriptor-bound xattr capture/replay.  Link
        # ACLs and nonzero BSD flags have no equally safe fd-relative setter, so
        # those candidates are rejected instead of silently losing metadata.
        if acl or bsd_flags(info):
            raise ArchiveError("symlink ACL/flags are not safely representable")
        return {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid,
                "mtime_ns": info.st_mtime_ns, "flags": 0, "xattrs": attributes, "acl": "",
                "allocated_bytes": allocated_bytes(info)}
    finally:
        os.close(descriptor)


def _semantic_entry(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items()
            if key not in {"allocated_bytes", "local_identity"}}


def finish_manifest(entries: list[dict[str, Any]]) -> dict[str, Any]:
    entries.sort(key=lambda item: item["path"])
    semantic_entries = [_semantic_entry(item) for item in entries]
    regular = [row for row in entries if row["kind"] == "regular"]
    result = {
        "schema": SCHEMA,
        "entries": entries,
        "directories": sum(row["kind"] == "directory" for row in entries),
        "files": len(regular),
        "symlinks": sum(row["kind"] == "symlink" for row in entries),
        "logical_bytes": sum(row["logical_bytes"] for row in regular),
        "allocated_bytes": sum(row["allocated_bytes"] for row in entries),
    }
    semantic = {key: value for key, value in result.items()
                if key not in {"entries", "allocated_bytes"}}
    semantic["entries"] = semantic_entries
    result["tree_sha256"] = sha256_bytes(canonical_json(semantic))
    return result


def manifest_bound(root_fd: int, kind: str,
                   regular_hook: Callable[..., None] | None = None) -> dict[str, Any]:
    """Generate a complete tree manifest without deriving any filesystem path."""
    root_info = os.fstat(root_fd)
    expected_root = stat.S_ISDIR(root_info.st_mode) if kind == "directory" else stat.S_ISREG(root_info.st_mode)
    if not expected_root:
        raise ArchiveError("manifest root kind is invalid")
    if kind == "regular":
        before = os.fstat(root_fd)
        if before.st_nlink != 1:
            raise ArchiveError("regular manifest root has an external hard link")
        if regular_hook is not None:
            regular_hook(path=".", file_fd=root_fd, parent_fd=None, name=None)
        digest = sha256_fd(root_fd)
        after = os.fstat(root_fd)
        stable = ("st_dev", "st_ino", "st_size", "st_mode", "st_uid", "st_gid",
                  "st_mtime_ns", "st_ctime_ns")
        if after.st_nlink != 1 or any(
                getattr(before, field) != getattr(after, field) for field in stable):
            raise ArchiveError("regular manifest root changed or gained a hard link while hashing")
        row = {"path": ".", "kind": "regular", **entry_metadata(root_fd),
               "local_identity": list(identity(after)),
               "logical_bytes": after.st_size, "sha256": digest}
        return finish_manifest([row])

    root_device = root_info.st_dev
    root_row = {"path": ".", "kind": "directory", **entry_metadata(root_fd),
                "local_identity": list(identity(root_info)),
                "logical_bytes": 0}
    entries = [root_row]
    stack: list[tuple[str, int]] = [("", os.dup(root_fd))]
    try:
        while stack:
            prefix, directory_fd = stack.pop()
            try:
                if os.fstat(directory_fd).st_dev != root_device:
                    raise ArchiveError("cross-device directory traversal is forbidden")
                for name in sorted(os.listdir(directory_fd)):
                    require_leaf(name, "tree entry")
                    relative = f"{prefix}/{name}" if prefix else name
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if info.st_dev != root_device:
                        raise ArchiveError(f"cross-device entry is forbidden: {relative}")
                    if stat.S_ISDIR(info.st_mode):
                        child_fd = open_leaf(directory_fd, name, "directory")
                        linked = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        if identity(os.fstat(child_fd)) != identity(linked):
                            os.close(child_fd)
                            raise ArchiveError(f"directory changed during manifest: {relative}")
                        entries.append({"path": relative, "kind": "directory",
                                        **entry_metadata(child_fd),
                                        "local_identity": list(identity(os.fstat(child_fd))),
                                        "logical_bytes": 0})
                        stack.append((relative, child_fd))
                    elif stat.S_ISREG(info.st_mode):
                        child_fd = open_leaf(directory_fd, name, "regular")
                        try:
                            linked = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                            before = os.fstat(child_fd)
                            if identity(before) != identity(linked):
                                raise ArchiveError(f"file changed during manifest: {relative}")
                            if before.st_nlink != 1:
                                raise ArchiveError(
                                    f"regular entry has an external hard link: {relative}")
                            if regular_hook is not None:
                                regular_hook(path=relative, file_fd=child_fd,
                                             parent_fd=directory_fd, name=name)
                            digest = sha256_fd(child_fd)
                            after = os.fstat(child_fd)
                            rebound = os.stat(
                                name, dir_fd=directory_fd, follow_symlinks=False)
                            stable = ("st_dev", "st_ino", "st_size", "st_mode", "st_uid",
                                      "st_gid", "st_mtime_ns", "st_ctime_ns")
                            if (after.st_nlink != 1 or identity(rebound) != identity(before)
                                    or any(getattr(before, field) != getattr(after, field)
                                           for field in stable)):
                                raise ArchiveError(
                                    f"regular entry changed or gained a hard link while hashing: {relative}")
                            entries.append({"path": relative, "kind": "regular",
                                            **entry_metadata(child_fd),
                                            "local_identity": list(identity(after)),
                                            "logical_bytes": after.st_size,
                                            "sha256": digest})
                        finally:
                            os.close(child_fd)
                    elif stat.S_ISLNK(info.st_mode):
                        target = os.readlink(name, dir_fd=directory_fd)
                        entries.append({"path": relative, "kind": "symlink",
                                        **symlink_metadata(directory_fd, name, info),
                                        "local_identity": list(identity(info)),
                                        "logical_bytes": len(os.fsencode(target)), "target": target})
                    else:
                        raise ArchiveError(f"special file is forbidden: {relative}")
            finally:
                os.close(directory_fd)
    except BaseException:
        for _, descriptor in stack:
            os.close(descriptor)
        raise
    return finish_manifest(entries)


def manifests_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Cross-volume equality intentionally ignores physical allocation layout."""
    keys = ("directories", "files", "symlinks", "logical_bytes", "tree_sha256")
    return all(left.get(key) == right.get(key) for key in keys)


def manifest_canonical_sha256(manifest: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(manifest))


def provenance_overlay_proof_hash(proof: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in proof.items() if key != "proof_hash"}
    return sha256_bytes(canonical_json(unsigned))


def compare_provenance_overlay(
        source_manifest: dict[str, Any], physical_manifest: dict[str, Any], *,
        transaction_id: str, candidate_id: str, allowlist_version: int,
        policy: ProvenanceOverlayPolicy,
        authorization_sha256: str) -> dict[str, Any]:
    """Prove the sole reviewed APFS xattr overlay without weakening equality.

    All semantic fields remain exact under the existing cross-volume exclusions.
    The sole permitted physical difference is addition of the exact provenance
    xattr where the immutable source did not already carry that name.
    """
    if (candidate_id != policy.candidate_id
            or allowlist_version != policy.allowlist_version
            or source_manifest.get("tree_sha256") != policy.source_tree_sha256
            or physical_manifest.get("tree_sha256") != policy.physical_tree_sha256):
        raise ArchiveError("provenance overlay identity/tree contract differs")
    top_excluded = {"entries", "allocated_bytes", "tree_sha256"}
    source_top = {key: value for key, value in source_manifest.items()
                  if key not in top_excluded}
    physical_top = {key: value for key, value in physical_manifest.items()
                    if key not in top_excluded}
    if source_top != physical_top:
        raise ArchiveError("provenance overlay has non-entry manifest drift")
    if (source_manifest.get("files") != policy.file_count
            or source_manifest.get("directories") != policy.directory_count
            or source_manifest.get("symlinks") != 0
            or len(source_manifest.get("entries", [])) != policy.entry_count
            or len(physical_manifest.get("entries", [])) != policy.entry_count):
        raise ArchiveError("provenance overlay inventory count differs")

    def rows(manifest: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
        entries = manifest.get("entries")
        if not isinstance(entries, list):
            raise ArchiveError(f"{label} entries are invalid")
        result: dict[str, dict[str, Any]] = {}
        for row in entries:
            path = row.get("path") if isinstance(row, dict) else None
            if not isinstance(path, str) or path in result:
                raise ArchiveError(f"{label} inventory is invalid or duplicated")
            result[path] = row
        return result

    source_rows = rows(source_manifest, "source overlay manifest")
    physical_rows = rows(physical_manifest, "physical overlay manifest")
    if set(source_rows) != set(physical_rows):
        raise ArchiveError("provenance overlay path inventory differs")
    inherited: list[str] = []
    added: list[str] = []
    for path in sorted(source_rows):
        source = source_rows[path]
        physical = physical_rows[path]
        source_non_xattr = {key: value for key, value in source.items()
                            if key not in {"xattrs", "allocated_bytes", "local_identity"}}
        physical_non_xattr = {key: value for key, value in physical.items()
                              if key not in {"xattrs", "allocated_bytes", "local_identity"}}
        if source_non_xattr != physical_non_xattr:
            raise ArchiveError(f"provenance overlay has non-xattr drift: {path}")
        source_xattrs = source.get("xattrs")
        physical_xattrs = physical.get("xattrs")
        if not isinstance(source_xattrs, dict) or not isinstance(physical_xattrs, dict):
            raise ArchiveError(f"provenance overlay xattrs are invalid: {path}")
        if policy.xattr_name in source_xattrs:
            if physical_xattrs != source_xattrs:
                raise ArchiveError(f"inherited provenance xattrs differ: {path}")
            inherited.append(path)
        else:
            expected_xattrs = dict(source_xattrs)
            expected_xattrs[policy.xattr_name] = policy.xattr_value
            if physical_xattrs != expected_xattrs:
                raise ArchiveError(f"added provenance xattrs differ: {path}")
            added.append(path)
    added_paths_sha256 = sha256_bytes(canonical_json(added))
    if (len(inherited) != policy.inherited_count
            or len(added) != policy.added_count
            or added_paths_sha256 != policy.added_paths_sha256):
        raise ArchiveError("provenance overlay inherited/added contract differs")
    proof = {
        "schema": "openxray.apfs-provenance-overlay-proof.v1",
        "policy_version": policy.version,
        "xattr_name": policy.xattr_name,
        "xattr_value": policy.xattr_value,
        "transaction_id": transaction_id,
        "candidate_id": candidate_id,
        "allowlist_version": allowlist_version,
        "production_authorization_sha256": authorization_sha256,
        "source_semantic_tree_sha256": source_manifest["tree_sha256"],
        "source_manifest_sha256": manifest_canonical_sha256(source_manifest),
        "physical_tree_sha256": physical_manifest["tree_sha256"],
        "physical_manifest_sha256": manifest_canonical_sha256(physical_manifest),
        "total_entries": len(source_rows),
        "regular_files": source_manifest["files"],
        "directories": source_manifest["directories"],
        "inherited_count": len(inherited),
        "added_count": len(added),
        "added_paths_sha256": added_paths_sha256,
    }
    proof["proof_hash"] = provenance_overlay_proof_hash(proof)
    return proof


def provenance_overlay_proofs_equal(left: Any, right: Any) -> bool:
    return (isinstance(left, dict) and isinstance(right, dict)
            and left.get("proof_hash") == provenance_overlay_proof_hash(left)
            and right.get("proof_hash") == provenance_overlay_proof_hash(right)
            and canonical_json(left) == canonical_json(right))


def source_root_overlay_proof_hash(proof: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in proof.items() if key != "proof_hash"}
    return sha256_bytes(canonical_json(unsigned))


def compare_source_root_overlay(
        semantic_manifest: dict[str, Any], physical_manifest: dict[str, Any], *,
        policy: SourceRootOverlayPolicy,
        production_authorization_sha256: str) -> dict[str, Any]:
    """Prove the exact reviewed root-only source provenance overlay.

    Unlike cross-volume comparison, this same-inode proof includes allocation
    and local identities.  Every child row is byte-for-byte canonical equal;
    only the root xattr map may add the exact reviewed provenance value.
    """
    if (manifest_canonical_sha256(semantic_manifest) != policy.semantic_manifest_sha256
            or semantic_manifest.get("tree_sha256") != policy.semantic_tree_sha256
            or manifest_canonical_sha256(physical_manifest) != policy.physical_manifest_sha256
            or physical_manifest.get("tree_sha256") != policy.physical_tree_sha256):
        raise ArchiveError("source-root overlay manifest identity differs")
    if (semantic_manifest.get("files") != policy.file_count
            or semantic_manifest.get("directories") != policy.directory_count
            or semantic_manifest.get("symlinks") != 0
            or len(semantic_manifest.get("entries", [])) != policy.entry_count
            or len(physical_manifest.get("entries", [])) != policy.entry_count):
        raise ArchiveError("source-root overlay inventory count differs")
    top_excluded = {"entries", "tree_sha256"}
    if ({key: value for key, value in semantic_manifest.items()
         if key not in top_excluded}
            != {key: value for key, value in physical_manifest.items()
                if key not in top_excluded}):
        raise ArchiveError("source-root overlay top-level fields differ")

    def indexed(manifest: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for row in manifest.get("entries", []):
            path = row.get("path") if isinstance(row, dict) else None
            if not isinstance(path, str) or path in result:
                raise ArchiveError(f"{label} inventory is invalid")
            result[path] = row
        return result

    semantic = indexed(semantic_manifest, "semantic source-root manifest")
    physical = indexed(physical_manifest, "physical source-root manifest")
    if set(semantic) != set(physical) or set(semantic) == set():
        raise ArchiveError("source-root overlay path inventory differs")
    for path in sorted(semantic):
        if path != "." and canonical_json(semantic[path]) != canonical_json(physical[path]):
            raise ArchiveError(f"source-root overlay child differs: {path}")
    semantic_root = semantic.get(".")
    physical_root = physical.get(".")
    if semantic_root is None or physical_root is None:
        raise ArchiveError("source-root overlay lacks its root")
    if ({key: value for key, value in semantic_root.items() if key != "xattrs"}
            != {key: value for key, value in physical_root.items() if key != "xattrs"}):
        raise ArchiveError("source-root overlay root fields differ")
    source_xattrs = semantic_root.get("xattrs")
    physical_xattrs = physical_root.get("xattrs")
    if not isinstance(source_xattrs, dict) or not isinstance(physical_xattrs, dict) \
            or policy.xattr_name in source_xattrs:
        raise ArchiveError("source-root semantic xattr contract differs")
    expected_xattrs = dict(source_xattrs)
    expected_xattrs[policy.xattr_name] = policy.xattr_value
    if physical_xattrs != expected_xattrs:
        raise ArchiveError("source-root physical xattrs differ")
    added_paths = ["."]
    if (policy.added_count != 1
            or sha256_bytes(canonical_json(added_paths)) != policy.added_paths_sha256):
        raise ArchiveError("source-root overlay path proof differs")
    proof = {
        "schema": "openxray.source-root-provenance-overlay-proof.v1",
        "policy_version": policy.version,
        "xattr_name": policy.xattr_name,
        "xattr_value": policy.xattr_value,
        "transaction_id": policy.transaction_id,
        "candidate_id": policy.candidate_id,
        "source_path": policy.source_path,
        "source_identity": list(policy.source_identity),
        "source_parent_identity": list(policy.source_parent_identity),
        "allowlist_version": policy.allowlist_version,
        "semantic_tree_sha256": semantic_manifest["tree_sha256"],
        "semantic_manifest_sha256": manifest_canonical_sha256(semantic_manifest),
        "physical_tree_sha256": physical_manifest["tree_sha256"],
        "physical_manifest_sha256": manifest_canonical_sha256(physical_manifest),
        "total_entries": policy.entry_count,
        "regular_files": policy.file_count,
        "directories": policy.directory_count,
        "added_count": policy.added_count,
        "added_paths_sha256": policy.added_paths_sha256,
        "production_authorization_sha256": production_authorization_sha256,
    }
    proof["proof_hash"] = source_root_overlay_proof_hash(proof)
    return proof


def source_root_overlay_proofs_equal(left: Any, right: Any) -> bool:
    return (isinstance(left, dict) and isinstance(right, dict)
            and left.get("proof_hash") == source_root_overlay_proof_hash(left)
            and right.get("proof_hash") == source_root_overlay_proof_hash(right)
            and canonical_json(left) == canonical_json(right))


def reconstruct_source_root_overlay_proof(
        semantic_manifest: dict[str, Any], physical_manifest: dict[str, Any], *,
        policy: SourceRootOverlayPolicy, authorization_sha256: str,
        stored_proof: Any = None) -> dict[str, Any]:
    """Rebuild one proof from policy, never from its stored authorization.

    ``authorization_sha256`` is selected by reviewed code before this helper is
    called.  A stored digest is only an equality target: it can neither select
    the historical branch nor authorize itself.
    """
    expected = compare_source_root_overlay(
        semantic_manifest, physical_manifest, policy=policy,
        production_authorization_sha256=authorization_sha256)
    if stored_proof is not None and not source_root_overlay_proofs_equal(
            stored_proof, expected):
        raise ArchiveError("source-root overlay proof differs")
    return expected


def source_root_overlay_historical_authorization_for_tuple(
        record: dict[str, Any], policy: SourceRootOverlayPolicy, *,
        source_quarantined_sha256: str, retirement_started_sha256: str,
        legacy_prefix_sha256: str) -> str | None:
    """Return ``d0`` only for the immutable reviewed production tuple."""
    exact = (
        policy == PRODUCTION_SOURCE_ROOT_OVERLAY,
        record.get("transaction_id") == SOURCE_ROOT_OVERLAY_TRANSACTION_ID,
        record.get("candidate_id") == SOURCE_ROOT_OVERLAY_CANDIDATE_ID,
        record.get("source") == SOURCE_ROOT_OVERLAY_PATH,
        tuple(record.get("source_identity", ())) == SOURCE_ROOT_OVERLAY_IDENTITY,
        tuple(record.get("source_parent_identity", ()))
        == SOURCE_ROOT_OVERLAY_PARENT_IDENTITY,
        record.get("allowlist_version") == ALLOWLIST_VERSION,
        record.get("source_tree_sha256")
        == SOURCE_ROOT_OVERLAY_SEMANTIC_TREE_SHA256,
        record.get("category") == RETAIL_BACKUP_CANDIDATE.category,
        record.get("data_class") == RETAIL_BACKUP_CANDIDATE.data_class,
        record.get("deletion_rule") == RETAIL_BACKUP_CANDIDATE.deletion_rule,
        source_quarantined_sha256 == RETIREMENT_OVERLAY_BASELINE_008_SHA256,
        retirement_started_sha256 == RETIREMENT_OVERLAY_BASELINE_009_SHA256,
        legacy_prefix_sha256 == SOURCE_ROOT_OVERLAY_LEGACY_PREFIX_SHA256,
    )
    return SOURCE_ROOT_OVERLAY_HISTORICAL_AUTHORIZATION_SHA256 \
        if all(exact) else None


def retirement_overlay_proof_hash(proof: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in proof.items() if key != "proof_hash"}
    return sha256_bytes(canonical_json(unsigned))


def retirement_pretruncate_open_proof_hash(proof: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in proof.items() if key != "proof_hash"}
    return sha256_bytes(canonical_json(unsigned))


def build_retirement_pretruncate_open_base_manifest(
        baseline_manifest: dict[str, Any], *,
        policy: RetirementPretruncateOpenPolicy) -> dict[str, Any]:
    """Derive the immutable pre-009b base from exact 009a plus one xattr."""
    if (manifest_canonical_sha256(baseline_manifest)
            != policy.baseline_manifest_sha256
            or baseline_manifest.get("tree_sha256") != policy.baseline_tree_sha256):
        raise ArchiveError("pretruncate open 009a manifest identity differs")
    entries = strict_json_loads(
        canonical_json(baseline_manifest.get("entries")).decode("ascii"))
    if not isinstance(entries, list):
        raise ArchiveError("pretruncate open 009a entries are invalid")
    changed = [row for row in entries
               if isinstance(row, dict) and row.get("path") == policy.changed_path]
    if len(changed) != 1:
        raise ArchiveError("pretruncate open changed path is absent")
    row = changed[0]
    if (row.get("kind") != "regular"
            or tuple(row.get("local_identity", ())) != policy.changed_identity
            or row.get("logical_bytes") != policy.changed_size
            or row.get("sha256") != policy.changed_sha256
            or row.get("mode") != policy.changed_mode
            or row.get("mtime_ns") != policy.changed_mtime_ns):
        raise ArchiveError("pretruncate open changed file identity differs")
    xattrs = row.get("xattrs")
    if (not isinstance(xattrs, dict)
            or policy.runtime_xattr_name in xattrs):
        raise ArchiveError("pretruncate open changed file xattrs differ")
    row["xattrs"] = {
        **xattrs, policy.runtime_xattr_name: policy.runtime_xattr_value}
    result = finish_manifest(entries)
    if (manifest_canonical_sha256(result) != policy.pre_open_manifest_sha256
            or result.get("tree_sha256") != policy.pre_open_tree_sha256):
        raise ArchiveError("pretruncate open derived base identity differs")
    changed_paths = [policy.changed_path]
    if sha256_bytes(canonical_json(changed_paths)) \
            != policy.changed_paths_sha256:
        raise ArchiveError("pretruncate open changed path digest differs")
    rows = manifest_rows_exact(result, "pretruncate open base")
    eligible = sorted(
        path for path, item in rows.items()
        if item.get("kind") == "regular"
        and policy.runtime_xattr_name not in item.get("xattrs", {}))
    inherited = sorted(
        path for path, item in rows.items()
        if policy.runtime_xattr_name in item.get("xattrs", {}))
    if (len(rows) != policy.entry_count
            or result.get("files") != policy.file_count
            or result.get("directories") != policy.directory_count
            or result.get("symlinks") != policy.symlink_count
            or len(eligible) != policy.eligible_count
            or sha256_bytes(canonical_json(eligible))
            != policy.eligible_paths_sha256
            or len(inherited) != policy.inherited_count
            or sha256_bytes(canonical_json(inherited))
            != policy.inherited_paths_sha256):
        raise ArchiveError("pretruncate open base inventory differs")
    return result


def retirement_pretruncate_open_baseline_proof(
        baseline_manifest: dict[str, Any], base_manifest: dict[str, Any], *,
        policy: RetirementPretruncateOpenPolicy,
        production_authorization_sha256: str) -> dict[str, Any]:
    rebuilt = build_retirement_pretruncate_open_base_manifest(
        baseline_manifest, policy=policy)
    if canonical_json(rebuilt) != canonical_json(base_manifest):
        raise ArchiveError("pretruncate open baseline reconstruction differs")
    proof = {
        "schema": policy.schema,
        "policy_version": policy.version,
        "transaction_id": policy.transaction_id,
        "candidate_id": policy.candidate_id,
        "source": policy.source_path,
        "source_identity": list(policy.source_identity),
        "source_parent_identity": list(policy.source_parent_identity),
        "allowlist_version": policy.allowlist_version,
        "baseline_manifest_sha256": policy.baseline_manifest_sha256,
        "baseline_tree_sha256": policy.baseline_tree_sha256,
        "base_manifest_sha256": policy.pre_open_manifest_sha256,
        "base_tree_sha256": policy.pre_open_tree_sha256,
        "changed_path": policy.changed_path,
        "changed_identity": list(policy.changed_identity),
        "changed_size": policy.changed_size,
        "changed_sha256": policy.changed_sha256,
        "changed_mode": policy.changed_mode,
        "changed_mtime_ns": policy.changed_mtime_ns,
        "changed_paths_sha256": policy.changed_paths_sha256,
        "eligible_count": policy.eligible_count,
        "eligible_paths_sha256": policy.eligible_paths_sha256,
        "inherited_count": policy.inherited_count,
        "inherited_paths_sha256": policy.inherited_paths_sha256,
        "runtime_xattr_name": policy.runtime_xattr_name,
        "runtime_xattr_value": policy.runtime_xattr_value,
        "production_authorization_sha256": production_authorization_sha256,
    }
    proof["proof_hash"] = retirement_pretruncate_open_proof_hash(proof)
    return proof


def compare_retirement_pretruncate_open_overlay(
        base_manifest: dict[str, Any], current_manifest: dict[str, Any], *,
        policy: RetirementPretruncateOpenPolicy,
        production_authorization_sha256: str) -> dict[str, Any]:
    """Admit only monotonic exact provenance additions before truncation."""
    if (manifest_canonical_sha256(base_manifest)
            != policy.pre_open_manifest_sha256
            or base_manifest.get("tree_sha256") != policy.pre_open_tree_sha256):
        raise ArchiveError("pretruncate open immutable base differs")
    base = manifest_rows_exact(base_manifest, "pretruncate open base")
    current = manifest_rows_exact(current_manifest, "pretruncate open current")
    if (set(base) != set(current) or len(base) != policy.entry_count
            or current_manifest.get("files") != policy.file_count
            or current_manifest.get("directories") != policy.directory_count
            or current_manifest.get("symlinks") != policy.symlink_count):
        raise ArchiveError("pretruncate open inventory differs")
    top_excluded = {"entries", "tree_sha256"}
    if ({key: value for key, value in base_manifest.items()
         if key not in top_excluded}
            != {key: value for key, value in current_manifest.items()
                if key not in top_excluded}):
        raise ArchiveError("pretruncate open aggregate fields differ")
    observed: list[str] = []
    for path in sorted(base):
        before = base[path]
        after = current[path]
        if before.get("kind") != "regular":
            if canonical_json(before) != canonical_json(after):
                raise ArchiveError(
                    f"pretruncate open non-regular drift: {path}")
            continue
        before_without = {key: value for key, value in before.items()
                          if key != "xattrs"}
        after_without = {key: value for key, value in after.items()
                         if key != "xattrs"}
        if before_without != after_without:
            raise ArchiveError(
                f"pretruncate open regular metadata/content drift: {path}")
        before_xattrs = before.get("xattrs")
        after_xattrs = after.get("xattrs")
        if not isinstance(before_xattrs, dict) \
                or not isinstance(after_xattrs, dict):
            raise ArchiveError(f"pretruncate open xattrs invalid: {path}")
        if policy.runtime_xattr_name in before_xattrs:
            if after_xattrs != before_xattrs:
                raise ArchiveError(
                    f"pretruncate open inherited provenance drift: {path}")
        elif after_xattrs == before_xattrs:
            continue
        else:
            expected = {
                **before_xattrs,
                policy.runtime_xattr_name: policy.runtime_xattr_value}
            if after_xattrs != expected:
                raise ArchiveError(
                    f"pretruncate open provenance differs: {path}")
            observed.append(path)
    proof = {
        "schema": policy.schema,
        "policy_version": policy.version,
        "transaction_id": policy.transaction_id,
        "candidate_id": policy.candidate_id,
        "source": policy.source_path,
        "source_identity": list(policy.source_identity),
        "source_parent_identity": list(policy.source_parent_identity),
        "allowlist_version": policy.allowlist_version,
        "base_manifest_sha256": policy.pre_open_manifest_sha256,
        "base_tree_sha256": policy.pre_open_tree_sha256,
        "current_manifest_sha256": manifest_canonical_sha256(current_manifest),
        "current_tree_sha256": current_manifest["tree_sha256"],
        "observed_paths": observed,
        "observed_count": len(observed),
        "observed_paths_sha256": sha256_bytes(canonical_json(observed)),
        "eligible_count": policy.eligible_count,
        "eligible_paths_sha256": policy.eligible_paths_sha256,
        "inherited_count": policy.inherited_count,
        "inherited_paths_sha256": policy.inherited_paths_sha256,
        "runtime_xattr_name": policy.runtime_xattr_name,
        "runtime_xattr_value": policy.runtime_xattr_value,
        "production_authorization_sha256": production_authorization_sha256,
    }
    proof["proof_hash"] = retirement_pretruncate_open_proof_hash(proof)
    return proof


def manifest_rows_exact(manifest: dict[str, Any], label: str) \
        -> dict[str, dict[str, Any]]:
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ArchiveError(f"{label} entries are invalid")
    rows: dict[str, dict[str, Any]] = {}
    for row in entries:
        path = row.get("path") if isinstance(row, dict) else None
        if not isinstance(path, str) or path in rows:
            raise ArchiveError(f"{label} inventory is invalid or duplicated")
        rows[path] = row
    return rows


def compare_retirement_overlay_baseline(
        immutable_manifest: dict[str, Any], current_manifest: dict[str, Any], *,
        policy: RetirementOverlayBaselinePolicy,
        production_authorization_sha256: str) -> dict[str, Any]:
    """Admit only the reviewed post-009 ``.DS_Store`` provenance addition."""
    if (manifest_canonical_sha256(immutable_manifest)
            != policy.immutable_manifest_sha256
            or immutable_manifest.get("tree_sha256") != policy.immutable_tree_sha256
            or manifest_canonical_sha256(current_manifest)
            != policy.current_manifest_sha256
            or current_manifest.get("tree_sha256") != policy.current_tree_sha256):
        raise ArchiveError("retirement overlay baseline manifest identity differs")
    counts = (policy.file_count, policy.directory_count, policy.symlink_count)
    if ((immutable_manifest.get("files"), immutable_manifest.get("directories"),
         immutable_manifest.get("symlinks")) != counts
            or (current_manifest.get("files"), current_manifest.get("directories"),
                current_manifest.get("symlinks")) != counts):
        raise ArchiveError("retirement overlay baseline inventory count differs")
    immutable = manifest_rows_exact(immutable_manifest, "immutable retirement baseline")
    current = manifest_rows_exact(current_manifest, "current retirement baseline")
    if (len(immutable) != policy.entry_count or set(immutable) != set(current)):
        raise ArchiveError("retirement overlay baseline inventory differs")
    top_excluded = {"entries", "tree_sha256"}
    if ({key: value for key, value in immutable_manifest.items()
         if key not in top_excluded}
            != {key: value for key, value in current_manifest.items()
                if key not in top_excluded}):
        raise ArchiveError("retirement overlay baseline aggregate fields differ")
    changed: list[str] = []
    for path in sorted(immutable):
        before = immutable[path]
        after = current[path]
        if path != policy.changed_path:
            if canonical_json(before) != canonical_json(after):
                raise ArchiveError(f"retirement overlay has foreign drift: {path}")
            continue
        if (before.get("kind") != "regular"
                or tuple(before.get("local_identity", ())) != policy.changed_identity
                or tuple(after.get("local_identity", ())) != policy.changed_identity):
            raise ArchiveError("retirement overlay changed inode binding differs")
        if ({key: value for key, value in before.items() if key != "xattrs"}
                != {key: value for key, value in after.items() if key != "xattrs"}
                or tuple(sorted(before.get("xattrs", {}).items()))
                != policy.changed_old_xattrs
                or tuple(sorted(after.get("xattrs", {}).items()))
                != policy.changed_new_xattrs):
            raise ArchiveError("retirement overlay changed xattrs differ")
        changed.append(path)
    if (len(changed) != policy.changed_count
            or sha256_bytes(canonical_json(changed))
            != policy.changed_paths_sha256):
        raise ArchiveError("retirement overlay changed path proof differs")
    eligible = sorted(
        path for path, row in current.items()
        if row.get("kind") == "regular"
        and policy.runtime_xattr_name not in row.get("xattrs", {}))
    inherited = sorted(
        path for path, row in current.items()
        if policy.runtime_xattr_name in row.get("xattrs", {}))
    if (len(eligible) != policy.eligible_count
            or sha256_bytes(canonical_json(eligible))
            != policy.eligible_paths_sha256
            or sha256_bytes(canonical_json(inherited))
            != policy.inherited_paths_sha256):
        raise ArchiveError("retirement overlay runtime eligibility differs")
    proof = {
        "schema": policy.schema,
        "policy_version": policy.version,
        "eligibility_version": policy.eligibility_version,
        "transaction_id": policy.transaction_id,
        "candidate_id": policy.candidate_id,
        "source": policy.source_path,
        "source_identity": list(policy.source_identity),
        "source_parent_identity": list(policy.source_parent_identity),
        "allowlist_version": policy.allowlist_version,
        "immutable_manifest_sha256": policy.immutable_manifest_sha256,
        "immutable_tree_sha256": policy.immutable_tree_sha256,
        "current_manifest_sha256": policy.current_manifest_sha256,
        "current_tree_sha256": policy.current_tree_sha256,
        "changed_path": policy.changed_path,
        "changed_identity": list(policy.changed_identity),
        "changed_count": policy.changed_count,
        "changed_paths_sha256": policy.changed_paths_sha256,
        "eligible_count": policy.eligible_count,
        "eligible_paths_sha256": policy.eligible_paths_sha256,
        "inherited_paths_sha256": policy.inherited_paths_sha256,
        "runtime_xattr_name": policy.runtime_xattr_name,
        "runtime_xattr_value": policy.runtime_xattr_value,
        "production_authorization_sha256": production_authorization_sha256,
    }
    proof["proof_hash"] = retirement_overlay_proof_hash(proof)
    return proof


def compare_retirement_runtime_overlay(
        baseline_manifest: dict[str, Any], tombstone_manifest: dict[str, Any], *,
        policy: RetirementOverlayBaselinePolicy,
        baseline_proof: dict[str, Any]) -> dict[str, Any]:
    """Prove a zero-data tombstone plus an OS provenance subset."""
    if (manifest_canonical_sha256(baseline_manifest)
            != policy.current_manifest_sha256
            or baseline_manifest.get("tree_sha256") != policy.current_tree_sha256
            or baseline_proof.get("proof_hash")
            != retirement_overlay_proof_hash(baseline_proof)):
        raise ArchiveError("runtime overlay baseline proof differs")
    baseline = manifest_rows_exact(baseline_manifest, "runtime overlay baseline")
    current = manifest_rows_exact(tombstone_manifest, "runtime overlay tombstone")
    if set(baseline) != set(current) or len(current) != policy.entry_count:
        raise ArchiveError("runtime overlay inventory differs")
    observed: list[str] = []
    empty_sha = sha256_bytes(b"")
    for path in sorted(baseline):
        before = baseline[path]
        after = current[path]
        if before.get("kind") != "regular":
            if canonical_json(before) != canonical_json(after):
                raise ArchiveError(f"runtime overlay non-regular drift: {path}")
            continue
        exact_fields = set(before) - {
            "logical_bytes", "allocated_bytes", "sha256", "xattrs"}
        if any(canonical_json(before.get(key)) != canonical_json(after.get(key))
               for key in exact_fields):
            raise ArchiveError(f"runtime overlay regular metadata drift: {path}")
        allocated = after.get("allocated_bytes")
        if (after.get("logical_bytes") != 0
                or not isinstance(allocated, int) or isinstance(allocated, bool)
                or allocated < 0 or after.get("sha256") != empty_sha):
            raise ArchiveError(f"runtime overlay regular is not empty: {path}")
        before_xattrs = before.get("xattrs")
        after_xattrs = after.get("xattrs")
        if not isinstance(before_xattrs, dict) or not isinstance(after_xattrs, dict):
            raise ArchiveError(f"runtime overlay xattrs are invalid: {path}")
        if policy.runtime_xattr_name in before_xattrs:
            if after_xattrs != before_xattrs:
                raise ArchiveError(f"runtime inherited provenance changed: {path}")
        elif after_xattrs == before_xattrs:
            pass
        else:
            expected = dict(before_xattrs)
            expected[policy.runtime_xattr_name] = policy.runtime_xattr_value
            if after_xattrs != expected:
                raise ArchiveError(f"runtime provenance addition differs: {path}")
            observed.append(path)
    observed_hash = sha256_bytes(canonical_json(observed))
    proof = {
        "schema": "openxray.retirement-runtime-provenance-proof.v1",
        "policy_version": policy.version,
        "eligibility_version": policy.eligibility_version,
        "transaction_id": policy.transaction_id,
        "candidate_id": policy.candidate_id,
        "baseline_proof_sha256": sha256_bytes(canonical_json(baseline_proof)),
        "baseline_manifest_sha256": manifest_canonical_sha256(baseline_manifest),
        "tombstone_manifest_sha256": manifest_canonical_sha256(tombstone_manifest),
        "tombstone_tree_sha256": tombstone_manifest["tree_sha256"],
        "eligible_count": policy.eligible_count,
        "eligible_paths_sha256": policy.eligible_paths_sha256,
        "observed_count": len(observed),
        "observed_paths": observed,
        "observed_paths_sha256": observed_hash,
        "runtime_xattr_name": policy.runtime_xattr_name,
        "runtime_xattr_value": policy.runtime_xattr_value,
    }
    proof["proof_hash"] = retirement_overlay_proof_hash(proof)
    return proof


def reviewed_legacy_overlay_recovery_tuple(
        candidate_record: dict[str, Any], copy_record: dict[str, Any],
        proof: dict[str, Any], *, candidate_record_sha256: str,
        copy_record_sha256: str) -> bool:
    """Recognize only the one Sol-reviewed, already durable retail tuple."""
    return all((
        candidate_record.get("transaction_id")
        == PROVENANCE_OVERLAY_RECOVERY_TRANSACTION_ID,
        candidate_record.get("candidate_id") == PROVENANCE_OVERLAY_CANDIDATE_ID,
        candidate_record.get("allowlist_version") == ALLOWLIST_VERSION,
        candidate_record.get("source_tree_sha256")
        == PROVENANCE_OVERLAY_SOURCE_TREE_SHA256,
        hmac.compare_digest(
            candidate_record_sha256,
            PROVENANCE_OVERLAY_RECOVERY_CANDIDATE_SHA256),
        set(copy_record) == {
            "schema", "stage", "transaction_id", "payload_identity",
            "destination_manifest"},
        copy_record.get("schema") == SCHEMA,
        copy_record.get("stage") == "copy-complete",
        copy_record.get("transaction_id")
        == PROVENANCE_OVERLAY_RECOVERY_TRANSACTION_ID,
        hmac.compare_digest(
            copy_record_sha256,
            PROVENANCE_OVERLAY_RECOVERY_COPY_COMPLETE_SHA256),
        proof.get("physical_tree_sha256")
        == PROVENANCE_OVERLAY_PHYSICAL_TREE_SHA256,
        proof.get("physical_manifest_sha256")
        == PROVENANCE_OVERLAY_RECOVERY_PHYSICAL_MANIFEST_SHA256,
        proof.get("source_semantic_tree_sha256")
        == PROVENANCE_OVERLAY_SOURCE_TREE_SHA256,
        proof.get("inherited_count") == PROVENANCE_OVERLAY_INHERITED_COUNT,
        proof.get("added_count") == PROVENANCE_OVERLAY_ADDED_COUNT,
        proof.get("added_paths_sha256")
        == PROVENANCE_OVERLAY_ADDED_PATHS_SHA256,
    ))


def reviewed_legacy_overlay_recovery(
        candidate_record: dict[str, Any], copy_record: dict[str, Any],
        proof: dict[str, Any]) -> bool:
    return reviewed_legacy_overlay_recovery_tuple(
        candidate_record, copy_record, proof,
        candidate_record_sha256=sha256_bytes(canonical_json(candidate_record)),
        copy_record_sha256=sha256_bytes(canonical_json(copy_record)))


class DarwinPlatform:
    def open_mount(self, path: Path) -> int:
        return open_absolute_directory(path, "DevArchive mount")

    def volume_attrs(self, fd: int) -> dict[str, str]:
        if sys.platform != "darwin":
            raise DeferredVolume("DevArchive validation requires Darwin")

        class AttrList(ctypes.Structure):
            _fields_ = [("bitmapcount", ctypes.c_ushort), ("reserved", ctypes.c_uint16),
                        ("commonattr", ctypes.c_uint32), ("volattr", ctypes.c_uint32),
                        ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32),
                        ("forkattr", ctypes.c_uint32)]

        attrs = AttrList(5, 0, 0, 0x00000001 | 0x00001000 | 0x00002000 |
                         0x00040000 | 0x00100000, 0, 0, 0)
        buffer = ctypes.create_string_buffer(4096)
        function = ctypes.CDLL(None, use_errno=True).fgetattrlist
        function.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_size_t, ctypes.c_ulong)
        function.restype = ctypes.c_int
        if function(fd, ctypes.byref(attrs), buffer, ctypes.sizeof(buffer), 0) != 0:
            number = ctypes.get_errno()
            raise DeferredVolume(f"fgetattrlist failed: {os.strerror(number)}")
        raw = buffer.raw
        length = int.from_bytes(raw[:4], "little")
        if length < 4 or length > len(raw):
            raise DeferredVolume("fgetattrlist returned an invalid buffer")
        cursor = 8  # length + ATTR_VOL_FSTYPE

        def reference() -> str:
            nonlocal cursor
            base = cursor
            offset = int.from_bytes(raw[cursor:cursor + 4], "little", signed=True)
            size = int.from_bytes(raw[cursor + 4:cursor + 8], "little")
            cursor += 8
            end = base + offset + size
            if offset < 0 or size == 0 or end > length:
                raise DeferredVolume("fgetattrlist returned an invalid string reference")
            return raw[base + offset:end].rstrip(b"\0").decode("utf-8", "strict")

        mountpoint = reference()
        name = reference()
        volume_uuid = str(uuid.UUID(bytes=raw[cursor:cursor + 16])).upper()
        cursor += 16
        filesystem = reference()
        return {"mountpoint": mountpoint, "name": name, "uuid": volume_uuid,
                "filesystem": filesystem}

    def rename_exclusive(self, source_parent_fd: int, source: str,
                         destination_parent_fd: int, destination: str) -> None:
        function = getattr(ctypes.CDLL(None, use_errno=True), "renameatx_np", None)
        if function is None:
            raise ArchiveError("renameatx_np is unavailable")
        function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                             ctypes.c_char_p, ctypes.c_uint)
        function.restype = ctypes.c_int
        if function(source_parent_fd, os.fsencode(source), destination_parent_fd,
                    os.fsencode(destination), RENAME_EXCL) != 0:
            number = ctypes.get_errno()
            raise ArchiveError(f"exclusive rename failed: {os.strerror(number)}")

    def fsync(self, fd: int) -> None:
        os.fsync(fd)

    def write(self, fd: int, value: bytes) -> int:
        return os.write(fd, value)

    def now_unix(self) -> float:
        return time.time()

    def gate_input_hash(self, settings: Settings) -> str:
        completed = subprocess.run(
            (sys.executable, str(settings.repo_root / "misc/ios/gate_hash.py"),
             "--salt", "ios-device-artifact-v2", "--build-context-root",
             str(settings.repo_root), "--ios-artifact-root", str(settings.repo_root)),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        digest = completed.stdout.strip()
        if completed.returncode != 0 or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ArchiveError(
                f"cannot compute current full-gate input hash: {completed.stderr.strip()}")
        return digest

    def gate_state(self, settings: Settings) -> dict[str, Any]:
        root = settings.repo_root
        status = subprocess.run(
            ("git", "-C", str(root), "status", "--porcelain=v2", "-z"),
            stdout=subprocess.PIPE, check=True).stdout
        diff = subprocess.run(
            ("git", "-C", str(root), "diff", "--binary", "HEAD"),
            stdout=subprocess.PIPE, check=True).stdout
        names = subprocess.run(
            ("git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"),
            stdout=subprocess.PIPE, check=True).stdout.split(b"\0")
        untracked: list[dict[str, str]] = []
        for raw in names:
            if not raw:
                continue
            name = raw.decode("utf-8", errors="surrogateescape")
            path = root / name
            detail = path.lstat()
            if stat.S_ISLNK(detail.st_mode) or not stat.S_ISREG(detail.st_mode):
                raise ArchiveError(f"unsafe untracked gate path: {name}")
            untracked.append({"path": name, "sha256": sha256_file(path)})
        head_process = subprocess.run(
            ("git", "-C", str(root), "rev-parse", "--verify", "HEAD"),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        head = head_process.stdout.decode(errors="replace").strip() \
            if head_process.returncode == 0 else "unavailable"
        return {
            "head": head,
            "status_sha256": sha256_bytes(status),
            "diff_binary_head_sha256": sha256_bytes(diff),
            "untracked": sorted(untracked, key=lambda item: item["path"]),
        }


@dataclass
class ArchiveService:
    settings: Settings = field(default_factory=Settings)
    platform: DarwinPlatform = field(default_factory=DarwinPlatform)
    hooks: dict[str, Callable[..., None]] = field(default_factory=dict)

    def production_authorization_valid(self) -> bool:
        if not self.settings.production:
            return bool(self.settings.review_authorized)
        reviewed = reviewed_allowlist_authorized(
            ALLOWLIST_VERSION, self.settings.candidates)
        return (reviewed and PRODUCTION_REVIEW_AUTHORIZED == reviewed
                and self.settings.review_authorized == reviewed
                and self.settings.candidates == INITIAL_CANDIDATES)

    def historical_completed_policy(self) -> HistoricalCompletedRetirementPolicy:
        """Select the sole reviewed read-only completed-retirement capability.

        This selector is intentionally not configurable through Settings, the
        CLI or CandidateSpec.  Changing any tuple field invalidates the separate
        Sol-reviewed authorization digest without changing mutation authority.
        """
        policy = PRODUCTION_HISTORICAL_COMPLETED
        if (not self.settings.production
                or type(self.platform) is not DarwinPlatform
                or not self.production_authorization_valid()
                or policy.transaction_id != SOURCE_ROOT_OVERLAY_TRANSACTION_ID
                or policy.candidate_id != SOURCE_ROOT_OVERLAY_CANDIDATE_ID
                or policy.source_path != SOURCE_ROOT_OVERLAY_PATH
                or policy.source_identity != SOURCE_ROOT_OVERLAY_IDENTITY
                or policy.source_parent_identity
                != SOURCE_ROOT_OVERLAY_PARENT_IDENTITY
                or policy.allowlist_version != ALLOWLIST_VERSION
                or policy.production_authorization_sha256
                != REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256
                or not hmac.compare_digest(
                    historical_completed_policy_authorization_sha256(policy),
                    HISTORICAL_COMPLETED_POLICY_AUTHORIZATION_SHA256)):
            raise ArchiveError(
                "historical completed-retirement policy is not authorized")
        return policy

    def historical_prepared_overlay_policy(
            self, completed: HistoricalCompletedRetirementPolicy
            ) -> HistoricalPreparedMetadataOverlayPolicy:
        """Select the sole reviewed read-only prepared-cache exception."""
        policy = PRODUCTION_HISTORICAL_PREPARED_OVERLAY
        if (not self.settings.production
                or type(self.platform) is not DarwinPlatform
                or completed != PRODUCTION_HISTORICAL_COMPLETED
                or completed.transaction_id != policy.transaction_id
                or completed.candidate_id != policy.candidate_id
                or completed.external_second_copy_name
                != policy.external_second_copy_name
                or completed.external_second_copy_sha256
                != policy.external_second_copy_sha256
                or completed.prepared_root_identity
                != policy.prepared_root_identity
                or policy.historical_policy_authorization_sha256
                != HISTORICAL_COMPLETED_POLICY_AUTHORIZATION_SHA256
                or not hmac.compare_digest(
                    historical_prepared_overlay_authorization_sha256(policy),
                    HISTORICAL_PREPARED_OVERLAY_AUTHORIZATION_SHA256)):
            raise ArchiveError(
                "historical prepared metadata-overlay policy is not authorized")
        return policy

    def __post_init__(self) -> None:
        if not self.settings.production:
            return
        reviewed = reviewed_allowlist_authorized(
            ALLOWLIST_VERSION, self.settings.candidates)
        fixed = (
            self.settings.queue_root == QUEUE_ROOT,
            self.settings.mount == VOLUME_MOUNT,
            self.settings.volume_name == VOLUME_NAME,
            self.settings.volume_uuid == VOLUME_UUID,
            self.settings.repo_root == REPO_ROOT,
            self.settings.gate_log_root == GATE_LOG_ROOT,
            self.settings.full_gate_stamp == FULL_GATE_STAMP,
            self.settings.gate_max_age_seconds == GATE_MAX_AGE_SECONDS,
            self.settings.retail_prepared_root == RETAIL_PREPARED_ROOT,
            not self.settings.test_retail_roles,
            self.settings.test_retail_inventory is None,
            self.settings.candidates == INITIAL_CANDIDATES,
            reviewed,
            self.production_authorization_valid(),
            self.settings.review_authorized == reviewed,
            PRODUCTION_REVIEW_AUTHORIZED == reviewed,
            type(self.platform) is DarwinPlatform,
        )
        if not all(fixed):
            raise ArchiveError("production archive policy cannot be overridden")

    def hook(self, hook_name: str, **values: Any) -> None:
        function = self.hooks.get(hook_name)
        if function is not None:
            function(**values)

    def fsync(self, fd: int, label: str, volume: VolumeBinding | None = None) -> None:
        if volume is not None:
            self.require_volume(volume)
        self.hook("before_fsync", fd=fd, label=label)
        try:
            self.platform.fsync(fd)
        except OSError as error:
            raise ArchiveError(f"cannot fsync {label}: {error}") from error
        if volume is not None:
            self.require_volume(volume)

    def ensure_private_directory(self, parent_fd: int, name: str,
                                 volume: VolumeBinding | None = None) -> tuple[int, bool]:
        require_leaf(name, "private directory")
        created = False
        try:
            if volume is not None:
                self.require_volume(volume)
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            pass
        descriptor = os.open(name, directory_open_flags(), dir_fd=parent_fd)
        try:
            if created:
                if volume is not None:
                    self.require_volume(volume)
                os.fchmod(descriptor, 0o700)
            opened = os.fstat(descriptor)
            linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.geteuid()
                    or stat.S_IMODE(opened.st_mode) != 0o700
                    or identity(opened) != identity(linked)):
                raise ArchiveError(f"private directory is foreign or unsafe: {name}")
            if created:
                self.fsync(descriptor, f"created directory {name}", volume)
                self.fsync(parent_fd, f"parent of {name}", volume)
            return descriptor, created
        except BaseException:
            os.close(descriptor)
            raise

    def open_policy_directory(self, parent_fd: int, name: str,
                              volume: VolumeBinding) -> int:
        require_leaf(name, "fixed policy directory")
        self.require_volume(volume)
        descriptor = os.open(name, directory_open_flags(), dir_fd=parent_fd)
        try:
            opened = os.fstat(descriptor)
            linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.geteuid()
                    or stat.S_IMODE(opened.st_mode) not in {0o700, 0o755}
                    or identity(opened) != identity(linked)):
                raise ArchiveError(f"fixed policy directory is foreign or unsafe: {name}")
            self.require_volume(volume)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def create_private_directory_exclusive(self, parent_fd: int, name: str,
                                           volume: VolumeBinding | None = None) -> int:
        require_leaf(name, "exclusive directory")
        if volume is not None:
            self.require_volume(volume)
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        descriptor = os.open(name, directory_open_flags(), dir_fd=parent_fd)
        try:
            if volume is not None:
                self.require_volume(volume)
            os.fchmod(descriptor, 0o700)
            opened = os.fstat(descriptor)
            linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) != 0o700
                    or identity(opened) != identity(linked)):
                raise ArchiveError("new private directory could not be rebound")
            self.fsync(descriptor, f"new directory {name}", volume)
            self.fsync(parent_fd, f"new directory parent {name}", volume)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def validate_private_record_fd(self, parent_fd: int, name: str, descriptor: int) -> tuple[int, int]:
        opened = os.fstat(descriptor)
        linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_nlink != 1
                or identity(opened) != identity(linked)):
            raise ArchiveError(f"record residue is foreign or unsafe: {name}")
        return identity(opened)

    def quarantine_record_partial(self, parent_fd: int, name: str,
                                  volume: VolumeBinding | None = None) -> None:
        partial_record_canonical(name)
        descriptor = os.open(name, os.O_RDONLY | O_NOFOLLOW, dir_fd=parent_fd)
        try:
            expected = self.validate_private_record_fd(parent_fd, name, descriptor)
        finally:
            os.close(descriptor)
        residue_fd, _ = self.ensure_private_directory(parent_fd, RECORD_RESIDUE, volume)
        try:
            if volume is not None:
                self.require_volume(volume)
            self.platform.rename_exclusive(parent_fd, name, residue_fd, name)
            moved = os.stat(name, dir_fd=residue_fd, follow_symlinks=False)
            if identity(moved) != expected:
                raise ArchiveError("partial record identity changed during residue quarantine")
            self.fsync(residue_fd, "record residue namespace", volume)
            self.fsync(parent_fd, "record partial quarantine parent", volume)
        finally:
            os.close(residue_fd)

    def validate_record_residue(self, parent_fd: int,
                                allowed: Callable[[str], bool] | None = None) -> None:
        try:
            residue_fd = os.open(RECORD_RESIDUE, directory_open_flags(), dir_fd=parent_fd)
        except FileNotFoundError:
            return
        try:
            opened = os.fstat(residue_fd)
            linked = os.stat(RECORD_RESIDUE, dir_fd=parent_fd, follow_symlinks=False)
            if (opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) != 0o700
                    or identity(opened) != identity(linked)):
                raise ArchiveError("record residue namespace is foreign or unsafe")
            for partial in os.listdir(residue_fd):
                canonical = partial_record_canonical(partial)
                if allowed is not None and not allowed(canonical):
                    raise ArchiveError("record residue is outside its reviewed family")
                descriptor = os.open(
                    partial, os.O_RDONLY | O_NOFOLLOW, dir_fd=residue_fd)
                try:
                    self.validate_private_record_fd(residue_fd, partial, descriptor)
                finally:
                    os.close(descriptor)
        finally:
            os.close(residue_fd)

    def recover_record_partials(self, parent_fd: int,
                                volume: VolumeBinding | None = None,
                                allowed: Callable[[str], bool] | None = None) -> None:
        partials = sorted(name for name in os.listdir(parent_fd) if name.startswith(".partial-"))
        for name in partials:
            canonical = partial_record_canonical(name)
            if allowed is not None and not allowed(canonical):
                raise ArchiveError(f"partial record is outside its reviewed family: {name}")
            self.quarantine_record_partial(parent_fd, name, volume)
        self.validate_record_residue(parent_fd, allowed)

    def write_record(self, parent_fd: int, name: str, value: dict[str, Any],
                     volume: VolumeBinding | None = None) -> str:
        require_leaf(name, "record")
        if name in TRANSACTION_RECORD_NAMES:
            allowed = lambda canonical: canonical in TRANSACTION_RECORD_NAMES
        elif EXTERNAL_RECORD_RE.fullmatch(name):
            allowed = lambda canonical: EXTERNAL_RECORD_RE.fullmatch(canonical) is not None
        else:
            allowed = lambda canonical: canonical == name
        self.recover_record_partials(parent_fd, volume, allowed)
        contents = canonical_json(value)
        try:
            existing = self.read_record(parent_fd, name)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if canonical_json(existing) != contents:
                raise ArchiveError(f"immutable record differs: {name}")
            return sha256_bytes(contents)

        stage_slug = name[:-5] if name.endswith(".json") else name
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", stage_slug):
            raise ArchiveError("record name cannot produce a confined partial stage")
        temporary = f".partial-{stage_slug}-{uuid.uuid4().hex}"
        if volume is not None:
            self.require_volume(volume)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW,
                             0o600, dir_fd=parent_fd)
        temporary_identity = identity(os.fstat(descriptor))
        published = False
        try:
            if volume is not None:
                self.require_volume(volume)
            os.fchmod(descriptor, 0o600)
            def guarded_write(fd: int, pending: bytes) -> int:
                if volume is not None:
                    self.require_volume(volume)
                return self.platform.write(fd, pending)
            complete_write(descriptor, contents, guarded_write)
            self.fsync(descriptor, f"record payload {name}", volume)
            opened = os.fstat(descriptor)
            rebound = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
            if (opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) != 0o600
                    or opened.st_nlink != 1 or opened.st_size != len(contents)
                    or identity(opened) != identity(rebound)):
                raise ArchiveError(f"temporary record is not private and complete: {name}")
            self.hook("after_partial_record_fsync", name=name, temporary=temporary)
            os.close(descriptor)
            descriptor = -1
            if volume is not None:
                self.require_volume(volume)
            self.platform.rename_exclusive(parent_fd, temporary, parent_fd, name)
            published = True
            final = stable_rebind(parent_fd, name, temporary_identity, "regular")
            try:
                if sha256_fd(final) != sha256_bytes(contents):
                    raise ArchiveError(f"published record content changed: {name}")
                self.fsync(final, f"published record {name}", volume)
            finally:
                os.close(final)
            self.fsync(parent_fd, f"record directory {name}", volume)
            return sha256_bytes(contents)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if not published:
                try:
                    current = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
                    if identity(current) == temporary_identity:
                        self.quarantine_record_partial(parent_fd, temporary, volume)
                except FileNotFoundError:
                    pass

    def read_record(self, parent_fd: int, name: str) -> dict[str, Any]:
        descriptor = os.open(name, os.O_RDONLY | O_NOFOLLOW, dir_fd=parent_fd)
        try:
            opened = os.fstat(descriptor)
            linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                    or stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_nlink != 1
                    or identity(opened) != identity(linked)):
                raise ArchiveError(f"record is foreign or unsafe: {name}")
            chunks: list[bytes] = []
            while True:
                block = os.read(descriptor, 1024 * 1024)
                if not block:
                    break
                chunks.append(block)
            raw = b"".join(chunks)
            try:
                value = strict_json_loads(raw.decode("ascii"))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                raise ArchiveError(f"record is malformed: {name}") from error
            if not isinstance(value, dict) or canonical_json(value) != raw:
                raise ArchiveError(f"record is not canonical: {name}")
            return value
        finally:
            os.close(descriptor)

    def stage(self, transaction_fd: int, stage: str, payload: dict[str, Any]) -> str:
        if stage not in RECORD_NAMES:
            raise ArchiveError(f"unknown stage: {stage}")
        return self.write_record(transaction_fd, RECORD_NAMES[stage],
                                 {"schema": SCHEMA, "stage": stage, **payload})

    def load_stage(self, transaction_fd: int, stage: str) -> dict[str, Any] | None:
        try:
            value = self.read_record(transaction_fd, RECORD_NAMES[stage])
        except FileNotFoundError:
            return None
        if value.get("schema") != SCHEMA or value.get("stage") != stage:
            raise ArchiveError(f"stage record is invalid: {stage}")
        return value

    def validate_transaction_residue(self, transaction_fd: int) -> None:
        names = set(os.listdir(transaction_fd))
        allowed = set(TRANSACTION_RECORD_NAMES) | {RECORD_RESIDUE}
        if not names <= allowed:
            raise ArchiveError("transaction contains unknown/tampered residue")
        if RECORD_RESIDUE in names:
            self.validate_record_residue(
                transaction_fd, allowed=lambda name: name in TRANSACTION_RECORD_NAMES)
        seen_gap = False
        for stage in RECORD_STAGES:
            present = RECORD_NAMES[stage] in names
            if not present:
                seen_gap = True
            elif seen_gap:
                raise ArchiveError("transaction stages are not a durable prefix")
        if RETIREMENT_OVERLAY_BASELINE_RECORD in names:
            required = {
                RECORD_NAMES["source-quarantined"],
                RECORD_NAMES["retirement-started"],
            }
            if not required <= names:
                raise ArchiveError(
                    "retirement overlay baseline lacks durable 008/009")
        if RETIREMENT_PRETRUNCATE_OPEN_RECORD in names:
            required = {
                RECORD_NAMES["source-quarantined"],
                RECORD_NAMES["retirement-started"],
                RETIREMENT_OVERLAY_BASELINE_RECORD,
            }
            if not required <= names:
                raise ArchiveError(
                    "retirement pretruncate open lacks durable 008/009/009a")

    def queue_fd(self, *, create: bool) -> int:
        parent_fd = open_absolute_directory(self.settings.queue_root.parent, "queue parent")
        try:
            if create:
                descriptor, _ = self.ensure_private_directory(parent_fd, self.settings.queue_root.name)
                return descriptor
            return os.open(self.settings.queue_root.name, directory_open_flags(), dir_fd=parent_fd)
        finally:
            os.close(parent_fd)

    def validate_archiver_lock_fd(self, descriptor: int) -> tuple[int, int]:
        """Bind a lock FD to the fixed queue parent without changing it."""
        opened = os.fstat(descriptor)
        linked = os.stat(self.settings.queue_root.parent, follow_symlinks=False)
        if (not stat.S_ISDIR(opened.st_mode)
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) not in {0o700, 0o755}
                or identity(opened) != identity(linked)):
            raise ArchiveError("archiver lock root is foreign or unsafe")
        return identity(opened)

    def acquire_archiver_lock(self, *, exclusive: bool) -> int:
        """Acquire the process-wide cooperating-tool boundary, nonblocking."""
        descriptor = open_absolute_directory(
            self.settings.queue_root.parent, "archiver lock root")
        try:
            self.validate_archiver_lock_fd(descriptor)
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
            self.validate_archiver_lock_fd(descriptor)
        except BlockingIOError as error:
            os.close(descriptor)
            mode = "exclusive" if exclusive else "shared"
            raise ArchiveBusy(
                f"archiver BUSY: cooperating worker blocks {mode} access") from error
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def spec(self, candidate_id: str) -> CandidateSpec:
        if self.settings.production and not self.production_authorization_valid():
            raise ArchiveError("production allowlist authorization digest is stale")
        candidate = next((item for item in self.settings.candidates if item.ident == candidate_id), None)
        if candidate is None:
            raise ArchiveError("candidate is outside the reviewed allowlist")
        self.validate_candidate(candidate)
        return candidate

    def retail_role(self, candidate: CandidateSpec) -> str | None:
        if self.settings.production:
            if candidate is RETAIL_BACKUP_CANDIDATE \
                    and candidate.ident == "backup-device-retail-20260808-185559" \
                    and candidate.source == RETAIL_BACKUP_PATH:
                return "main"
            if candidate is RETAIL_BACKUP_MANIFEST_CANDIDATE \
                    and candidate.ident == "backup-device-retail-20260808-185559-manifest" \
                    and candidate.source == RETAIL_BACKUP_MANIFEST_PATH:
                return "sibling"
            return None
        roles = dict(self.settings.test_retail_roles)
        if len(roles) != len(self.settings.test_retail_roles) \
                or set(roles.values()) - {"main", "sibling"}:
            raise ArchiveError("test retail role binding is invalid")
        return roles.get(candidate.ident)

    def provenance_overlay_policy_for_record(
            self, record: dict[str, Any]) -> ProvenanceOverlayPolicy | None:
        """Return the overlay only for the immutable production main candidate.

        Non-production settings, test retail-role injection and arbitrary
        CandidateSpec instances can never activate this branch.
        """
        if not self.settings.production:
            return None
        candidate = self.spec(record.get("candidate_id"))
        if (candidate is RETAIL_BACKUP_CANDIDATE
                and record.get("candidate_id") == PROVENANCE_OVERLAY_CANDIDATE_ID
                and record.get("source") == str(RETAIL_BACKUP_PATH)
                and record.get("category") == RETAIL_BACKUP_CANDIDATE.category
                and record.get("data_class") == RETAIL_BACKUP_CANDIDATE.data_class
                and record.get("deletion_rule") == RETAIL_BACKUP_CANDIDATE.deletion_rule
                and record.get("allowlist_version") == ALLOWLIST_VERSION):
            return PRODUCTION_PROVENANCE_OVERLAY
        return None

    def source_root_overlay_policy_for_record(
            self, record: dict[str, Any]) -> SourceRootOverlayPolicy | None:
        if not self.settings.production:
            return None
        policy = PRODUCTION_SOURCE_ROOT_OVERLAY
        if (record.get("transaction_id") == policy.transaction_id
                and record.get("candidate_id") == policy.candidate_id
                and record.get("source") == policy.source_path
                and tuple(record.get("source_identity", ())) == policy.source_identity
                and tuple(record.get("source_parent_identity", ()))
                == policy.source_parent_identity
                and record.get("allowlist_version") == policy.allowlist_version
                and record.get("source_tree_sha256") == policy.semantic_tree_sha256
                and record.get("category") == RETAIL_BACKUP_CANDIDATE.category
                and record.get("data_class") == RETAIL_BACKUP_CANDIDATE.data_class
                and record.get("deletion_rule") == RETAIL_BACKUP_CANDIDATE.deletion_rule):
            return policy
        return None

    def retirement_overlay_baseline_policy_for_transaction(
            self, transaction_fd: int,
            record: dict[str, Any]) -> RetirementOverlayBaselinePolicy | None:
        """Recognize only the one reviewed post-009 production transaction."""
        if (not self.settings.production
                or self.source_root_overlay_policy_for_record(record) is None
                or not self.production_authorization_valid()):
            return None
        policy = PRODUCTION_RETIREMENT_OVERLAY_BASELINE
        if (record.get("transaction_id") != policy.transaction_id
                or record.get("candidate_id") != policy.candidate_id
                or record.get("source") != policy.source_path
                or tuple(record.get("source_identity", ())) != policy.source_identity
                or tuple(record.get("source_parent_identity", ()))
                != policy.source_parent_identity
                or record.get("allowlist_version") != policy.allowlist_version):
            return None
        quarantined = self.load_stage(transaction_fd, "source-quarantined")
        retirement = self.load_stage(transaction_fd, "retirement-started")
        if quarantined is None or retirement is None:
            return None
        if (sha256_bytes(canonical_json(quarantined))
                != policy.source_quarantined_sha256
                or sha256_bytes(canonical_json(retirement))
                != policy.retirement_started_sha256):
            return None
        return policy

    def retirement_pretruncate_open_policy_for_transaction(
            self, transaction_fd: int,
            record: dict[str, Any]) -> RetirementPretruncateOpenPolicy | None:
        """Recognize only the reviewed real 008/009/009a tuple."""
        if (not self.settings.production
                or self.source_root_overlay_policy_for_record(record) is None
                or not self.production_authorization_valid()):
            return None
        policy = PRODUCTION_RETIREMENT_PRETRUNCATE_OPEN
        if (record.get("transaction_id") != policy.transaction_id
                or record.get("candidate_id") != policy.candidate_id
                or record.get("source") != policy.source_path
                or tuple(record.get("source_identity", ()))
                != policy.source_identity
                or tuple(record.get("source_parent_identity", ()))
                != policy.source_parent_identity
                or record.get("allowlist_version") != policy.allowlist_version):
            return None
        quarantined = self.load_stage(transaction_fd, "source-quarantined")
        retirement = self.load_stage(transaction_fd, "retirement-started")
        baseline = self.load_retirement_overlay_baseline(transaction_fd)
        if quarantined is None or retirement is None or baseline is None:
            return None
        if (sha256_bytes(canonical_json(quarantined))
                != policy.source_quarantined_sha256
                or sha256_bytes(canonical_json(retirement))
                != policy.retirement_started_sha256
                or sha256_bytes(canonical_json(baseline))
                != policy.retirement_overlay_baseline_sha256):
            return None
        return policy

    def load_retirement_overlay_baseline(
            self, transaction_fd: int) -> dict[str, Any] | None:
        try:
            value = self.read_record(
                transaction_fd, RETIREMENT_OVERLAY_BASELINE_RECORD)
        except FileNotFoundError:
            return None
        if (value.get("schema") != RETIREMENT_OVERLAY_BASELINE_SCHEMA
                or value.get("stage") != "retirement-overlay-baseline"):
            raise ArchiveError("retirement overlay baseline record is invalid")
        return value

    def load_retirement_pretruncate_open(
            self, transaction_fd: int) -> dict[str, Any] | None:
        try:
            value = self.read_record(
                transaction_fd, RETIREMENT_PRETRUNCATE_OPEN_RECORD)
        except FileNotFoundError:
            return None
        if (value.get("schema") != RETIREMENT_PRETRUNCATE_OPEN_SCHEMA
                or value.get("stage")
                != "retirement-pretruncate-open-overlay"):
            raise ArchiveError("retirement pretruncate open record is invalid")
        return value

    def retirement_overlay_baseline_payload(
            self, transaction_fd: int, record: dict[str, Any],
            current_manifest: dict[str, Any], gate_proof: dict[str, Any],
            policy: RetirementOverlayBaselinePolicy, *,
            require_gate_fresh: bool = True) -> dict[str, Any]:
        quarantined = self.load_stage(transaction_fd, "source-quarantined")
        retirement = self.load_stage(transaction_fd, "retirement-started")
        if quarantined is None or retirement is None:
            raise ArchiveError("retirement overlay baseline lacks 008/009")
        sha_008 = sha256_bytes(canonical_json(quarantined))
        sha_009 = sha256_bytes(canonical_json(retirement))
        if (sha_008 != policy.source_quarantined_sha256
                or sha_009 != policy.retirement_started_sha256):
            raise ArchiveError("retirement overlay 008/009 tuple differs")
        immutable = quarantined.get("source_physical_manifest")
        if not isinstance(immutable, dict):
            raise ArchiveError("retirement overlay 008 lacks immutable manifest")
        proof = compare_retirement_overlay_baseline(
            immutable, current_manifest, policy=policy,
            production_authorization_sha256=
            REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
        immutable_gate = quarantined.get("gate_proof")
        if (not isinstance(immutable_gate, dict)
                or quarantined.get("gate_proof_sha256")
                != sha256_bytes(canonical_json(immutable_gate))):
            raise ArchiveError("retirement overlay 008 gate proof is invalid")
        self.validate_reviewed_historical_gate_proof(immutable_gate)
        self.validate_full_gate_proof(
            gate_proof, require_fresh=require_gate_fresh)
        return {
            "schema": policy.schema,
            "stage": "retirement-overlay-baseline",
            "transaction_id": record["transaction_id"],
            "candidate_id": record["candidate_id"],
            "source": record["source"],
            "source_identity": record["source_identity"],
            "source_parent_identity": record["source_parent_identity"],
            "allowlist_version": record["allowlist_version"],
            "source_quarantined_stage_sha256": sha_008,
            "retirement_started_stage_sha256": sha_009,
            "immutable_source_manifest": immutable,
            "immutable_source_manifest_sha256":
                manifest_canonical_sha256(immutable),
            "immutable_source_tree_sha256": immutable["tree_sha256"],
            "current_source_manifest": current_manifest,
            "current_source_manifest_sha256":
                manifest_canonical_sha256(current_manifest),
            "current_source_tree_sha256": current_manifest["tree_sha256"],
            "baseline_proof": proof,
            "baseline_proof_sha256": sha256_bytes(canonical_json(proof)),
            "immutable_008_gate_proof": immutable_gate,
            "immutable_008_gate_proof_sha256":
                sha256_bytes(canonical_json(immutable_gate)),
            "current_009a_gate_proof": gate_proof,
            "current_009a_gate_proof_sha256":
                sha256_bytes(canonical_json(gate_proof)),
            "production_authorization_sha256":
                REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256,
        }

    def validate_retirement_overlay_baseline_record(
            self, transaction_fd: int, record: dict[str, Any],
            baseline: dict[str, Any], *, require_current_gate: bool) \
            -> RetirementOverlayBaselinePolicy:
        policy = self.retirement_overlay_baseline_policy_for_transaction(
            transaction_fd, record)
        if policy is None:
            raise ArchiveError("retirement overlay baseline policy is unavailable")
        open_policy = self.retirement_pretruncate_open_policy_for_transaction(
            transaction_fd, record)
        if open_policy is not None:
            self.validate_historical_retirement_overlay_baseline_record(
                transaction_fd, record, baseline, policy=open_policy)
            if require_current_gate:
                current_gate = self.matching_full_gate_receipt()
                self.validate_full_gate_proof(
                    current_gate, require_fresh=True)
                open_record = self.load_retirement_pretruncate_open(
                    transaction_fd)
                if open_record is not None:
                    stage_c = open_record.get("current_009b_gate_proof")
                    if not isinstance(stage_c, dict):
                        raise ArchiveError(
                            "retirement pretruncate open lacks gate C")
                    self.require_equivalent_gate_proofs(
                        stage_c, current_gate)
            return policy
        current = baseline.get("current_source_manifest")
        if not isinstance(current, dict):
            raise ArchiveError("retirement overlay baseline lacks current manifest")
        immutable_gate = baseline.get("immutable_008_gate_proof")
        if (not isinstance(immutable_gate, dict)
                or baseline.get("immutable_008_gate_proof_sha256")
                != sha256_bytes(canonical_json(immutable_gate))):
            raise ArchiveError(
                "retirement overlay historical gate binding differs")
        self.validate_reviewed_historical_gate_proof(immutable_gate)
        stage_gate = baseline.get("current_009a_gate_proof")
        if (not isinstance(stage_gate, dict)
                or baseline.get("current_009a_gate_proof_sha256")
                != sha256_bytes(canonical_json(stage_gate))):
            raise ArchiveError("retirement overlay baseline gate binding differs")
        self.validate_full_gate_proof(stage_gate, require_fresh=False)
        expected = self.retirement_overlay_baseline_payload(
            transaction_fd, record, current, stage_gate, policy,
            require_gate_fresh=False)
        if canonical_json(expected) != canonical_json(baseline):
            raise ArchiveError("retirement overlay baseline immutable record differs")
        if require_current_gate:
            current_gate = self.matching_full_gate_receipt()
            self.validate_full_gate_proof(current_gate, require_fresh=True)
            self.require_equivalent_gate_proofs(stage_gate, current_gate)
        return policy

    def validate_historical_retirement_overlay_baseline_record(
            self, transaction_fd: int, record: dict[str, Any],
            baseline: dict[str, Any], *,
            policy: RetirementPretruncateOpenPolicy) -> None:
        """Validate immutable historical 009a without rewriting its auth."""
        if (sha256_bytes(canonical_json(baseline))
                != policy.retirement_overlay_baseline_sha256
                or baseline.get("transaction_id") != policy.transaction_id
                or baseline.get("candidate_id") != policy.candidate_id
                or baseline.get("source") != policy.source_path
                or tuple(baseline.get("source_identity", ()))
                != policy.source_identity
                or tuple(baseline.get("source_parent_identity", ()))
                != policy.source_parent_identity
                or baseline.get("allowlist_version") != policy.allowlist_version
                or baseline.get("source_quarantined_stage_sha256")
                != policy.source_quarantined_sha256
                or baseline.get("retirement_started_stage_sha256")
                != policy.retirement_started_sha256
                or baseline.get("production_authorization_sha256")
                != policy.historical_009a_authorization_sha256):
            raise ArchiveError("historical retirement overlay 009a tuple differs")
        quarantined = self.load_stage(transaction_fd, "source-quarantined")
        if quarantined is None:
            raise ArchiveError("historical retirement overlay lacks 008")
        immutable = quarantined.get("source_physical_manifest")
        current = baseline.get("current_source_manifest")
        if (not isinstance(immutable, dict) or not isinstance(current, dict)
                or manifest_canonical_sha256(current)
                != policy.baseline_manifest_sha256
                or current.get("tree_sha256") != policy.baseline_tree_sha256):
            raise ArchiveError("historical retirement overlay manifests differ")
        proof = compare_retirement_overlay_baseline(
            immutable, current,
            policy=(self.retirement_overlay_baseline_policy_for_transaction(
                transaction_fd, record)
                or PRODUCTION_RETIREMENT_OVERLAY_BASELINE),
            production_authorization_sha256=
            policy.historical_009a_authorization_sha256)
        if (baseline.get("baseline_proof_sha256")
                != sha256_bytes(canonical_json(proof))
                or canonical_json(baseline.get("baseline_proof"))
                != canonical_json(proof)):
            raise ArchiveError("historical retirement overlay proof differs")
        immutable_gate = baseline.get("immutable_008_gate_proof")
        stage_b = baseline.get("current_009a_gate_proof")
        if (not isinstance(immutable_gate, dict)
                or baseline.get("immutable_008_gate_proof_sha256")
                != sha256_bytes(canonical_json(immutable_gate))
                or not isinstance(stage_b, dict)
                or baseline.get("current_009a_gate_proof_sha256")
                != sha256_bytes(canonical_json(stage_b))):
            raise ArchiveError("historical retirement overlay gate binding differs")
        self.validate_reviewed_historical_gate_proof(immutable_gate)
        self.validate_reviewed_historical_gate_proof(stage_b)
        if (stage_b.get("gate_source_sha256") != policy.gate_b_source_sha256
                or stage_b.get("gate_stamp_sha256")
                != policy.gate_b_stamp_sha256):
            raise ArchiveError("historical retirement overlay gate B differs")

    def ensure_retirement_overlay_baseline(
            self, transaction_fd: int, record: dict[str, Any],
            intent: dict[str, Any], queue_fd: int) -> dict[str, Any] | None:
        policy = self.retirement_overlay_baseline_policy_for_transaction(
            transaction_fd, record)
        if policy is None:
            return None
        existing = self.load_retirement_overlay_baseline(transaction_fd)
        if existing is not None:
            self.validate_retirement_overlay_baseline_record(
                transaction_fd, record, existing, require_current_gate=True)
            return existing
        quarantine_fd = self.quarantine_namespace(queue_fd, intent)
        descriptor = -1
        try:
            owned_name = self.owned_quarantine_name(
                quarantine_fd, intent, tuple(record["source_identity"]))
            if owned_name is None:
                raise ArchiveError("retirement overlay source is absent")
            descriptor = stable_rebind(
                quarantine_fd, owned_name, tuple(record["source_identity"]),
                record["source_kind"])
            current = manifest_bound(descriptor, record["source_kind"])
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(quarantine_fd)
        gate = self.matching_full_gate_receipt()
        payload = self.retirement_overlay_baseline_payload(
            transaction_fd, record, current, gate, policy)
        self.hook("before_retirement_overlay_baseline_record")
        self.write_record(
            transaction_fd, policy.record_name, payload)
        self.hook("after_retirement_overlay_baseline_record")
        durable = self.load_retirement_overlay_baseline(transaction_fd)
        if durable is None:
            raise ArchiveError("retirement overlay baseline was not durable")
        self.validate_retirement_overlay_baseline_record(
            transaction_fd, record, durable, require_current_gate=True)
        return durable

    def retirement_pretruncate_open_payload(
            self, transaction_fd: int, record: dict[str, Any],
            post_open_manifest: dict[str, Any], gate_proof: dict[str, Any],
            policy: RetirementPretruncateOpenPolicy, *,
            require_gate_fresh: bool = True) -> dict[str, Any]:
        quarantined = self.load_stage(transaction_fd, "source-quarantined")
        retirement = self.load_stage(transaction_fd, "retirement-started")
        baseline = self.load_retirement_overlay_baseline(transaction_fd)
        if quarantined is None or retirement is None or baseline is None:
            raise ArchiveError("retirement pretruncate open lacks 008/009/009a")
        if (sha256_bytes(canonical_json(quarantined))
                != policy.source_quarantined_sha256
                or sha256_bytes(canonical_json(retirement))
                != policy.retirement_started_sha256
                or sha256_bytes(canonical_json(baseline))
                != policy.retirement_overlay_baseline_sha256):
            raise ArchiveError("retirement pretruncate open predecessor differs")
        self.validate_historical_retirement_overlay_baseline_record(
            transaction_fd, record, baseline, policy=policy)
        self.validate_full_gate_proof(
            gate_proof, require_fresh=require_gate_fresh)
        baseline_manifest = baseline.get("current_source_manifest")
        if not isinstance(baseline_manifest, dict):
            raise ArchiveError("retirement pretruncate open lacks baseline manifest")
        base_manifest = build_retirement_pretruncate_open_base_manifest(
            baseline_manifest, policy=policy)
        baseline_proof = retirement_pretruncate_open_baseline_proof(
            baseline_manifest, base_manifest, policy=policy,
            production_authorization_sha256=
            REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
        open_proof = compare_retirement_pretruncate_open_overlay(
            base_manifest, post_open_manifest, policy=policy,
            production_authorization_sha256=
            REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
        stage_b = baseline["current_009a_gate_proof"]
        return {
            "schema": policy.schema,
            "stage": "retirement-pretruncate-open-overlay",
            "transaction_id": record["transaction_id"],
            "candidate_id": record["candidate_id"],
            "source": record["source"],
            "source_identity": record["source_identity"],
            "source_parent_identity": record["source_parent_identity"],
            "allowlist_version": record["allowlist_version"],
            "source_quarantined_stage_sha256":
                policy.source_quarantined_sha256,
            "retirement_started_stage_sha256":
                policy.retirement_started_sha256,
            "retirement_overlay_baseline_stage_sha256":
                policy.retirement_overlay_baseline_sha256,
            "historical_009a_authorization_sha256":
                policy.historical_009a_authorization_sha256,
            "historical_009a_gate_proof": stage_b,
            "historical_009a_gate_proof_sha256":
                sha256_bytes(canonical_json(stage_b)),
            "current_009b_gate_proof": gate_proof,
            "current_009b_gate_proof_sha256":
                sha256_bytes(canonical_json(gate_proof)),
            "pre_open_base_manifest_sha256":
                policy.pre_open_manifest_sha256,
            "pre_open_base_tree_sha256": policy.pre_open_tree_sha256,
            "post_open_manifest": post_open_manifest,
            "post_open_manifest_sha256":
                manifest_canonical_sha256(post_open_manifest),
            "post_open_tree_sha256": post_open_manifest["tree_sha256"],
            "baseline_proof": baseline_proof,
            "baseline_proof_sha256":
                sha256_bytes(canonical_json(baseline_proof)),
            "open_overlay_proof": open_proof,
            "open_overlay_proof_sha256":
                sha256_bytes(canonical_json(open_proof)),
            "observed_paths": open_proof["observed_paths"],
            "observed_count": open_proof["observed_count"],
            "observed_paths_sha256": open_proof["observed_paths_sha256"],
            "production_authorization_sha256":
                REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256,
        }

    def validate_retirement_pretruncate_open_record(
            self, transaction_fd: int, record: dict[str, Any],
            open_record: dict[str, Any], *, require_current_gate: bool,
            current_manifest: dict[str, Any] | None = None) \
            -> RetirementPretruncateOpenPolicy:
        policy = self.retirement_pretruncate_open_policy_for_transaction(
            transaction_fd, record)
        if policy is None:
            raise ArchiveError(
                "retirement pretruncate open policy is unavailable")
        stored_manifest = open_record.get("post_open_manifest")
        stage_c = open_record.get("current_009b_gate_proof")
        if (not isinstance(stored_manifest, dict)
                or not isinstance(stage_c, dict)
                or open_record.get("post_open_manifest_sha256")
                != manifest_canonical_sha256(stored_manifest)
                or open_record.get("post_open_tree_sha256")
                != stored_manifest.get("tree_sha256")
                or open_record.get("current_009b_gate_proof_sha256")
                != sha256_bytes(canonical_json(stage_c))):
            raise ArchiveError(
                "retirement pretruncate open immutable binding differs")
        self.validate_full_gate_proof(stage_c, require_fresh=False)
        expected = self.retirement_pretruncate_open_payload(
            transaction_fd, record, stored_manifest, stage_c, policy,
            require_gate_fresh=False)
        if canonical_json(expected) != canonical_json(open_record):
            raise ArchiveError(
                "retirement pretruncate open immutable record differs")
        if current_manifest is not None:
            baseline = self.load_retirement_overlay_baseline(transaction_fd)
            if baseline is None:
                raise ArchiveError(
                    "retirement pretruncate open lost historical 009a")
            base = build_retirement_pretruncate_open_base_manifest(
                baseline["current_source_manifest"], policy=policy)
            self.validate_retirement_overlay_partial_manifest(
                base, current_manifest, policy, permit_partial_mtime=True)
            base_rows = manifest_rows_exact(base, "pretruncate open base")
            current_rows = manifest_rows_exact(
                current_manifest, "pretruncate open current")
            observed = sorted(
                path for path, row in current_rows.items()
                if row.get("kind") == "regular"
                and policy.runtime_xattr_name
                not in base_rows[path].get("xattrs", {})
                and row.get("xattrs", {}).get(policy.runtime_xattr_name)
                == policy.runtime_xattr_value)
            if not set(open_record["observed_paths"]) <= set(
                    observed):
                raise ArchiveError(
                    "retirement pretruncate open provenance regressed")
        if require_current_gate:
            current_gate = self.matching_full_gate_receipt()
            self.validate_full_gate_proof(current_gate, require_fresh=True)
            self.require_equivalent_gate_proofs(stage_c, current_gate)
        return policy

    def ensure_retirement_pretruncate_open(
            self, transaction_fd: int, record: dict[str, Any],
            post_open_manifest: dict[str, Any]) -> dict[str, Any] | None:
        policy = self.retirement_pretruncate_open_policy_for_transaction(
            transaction_fd, record)
        if policy is None:
            return None
        existing = self.load_retirement_pretruncate_open(transaction_fd)
        if existing is not None:
            self.validate_retirement_pretruncate_open_record(
                transaction_fd, record, existing,
                require_current_gate=True,
                current_manifest=post_open_manifest)
            return existing
        gate = self.matching_full_gate_receipt()
        payload = self.retirement_pretruncate_open_payload(
            transaction_fd, record, post_open_manifest, gate, policy)
        self.hook("before_retirement_pretruncate_open_record")
        self.write_record(transaction_fd, policy.record_name, payload)
        self.hook("after_retirement_pretruncate_open_record")
        durable = self.load_retirement_pretruncate_open(transaction_fd)
        if durable is None:
            raise ArchiveError(
                "retirement pretruncate open record was not durable")
        self.validate_retirement_pretruncate_open_record(
            transaction_fd, record, durable, require_current_gate=True,
            current_manifest=post_open_manifest)
        return durable

    def validate_retirement_pretruncate_source_state(
            self, transaction_fd: int, record: dict[str, Any],
            current_manifest: dict[str, Any], *,
            require_current_gate: bool) -> bool:
        """Validate the exact pre-009b or proof-bound post-009b source state."""
        policy = self.retirement_pretruncate_open_policy_for_transaction(
            transaction_fd, record)
        if policy is None:
            return False
        baseline = self.load_retirement_overlay_baseline(transaction_fd)
        if baseline is None:
            raise ArchiveError("retirement pretruncate source lacks 009a")
        base = build_retirement_pretruncate_open_base_manifest(
            baseline["current_source_manifest"], policy=policy)
        open_record = self.load_retirement_pretruncate_open(transaction_fd)
        if open_record is None:
            # Before the sidecar no source byte may have been retired.  A
            # crash during O_RDWR binding may only leave an exact monotonic
            # provenance subset.
            compare_retirement_pretruncate_open_overlay(
                base, current_manifest, policy=policy,
                production_authorization_sha256=
                REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
        else:
            self.validate_retirement_pretruncate_open_record(
                transaction_fd, record, open_record,
                require_current_gate=require_current_gate,
                current_manifest=current_manifest)
        return True

    def retirement_overlay_downstream_binding(
            self, transaction_fd: int, record: dict[str, Any],
            runtime_proof: dict[str, Any] | None = None) -> dict[str, Any]:
        baseline = self.load_retirement_overlay_baseline(transaction_fd)
        if baseline is None:
            return {}
        self.validate_retirement_overlay_baseline_record(
            transaction_fd, record, baseline, require_current_gate=True)
        binding: dict[str, Any] = {
            "retirement_overlay_baseline_stage_sha256":
                sha256_bytes(canonical_json(baseline)),
            "retirement_overlay_baseline_proof": baseline["baseline_proof"],
            "retirement_overlay_baseline_proof_sha256":
                baseline["baseline_proof_sha256"],
            "retirement_overlay_policy_version":
                baseline["baseline_proof"]["policy_version"],
        }
        open_record = self.load_retirement_pretruncate_open(transaction_fd)
        if open_record is not None:
            self.validate_retirement_pretruncate_open_record(
                transaction_fd, record, open_record,
                require_current_gate=True)
            binding.update({
                "retirement_pretruncate_open_stage_sha256":
                    sha256_bytes(canonical_json(open_record)),
                "retirement_pretruncate_open_baseline_proof":
                    open_record["baseline_proof"],
                "retirement_pretruncate_open_baseline_proof_sha256":
                    open_record["baseline_proof_sha256"],
                "retirement_pretruncate_open_overlay_proof":
                    open_record["open_overlay_proof"],
                "retirement_pretruncate_open_overlay_proof_sha256":
                    open_record["open_overlay_proof_sha256"],
                "retirement_pretruncate_open_observed_count":
                    open_record["observed_count"],
                "retirement_pretruncate_open_observed_paths_sha256":
                    open_record["observed_paths_sha256"],
                "retirement_pretruncate_open_policy_version":
                    open_record["open_overlay_proof"]["policy_version"],
            })
        if runtime_proof is not None:
            if runtime_proof.get("proof_hash") \
                    != retirement_overlay_proof_hash(runtime_proof):
                raise ArchiveError("runtime retirement overlay proof is invalid")
            binding.update({
                "retirement_runtime_overlay_proof": runtime_proof,
                "retirement_runtime_overlay_proof_sha256":
                    sha256_bytes(canonical_json(runtime_proof)),
                "retirement_runtime_observed_count":
                    runtime_proof["observed_count"],
                "retirement_runtime_observed_paths_sha256":
                    runtime_proof["observed_paths_sha256"],
            })
        return binding

    def validate_retirement_overlay_downstream_binding(
            self, transaction_fd: int, record: dict[str, Any],
            payload: dict[str, Any], tombstone_manifest: dict[str, Any]) \
            -> dict[str, Any]:
        baseline = self.load_retirement_overlay_baseline(transaction_fd)
        policy = self.retirement_overlay_baseline_policy_for_transaction(
            transaction_fd, record)
        if baseline is None or policy is None:
            raise ArchiveError("retirement overlay downstream policy is absent")
        self.validate_retirement_overlay_baseline_record(
            transaction_fd, record, baseline, require_current_gate=True)
        proof = compare_retirement_runtime_overlay(
            baseline["current_source_manifest"], tombstone_manifest,
            policy=policy, baseline_proof=baseline["baseline_proof"])
        expected = self.retirement_overlay_downstream_binding(
            transaction_fd, record, proof)
        for key, value in expected.items():
            if canonical_json(payload.get(key)) != canonical_json(value):
                raise ArchiveError(
                    f"retirement overlay downstream binding differs: {key}")
        return proof

    def validate_source_root_overlay_manifest(
            self, record: dict[str, Any], physical_manifest: dict[str, Any],
            stored_proof: Any = None, *,
            authorization_sha256: str = REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256
            ) -> dict[str, Any] | None:
        policy = self.source_root_overlay_policy_for_record(record)
        if policy is None:
            if stored_proof is not None:
                raise ArchiveError("source-root overlay is forbidden for this transaction")
            if not manifests_equal(record["source_manifest"], physical_manifest):
                raise ArchiveError("source content changed since enqueue")
            return None
        return reconstruct_source_root_overlay_proof(
            record["source_manifest"], physical_manifest, policy=policy,
            authorization_sha256=authorization_sha256,
            stored_proof=stored_proof)

    def historical_source_root_overlay_authorization(
            self, transaction_fd: int, record: dict[str, Any],
            binding: dict[str, Any], prefix: dict[str, Any]) -> str | None:
        """Select historical ``d0`` only for the fixed real 008/009.

        Production initialization already enforces these fixed settings.  They
        are repeated here so a subsequently mutated Settings object, a test
        platform, CLI input or CandidateSpec cannot activate the exception.
        """
        fixed_environment = (
            self.settings.production,
            type(self.platform) is DarwinPlatform,
            self.settings.queue_root == QUEUE_ROOT,
            self.settings.mount == VOLUME_MOUNT,
            self.settings.volume_name == VOLUME_NAME,
            self.settings.volume_uuid == VOLUME_UUID,
            self.settings.repo_root == REPO_ROOT,
            self.settings.gate_log_root == GATE_LOG_ROOT,
            self.settings.full_gate_stamp == FULL_GATE_STAMP,
            self.settings.gate_max_age_seconds == GATE_MAX_AGE_SECONDS,
            self.settings.retail_prepared_root == RETAIL_PREPARED_ROOT,
            not self.settings.test_retail_roles,
            self.settings.test_retail_inventory is None,
            self.settings.candidates == INITIAL_CANDIDATES,
            self.production_authorization_valid(),
        )
        if not all(fixed_environment):
            return None
        reviewed = self.retirement_overlay_baseline_policy_for_transaction(
            transaction_fd, record)
        if reviewed is None:
            return None
        quarantined = self.load_stage(transaction_fd, "source-quarantined")
        retirement = self.load_stage(transaction_fd, "retirement-started")
        if quarantined is None or retirement is None:
            return None
        if canonical_json(quarantined) != canonical_json(binding):
            raise ArchiveError("historical source-root binding is not exact 008")
        return source_root_overlay_historical_authorization_for_tuple(
            record, PRODUCTION_SOURCE_ROOT_OVERLAY,
            source_quarantined_sha256=sha256_bytes(canonical_json(quarantined)),
            retirement_started_sha256=sha256_bytes(canonical_json(retirement)),
            legacy_prefix_sha256=prefix.get("aggregate_sha256", ""))

    def validate_source_root_legacy_prefix(
            self, transaction_fd: int, record: dict[str, Any]) -> dict[str, Any]:
        policy = self.source_root_overlay_policy_for_record(record)
        if policy is None:
            raise ArchiveError("legacy source-root prefix is forbidden")
        rows: list[dict[str, str]] = []
        for name, expected_sha256 in SOURCE_ROOT_OVERLAY_LEGACY_PREFIX:
            value = self.read_record(transaction_fd, name)
            digest = sha256_bytes(canonical_json(value))
            if not hmac.compare_digest(digest, expected_sha256):
                raise ArchiveError(f"legacy source-root record changed: {name}")
            rows.append({"name": name, "sha256": digest})
        aggregate = sha256_bytes(canonical_json(rows))
        if not hmac.compare_digest(aggregate, SOURCE_ROOT_OVERLAY_LEGACY_PREFIX_SHA256):
            raise ArchiveError("legacy source-root prefix aggregate changed")
        legacy_005 = self.read_record(transaction_fd, RECORD_NAMES["manifest-published"])
        if canonical_json(legacy_005) != canonical_json(SOURCE_ROOT_OVERLAY_LEGACY_005):
            raise ArchiveError("legacy 005 contents changed")
        legacy_006 = self.read_record(transaction_fd, RECORD_NAMES["second-copy-proof"])
        if canonical_json(legacy_006) != SOURCE_ROOT_OVERLAY_LEGACY_006_CANONICAL:
            raise ArchiveError("legacy 006 bytes changed")
        return {"records": rows, "aggregate_sha256": aggregate}

    def source_root_overlay_binding(
            self, transaction_fd: int, record: dict[str, Any],
            physical_manifest: dict[str, Any], gate_proof: dict[str, Any], *,
            validated_source_root_proof: dict[str, Any] | None = None
            ) -> dict[str, Any]:
        prefix = self.validate_source_root_legacy_prefix(transaction_fd, record)
        proof = validated_source_root_proof
        if proof is None:
            proof = self.validate_source_root_overlay_manifest(
                record, physical_manifest)
        elif proof.get("proof_hash") != source_root_overlay_proof_hash(proof):
            raise ArchiveError("validated source-root overlay proof hash differs")
        if proof is None:
            raise ArchiveError("source-root overlay binding is unavailable")
        return {
            "transaction_id": record["transaction_id"],
            "candidate_id": record["candidate_id"],
            "allowlist_version": record["allowlist_version"],
            "source": record["source"],
            "source_identity": record["source_identity"],
            "source_parent_identity": record["source_parent_identity"],
            "source_semantic_manifest": record["source_manifest"],
            "source_semantic_manifest_sha256":
                manifest_canonical_sha256(record["source_manifest"]),
            "source_physical_manifest": physical_manifest,
            "source_physical_manifest_sha256":
                manifest_canonical_sha256(physical_manifest),
            "source_root_overlay_proof": proof,
            "source_root_overlay_proof_sha256": sha256_bytes(canonical_json(proof)),
            "source_root_overlay_policy_version": proof["policy_version"],
            "source_root_overlay_xattr_name": proof["xattr_name"],
            "source_root_overlay_xattr_value": proof["xattr_value"],
            "source_root_overlay_added_count": proof["added_count"],
            "source_root_overlay_added_paths_sha256": proof["added_paths_sha256"],
            "legacy_prefix": prefix["records"],
            "legacy_prefix_sha256": prefix["aggregate_sha256"],
            "gate_proof": gate_proof,
            "gate_proof_sha256": sha256_bytes(canonical_json(gate_proof)),
        }

    def validate_source_root_overlay_binding(
            self, transaction_fd: int, record: dict[str, Any],
            binding: dict[str, Any], physical_manifest: dict[str, Any], *,
            require_current_gate: bool) -> dict[str, Any]:
        prefix = self.validate_source_root_legacy_prefix(transaction_fd, record)
        stored_proof = binding.get("source_root_overlay_proof")
        if (not isinstance(stored_proof, dict)
                or stored_proof.get("proof_hash")
                != source_root_overlay_proof_hash(stored_proof)
                or binding.get("source_root_overlay_proof_sha256")
                != sha256_bytes(canonical_json(stored_proof))):
            raise ArchiveError("source-root overlay stored proof binding differs")
        authorization = self.historical_source_root_overlay_authorization(
            transaction_fd, record, binding, prefix)
        if authorization is None:
            authorization = REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256
        proof = self.validate_source_root_overlay_manifest(
            record, physical_manifest, stored_proof,
            authorization_sha256=authorization)
        if proof is None:
            raise ArchiveError("source-root overlay binding is unavailable")
        gate_proof = binding.get("gate_proof")
        if not isinstance(gate_proof, dict) \
                or binding.get("gate_proof_sha256") != sha256_bytes(canonical_json(gate_proof)):
            raise ArchiveError("source-root overlay gate binding is invalid")
        recovery_policy = \
            self.retirement_overlay_baseline_policy_for_transaction(
                transaction_fd, record)
        immutable_gate = (
            self.validate_reviewed_historical_gate_proof(gate_proof)
            if recovery_policy is not None else
            self.validate_full_gate_proof(gate_proof, require_fresh=False))
        if require_current_gate:
            current_gate = self.matching_full_gate_receipt()
            self.validate_full_gate_proof(current_gate, require_fresh=True)
            if recovery_policy is None:
                self.require_equivalent_gate_proofs(
                    immutable_gate, current_gate)
            else:
                baseline = self.load_retirement_overlay_baseline(
                    transaction_fd)
                if baseline is not None:
                    open_policy = \
                        self.retirement_pretruncate_open_policy_for_transaction(
                            transaction_fd, record)
                    open_record = self.load_retirement_pretruncate_open(
                        transaction_fd)
                    if open_record is not None:
                        if open_policy is None:
                            raise ArchiveError(
                                "retirement pretruncate open policy is absent")
                        stage_c = open_record.get("current_009b_gate_proof")
                        if not isinstance(stage_c, dict):
                            raise ArchiveError(
                                "retirement pretruncate open lacks gate C")
                        self.require_equivalent_gate_proofs(
                            stage_c, current_gate)
                    elif open_policy is None:
                        stage_b = baseline.get("current_009a_gate_proof")
                        if not isinstance(stage_b, dict):
                            raise ArchiveError(
                                "retirement overlay baseline lacks gate B")
                        self.require_equivalent_gate_proofs(
                            stage_b, current_gate)
        expected = self.source_root_overlay_binding(
            transaction_fd, record, physical_manifest, gate_proof,
            validated_source_root_proof=proof)
        for key, value in expected.items():
            if key not in binding or canonical_json(binding[key]) != canonical_json(value):
                raise ArchiveError(f"source-root overlay binding differs: {key}")
        if canonical_json(prefix["records"]) != canonical_json(binding["legacy_prefix"]):
            raise ArchiveError("source-root overlay prefix binding differs")
        return proof

    def source_manifest_for_retirement(
            self, transaction_fd: int, record: dict[str, Any], *,
            require_current_gate: bool = True) -> dict[str, Any]:
        policy = self.source_root_overlay_policy_for_record(record)
        if policy is None:
            return record["source_manifest"]
        retirement_overlay = self.load_retirement_overlay_baseline(transaction_fd)
        if retirement_overlay is not None:
            self.validate_retirement_overlay_baseline_record(
                transaction_fd, record, retirement_overlay,
                require_current_gate=require_current_gate)
            current = retirement_overlay.get("current_source_manifest")
            if not isinstance(current, dict):
                raise ArchiveError(
                    "retirement overlay baseline lacks current source")
            return current
        quarantined = self.load_stage(transaction_fd, "source-quarantined")
        if quarantined is None:
            raise ArchiveError("source-root overlay retirement lacks durable 008")
        physical = quarantined.get("source_physical_manifest")
        if not isinstance(physical, dict):
            raise ArchiveError("source-root overlay 008 lacks physical manifest")
        self.validate_source_root_overlay_binding(
            transaction_fd, record, quarantined, physical,
            require_current_gate=require_current_gate)
        return physical

    def source_root_overlay_downstream_binding(
            self, transaction_fd: int, record: dict[str, Any], *,
            require_current_gate: bool) -> dict[str, Any]:
        """Bind every post-008 record to the immutable physical source proof."""
        if self.source_root_overlay_policy_for_record(record) is None:
            return {}
        quarantined = self.load_stage(transaction_fd, "source-quarantined")
        if quarantined is None:
            raise ArchiveError("source-root overlay lacks durable 008")
        physical = quarantined.get("source_physical_manifest")
        if not isinstance(physical, dict):
            raise ArchiveError("source-root overlay 008 lacks its physical manifest")
        proof = self.validate_source_root_overlay_binding(
            transaction_fd, record, quarantined, physical,
            require_current_gate=require_current_gate)
        immutable_gate = quarantined["gate_proof"]
        recovery_policy = \
            self.retirement_overlay_baseline_policy_for_transaction(
                transaction_fd, record)
        if recovery_policy is not None:
            self.validate_reviewed_historical_gate_proof(immutable_gate)
            # Immutable 009 already binds historical A in this field.  Gate B
            # is bound separately by 009a and must never rewrite 009.
            current_gate = immutable_gate
        else:
            current_gate = self.matching_full_gate_receipt() \
                if require_current_gate else immutable_gate
            self.validate_full_gate_proof(
                current_gate, require_fresh=require_current_gate)
            self.require_equivalent_gate_proofs(
                immutable_gate, current_gate)
        return {
            "source_quarantined_stage_sha256":
                sha256_bytes(canonical_json(quarantined)),
            "source_semantic_manifest_sha256":
                manifest_canonical_sha256(record["source_manifest"]),
            "source_physical_manifest_sha256":
                manifest_canonical_sha256(physical),
            "source_root_overlay_proof": proof,
            "source_root_overlay_proof_sha256":
                sha256_bytes(canonical_json(proof)),
            "source_root_overlay_policy_version": proof["policy_version"],
            "source_root_overlay_xattr_name": proof["xattr_name"],
            "source_root_overlay_xattr_value": proof["xattr_value"],
            "source_root_overlay_added_count": proof["added_count"],
            "source_root_overlay_added_paths_sha256": proof["added_paths_sha256"],
            "source_root_overlay_legacy_prefix_sha256":
                quarantined["legacy_prefix_sha256"],
            "source_root_overlay_gate_proof": immutable_gate,
            "source_root_overlay_gate_proof_sha256":
                sha256_bytes(canonical_json(immutable_gate)),
            "source_root_overlay_current_gate_proof": current_gate,
            "source_root_overlay_current_gate_proof_sha256":
                sha256_bytes(canonical_json(current_gate)),
            "production_authorization_sha256":
                proof["production_authorization_sha256"],
        }

    def validate_source_root_overlay_downstream_binding(
            self, transaction_fd: int, record: dict[str, Any],
            payload: dict[str, Any], *, require_current_gate: bool) -> None:
        # Stable 008/policy fields must remain exact.  The stage's own current
        # proof is immutable but need not be the newest receipt on a later
        # resume; it is reopened and compared semantically instead.
        expected = self.source_root_overlay_downstream_binding(
            transaction_fd, record, require_current_gate=False)
        dynamic = {
            "source_root_overlay_current_gate_proof",
            "source_root_overlay_current_gate_proof_sha256",
        }
        for key, value in expected.items():
            if key in dynamic:
                continue
            if key not in payload or canonical_json(payload[key]) != canonical_json(value):
                raise ArchiveError(f"source-root downstream binding differs: {key}")
        stage_gate = payload.get("source_root_overlay_current_gate_proof")
        if (not isinstance(stage_gate, dict)
                or payload.get("source_root_overlay_current_gate_proof_sha256")
                != sha256_bytes(canonical_json(stage_gate))):
            raise ArchiveError("source-root current gate binding is invalid")
        recovery_policy = \
            self.retirement_overlay_baseline_policy_for_transaction(
                transaction_fd, record)
        validated_stage_gate = (
            self.validate_reviewed_historical_gate_proof(stage_gate)
            if recovery_policy is not None else
            self.validate_full_gate_proof(stage_gate, require_fresh=False))
        immutable_gate = expected["source_root_overlay_gate_proof"]
        self.require_equivalent_gate_proofs(immutable_gate, validated_stage_gate)
        if require_current_gate:
            current_gate = self.matching_full_gate_receipt()
            self.validate_full_gate_proof(current_gate, require_fresh=True)
            if recovery_policy is None:
                self.require_equivalent_gate_proofs(
                    immutable_gate, current_gate)
            else:
                baseline = self.load_retirement_overlay_baseline(
                    transaction_fd)
                if baseline is not None:
                    open_policy = \
                        self.retirement_pretruncate_open_policy_for_transaction(
                            transaction_fd, record)
                    open_record = self.load_retirement_pretruncate_open(
                        transaction_fd)
                    if open_record is not None:
                        if open_policy is None:
                            raise ArchiveError(
                                "retirement pretruncate open policy is absent")
                        stage_c = open_record.get("current_009b_gate_proof")
                        if not isinstance(stage_c, dict):
                            raise ArchiveError(
                                "retirement pretruncate open lacks gate C")
                        self.require_equivalent_gate_proofs(
                            stage_c, current_gate)
                    elif open_policy is None:
                        stage_b = baseline.get("current_009a_gate_proof")
                        if not isinstance(stage_b, dict):
                            raise ArchiveError(
                                "retirement overlay baseline lacks gate B")
                        self.require_equivalent_gate_proofs(
                            stage_b, current_gate)

    def provenance_overlay_proof_for(
            self, record: dict[str, Any], physical_manifest: dict[str, Any],
            policy: ProvenanceOverlayPolicy) -> dict[str, Any]:
        authorization = REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256
        if (record.get("transaction_id") == SOURCE_ROOT_OVERLAY_TRANSACTION_ID
                and record.get("candidate_id") == SOURCE_ROOT_OVERLAY_CANDIDATE_ID
                and physical_manifest.get("tree_sha256")
                == PROVENANCE_OVERLAY_PHYSICAL_TREE_SHA256):
            authorization = SOURCE_ROOT_OVERLAY_LEGACY_DESTINATION_AUTHORIZATION_SHA256
        return compare_provenance_overlay(
            record["source_manifest"], physical_manifest,
            transaction_id=record["transaction_id"],
            candidate_id=record["candidate_id"],
            allowlist_version=record["allowlist_version"],
            policy=policy,
            authorization_sha256=authorization)

    def validate_copy_overlay(
            self, record: dict[str, Any], physical_manifest: dict[str, Any],
            stored_proof: Any = None, *, allow_legacy_002: dict[str, Any] | None = None
            ) -> dict[str, Any] | None:
        policy = self.provenance_overlay_policy_for_record(record)
        if policy is None:
            if stored_proof is not None:
                raise ArchiveError("provenance overlay proof is forbidden for this candidate")
            if not manifests_equal(record["source_manifest"], physical_manifest):
                raise ArchiveError("copied payload differs from source")
            return None
        proof = self.provenance_overlay_proof_for(record, physical_manifest, policy)
        if stored_proof is not None:
            if not provenance_overlay_proofs_equal(stored_proof, proof):
                raise ArchiveError("provenance overlay proof differs from physical payload")
            return proof
        if allow_legacy_002 is None \
                or not reviewed_legacy_overlay_recovery(record, allow_legacy_002, proof):
            raise ArchiveError("unproved provenance overlay copy-complete is not reviewed")
        return proof

    @staticmethod
    def overlay_record_binding(source_manifest: dict[str, Any],
                               physical_manifest: dict[str, Any],
                               proof: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_manifest": source_manifest,
            "source_manifest_sha256": manifest_canonical_sha256(source_manifest),
            "destination_manifest": physical_manifest,
            "destination_manifest_sha256": manifest_canonical_sha256(physical_manifest),
            "provenance_overlay_proof": proof,
            "provenance_overlay_proof_sha256": sha256_bytes(canonical_json(proof)),
        }

    def validate_overlay_record_binding(
            self, record: dict[str, Any], payload: dict[str, Any],
            physical_manifest: dict[str, Any]) -> dict[str, Any] | None:
        policy = self.provenance_overlay_policy_for_record(record)
        proof = payload.get("provenance_overlay_proof")
        if policy is None:
            forbidden = {
                "source_manifest", "source_manifest_sha256",
                "destination_manifest_sha256", "provenance_overlay_proof",
                "provenance_overlay_proof_sha256",
            }
            if forbidden & set(payload):
                raise ArchiveError("overlay binding is forbidden for this candidate")
            if not manifests_equal(payload["destination_manifest"], physical_manifest):
                raise ArchiveError("strict destination manifest changed")
            return None
        expected = self.provenance_overlay_proof_for(record, physical_manifest, policy)
        binding = self.overlay_record_binding(
            record["source_manifest"], physical_manifest, expected)
        for key, value in binding.items():
            if key not in payload or canonical_json(payload[key]) != canonical_json(value):
                raise ArchiveError(f"overlay record binding differs: {key}")
        if not provenance_overlay_proofs_equal(proof, expected):
            raise ArchiveError("overlay record proof is invalid")
        return expected

    def validate_external_overlay_binding(
            self, record: dict[str, Any], payload: dict[str, Any],
            published: dict[str, Any]) -> None:
        policy = self.provenance_overlay_policy_for_record(record)
        keys = (
            "source_manifest", "source_manifest_sha256",
            "destination_manifest", "destination_manifest_sha256",
            "provenance_overlay_proof", "provenance_overlay_proof_sha256")
        if policy is None:
            overlay_only = {
                "source_manifest_sha256", "destination_manifest_sha256",
                "provenance_overlay_proof", "provenance_overlay_proof_sha256"}
            if overlay_only & set(payload):
                raise ArchiveError("external overlay binding is forbidden")
            return
        self.validate_overlay_record_binding(
            record, published, published["destination_manifest"])
        for key in keys:
            if key not in payload or canonical_json(payload[key]) != canonical_json(
                    published[key]):
                raise ArchiveError(f"external overlay binding differs: {key}")

    def validate_immutable_copy_record(
            self, transaction_fd: int, record: dict[str, Any],
            physical_manifest: dict[str, Any]) -> dict[str, Any] | None:
        copied = self.load_stage(transaction_fd, "copy-complete")
        if copied is None:
            raise ArchiveError("overlay validation lacks immutable copy-complete")
        policy = self.provenance_overlay_policy_for_record(record)
        if policy is None:
            if not manifests_equal(copied["destination_manifest"], physical_manifest):
                raise ArchiveError("strict payload differs from copy-complete")
            return None
        if canonical_json(copied["destination_manifest"]) != canonical_json(
                physical_manifest):
            raise ArchiveError("physical payload differs from immutable 002 manifest")
        stored = copied.get("provenance_overlay_proof")
        proof = self.validate_copy_overlay(
            record, physical_manifest, stored,
            allow_legacy_002=copied if stored is None else None)
        assert proof is not None
        if stored is not None:
            self.validate_overlay_record_binding(record, copied, physical_manifest)
        return proof

    def validate_candidate(self, candidate: CandidateSpec) -> None:
        if candidate.category not in CATEGORY_ROOTS:
            raise ArchiveError("candidate category is invalid")
        if candidate.data_class not in {"reproducible-noncritical", "redundant", "critical"}:
            raise ArchiveError("candidate data class is invalid")
        if not candidate.source.is_absolute() or candidate.source != Path(os.path.normpath(str(candidate.source))):
            raise ArchiveError("candidate path must be normalized and absolute")
        text = str(candidate.source)
        if text == "/Users/patryk/openxray" or text.startswith("/Users/patryk/openxray/"):
            raise ArchiveError("active repository is protected")
        lower_parts = {component.lower() for component in candidate.source.parts}
        retail_role = self.retail_role(candidate)
        if (lower_parts & PROTECTED_COMPONENTS
                or candidate.source.name.startswith(PROTECTED_PREFIXES)) \
                and retail_role is None:
            raise ArchiveError("candidate is protected build/cache/model/retail/active data")
        exact = {str(item.source) for item in self.settings.candidates}
        if text not in exact:
            raise ArchiveError("candidate source is not an exact allowlist entry")
        if candidate.data_class == "critical" and candidate.deletion_rule != "copy-only":
            raise ArchiveError("critical data must be copy-only")

    def historical_retail_path(self, path: str) -> bool:
        if path in HISTORICAL_EXACT_PATHS or path in HISTORICAL_BIN_PATHS \
                or Path(path).name == ".DS_Store":
            return True
        if path.startswith("gamedata/configs/"):
            return Path(path).name == ".gitattributes" \
                or Path(path).suffix.lower() in {".ltx", ".xml"}
        if path.startswith("gamedata/scripts/"):
            return Path(path).name == ".gitattributes" \
                or Path(path).suffix.lower() == ".script"
        if path in {
                "gamedata/shaders/.gitattributes", "gamedata/shaders/compile.py",
                "gamedata/shaders/gl/.s", "gamedata/shaders/r2/shared",
                "gamedata/shaders/r3/shared"}:
            return True
        parts = Path(path).parts
        return (len(parts) >= 4 and parts[:2] == ("gamedata", "shaders")
                and parts[2] in {"gl", "r1", "r2", "r3"}
                and Path(path).suffix.lower() in {".ps", ".vs", ".s", ".h"})

    def retail_inventory_contract(
            self, classifications: list[dict[str, str]]) -> dict[str, Any]:
        ordered = sorted(classifications, key=lambda item: item["path"])
        actual = (
            len(ordered),
            sha256_bytes(canonical_json([item["path"] for item in ordered])),
            sha256_bytes(canonical_json(ordered)),
        )
        expected = (
            RETAIL_INVENTORY_COUNT,
            RETAIL_INVENTORY_PATHS_SHA256,
            RETAIL_INVENTORY_CLASSIFIED_SHA256,
        ) if self.settings.production else self.settings.test_retail_inventory
        if expected is None or actual != expected:
            raise ArchiveError("retail backup differs from exact audited inventory v1")
        return {
            "version": RETAIL_INVENTORY_VERSION,
            "file_count": actual[0],
            "paths_sha256": actual[1],
            "classified_sha256": actual[2],
        }

    def classify_retail_main_manifest(self, manifest: dict[str, Any]) -> list[dict[str, str]]:
        if manifest.get("symlinks") != 0:
            raise ArchiveError("retail backup cannot contain symlinks")
        result: list[dict[str, str]] = []
        entries = manifest.get("entries", [])
        if any(entry.get("uid") != os.geteuid() for entry in entries):
            raise ArchiveError("retail backup contains a foreign-owned entry")
        files = [entry for entry in manifest.get("entries", [])
                 if entry.get("kind") == "regular"]
        for entry in files:
            path = entry.get("path")
            if not isinstance(path, str) or path == ".":
                raise ArchiveError("retail backup contains an invalid file path")
            lower = path.lower()
            if path in MAPPED_SOURCE_PATHS:
                classification = "redundant-mapped"
            elif "savedgames" in Path(lower).parts or lower.endswith(".scop"):
                raise ArchiveError(f"retail backup contains a second save: {path}")
            elif re.search(r"\.db[^/]*$", lower):
                raise ArchiveError(f"retail backup contains an unknown database/archive: {path}")
            elif Path(lower).suffix in ARCHIVE_SUFFIXES:
                raise ArchiveError(f"retail backup contains an unknown archive: {path}")
            elif self.historical_retail_path(path):
                classification = "historical-reproducible"
            else:
                raise ArchiveError(f"retail backup path is not reviewed: {path}")
            result.append({"path": path, "classification": classification})
        mapped = {item["path"] for item in result
                  if item["classification"] == "redundant-mapped"}
        if mapped != MAPPED_SOURCE_PATHS:
            raise ArchiveError("retail backup does not contain exactly 13 mapped files")
        reviewed_directories = {"."}
        for item in result:
            path = Path(item["path"])
            reviewed_directories.update(
                str(parent) for parent in path.parents if str(parent) != ".")
        actual_directories = {entry.get("path") for entry in entries
                              if entry.get("kind") == "directory"}
        if actual_directories != reviewed_directories:
            raise ArchiveError("retail backup contains an unreviewed or empty directory")
        result = sorted(result, key=lambda item: item["path"])
        self.retail_inventory_contract(result)
        return result

    def validate_prepared_manifest(self, manifest: dict[str, Any]) -> None:
        if manifest.get("symlinks") != 0:
            raise ArchiveError("prepared retail tree cannot contain symlinks")
        entries = manifest.get("entries", [])
        if any(entry.get("uid") != os.geteuid() for entry in entries):
            raise ArchiveError("prepared retail tree contains a foreign-owned entry")
        for entry in entries:
            kind = entry.get("kind")
            mode = entry.get("mode")
            path = entry.get("path")
            if kind == "directory" and mode not in {0o700, 0o755}:
                raise ArchiveError(f"prepared retail directory mode is unsafe: {path}")
            if kind == "regular" and (not isinstance(mode, int) or mode & 0o022):
                raise ArchiveError(f"prepared retail file is group/other writable: {path}")
        files = {entry.get("path") for entry in manifest.get("entries", [])
                 if entry.get("kind") == "regular"}
        if None in files or "." in files:
            raise ArchiveError("prepared retail tree contains an invalid path")
        for path in files:
            assert isinstance(path, str)
            lower = path.lower()
            if path in MAPPED_PREPARED_PATHS or path in PREPARED_METADATA_PATHS \
                    or Path(path).name == ".DS_Store":
                continue
            if "savedgames" in Path(lower).parts or lower.endswith(".scop"):
                raise ArchiveError(f"prepared retail tree contains a second save: {path}")
            if re.search(r"\.db[^/]*$", lower) or Path(lower).suffix in ARCHIVE_SUFFIXES:
                raise ArchiveError(f"prepared retail tree contains an unknown database/archive: {path}")
            raise ArchiveError(f"prepared retail tree path is not reviewed: {path}")
        if not MAPPED_PREPARED_PATHS <= files:
            raise ArchiveError("prepared retail tree lacks one of the exact 13 mappings")
        reviewed_directories = {"."}
        for path in files:
            assert isinstance(path, str)
            reviewed_directories.update(
                str(parent) for parent in Path(path).parents if str(parent) != ".")
        actual_directories = {entry.get("path") for entry in entries
                              if entry.get("kind") == "directory"}
        if actual_directories != reviewed_directories:
            raise ArchiveError("prepared retail tree contains an unreviewed or empty directory")

    def validate_sibling_manifest(self, manifest: dict[str, Any]) -> None:
        if manifest.get("symlinks") != 0:
            raise ArchiveError("retail sibling manifest cannot contain symlinks")
        entries = manifest.get("entries", [])
        if any(entry.get("uid") != os.geteuid() for entry in entries):
            raise ArchiveError("retail sibling manifest contains a foreign-owned entry")
        files = {entry.get("path") for entry in manifest.get("entries", [])
                 if entry.get("kind") == "regular"}
        if files != SIBLING_MANIFEST_PATHS:
            raise ArchiveError("retail sibling manifest inventory is not exact")
        actual_directories = {entry.get("path") for entry in entries
                              if entry.get("kind") == "directory"}
        if actual_directories != {"."}:
            raise ArchiveError("retail sibling manifest contains an unreviewed directory")

    def validate_manifest_scope(self, manifest: dict[str, Any],
                                candidate: CandidateSpec | None = None) -> None:
        role = self.retail_role(candidate) if candidate is not None else None
        if role == "main":
            self.classify_retail_main_manifest(manifest)
            return
        if role == "sibling":
            self.validate_sibling_manifest(manifest)
            return
        for entry in manifest.get("entries", []):
            path = entry.get("path")
            if not isinstance(path, str):
                raise ArchiveError("candidate manifest path is invalid")
            components = [component.lower() for component in Path(path).parts
                          if component not in {".", ""}]
            if (set(components) & PROTECTED_COMPONENTS
                    or any(component.startswith(PROTECTED_PREFIXES)
                           for component in components)):
                raise ArchiveError(
                    f"candidate payload contains protected data/build output: {path}")

    def bind_source(self, candidate: CandidateSpec) -> SourceBinding:
        parent_fd = open_absolute_directory(candidate.source.parent, "source parent")
        name = require_leaf(candidate.source.name, "source name")
        try:
            parent_info = os.fstat(parent_fd)
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                kind = "directory"
            elif stat.S_ISREG(info.st_mode):
                kind = "regular"
            else:
                raise ArchiveError("source root must be a regular file or directory")
            root_fd = open_leaf(parent_fd, name, kind)
            linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if identity(os.fstat(root_fd)) != identity(linked):
                os.close(root_fd)
                raise ArchiveError("source root changed while binding")
            return SourceBinding(parent_fd, root_fd, name, kind, identity(parent_info), identity(linked))
        except BaseException:
            os.close(parent_fd)
            raise

    def manifest_admitted_source(self, root_fd: int, kind: str) -> dict[str, Any]:
        return manifest_bound(
            root_fd, kind,
            regular_hook=lambda **values: self.hook(
                "during_source_manifest_hash", **values))

    def bind_owned_tree(self, path: Path, label: str) -> int:
        descriptor = open_absolute_directory(path, label)
        try:
            opened = os.fstat(descriptor)
            linked = os.stat(path, follow_symlinks=False)
            if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.geteuid()
                    or stat.S_IMODE(opened.st_mode) not in {0o700, 0o755}
                    or identity(opened) != identity(linked)):
                raise ArchiveError(f"{label} is foreign, linked, or unstable")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def bind_relative_regular(self, root_fd: int, relative: str,
                              label: str) -> RelativeFileBinding:
        path = Path(relative)
        if (path.is_absolute() or path != Path(os.path.normpath(relative))
                or not path.parts or ".." in path.parts or "." in path.parts):
            raise ArchiveError(f"{label} path is not confined")
        root_info = os.fstat(root_fd)
        if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.geteuid():
            raise ArchiveError(f"{label} root is not owner-safe")
        parent_fd = os.dup(root_fd)
        try:
            for component in path.parts[:-1]:
                info = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) not in {0o700, 0o755}
                        or info.st_dev != root_info.st_dev):
                    raise ArchiveError(f"{label} directory is foreign or cross-device")
                following = open_leaf(parent_fd, component, "directory")
                linked = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
                if identity(os.fstat(following)) != identity(linked):
                    os.close(following)
                    raise ArchiveError(f"{label} directory identity changed")
                os.close(parent_fd)
                parent_fd = following
            name = require_leaf(path.parts[-1], f"{label} filename")
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or info.st_mode & 0o022
                    or info.st_dev != root_info.st_dev):
                raise ArchiveError(f"{label} file is foreign, linked, or cross-device")
            file_fd = open_leaf(parent_fd, name, "regular")
            linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if identity(os.fstat(file_fd)) != identity(linked):
                os.close(file_fd)
                raise ArchiveError(f"{label} file identity changed")
            return RelativeFileBinding(parent_fd, file_fd, name, identity(linked))
        except BaseException:
            os.close(parent_fd)
            raise

    def stable_file_fingerprint(self, binding: RelativeFileBinding,
                                label: str) -> dict[str, Any]:
        before = os.fstat(binding.file_fd)
        digest = sha256_fd(binding.file_fd)
        after = os.fstat(binding.file_fd)
        linked = os.stat(binding.name, dir_fd=binding.parent_fd, follow_symlinks=False)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mode", "st_uid", "st_gid",
                         "st_mtime_ns", "st_ctime_ns")
        if (any(getattr(before, field) != getattr(after, field) for field in stable_fields)
                or identity(after) != binding.file_identity
                or identity(linked) != binding.file_identity):
            raise ArchiveError(f"{label} changed while proving redundancy")
        return {"identity": list(binding.file_identity), "size": before.st_size,
                "sha256": digest}

    def audit(self) -> list[dict[str, Any]]:
        lock_fd = self.acquire_archiver_lock(exclusive=False)
        try:
            results: list[dict[str, Any]] = []
            for candidate in self.settings.candidates:
                try:
                    self.validate_candidate(candidate)
                    source = self.bind_source(candidate)
                    try:
                        manifest = self.manifest_admitted_source(source.root_fd, source.kind)
                        self.validate_manifest_scope(manifest, candidate)
                    finally:
                        source.close()
                    results.append({"id": candidate.ident, "status": "READY_TO_ENQUEUE",
                                    "allowlist_version": ALLOWLIST_VERSION,
                                    "category": candidate.category,
                                    "data_class": candidate.data_class,
                                    "logical_bytes": manifest["logical_bytes"],
                                    "source_tree_sha256": manifest["tree_sha256"]})
                except (ArchiveError, OSError) as error:
                    results.append({"id": candidate.ident, "status": "DEFERRED_SOURCE",
                                    "reason": str(error)})
            return results
        finally:
            os.close(lock_fd)

    def enqueue(self, candidate_id: str) -> dict[str, Any]:
        lock_fd = self.acquire_archiver_lock(exclusive=True)
        try:
            candidate = self.spec(candidate_id)
            source = self.bind_source(candidate)
            try:
                source_manifest = self.manifest_admitted_source(source.root_fd, source.kind)
                self.validate_manifest_scope(source_manifest, candidate)
                record = {
                    "schema": SCHEMA, "stage": "candidate",
                    "transaction_id": uuid.uuid4().hex,
                    "allowlist_version": ALLOWLIST_VERSION,
                    "created_utc": utc_now(), "candidate_id": candidate.ident,
                    "source": str(candidate.source), "source_name": source.name,
                    "source_kind": source.kind,
                    "source_identity": list(source.root_identity),
                    "source_parent_identity": list(source.parent_identity),
                    "category": candidate.category, "data_class": candidate.data_class,
                    "deletion_rule": candidate.deletion_rule,
                    "source_manifest": source_manifest,
                    "source_tree_sha256": source_manifest["tree_sha256"],
                }
            finally:
                source.close()
            queue_fd = self.queue_fd(create=True)
            try:
                transaction_fd = self.create_private_directory_exclusive(
                    queue_fd, record["transaction_id"])
                try:
                    digest = self.write_record(
                        transaction_fd, RECORD_NAMES["candidate"], record)
                finally:
                    os.close(transaction_fd)
                return {"status": "ENQUEUED",
                        "transaction_id": record["transaction_id"],
                        "record_hash": digest}
            finally:
                os.close(queue_fd)
        finally:
            os.close(lock_fd)

    def candidate_record(self, transaction_fd: int) -> dict[str, Any]:
        self.validate_transaction_residue(transaction_fd)
        record = self.load_stage(transaction_fd, "candidate")
        if record is None:
            raise ArchiveError("transaction lacks its candidate record")
        required = {"schema", "stage", "transaction_id", "allowlist_version",
                    "created_utc", "candidate_id",
                    "source", "source_name", "source_kind", "source_identity",
                    "source_parent_identity", "category", "data_class", "deletion_rule",
                    "source_manifest", "source_tree_sha256"}
        if set(record) != required:
            raise ArchiveError("candidate record shape is invalid")
        candidate = self.spec(record["candidate_id"])
        if (record["allowlist_version"] != ALLOWLIST_VERSION
                or record["source"] != str(candidate.source)
                or record["category"] != candidate.category
                or record["source_name"] != candidate.source.name
                or record["data_class"] != candidate.data_class
                or record["deletion_rule"] != candidate.deletion_rule
                or record["source_tree_sha256"] != record["source_manifest"].get("tree_sha256")):
            raise ArchiveError("candidate record no longer matches the allowlist")
        return record

    def bind_volume(self) -> VolumeBinding:
        try:
            descriptor = self.platform.open_mount(self.settings.mount)
        except OSError as error:
            raise DeferredVolume(f"DevArchive is unavailable: {error}") from error
        try:
            opened = os.fstat(descriptor)
            linked = os.stat(self.settings.mount, follow_symlinks=False)
            if not stat.S_ISDIR(opened.st_mode) or identity(opened) != identity(linked):
                raise DeferredVolume("DevArchive path is not bound to the opened descriptor")
            attrs = self.platform.volume_attrs(descriptor)
            expected = {"mountpoint": str(self.settings.mount), "name": self.settings.volume_name,
                        "uuid": self.settings.volume_uuid, "filesystem": "apfs"}
            if attrs != expected:
                raise DeferredVolume("DevArchive attributes differ from the permanent policy")
            return VolumeBinding(descriptor, identity(opened), attrs)
        except BaseException:
            os.close(descriptor)
            raise

    def require_volume(self, volume: VolumeBinding) -> None:
        if volume.fd < 0:
            raise DeferredVolume("DevArchive descriptor is closed")
        opened = os.fstat(volume.fd)
        try:
            linked = os.stat(self.settings.mount, follow_symlinks=False)
        except OSError as error:
            raise DeferredVolume("DevArchive path disappeared") from error
        if identity(opened) != volume.identity or identity(linked) != volume.identity:
            raise DeferredVolume("DevArchive was remounted or replaced")
        if self.platform.volume_attrs(volume.fd) != volume.attrs:
            raise DeferredVolume("DevArchive descriptor attributes changed")

    def external_roots(self, volume: VolumeBinding, category: str) -> ExternalRoots:
        self.require_volume(volume)
        archive_fd = self.open_policy_directory(volume.fd, ARCHIVE_ROOT, volume)
        try:
            self.require_volume(volume)
            category_fd = self.open_policy_directory(
                archive_fd, CATEGORY_ROOTS[category], volume)
            try:
                self.require_volume(volume)
                manifests_fd = self.open_policy_directory(
                    archive_fd, MANIFEST_ROOT, volume)
                return ExternalRoots(archive_fd, category_fd, manifests_fd)
            except BaseException:
                os.close(category_fd)
                raise
        except BaseException:
            os.close(archive_fd)
            raise

    def preflight_policy_roots_readonly(self) -> dict[str, int]:
        volume = self.bind_volume()
        descriptors: list[int] = []
        try:
            archive_fd = self.open_policy_directory(volume.fd, ARCHIVE_ROOT, volume)
            descriptors.append(archive_fd)
            result = {ARCHIVE_ROOT: stat.S_IMODE(os.fstat(archive_fd).st_mode)}
            for leaf in (*CATEGORY_ROOTS.values(), MANIFEST_ROOT):
                descriptor = self.open_policy_directory(archive_fd, leaf, volume)
                descriptors.append(descriptor)
                result[leaf] = stat.S_IMODE(os.fstat(descriptor).st_mode)
            return result
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            volume.close()

    def apply_metadata(self, source_fd: int, destination_fd: int,
                       volume: VolumeBinding) -> None:
        info = os.fstat(source_fd)
        guard = lambda: self.require_volume(volume)
        apply_xattrs_fd(destination_fd, xattrs_fd(source_fd), guard)
        copy_acl_fd(source_fd, destination_fd, guard)
        guard()
        os.fchown(destination_fd, info.st_uid, info.st_gid)
        guard()
        os.fchmod(destination_fd, stat.S_IMODE(info.st_mode))
        guard()
        os.utime(destination_fd, ns=(info.st_atime_ns, info.st_mtime_ns))
        apply_bsd_flags_fd(destination_fd, bsd_flags(info), guard)

    def copy_regular(self, source_fd: int, destination_parent_fd: int, name: str,
                     relative: str, volume: VolumeBinding) -> None:
        self.require_volume(volume)
        destination_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW,
                                 0o600, dir_fd=destination_parent_fd)
        try:
            os.lseek(source_fd, 0, os.SEEK_SET)
            while True:
                block = os.read(source_fd, 1024 * 1024)
                if not block:
                    break
                def guarded_write(fd: int, pending: bytes) -> int:
                    self.require_volume(volume)
                    return self.platform.write(fd, pending)
                complete_write(destination_fd, block, guarded_write)
                self.hook("after_copy_write", relative=relative,
                          destination_parent_fd=destination_parent_fd)
            self.apply_metadata(source_fd, destination_fd, volume)
            self.fsync(destination_fd, f"copied file {relative}", volume)
        finally:
            os.close(destination_fd)

    def copy_directory_contents(self, source_fd: int, destination_fd: int,
                                volume: VolumeBinding, prefix: str = "") -> None:
        source_device = os.fstat(source_fd).st_dev
        for name in sorted(os.listdir(source_fd)):
            require_leaf(name, "copy entry")
            relative = f"{prefix}/{name}" if prefix else name
            info = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
            if info.st_dev != source_device:
                raise ArchiveError(f"copy cannot cross a mount: {relative}")
            if stat.S_ISDIR(info.st_mode):
                child_source = open_leaf(source_fd, name, "directory")
                self.require_volume(volume)
                os.mkdir(name, 0o700, dir_fd=destination_fd)
                child_destination = open_leaf(destination_fd, name, "directory")
                try:
                    self.copy_directory_contents(child_source, child_destination,
                                                 volume, relative)
                    self.apply_metadata(child_source, child_destination, volume)
                    self.fsync(child_destination, f"copied directory {relative}", volume)
                finally:
                    os.close(child_destination)
                    os.close(child_source)
            elif stat.S_ISREG(info.st_mode):
                child_source = open_leaf(source_fd, name, "regular")
                try:
                    self.copy_regular(child_source, destination_fd, name, relative, volume)
                finally:
                    os.close(child_source)
            elif stat.S_ISLNK(info.st_mode):
                metadata = symlink_metadata(source_fd, name, info)
                target = os.readlink(name, dir_fd=source_fd)
                self.require_volume(volume)
                os.symlink(target, name, dir_fd=destination_fd)
                link_fd = os.open(name, os.O_RDONLY | O_SYMLINK, dir_fd=destination_fd)
                try:
                    apply_xattrs_fd(link_fd, metadata["xattrs"],
                                    lambda: self.require_volume(volume))
                finally:
                    os.close(link_fd)
                self.require_volume(volume)
                os.chown(name, metadata["uid"], metadata["gid"], dir_fd=destination_fd,
                         follow_symlinks=False)
                self.require_volume(volume)
                os.utime(name, ns=(metadata["mtime_ns"], metadata["mtime_ns"]),
                         dir_fd=destination_fd, follow_symlinks=False)
            else:
                raise ArchiveError(f"special file cannot be copied: {relative}")

    def copy_source_to(self, source: SourceBinding, stage_fd: int, payload_name: str,
                       volume: VolumeBinding) -> int:
        if source.kind == "regular":
            self.copy_regular(source.root_fd, stage_fd, payload_name, ".", volume)
            return open_leaf(stage_fd, payload_name, "regular")
        self.require_volume(volume)
        os.mkdir(payload_name, 0o700, dir_fd=stage_fd)
        destination_fd = open_leaf(stage_fd, payload_name, "directory")
        try:
            self.copy_directory_contents(source.root_fd, destination_fd, volume)
            self.apply_metadata(source.root_fd, destination_fd, volume)
            self.fsync(destination_fd, "copied root directory", volume)
            return os.dup(destination_fd)
        finally:
            os.close(destination_fd)

    def admitted_retirement_rows(self, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
        entries = manifest.get("entries")
        if not isinstance(entries, list):
            raise ArchiveError("admitted source manifest entries are invalid")
        rows: dict[str, dict[str, Any]] = {}
        for row in entries:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str) \
                    or row["path"] in rows:
                raise ArchiveError("admitted source manifest path is invalid or duplicated")
            local = row.get("local_identity")
            if (not isinstance(local, list) or len(local) != 2
                    or not all(isinstance(value, int) for value in local)):
                raise ArchiveError("admitted source manifest lacks a local identity")
            rows[row["path"]] = row
        if "." not in rows:
            raise ArchiveError("admitted source manifest lacks its root")
        return rows

    def preflight_retirement_tree(
            self, root_fd: int, kind: str, manifest: dict[str, Any], *,
            allow_zeroed_recovery: bool = False) -> dict[str, dict[str, Any]]:
        """Bind the complete admitted inode tree before the first destructive write."""
        rows = self.admitted_retirement_rows(manifest)
        root_row = rows["."]
        if root_row.get("kind") != kind \
                or identity(os.fstat(root_fd)) != tuple(root_row["local_identity"]):
            raise ArchiveError("retirement root differs from its admitted identity")
        if kind == "regular":
            before = os.fstat(root_fd)
            admitted_size = root_row["logical_bytes"]
            if before.st_nlink != 1:
                raise ArchiveError("retirement root gained an external hard link")
            if set(rows) != {"."}:
                raise ArchiveError("regular retirement manifest contains children")
            if before.st_size == admitted_size:
                digest = sha256_fd(root_fd)
                after = os.fstat(root_fd)
                if (identity(after) != tuple(root_row["local_identity"])
                        or after.st_nlink != 1 or after.st_size != admitted_size
                        or digest != root_row["sha256"]):
                    raise ArchiveError("retirement root content changed")
            elif not (allow_zeroed_recovery and before.st_size == 0
                      and admitted_size > 0):
                raise ArchiveError("retirement root size changed")
            return rows

        def walk(directory_fd: int, prefix: str) -> None:
            expected: dict[str, dict[str, Any]] = {}
            for path, row in rows.items():
                if path == ".":
                    continue
                parent = str(Path(path).parent)
                if parent == prefix:
                    expected[Path(path).name] = row
            actual = set(os.listdir(directory_fd))
            if actual != set(expected):
                raise ArchiveError(f"retirement directory inventory changed: {prefix}")
            for name in sorted(expected):
                row = expected[name]
                relative = row["path"]
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                expected_identity = tuple(row["local_identity"])
                if identity(info) != expected_identity:
                    raise ArchiveError(f"retirement child identity changed: {relative}")
                row_kind = row.get("kind")
                if row_kind == "directory" and stat.S_ISDIR(info.st_mode):
                    child = stable_rebind(
                        directory_fd, name, expected_identity, "directory")
                    try:
                        walk(child, relative)
                    finally:
                        os.close(child)
                elif row_kind == "regular" and stat.S_ISREG(info.st_mode):
                    child = stable_rebind(
                        directory_fd, name, expected_identity, "regular")
                    try:
                        before = os.fstat(child)
                        admitted_size = row["logical_bytes"]
                        if before.st_nlink != 1:
                            raise ArchiveError(
                                f"retirement child gained an external hard link: {relative}")
                        if before.st_size == admitted_size:
                            digest = sha256_fd(child)
                            after = os.fstat(child)
                            if (identity(after) != expected_identity
                                    or after.st_nlink != 1
                                    or after.st_size != admitted_size
                                    or digest != row["sha256"]):
                                raise ArchiveError(
                                    f"retirement child content changed: {relative}")
                        elif not (allow_zeroed_recovery and before.st_size == 0
                                  and admitted_size > 0):
                            raise ArchiveError(
                                f"retirement child size changed: {relative}")
                    finally:
                        os.close(child)
                elif row_kind == "symlink" and stat.S_ISLNK(info.st_mode):
                    if os.readlink(name, dir_fd=directory_fd) != row.get("target"):
                        raise ArchiveError(f"retirement symlink target changed: {relative}")
                else:
                    raise ArchiveError(f"retirement child kind changed: {relative}")

        walk(root_fd, ".")
        return rows

    def bind_metadata_restore_plan(
            self, root_fd: int, kind: str,
            manifest: dict[str, Any]) -> MetadataRestorePlan:
        """Verify immutable identity and content before restoring any metadata."""
        rows = self.admitted_retirement_rows(manifest)
        plan = MetadataRestorePlan([], [])

        def bind_regular(descriptor: int, path: str,
                         admitted: dict[str, Any]) -> None:
            plan.regulars.append(MetadataRestoreEntry(descriptor, path, admitted))
            before = os.fstat(descriptor)
            expected_identity = tuple(admitted["local_identity"])
            if (not stat.S_ISREG(before.st_mode)
                    or identity(before) != expected_identity
                    or before.st_nlink != 1
                    or before.st_size != admitted["logical_bytes"]):
                raise ArchiveError(f"pre-restore regular identity/content changed: {path}")
            digest = sha256_fd(descriptor)
            after = os.fstat(descriptor)
            if (not stat.S_ISREG(after.st_mode)
                    or identity(after) != expected_identity
                    or after.st_nlink != 1
                    or after.st_size != admitted["logical_bytes"]
                    or digest != admitted["sha256"]):
                raise ArchiveError(f"pre-restore regular identity/content changed: {path}")

        def bind_directory(directory_fd: int, prefix: str) -> None:
            admitted = rows[prefix]
            plan.directories.append(MetadataRestoreEntry(directory_fd, prefix, admitted))
            expected_identity = tuple(admitted["local_identity"])
            current = os.fstat(directory_fd)
            if (not stat.S_ISDIR(current.st_mode)
                    or identity(current) != expected_identity):
                raise ArchiveError(f"pre-restore directory identity changed: {prefix}")
            expected_names = {Path(path).name for path in rows
                              if path != "." and str(Path(path).parent) == prefix}
            if set(os.listdir(directory_fd)) != expected_names:
                raise ArchiveError(f"pre-restore directory inventory changed: {prefix}")
            for leaf in sorted(expected_names):
                relative = leaf if prefix == "." else f"{prefix}/{leaf}"
                child_row = rows[relative]
                child_identity = tuple(child_row["local_identity"])
                child_info = os.stat(leaf, dir_fd=directory_fd, follow_symlinks=False)
                if identity(child_info) != child_identity:
                    raise ArchiveError(f"pre-restore child identity changed: {relative}")
                if child_row["kind"] == "directory" and stat.S_ISDIR(child_info.st_mode):
                    child = stable_rebind(
                        directory_fd, leaf, child_identity, "directory")
                    bind_directory(child, relative)
                elif child_row["kind"] == "regular" and stat.S_ISREG(child_info.st_mode):
                    child = stable_rebind(
                        directory_fd, leaf, child_identity, "regular")
                    bind_regular(child, relative, child_row)
                elif child_row["kind"] == "symlink" and stat.S_ISLNK(child_info.st_mode):
                    if os.readlink(leaf, dir_fd=directory_fd) != child_row.get("target"):
                        raise ArchiveError(f"pre-restore symlink target changed: {relative}")
                else:
                    raise ArchiveError(f"pre-restore child kind changed: {relative}")

        try:
            root_row = rows["."]
            if kind == "regular":
                if set(rows) != {"."}:
                    raise ArchiveError("regular restore manifest contains children")
                bind_regular(os.dup(root_fd), ".", root_row)
            elif kind == "directory":
                bind_directory(os.dup(root_fd), ".")
            else:
                raise ArchiveError("unsupported metadata restore root kind")
            # Every admitted row was reached before the first metadata write.
            reached = {entry.path for entry in (*plan.regulars, *plan.directories)}
            reached.update(path for path, row in rows.items() if row["kind"] == "symlink")
            if reached != set(rows):
                raise ArchiveError("pre-restore tree coverage differs from admitted manifest")
            return plan
        except BaseException:
            plan.close()
            raise

    def restore_metadata_entry(self, entry: MetadataRestoreEntry, *,
                               directory: bool) -> None:
        """Restore one pinned inode; blocking BSD flags are always applied last."""
        admitted = entry.admitted
        current = os.fstat(entry.fd)
        expected_identity = tuple(admitted["local_identity"])
        expected_type = stat.S_ISDIR(current.st_mode) if directory \
            else stat.S_ISREG(current.st_mode)
        if not expected_type or identity(current) != expected_identity \
                or (not directory and current.st_nlink != 1):
            raise ArchiveError(f"metadata restore inode changed: {entry.path}")
        restore_bsd_flags_fd(entry.fd, 0)
        temporary_mode = admitted["mode"] | stat.S_IRUSR | stat.S_IWUSR
        if directory:
            temporary_mode |= stat.S_IXUSR
        os.fchmod(entry.fd, temporary_mode)
        current = os.fstat(entry.fd)
        if current.st_uid != admitted["uid"] or current.st_gid != admitted["gid"]:
            os.fchown(entry.fd, admitted["uid"], admitted["gid"])
        restore_xattrs_fd(entry.fd, admitted["xattrs"])
        current = os.fstat(entry.fd)
        os.utime(entry.fd, ns=(current.st_atime_ns, admitted["mtime_ns"]))
        os.fchmod(entry.fd, admitted["mode"])
        restore_acl_text_fd(entry.fd, admitted["acl"])
        self.fsync(entry.fd, f"restored source metadata {entry.path}")
        restore_bsd_flags_fd(entry.fd, admitted["flags"])
        self.fsync(entry.fd, f"restored source flags {entry.path}")

    def restore_metadata_plan(self, plan: MetadataRestorePlan) -> None:
        for entry in plan.regulars:
            self.restore_metadata_entry(entry, directory=False)
        for entry in reversed(plan.directories):
            self.restore_metadata_entry(entry, directory=True)

    def validate_retirement_entry_snapshot(
            self, snapshot: dict[str, Any], admitted: dict[str, Any], *,
            allow_recovery_state: bool) -> None:
        """Admit either exact 008 state or one deterministic retirement state."""
        current = retirement_manifest_row(snapshot)
        if canonical_json(current) == canonical_json(admitted):
            return
        path = str(admitted.get("path", "?"))
        if not allow_recovery_state:
            raise ArchiveError(f"retirement entry differs from physical 008: {path}")
        immutable = ("path", "kind", "uid", "gid", "xattrs", "local_identity")
        if any(canonical_json(current.get(key)) != canonical_json(admitted.get(key))
               for key in immutable):
            raise ArchiveError(f"retirement recovery inode metadata differs: {path}")
        kind = admitted["kind"]
        if kind == "symlink":
            raise ArchiveError(f"retirement recovery symlink differs: {path}")
        if current.get("flags") not in {0, admitted.get("flags")} \
                or current.get("acl") not in {"", admitted.get("acl")}:
            raise ArchiveError(f"retirement recovery barriers differ: {path}")
        permission_bits = 0o700 if kind == "directory" else 0o600
        allowed_modes = {admitted["mode"], admitted["mode"] | permission_bits,
                         0o500 if kind == "directory" else 0o400,
                         permission_bits}
        if current.get("mode") not in allowed_modes:
            raise ArchiveError(f"retirement recovery mode differs: {path}")
        if kind == "directory":
            if (current.get("mtime_ns") != admitted.get("mtime_ns")
                    or current.get("logical_bytes") != 0):
                raise ArchiveError(f"retirement recovery directory differs: {path}")
            return
        admitted_size = admitted["logical_bytes"]
        current_size = current.get("logical_bytes")
        if current_size == admitted_size:
            if (current.get("sha256") != admitted.get("sha256")
                    or current.get("mtime_ns") != admitted.get("mtime_ns")):
                raise ArchiveError(f"retirement recovery content differs: {path}")
        elif current_size == 0 and admitted_size > 0:
            if current.get("sha256") != sha256_bytes(b""):
                raise ArchiveError(f"retirement recovery tombstone content differs: {path}")
        else:
            raise ArchiveError(f"retirement recovery size differs: {path}")

    def validate_prepared_retirement_snapshot(
            self, before: dict[str, Any], after: dict[str, Any],
            admitted: dict[str, Any], *, directory: bool) -> None:
        """Prove the exact, deterministic metadata transformation before pinning."""
        path = str(admitted.get("path", "?"))
        expected_mode = before["mode"] | (0o700 if directory else 0o600)
        exact = ("path", "kind", "uid", "gid", "mtime_ns", "xattrs",
                 "local_identity", "logical_bytes")
        if any(canonical_json(after.get(key)) != canonical_json(before.get(key))
               for key in exact):
            raise ArchiveError(f"retirement preparation drifted: {path}")
        if (after.get("mode") != expected_mode or after.get("flags") != 0
                or after.get("acl") != ""):
            raise ArchiveError(f"retirement preparation metadata differs: {path}")
        if not directory and after.get("sha256") != before.get("sha256"):
            raise ArchiveError(f"retirement preparation content differs: {path}")

    def validate_retirement_overlay_partial_manifest(
            self, baseline_manifest: dict[str, Any],
            current_manifest: dict[str, Any],
            policy: RetirementOverlayBaselinePolicy, *,
            permit_partial_mtime: bool = False) -> int:
        baseline = manifest_rows_exact(
            baseline_manifest, "retirement overlay baseline")
        current = manifest_rows_exact(
            current_manifest, "retirement overlay recovery")
        if set(baseline) != set(current):
            raise ArchiveError("retirement overlay recovery inventory differs")
        zeroed = 0
        empty_sha = sha256_bytes(b"")
        for path in sorted(baseline):
            admitted = baseline[path]
            row = current[path]
            if admitted["kind"] != "regular":
                if canonical_json(admitted) != canonical_json(row):
                    raise ArchiveError(
                        f"retirement overlay non-regular changed: {path}")
                continue
            if canonical_json(admitted) == canonical_json(row):
                continue
            before_xattrs = admitted.get("xattrs")
            after_xattrs = row.get("xattrs")
            if not isinstance(before_xattrs, dict) \
                    or not isinstance(after_xattrs, dict):
                raise ArchiveError(
                    f"retirement overlay partial xattrs invalid: {path}")
            if policy.runtime_xattr_name in before_xattrs:
                valid_xattrs = after_xattrs == before_xattrs
            else:
                added = dict(before_xattrs)
                added[policy.runtime_xattr_name] = policy.runtime_xattr_value
                valid_xattrs = after_xattrs in (before_xattrs, added)
            if not valid_xattrs:
                raise ArchiveError(
                    f"retirement overlay partial provenance differs: {path}")
            if row.get("logical_bytes") == admitted.get("logical_bytes"):
                exact_non_xattr = set(admitted) - {"xattrs"}
                if any(canonical_json(admitted.get(key))
                       != canonical_json(row.get(key))
                       for key in exact_non_xattr):
                    raise ArchiveError(
                        f"retirement overlay live regular differs: {path}")
                continue
            fixed = set(admitted) - {
                "logical_bytes", "allocated_bytes", "sha256", "xattrs",
                "mtime_ns"}
            if any(canonical_json(admitted.get(key))
                   != canonical_json(row.get(key)) for key in fixed):
                raise ArchiveError(
                    f"retirement overlay partial metadata differs: {path}")
            allocated = row.get("allocated_bytes")
            if (row.get("logical_bytes") != 0
                    or not isinstance(allocated, int)
                    or isinstance(allocated, bool) or allocated < 0
                    or row.get("sha256") != empty_sha):
                raise ArchiveError(
                    f"retirement overlay partial content differs: {path}")
            if (not permit_partial_mtime
                    and row.get("mtime_ns") != admitted.get("mtime_ns")):
                raise ArchiveError(
                    f"retirement overlay partial mtime differs: {path}")
            zeroed += 1
        return zeroed

    def expected_overlay_regular_tombstone(
            self, admitted: dict[str, Any]) -> dict[str, Any]:
        row = strict_json_loads(canonical_json(admitted).decode("ascii"))
        if not isinstance(row, dict) or row.get("kind") != "regular":
            raise ArchiveError("overlay retirement regular is invalid")
        row["logical_bytes"] = 0
        row["allocated_bytes"] = 0
        row["sha256"] = sha256_bytes(b"")
        return row

    def prepare_retirement_overlay_plan(
            self, descriptor: int, kind: str,
            baseline_manifest: dict[str, Any],
            policy: RetirementOverlayBaselinePolicy, *, parent_fd: int | None,
            name: str | None, root_aliases: set[str]) -> RetirementPlan:
        """Pin writable inodes without changing ACL, flags, mode or xattrs."""
        rows = self.admitted_retirement_rows(baseline_manifest)
        plan = RetirementPlan([], [])

        def pin_regular(parent: int | None, leaf: str | None,
                        admitted: dict[str, Any], path: str) -> None:
            if parent is None or leaf is None:
                raise ArchiveError("overlay regular lacks stable parent binding")
            expected = tuple(admitted["local_identity"])
            writable = stable_rebind(
                parent, leaf, expected, "regular", writable=True)
            try:
                # On APFS, the O_RDWR open itself may add com.apple.provenance.
                # The immutable 009b comparator below admits only that exact
                # monotonic policy value; this hook models the kernel side
                # effect in hermetic tests before the descriptor snapshot.
                self.hook(
                    "after_retirement_overlay_regular_open", path=path,
                    file_fd=writable, parent_fd=parent, name=leaf)
                snapshot = stable_retirement_snapshot(
                    writable, "regular", path)
                row = retirement_manifest_row(snapshot)
                probe = finish_manifest([row])
                baseline_probe = finish_manifest([admitted])
                self.validate_retirement_overlay_partial_manifest(
                    baseline_probe, probe, policy, permit_partial_mtime=True)
                if row["logical_bytes"] == 0 \
                        and admitted["logical_bytes"] > 0:
                    rebound = os.fstat(writable)
                    if (identity(rebound) != expected or rebound.st_nlink != 1
                            or rebound.st_size != 0):
                        raise ArchiveError(
                            f"overlay partial inode differs: {path}")
                    os.utime(
                        writable,
                        ns=(rebound.st_atime_ns, int(admitted["mtime_ns"])))
                    self.fsync(
                        writable, f"restored overlay partial mtime {path}")
                    snapshot = stable_retirement_snapshot(
                        writable, "regular", path)
                    probe = finish_manifest([retirement_manifest_row(snapshot)])
                    self.validate_retirement_overlay_partial_manifest(
                        baseline_probe, probe, policy)
                info = os.fstat(writable)
                if (identity(info) != expected or info.st_nlink != 1
                        or not stat.S_ISREG(info.st_mode)):
                    raise ArchiveError(f"overlay writable binding differs: {path}")
                plan.regulars.append(RetirementRegular(
                    writable, parent, leaf, path, expected[0], expected[1],
                    info.st_size, stat.S_IMODE(info.st_mode), admitted,
                    snapshot, int(admitted["mtime_ns"]),
                    self.expected_overlay_regular_tombstone(admitted)))
                writable = -1
            finally:
                if writable >= 0:
                    os.close(writable)

        def pin_directory(directory_fd: int, prefix: str) -> None:
            admitted = rows[prefix]
            expected_names = {
                Path(path).name for path in rows
                if path != "." and str(Path(path).parent) == prefix}
            if set(os.listdir(directory_fd)) != expected_names:
                raise ArchiveError(
                    f"overlay retirement inventory differs: {prefix}")
            snapshot = stable_retirement_snapshot(
                directory_fd, "directory", prefix)
            if canonical_json(retirement_manifest_row(snapshot)) \
                    != canonical_json(admitted):
                raise ArchiveError(
                    f"overlay retirement directory differs: {prefix}")
            plan.directories.append(RetirementDirectory(
                directory_fd, prefix, snapshot))
            for leaf in sorted(expected_names):
                path = leaf if prefix == "." else f"{prefix}/{leaf}"
                child = rows[path]
                expected = tuple(child["local_identity"])
                info = os.stat(
                    leaf, dir_fd=directory_fd, follow_symlinks=False)
                if identity(info) != expected or info.st_dev != os.fstat(directory_fd).st_dev:
                    raise ArchiveError(
                        f"overlay retirement child binding differs: {path}")
                if child["kind"] == "directory" and stat.S_ISDIR(info.st_mode):
                    pin_directory(stable_rebind(
                        directory_fd, leaf, expected, "directory"), path)
                elif child["kind"] == "regular" and stat.S_ISREG(info.st_mode):
                    pin_regular(directory_fd, leaf, child, path)
                elif child["kind"] == "symlink" and stat.S_ISLNK(info.st_mode):
                    link_fd = os.open(
                        leaf, os.O_RDONLY | O_SYMLINK, dir_fd=directory_fd)
                    target = os.readlink(leaf, dir_fd=directory_fd)
                    snapshot = stable_retirement_snapshot(
                        link_fd, "symlink", path, target=target)
                    if canonical_json(retirement_manifest_row(snapshot)) \
                            != canonical_json(child):
                        os.close(link_fd)
                        raise ArchiveError(
                            f"overlay retirement symlink differs: {path}")
                    plan.symlinks.append(RetirementSymlink(
                        link_fd, directory_fd, leaf, path, expected, snapshot))
                else:
                    raise ArchiveError(
                        f"overlay retirement child kind differs: {path}")

        try:
            if kind == "directory":
                pin_directory(os.dup(descriptor), ".")
            elif kind == "regular":
                if set(rows) != {"."}:
                    raise ArchiveError("overlay regular root has child entries")
                pin_regular(parent_fd, name, rows["."], ".")
            else:
                raise ArchiveError("overlay retirement root kind is unsupported")
            plan.regulars.sort(key=lambda item: item.path)
            plan.symlinks.sort(key=lambda item: item.path)
            plan.root_fd = os.dup(descriptor)
            plan.root_parent_fd = os.dup(parent_fd) if parent_fd is not None else -1
            plan.root_name = name
            plan.root_aliases = set(root_aliases)
            plan.root_kind = kind
            plan.root_identity = identity(os.fstat(descriptor))
            plan.prepared_manifest = manifest_bound(descriptor, kind)
            self.validate_retirement_overlay_partial_manifest(
                baseline_manifest, plan.prepared_manifest, policy)
            return plan
        except BaseException:
            plan.close()
            raise

    def expected_regular_tombstone(
            self, admitted: dict[str, Any]) -> dict[str, Any]:
        """Derive one regular tombstone only from immutable admitted metadata."""
        row = strict_json_loads(canonical_json(admitted).decode("ascii"))
        if not isinstance(row, dict) or row.get("kind") != "regular":
            raise ArchiveError("immutable regular retirement entry is invalid")
        row["mode"] = 0o400
        row["flags"] = 0
        row["acl"] = ""
        row["logical_bytes"] = 0
        row["allocated_bytes"] = 0
        row["sha256"] = sha256_bytes(b"")
        return row

    def build_expected_tombstone_manifest(
            self, immutable_manifest: dict[str, Any]) -> dict[str, Any]:
        """Derive the only admitted tombstone from immutable source metadata."""
        entries = strict_json_loads(
            canonical_json(immutable_manifest["entries"]).decode("ascii"))
        if not isinstance(entries, list):
            raise ArchiveError("prepared retirement inventory is invalid")
        for row in entries:
            if row["kind"] == "regular":
                row.update(self.expected_regular_tombstone(row))
            elif row["kind"] == "directory":
                row["mode"] = 0o500
                row["flags"] = 0
                row["acl"] = ""
            elif row["kind"] != "symlink":
                raise ArchiveError("unsupported expected tombstone entry kind")
        return finish_manifest(entries)

    def validate_existing_tombstone_against_immutable_plan(
            self, transaction_fd: int, record: dict[str, Any],
            retired: dict[str, Any]) -> dict[str, Any]:
        """Ensure an existing 010 never becomes its own metadata authority."""
        baseline = self.load_retirement_overlay_baseline(transaction_fd)
        policy = self.retirement_overlay_baseline_policy_for_transaction(
            transaction_fd, record)
        if baseline is not None and policy is not None:
            tombstone = retired.get("tombstone_manifest")
            if not isinstance(tombstone, dict):
                raise ArchiveError(
                    "durable overlay 010 lacks tombstone manifest")
            self.validate_retirement_overlay_downstream_binding(
                transaction_fd, record, retired, tombstone)
            return tombstone
        immutable = self.source_manifest_for_retirement(
            transaction_fd, record, require_current_gate=True)
        expected = self.build_expected_tombstone_manifest(immutable)
        if canonical_json(retired.get("tombstone_manifest")) \
                != canonical_json(expected):
            raise ArchiveError(
                "durable 010 tombstone differs from immutable retirement plan")
        return expected

    def prepare_retirement_plan(
            self, descriptor: int, kind: str, volume: VolumeBinding,
            rows: dict[str, dict[str, Any]], *, parent_fd: int | None,
            name: str | None, allow_recovery_state: bool = False,
            root_aliases: set[str] | None = None) -> RetirementPlan:
        """Apply all metadata changes and pin every inode before the boundary.

        The returned regular descriptors are writable and remain pinned until
        retirement completes.  Execution performs no metadata operation after
        the final boundary callback and before the first payload truncation.
        """
        plan = RetirementPlan([], [])
        guard = lambda: self.require_volume(volume)

        def prepare_regular(parent: int | None, leaf: str | None,
                            admitted: dict[str, Any], path: str,
                            pinned: int | None = None) -> None:
            immutable_admitted = strict_json_loads(
                canonical_json(admitted).decode("ascii"))
            if not isinstance(immutable_admitted, dict):
                raise ArchiveError(f"immutable retirement entry is invalid: {path}")
            expected_original_mtime_ns = int(immutable_admitted["mtime_ns"])
            expected_tombstone = self.expected_regular_tombstone(
                immutable_admitted)
            admitted_identity = tuple(admitted["local_identity"])
            readonly = pinned
            owns_readonly = pinned is None
            try:
                if readonly is None:
                    if parent is None or leaf is None:
                        raise ArchiveError("regular retirement root lacks a parent binding")
                    readonly = stable_rebind(parent, leaf, admitted_identity, "regular")
                before = stable_retirement_snapshot(readonly, "regular", path)
                self.validate_retirement_entry_snapshot(
                    before, admitted, allow_recovery_state=allow_recovery_state)
                if (before["logical_bytes"] == 0
                        and admitted["logical_bytes"] > 0):
                    # A crash after ftruncate+fsync but before utime is the one
                    # admitted partial-metadata state.  Identity, nlink,
                    # content, ownership and xattrs were already checked above;
                    # restore mtime from immutable 008, never from this partial.
                    rebound = os.fstat(readonly)
                    if (identity(rebound) != admitted_identity
                            or rebound.st_nlink != 1 or rebound.st_size != 0):
                        raise ArchiveError(
                            f"partial tombstone inode is not exact: {path}")
                    os.utime(
                        readonly,
                        ns=(rebound.st_atime_ns, expected_original_mtime_ns))
                    self.fsync(readonly, f"restored partial tombstone mtime {path}")
                    before = stable_retirement_snapshot(
                        readonly, "regular", path)
                    self.validate_retirement_entry_snapshot(
                        before, admitted, allow_recovery_state=True)
                clear_delete_protection_fd(readonly, directory=False, guard=guard)
                guard()
                os.fchmod(readonly, before["mode"] | 0o600)
            finally:
                if owns_readonly and readonly is not None:
                    os.close(readonly)
            if parent is None or leaf is None:
                raise ArchiveError("regular retirement root lacks a stable name binding")
            writable = stable_rebind(
                parent, leaf, admitted_identity, "regular", writable=True)
            try:
                prepared = stable_retirement_snapshot(writable, "regular", path)
                self.validate_prepared_retirement_snapshot(
                    before, prepared, admitted, directory=False)
                prepared_info = os.fstat(writable)
                plan.regulars.append(RetirementRegular(
                    writable, parent, leaf, path, admitted_identity[0],
                    admitted_identity[1], prepared_info.st_size,
                    stat.S_IMODE(prepared_info.st_mode), immutable_admitted,
                    prepared, expected_original_mtime_ns,
                    expected_tombstone))
            except BaseException:
                os.close(writable)
                raise

        def prepare_directory(directory_fd: int, prefix: str) -> None:
            admitted = rows[prefix]
            admitted_identity = tuple(admitted["local_identity"])
            expected_names = {Path(path).name for path in rows
                              if path != "." and str(Path(path).parent) == prefix}
            if set(os.listdir(directory_fd)) != expected_names:
                raise ArchiveError(f"retirement directory inventory changed: {prefix}")
            before = stable_retirement_snapshot(directory_fd, "directory", prefix)
            self.validate_retirement_entry_snapshot(
                before, admitted, allow_recovery_state=allow_recovery_state)
            clear_delete_protection_fd(directory_fd, directory=True, guard=guard)
            prepared = stable_retirement_snapshot(directory_fd, "directory", prefix)
            self.validate_prepared_retirement_snapshot(
                before, prepared, admitted, directory=True)
            plan.directories.append(RetirementDirectory(
                directory_fd, prefix, prepared))
            for leaf in sorted(expected_names):
                relative = leaf if prefix == "." else f"{prefix}/{leaf}"
                child_row = rows[relative]
                child_identity = tuple(child_row["local_identity"])
                child_info = os.stat(leaf, dir_fd=directory_fd, follow_symlinks=False)
                if identity(child_info) != child_identity:
                    raise ArchiveError(f"retirement child identity changed: {relative}")
                if child_row["kind"] == "directory" and stat.S_ISDIR(child_info.st_mode):
                    child_fd = stable_rebind(
                        directory_fd, leaf, child_identity, "directory")
                    prepare_directory(child_fd, relative)
                elif child_row["kind"] == "regular" and stat.S_ISREG(child_info.st_mode):
                    prepare_regular(directory_fd, leaf, child_row, relative)
                elif child_row["kind"] == "symlink" and stat.S_ISLNK(child_info.st_mode):
                    link_fd = os.open(leaf, os.O_RDONLY | O_SYMLINK,
                                      dir_fd=directory_fd)
                    try:
                        target = os.readlink(leaf, dir_fd=directory_fd)
                        snapshot = stable_retirement_snapshot(
                            link_fd, "symlink", relative, target=target)
                        self.validate_retirement_entry_snapshot(
                            snapshot, child_row,
                            allow_recovery_state=allow_recovery_state)
                        plan.symlinks.append(RetirementSymlink(
                            link_fd, directory_fd, leaf, relative,
                            child_identity, snapshot))
                        link_fd = -1
                    finally:
                        if link_fd >= 0:
                            os.close(link_fd)
                else:
                    raise ArchiveError("special entry cannot become a tombstone")

        try:
            if kind == "directory":
                prepare_directory(os.dup(descriptor), ".")
            elif kind == "regular":
                prepare_regular(parent_fd, name, rows["."], ".", descriptor)
            else:
                raise ArchiveError("unsupported retirement root kind")
            plan.regulars.sort(key=lambda binding: binding.path)
            plan.symlinks.sort(key=lambda binding: binding.path)
            plan.root_fd = os.dup(descriptor)
            plan.root_parent_fd = os.dup(parent_fd) if parent_fd is not None else -1
            plan.root_name = name
            plan.root_aliases = set(root_aliases or ({name} if name is not None else set()))
            plan.root_kind = kind
            plan.root_identity = identity(os.fstat(descriptor))
            plan.prepared_manifest = manifest_bound(descriptor, kind)
            immutable_manifest = finish_manifest(strict_json_loads(
                canonical_json(list(rows.values())).decode("ascii")))
            plan.expected_tombstone_manifest = \
                self.build_expected_tombstone_manifest(immutable_manifest)
            return plan
        except BaseException:
            plan.close()
            raise

    def validate_prepared_retirement_root_alias(self, plan: RetirementPlan) -> None:
        if plan.root_parent_fd < 0:
            return
        if plan.root_name is None:
            raise ArchiveError("prepared retirement root name is absent")
        matches = []
        for alias in sorted(plan.root_aliases):
            try:
                linked = os.stat(
                    alias, dir_fd=plan.root_parent_fd,
                    follow_symlinks=False)
            except FileNotFoundError:
                continue
            if identity(linked) == plan.root_identity:
                matches.append(alias)
        if len(matches) != 1:
            raise ArchiveError("prepared retirement root alias is absent or ambiguous")
        rebound = stable_rebind(
            plan.root_parent_fd, matches[0], plan.root_identity,
            plan.root_kind)
        os.close(rebound)

    def close_final_prepared_retirement_tree(
            self, plan: RetirementPlan,
            expected_rows: dict[str, dict[str, Any]],
            first: RetirementRegular | None) -> dict[str, int] | None:
        """Second deterministic closure pass; deliberately contains no hooks.

        Symlinks are closed first, then directories deepest-first so every
        ancestor (including root) is checked after its descendants.  Every
        directory binds each child twice by descriptor-relative nofollow
        ``(name, kind, dev, ino, stat metadata[, symlink target])`` snapshots,
        with stable parent snapshots before, between and after them.  Root is
        closed last; the caller follows only with the first regular's final
        ``fstat`` and immediate ``ftruncate``.
        """
        for symlink in sorted(plan.symlinks, key=lambda item: item.path):
            linked = os.stat(
                symlink.name, dir_fd=symlink.parent_fd,
                follow_symlinks=False)
            if identity(linked) != symlink.expected_identity:
                raise ArchiveError(
                    f"retirement closure symlink was replaced: {symlink.path}")
            target = os.readlink(symlink.name, dir_fd=symlink.parent_fd)
            snapshot = stable_retirement_snapshot(
                symlink.fd, "symlink", symlink.path, target=target)
            if (canonical_json(snapshot) != canonical_json(
                    symlink.prepared_snapshot)
                    or canonical_json(retirement_manifest_row(snapshot))
                    != canonical_json(expected_rows[symlink.path])):
                raise ArchiveError(
                    f"retirement closure symlink changed: {symlink.path}")

        prepared_by_path: dict[str, dict[str, Any]] = {
            item.path: item.prepared_snapshot for item in plan.directories}
        prepared_by_path.update(
            {item.path: item.prepared_snapshot for item in plan.symlinks})
        prepared_by_path.update(
            {item.path: item.prepared_snapshot for item in plan.regulars})
        if set(prepared_by_path) != set(expected_rows):
            raise ArchiveError("retirement closure prepared inventory is incomplete")

        def child_inventory(directory: RetirementDirectory) -> list[dict[str, Any]]:
            expected_names = {
                Path(path).name for path in expected_rows
                if path != "." and str(Path(path).parent) == directory.path}
            names = sorted(os.listdir(directory.fd))
            if set(names) != expected_names:
                raise ArchiveError(
                    f"retirement closure inventory changed: {directory.path}")
            parent_device = os.fstat(directory.fd).st_dev
            bindings: list[dict[str, Any]] = []
            for name in names:
                path = name if directory.path == "." \
                    else f"{directory.path}/{name}"
                admitted = expected_rows.get(path)
                prepared = prepared_by_path.get(path)
                if admitted is None or prepared is None:
                    raise ArchiveError(
                        f"retirement closure child is not admitted: {path}")
                info = os.stat(
                    name, dir_fd=directory.fd, follow_symlinks=False)
                kind = ("directory" if stat.S_ISDIR(info.st_mode)
                        else "regular" if stat.S_ISREG(info.st_mode)
                        else "symlink" if stat.S_ISLNK(info.st_mode)
                        else "special")
                if (kind != admitted["kind"] or info.st_dev != parent_device
                        or identity(info) != tuple(admitted["local_identity"])
                        or retirement_stat_guard(info) != prepared["_stat_guard"]):
                    raise ArchiveError(
                        f"retirement closure child binding changed: {path}")
                binding: dict[str, Any] = {
                    "name": name, "kind": kind,
                    "dev": info.st_dev, "ino": info.st_ino,
                    "stat_guard": retirement_stat_guard(info),
                }
                if kind == "symlink":
                    target = os.readlink(name, dir_fd=directory.fd)
                    if target != admitted.get("target"):
                        raise ArchiveError(
                            f"retirement closure symlink target changed: {path}")
                    binding["target"] = target
                bindings.append(binding)
            return bindings

        def close_directory(directory: RetirementDirectory) -> None:
            if directory.path == ".":
                self.validate_prepared_retirement_root_alias(plan)
            parent_before = stable_retirement_snapshot(
                directory.fd, "directory", directory.path)
            children_before = child_inventory(directory)
            parent_middle = stable_retirement_snapshot(
                directory.fd, "directory", directory.path)
            children_after = child_inventory(directory)
            parent_after = stable_retirement_snapshot(
                directory.fd, "directory", directory.path)
            for snapshot in (parent_before, parent_middle, parent_after):
                if (canonical_json(snapshot) != canonical_json(
                        directory.prepared_snapshot)
                        or canonical_json(retirement_manifest_row(snapshot))
                        != canonical_json(expected_rows[directory.path])):
                    raise ArchiveError(
                        f"retirement closure directory changed: {directory.path}")
            if canonical_json(children_before) != canonical_json(children_after):
                raise ArchiveError(
                    f"retirement closure child bindings drifted: {directory.path}")

        directories = sorted(
            (item for item in plan.directories if item.path != "."),
            key=lambda item: (-len(Path(item.path).parts), item.path))
        for directory in directories:
            close_directory(directory)

        if not plan.directories:
            self.validate_prepared_retirement_root_alias(plan)
        first_guard: dict[str, int] | None = None
        if first is not None:
            if first.parent_fd is not None and first.name is not None:
                linked = os.stat(
                    first.name, dir_fd=first.parent_fd, follow_symlinks=False)
                if identity(linked) != (first.expected_dev, first.expected_ino):
                    raise ArchiveError(
                        f"retirement closure regular was replaced: {first.path}")
            snapshot = stable_retirement_snapshot(first.fd, "regular", first.path)
            if (canonical_json(snapshot) != canonical_json(first.prepared_snapshot)
                    or canonical_json(retirement_manifest_row(snapshot))
                    != canonical_json(expected_rows[first.path])):
                raise ArchiveError(
                    f"retirement closure regular changed: {first.path}")
            first_guard = snapshot["_stat_guard"]

        roots = [item for item in plan.directories if item.path == "."]
        if len(roots) > 1 or (plan.root_kind == "directory" and len(roots) != 1):
            raise ArchiveError("retirement closure root directory binding differs")
        if roots:
            # Root is intentionally the final tree operation before returning
            # the first-regular guard to the adjacent fstat/ftruncate pair.
            close_directory(roots[0])
        return first_guard

    def validate_final_prepared_retirement_tree(
            self, plan: RetirementPlan,
            first: RetirementRegular | None) -> dict[str, int] | None:
        """Revalidate the complete pinned prepared tree at the truncate edge.

        The full descriptor walk proves inventory, mount confinement, entry
        identities/types, all metadata, symlink targets and regular contents.
        Per-inode adjacent snapshots then close metadata drift during the walk;
        the no-hook closure pass finishes with root and the caller can issue
        only the first regular's final ``fstat`` and immediate ``ftruncate``.
        """
        if (plan.root_fd < 0 or plan.prepared_manifest is None
                or plan.root_kind not in {"directory", "regular"}):
            raise ArchiveError("retirement plan lacks its prepared tree binding")
        root = os.fstat(plan.root_fd)
        if identity(root) != plan.root_identity:
            raise ArchiveError("prepared retirement root identity changed")
        self.validate_prepared_retirement_root_alias(plan)
        try:
            current = manifest_bound(plan.root_fd, plan.root_kind)
        except ArchiveError as error:
            if "hard link" in str(error):
                raise ArchiveError(
                    "regular entry gained a hard link before truncate") from error
            raise
        if canonical_json(current) != canonical_json(plan.prepared_manifest):
            raise ArchiveError("prepared retirement tree changed at final boundary")
        expected_rows = self.admitted_retirement_rows(plan.prepared_manifest)

        for directory in plan.directories:
            snapshot = stable_retirement_snapshot(
                directory.fd, "directory", directory.path)
            if (canonical_json(snapshot) != canonical_json(
                    directory.prepared_snapshot)
                    or canonical_json(retirement_manifest_row(snapshot))
                    != canonical_json(expected_rows[directory.path])):
                raise ArchiveError(
                    f"prepared retirement directory changed: {directory.path}")
            self.hook(
                "after_detailed_retirement_directory_snapshot",
                path=directory.path, directory_fd=directory.fd,
                root_fd=plan.root_fd)
        for symlink in plan.symlinks:
            linked = os.stat(
                symlink.name, dir_fd=symlink.parent_fd,
                follow_symlinks=False)
            if identity(linked) != symlink.expected_identity:
                raise ArchiveError(
                    f"prepared retirement symlink was replaced: {symlink.path}")
            target = os.readlink(symlink.name, dir_fd=symlink.parent_fd)
            snapshot = stable_retirement_snapshot(
                symlink.fd, "symlink", symlink.path, target=target)
            if (canonical_json(snapshot) != canonical_json(
                    symlink.prepared_snapshot)
                    or canonical_json(retirement_manifest_row(snapshot))
                    != canonical_json(expected_rows[symlink.path])):
                raise ArchiveError(
                    f"prepared retirement symlink changed: {symlink.path}")
        for regular in plan.regulars:
            if regular is first:
                continue
            if regular.parent_fd is not None and regular.name is not None:
                linked = os.stat(
                    regular.name, dir_fd=regular.parent_fd,
                    follow_symlinks=False)
                if identity(linked) != (regular.expected_dev, regular.expected_ino):
                    raise ArchiveError(
                        f"prepared retirement regular was replaced: {regular.path}")
            snapshot = stable_retirement_snapshot(
                regular.fd, "regular", regular.path)
            if (canonical_json(snapshot) != canonical_json(
                    regular.prepared_snapshot)
                    or canonical_json(retirement_manifest_row(snapshot))
                    != canonical_json(expected_rows[regular.path])):
                raise ArchiveError(
                    f"prepared retirement regular changed: {regular.path}")
        return self.close_final_prepared_retirement_tree(
            plan, expected_rows, first)

    def validate_exact_postclosure_tombstone(
            self, plan: RetirementPlan) -> dict[str, Any]:
        """Bind the exact immutable plan after retirement and before 010.

        This is the sole post-closure inventory.  It is not another attempt to
        close the pre-truncate race: the cooperating-tool OS lock supplies that
        boundary.  Instead it proves that retirement produced only the pinned
        zero-data skeleton and that no foreign or drifted entry can be blessed
        by 010/011 merely because aggregate logical bytes happen to be zero.
        """
        expected = plan.expected_tombstone_manifest
        if expected is None or plan.root_fd < 0:
            raise ArchiveError("retirement plan lacks exact expected tombstone")
        self.validate_prepared_retirement_root_alias(plan)
        current = manifest_bound(plan.root_fd, plan.root_kind)
        if canonical_json(current) != canonical_json(expected):
            raise ArchiveError("exact post-closure tombstone differs from retirement plan")
        rows = self.admitted_retirement_rows(expected)
        root_device = plan.root_identity[0]
        reached: set[str] = set()

        def require_bound_stat(
                info: os.stat_result, path: str, kind: str,
                expected_identity: tuple[int, int]) -> None:
            actual_kind = ("directory" if stat.S_ISDIR(info.st_mode)
                           else "regular" if stat.S_ISREG(info.st_mode)
                           else "symlink" if stat.S_ISLNK(info.st_mode)
                           else "special")
            if (actual_kind != kind or info.st_dev != root_device
                    or identity(info) != expected_identity):
                raise ArchiveError(
                    f"post-closure tombstone binding differs: {path}")

        for directory in plan.directories:
            admitted = rows[directory.path]
            opened = os.fstat(directory.fd)
            require_bound_stat(
                opened, directory.path, "directory",
                tuple(admitted["local_identity"]))
            snapshot = stable_retirement_snapshot(
                directory.fd, "directory", directory.path)
            if canonical_json(retirement_manifest_row(snapshot)) \
                    != canonical_json(admitted):
                raise ArchiveError(
                    f"post-closure directory metadata differs: {directory.path}")
            expected_names = {
                Path(path).name for path in rows
                if path != "." and str(Path(path).parent) == directory.path}
            if set(os.listdir(directory.fd)) != expected_names:
                raise ArchiveError(
                    f"post-closure directory inventory differs: {directory.path}")
            for name in sorted(expected_names):
                path = name if directory.path == "." \
                    else f"{directory.path}/{name}"
                child = rows[path]
                linked = os.stat(
                    name, dir_fd=directory.fd, follow_symlinks=False)
                require_bound_stat(
                    linked, path, child["kind"],
                    tuple(child["local_identity"]))
                if child["kind"] == "symlink" \
                        and os.readlink(name, dir_fd=directory.fd) \
                        != child.get("target"):
                    raise ArchiveError(
                        f"post-closure symlink target differs: {path}")
            reached.add(directory.path)

        for symlink in plan.symlinks:
            admitted = rows[symlink.path]
            linked = os.stat(
                symlink.name, dir_fd=symlink.parent_fd,
                follow_symlinks=False)
            require_bound_stat(
                linked, symlink.path, "symlink",
                tuple(admitted["local_identity"]))
            target = os.readlink(symlink.name, dir_fd=symlink.parent_fd)
            snapshot = stable_retirement_snapshot(
                symlink.fd, "symlink", symlink.path, target=target)
            if canonical_json(retirement_manifest_row(snapshot)) \
                    != canonical_json(admitted):
                raise ArchiveError(
                    f"post-closure symlink metadata differs: {symlink.path}")
            reached.add(symlink.path)

        for regular in plan.regulars:
            admitted = rows[regular.path]
            if regular.parent_fd is None or regular.name is None:
                raise ArchiveError(
                    f"post-closure regular lacks name binding: {regular.path}")
            linked = os.stat(
                regular.name, dir_fd=regular.parent_fd,
                follow_symlinks=False)
            expected_identity = tuple(admitted["local_identity"])
            require_bound_stat(
                linked, regular.path, "regular", expected_identity)
            opened = os.fstat(regular.fd)
            require_bound_stat(
                opened, regular.path, "regular", expected_identity)
            if opened.st_nlink != 1 or opened.st_size != 0:
                raise ArchiveError(
                    f"post-closure regular is not an exact pinned tombstone: {regular.path}")
            snapshot = stable_retirement_snapshot(
                regular.fd, "regular", regular.path)
            if canonical_json(retirement_manifest_row(snapshot)) \
                    != canonical_json(admitted):
                raise ArchiveError(
                    f"post-closure regular metadata differs: {regular.path}")
            reached.add(regular.path)

        if reached != set(rows):
            raise ArchiveError("post-closure pinned plan coverage differs")
        return current

    def execute_retirement_plan(
            self, plan: RetirementPlan, volume: VolumeBinding,
            retirement_state: dict[str, bool],
            final_retirement_boundary: Callable[[], None], *,
            boundary_hook: bool) -> dict[str, Any]:
        """Cross one proof boundary, then perform only fstat -> ftruncate.

        The hook is deliberately before the callback.  There is no production
        injection point after the callback: for the first nonempty regular file
        its return is followed by exactly one fstat syscall and ftruncate as the
        immediately following syscall.
        """
        nonempty = [item for item in plan.regulars if item.expected_size != 0]
        first = nonempty[0] if nonempty else None

        def durably_restore_truncated_metadata(
                regular: RetirementRegular, index: int) -> None:
            retirement_state["payload_write_started"] = True
            try:
                self.platform.fsync(regular.fd)
            except OSError as error:
                raise ArchiveError(
                    f"cannot durably record partial retirement: {error}") from error
            self.hook(
                "after_retirement_ftruncate_before_metadata_restore",
                path=regular.path, index=index, total=len(nonempty),
                file_fd=regular.fd, parent_fd=regular.parent_fd,
                name=regular.name)
            current = os.fstat(regular.fd)
            if (identity(current) != (regular.expected_dev, regular.expected_ino)
                    or current.st_nlink != 1 or current.st_size != 0):
                raise ArchiveError(
                    "truncated regular identity changed before metadata restore")
            os.utime(
                regular.fd,
                ns=(current.st_atime_ns, regular.expected_original_mtime_ns))
            try:
                self.platform.fsync(regular.fd)
            except OSError as error:
                raise ArchiveError(
                    f"cannot durably restore tombstone metadata: {error}") from error
            restored = stable_retirement_snapshot(
                regular.fd, "regular", regular.path)
            current_row = retirement_manifest_row(restored)
            expected = regular.expected_tombstone
            # Mode sealing is deliberately later; all other immutable metadata
            # must already match before the legacy after-truncate hook can run.
            current_row["mode"] = expected["mode"]
            if canonical_json(current_row) != canonical_json(expected):
                raise ArchiveError(
                    f"truncated regular metadata restore differs: {regular.path}")
            self.hook("after_retirement_truncate", path=regular.path,
                      index=index, total=len(nonempty))
        if boundary_hook:
            self.hook(
                "after_retirement_metadata_prepared",
                file_fd=first.fd if first is not None else None,
                parent_fd=first.parent_fd if first is not None else None,
                name=first.name if first is not None else None,
                path=first.path if first is not None else None,
                root_fd=plan.root_fd)
        if boundary_hook:
            self.hook(
                "before_final_retirement_boundary",
                file_fd=first.fd if first is not None else None,
                parent_fd=first.parent_fd if first is not None else None,
                name=first.name if first is not None else None,
                path=first.path if first is not None else None,
                root_fd=plan.root_fd)
        final_retirement_boundary()
        first_guard = self.validate_final_prepared_retirement_tree(plan, first)
        if first is not None:
            final = os.fstat(first.fd)
            if (first_guard is None
                    or retirement_stat_guard(final) != first_guard
                    or not stat.S_ISREG(final.st_mode)
                    or final.st_dev != first.expected_dev
                    or final.st_ino != first.expected_ino
                    or final.st_nlink != 1
                    or final.st_size != first.expected_size
                    or stat.S_IMODE(final.st_mode) != first.prepared_mode):
                raise ArchiveError("regular file changed or gained a hard link before truncate")
            os.ftruncate(first.fd, 0)
            durably_restore_truncated_metadata(first, 0)
        for index, regular in enumerate(nonempty[1:], start=1):
            self.require_volume(volume)
            snapshot = stable_retirement_snapshot(
                regular.fd, "regular", regular.path)
            if canonical_json(snapshot) != canonical_json(
                    regular.prepared_snapshot):
                raise ArchiveError(
                    "regular metadata/content changed after retirement preparation")
            guard = snapshot["_stat_guard"]
            if regular.parent_fd is not None and regular.name is not None:
                linked = os.stat(
                    regular.name, dir_fd=regular.parent_fd,
                    follow_symlinks=False)
                if identity(linked) != (regular.expected_dev, regular.expected_ino):
                    raise ArchiveError(
                        "regular name was replaced after retirement began")
            final = os.fstat(regular.fd)
            if (retirement_stat_guard(final) != guard
                    or not stat.S_ISREG(final.st_mode)
                    or final.st_dev != regular.expected_dev
                    or final.st_ino != regular.expected_ino
                    or final.st_nlink != 1
                    or final.st_size != regular.expected_size
                    or stat.S_IMODE(final.st_mode) != regular.prepared_mode):
                raise ArchiveError("regular file changed or gained a hard link before truncate")
            os.ftruncate(regular.fd, 0)
            durably_restore_truncated_metadata(regular, index)
        for regular in plan.regulars:
            self.fsync(regular.fd, "retired regular payload", volume)
            self.require_volume(volume)
            os.fchmod(regular.fd, 0o400)
            self.fsync(regular.fd, "sealed regular tombstone", volume)
        for directory in reversed(plan.directories):
            self.fsync(directory.fd, "retired directory payload", volume)
            self.require_volume(volume)
            os.fchmod(directory.fd, 0o500)
            self.fsync(directory.fd, "sealed directory tombstone", volume)
        return self.validate_exact_postclosure_tombstone(plan)

    def execute_retirement_overlay_plan(
            self, plan: RetirementPlan, volume: VolumeBinding,
            retirement_state: dict[str, bool],
            final_retirement_boundary: Callable[[], None],
            baseline_manifest: dict[str, Any],
            baseline_proof: dict[str, Any],
            policy: RetirementOverlayBaselinePolicy) \
            -> tuple[dict[str, Any], dict[str, Any]]:
        """Truncate exact pinned inodes without preparing or sealing metadata."""
        nonempty = [item for item in plan.regulars if item.expected_size != 0]
        first = nonempty[0] if nonempty else None
        self.hook(
            "before_final_retirement_boundary",
            file_fd=first.fd if first is not None else None,
            parent_fd=first.parent_fd if first is not None else None,
            name=first.name if first is not None else None,
            path=first.path if first is not None else None,
            root_fd=plan.root_fd)
        final_retirement_boundary()
        first_guard = self.validate_final_prepared_retirement_tree(plan, first)

        def finish_truncate(regular: RetirementRegular, index: int) -> None:
            retirement_state["payload_write_started"] = True
            try:
                self.platform.fsync(regular.fd)
            except OSError as error:
                raise ArchiveError(
                    f"cannot durably record overlay retirement: {error}") from error
            self.hook(
                "after_retirement_ftruncate_before_metadata_restore",
                path=regular.path, index=index, total=len(nonempty),
                file_fd=regular.fd, parent_fd=regular.parent_fd,
                name=regular.name)
            current = os.fstat(regular.fd)
            if (identity(current) != (regular.expected_dev, regular.expected_ino)
                    or current.st_nlink != 1 or current.st_size != 0):
                raise ArchiveError(
                    "overlay truncated inode changed before mtime restore")
            os.utime(
                regular.fd,
                ns=(current.st_atime_ns, regular.expected_original_mtime_ns))
            try:
                self.platform.fsync(regular.fd)
            except OSError as error:
                raise ArchiveError(
                    f"cannot durably restore overlay mtime: {error}") from error
            snapshot = stable_retirement_snapshot(
                regular.fd, "regular", regular.path)
            self.validate_retirement_overlay_partial_manifest(
                finish_manifest([regular.admitted]),
                finish_manifest([retirement_manifest_row(snapshot)]), policy)
            self.hook(
                "after_retirement_truncate", path=regular.path,
                index=index, total=len(nonempty), file_fd=regular.fd,
                root_fd=plan.root_fd)

        if first is not None:
            final = os.fstat(first.fd)
            if (first_guard is None
                    or retirement_stat_guard(final) != first_guard
                    or not stat.S_ISREG(final.st_mode)
                    or identity(final) != (first.expected_dev, first.expected_ino)
                    or final.st_nlink != 1
                    or final.st_size != first.expected_size
                    or stat.S_IMODE(final.st_mode) != first.prepared_mode):
                raise ArchiveError(
                    "overlay regular changed before first truncate")
            os.ftruncate(first.fd, 0)
            finish_truncate(first, 0)
        for index, regular in enumerate(nonempty[1:], start=1):
            self.require_volume(volume)
            snapshot = stable_retirement_snapshot(
                regular.fd, "regular", regular.path)
            if canonical_json(snapshot) != canonical_json(
                    regular.prepared_snapshot):
                raise ArchiveError("overlay regular changed before truncate")
            guard = snapshot["_stat_guard"]
            if regular.parent_fd is not None and regular.name is not None:
                linked = os.stat(
                    regular.name, dir_fd=regular.parent_fd,
                    follow_symlinks=False)
                if identity(linked) != (regular.expected_dev, regular.expected_ino):
                    raise ArchiveError("overlay regular name binding changed")
            final = os.fstat(regular.fd)
            if (retirement_stat_guard(final) != guard
                    or not stat.S_ISREG(final.st_mode)
                    or identity(final) != (regular.expected_dev, regular.expected_ino)
                    or final.st_nlink != 1
                    or final.st_size != regular.expected_size
                    or stat.S_IMODE(final.st_mode) != regular.prepared_mode):
                raise ArchiveError("overlay regular changed before truncate")
            os.ftruncate(regular.fd, 0)
            finish_truncate(regular, index)
        current = manifest_bound(plan.root_fd, plan.root_kind)
        self.validate_retirement_overlay_partial_manifest(
            baseline_manifest, current, policy)
        proof = compare_retirement_runtime_overlay(
            baseline_manifest, current, policy=policy,
            baseline_proof=baseline_proof)
        return current, proof

    def retire_pinned_payload(
            self, descriptor: int, kind: str, volume: VolumeBinding,
            rows: dict[str, dict[str, Any]], *, parent_fd: int | None,
            name: str | None, retirement_state: dict[str, bool],
            final_retirement_boundary: Callable[[], None],
            boundary_hook: bool = True) -> dict[str, Any]:
        plan = self.prepare_retirement_plan(
            descriptor, kind, volume, rows, parent_fd=parent_fd, name=name)
        try:
            return self.execute_retirement_plan(
                plan, volume, retirement_state, final_retirement_boundary,
                boundary_hook=boundary_hook)
        finally:
            plan.close()

    def find_owned_name(self, parent_fd: int, expected: tuple[int, int],
                        allowed: set[str]) -> str:
        matches: list[str] = []
        for name in sorted(os.listdir(parent_fd)):
            if name not in allowed:
                continue
            if identity(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) == expected:
                matches.append(name)
        if len(matches) != 1:
            raise ArchiveError("owned tombstone does not have one exact retained name")
        return matches[0]

    def restore_mismatch(self, quarantine_fd: int, moved_name: str,
                         destination_fd: int, destination_name: str) -> None:
        try:
            os.stat(destination_name, dir_fd=destination_fd, follow_symlinks=False)
        except FileNotFoundError:
            self.platform.rename_exclusive(quarantine_fd, moved_name,
                                           destination_fd, destination_name)
            self.fsync(quarantine_fd, "mismatch quarantine restore")
            self.fsync(destination_fd, "mismatch destination restore")
        else:
            raise ArchiveError("foreign moved entry preserved in quarantine; destination is occupied")

    def bind_recorded_source(self, record: dict[str, Any],
                             transaction_fd: int | None = None) -> SourceBinding:
        candidate = self.spec(record["candidate_id"])
        source = self.bind_source(candidate)
        if (list(source.root_identity) != record["source_identity"]
                or list(source.parent_identity) != record["source_parent_identity"]
                or source.kind != record["source_kind"]):
            source.close()
            raise ArchiveError("source root or parent identity changed")
        manifest = self.manifest_admitted_source(source.root_fd, source.kind)
        policy = self.source_root_overlay_policy_for_record(record)
        if policy is None:
            if not manifests_equal(record["source_manifest"], manifest):
                source.close()
                raise ArchiveError("source content changed since enqueue")
        else:
            if transaction_fd is None:
                source.close()
                raise ArchiveError("source-root overlay requires its transaction binding")
            self.validate_source_root_legacy_prefix(transaction_fd, record)
            retirement_overlay_policy = \
                self.retirement_overlay_baseline_policy_for_transaction(
                    transaction_fd, record)
            retirement_overlay_baseline = \
                self.load_retirement_overlay_baseline(transaction_fd)
            if retirement_overlay_policy is not None:
                if retirement_overlay_baseline is None:
                    quarantined = self.load_stage(
                        transaction_fd, "source-quarantined")
                    if quarantined is None:
                        source.close()
                        raise ArchiveError(
                            "retirement overlay source lacks immutable 008")
                    compare_retirement_overlay_baseline(
                        quarantined["source_physical_manifest"], manifest,
                        policy=retirement_overlay_policy,
                        production_authorization_sha256=
                        REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
                else:
                    self.validate_retirement_overlay_baseline_record(
                        transaction_fd, record, retirement_overlay_baseline,
                        require_current_gate=True)
                    if not self.validate_retirement_pretruncate_source_state(
                            transaction_fd, record, manifest,
                            require_current_gate=True):
                        self.validate_retirement_overlay_partial_manifest(
                            retirement_overlay_baseline[
                                "current_source_manifest"],
                            manifest, retirement_overlay_policy,
                            permit_partial_mtime=True)
                return source
            quarantined = self.load_stage(transaction_fd, "source-quarantined")
            if quarantined is None:
                self.validate_source_root_overlay_manifest(record, manifest)
            else:
                self.validate_source_root_overlay_binding(
                    transaction_fd, record, quarantined, manifest,
                    require_current_gate=True)
        return source

    def second_copy_payload_hash(self, payload: dict[str, Any]) -> str:
        unsigned = dict(payload)
        supplied = unsigned.pop("proof_hash", None)
        digest = sha256_bytes(canonical_json(unsigned))
        if supplied is not None and supplied != digest:
            raise ArchiveError("second-copy proof hash is invalid")
        return digest

    def build_second_copy_proof(self, record: dict[str, Any],
                                source_fd: int | None = None,
                                transaction_fd: int | None = None) -> dict[str, Any]:
        candidate = self.spec(record["candidate_id"])
        if self.retail_role(candidate) != "main":
            raise ArchiveError("second-copy proof is restricted to the exact retail backup")
        owned_source: SourceBinding | None = None
        if source_fd is None:
            owned_source = self.bind_recorded_source(record, transaction_fd)
            source_fd = owned_source.root_fd
        prepared_fd = self.bind_owned_tree(
            self.settings.retail_prepared_root, "fixed prepared retail root")
        try:
            assert source_fd is not None
            source_info = os.fstat(source_fd)
            prepared_info = os.fstat(prepared_fd)
            if identity(source_info) == identity(prepared_info):
                raise ArchiveError("prepared root is not independent from the source root")
            source_manifest = self.manifest_admitted_source(source_fd, record["source_kind"])
            policy = self.source_root_overlay_policy_for_record(record)
            if policy is None:
                if not manifests_equal(record["source_manifest"], source_manifest):
                    raise ArchiveError("retail source changed before second-copy proof")
                proof_source_manifest = source_manifest
            else:
                if transaction_fd is None:
                    raise ArchiveError("source-root overlay proof lacks transaction binding")
                self.validate_source_root_overlay_manifest(record, source_manifest)
                quarantined = self.load_stage(transaction_fd, "source-quarantined")
                if quarantined is not None:
                    self.validate_source_root_overlay_binding(
                        transaction_fd, record, quarantined, source_manifest,
                        require_current_gate=True)
                proof_source_manifest = record["source_manifest"]
            classifications = self.classify_retail_main_manifest(proof_source_manifest)
            prepared_manifest = manifest_bound(prepared_fd, "directory")
            self.validate_prepared_manifest(prepared_manifest)
            mappings: list[dict[str, Any]] = []
            for index, (source_path, prepared_path) in enumerate(SECOND_COPY_MAPPINGS):
                source_file = self.bind_relative_regular(
                    source_fd, source_path, "retail source mapping")
                prepared_file = self.bind_relative_regular(
                    prepared_fd, prepared_path, "prepared retail mapping")
                try:
                    self.hook(
                        "after_second_copy_file_bind", index=index,
                        source_path=source_path, prepared_path=prepared_path,
                        source_binding=source_file, prepared_binding=prepared_file)
                    source_fingerprint = self.stable_file_fingerprint(
                        source_file, "retail source mapping")
                    prepared_fingerprint = self.stable_file_fingerprint(
                        prepared_file, "prepared retail mapping")
                    if source_fingerprint["identity"] == prepared_fingerprint["identity"]:
                        raise ArchiveError("prepared mapping is a hard link to its source")
                    if (source_fingerprint["size"] != prepared_fingerprint["size"]
                            or source_fingerprint["sha256"]
                            != prepared_fingerprint["sha256"]):
                        raise ArchiveError(f"prepared mapping differs: {source_path}")
                    mappings.append({
                        "source_path": source_path, "prepared_path": prepared_path,
                        "source_identity": source_fingerprint["identity"],
                        "prepared_identity": prepared_fingerprint["identity"],
                        "size": source_fingerprint["size"],
                        "sha256": source_fingerprint["sha256"],
                    })
                finally:
                    prepared_file.close()
                    source_file.close()
            payload = {
                "schema": "openxray.second-copy-proof.v1",
                "transaction_id": record["transaction_id"],
                "candidate_id": record["candidate_id"], "category": record["category"],
                "source": record["source"],
                "prepared_root": str(self.settings.retail_prepared_root),
                "source_root_identity": list(identity(source_info)),
                "prepared_root_identity": list(identity(prepared_info)),
                "source_manifest": proof_source_manifest,
                "prepared_manifest": prepared_manifest,
                "inventory": self.retail_inventory_contract(classifications),
                "classifications": classifications, "mappings": mappings,
            }
            payload["proof_hash"] = self.second_copy_payload_hash(payload)
            return payload
        finally:
            os.close(prepared_fd)
            if owned_source is not None:
                owned_source.close()

    def ensure_main_second_copy_proof(self, transaction_fd: int,
                                      record: dict[str, Any], volume: VolumeBinding,
                                      roots: ExternalRoots) -> dict[str, Any]:
        existing = self.load_stage(transaction_fd, "second-copy-proof")
        quarantined = self.load_stage(transaction_fd, "source-quarantined")
        proof: dict[str, Any] | None = None
        if (existing is not None
                and self.source_root_overlay_policy_for_record(record) is not None):
            # In the one reviewed crash window the exact source inode may have
            # moved to private quarantine before 008 was published.  Immutable
            # 006 and its external proof already precede that move and are
            # sufficient here; 008 will independently bind the physical source.
            self.validate_source_root_legacy_prefix(transaction_fd, record)
            name = f"{record['transaction_id']}-second-copy-proof.json"
            external = self.read_record(roots.manifests_fd, existing["external_proof"])
            if (existing.get("external_proof") != name
                    or existing.get("external_proof_sha256")
                    != sha256_bytes(canonical_json(external))
                    or existing.get("proof_hash")
                    != self.second_copy_payload_hash(external)):
                raise ArchiveError("legacy source-root second-copy proof changed")
            return external
        if quarantined is None:
            first = self.build_second_copy_proof(record, transaction_fd=transaction_fd)
            self.hook("after_initial_second_copy_proof", proof=first)
            second = self.build_second_copy_proof(record, transaction_fd=transaction_fd)
            if canonical_json(first) != canonical_json(second):
                raise ArchiveError("second-copy proof changed between independent reads")
            proof = second
        name = f"{record['transaction_id']}-second-copy-proof.json"
        if existing is not None and proof is None:
            external = self.read_record(roots.manifests_fd, existing["external_proof"])
            if existing["external_proof"] != name \
                    or existing["external_proof_sha256"] != sha256_bytes(canonical_json(external)) \
                    or existing["proof_hash"] != self.second_copy_payload_hash(external):
                raise ArchiveError("durable second-copy proof record is inconsistent")
            return external
        assert proof is not None
        self.hook("before_second_copy_proof_publish", proof=proof)
        self.require_volume(volume)
        digest = self.write_record(roots.manifests_fd, name, proof, volume)
        self.fsync(roots.manifests_fd, "external second-copy proof root", volume)
        if existing is None:
            self.hook("after_external_second_copy_proof")
            payload = {"transaction_id": record["transaction_id"],
                       "proof_kind": "fixed-prepared-retail",
                       "proof_hash": proof["proof_hash"],
                       "external_proof": name, "external_proof_sha256": digest}
            self.stage(transaction_fd, "second-copy-proof", payload)
            return proof
        if (existing["external_proof"] != name
                or existing["external_proof_sha256"] != digest
                or existing["proof_hash"] != proof["proof_hash"]):
            raise ArchiveError("local and external second-copy proof records differ")
        return proof

    def verify_prepared_against_immutable_proof(
            self, proof: dict[str, Any]) -> None:
        if proof.get("schema") != "openxray.second-copy-proof.v1" \
                or self.second_copy_payload_hash(proof) != proof.get("proof_hash"):
            raise ArchiveError("immutable retail second-copy proof is invalid")
        inventory = self.retail_inventory_contract(proof.get("classifications", []))
        if canonical_json(proof.get("inventory")) != canonical_json(inventory):
            raise ArchiveError("immutable retail inventory binding is invalid")
        expected_mappings = proof.get("mappings")
        if not isinstance(expected_mappings, list) or len(expected_mappings) != 13:
            raise ArchiveError("immutable retail mapping set is invalid")
        by_path = {item.get("prepared_path"): item for item in expected_mappings
                   if isinstance(item, dict)}
        if set(by_path) != MAPPED_PREPARED_PATHS:
            raise ArchiveError("immutable retail mapping paths are invalid")
        prepared_fd = self.bind_owned_tree(
            self.settings.retail_prepared_root, "fixed prepared retail root")
        try:
            if list(identity(os.fstat(prepared_fd))) != proof.get("prepared_root_identity"):
                raise ArchiveError("prepared retail root identity changed after proof")
            current_manifest = manifest_bound(prepared_fd, "directory")
            self.validate_prepared_manifest(current_manifest)
            if canonical_json(current_manifest) != canonical_json(proof.get("prepared_manifest")):
                raise ArchiveError("prepared retail manifest changed after proof")
            for _, prepared_path in SECOND_COPY_MAPPINGS:
                binding = self.bind_relative_regular(
                    prepared_fd, prepared_path, "prepared recovery mapping")
                try:
                    fingerprint = self.stable_file_fingerprint(
                        binding, "prepared recovery mapping")
                finally:
                    binding.close()
                expected = by_path[prepared_path]
                if (fingerprint["identity"] != expected.get("prepared_identity")
                        or fingerprint["size"] != expected.get("size")
                        or fingerprint["sha256"] != expected.get("sha256")):
                    raise ArchiveError(f"prepared recovery mapping differs: {prepared_path}")
        finally:
            os.close(prepared_fd)

    @staticmethod
    def validate_historical_prepared_metadata_overlay(
            old: dict[str, Any], current: dict[str, Any],
            policy: HistoricalPreparedMetadataOverlayPolicy) -> None:
        """Accept exactly the reviewed removal of prepared-root .DS_Store."""
        if (manifest_canonical_sha256(old) != policy.old_manifest_sha256
                or old.get("tree_sha256") != policy.old_tree_sha256
                or manifest_canonical_sha256(current)
                != policy.new_manifest_sha256
                or current.get("tree_sha256") != policy.new_tree_sha256):
            raise ArchiveError("historical prepared overlay manifest differs")
        old_entries = old.get("entries")
        current_entries = current.get("entries")
        if not isinstance(old_entries, list) or not isinstance(current_entries, list):
            raise ArchiveError("historical prepared overlay inventory is invalid")
        old_rows = {row.get("path"): row for row in old_entries
                    if isinstance(row, dict)}
        current_rows = {row.get("path"): row for row in current_entries
                        if isinstance(row, dict)}
        if (len(old_rows) != len(old_entries)
                or len(current_rows) != len(current_entries)
                or set(old_rows) - set(current_rows) != {policy.removed_path}
                or set(current_rows) - set(old_rows)):
            raise ArchiveError("historical prepared overlay path delta differs")
        removed = old_rows.get(policy.removed_path)
        old_root = old_rows.get(".")
        current_root = current_rows.get(".")
        if not all(isinstance(row, dict)
                   for row in (removed, old_root, current_root)):
            raise ArchiveError("historical prepared overlay reviewed records are absent")
        assert isinstance(removed, dict)
        assert isinstance(old_root, dict)
        assert isinstance(current_root, dict)
        removed_xattrs = dict(policy.removed_xattrs)
        if (sha256_bytes(canonical_json(removed))
                != policy.removed_record_sha256
                or removed.get("kind") != "regular"
                or removed.get("sha256") != policy.removed_content_sha256
                or removed.get("logical_bytes") != policy.removed_logical_bytes
                or removed.get("allocated_bytes")
                != policy.removed_allocated_bytes
                or removed.get("flags") != policy.removed_flags
                or removed.get("xattrs") != removed_xattrs
                or sha256_bytes(canonical_json(old_root))
                != policy.old_root_record_sha256
                or sha256_bytes(canonical_json(current_root))
                != policy.new_root_record_sha256
                or tuple(old_root.get("local_identity", ()))
                != policy.prepared_root_identity
                or tuple(current_root.get("local_identity", ()))
                != policy.prepared_root_identity):
            raise ArchiveError("historical prepared overlay record differs")
        old_root_stable = {key: value for key, value in old_root.items()
                           if key != "mtime_ns"}
        current_root_stable = {key: value for key, value in current_root.items()
                               if key != "mtime_ns"}
        if old_root_stable != current_root_stable:
            raise ArchiveError(
                "historical prepared overlay root changed beyond mtime")
        unchanged_paths = sorted(set(current_rows) - {"."})
        unchanged = [old_rows[path] for path in unchanged_paths]
        if (any(old_rows[path] != current_rows[path]
                for path in unchanged_paths)
                or sha256_bytes(canonical_json(unchanged))
                != policy.unchanged_records_sha256):
            raise ArchiveError("historical prepared overlay common records differ")
        if ((old.get("files"), current.get("files"))
                != (policy.old_file_count, policy.new_file_count)
                or old.get("directories") != policy.directory_count
                or current.get("directories") != policy.directory_count
                or old.get("symlinks") != policy.symlink_count
                or current.get("symlinks") != policy.symlink_count
                or current.get("logical_bytes", 0) - old.get("logical_bytes", 0)
                != policy.logical_bytes_delta
                or current.get("allocated_bytes", 0)
                - old.get("allocated_bytes", 0)
                != policy.allocated_bytes_delta):
            raise ArchiveError("historical prepared overlay totals differ")

    @staticmethod
    def retail_import_prepared_verifier() -> Callable[[int], Any]:
        return load_retail_import_prepared_verifier(
            Path(__file__).with_name("retail_import.py"))

    def verify_retail_import_prepared_fd(self, prepared_fd: int) -> None:
        """Run retail_import's read-only verifier on the already pinned root."""
        previous_fd = os.open(".", directory_open_flags())
        previous_identity = identity(os.fstat(previous_fd))
        try:
            try:
                self.retail_import_prepared_verifier()(prepared_fd)
            except Exception as error:
                raise ArchiveError(
                    f"retail importer rejected historical prepared cache: {error}") \
                    from error
        finally:
            try:
                os.fchdir(previous_fd)
                if identity(os.stat(".")) != previous_identity:
                    raise ArchiveError(
                        "current directory identity changed during retail verification")
            finally:
                os.close(previous_fd)

    def verify_historical_prepared_overlay(
            self, proof: dict[str, Any],
            completed: HistoricalCompletedRetirementPolicy) -> None:
        """Verify the exact metadata-only cache evolution without authorizing writes."""
        policy = self.historical_prepared_overlay_policy(completed)
        if (completed.transaction_id != policy.transaction_id
                or completed.candidate_id != policy.candidate_id
                or completed.external_second_copy_name
                != policy.external_second_copy_name
                or completed.external_second_copy_sha256
                != policy.external_second_copy_sha256
                or completed.prepared_root_identity
                != policy.prepared_root_identity
                or policy.historical_policy_authorization_sha256
                != historical_completed_policy_authorization_sha256(completed)
                or proof.get("transaction_id") != policy.transaction_id
                or proof.get("candidate_id") != policy.candidate_id
                or proof.get("prepared_root")
                != str(self.settings.retail_prepared_root)
                or tuple(proof.get("prepared_root_identity", ()))
                != policy.prepared_root_identity
                or self.second_copy_payload_hash(proof) != proof.get("proof_hash")):
            raise ArchiveError("historical prepared overlay tuple differs")
        old = proof.get("prepared_manifest")
        mappings = proof.get("mappings")
        if not isinstance(old, dict) or not isinstance(mappings, list) \
                or len(mappings) != len(SECOND_COPY_MAPPINGS):
            raise ArchiveError("historical prepared overlay proof is incomplete")
        by_path = {item.get("prepared_path"): item for item in mappings
                   if isinstance(item, dict)}
        if (set(by_path) != MAPPED_PREPARED_PATHS
                or {(item.get("source_path"), item.get("prepared_path"))
                    for item in mappings if isinstance(item, dict)}
                != set(SECOND_COPY_MAPPINGS)):
            raise ArchiveError("historical prepared overlay mappings differ")

        prepared_fd = self.bind_owned_tree(
            self.settings.retail_prepared_root,
            "historical fixed prepared retail root")
        try:
            root_identity = identity(os.fstat(prepared_fd))
            if root_identity != policy.prepared_root_identity:
                raise ArchiveError("historical prepared root identity differs")
            current = manifest_bound(prepared_fd, "directory")
            self.validate_historical_prepared_metadata_overlay(
                old, current, policy)
            self.validate_prepared_manifest(current)
            for _, prepared_path in SECOND_COPY_MAPPINGS:
                binding = self.bind_relative_regular(
                    prepared_fd, prepared_path,
                    "historical prepared recovery mapping")
                try:
                    fingerprint = self.stable_file_fingerprint(
                        binding, "historical prepared recovery mapping")
                finally:
                    binding.close()
                expected = by_path[prepared_path]
                if (fingerprint["identity"] != expected.get("prepared_identity")
                        or fingerprint["size"] != expected.get("size")
                        or fingerprint["sha256"] != expected.get("sha256")):
                    raise ArchiveError(
                        f"historical prepared mapping differs: {prepared_path}")
            self.verify_retail_import_prepared_fd(prepared_fd)
            if identity(os.fstat(prepared_fd)) != root_identity:
                raise ArchiveError(
                    "historical prepared root changed during retail verification")
            after = manifest_bound(prepared_fd, "directory")
            if canonical_json(after) != canonical_json(current):
                raise ArchiveError(
                    "historical prepared cache changed during retail verification")
            self.validate_historical_prepared_metadata_overlay(old, after, policy)
        finally:
            os.close(prepared_fd)

    def verify_bound_payload(self, parent_fd: int, name: str, expected: tuple[int, int],
                             record: dict[str, Any], label: str, *,
                             transaction_fd: int | None = None,
                             require_current_overlay_gate: bool = True,
                             exact: bool = False) -> dict[str, Any]:
        try:
            descriptor = stable_rebind(parent_fd, name, expected, record["source_kind"])
        except (ArchiveError, FileNotFoundError) as error:
            raise ArchiveError(f"{label} is absent or foreign") from error
        try:
            manifest = manifest_bound(descriptor, record["source_kind"])
        finally:
            os.close(descriptor)
        policy = self.source_root_overlay_policy_for_record(record)
        if policy is None:
            matches = canonical_json(record["source_manifest"]) == canonical_json(manifest) \
                if exact else manifests_equal(record["source_manifest"], manifest)
            if not matches:
                raise ArchiveError(f"{label} differs from the source closure proof")
        else:
            if transaction_fd is None:
                raise ArchiveError("source-root overlay verification lacks transaction")
            retirement_overlay_policy = \
                self.retirement_overlay_baseline_policy_for_transaction(
                    transaction_fd, record)
            if retirement_overlay_policy is not None:
                baseline = self.load_retirement_overlay_baseline(transaction_fd)
                if baseline is None:
                    quarantined = self.load_stage(
                        transaction_fd, "source-quarantined")
                    if quarantined is None:
                        raise ArchiveError(
                            "retirement overlay lacks immutable 008")
                    compare_retirement_overlay_baseline(
                        quarantined["source_physical_manifest"], manifest,
                        policy=retirement_overlay_policy,
                        production_authorization_sha256=
                        REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
                else:
                    self.validate_retirement_overlay_baseline_record(
                        transaction_fd, record, baseline,
                        require_current_gate=require_current_overlay_gate)
                    if not self.validate_retirement_pretruncate_source_state(
                            transaction_fd, record, manifest,
                            require_current_gate=require_current_overlay_gate):
                        self.validate_retirement_overlay_partial_manifest(
                            baseline["current_source_manifest"], manifest,
                            retirement_overlay_policy,
                            permit_partial_mtime=True)
                return manifest
            quarantined = self.load_stage(transaction_fd, "source-quarantined")
            if quarantined is None:
                self.validate_source_root_legacy_prefix(transaction_fd, record)
                self.validate_source_root_overlay_manifest(record, manifest)
            else:
                self.validate_source_root_overlay_binding(
                    transaction_fd, record, quarantined, manifest,
                    require_current_gate=require_current_overlay_gate)
        return manifest

    def open_transaction(
            self, transaction_id: str, *, exclusive: bool,
            recover_partials: bool) -> tuple[int, int, int, dict[str, Any]]:
        require_leaf(transaction_id, "transaction id")
        lock_fd = self.acquire_archiver_lock(exclusive=exclusive)
        try:
            queue_fd = self.queue_fd(create=False)
            try:
                transaction_fd = os.open(
                    transaction_id, directory_open_flags(), dir_fd=queue_fd)
                try:
                    if recover_partials:
                        if not exclusive:
                            raise ArchiveError(
                                "record recovery requires the exclusive archiver lock")
                        self.recover_record_partials(
                            transaction_fd,
                            allowed=lambda name: name in TRANSACTION_RECORD_NAMES)
                    record = self.candidate_record(transaction_fd)
                    if record["transaction_id"] != transaction_id:
                        raise ArchiveError(
                            "transaction id differs from candidate record")
                    return lock_fd, queue_fd, transaction_fd, record
                except BaseException:
                    os.close(transaction_fd)
                    raise
            except BaseException:
                os.close(queue_fd)
                raise
        except BaseException:
            os.close(lock_fd)
            raise

    def open_stage(self, roots: ExternalRoots, started: dict[str, Any], kind: str) -> int:
        expected = tuple(started["stage_identity"])
        descriptor = stable_rebind(
            roots.category_fd, started["stage_name"], expected, "directory")
        try:
            for name in os.listdir(descriptor):
                if name == started["payload_name"]:
                    continue
                if COPY_RESIDUE_RE.fullmatch(name) is None:
                    raise ArchiveError("staging namespace contains unknown/tampered residue")
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                expected_kind = stat.S_ISDIR(info.st_mode) if kind == "directory" \
                    else stat.S_ISREG(info.st_mode)
                if not expected_kind or info.st_uid != os.geteuid() \
                        or (kind == "regular" and info.st_nlink != 1):
                    raise ArchiveError("copy residue is foreign or unsafe")
                residue = open_leaf(descriptor, name, kind)
                try:
                    linked = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    if identity(os.fstat(residue)) != identity(linked):
                        raise ArchiveError("copy residue identity changed")
                finally:
                    os.close(residue)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def ensure_copy_started(self, transaction_fd: int, record: dict[str, Any],
                            volume: VolumeBinding, roots: ExternalRoots) -> dict[str, Any]:
        existing = self.load_stage(transaction_fd, "copy-started")
        if existing is not None:
            return existing
        stage_name = f".stage-{record['transaction_id']}-{uuid.uuid4().hex}"
        self.require_volume(volume)
        stage_fd = self.create_private_directory_exclusive(
            roots.category_fd, stage_name, volume)
        try:
            stage_identity = list(identity(os.fstat(stage_fd)))
        finally:
            os.close(stage_fd)
        started = {"transaction_id": record["transaction_id"], "stage_name": stage_name,
                   "stage_identity": stage_identity, "payload_name": "payload",
                   "final_name": f"{record['transaction_id']}-{record['source_name']}"}
        self.stage(transaction_fd, "copy-started", started)
        self.hook("after_copy_started")
        return self.load_stage(transaction_fd, "copy-started") or started

    def independent_staging_manifest(
            self, transaction_id: str, *,
            inherited_lock_fd: int | None = None) -> dict[str, Any]:
        """Verify staging under a shared lock or the drain's inherited EX lock."""
        require_leaf(transaction_id, "transaction id")
        if inherited_lock_fd is None:
            if self.settings.production:
                raise ArchiveError(
                    "production staging verifier requires inherited archiver lock")
            lock_fd = self.acquire_archiver_lock(exclusive=False)
        else:
            lock_fd = os.dup(inherited_lock_fd)
            try:
                self.validate_archiver_lock_fd(lock_fd)
            except BaseException:
                os.close(lock_fd)
                raise
        try:
            queue_fd = self.queue_fd(create=False)
            try:
                transaction_fd = os.open(
                    transaction_id, directory_open_flags(), dir_fd=queue_fd)
                try:
                    record = self.candidate_record(transaction_fd)
                    started = self.load_stage(transaction_fd, "copy-started")
                    if started is None:
                        raise ArchiveError("staging verifier lacks copy-started")
                    volume = self.bind_volume()
                    try:
                        roots = self.external_roots(volume, record["category"])
                        try:
                            stage_fd = self.open_stage(
                                roots, started, record["source_kind"])
                            try:
                                payload_fd = open_leaf(
                                    stage_fd, started["payload_name"],
                                    record["source_kind"])
                                try:
                                    return manifest_bound(
                                        payload_fd, record["source_kind"])
                                finally:
                                    os.close(payload_fd)
                            finally:
                                os.close(stage_fd)
                        finally:
                            roots.close()
                    finally:
                        volume.close()
                finally:
                    os.close(transaction_fd)
            finally:
                os.close(queue_fd)
        finally:
            os.close(lock_fd)

    def run_independent_verifier(
            self, transaction_id: str, lock_fd: int) -> dict[str, Any]:
        if not self.settings.production:
            return self.independent_staging_manifest(
                transaction_id, inherited_lock_fd=lock_fd)
        completed = subprocess.run(
            (sys.executable, str(Path(__file__).resolve()), "verify", "--transaction",
             transaction_id, "--staging", "--archiver-lock-fd", str(lock_fd)),
            text=True, capture_output=True, check=False, pass_fds=(lock_fd,))
        if completed.returncode != 0:
            raise ArchiveError(f"independent staging verifier failed: {completed.stderr.strip()}")
        try:
            value = strict_json_loads(completed.stdout)
        except (json.JSONDecodeError, ValueError) as error:
            raise ArchiveError("independent staging verifier returned invalid JSON") from error
        if not isinstance(value, dict):
            raise ArchiveError("independent staging verifier returned a non-object")
        return value

    def ensure_copied(self, transaction_fd: int, record: dict[str, Any], source: SourceBinding,
                      volume: VolumeBinding, roots: ExternalRoots,
                      started: dict[str, Any]) -> dict[str, Any]:
        existing = self.load_stage(transaction_fd, "copy-complete")
        stage_fd = self.open_stage(roots, started, record["source_kind"])
        try:
            if existing is None:
                try:
                    partial = os.stat(started["payload_name"], dir_fd=stage_fd,
                                      follow_symlinks=False)
                except FileNotFoundError:
                    partial = None
                if partial is not None:
                    payload_fd = stable_rebind(
                        stage_fd, started["payload_name"], identity(partial),
                        record["source_kind"])
                    try:
                        copied_manifest = manifest_bound(payload_fd, record["source_kind"])
                        payload_identity = list(identity(os.fstat(payload_fd)))
                    finally:
                        os.close(payload_fd)
                    try:
                        policy = self.provenance_overlay_policy_for_record(record)
                        if policy is None:
                            if not manifests_equal(record["source_manifest"], copied_manifest):
                                raise ArchiveError("partial copy differs from source")
                        else:
                            self.provenance_overlay_proof_for(
                                record, copied_manifest, policy)
                        partial_valid = True
                    except ArchiveError:
                        partial_valid = False
                    if not partial_valid:
                        residue_name = f".copy-residue-{uuid.uuid4().hex}"
                        self.require_volume(volume)
                        self.platform.rename_exclusive(
                            stage_fd, started["payload_name"], stage_fd, residue_name)
                        moved = os.stat(residue_name, dir_fd=stage_fd, follow_symlinks=False)
                        if identity(moved) != tuple(payload_identity):
                            raise ArchiveError("partial-copy residue identity changed")
                        self.fsync(stage_fd, "partial-copy residue quarantine", volume)
                        self.hook("after_partial_copy_quarantine", residue_name=residue_name)
                        self.require_volume(volume)
                        payload_fd = self.copy_source_to(
                            source, stage_fd, started["payload_name"], volume)
                        try:
                            self.hook("before_copy_manifest", payload_fd=payload_fd)
                            copied_manifest = manifest_bound(payload_fd, record["source_kind"])
                            payload_identity = list(identity(os.fstat(payload_fd)))
                        finally:
                            os.close(payload_fd)
                else:
                    self.require_volume(volume)
                    payload_fd = self.copy_source_to(
                        source, stage_fd, started["payload_name"], volume)
                    try:
                        self.hook("before_copy_manifest", payload_fd=payload_fd)
                        copied_manifest = manifest_bound(payload_fd, record["source_kind"])
                        payload_identity = list(identity(os.fstat(payload_fd)))
                    finally:
                        os.close(payload_fd)
                self.fsync(stage_fd, "completed staging directory", volume)
                self.require_volume(volume)
                self.hook("after_copy_before_record")
                policy = self.provenance_overlay_policy_for_record(record)
                if policy is None:
                    if not manifests_equal(record["source_manifest"], copied_manifest):
                        raise ArchiveError("completed copy differs from source")
                    binding: dict[str, Any] = {
                        "destination_manifest": copied_manifest}
                else:
                    overlay_proof = self.provenance_overlay_proof_for(
                        record, copied_manifest, policy)
                    binding = self.overlay_record_binding(
                        record["source_manifest"], copied_manifest, overlay_proof)
                payload = {"transaction_id": record["transaction_id"],
                           "payload_identity": payload_identity, **binding}
                self.stage(transaction_fd, "copy-complete", payload)
                self.hook("after_copy_complete")
                return self.load_stage(transaction_fd, "copy-complete") or payload
            payload_fd = stable_rebind(stage_fd, started["payload_name"],
                                       tuple(existing["payload_identity"]), record["source_kind"])
            try:
                current = manifest_bound(payload_fd, record["source_kind"])
            finally:
                os.close(payload_fd)
            policy = self.provenance_overlay_policy_for_record(record)
            if policy is None:
                if not manifests_equal(existing["destination_manifest"], current):
                    raise ArchiveError("copy-complete payload changed")
                return existing
            if canonical_json(existing["destination_manifest"]) != canonical_json(current):
                raise ArchiveError("overlay copy-complete physical manifest changed")
            stored_proof = existing.get("provenance_overlay_proof")
            proof = self.validate_copy_overlay(
                record, current, stored_proof,
                allow_legacy_002=existing if stored_proof is None else None)
            assert proof is not None
            binding = self.overlay_record_binding(
                record["source_manifest"], current, proof)
            if stored_proof is not None:
                self.validate_overlay_record_binding(record, existing, current)
                return existing
            return {**existing, **binding}
        finally:
            os.close(stage_fd)

    def ensure_verified(self, transaction_fd: int, record: dict[str, Any],
                        copied: dict[str, Any], lock_fd: int) -> dict[str, Any]:
        existing = self.load_stage(transaction_fd, "verified")
        policy = self.provenance_overlay_policy_for_record(record)
        if existing is not None:
            if policy is None:
                if not manifests_equal(
                        existing["destination_manifest"], copied["destination_manifest"]):
                    raise ArchiveError("verified record differs from copy-complete")
            else:
                if canonical_json(existing["destination_manifest"]) != canonical_json(
                        copied["destination_manifest"]):
                    raise ArchiveError("verified physical manifest differs from copy-complete")
                self.validate_overlay_record_binding(
                    record, existing, existing["destination_manifest"])
                self.validate_overlay_record_binding(
                    record, copied, copied["destination_manifest"])
            return existing
        verified_manifest = self.run_independent_verifier(
            record["transaction_id"], lock_fd)
        if policy is None:
            if not manifests_equal(record["source_manifest"], verified_manifest):
                raise ArchiveError("independent staging manifest differs from source")
            payload = {"transaction_id": record["transaction_id"],
                       "destination_manifest": verified_manifest}
        else:
            if canonical_json(copied["destination_manifest"]) != canonical_json(
                    verified_manifest):
                raise ArchiveError("independent overlay manifest differs from immutable 002")
            proof = self.validate_immutable_copy_record(
                transaction_fd, record, verified_manifest)
            assert proof is not None
            if not provenance_overlay_proofs_equal(
                    copied.get("provenance_overlay_proof"), proof):
                raise ArchiveError("independent overlay proof differs from copy-complete")
            payload = {"transaction_id": record["transaction_id"],
                       **self.overlay_record_binding(
                           record["source_manifest"], verified_manifest, proof)}
        self.stage(transaction_fd, "verified", payload)
        self.hook("after_verified")
        return self.load_stage(transaction_fd, "verified") or payload

    def ensure_published(self, transaction_fd: int, record: dict[str, Any],
                         volume: VolumeBinding, roots: ExternalRoots,
                         started: dict[str, Any], verified: dict[str, Any]) -> dict[str, Any]:
        existing = self.load_stage(transaction_fd, "published")
        if existing is None:
            stage_fd = self.open_stage(roots, started, record["source_kind"])
            try:
                payload_exists = True
                try:
                    payload_info = os.stat(started["payload_name"], dir_fd=stage_fd,
                                           follow_symlinks=False)
                except FileNotFoundError:
                    payload_exists = False
                    payload_info = None
                try:
                    final_info = os.stat(started["final_name"], dir_fd=roots.category_fd,
                                         follow_symlinks=False)
                except FileNotFoundError:
                    final_info = None
                if payload_exists and final_info is None:
                    self.require_volume(volume)
                    self.platform.rename_exclusive(stage_fd, started["payload_name"],
                                                   roots.category_fd, started["final_name"])
                    final_info = os.stat(started["final_name"], dir_fd=roots.category_fd,
                                         follow_symlinks=False)
                    self.fsync(roots.category_fd, "published category", volume)
                    publication_observed_unix = self.platform.now_unix()
                    self.hook("after_publish_rename")
                elif payload_exists or final_info is None:
                    raise ArchiveError("publish recovery found an ambiguous collision")
                else:
                    publication_observed_unix = self.platform.now_unix()
                assert final_info is not None
                final_fd = stable_rebind(roots.category_fd, started["final_name"],
                                         identity(final_info), record["source_kind"])
                try:
                    manifest = manifest_bound(final_fd, record["source_kind"])
                    final_identity = list(identity(os.fstat(final_fd)))
                    self.fsync(final_fd, "rebound published payload", volume)
                finally:
                    os.close(final_fd)
                policy = self.provenance_overlay_policy_for_record(record)
                if policy is None:
                    if not manifests_equal(verified["destination_manifest"], manifest):
                        raise ArchiveError("published payload differs from verified staging")
                    publication_binding: dict[str, Any] = {
                        "destination_manifest": manifest}
                else:
                    if canonical_json(verified["destination_manifest"]) != canonical_json(manifest):
                        raise ArchiveError(
                            "published physical manifest differs from immutable verified staging")
                    self.validate_overlay_record_binding(record, verified, manifest)
                    proof = self.validate_immutable_copy_record(
                        transaction_fd, record, manifest)
                    assert proof is not None
                    publication_binding = self.overlay_record_binding(
                        record["source_manifest"], manifest, proof)
                for residue_name in sorted(os.listdir(stage_fd)):
                    if COPY_RESIDUE_RE.fullmatch(residue_name) is None:
                        raise ArchiveError("published staging wrapper contains foreign residue")
                    residue_info = os.stat(
                        residue_name, dir_fd=stage_fd, follow_symlinks=False)
                    residue_identity = identity(residue_info)
                    residue_probe = stable_rebind(
                        stage_fd, residue_name, residue_identity,
                        record["source_kind"])
                    try:
                        residue_admitted = manifest_bound(
                            residue_probe, record["source_kind"])
                        residue_rows = self.preflight_retirement_tree(
                            residue_probe, record["source_kind"], residue_admitted)
                    finally:
                        os.close(residue_probe)
                    residue_fd = stable_rebind(
                        stage_fd, residue_name, residue_identity,
                        record["source_kind"])
                    try:
                        residue_manifest = self.retire_pinned_payload(
                            residue_fd, record["source_kind"], volume, residue_rows,
                            parent_fd=stage_fd, name=residue_name,
                            retirement_state={"payload_write_started": False},
                            final_retirement_boundary=lambda: self.require_volume(volume),
                            boundary_hook=False)
                    finally:
                        os.close(residue_fd)
                    if residue_manifest["logical_bytes"] != 0:
                        raise ArchiveError("partial-copy residue retained nonzero data")
                stage_identity = tuple(started["stage_identity"])
                rebound = os.stat(started["stage_name"], dir_fd=roots.category_fd,
                                  follow_symlinks=False)
                if identity(rebound) != stage_identity:
                    raise ArchiveError("staging wrapper changed after publication")
                self.fsync(stage_fd, "retained staging tombstone", volume)
                payload = {"transaction_id": record["transaction_id"],
                           "final_name": started["final_name"],
                           "final_identity": final_identity,
                           "published_utc": utc_from_unix(publication_observed_unix),
                           **publication_binding}
                self.stage(transaction_fd, "published", payload)
                self.hook("after_published_record")
                return self.load_stage(transaction_fd, "published") or payload
            finally:
                os.close(stage_fd)
        final_fd = stable_rebind(roots.category_fd, existing["final_name"],
                                 tuple(existing["final_identity"]), record["source_kind"])
        try:
            manifest = manifest_bound(final_fd, record["source_kind"])
        finally:
            os.close(final_fd)
        policy = self.provenance_overlay_policy_for_record(record)
        if policy is None:
            if not manifests_equal(existing["destination_manifest"], manifest):
                raise ArchiveError("published payload changed")
        else:
            self.validate_immutable_copy_record(transaction_fd, record, manifest)
            if canonical_json(existing["destination_manifest"]) != canonical_json(
                    verified["destination_manifest"]):
                raise ArchiveError("published overlay record differs from immutable 003")
            self.validate_overlay_record_binding(
                record, verified, verified["destination_manifest"])
            if canonical_json(existing["destination_manifest"]) != canonical_json(manifest):
                raise ArchiveError("published overlay physical manifest changed")
            self.validate_overlay_record_binding(record, existing, manifest)
        return existing

    def read_gate_file(self, parent_fd: int, name: str, *, modes: set[int]) -> tuple[bytes, str]:
        descriptor = os.open(name, os.O_RDONLY | O_NOFOLLOW, dir_fd=parent_fd)
        try:
            opened = os.fstat(descriptor)
            linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                    or stat.S_IMODE(opened.st_mode) not in modes or opened.st_nlink != 1
                    or identity(opened) != identity(linked)):
                raise ArchiveError(f"gate evidence is foreign or unsafe: {name}")
            chunks = []
            while block := os.read(descriptor, 1024 * 1024):
                chunks.append(block)
            raw = b"".join(chunks)
            return raw, sha256_bytes(raw)
        finally:
            os.close(descriptor)

    def parse_full_gate_stamp(self, raw: bytes) -> dict[str, str]:
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError as error:
            raise ArchiveError("full-gate stamp is not ASCII") from error
        rows = text.splitlines()
        if not rows or text != "\n".join(rows) + "\n":
            raise ArchiveError("full-gate stamp is not canonical line data")
        result: dict[str, str] = {}
        for row in rows:
            if row.count("=") != 1:
                raise ArchiveError("full-gate stamp row is malformed")
            key, value = row.split("=", 1)
            if key in result or not key or not value:
                raise ArchiveError("full-gate stamp has duplicate/empty fields")
            result[key] = value
        required = {"source_sha256", "app_uuid", "bundle_sha256", "openal_provider",
                    "openal_sha256", "platform", "minos", "shader_cache"}
        if set(result) != required or result["platform"] != "IOS" \
                or result["minos"] != "16.4" \
                or result["shader_cache"] != "forced-off":
            raise ArchiveError("full-gate stamp contract is incomplete")
        for key in ("source_sha256", "bundle_sha256", "openal_sha256"):
            if re.fullmatch(r"[0-9a-f]{64}", result[key]) is None:
                raise ArchiveError(f"full-gate stamp digest is invalid: {key}")
        return result

    def valid_gate_state(self, value: Any) -> bool:
        if not isinstance(value, dict) or set(value) != {
                "head", "status_sha256", "diff_binary_head_sha256", "untracked"}:
            return False
        if not isinstance(value["head"], str) \
                or re.fullmatch(r"[0-9a-f]{40,64}", value["head"]) is None:
            return False
        if any(re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
               for key in ("status_sha256", "diff_binary_head_sha256")):
            return False
        if not isinstance(value["untracked"], list):
            return False
        paths: list[str] = []
        for item in value["untracked"]:
            if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                return False
            path = item["path"]
            if (not isinstance(path, str) or not path
                    or Path(path).is_absolute() or Path(path) != Path(os.path.normpath(path))
                    or ".." in Path(path).parts
                    or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None):
                return False
            paths.append(path)
        return paths == sorted(set(paths))

    def matching_full_gate_receipt(self) -> dict[str, Any]:
        current_hash = self.platform.gate_input_hash(self.settings)
        current_state = self.platform.gate_state(self.settings)
        if not self.valid_gate_state(current_state):
            raise ArchiveError("current repository state is invalid")
        now = self.platform.now_unix()
        if not finite_timestamp(now) or now < 0:
            raise ArchiveError("current gate clock is invalid")
        gate_fd = open_absolute_directory(self.settings.gate_log_root, "gate receipt root")
        stamp_parent = open_absolute_directory(self.settings.full_gate_stamp.parent,
                                               "full-gate stamp parent")
        try:
            stamp_raw, stamp_sha = self.read_gate_file(
                stamp_parent, self.settings.full_gate_stamp.name, modes={0o600, 0o644})
            stamp = self.parse_full_gate_stamp(stamp_raw)
            if stamp["source_sha256"] != current_hash:
                raise ArchiveError("current gate input hash does not match the full-gate stamp")
            matches: list[dict[str, Any]] = []
            for name in sorted(os.listdir(gate_fd)):
                if not GATE_RECEIPT_RE.fullmatch(name):
                    continue
                raw, receipt_sha = self.read_gate_file(gate_fd, name, modes={0o600})
                try:
                    receipt = strict_json_loads(raw.decode("ascii"))
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                    raise ArchiveError(f"gate receipt is malformed: {name}") from error
                if not isinstance(receipt, dict) or canonical_json(receipt) != raw:
                    raise ArchiveError(f"gate receipt is not canonical: {name}")
                # Canonical historical receipts are immutable evidence, not a
                # reason to brick a newer policy.  They remain readable but are
                # ineligible unless both schema and the complete current shape
                # match.  Malformed, noncanonical or unsafe files were already
                # rejected above and must never be silently skipped.
                if set(receipt) != CURRENT_GATE_RECEIPT_FIELDS \
                        or receipt.get("schema") != CURRENT_GATE_RECEIPT_SCHEMA:
                    continue
                if receipt.get("gate") != "full" \
                        or type(receipt.get("exit_code")) is not int \
                        or receipt.get("exit_code") != 0:
                    continue
                started = receipt.get("started_unix")
                ended = receipt.get("ended_unix")
                if not finite_timestamp(started) or not finite_timestamp(ended) \
                        or started < 0 or ended < 0 or started > ended or ended > now + 5 \
                        or now - ended > self.settings.gate_max_age_seconds:
                    continue
                if receipt.get("before") != receipt.get("after") \
                        or not self.valid_gate_state(receipt.get("before")) \
                        or receipt.get("after") != current_state \
                        or receipt.get("log_identity_mismatch") is not False:
                    continue
                underlying = receipt.get("underlying_stamp")
                if not isinstance(underlying, dict) \
                        or underlying != {"path": str(self.settings.full_gate_stamp),
                                          "sha256": stamp_sha}:
                    continue
                expected_log = self.settings.gate_log_root / name[:-5]
                expected_log = expected_log.with_suffix(".log")
                if receipt.get("log_path") != str(expected_log):
                    continue
                expected_detail = self.settings.gate_log_root / f"detail-{name[:-5]}"
                if receipt.get("detail_dir") != str(expected_detail):
                    continue
                log_raw, log_sha = self.read_gate_file(
                    gate_fd, expected_log.name, modes={0o600})
                del log_raw
                if receipt.get("log_sha256") != log_sha:
                    continue
                matches.append({
                    "schema": "openxray.archive-gate-proof.v1",
                    "review_policy_version": REVIEW_POLICY_VERSION,
                    "receipt_name": name, "receipt_sha256": receipt_sha,
                    "gate": "full", "gate_ended_unix": ended,
                    "gate_source_sha256": current_hash,
                    "gate_stamp_sha256": stamp_sha, "gate_log_sha256": log_sha,
                })
            if not matches:
                raise ArchiveError("no fresh matching full-gate receipt")
            return max(matches, key=lambda value: value["gate_ended_unix"])
        finally:
            os.close(stamp_parent)
            os.close(gate_fd)

    def matching_clean_full_gate_receipt(self) -> dict[str, Any]:
        """Return a fresh full proof for an exactly clean current worktree."""
        proof = self.matching_full_gate_receipt()
        current = self.platform.gate_state(self.settings)
        empty = sha256_bytes(b"")
        if (not self.valid_gate_state(current)
                or current.get("status_sha256") != empty
                or current.get("diff_binary_head_sha256") != empty
                or current.get("untracked") != []):
            raise ArchiveError(
                "historical verification requires a clean current worktree")
        self.validate_full_gate_proof(proof, require_fresh=True)
        return proof

    def validate_full_gate_proof(
            self, proof: Any, *, require_fresh: bool) -> dict[str, Any]:
        """Reopen and validate one immutable full-gate proof against now.

        Freshness is deliberately optional: an old proof sealed into a durable
        stage remains valid evidence when its receipt, log, stamp, source hash
        and exact worktree state still match.  A separate fresh equivalent
        proof authorizes the current resume.
        """
        required = {
            "schema", "review_policy_version", "receipt_name", "receipt_sha256",
            "gate", "gate_ended_unix", "gate_source_sha256",
            "gate_stamp_sha256", "gate_log_sha256",
        }
        if not isinstance(proof, dict) or set(proof) != required \
                or proof.get("schema") != "openxray.archive-gate-proof.v1" \
                or proof.get("review_policy_version") != REVIEW_POLICY_VERSION \
                or proof.get("gate") != "full" \
                or GATE_RECEIPT_RE.fullmatch(proof.get("receipt_name", "")) is None:
            raise ArchiveError("stored full-gate proof shape is invalid")
        current_hash = self.platform.gate_input_hash(self.settings)
        current_state = self.platform.gate_state(self.settings)
        if not self.valid_gate_state(current_state):
            raise ArchiveError("current repository state is invalid")
        now = self.platform.now_unix()
        if not finite_timestamp(now) or now < 0:
            raise ArchiveError("current gate clock is invalid")
        gate_fd = open_absolute_directory(self.settings.gate_log_root, "gate receipt root")
        stamp_parent = open_absolute_directory(
            self.settings.full_gate_stamp.parent, "full-gate stamp parent")
        try:
            stamp_raw, stamp_sha = self.read_gate_file(
                stamp_parent, self.settings.full_gate_stamp.name, modes={0o600, 0o644})
            stamp = self.parse_full_gate_stamp(stamp_raw)
            if stamp["source_sha256"] != current_hash:
                raise ArchiveError("stored gate proof source no longer matches current inputs")
            name = proof["receipt_name"]
            raw, receipt_sha = self.read_gate_file(gate_fd, name, modes={0o600})
            try:
                receipt = strict_json_loads(raw.decode("ascii"))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                raise ArchiveError("stored gate receipt is malformed") from error
            if (not isinstance(receipt, dict) or canonical_json(receipt) != raw
                    or set(receipt) != CURRENT_GATE_RECEIPT_FIELDS
                    or receipt.get("schema") != CURRENT_GATE_RECEIPT_SCHEMA
                    or receipt.get("gate") != "full"
                    or type(receipt.get("exit_code")) is not int
                    or receipt.get("exit_code") != 0):
                raise ArchiveError("stored gate receipt shape/result is invalid")
            started = receipt.get("started_unix")
            ended = receipt.get("ended_unix")
            if (not finite_timestamp(started) or not finite_timestamp(ended)
                    or started < 0 or ended < 0 or started > ended or ended > now + 5
                    or (require_fresh
                        and now - ended > self.settings.gate_max_age_seconds)):
                raise ArchiveError("stored gate receipt time is invalid or stale")
            if (receipt.get("before") != receipt.get("after")
                    or not self.valid_gate_state(receipt.get("before"))
                    or receipt.get("after") != current_state
                    or receipt.get("log_identity_mismatch") is not False):
                raise ArchiveError("stored gate receipt does not match the current worktree")
            underlying = receipt.get("underlying_stamp")
            if underlying != {"path": str(self.settings.full_gate_stamp),
                              "sha256": stamp_sha}:
                raise ArchiveError("stored gate receipt stamp binding differs")
            expected_log = (self.settings.gate_log_root / name[:-5]).with_suffix(".log")
            expected_detail = self.settings.gate_log_root / f"detail-{name[:-5]}"
            if (receipt.get("log_path") != str(expected_log)
                    or receipt.get("detail_dir") != str(expected_detail)):
                raise ArchiveError("stored gate receipt path binding differs")
            _, log_sha = self.read_gate_file(gate_fd, expected_log.name, modes={0o600})
            if receipt.get("log_sha256") != log_sha:
                raise ArchiveError("stored gate log digest differs")
            expected = {
                "schema": "openxray.archive-gate-proof.v1",
                "review_policy_version": REVIEW_POLICY_VERSION,
                "receipt_name": name, "receipt_sha256": receipt_sha,
                "gate": "full", "gate_ended_unix": ended,
                "gate_source_sha256": current_hash,
                "gate_stamp_sha256": stamp_sha, "gate_log_sha256": log_sha,
            }
            if canonical_json(expected) != canonical_json(proof):
                raise ArchiveError("stored full-gate proof bytes do not match evidence")
            return expected
        finally:
            os.close(stamp_parent)
            os.close(gate_fd)

    def validate_reviewed_historical_gate_proof(
            self, proof: Any) -> dict[str, Any]:
        """Validate immutable gate A without comparing it to current code.

        This validator is used only after the hard-coded 008/009 transaction
        digests select the reviewed A-to-B recovery.  Receipt and log bytes,
        canonical shape, timestamps and the historical stamp hash remain
        mandatory; the replaceable current stamp/worktree are deliberately not
        treated as evidence for historical A.
        """
        required = {
            "schema", "review_policy_version", "receipt_name", "receipt_sha256",
            "gate", "gate_ended_unix", "gate_source_sha256",
            "gate_stamp_sha256", "gate_log_sha256",
        }
        if (not isinstance(proof, dict) or set(proof) != required
                or proof.get("schema") != "openxray.archive-gate-proof.v1"
                or proof.get("review_policy_version") != REVIEW_POLICY_VERSION
                or proof.get("gate") != "full"
                or re.fullmatch(r"[0-9a-f]{64}",
                                proof.get("gate_source_sha256", "")) is None
                or re.fullmatch(r"[0-9a-f]{64}",
                                proof.get("gate_stamp_sha256", "")) is None
                or GATE_RECEIPT_RE.fullmatch(
                    proof.get("receipt_name", "")) is None):
            raise ArchiveError("historical full-gate proof shape is invalid")
        gate_fd = open_absolute_directory(
            self.settings.gate_log_root, "historical gate receipt root")
        try:
            name = proof["receipt_name"]
            raw, receipt_sha = self.read_gate_file(gate_fd, name, modes={0o600})
            try:
                receipt = strict_json_loads(raw.decode("ascii"))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                raise ArchiveError(
                    "historical gate receipt is malformed") from error
            if (not isinstance(receipt, dict) or canonical_json(receipt) != raw
                    or set(receipt) != CURRENT_GATE_RECEIPT_FIELDS
                    or receipt.get("schema") != CURRENT_GATE_RECEIPT_SCHEMA
                    or receipt.get("gate") != "full"
                    or type(receipt.get("exit_code")) is not int
                    or receipt.get("exit_code") != 0
                    or receipt.get("log_identity_mismatch") is not False):
                raise ArchiveError(
                    "historical gate receipt shape/result is invalid")
            started = receipt.get("started_unix")
            ended = receipt.get("ended_unix")
            if (not finite_timestamp(started) or not finite_timestamp(ended)
                    or started < 0 or ended < 0 or started > ended
                    or receipt.get("before") != receipt.get("after")
                    or not self.valid_gate_state(receipt.get("before"))):
                raise ArchiveError("historical gate receipt evidence is invalid")
            underlying = receipt.get("underlying_stamp")
            if underlying != {
                    "path": str(self.settings.full_gate_stamp),
                    "sha256": proof["gate_stamp_sha256"]}:
                raise ArchiveError("historical gate stamp binding differs")
            expected_log = (
                self.settings.gate_log_root / name[:-5]).with_suffix(".log")
            expected_detail = self.settings.gate_log_root / f"detail-{name[:-5]}"
            if (receipt.get("log_path") != str(expected_log)
                    or receipt.get("detail_dir") != str(expected_detail)):
                raise ArchiveError("historical gate receipt path binding differs")
            _, log_sha = self.read_gate_file(
                gate_fd, expected_log.name, modes={0o600})
            expected = {
                "schema": "openxray.archive-gate-proof.v1",
                "review_policy_version": REVIEW_POLICY_VERSION,
                "receipt_name": name, "receipt_sha256": receipt_sha,
                "gate": "full", "gate_ended_unix": ended,
                "gate_source_sha256": proof["gate_source_sha256"],
                "gate_stamp_sha256": underlying["sha256"],
                "gate_log_sha256": log_sha,
            }
            if (receipt.get("log_sha256") != log_sha
                    or canonical_json(expected) != canonical_json(proof)):
                raise ArchiveError(
                    "historical full-gate proof bytes do not match evidence")
            return expected
        finally:
            os.close(gate_fd)

    @staticmethod
    def require_equivalent_gate_proofs(
            immutable: dict[str, Any], current: dict[str, Any]) -> None:
        semantic_keys = (
            "schema", "review_policy_version", "gate",
            "gate_source_sha256", "gate_stamp_sha256",
        )
        if any(immutable.get(key) != current.get(key) for key in semantic_keys):
            raise ArchiveError("full-gate proofs are not semantically equivalent")

    def transfer_manifest_payload(self, record: dict[str, Any],
                                  published: dict[str, Any], volume: VolumeBinding,
                                  gate_proof: dict[str, Any]) -> dict[str, Any]:
        destination = (Path(volume.attrs["mountpoint"]) / ARCHIVE_ROOT
                       / CATEGORY_ROOTS[record["category"]] / published["final_name"])
        payload = {"schema": SCHEMA, "transaction_id": record["transaction_id"],
                   "allowlist_version": record["allowlist_version"],
                   "category": record["category"], "source": record["source"],
                   "destination": str(destination),
                   "destination_name": published["final_name"],
                   "volume": volume.attrs, "started_utc": record["created_utc"],
                   "finished_utc": published["published_utc"],
                   "data_class": record["data_class"],
                   "deletion_rule": record["deletion_rule"],
                   "closure_proof_hash": gate_proof["receipt_sha256"],
                   "gate_proof": gate_proof,
                   "source_tree_sha256": record["source_tree_sha256"],
                   "source_manifest": record["source_manifest"],
                   "destination_manifest": published["destination_manifest"],
                   "verification": "PASS"}
        if published.get("provenance_overlay_proof") is not None:
            for key in (
                    "source_manifest_sha256", "destination_manifest_sha256",
                    "provenance_overlay_proof", "provenance_overlay_proof_sha256"):
                payload[key] = published[key]
        return payload

    def ensure_external_manifest(self, transaction_fd: int, record: dict[str, Any],
                                 volume: VolumeBinding, roots: ExternalRoots,
                                 published: dict[str, Any],
                                 gate_proof: dict[str, Any]) -> dict[str, Any]:
        local = self.load_stage(transaction_fd, "manifest-published")
        payload = self.transfer_manifest_payload(record, published, volume, gate_proof)
        name = f"{record['transaction_id']}.json"
        self.require_volume(volume)
        digest = self.write_record(roots.manifests_fd, name, payload, volume)
        self.require_volume(volume)
        self.fsync(roots.manifests_fd, "external transfer manifest root", volume)
        self.require_volume(volume)
        external = self.read_record(roots.manifests_fd, name)
        if sha256_bytes(canonical_json(external)) != digest:
            raise ArchiveError("external transfer manifest digest changed")
        self.validate_external_overlay_binding(record, external, published)
        if local is None:
            self.hook("after_external_manifest")
            stage_payload = {"transaction_id": record["transaction_id"],
                             "external_manifest": name, "external_manifest_sha256": digest}
            self.stage(transaction_fd, "manifest-published", stage_payload)
            self.hook("after_manifest_record")
            return self.load_stage(transaction_fd, "manifest-published") or stage_payload
        if local["external_manifest"] != name or local["external_manifest_sha256"] != digest:
            raise ArchiveError("local and external transfer manifest records differ")
        return local

    def validate_legacy_external_manifest_005(
            self, transaction_fd: int, record: dict[str, Any],
            roots: ExternalRoots, published: dict[str, Any]) -> dict[str, Any]:
        if self.source_root_overlay_policy_for_record(record) is None:
            raise ArchiveError("legacy 005 validation is forbidden")
        self.validate_source_root_legacy_prefix(transaction_fd, record)
        local = self.load_stage(transaction_fd, "manifest-published")
        if local is None or canonical_json(local) != canonical_json(
                SOURCE_ROOT_OVERLAY_LEGACY_005):
            raise ArchiveError("legacy 005 contents changed")
        external = self.read_record(roots.manifests_fd, local["external_manifest"])
        digest = sha256_bytes(canonical_json(external))
        if (digest != local["external_manifest_sha256"]
                or external.get("transaction_id") != record["transaction_id"]
                or external.get("verification") != "PASS"):
            raise ArchiveError("legacy 005 external manifest changed")
        self.validate_external_overlay_binding(record, external, published)
        return local

    def open_quarantine_namespace_readonly(
            self, queue_fd: int, intent: dict[str, Any]) -> int:
        name = require_leaf(intent["quarantine_namespace"], "quarantine namespace")
        descriptor = os.open(name, directory_open_flags(), dir_fd=queue_fd)
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(descriptor)
            raise ArchiveError("quarantine namespace is not private and owner-safe")
        allowed = {intent["quarantine_name"], intent["retired_name"],
                   intent["late_owned_name"]}
        if not set(os.listdir(descriptor)) <= allowed:
            os.close(descriptor)
            raise ArchiveError("quarantine namespace contains unknown/tampered residue")
        return descriptor

    def retirement_recovery_manifest_bound(
            self, root_fd: int, kind: str,
            admitted_manifest: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """Verify and inventory one exact proof-gated crash-recovery state."""
        rows = self.admitted_retirement_rows(admitted_manifest)
        captured: list[dict[str, Any]] = []
        zeroed = 0

        def capture_regular(fd: int, path: str,
                            admitted: dict[str, Any]) -> None:
            nonlocal zeroed
            snapshot = stable_retirement_snapshot(fd, "regular", path)
            self.validate_retirement_entry_snapshot(
                snapshot, admitted, allow_recovery_state=True)
            row = retirement_manifest_row(snapshot)
            if admitted["logical_bytes"] > 0 and row["logical_bytes"] == 0:
                zeroed += 1
            captured.append(row)

        def walk(directory_fd: int, prefix: str) -> None:
            admitted = rows[prefix]
            snapshot = stable_retirement_snapshot(directory_fd, "directory", prefix)
            self.validate_retirement_entry_snapshot(
                snapshot, admitted, allow_recovery_state=True)
            captured.append(retirement_manifest_row(snapshot))
            expected = {Path(path).name: row for path, row in rows.items()
                        if path != "." and str(Path(path).parent) == prefix}
            if set(os.listdir(directory_fd)) != set(expected):
                raise ArchiveError(
                    f"retirement recovery inventory differs: {prefix}")
            for leaf in sorted(expected):
                admitted_child = expected[leaf]
                path = admitted_child["path"]
                child_identity = tuple(admitted_child["local_identity"])
                info = os.stat(leaf, dir_fd=directory_fd, follow_symlinks=False)
                if identity(info) != child_identity:
                    raise ArchiveError(
                        f"retirement recovery child identity differs: {path}")
                child_kind = admitted_child["kind"]
                if child_kind == "directory" and stat.S_ISDIR(info.st_mode):
                    child_fd = stable_rebind(
                        directory_fd, leaf, child_identity, "directory")
                    try:
                        walk(child_fd, path)
                    finally:
                        os.close(child_fd)
                elif child_kind == "regular" and stat.S_ISREG(info.st_mode):
                    child_fd = stable_rebind(
                        directory_fd, leaf, child_identity, "regular")
                    try:
                        capture_regular(child_fd, path, admitted_child)
                    finally:
                        os.close(child_fd)
                elif child_kind == "symlink" and stat.S_ISLNK(info.st_mode):
                    child_fd = os.open(
                        leaf, os.O_RDONLY | O_SYMLINK, dir_fd=directory_fd)
                    try:
                        target = os.readlink(leaf, dir_fd=directory_fd)
                        snapshot = stable_retirement_snapshot(
                            child_fd, "symlink", path, target=target)
                        self.validate_retirement_entry_snapshot(
                            snapshot, admitted_child,
                            allow_recovery_state=True)
                        captured.append(retirement_manifest_row(snapshot))
                    finally:
                        os.close(child_fd)
                else:
                    raise ArchiveError(
                        f"retirement recovery child kind differs: {path}")

        if kind == "regular":
            if set(rows) != {"."}:
                raise ArchiveError("regular retirement recovery has child entries")
            capture_regular(root_fd, ".", rows["."])
        elif kind == "directory":
            walk(root_fd, ".")
        else:
            raise ArchiveError("retirement recovery root kind is unsupported")
        if {row["path"] for row in captured} != set(rows):
            raise ArchiveError("retirement recovery did not bind every admitted entry")
        nonempty = sum(row["kind"] == "regular" and row["logical_bytes"] > 0
                       for row in rows.values())
        if zeroed:
            complete = zeroed == nonempty
            for row in captured:
                admitted = rows[row["path"]]
                if row["kind"] == "symlink":
                    continue
                prepared_mode = admitted["mode"] | (
                    0o700 if row["kind"] == "directory" else 0o600)
                allowed_modes = {prepared_mode}
                if complete:
                    allowed_modes.add(
                        0o500 if row["kind"] == "directory" else 0o400)
                if (row["flags"] != 0 or row["acl"] != ""
                        or row["mode"] not in allowed_modes):
                    raise ArchiveError(
                        f"retirement recovery phase differs: {row['path']}")
        return finish_manifest(captured), zeroed

    def validate_source_root_retirement_chain(
            self, transaction_fd: int, record: dict[str, Any],
            external_second: dict[str, Any], published: dict[str, Any],
            roots: ExternalRoots) -> tuple[
                dict[str, Any] | None, dict[str, Any] | None,
                dict[str, Any] | None]:
        """Validate immutable 008/009/010/011 and any external receipt."""
        quarantined = self.load_stage(transaction_fd, "source-quarantined")
        retirement = self.load_stage(transaction_fd, "retirement-started")
        retired = self.load_stage(transaction_fd, "source-deleted")
        receipt = self.load_stage(transaction_fd, "deletion-receipt")
        if quarantined is None:
            if any(value is not None for value in (retirement, retired, receipt)):
                raise ArchiveError("source-root retirement chain starts after missing 008")
            return None, None, None
        physical = quarantined.get("source_physical_manifest")
        if not isinstance(physical, dict):
            raise ArchiveError("source-root 008 lacks immutable physical source")
        self.validate_source_root_overlay_binding(
            transaction_fd, record, quarantined, physical,
            require_current_gate=True)
        expected_008_keys = {
            "schema", "stage", "quarantine_namespace", "quarantine_name",
            *self.source_root_overlay_binding(
                transaction_fd, record, physical, quarantined["gate_proof"]).keys(),
        }
        if set(quarantined) != expected_008_keys:
            raise ArchiveError("source-root durable 008 shape differs")
        if retirement is None:
            if retired is not None or receipt is not None:
                raise ArchiveError("source-root retirement chain skips durable 009")
            return quarantined, None, None
        second_stage = self.load_stage(transaction_fd, "second-copy-proof")
        if second_stage is None:
            raise ArchiveError("source-root 009 lacks immutable 006")
        expected_009 = {
            "transaction_id": record["transaction_id"],
            "source_identity": record["source_identity"],
            "source_tree_sha256": record["source_manifest"]["tree_sha256"],
            "proof_kind": second_stage.get("proof_kind"),
            "proof_hash": second_stage.get("proof_hash"),
            "second_copy_stage_sha256": sha256_bytes(canonical_json(second_stage)),
            "immutable_proof_sha256": sha256_bytes(canonical_json(external_second)),
        }
        if any(canonical_json(retirement.get(key)) != canonical_json(value)
               for key, value in expected_009.items()):
            raise ArchiveError("source-root durable 009 predecessor differs")
        self.validate_source_root_overlay_downstream_binding(
            transaction_fd, record, retirement, require_current_gate=True)
        downstream_keys = set(self.source_root_overlay_downstream_binding(
            transaction_fd, record, require_current_gate=False))
        if set(retirement) != {"schema", "stage", *expected_009, *downstream_keys}:
            raise ArchiveError("source-root durable 009 shape differs")
        retirement_overlay_policy = \
            self.retirement_overlay_baseline_policy_for_transaction(
                transaction_fd, record)
        retirement_overlay_baseline = \
            self.load_retirement_overlay_baseline(transaction_fd)
        if retirement_overlay_baseline is not None:
            if retirement_overlay_policy is None:
                raise ArchiveError(
                    "retirement overlay baseline is outside reviewed tuple")
            self.validate_retirement_overlay_baseline_record(
                transaction_fd, record, retirement_overlay_baseline,
                require_current_gate=True)
        retirement_pretruncate_record = \
            self.load_retirement_pretruncate_open(transaction_fd)
        if retirement_pretruncate_record is not None:
            self.validate_retirement_pretruncate_open_record(
                transaction_fd, record, retirement_pretruncate_record,
                require_current_gate=True)
        if retired is None:
            if receipt is not None:
                raise ArchiveError("source-root retirement chain skips durable 010")
            return quarantined, retirement, None
        self.validate_source_root_overlay_downstream_binding(
            transaction_fd, record, retired, require_current_gate=True)
        if (retired.get("receipt") != "PASS"
                or retired.get("public_source") != "ABSENT"
                or retired.get("owned_payload") != "ZEROED_TOMBSTONE"
                or retired.get("source_tree_sha256")
                != record["source_manifest"]["tree_sha256"]
                or retired.get("retirement_started_stage_sha256")
                != sha256_bytes(canonical_json(retirement))):
            raise ArchiveError("source-root durable 010 chain differs")
        fixed_010 = {
            "schema", "stage", "transaction_id", "receipt", "public_source",
            "owned_payload", "foreign_entries_removed", "tombstone_name",
            "tombstone_identity", "tombstone_manifest", "source_tree_sha256",
            "reclaimed_logical_bytes", "reclaimed_allocated_bytes",
            "retirement_started_stage_sha256",
        }
        retirement_overlay_keys: set[str] = set()
        if retirement_overlay_policy is not None:
            if retirement_overlay_baseline is None:
                raise ArchiveError(
                    "source-root durable 010 lacks retirement baseline")
            self.validate_retirement_overlay_downstream_binding(
                transaction_fd, record, retired,
                retired.get("tombstone_manifest"))
            retirement_overlay_keys = set(
                self.retirement_overlay_downstream_binding(
                    transaction_fd, record,
                    retired["retirement_runtime_overlay_proof"]))
        if set(retired) != fixed_010 | downstream_keys | retirement_overlay_keys:
            raise ArchiveError("source-root durable 010 shape differs")
        tombstone = retired.get("tombstone_manifest")
        if retirement_overlay_policy is None:
            expected_tombstone = self.build_expected_tombstone_manifest(physical)
            exact_tombstone = (isinstance(tombstone, dict)
                               and canonical_json(tombstone)
                               == canonical_json(expected_tombstone))
        else:
            exact_tombstone = isinstance(tombstone, dict)
        if (not exact_tombstone
                or retired.get("tombstone_identity") != record["source_identity"]
                or retired.get("foreign_entries_removed") != 0
                or retired.get("reclaimed_logical_bytes")
                != physical["logical_bytes"] - tombstone.get("logical_bytes", -1)
                or retired.get("reclaimed_allocated_bytes")
                != physical["allocated_bytes"] - tombstone.get("allocated_bytes", -1)):
            raise ArchiveError("source-root durable 010 retirement accounting differs")
        external_name = f"{record['transaction_id']}-source-deletion.json"
        try:
            external = self.read_record(roots.manifests_fd, external_name)
        except FileNotFoundError:
            external = None
        if external is not None:
            self.validate_external_overlay_binding(record, external, published)
            self.validate_source_root_overlay_downstream_binding(
                transaction_fd, record, external, require_current_gate=True)
            if retirement_overlay_policy is not None:
                self.validate_retirement_overlay_downstream_binding(
                    transaction_fd, record, external,
                    external.get("tombstone_manifest"))
            if (external.get("retirement_started_stage_sha256")
                    != sha256_bytes(canonical_json(retirement))
                    or external.get("source_deleted_stage_sha256")
                    != sha256_bytes(canonical_json(retired))
                    or external.get("tombstone_identity")
                    != retired.get("tombstone_identity")
                    or canonical_json(external.get("tombstone_manifest"))
                    != canonical_json(retired.get("tombstone_manifest"))):
                raise ArchiveError("external source-root deletion receipt chain differs")
            expected_external_keys = set(self.deletion_receipt_payload(
                record, retired, published, transaction_fd=transaction_fd))
            if set(external) != expected_external_keys:
                raise ArchiveError("external source-root deletion receipt shape differs")
        if receipt is not None:
            if external is None:
                raise ArchiveError("local 011 lacks its external deletion receipt")
            digest = sha256_bytes(canonical_json(external))
            if (receipt.get("external_receipt") != external_name
                    or receipt.get("external_receipt_sha256") != digest
                    or receipt.get("source_deleted_stage_sha256")
                    != sha256_bytes(canonical_json(retired))):
                raise ArchiveError("local 011 source-root chain differs")
            self.validate_source_root_overlay_downstream_binding(
                transaction_fd, record, receipt, require_current_gate=True)
            if retirement_overlay_policy is not None:
                for key in retirement_overlay_keys:
                    if canonical_json(receipt.get(key)) \
                            != canonical_json(external.get(key)):
                        raise ArchiveError(
                            f"local retirement overlay chain differs: {key}")
            local_fixed = {
                "schema", "stage", "transaction_id", "external_receipt",
                "external_receipt_sha256", "retirement_started_stage_sha256",
                "source_deleted_stage_sha256",
            }
            if set(receipt) != local_fixed | downstream_keys | retirement_overlay_keys:
                raise ArchiveError("local source-root 011 shape differs")
        return quarantined, retirement, retired

    def verify_source_root_production_state_bound(
            self, queue_fd: int, transaction_fd: int, record: dict[str, Any],
            volume: VolumeBinding, roots: ExternalRoots) -> dict[str, Any]:
        """Read-only exact proof required before the reviewed production drain."""
        policy = self.source_root_overlay_policy_for_record(record)
        if policy is None:
            raise ArchiveError("production-state proof is restricted to the reviewed overlay")
        self.require_volume(volume)
        prefix = self.validate_source_root_legacy_prefix(transaction_fd, record)
        published = self.load_stage(transaction_fd, "published")
        if published is None:
            raise ArchiveError("production-state proof lacks immutable 004")
        self.verify_final_boundary(transaction_fd, record, published, volume, roots)
        self.validate_legacy_external_manifest_005(
            transaction_fd, record, roots, published)
        second = self.load_stage(transaction_fd, "second-copy-proof")
        if second is None:
            raise ArchiveError("production-state proof lacks immutable 006")
        external_second = self.read_record(roots.manifests_fd, second["external_proof"])
        if (sha256_bytes(canonical_json(external_second))
                != second.get("external_proof_sha256")
                or self.second_copy_payload_hash(external_second)
                != second.get("proof_hash")):
            raise ArchiveError("production-state external second-copy proof differs")

        quarantined, retirement, retired = self.validate_source_root_retirement_chain(
            transaction_fd, record, external_second, published, roots)
        retirement_overlay_policy = \
            self.retirement_overlay_baseline_policy_for_transaction(
                transaction_fd, record)
        retirement_overlay_baseline = \
            self.load_retirement_overlay_baseline(transaction_fd)
        retirement_pretruncate_policy = \
            self.retirement_pretruncate_open_policy_for_transaction(
                transaction_fd, record)
        retirement_pretruncate_record = \
            self.load_retirement_pretruncate_open(transaction_fd)
        source_parent = open_absolute_directory(
            Path(record["source"]).parent, "reviewed production source parent")
        quarantine_fd = -1
        source_fd = -1
        try:
            try:
                if identity(os.fstat(source_parent)) \
                        != tuple(record["source_parent_identity"]):
                    raise ArchiveError("reviewed production source parent changed")
                public = os.stat(record["source_name"], dir_fd=source_parent,
                                 follow_symlinks=False)
            except FileNotFoundError:
                public = None
            expected = tuple(record["source_identity"])
            intent = self.load_stage(transaction_fd, "delete-intent")
            owned_name: str | None = None
            if quarantined is not None:
                if intent is None:
                    raise ArchiveError("source-root 008 lacks delete intent")
                quarantine_fd = self.open_quarantine_namespace_readonly(
                    queue_fd, intent)
                owned_name = self.owned_quarantine_name(
                    quarantine_fd, intent, expected)
            if public is not None:
                if identity(public) != expected or owned_name is not None:
                    raise ArchiveError("reviewed production source identity is ambiguous")
                source_fd = stable_rebind(
                    source_parent, record["source_name"], expected,
                    record["source_kind"])
                physical = manifest_bound(source_fd, record["source_kind"])
                if retirement_overlay_policy is None:
                    self.validate_source_root_overlay_manifest(record, physical)
                elif retirement_overlay_baseline is None:
                    compare_retirement_overlay_baseline(
                        quarantined["source_physical_manifest"], physical,
                        policy=retirement_overlay_policy,
                        production_authorization_sha256=
                        REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
                else:
                    self.validate_retirement_overlay_baseline_record(
                        transaction_fd, record, retirement_overlay_baseline,
                        require_current_gate=True)
                    admitted = retirement_overlay_baseline[
                        "current_source_manifest"]
                    if retirement_pretruncate_policy is not None:
                        admitted = build_retirement_pretruncate_open_base_manifest(
                            admitted, policy=retirement_pretruncate_policy)
                    self.validate_retirement_overlay_partial_manifest(
                        admitted, physical, retirement_overlay_policy,
                        permit_partial_mtime=True)
                state = "EXACT_PUBLIC"
                zeroed = 0
                if retired is not None:
                    raise ArchiveError("durable 010 cannot retain a public source")
            else:
                if owned_name is None or quarantine_fd < 0:
                    raise ArchiveError("reviewed production source inode is absent")
                source_fd = stable_rebind(
                    quarantine_fd, owned_name, tuple(record["source_identity"]),
                    record["source_kind"])
                immutable_physical = quarantined["source_physical_manifest"]
                if retirement is None:
                    physical = manifest_bound(source_fd, record["source_kind"])
                    if canonical_json(physical) != canonical_json(immutable_physical):
                        raise ArchiveError("pre-009 production source differs from 008")
                    zeroed = 0
                    state = "EXACT_QUARANTINED"
                elif retired is None:
                    if retirement_overlay_policy is None:
                        physical, zeroed = self.retirement_recovery_manifest_bound(
                            source_fd, record["source_kind"], immutable_physical)
                        state = "PARTIAL_RETIREMENT" \
                            if zeroed else "PREPARED_RETIREMENT"
                    else:
                        physical = manifest_bound(source_fd, record["source_kind"])
                        if retirement_overlay_baseline is None:
                            compare_retirement_overlay_baseline(
                                immutable_physical, physical,
                                policy=retirement_overlay_policy,
                                production_authorization_sha256=
                                REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
                            zeroed = 0
                            state = "RETIREMENT_OVERLAY_PENDING"
                        else:
                            self.validate_retirement_overlay_baseline_record(
                                transaction_fd, record,
                                retirement_overlay_baseline,
                                require_current_gate=True)
                            admitted = retirement_overlay_baseline[
                                "current_source_manifest"]
                            if retirement_pretruncate_policy is not None:
                                admitted = \
                                    build_retirement_pretruncate_open_base_manifest(
                                        admitted,
                                        policy=retirement_pretruncate_policy)
                            zeroed = \
                                self.validate_retirement_overlay_partial_manifest(
                                    admitted,
                                    physical, retirement_overlay_policy,
                                    permit_partial_mtime=True)
                            state = "PARTIAL_RETIREMENT" \
                                if zeroed else "RETIREMENT_OVERLAY_BASELINE"
                    self.verify_prepared_against_immutable_proof(external_second)
                else:
                    physical = manifest_bound(source_fd, record["source_kind"])
                    if (owned_name != retired.get("tombstone_name")
                            or list(identity(os.fstat(source_fd)))
                            != retired.get("tombstone_identity")
                            or canonical_json(physical)
                            != canonical_json(retired.get("tombstone_manifest"))
                            or physical.get("logical_bytes") != 0):
                        raise ArchiveError("durable 010 tombstone differs")
                    if retirement_overlay_policy is not None:
                        self.validate_retirement_overlay_downstream_binding(
                            transaction_fd, record, retired, physical)
                    zeroed = immutable_physical["files"]
                    state = "RETIRED_TOMBSTONE"
            if retirement_pretruncate_record is not None:
                if retirement_pretruncate_policy is None:
                    raise ArchiveError(
                        "retirement pretruncate open is outside reviewed tuple")
                self.validate_retirement_pretruncate_open_record(
                    transaction_fd, record, retirement_pretruncate_record,
                    require_current_gate=True, current_manifest=physical)
            elif retirement_pretruncate_policy is not None \
                    and retirement_overlay_baseline is not None \
                    and zeroed == 0:
                base = build_retirement_pretruncate_open_base_manifest(
                    retirement_overlay_baseline["current_source_manifest"],
                    policy=retirement_pretruncate_policy)
                compare_retirement_pretruncate_open_overlay(
                    base, physical, policy=retirement_pretruncate_policy,
                    production_authorization_sha256=
                    REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256)
            self.require_volume(volume)
            return {
                "status": "PASS", "transaction_id": record["transaction_id"],
                "retirement_state": state, "zeroed_files": zeroed,
                "legacy_prefix_sha256": prefix["aggregate_sha256"],
                "source_semantic_manifest_sha256":
                    manifest_canonical_sha256(record["source_manifest"]),
                "source_physical_manifest_sha256":
                    manifest_canonical_sha256(
                        quarantined["source_physical_manifest"]
                        if quarantined is not None else physical),
                "source_root_overlay_proof_sha256":
                    sha256_bytes(canonical_json(
                        quarantined["source_root_overlay_proof"]
                        if quarantined is not None else
                        self.validate_source_root_overlay_manifest(record, physical))),
                "volume_uuid": volume.attrs["uuid"],
            }
        finally:
            if source_fd >= 0:
                os.close(source_fd)
            if quarantine_fd >= 0:
                os.close(quarantine_fd)
            os.close(source_parent)

    @staticmethod
    def historical_gate_proof_from_row(
            row: tuple[str, str, float, str, str, str]) -> dict[str, Any]:
        receipt, receipt_sha, ended, source, stamp, log = row
        return {
            "schema": "openxray.archive-gate-proof.v1",
            "review_policy_version": REVIEW_POLICY_VERSION,
            "receipt_name": receipt,
            "receipt_sha256": receipt_sha,
            "gate": "full",
            "gate_ended_unix": ended,
            "gate_source_sha256": source,
            "gate_stamp_sha256": stamp,
            "gate_log_sha256": log,
        }

    @staticmethod
    def historical_gate_proofs_in(value: Any) -> dict[bytes, dict[str, Any]]:
        required = {
            "schema", "review_policy_version", "receipt_name", "receipt_sha256",
            "gate", "gate_ended_unix", "gate_source_sha256",
            "gate_stamp_sha256", "gate_log_sha256",
        }
        found: dict[bytes, dict[str, Any]] = {}

        def visit(item: Any) -> None:
            if isinstance(item, dict):
                if (set(item) == required
                        and item.get("schema")
                        == "openxray.archive-gate-proof.v1"):
                    found[canonical_json(item)] = item
                for child in item.values():
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        return found

    def validate_historical_gate_proof_inventory(
            self, values: list[dict[str, Any]],
            policy: HistoricalCompletedRetirementPolicy) -> None:
        """Require the exact reviewed proof set; no subset or wildcard match."""
        found: dict[bytes, dict[str, Any]] = {}
        for value in values:
            found.update(self.historical_gate_proofs_in(value))
        expected = {
            canonical_json(self.historical_gate_proof_from_row(row)):
                self.historical_gate_proof_from_row(row)
            for row in policy.historical_gate_proofs
        }
        if set(found) != set(expected):
            raise ArchiveError(
                "historical completed gate proof inventory differs")
        for proof in expected.values():
            self.validate_reviewed_historical_gate_proof(proof)

    def verify_historical_completed_state_bound(
            self, queue_fd: int, transaction_fd: int, record: dict[str, Any],
            volume: VolumeBinding, roots: ExternalRoots,
            policy: HistoricalCompletedRetirementPolicy) -> dict[str, str]:
        """Verify one closed historical retirement without mutation authority.

        This routine deliberately does not call the generic retired-main,
        production-state or publication verifiers: those remain strict
        mutation/dependency capabilities and continue to require equivalent
        historical/current gate proofs.
        """
        self.require_volume(volume)
        expected_names = {name for name, _ in policy.stage_sha256}
        if set(os.listdir(transaction_fd)) != expected_names:
            raise ArchiveError(
                "historical completed transaction record inventory differs")
        stages: dict[str, dict[str, Any]] = {}
        for name, expected_sha in policy.stage_sha256:
            payload = self.read_record(transaction_fd, name)
            if sha256_bytes(canonical_json(payload)) != expected_sha:
                raise ArchiveError(f"historical completed stage differs: {name}")
            stages[name] = payload

        if (policy.production_authorization_sha256
                != REVIEWED_ALLOWLIST_AUTHORIZATION_SHA256
                or record.get("transaction_id") != policy.transaction_id
                or record.get("candidate_id") != policy.candidate_id
                or record.get("source") != policy.source_path
                or tuple(record.get("source_identity", ()))
                != policy.source_identity
                or tuple(record.get("source_parent_identity", ()))
                != policy.source_parent_identity
                or record.get("allowlist_version") != policy.allowlist_version
                or record.get("category") != policy.category
                or record.get("data_class") != policy.data_class
                or record.get("deletion_rule") != policy.deletion_rule
                or manifest_canonical_sha256(record.get("source_manifest", {}))
                != policy.source_manifest_sha256
                or record.get("source_tree_sha256") != policy.source_tree_sha256):
            raise ArchiveError("historical completed candidate/policy tuple differs")

        published = stages[RECORD_NAMES["published"]]
        manifest_stage = stages[RECORD_NAMES["manifest-published"]]
        second_stage = stages[RECORD_NAMES["second-copy-proof"]]
        intent = stages[RECORD_NAMES["delete-intent"]]
        quarantined = stages[RECORD_NAMES["source-quarantined"]]
        retirement = stages[RECORD_NAMES["retirement-started"]]
        baseline = stages[RETIREMENT_OVERLAY_BASELINE_RECORD]
        pretruncate = stages[RETIREMENT_PRETRUNCATE_OPEN_RECORD]
        retired = stages[RECORD_NAMES["source-deleted"]]
        receipt = stages[RECORD_NAMES["deletion-receipt"]]
        stage_hashes = dict(policy.stage_sha256)

        if (published.get("final_name") != policy.final_name
                or tuple(published.get("final_identity", ()))
                != policy.final_identity
                or manifest_canonical_sha256(
                    published.get("destination_manifest", {}))
                != policy.destination_manifest_sha256
                or published.get("destination_manifest", {}).get("tree_sha256")
                != policy.destination_tree_sha256
                or manifest_stage.get("external_manifest")
                != policy.external_manifest_name
                or manifest_stage.get("external_manifest_sha256")
                != policy.external_manifest_sha256
                or second_stage.get("external_proof")
                != policy.external_second_copy_name
                or second_stage.get("external_proof_sha256")
                != policy.external_second_copy_sha256
                or second_stage.get("proof_hash")
                != policy.second_copy_proof_hash
                or second_stage.get("proof_kind") != "fixed-prepared-retail"
                or sha256_bytes(canonical_json(quarantined))
                != stage_hashes[RECORD_NAMES["source-quarantined"]]
                or retirement.get("source_quarantined_stage_sha256")
                != stage_hashes[RECORD_NAMES["source-quarantined"]]
                or baseline.get("source_quarantined_stage_sha256")
                != stage_hashes[RECORD_NAMES["source-quarantined"]]
                or baseline.get("retirement_started_stage_sha256")
                != stage_hashes[RECORD_NAMES["retirement-started"]]
                or pretruncate.get("source_quarantined_stage_sha256")
                != stage_hashes[RECORD_NAMES["source-quarantined"]]
                or pretruncate.get("retirement_started_stage_sha256")
                != stage_hashes[RECORD_NAMES["retirement-started"]]
                or pretruncate.get("retirement_overlay_baseline_stage_sha256")
                != stage_hashes[RETIREMENT_OVERLAY_BASELINE_RECORD]
                or pretruncate.get("production_authorization_sha256")
                != policy.production_authorization_sha256
                or retired.get("retirement_started_stage_sha256")
                != stage_hashes[RECORD_NAMES["retirement-started"]]
                or retired.get("retirement_pretruncate_open_stage_sha256")
                != stage_hashes[RETIREMENT_PRETRUNCATE_OPEN_RECORD]
                or receipt.get("source_deleted_stage_sha256")
                != stage_hashes[RECORD_NAMES["source-deleted"]]
                or receipt.get("external_receipt")
                != policy.external_receipt_name
                or receipt.get("external_receipt_sha256")
                != policy.external_receipt_sha256):
            raise ArchiveError("historical completed immutable stage chain differs")

        final_fd = stable_rebind(
            roots.category_fd, policy.final_name, policy.final_identity,
            record["source_kind"])
        try:
            try:
                destination = manifest_bound(final_fd, record["source_kind"])
            except OSError as error:
                raise ArchiveError(
                    "historical completed destination cannot be read") from error
        finally:
            os.close(final_fd)
        if (canonical_json(destination)
                != canonical_json(published["destination_manifest"])
                or manifest_canonical_sha256(destination)
                != policy.destination_manifest_sha256
                or destination.get("tree_sha256")
                != policy.destination_tree_sha256
                or (destination.get("files"), destination.get("directories"),
                    destination.get("symlinks"), destination.get("logical_bytes"))
                != (policy.destination_files, policy.destination_directories,
                    policy.destination_symlinks,
                    policy.destination_logical_bytes)):
            raise ArchiveError("historical completed destination differs")

        external_manifest = self.read_record(
            roots.manifests_fd, policy.external_manifest_name)
        external_second = self.read_record(
            roots.manifests_fd, policy.external_second_copy_name)
        external_receipt = self.read_record(
            roots.manifests_fd, policy.external_receipt_name)
        if (sha256_bytes(canonical_json(external_manifest))
                != policy.external_manifest_sha256
                or sha256_bytes(canonical_json(external_second))
                != policy.external_second_copy_sha256
                or sha256_bytes(canonical_json(external_receipt))
                != policy.external_receipt_sha256
                or self.second_copy_payload_hash(external_second)
                != policy.second_copy_proof_hash
                or external_manifest.get("verification") != "PASS"
                or external_receipt.get("source_public") != "ABSENT"
                or external_receipt.get("owned_payload")
                != "ZEROED_TOMBSTONE"
                or external_receipt.get("source_deleted_stage_sha256")
                != stage_hashes[RECORD_NAMES["source-deleted"]]):
            raise ArchiveError("historical completed external records differ")
        self.validate_external_overlay_binding(
            record, external_manifest, published)
        self.validate_external_overlay_binding(
            record, external_receipt, published)

        prepared = external_second.get("prepared_manifest")
        if (not isinstance(prepared, dict)
                or tuple(external_second.get("prepared_root_identity", ()))
                != policy.prepared_root_identity
                or manifest_canonical_sha256(prepared)
                != policy.prepared_manifest_sha256
                or prepared.get("tree_sha256") != policy.prepared_tree_sha256
                or len(external_second.get("mappings", ())) != 13):
            raise ArchiveError("historical completed prepared proof differs")
        try:
            self.verify_historical_prepared_overlay(external_second, policy)
        except OSError as error:
            raise ArchiveError(
                "historical completed prepared proof cannot be read") from error

        source_parent = self.require_public_source_absent(record)
        os.close(source_parent)
        quarantine_fd = self.open_quarantine_namespace_readonly(queue_fd, intent)
        try:
            owned_name = self.owned_quarantine_name(
                quarantine_fd, intent, policy.tombstone_identity)
            if (owned_name != policy.tombstone_name
                    or retired.get("tombstone_name") != policy.tombstone_name
                    or tuple(retired.get("tombstone_identity", ()))
                    != policy.tombstone_identity):
                raise ArchiveError("historical completed tombstone identity differs")
            tombstone_fd = stable_rebind(
                quarantine_fd, owned_name, policy.tombstone_identity,
                record["source_kind"])
            try:
                try:
                    tombstone = manifest_bound(
                        tombstone_fd, record["source_kind"])
                except OSError as error:
                    raise ArchiveError(
                        "historical completed tombstone cannot be read") from error
            finally:
                os.close(tombstone_fd)
        finally:
            os.close(quarantine_fd)
        if (canonical_json(tombstone)
                != canonical_json(retired.get("tombstone_manifest"))
                or manifest_canonical_sha256(tombstone)
                != policy.tombstone_manifest_sha256
                or tombstone.get("tree_sha256") != policy.tombstone_tree_sha256
                or (tombstone.get("files"), tombstone.get("directories"),
                    tombstone.get("symlinks"), tombstone.get("logical_bytes"),
                    tombstone.get("allocated_bytes"))
                != (policy.tombstone_files, policy.tombstone_directories,
                    policy.tombstone_symlinks, policy.tombstone_logical_bytes,
                    policy.tombstone_allocated_bytes)
                or retired.get("receipt") != "PASS"
                or retired.get("public_source") != "ABSENT"
                or retired.get("owned_payload") != "ZEROED_TOMBSTONE"):
            raise ArchiveError("historical completed tombstone differs")

        historical_values = [*stages.values(), external_manifest,
                             external_second, external_receipt]
        self.validate_historical_gate_proof_inventory(
            historical_values, policy)

        current_gate = self.matching_clean_full_gate_receipt()
        if current_gate.get("receipt_name") in {
                row[0] for row in policy.historical_gate_proofs}:
            raise ArchiveError(
                "historical verification requires a separate current full gate")
        self.require_volume(volume)
        return {"status": "HISTORICAL_PASS", "mutation_authorization": "NONE"}

    def verify_historical_production_state(self) -> dict[str, str]:
        """Read-only verification of the sole reviewed completed retirement.

        The return value is intentionally not a generic PASS/proof and is never
        consumed by sibling, drain, recovery or publication services.
        """
        policy = self.historical_completed_policy()
        lock_fd, queue_fd, transaction_fd, record = self.open_transaction(
            policy.transaction_id, exclusive=False, recover_partials=False)
        try:
            volume = self.bind_volume()
            try:
                roots = self.external_roots(volume, record["category"])
                try:
                    return self.verify_historical_completed_state_bound(
                        queue_fd, transaction_fd, record, volume, roots, policy)
                finally:
                    roots.close()
            finally:
                volume.close()
        finally:
            os.close(transaction_fd)
            os.close(queue_fd)
            os.close(lock_fd)

    def verify_production_state(
            self, transaction_id: str = SOURCE_ROOT_OVERLAY_TRANSACTION_ID
            ) -> dict[str, Any]:
        lock_fd, queue_fd, transaction_fd, record = self.open_transaction(
            transaction_id, exclusive=False, recover_partials=False)
        try:
            volume = self.bind_volume()
            try:
                roots = self.external_roots(volume, record["category"])
                try:
                    return self.verify_source_root_production_state_bound(
                        queue_fd, transaction_fd, record, volume, roots)
                finally:
                    roots.close()
            finally:
                volume.close()
        finally:
            os.close(transaction_fd)
            os.close(queue_fd)
            os.close(lock_fd)

    def verify_final_boundary(self, transaction_fd: int, record: dict[str, Any],
                              published: dict[str, Any], volume: VolumeBinding,
                              roots: ExternalRoots) -> None:
        self.require_volume(volume)
        try:
            descriptor = stable_rebind(
                roots.category_fd, published["final_name"],
                tuple(published["final_identity"]), record["source_kind"])
        except (ArchiveError, FileNotFoundError) as error:
            raise ArchiveError("published destination is absent or foreign") from error
        try:
            manifest = manifest_bound(descriptor, record["source_kind"])
        finally:
            os.close(descriptor)
        policy = self.provenance_overlay_policy_for_record(record)
        if policy is None:
            if (not manifests_equal(record["source_manifest"], manifest)
                    or not manifests_equal(published["destination_manifest"], manifest)):
                raise ArchiveError("published destination differs from its durable manifest")
        else:
            self.validate_immutable_copy_record(transaction_fd, record, manifest)
            if canonical_json(published["destination_manifest"]) != canonical_json(manifest):
                raise ArchiveError(
                    "published destination differs from immutable physical manifest")
            self.validate_overlay_record_binding(record, published, manifest)
        self.require_volume(volume)

    def ensure_not_required_second_copy(self, transaction_fd: int,
                                        record: dict[str, Any]) -> dict[str, Any]:
        payload = {"transaction_id": record["transaction_id"],
                   "proof_kind": "not-required", "proof_hash": None}
        self.stage(transaction_fd, "second-copy-proof", payload)
        return self.load_stage(transaction_fd, "second-copy-proof") or payload

    def verify_retired_main_transaction(self, queue_fd: int, transaction_fd: int,
                                        record: dict[str, Any], volume: VolumeBinding,
                                        roots: ExternalRoots) -> dict[str, Any]:
        proof = self.load_stage(transaction_fd, "second-copy-proof")
        published = self.load_stage(transaction_fd, "published")
        manifest_stage = self.load_stage(transaction_fd, "manifest-published")
        intent = self.load_stage(transaction_fd, "delete-intent")
        quarantined = self.load_stage(transaction_fd, "source-quarantined")
        retirement_started = self.load_stage(transaction_fd, "retirement-started")
        retired = self.load_stage(transaction_fd, "source-deleted")
        receipt = self.load_stage(transaction_fd, "deletion-receipt")
        if any(value is None for value in (
                proof, published, manifest_stage, intent, quarantined,
                retirement_started, retired, receipt)):
            raise ArchiveError("main retail backup transaction is not complete")
        assert proof is not None and published is not None and manifest_stage is not None
        assert intent is not None and quarantined is not None
        assert retirement_started is not None and retired is not None and receipt is not None
        if (proof.get("proof_kind") != "fixed-prepared-retail"
                or retired.get("receipt") != "PASS"
                or retired.get("public_source") != "ABSENT"
                or retired.get("owned_payload") != "ZEROED_TOMBSTONE"):
            raise ArchiveError("main retail backup is not source-retired")
        source_parent = self.require_public_source_absent(record)
        os.close(source_parent)
        quarantine_fd = self.quarantine_namespace(queue_fd, intent)
        try:
            expected = tuple(record["source_identity"])
            owned_name = self.owned_quarantine_name(quarantine_fd, intent, expected)
            if owned_name != retired.get("tombstone_name"):
                raise ArchiveError("main retail tombstone identity is absent")
            tombstone_fd = stable_rebind(
                quarantine_fd, owned_name, expected, record["source_kind"])
            try:
                tombstone_manifest = manifest_bound(tombstone_fd, record["source_kind"])
            finally:
                os.close(tombstone_fd)
            if canonical_json(tombstone_manifest) != canonical_json(
                    retired.get("tombstone_manifest")):
                raise ArchiveError("main retail tombstone changed")
            self.validate_existing_tombstone_against_immutable_plan(
                transaction_fd, record, retired)
        finally:
            os.close(quarantine_fd)
        self.verify_final_boundary(transaction_fd, record, published, volume, roots)
        external_second_copy = self.read_record(
            roots.manifests_fd, proof["external_proof"])
        external_manifest = self.read_record(
            roots.manifests_fd, manifest_stage["external_manifest"])
        external_receipt = self.read_record(
            roots.manifests_fd, receipt["external_receipt"])
        self.validate_external_overlay_binding(record, external_manifest, published)
        self.validate_external_overlay_binding(record, external_receipt, published)
        if self.source_root_overlay_policy_for_record(record) is not None:
            physical = quarantined.get("source_physical_manifest")
            if not isinstance(physical, dict):
                raise ArchiveError("main retail source-root 008 is incomplete")
            self.validate_source_root_overlay_binding(
                transaction_fd, record, quarantined, physical,
                require_current_gate=True)
            for payload in (retirement_started, retired, receipt, external_receipt):
                self.validate_source_root_overlay_downstream_binding(
                    transaction_fd, record, payload, require_current_gate=True)
            if (retirement_started.get("source_quarantined_stage_sha256")
                    != sha256_bytes(canonical_json(quarantined))
                    or retired.get("retirement_started_stage_sha256")
                    != sha256_bytes(canonical_json(retirement_started))
                    or receipt.get("source_deleted_stage_sha256")
                    != sha256_bytes(canonical_json(retired))
                    or external_receipt.get("source_deleted_stage_sha256")
                    != receipt.get("source_deleted_stage_sha256")):
                raise ArchiveError("main retail source-root stage chain differs")
        if (sha256_bytes(canonical_json(external_second_copy))
                != proof["external_proof_sha256"]
                or self.second_copy_payload_hash(external_second_copy) != proof["proof_hash"]
                or sha256_bytes(canonical_json(external_manifest))
                != manifest_stage["external_manifest_sha256"]
                or sha256_bytes(canonical_json(external_receipt))
                != receipt["external_receipt_sha256"]
                or external_manifest.get("verification") != "PASS"
                or external_receipt.get("source_public") != "ABSENT"
                or external_receipt.get("owned_payload") != "ZEROED_TOMBSTONE"):
            raise ArchiveError("main retail external PASS/receipt is invalid")
        return {
            "main_transaction_id": record["transaction_id"],
            "main_transfer_manifest": manifest_stage["external_manifest"],
            "main_transfer_manifest_sha256": manifest_stage["external_manifest_sha256"],
            "main_deletion_receipt": receipt["external_receipt"],
            "main_deletion_receipt_sha256": receipt["external_receipt_sha256"],
            "main_source_deleted_sha256": sha256_bytes(canonical_json(retired)),
            "main_second_copy_proof_hash": proof["proof_hash"],
        }

    def ensure_sibling_dependency_proof(self, queue_fd: int, transaction_fd: int,
                                        record: dict[str, Any], volume: VolumeBinding,
                                        roots: ExternalRoots) -> dict[str, Any]:
        main_ids = [candidate.ident for candidate in self.settings.candidates
                    if self.retail_role(candidate) == "main"]
        if len(main_ids) != 1:
            raise ArchiveError("exact main retail candidate is not configured")
        matches: list[dict[str, Any]] = []
        for name in sorted(os.listdir(queue_fd)):
            if name == record["transaction_id"] or re.fullmatch(r"[0-9a-f]{32}", name) is None:
                continue
            try:
                candidate_fd = os.open(name, directory_open_flags(), dir_fd=queue_fd)
            except (FileNotFoundError, NotADirectoryError):
                continue
            try:
                candidate_record = self.candidate_record(candidate_fd)
                if candidate_record["candidate_id"] != main_ids[0]:
                    continue
                dependency = self.verify_retired_main_transaction(
                    queue_fd, candidate_fd, candidate_record, volume, roots)
                required_dependency = {
                    "main_transaction_id", "main_transfer_manifest",
                    "main_transfer_manifest_sha256", "main_deletion_receipt",
                    "main_deletion_receipt_sha256", "main_source_deleted_sha256",
                    "main_second_copy_proof_hash",
                }
                expected_transaction = candidate_record["transaction_id"]
                if (set(dependency) != required_dependency
                        or dependency.get("main_transaction_id")
                        != expected_transaction
                        or dependency.get("main_transfer_manifest")
                        != f"{expected_transaction}.json"
                        or dependency.get("main_deletion_receipt")
                        != f"{expected_transaction}-source-deletion.json"
                        or any(re.fullmatch(r"[0-9a-f]{64}",
                                            dependency.get(key, "")) is None
                               for key in (
                                   "main_transfer_manifest_sha256",
                                   "main_deletion_receipt_sha256",
                                   "main_source_deleted_sha256",
                                   "main_second_copy_proof_hash"))):
                    raise ArchiveError(
                        "sibling main dependency result is not an authorizing proof")
                matches.append(dependency)
            except ArchiveError as error:
                if "not complete" not in str(error):
                    raise
            finally:
                os.close(candidate_fd)
        if len(matches) != 1:
            raise ArchiveError("sibling manifest requires exactly one completed main backup transaction")
        payload = {
            "schema": "openxray.sibling-dependency-proof.v1",
            "transaction_id": record["transaction_id"],
            "candidate_id": record["candidate_id"], "category": record["category"],
            **matches[0],
        }
        payload["proof_hash"] = self.second_copy_payload_hash(payload)
        name = f"{record['transaction_id']}-second-copy-proof.json"
        self.require_volume(volume)
        digest = self.write_record(roots.manifests_fd, name, payload, volume)
        self.fsync(roots.manifests_fd, "external sibling dependency proof root", volume)
        existing = self.load_stage(transaction_fd, "second-copy-proof")
        stage_payload = {"transaction_id": record["transaction_id"],
                         "proof_kind": "main-backup-dependency",
                         "proof_hash": payload["proof_hash"],
                         "external_proof": name, "external_proof_sha256": digest,
                         "main_transaction_id": matches[0]["main_transaction_id"]}
        if existing is None:
            self.stage(transaction_fd, "second-copy-proof", stage_payload)
        elif canonical_json(existing) != canonical_json(
                {"schema": SCHEMA, "stage": "second-copy-proof", **stage_payload}):
            raise ArchiveError("sibling dependency stage changed")
        return payload

    def create_delete_intent(self, transaction_fd: int, record: dict[str, Any]) -> dict[str, Any]:
        existing = self.load_stage(transaction_fd, "delete-intent")
        if existing is not None:
            return existing
        payload = {"transaction_id": record["transaction_id"],
                   "source_identity": record["source_identity"],
                   "source_parent_identity": record["source_parent_identity"],
                   "source_tree_sha256": record["source_manifest"]["tree_sha256"],
                   "quarantine_namespace": f".archive-quarantine-{uuid.uuid4().hex}",
                   "quarantine_name": "source",
                   "retired_name": f".retired-{uuid.uuid4().hex}",
                   "late_owned_name": f".late-owned-{uuid.uuid4().hex}"}
        self.stage(transaction_fd, "delete-intent", payload)
        self.hook("after_delete_intent")
        return self.load_stage(transaction_fd, "delete-intent") or payload

    def quarantine_namespace(self, queue_fd: int, intent: dict[str, Any]) -> int:
        descriptor, _ = self.ensure_private_directory(queue_fd, intent["quarantine_namespace"])
        allowed = {intent["quarantine_name"], intent["retired_name"],
                   intent["late_owned_name"]}
        if not set(os.listdir(descriptor)) <= allowed:
            os.close(descriptor)
            raise ArchiveError("quarantine namespace contains unknown/tampered residue")
        return descriptor

    def owned_quarantine_name(self, quarantine_fd: int, intent: dict[str, Any],
                              expected: tuple[int, int]) -> str | None:
        allowed = {intent["quarantine_name"], intent["retired_name"],
                   intent["late_owned_name"]}
        matches = []
        for name in sorted(allowed):
            try:
                current = os.stat(name, dir_fd=quarantine_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if identity(current) == expected:
                matches.append(name)
        if len(matches) > 1:
            raise ArchiveError("owned source appears under multiple quarantine names")
        return matches[0] if matches else None

    def require_public_source_absent(self, record: dict[str, Any]) -> int:
        source_parent = open_absolute_directory(Path(record["source"]).parent,
                                                "retired source parent")
        try:
            if identity(os.fstat(source_parent)) != tuple(record["source_parent_identity"]):
                raise ArchiveError("source parent changed before retirement")
            try:
                os.stat(record["source_name"], dir_fd=source_parent,
                        follow_symlinks=False)
            except FileNotFoundError:
                return source_parent
            raise ArchiveError("public source name reappeared during retirement")
        except BaseException:
            os.close(source_parent)
            raise

    def ensure_source_quarantined(self, transaction_fd: int, record: dict[str, Any],
                                  intent: dict[str, Any], queue_fd: int,
                                  volume: VolumeBinding) -> dict[str, Any]:
        existing = self.load_stage(transaction_fd, "source-quarantined")
        overlay_policy = self.source_root_overlay_policy_for_record(record)
        new_overlay_gate_proof: dict[str, Any] | None = None
        if overlay_policy is not None:
            self.validate_source_root_legacy_prefix(transaction_fd, record)
            if existing is None:
                # Prove the current code/gate policy before moving the public
                # source.  The same proof is then sealed into immutable 008;
                # a stale/missing gate cannot create a new quarantine window.
                new_overlay_gate_proof = self.matching_full_gate_receipt()
        quarantine_fd = self.quarantine_namespace(queue_fd, intent)
        try:
            expected = tuple(record["source_identity"])
            owned_name = self.owned_quarantine_name(quarantine_fd, intent, expected)
            if existing is not None and owned_name is not None:
                if owned_name == intent["quarantine_name"]:
                    physical_manifest = self.verify_bound_payload(
                        quarantine_fd, owned_name, expected, record,
                        "durable quarantined source", transaction_fd=transaction_fd)
                    if (overlay_policy is not None
                            and self.retirement_overlay_baseline_policy_for_transaction(
                                transaction_fd, record) is None):
                        self.validate_source_root_overlay_binding(
                            transaction_fd, record, existing, physical_manifest,
                            require_current_gate=True)
                elif owned_name not in {intent["retired_name"], intent["late_owned_name"]}:
                    raise ArchiveError("durable quarantined source name is invalid")
                source_parent = self.require_public_source_absent(record)
                os.close(source_parent)
                return existing
            physical_manifest: dict[str, Any] | None = None
            if owned_name is not None:
                physical_manifest = self.verify_bound_payload(
                    quarantine_fd, owned_name, expected, record,
                    "recovered quarantined source", transaction_fd=transaction_fd)
            else:
                source = self.bind_recorded_source(record, transaction_fd)
                try:
                    physical_manifest = self.manifest_admitted_source(
                        source.root_fd, source.kind)
                    if (overlay_policy is not None
                            and self.retirement_overlay_baseline_policy_for_transaction(
                                transaction_fd, record) is None):
                        if existing is None:
                            self.validate_source_root_overlay_manifest(
                                record, physical_manifest)
                        else:
                            self.validate_source_root_overlay_binding(
                                transaction_fd, record, existing, physical_manifest,
                                require_current_gate=True)
                    self.hook("before_source_quarantine", source=source,
                              quarantine_fd=quarantine_fd)
                    if identity(os.fstat(source.parent_fd)) != tuple(record["source_parent_identity"]):
                        raise ArchiveError("source parent changed before quarantine")
                    self.require_volume(volume)
                    self.platform.rename_exclusive(source.parent_fd, source.name,
                                                   quarantine_fd, intent["quarantine_name"])
                    moved = os.stat(intent["quarantine_name"], dir_fd=quarantine_fd,
                                    follow_symlinks=False)
                    if identity(moved) != expected:
                        self.restore_mismatch(quarantine_fd, intent["quarantine_name"],
                                              source.parent_fd, source.name)
                        raise ArchiveError("source race moved foreign data; restored without deletion")
                    try:
                        moved_manifest = self.verify_bound_payload(
                            quarantine_fd, intent["quarantine_name"], expected, record,
                            "quarantined source", transaction_fd=transaction_fd)
                        if canonical_json(moved_manifest) != canonical_json(physical_manifest):
                            raise ArchiveError("quarantined source physical manifest changed")
                        physical_manifest = moved_manifest
                    except BaseException:
                        self.restore_mismatch(
                            quarantine_fd, intent["quarantine_name"],
                            source.parent_fd, source.name)
                        raise
                    self.fsync(source.parent_fd, "source parent after quarantine")
                    self.fsync(quarantine_fd, "source quarantine namespace")
                    self.hook("after_source_quarantine_rename")
                finally:
                    source.close()
            payload = {"transaction_id": record["transaction_id"],
                       "quarantine_namespace": intent["quarantine_namespace"],
                       "quarantine_name": intent["quarantine_name"],
                       "source_identity": record["source_identity"]}
            if overlay_policy is not None:
                if physical_manifest is None:
                    raise ArchiveError("source-root overlay physical manifest is absent")
                if existing is None:
                    if new_overlay_gate_proof is None:
                        raise ArchiveError("source-root overlay gate proof is absent")
                    payload.update(self.source_root_overlay_binding(
                        transaction_fd, record, physical_manifest,
                        new_overlay_gate_proof))
                else:
                    if self.retirement_overlay_baseline_policy_for_transaction(
                            transaction_fd, record) is None:
                        self.validate_source_root_overlay_binding(
                            transaction_fd, record, existing, physical_manifest,
                            require_current_gate=True)
                    payload.update({key: value for key, value in existing.items()
                                    if key not in {"schema", "stage",
                                                   "quarantine_namespace",
                                                   "quarantine_name"}})
            if existing is None:
                self.stage(transaction_fd, "source-quarantined", payload)
                self.hook("after_source_quarantined_record")
                return self.load_stage(transaction_fd, "source-quarantined") or payload
            if canonical_json(existing) != canonical_json(
                    {"schema": SCHEMA, "stage": "source-quarantined", **payload}):
                raise ArchiveError("recovered quarantine differs from its durable record")
            return existing
        finally:
            os.close(quarantine_fd)

    def ensure_retirement_started(
            self, transaction_fd: int, record: dict[str, Any],
            immutable_proof: dict[str, Any]) -> dict[str, Any]:
        second_copy_stage = self.load_stage(transaction_fd, "second-copy-proof")
        if second_copy_stage is None:
            raise ArchiveError("retirement lacks its durable second-copy predecessor")
        payload = {
            "transaction_id": record["transaction_id"],
            "source_identity": record["source_identity"],
            "source_tree_sha256": record["source_manifest"]["tree_sha256"],
            "proof_kind": second_copy_stage.get("proof_kind"),
            "proof_hash": second_copy_stage.get("proof_hash"),
            "second_copy_stage_sha256": sha256_bytes(canonical_json(second_copy_stage)),
            "immutable_proof_sha256": sha256_bytes(canonical_json(immutable_proof)),
        }
        if self.source_root_overlay_policy_for_record(record) is not None:
            payload.update(self.source_root_overlay_downstream_binding(
                transaction_fd, record, require_current_gate=True))
        existing = self.load_stage(transaction_fd, "retirement-started")
        if existing is None:
            self.stage(transaction_fd, "retirement-started", payload)
            self.hook("after_retirement_started")
            return self.load_stage(transaction_fd, "retirement-started") or payload
        if self.source_root_overlay_policy_for_record(record) is not None:
            base_keys = {
                "transaction_id", "source_identity", "source_tree_sha256",
                "proof_kind", "proof_hash", "second_copy_stage_sha256",
                "immutable_proof_sha256",
            }
            if any(canonical_json(existing.get(key)) != canonical_json(payload[key])
                   for key in base_keys):
                raise ArchiveError("retirement-started immutable predecessor differs")
            self.validate_source_root_overlay_downstream_binding(
                transaction_fd, record, existing, require_current_gate=True)
        else:
            expected = {"schema": SCHEMA, "stage": "retirement-started", **payload}
            if canonical_json(existing) != canonical_json(expected):
                raise ArchiveError("retirement-started record differs from immutable proof")
        return existing

    def restore_quarantined_source(self, transaction_fd: int,
                                   record: dict[str, Any],
                                   intent: dict[str, Any], queue_fd: int, *,
                                   verify_contents: bool = True,
                                   exact_contents: bool = False) -> None:
        """Restore an intact owned payload after a late proof failure.

        The durable quarantine stage remains as recovery history.  A later drain
        may move the exact recorded source back into the same private namespace.
        """
        quarantine_fd = self.quarantine_namespace(queue_fd, intent)
        source_parent = open_absolute_directory(
            Path(record["source"]).parent, "second-copy restore parent")
        moved_public = False
        owned_name = ""
        expected = tuple(record["source_identity"])
        try:
            if identity(os.fstat(source_parent)) != tuple(record["source_parent_identity"]):
                raise ArchiveError("source parent changed before second-copy restore")
            owned_name = self.owned_quarantine_name(quarantine_fd, intent, expected)
            if owned_name is None:
                raise ArchiveError("owned retail source is absent from quarantine")
            if verify_contents:
                self.verify_bound_payload(
                    quarantine_fd, owned_name, expected, record,
                    "retail source before second-copy restore",
                    transaction_fd=transaction_fd,
                    require_current_overlay_gate=False, exact=exact_contents)
            try:
                os.stat(record["source_name"], dir_fd=source_parent,
                        follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ArchiveError("cannot restore retail source over an occupied public name")
            self.platform.rename_exclusive(
                quarantine_fd, owned_name, source_parent, record["source_name"])
            moved_public = True
            moved = os.stat(record["source_name"], dir_fd=source_parent,
                            follow_symlinks=False)
            if identity(moved) != expected:
                self.restore_mismatch(
                    source_parent, record["source_name"], quarantine_fd, owned_name)
                raise ArchiveError("second-copy restore moved foreign data")
            if verify_contents:
                self.verify_bound_payload(
                    source_parent, record["source_name"], expected, record,
                    "restored retail source", transaction_fd=transaction_fd,
                    require_current_overlay_gate=False,
                    exact=exact_contents)
            self.fsync(source_parent, "restored retail source parent")
            self.fsync(quarantine_fd, "restored retail quarantine namespace")
            self.hook("after_source_restore", transaction_id=record["transaction_id"])
        except BaseException:
            if moved_public:
                try:
                    public = os.stat(record["source_name"], dir_fd=source_parent,
                                     follow_symlinks=False)
                except FileNotFoundError:
                    public = None
                except OSError:
                    public = None
                quarantine_missing = False
                try:
                    os.stat(owned_name, dir_fd=quarantine_fd, follow_symlinks=False)
                except FileNotFoundError:
                    quarantine_missing = True
                if public is not None and quarantine_missing \
                        and identity(public) == expected:
                    self.platform.rename_exclusive(
                        source_parent, record["source_name"],
                        quarantine_fd, owned_name)
                    rebound = os.stat(
                        owned_name, dir_fd=quarantine_fd, follow_symlinks=False)
                    if identity(rebound) != expected:
                        raise ArchiveError(
                            "failed restore could not retain its owned quarantine inode")
                    self.fsync(source_parent, "failed source restore parent")
                    self.fsync(quarantine_fd, "failed source restore quarantine")
            raise
        finally:
            os.close(source_parent)
            os.close(quarantine_fd)

    def restore_pretruncate_source(
            self, transaction_fd: int, record: dict[str, Any], intent: dict[str, Any],
            queue_fd: int) -> bool:
        """Restore admitted metadata, prove the full manifest, then republish locally."""
        overlay_baseline = self.load_retirement_overlay_baseline(transaction_fd)
        overlay_policy = self.retirement_overlay_baseline_policy_for_transaction(
            transaction_fd, record)
        if overlay_baseline is not None and overlay_policy is not None:
            quarantine_fd = self.quarantine_namespace(queue_fd, intent)
            descriptor = -1
            try:
                owned_name = self.owned_quarantine_name(
                    quarantine_fd, intent, tuple(record["source_identity"]))
                if owned_name is None:
                    return False
                descriptor = stable_rebind(
                    quarantine_fd, owned_name, tuple(record["source_identity"]),
                    record["source_kind"])
                current = manifest_bound(descriptor, record["source_kind"])
                if canonical_json(current) != canonical_json(
                        overlay_baseline["current_source_manifest"]):
                    return False
            except (ArchiveError, OSError, ValueError, TypeError):
                return False
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                os.close(quarantine_fd)
            try:
                self.restore_quarantined_source(
                    transaction_fd, record, intent, queue_fd,
                    verify_contents=False)
            except (ArchiveError, OSError, ValueError, TypeError):
                return False
            return True
        quarantine_fd = self.quarantine_namespace(queue_fd, intent)
        try:
            expected = tuple(record["source_identity"])
            owned_name = self.owned_quarantine_name(quarantine_fd, intent, expected)
            if owned_name is None:
                return False
            descriptor = -1
            plan: MetadataRestorePlan | None = None
            try:
                descriptor = stable_rebind(
                    quarantine_fd, owned_name, expected, record["source_kind"])
                # This complete pass checks identities, kinds, inventory,
                # regular contents/sizes and nlink before changing metadata.
                retirement_manifest = self.source_manifest_for_retirement(
                    transaction_fd, record, require_current_gate=False)
                plan = self.bind_metadata_restore_plan(
                    descriptor, record["source_kind"], retirement_manifest)
                self.restore_metadata_plan(plan)
                restored = manifest_bound(descriptor, record["source_kind"])
                if canonical_json(restored) != canonical_json(retirement_manifest):
                    return False
            except (ArchiveError, OSError, ValueError, TypeError):
                return False
            finally:
                if plan is not None:
                    plan.close()
                if descriptor >= 0:
                    os.close(descriptor)
        finally:
            os.close(quarantine_fd)
        try:
            self.restore_quarantined_source(
                transaction_fd, record, intent, queue_fd, verify_contents=True,
                exact_contents=True)
        except (ArchiveError, OSError, ValueError, TypeError):
            return False
        return True

    def verify_main_second_copy_before_retirement(
            self, transaction_fd: int, record: dict[str, Any],
            intent: dict[str, Any], queue_fd: int,
            immutable_proof: dict[str, Any]) -> None:
        """Re-prove the prepared copy from the quarantined source immediately before zeroing."""
        quarantine_fd = self.quarantine_namespace(queue_fd, intent)
        try:
            expected = tuple(record["source_identity"])
            owned_name = self.owned_quarantine_name(quarantine_fd, intent, expected)
            if owned_name is None:
                raise ArchiveError("owned retail source is absent before final proof")
            source_fd = stable_rebind(
                quarantine_fd, owned_name, expected, record["source_kind"])
            try:
                self.hook("before_retirement_second_copy_proof")
                fresh = self.build_second_copy_proof(
                    record, source_fd, transaction_fd)
            finally:
                os.close(source_fd)
        except BaseException:
            os.close(quarantine_fd)
            self.restore_quarantined_source(
                transaction_fd, record, intent, queue_fd)
            raise
        os.close(quarantine_fd)
        if canonical_json(fresh) != canonical_json(immutable_proof):
            self.restore_quarantined_source(
                transaction_fd, record, intent, queue_fd)
            raise ArchiveError("second-copy proof changed immediately before retirement")

    def ensure_source_deleted(self, transaction_fd: int, record: dict[str, Any],
                              intent: dict[str, Any], queue_fd: int,
                              volume: VolumeBinding,
                              final_retirement_boundary: Callable[[], None], *,
                              allow_retirement_recovery: bool) -> dict[str, Any]:
        existing = self.load_stage(transaction_fd, "source-deleted")
        retirement = self.load_stage(transaction_fd, "retirement-started")
        if retirement is None \
                or retirement.get("source_identity") != record["source_identity"] \
                or retirement.get("source_tree_sha256") \
                != record["source_manifest"]["tree_sha256"]:
            raise ArchiveError("source retirement lacks a matching durable start record")
        overlay_policy = self.source_root_overlay_policy_for_record(record)
        retirement_overlay_policy = \
            self.retirement_overlay_baseline_policy_for_transaction(
                transaction_fd, record)
        retirement_overlay_baseline = \
            self.load_retirement_overlay_baseline(transaction_fd)
        retirement_pretruncate_policy = \
            self.retirement_pretruncate_open_policy_for_transaction(
                transaction_fd, record)
        if retirement_overlay_policy is not None:
            if retirement_overlay_baseline is None:
                raise ArchiveError(
                    "reviewed retirement overlay lacks durable 009a")
            self.validate_retirement_overlay_baseline_record(
                transaction_fd, record, retirement_overlay_baseline,
                require_current_gate=True)
        overlay_binding: dict[str, Any] = {}
        if overlay_policy is not None:
            self.validate_source_root_overlay_downstream_binding(
                transaction_fd, record, retirement, require_current_gate=True)
            overlay_binding = self.source_root_overlay_downstream_binding(
                transaction_fd, record, require_current_gate=True)
        retirement_manifest = self.source_manifest_for_retirement(
            transaction_fd, record)
        quarantine_fd = self.quarantine_namespace(queue_fd, intent)
        retirement_state = {"payload_write_started": False}
        runtime_overlay_proof: dict[str, Any] | None = None
        try:
            expected = tuple(record["source_identity"])
            source_parent = self.require_public_source_absent(record)
            try:
                failure_phase = "preflight"
                try:
                    owned_name = self.owned_quarantine_name(quarantine_fd, intent, expected)
                    if owned_name is None:
                        raise ArchiveError("owned source payload is absent from quarantine")
                    if existing is not None:
                        if overlay_policy is not None:
                            self.validate_source_root_overlay_downstream_binding(
                                transaction_fd, record, existing,
                                require_current_gate=True)
                            if existing.get("retirement_started_stage_sha256") \
                                    != sha256_bytes(canonical_json(retirement)):
                                raise ArchiveError(
                                    "source-deleted retirement-stage binding differs")
                            if retirement_overlay_policy is not None:
                                self.validate_retirement_overlay_downstream_binding(
                                    transaction_fd, record, existing,
                                    existing.get("tombstone_manifest"))
                        if owned_name != existing["tombstone_name"]:
                            raise ArchiveError("retained tombstone name changed after receipt")
                        descriptor = stable_rebind(
                            quarantine_fd, owned_name, expected, record["source_kind"])
                        try:
                            manifest = manifest_bound(descriptor, record["source_kind"])
                        finally:
                            os.close(descriptor)
                        if canonical_json(existing["tombstone_manifest"]) != canonical_json(manifest):
                            raise ArchiveError("retained tombstone changed after retirement")
                        self.validate_existing_tombstone_against_immutable_plan(
                            transaction_fd, record, existing)
                        return existing

                    if owned_name == intent["quarantine_name"]:
                        self.require_volume(volume)
                        self.platform.rename_exclusive(
                            quarantine_fd, intent["quarantine_name"],
                            quarantine_fd, intent["retired_name"])
                        moved = os.stat(intent["retired_name"], dir_fd=quarantine_fd,
                                        follow_symlinks=False)
                        if identity(moved) != expected:
                            self.restore_mismatch(
                                quarantine_fd, intent["retired_name"],
                                quarantine_fd, intent["quarantine_name"])
                            raise ArchiveError(
                                "quarantine race moved foreign data; restored without retirement")
                        owned_name = intent["retired_name"]
                        self.fsync(quarantine_fd, "source retirement namespace rename")

                    descriptor = stable_rebind(
                        quarantine_fd, owned_name, expected, record["source_kind"])
                    plan: RetirementPlan | None = None
                    try:
                        self.hook("after_final_retire_rebind", quarantine_fd=quarantine_fd,
                                  intent=intent, pinned_fd=descriptor, owned_name=owned_name)
                        rows = self.preflight_retirement_tree(
                            descriptor, record["source_kind"], retirement_manifest,
                            allow_zeroed_recovery=True)
                        current_before_metadata = manifest_bound(
                            descriptor, record["source_kind"])
                        admitted_rows = self.admitted_retirement_rows(retirement_manifest)
                        current_rows = self.admitted_retirement_rows(
                            current_before_metadata)
                        already_truncated = any(
                            admitted_rows[path].get("kind") == "regular"
                            and admitted_rows[path].get("logical_bytes", 0) > 0
                            and current_rows.get(path, {}).get("logical_bytes") == 0
                            for path in admitted_rows)
                        if retirement_overlay_policy is None:
                            if (not already_truncated
                                    and not allow_retirement_recovery
                                    and canonical_json(current_before_metadata)
                                    != canonical_json(retirement_manifest)):
                                raise ArchiveError(
                                    "source metadata/content differs immediately before retirement")
                        else:
                            # The reviewed APFS path can gain only the exact
                            # provenance value while O_RDWR descriptors are
                            # opened.  Validate that monotonic state before
                            # opening any further inode; arbitrary drift still
                            # fails closed.
                            if not self.validate_retirement_pretruncate_source_state(
                                    transaction_fd, record,
                                    current_before_metadata,
                                    require_current_gate=True):
                                self.validate_retirement_overlay_partial_manifest(
                                    retirement_manifest,
                                    current_before_metadata,
                                    retirement_overlay_policy,
                                    permit_partial_mtime=True)
                        self.hook(
                            "after_retirement_manifest_preflight",
                            pinned_fd=descriptor, owned_name=owned_name,
                            already_truncated=already_truncated)
                        failure_phase = "metadata"
                        aliases = {
                            intent["quarantine_name"], intent["retired_name"],
                            intent["late_owned_name"]}
                        if retirement_overlay_policy is None:
                            plan = self.prepare_retirement_plan(
                                descriptor, record["source_kind"], volume, rows,
                                parent_fd=quarantine_fd, name=owned_name,
                                allow_recovery_state=(
                                    allow_retirement_recovery or already_truncated),
                                root_aliases=aliases)
                        else:
                            assert retirement_overlay_baseline is not None
                            plan = self.prepare_retirement_overlay_plan(
                                descriptor, record["source_kind"],
                                retirement_manifest, retirement_overlay_policy,
                                parent_fd=quarantine_fd, name=owned_name,
                                root_aliases=aliases)
                            if retirement_pretruncate_policy is not None:
                                open_record = \
                                    self.ensure_retirement_pretruncate_open(
                                        transaction_fd, record,
                                        plan.prepared_manifest)
                                if open_record is None:
                                    raise ArchiveError(
                                        "reviewed retirement requires durable 009b")
                        if any(binding.expected_size == 0
                               and rows[binding.path]["logical_bytes"] > 0
                               for binding in plan.regulars):
                            retirement_state["payload_write_started"] = True
                        failure_phase = "boundary"
                        if retirement_overlay_policy is None:
                            tombstone_manifest = self.execute_retirement_plan(
                                plan, volume, retirement_state,
                                final_retirement_boundary, boundary_hook=True)
                        else:
                            assert retirement_overlay_baseline is not None
                            boundary = final_retirement_boundary
                            if retirement_pretruncate_policy is not None:
                                assert plan is not None
                                def boundary() -> None:
                                    durable_open = \
                                        self.load_retirement_pretruncate_open(
                                            transaction_fd)
                                    if durable_open is None:
                                        raise ArchiveError(
                                            "retirement lost durable 009b")
                                    self.validate_retirement_pretruncate_open_record(
                                        transaction_fd, record, durable_open,
                                        require_current_gate=True,
                                        current_manifest=plan.prepared_manifest)
                                    final_retirement_boundary()
                            tombstone_manifest, runtime_overlay_proof = \
                                self.execute_retirement_overlay_plan(
                                    plan, volume, retirement_state,
                                    boundary,
                                    retirement_manifest,
                                    retirement_overlay_baseline["baseline_proof"],
                                    retirement_overlay_policy)
                        failure_phase = "retired"
                        tombstone_identity = list(identity(os.fstat(descriptor)))
                    finally:
                        if plan is not None:
                            plan.close()
                        if descriptor >= 0:
                            os.close(descriptor)
                    retained_name = self.find_owned_name(
                        quarantine_fd, expected,
                        {intent["quarantine_name"], intent["retired_name"],
                         intent["late_owned_name"]})
                    if tombstone_manifest["logical_bytes"] != 0:
                        raise ArchiveError("retained tombstone still contains regular-file data")
                    reclaimed_logical = (retirement_manifest["logical_bytes"]
                                         - tombstone_manifest["logical_bytes"])
                    reclaimed_allocated = (retirement_manifest["allocated_bytes"]
                                           - tombstone_manifest["allocated_bytes"])
                    self.fsync(quarantine_fd, "retained source tombstone namespace")
                    self.hook("after_source_retire_before_record")
                except BaseException:
                    if existing is None and not retirement_state["payload_write_started"]:
                        self.restore_pretruncate_source(
                            transaction_fd, record, intent, queue_fd)
                    raise
            finally:
                os.close(source_parent)
            payload = {
                "transaction_id": record["transaction_id"], "receipt": "PASS",
                "public_source": "ABSENT", "owned_payload": "ZEROED_TOMBSTONE",
                "foreign_entries_removed": 0, "tombstone_name": retained_name,
                "tombstone_identity": tombstone_identity,
                "tombstone_manifest": tombstone_manifest,
                "source_tree_sha256": record["source_manifest"]["tree_sha256"],
                "reclaimed_logical_bytes": reclaimed_logical,
                "reclaimed_allocated_bytes": reclaimed_allocated,
            }
            if overlay_policy is not None:
                payload.update(overlay_binding)
                payload["retirement_started_stage_sha256"] = \
                    sha256_bytes(canonical_json(retirement))
            if retirement_overlay_policy is not None:
                if runtime_overlay_proof is None:
                    raise ArchiveError(
                        "retirement overlay completed without runtime proof")
                payload.update(self.retirement_overlay_downstream_binding(
                    transaction_fd, record, runtime_overlay_proof))
            self.stage(transaction_fd, "source-deleted", payload)
            self.hook("after_source_deleted_record")
            return self.load_stage(transaction_fd, "source-deleted") or payload
        finally:
            os.close(quarantine_fd)

    def deletion_receipt_payload(self, record: dict[str, Any],
                                 retired: dict[str, Any],
                                 published: dict[str, Any], *,
                                 transaction_fd: int | None = None) -> dict[str, Any]:
        payload = {"schema": SCHEMA, "transaction_id": record["transaction_id"],
                   "allowlist_version": record["allowlist_version"],
                   "category": record["category"], "source": record["source"],
                   "source_tree_sha256": record["source_manifest"]["tree_sha256"],
                   "source_public": "ABSENT", "owned_payload": "ZEROED_TOMBSTONE",
                   "foreign_entries_removed": 0,
                   "tombstone_name": retired["tombstone_name"],
                   "tombstone_identity": retired["tombstone_identity"],
                   "tombstone_manifest": retired["tombstone_manifest"],
                   "reclaimed_logical_bytes": retired["reclaimed_logical_bytes"],
                   "reclaimed_allocated_bytes": retired["reclaimed_allocated_bytes"],
                   "volume_uuid": self.settings.volume_uuid}
        if published.get("provenance_overlay_proof") is not None:
            payload.update({
                "source_manifest": published["source_manifest"],
                "source_manifest_sha256": published["source_manifest_sha256"],
                "destination_manifest": published["destination_manifest"],
                "destination_manifest_sha256": published["destination_manifest_sha256"],
                "provenance_overlay_proof": published["provenance_overlay_proof"],
                "provenance_overlay_proof_sha256":
                    published["provenance_overlay_proof_sha256"],
            })
        if self.source_root_overlay_policy_for_record(record) is not None:
            if transaction_fd is None:
                raise ArchiveError("source-root deletion receipt lacks transaction binding")
            self.validate_source_root_overlay_downstream_binding(
                transaction_fd, record, retired, require_current_gate=True)
            retirement = self.load_stage(transaction_fd, "retirement-started")
            if retirement is None:
                raise ArchiveError("source-root deletion receipt lacks durable 009")
            payload.update(self.source_root_overlay_downstream_binding(
                transaction_fd, record, require_current_gate=True))
            payload["retirement_started_stage_sha256"] = \
                sha256_bytes(canonical_json(retirement))
            payload["source_deleted_stage_sha256"] = \
                sha256_bytes(canonical_json(retired))
        if (transaction_fd is not None
                and self.load_retirement_overlay_baseline(transaction_fd) is not None):
            self.validate_retirement_overlay_downstream_binding(
                transaction_fd, record, retired, retired["tombstone_manifest"])
            payload.update(self.retirement_overlay_downstream_binding(
                transaction_fd, record,
                retired["retirement_runtime_overlay_proof"]))
        return payload

    def ensure_deletion_receipt(self, transaction_fd: int, record: dict[str, Any],
                                retired: dict[str, Any], published: dict[str, Any],
                                volume: VolumeBinding, roots: ExternalRoots) -> dict[str, Any]:
        local = self.load_stage(transaction_fd, "deletion-receipt")
        name = f"{record['transaction_id']}-source-deletion.json"
        self.require_volume(volume)
        try:
            external = self.read_record(roots.manifests_fd, name)
        except FileNotFoundError:
            external = None
        if external is None:
            digest = self.write_record(
                roots.manifests_fd, name,
                self.deletion_receipt_payload(
                    record, retired, published, transaction_fd=transaction_fd),
                volume)
            self.require_volume(volume)
            self.fsync(roots.manifests_fd, "external source-deletion receipt root", volume)
            self.require_volume(volume)
            external = self.read_record(roots.manifests_fd, name)
        else:
            digest = sha256_bytes(canonical_json(external))
        if sha256_bytes(canonical_json(external)) != digest:
            raise ArchiveError("external deletion receipt digest changed")
        self.validate_external_overlay_binding(record, external, published)
        local_payload = {"transaction_id": record["transaction_id"],
                         "external_receipt": name,
                         "external_receipt_sha256": digest}
        if self.source_root_overlay_policy_for_record(record) is not None:
            self.validate_source_root_overlay_downstream_binding(
                transaction_fd, record, external, require_current_gate=True)
            # Recovery of an already externalized receipt binds local 011 to
            # that immutable receipt's gate proof, not whichever equivalent
            # receipt happens to be newest now.
            downstream_keys = set(self.source_root_overlay_downstream_binding(
                transaction_fd, record, require_current_gate=False))
            if self.load_retirement_overlay_baseline(transaction_fd) is not None:
                downstream_keys.update(self.retirement_overlay_downstream_binding(
                    transaction_fd, record,
                    external["retirement_runtime_overlay_proof"]))
            for key in downstream_keys:
                if key not in external:
                    raise ArchiveError(
                        f"external deletion receipt lacks chain field: {key}")
                local_payload[key] = external[key]
            local_payload["retirement_started_stage_sha256"] = \
                external["retirement_started_stage_sha256"]
            local_payload["source_deleted_stage_sha256"] = \
                external["source_deleted_stage_sha256"]
        if local is None:
            self.hook("after_external_deletion_receipt")
            self.stage(transaction_fd, "deletion-receipt", local_payload)
            durable_local = self.load_stage(
                transaction_fd, "deletion-receipt") or local_payload
            self.hook("after_deletion_receipt_record")
            return durable_local
        if self.source_root_overlay_policy_for_record(record) is not None:
            if (local.get("external_receipt") != name
                    or local.get("external_receipt_sha256") != digest
                    or local.get("retirement_started_stage_sha256")
                    != external["retirement_started_stage_sha256"]
                    or local.get("source_deleted_stage_sha256")
                    != external["source_deleted_stage_sha256"]):
                raise ArchiveError("local and external deletion receipt records differ")
            self.validate_source_root_overlay_downstream_binding(
                transaction_fd, record, local, require_current_gate=True)
            if self.load_retirement_overlay_baseline(transaction_fd) is not None:
                self.validate_retirement_overlay_downstream_binding(
                    transaction_fd, record, external,
                    external["tombstone_manifest"])
                expected_overlay = self.retirement_overlay_downstream_binding(
                    transaction_fd, record,
                    external["retirement_runtime_overlay_proof"])
                for key, value in expected_overlay.items():
                    if canonical_json(local.get(key)) != canonical_json(value):
                        raise ArchiveError(
                            f"local retirement overlay receipt differs: {key}")
        else:
            expected_local = {"schema": SCHEMA, "stage": "deletion-receipt",
                              **local_payload}
            if canonical_json(local) != canonical_json(expected_local):
                raise ArchiveError("local and external deletion receipt records differ")
        return local

    def drain_one(self, transaction_id: str, *, apply: bool = False) -> dict[str, Any]:
        lock_fd, queue_fd, transaction_fd, record = self.open_transaction(
            transaction_id, exclusive=apply, recover_partials=apply)
        try:
            if not apply:
                return {"status": "DRY_RUN", "transaction_id": transaction_id}
            if not self.settings.review_authorized:
                return {"status": "DEFERRED_REVIEW", "transaction_id": transaction_id}
            if self.settings.production and not self.production_authorization_valid():
                raise ArchiveError("production allowlist authorization digest is stale")
            volume: VolumeBinding | None = None
            source: SourceBinding | None = None
            try:
                volume = self.bind_volume()
                roots = self.external_roots(volume, record["category"])
                try:
                    if (self.settings.production
                            and self.source_root_overlay_policy_for_record(record) is not None):
                        self.verify_source_root_production_state_bound(
                            queue_fd, transaction_fd, record, volume, roots)
                    started = self.ensure_copy_started(transaction_fd, record, volume, roots)
                    published_record = self.load_stage(transaction_fd, "published")
                    if published_record is None:
                        if self.load_stage(transaction_fd, "copy-complete") is None:
                            source = self.bind_recorded_source(record, transaction_fd)
                        copied = self.ensure_copied(
                            transaction_fd, record, source, volume, roots, started
                        ) if source is not None else self.ensure_copied_existing(
                            transaction_fd, record, roots, started)
                        if source is not None:
                            source.close()
                            source = None
                        verified = self.ensure_verified(
                            transaction_fd, record, copied, lock_fd)
                        published = self.ensure_published(transaction_fd, record, volume, roots,
                                                          started, verified)
                    else:
                        verified = self.load_stage(transaction_fd, "verified")
                        if verified is None:
                            raise ArchiveError("published record lacks verified predecessor")
                        published = self.ensure_published(transaction_fd, record, volume, roots,
                                                          started, verified)
                    if self.source_root_overlay_policy_for_record(record) is not None:
                        # The reviewed transaction already has immutable 005.
                        # It must be validated byte-for-byte and is never
                        # rewritten; the fresh current gate proof begins at 008.
                        self.validate_legacy_external_manifest_005(
                            transaction_fd, record, roots, published)
                    else:
                        gate_proof = self.matching_full_gate_receipt()
                        self.ensure_external_manifest(
                            transaction_fd, record, volume, roots, published, gate_proof)
                    self.verify_final_boundary(
                        transaction_fd, record, published, volume, roots)
                    candidate = self.spec(record["candidate_id"])
                    retail_role = self.retail_role(candidate)
                    if record["data_class"] == "critical":
                        return {"status": "PASS", "transaction_id": transaction_id,
                                "destination_name": published["final_name"], "source": "PRESERVED"}
                    if retail_role == "main":
                        second_copy_proof = self.ensure_main_second_copy_proof(
                            transaction_fd, record, volume, roots)
                    elif retail_role == "sibling":
                        second_copy_proof = self.ensure_sibling_dependency_proof(
                            queue_fd, transaction_fd, record, volume, roots)
                    else:
                        second_copy_proof = self.ensure_not_required_second_copy(
                            transaction_fd, record)
                    if record["data_class"] == "redundant" and retail_role != "main":
                        raise ArchiveError("redundant deletion requires an independent second-copy proof")
                    intent = self.create_delete_intent(transaction_fd, record)
                    self.ensure_source_quarantined(transaction_fd, record, intent, queue_fd, volume)
                    retirement_started = self.load_stage(transaction_fd, "retirement-started")
                    if retail_role == "main" and retirement_started is None:
                        self.verify_main_second_copy_before_retirement(
                            transaction_fd, record, intent, queue_fd,
                            second_copy_proof)
                    self.ensure_retirement_started(
                        transaction_fd, record, second_copy_proof)
                    retirement_overlay_baseline = \
                        self.ensure_retirement_overlay_baseline(
                            transaction_fd, record, intent, queue_fd)

                    def final_retirement_boundary() -> None:
                        if retail_role == "main":
                            self.verify_prepared_against_immutable_proof(
                                second_copy_proof)
                            self.hook(
                                "after_final_prepared_proof",
                                transaction_id=record["transaction_id"])
                        self.verify_final_boundary(
                            transaction_fd, record, published, volume, roots)
                        if retirement_overlay_baseline is not None:
                            open_record = \
                                self.load_retirement_pretruncate_open(
                                    transaction_fd)
                            if open_record is None:
                                current_gate = self.matching_full_gate_receipt()
                                self.validate_full_gate_proof(
                                    current_gate, require_fresh=True)
                                self.require_equivalent_gate_proofs(
                                    retirement_overlay_baseline[
                                        "current_009a_gate_proof"],
                                    current_gate)
                            else:
                                self.validate_retirement_pretruncate_open_record(
                                    transaction_fd, record, open_record,
                                    require_current_gate=True)
                            # Descriptor-bound volume identity is deliberately
                            # the final callback operation before source-tree
                            # closure and the first adjacent fstat/ftruncate.
                            self.require_volume(volume)

                    retired = self.ensure_source_deleted(
                        transaction_fd, record, intent, queue_fd, volume,
                        final_retirement_boundary,
                        allow_retirement_recovery=retirement_started is not None)
                    self.ensure_deletion_receipt(
                        transaction_fd, record, retired, published, volume, roots)
                    return {"status": "PASS", "transaction_id": transaction_id,
                            "destination_name": published["final_name"], "source": "RETIRED",
                            "reclaimed_logical_bytes": retired["reclaimed_logical_bytes"],
                            "reclaimed_allocated_bytes": retired["reclaimed_allocated_bytes"]}
                finally:
                    roots.close()
            except DeferredVolume as error:
                return {"status": "DEFERRED_VOLUME", "transaction_id": transaction_id,
                        "reason": str(error)}
            finally:
                if source is not None:
                    source.close()
                if volume is not None:
                    volume.close()
        finally:
            os.close(transaction_fd)
            os.close(queue_fd)
            os.close(lock_fd)

    def ensure_copied_existing(self, transaction_fd: int, record: dict[str, Any],
                               roots: ExternalRoots, started: dict[str, Any]) -> dict[str, Any]:
        existing = self.load_stage(transaction_fd, "copy-complete")
        if existing is None:
            raise ArchiveError("copy recovery lost its source binding")
        stage_fd = self.open_stage(roots, started, record["source_kind"])
        try:
            try:
                payload_fd = stable_rebind(stage_fd, started["payload_name"],
                                           tuple(existing["payload_identity"]),
                                           record["source_kind"])
            except FileNotFoundError:
                final_info = os.stat(started["final_name"], dir_fd=roots.category_fd,
                                     follow_symlinks=False)
                payload_fd = open_leaf(roots.category_fd, started["final_name"],
                                       record["source_kind"])
                if identity(os.fstat(payload_fd)) != identity(final_info):
                    os.close(payload_fd)
                    raise ArchiveError("published recovery identity changed")
            try:
                current = manifest_bound(payload_fd, record["source_kind"])
            finally:
                os.close(payload_fd)
        finally:
            os.close(stage_fd)
        policy = self.provenance_overlay_policy_for_record(record)
        if policy is None:
            if not manifests_equal(existing["destination_manifest"], current):
                raise ArchiveError("copy-complete payload changed")
            return existing
        if canonical_json(existing["destination_manifest"]) != canonical_json(current):
            raise ArchiveError("overlay copy-complete physical manifest changed")
        stored_proof = existing.get("provenance_overlay_proof")
        proof = self.validate_copy_overlay(
            record, current, stored_proof,
            allow_legacy_002=existing if stored_proof is None else None)
        assert proof is not None
        binding = self.overlay_record_binding(record["source_manifest"], current, proof)
        if stored_proof is not None:
            self.validate_overlay_record_binding(record, existing, current)
            return existing
        return {**existing, **binding}

    def verify_published(self, transaction_id: str) -> dict[str, Any]:
        lock_fd, queue_fd, transaction_fd, record = self.open_transaction(
            transaction_id, exclusive=False, recover_partials=False)
        try:
            published = self.load_stage(transaction_fd, "published")
            manifest_record = self.load_stage(transaction_fd, "manifest-published")
            if published is None or manifest_record is None:
                return {"status": "PENDING", "transaction_id": transaction_id}
            volume = self.bind_volume()
            try:
                roots = self.external_roots(volume, record["category"])
                try:
                    self.verify_final_boundary(
                        transaction_fd, record, published, volume, roots)
                    manifest = published["destination_manifest"]
                    external = self.read_record(roots.manifests_fd,
                                                manifest_record["external_manifest"])
                    if external.get("category") != record["category"]:
                        raise ArchiveError("external manifest category is missing or wrong")
                    self.validate_external_overlay_binding(record, external, published)
                    deleted = self.load_stage(transaction_fd, "source-deleted")
                    receipt = self.load_stage(transaction_fd, "deletion-receipt")
                    if deleted is not None:
                        self.validate_existing_tombstone_against_immutable_plan(
                            transaction_fd, record, deleted)
                        if receipt is None:
                            return {"status": "PENDING_RECEIPT", "transaction_id": transaction_id}
                        external_receipt = self.read_record(
                            roots.manifests_fd, receipt["external_receipt"])
                        if sha256_bytes(canonical_json(external_receipt)) \
                                != receipt["external_receipt_sha256"]:
                            raise ArchiveError("external deletion receipt digest changed")
                        self.validate_external_overlay_binding(
                            record, external_receipt, published)
                        if self.source_root_overlay_policy_for_record(record) is not None:
                            self.validate_source_root_overlay_downstream_binding(
                                transaction_fd, record, deleted,
                                require_current_gate=True)
                            self.validate_source_root_overlay_downstream_binding(
                                transaction_fd, record, receipt,
                                require_current_gate=True)
                            self.validate_source_root_overlay_downstream_binding(
                                transaction_fd, record, external_receipt,
                                require_current_gate=True)
                            if (receipt.get("source_deleted_stage_sha256")
                                    != sha256_bytes(canonical_json(deleted))
                                    or external_receipt.get("source_deleted_stage_sha256")
                                    != receipt.get("source_deleted_stage_sha256")):
                                raise ArchiveError(
                                    "source-root deletion receipt stage binding differs")
                    return {"status": "PASS", "transaction_id": transaction_id,
                            "tree_sha256": manifest["tree_sha256"]}
                finally:
                    roots.close()
            finally:
                volume.close()
        finally:
            os.close(transaction_fd)
            os.close(queue_fd)
            os.close(lock_fd)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Safely archive completed OpenXRay artifacts.",
        epilog=ARCHIVER_LOCK_CONTRACT)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("audit")
    commands.add_parser("verify-production-state")
    commands.add_parser(
        "verify-historical-production-state",
        help=("read-only exact verification of the reviewed completed main "
              "retirement; never grants mutation authorization"))
    enqueue = commands.add_parser("enqueue")
    enqueue.add_argument("--candidate", required=True, choices=sorted(SPEC_BY_ID))
    drain = commands.add_parser("drain")
    drain.add_argument("--transaction", required=True)
    drain.add_argument("--apply", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--transaction", required=True)
    verify.add_argument("--staging", action="store_true", help=argparse.SUPPRESS)
    verify.add_argument("--archiver-lock-fd", type=int, help=argparse.SUPPRESS)
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    service = ArchiveService()
    try:
        if arguments.command == "audit":
            result: Any = service.audit()
        elif arguments.command == "verify-production-state":
            result = service.verify_production_state()
        elif arguments.command == "verify-historical-production-state":
            result = service.verify_historical_production_state()
        elif arguments.command == "enqueue":
            result = service.enqueue(arguments.candidate)
        elif arguments.command == "drain":
            result = service.drain_one(arguments.transaction, apply=arguments.apply)
        elif arguments.staging:
            result = service.independent_staging_manifest(
                arguments.transaction,
                inherited_lock_fd=arguments.archiver_lock_fd)
        else:
            result = service.verify_published(arguments.transaction)
        print(canonical_json(result).decode("ascii"), end="")
        return 0
    except (ArchiveError, OSError) as error:
        print(f"archive: FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
