"""Canonical, repository-authored AutoCAD runtime-package bindings.

The adapter is not a single DLL.  Its loadable repository-owned runtime is a
small, profile-specific package whose three managed assemblies (and the modern
``.deps.json`` metadata) must remain one immutable unit.  This module keeps
the byte-level fingerprint deliberately independent from JSON serializers so
PowerShell, Python, and the netstandard adapter can reproduce it exactly.

This code never discovers an installed CAD product and never accepts vendor,
stub, or arbitrary third-party payload names.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from hashlib import sha256
from pathlib import Path
import re
from typing import Any
import unicodedata


RUNTIME_PACKAGE_FORMAT = "liang-pingfa/autocad-runtime-package/v1"
BUILD_RECEIPT_SCHEMA_VERSION = "liang-pingfa/autocad-adapter-build-receipt/v2"
BUILD_RECEIPT_FORMAT = "liang-pingfa/autocad-adapter-build-receipt-format/v1"

ADAPTER_ASSEMBLY = "LiangPingfa.NativeCad.AutoCAD.Adapter.dll"
CORE_ASSEMBLY = "LiangPingfa.NativeCad.Core.dll"
PROTOCOL_ASSEMBLY = "LiangPingfa.NativeCad.Protocol.dll"
ADAPTER_DEPS = "LiangPingfa.NativeCad.AutoCAD.Adapter.deps.json"

_RUNTIME_ASSEMBLIES = (
    ADAPTER_ASSEMBLY,
    CORE_ASSEMBLY,
    PROTOCOL_ASSEMBLY,
)
_AUXILIARY_FILES = (
    "LiangPingfa.NativeCad.AutoCAD.Adapter.pdb",
    "LiangPingfa.NativeCad.Core.pdb",
    "LiangPingfa.NativeCad.Protocol.pdb",
    "README.md",
    "native-bootstrap-context.template.json",
)
_PROFILE_TARGET_FRAMEWORKS = {
    "autocad2024": "net48",
    "autocad2025": "net8.0-windows",
    "autocad2026": "net10.0-windows",
}
_FORBIDDEN_PAYLOAD_NAMES = frozenset(
    {
        "acmgd.dll",
        "acdbmgd.dll",
        "accoremgd.dll",
        "liangpingfa.nativecad.autocad.apistubs.dll",
    }
)
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def target_framework_for_profile(profile: str) -> str:
    """Return the one supported target framework for an AutoCAD profile."""

    try:
        return _PROFILE_TARGET_FRAMEWORKS[profile]
    except KeyError as error:
        raise ValueError("runtime package profile is unsupported") from error


def required_runtime_component_names(profile: str) -> tuple[str, ...]:
    """Return the complete security-critical package set for ``profile``."""

    target_framework_for_profile(profile)
    if profile == "autocad2024":
        return _RUNTIME_ASSEMBLIES
    return _RUNTIME_ASSEMBLIES + (ADAPTER_DEPS,)


def allowed_package_file_names(profile: str) -> tuple[str, ...]:
    """Return every deployable file the receipt may allow for ``profile``."""

    return tuple(
        sorted(
            required_runtime_component_names(profile) + _AUXILIARY_FILES,
            key=_ordinal_key,
        )
    )


def runtime_package_fingerprint(
    *,
    format_version: str,
    profile: str,
    target_framework: str,
    components: Iterable[Mapping[str, Any]],
) -> str:
    """Hash the fixed cross-language runtime-package record sequence.

    Canonical bytes are UTF-8 with no BOM:

    ``format-version LF profile LF target-framework LF``, followed by one
    ``normalized-name TAB decimal-byte-size TAB lowercase-sha256 LF`` record
    per ordinal-sorted component.  File names are fixed ASCII names today,
    but NFC is still required before sorting and serialization so an apparent
    name collision cannot gain a second byte representation.
    """

    records = _normalized_component_records(
        profile=profile,
        target_framework=target_framework,
        components=components,
        require_exact_components=True,
    )
    if format_version != RUNTIME_PACKAGE_FORMAT:
        raise ValueError("runtime package format is unsupported")
    lines = [format_version, profile, target_framework]
    lines.extend(
        f"{record['name']}\t{record['byte_size']}\t{record['sha256']}"
        for record in records
    )
    return sha256(("\n".join(lines) + "\n").encode("utf-8", errors="strict")).hexdigest()


def normalize_runtime_package_descriptor(
    value: Mapping[str, Any],
    *,
    require_directory: bool,
    require_exact_components: bool = True,
) -> dict[str, Any]:
    """Validate and normalize one private runtime-package descriptor."""

    expected_keys = {
        "format_version",
        "profile",
        "target_framework",
        "fingerprint",
        "components",
    }
    if require_directory:
        expected_keys.add("directory")
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        raise ValueError("runtime package descriptor shape is invalid")
    profile = value.get("profile")
    target_framework = value.get("target_framework")
    fingerprint = value.get("fingerprint")
    if (
        not isinstance(profile, str)
        or not isinstance(target_framework, str)
        or not isinstance(fingerprint, str)
        or _SHA256.fullmatch(fingerprint) is None
    ):
        raise ValueError("runtime package descriptor is invalid")
    if require_directory:
        directory = value.get("directory")
        if (
            not isinstance(directory, str)
            or not directory
            or "\x00" in directory
            or any(ord(character) < 0x20 for character in directory)
        ):
            raise ValueError("runtime package directory is invalid")
    if profile in _PROFILE_TARGET_FRAMEWORKS:
        if target_framework != target_framework_for_profile(profile):
            raise ValueError("runtime package framework does not match profile")
    elif require_exact_components:
        # The active concrete adapter may not impersonate an unspecified
        # profile.  Test/future adapter descriptors may opt into the same
        # fixed component names with a separately reviewed profile.
        if not _safe_identifier(profile) or not _safe_identifier(target_framework):
            raise ValueError("runtime package profile is invalid")
    components = _normalized_component_records(
        profile=profile,
        target_framework=target_framework,
        components=_require_component_list(value.get("components")),
        require_exact_components=require_exact_components,
    )
    computed = runtime_package_fingerprint(
        format_version=value.get("format_version"),
        profile=profile,
        target_framework=target_framework,
        components=components,
    )
    if computed != fingerprint:
        raise ValueError("runtime package fingerprint differs")
    result: dict[str, Any] = {
        "format_version": RUNTIME_PACKAGE_FORMAT,
        "profile": profile,
        "target_framework": target_framework,
        "fingerprint": fingerprint,
        "components": components,
    }
    if require_directory:
        result["directory"] = value["directory"]
    return result


def runtime_component_by_name(
    runtime_package: Mapping[str, Any],
    name: str,
) -> dict[str, Any]:
    """Return one validated critical component record by its fixed name."""

    normalized = normalize_runtime_package_descriptor(
        runtime_package,
        require_directory="directory" in runtime_package,
    )
    for component in normalized["components"]:
        if component["name"] == name:
            return dict(component)
    raise ValueError("runtime package component is unavailable")


def build_runtime_package_descriptor(
    *,
    profile: str,
    components: Iterable[Mapping[str, Any]],
    directory: str | None = None,
    target_framework: str | None = None,
) -> dict[str, Any]:
    """Build a validated descriptor for package creation/tests.

    Operator configuration should be authored from the receipt instead of
    constructing this object by hand.  Keeping the helper here gives tests a
    single implementation for real generated package bytes.
    """

    framework = target_framework or target_framework_for_profile(profile)
    records = _normalized_component_records(
        profile=profile,
        target_framework=framework,
        components=components,
        require_exact_components=True,
    )
    descriptor: dict[str, Any] = {
        "format_version": RUNTIME_PACKAGE_FORMAT,
        "profile": profile,
        "target_framework": framework,
        "fingerprint": runtime_package_fingerprint(
            format_version=RUNTIME_PACKAGE_FORMAT,
            profile=profile,
            target_framework=framework,
            components=records,
        ),
        "components": records,
    }
    if directory is not None:
        descriptor["directory"] = directory
    return descriptor


def component_records_from_directory(
    directory: Path,
    *,
    profile: str,
) -> list[dict[str, Any]]:
    """Read only the fixed runtime set from a generated package directory."""

    records: list[dict[str, Any]] = []
    for name in required_runtime_component_names(profile):
        path = directory / name
        if (
            not path.is_file()
            or path.is_symlink()
            or path.parent != directory
        ):
            raise ValueError("runtime package component is unavailable")
        digest = sha256()
        with path.open("rb", buffering=0) as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        size = path.stat().st_size
        if size <= 0:
            raise ValueError("runtime package component is empty")
        records.append(
            {
                "name": name,
                "byte_size": size,
                "sha256": digest.hexdigest(),
            }
        )
    return _normalized_component_records(
        profile=profile,
        target_framework=target_framework_for_profile(profile),
        components=records,
        require_exact_components=True,
    )


def build_adapter_receipt(
    *,
    profile: str,
    configuration: str,
    runtime_package: Mapping[str, Any],
    allowed_files: Iterable[Mapping[str, Any]],
    sdk_input_fingerprints: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one canonical private receipt value for a generated package."""

    runtime = normalize_runtime_package_descriptor(
        runtime_package,
        require_directory=False,
    )
    if runtime["profile"] != profile:
        raise ValueError("receipt profile differs from runtime package")
    files = _normalized_allowed_file_records(profile, allowed_files, runtime)
    sdk = _normalized_sdk_fingerprints(sdk_input_fingerprints)
    if not isinstance(configuration, str) or configuration != "Release":
        raise ValueError("receipt configuration is unsupported")
    unsigned: dict[str, Any] = {
        "schema_version": BUILD_RECEIPT_SCHEMA_VERSION,
        "receipt_format_version": BUILD_RECEIPT_FORMAT,
        "profile": profile,
        "target_framework": runtime["target_framework"],
        "configuration": configuration,
        "runtime_package": runtime,
        "allowed_files": files,
        "sdk_input_fingerprints": sdk,
    }
    return {
        **unsigned,
        "integrity": {
            "algorithm": "SHA-256",
            "sha256": adapter_receipt_fingerprint(unsigned),
        },
    }


def adapter_receipt_fingerprint(receipt_without_integrity: Mapping[str, Any]) -> str:
    """Return the deterministic integrity hash for a receipt's semantic data."""

    required = {
        "schema_version",
        "receipt_format_version",
        "profile",
        "target_framework",
        "configuration",
        "runtime_package",
        "allowed_files",
        "sdk_input_fingerprints",
    }
    if set(receipt_without_integrity) != required:
        raise ValueError("receipt unsigned shape is invalid")
    runtime = normalize_runtime_package_descriptor(
        _require_mapping(receipt_without_integrity["runtime_package"]),
        require_directory=False,
    )
    profile = receipt_without_integrity["profile"]
    if (
        receipt_without_integrity["schema_version"] != BUILD_RECEIPT_SCHEMA_VERSION
        or receipt_without_integrity["receipt_format_version"] != BUILD_RECEIPT_FORMAT
        or not isinstance(profile, str)
        or runtime["profile"] != profile
        or receipt_without_integrity["target_framework"] != runtime["target_framework"]
        or receipt_without_integrity["configuration"] != "Release"
    ):
        raise ValueError("receipt header is invalid")
    files = _normalized_allowed_file_records(
        profile,
        _require_component_list(receipt_without_integrity["allowed_files"]),
        runtime,
    )
    sdk = _normalized_sdk_fingerprints(
        _require_mapping(receipt_without_integrity["sdk_input_fingerprints"])
    )
    lines = [
        BUILD_RECEIPT_FORMAT,
        BUILD_RECEIPT_SCHEMA_VERSION,
        profile,
        runtime["target_framework"],
        "Release",
        runtime["fingerprint"],
    ]
    lines.extend(
        f"{record['role']}\t{record['name']}\t{record['byte_size']}\t{record['sha256']}"
        for record in files
    )
    lines.extend(f"sdk\t{name}\t{sdk[name]}" for name in sorted(sdk, key=_ordinal_key))
    return sha256(("\n".join(lines) + "\n").encode("utf-8", errors="strict")).hexdigest()


def validate_adapter_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a private receipt without trusting its JSON formatting."""

    expected_keys = {
        "schema_version",
        "receipt_format_version",
        "profile",
        "target_framework",
        "configuration",
        "runtime_package",
        "allowed_files",
        "sdk_input_fingerprints",
        "integrity",
    }
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        raise ValueError("receipt shape is invalid")
    integrity = value["integrity"]
    if (
        not isinstance(integrity, Mapping)
        or set(integrity) != {"algorithm", "sha256"}
        or integrity["algorithm"] != "SHA-256"
        or not isinstance(integrity["sha256"], str)
        or _SHA256.fullmatch(integrity["sha256"]) is None
    ):
        raise ValueError("receipt integrity is invalid")
    unsigned = {key: value[key] for key in expected_keys if key != "integrity"}
    computed = adapter_receipt_fingerprint(unsigned)
    if computed != integrity["sha256"]:
        raise ValueError("receipt integrity differs")
    # Rebuild and compare semantic projections to reject a receipt whose
    # dependent fields merely happen to hash under an unsupported shape.
    expected = build_adapter_receipt(
        profile=value["profile"],
        configuration=value["configuration"],
        runtime_package=value["runtime_package"],
        allowed_files=value["allowed_files"],
        sdk_input_fingerprints=value["sdk_input_fingerprints"],
    )
    if expected != value:
        raise ValueError("receipt canonical projection differs")
    return expected


def verify_adapter_package_against_receipt(
    directory: Path,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Hash every receipt-listed file and reject extra/missing/case aliases."""

    checked = validate_adapter_receipt(receipt)
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("runtime package directory is unavailable")
    entries = list(directory.iterdir())
    if any(entry.is_dir() or entry.is_symlink() for entry in entries):
        raise ValueError("runtime package contains a non-file entry")
    names = [entry.name for entry in entries]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("runtime package contains case-colliding names")
    expected_names = [entry["name"] for entry in checked["allowed_files"]]
    if set(names) != set(expected_names) or len(names) != len(expected_names):
        raise ValueError("runtime package allowlist differs")
    for record in checked["allowed_files"]:
        path = directory / record["name"]
        digest = sha256()
        with path.open("rb", buffering=0) as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if path.stat().st_size != record["byte_size"] or digest.hexdigest() != record["sha256"]:
            raise ValueError("runtime package file differs from receipt")
    return checked


def _normalized_component_records(
    *,
    profile: str,
    target_framework: str,
    components: Iterable[Mapping[str, Any]],
    require_exact_components: bool,
) -> list[dict[str, Any]]:
    if not isinstance(profile, str) or not isinstance(target_framework, str):
        raise ValueError("runtime package profile is invalid")
    raw = list(components)
    records: list[dict[str, Any]] = []
    seen_folded: set[str] = set()
    for component in raw:
        if not isinstance(component, Mapping) or set(component) != {
            "name",
            "byte_size",
            "sha256",
        }:
            raise ValueError("runtime package component shape is invalid")
        name = component["name"]
        size = component["byte_size"]
        digest = component["sha256"]
        if (
            not isinstance(name, str)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
        ):
            raise ValueError("runtime package component is invalid")
        normalized_name = unicodedata.normalize("NFC", name)
        if (
            name != normalized_name
            or not name
            or "/" in name
            or "\\" in name
            or name in {".", ".."}
            or "\x00" in name
            or any(ord(character) < 0x20 for character in name)
            or name.casefold() in _FORBIDDEN_PAYLOAD_NAMES
            or name.casefold() in seen_folded
        ):
            raise ValueError("runtime package component name is unsafe")
        seen_folded.add(name.casefold())
        records.append(
            {
                "name": name,
                "byte_size": size,
                "sha256": digest,
            }
        )
    records.sort(key=lambda record: _ordinal_key(record["name"]))
    if require_exact_components and profile in _PROFILE_TARGET_FRAMEWORKS:
        if target_framework != target_framework_for_profile(profile):
            raise ValueError("runtime package framework differs")
        expected = required_runtime_component_names(profile)
        if tuple(record["name"] for record in records) != tuple(
            sorted(expected, key=_ordinal_key)
        ):
            raise ValueError("runtime package critical component set differs")
    elif require_exact_components:
        # A source-free fixture/future adapter still cannot place arbitrary
        # binaries in this repository-owned package contract. It may omit the
        # profile-specific deps metadata only when no known AutoCAD profile
        # is claimed.
        allowed = set(_RUNTIME_ASSEMBLIES + (ADAPTER_DEPS,))
        if (
            not {record["name"] for record in records}.issubset(allowed)
            or {record["name"] for record in records} != set(_RUNTIME_ASSEMBLIES)
        ):
            raise ValueError("runtime package component set is unlisted")
    return records


def _normalized_allowed_file_records(
    profile: str,
    files: Iterable[Mapping[str, Any]],
    runtime_package: Mapping[str, Any],
) -> list[dict[str, Any]]:
    expected_runtime = {
        record["name"]: record
        for record in normalize_runtime_package_descriptor(
            runtime_package,
            require_directory=False,
        )["components"]
    }
    raw = list(files)
    records: list[dict[str, Any]] = []
    seen_folded: set[str] = set()
    for entry in raw:
        if not isinstance(entry, Mapping) or set(entry) != {
            "name",
            "byte_size",
            "sha256",
            "role",
        }:
            raise ValueError("receipt allowed-file shape is invalid")
        name = entry["name"]
        size = entry["byte_size"]
        digest = entry["sha256"]
        role = entry["role"]
        if (
            not isinstance(name, str)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or role not in {"runtime", "auxiliary"}
            or unicodedata.normalize("NFC", name) != name
            or not name
            or "/" in name
            or "\\" in name
            or name.casefold() in seen_folded
            or name.casefold() in _FORBIDDEN_PAYLOAD_NAMES
        ):
            raise ValueError("receipt allowed file is invalid")
        seen_folded.add(name.casefold())
        records.append(
            {
                "name": name,
                "byte_size": size,
                "sha256": digest,
                "role": role,
            }
        )
    records.sort(key=lambda record: _ordinal_key(record["name"]))
    expected_names = allowed_package_file_names(profile)
    if tuple(record["name"] for record in records) != expected_names:
        raise ValueError("receipt package allowlist differs")
    for record in records:
        runtime_component = expected_runtime.get(record["name"])
        if runtime_component is None:
            if record["role"] != "auxiliary":
                raise ValueError("receipt auxiliary file has wrong role")
        elif (
            record["role"] != "runtime"
            or record["byte_size"] != runtime_component["byte_size"]
            or record["sha256"] != runtime_component["sha256"]
        ):
            raise ValueError("receipt runtime file differs from package")
    return records


def _normalized_sdk_fingerprints(value: Mapping[str, Any]) -> dict[str, str]:
    expected = ("AcCoreMgd.dll", "AcDbMgd.dll", "AcMgd.dll")
    if set(value) != set(expected):
        raise ValueError("receipt SDK input set is invalid")
    result: dict[str, str] = {}
    for name in expected:
        digest = value[name]
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise ValueError("receipt SDK input fingerprint is invalid")
        result[name] = digest
    return result


def _require_component_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("runtime package component list is invalid")
    return value


def _require_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("runtime package mapping is invalid")
    return value


def _ordinal_key(value: str) -> bytes:
    return value.encode("utf-8", errors="strict")


def _safe_identifier(value: str) -> bool:
    return bool(
        value
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value)
        and unicodedata.normalize("NFC", value) == value
    )
