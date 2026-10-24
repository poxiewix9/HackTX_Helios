"""Signed community reports: canonical bytes, Ed25519 sign / verify, reporter hash.

The mobile app signs the same canonical form (documented in `apps/mobile/README.md` and
in HANDOFFS): a compact JSON object with sorted keys, coordinates rounded to 6 decimals,
`created_at` as an ISO-8601 UTC timestamp with millisecond precision and a `Z` suffix
(JavaScript `Date.toISOString()` format). `reporter_hash` is the lowercase hex SHA-256 of
the raw 32-byte public key, so identity is never more than a key fingerprint.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from arco_schemas import CommunityReport, HazardClass, Point
from arco_schemas.blackboard import ReportType


def iso_ms_z(dt: datetime) -> str:
    """`2026-10-24T21:00:00.123Z` — millisecond precision, always UTC."""
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def reporter_hash_for(pubkey_b64: str) -> str:
    return hashlib.sha256(base64.b64decode(pubkey_b64)).hexdigest()


def canonical_bytes(
    *,
    report_id: UUID,
    lon: float,
    lat: float,
    report_type: ReportType,
    hazard_class: HazardClass | None,
    target_hazard_id: UUID | None,
    reporter_hash: str,
    created_at: datetime,
    note: str | None,
) -> bytes:
    payload: dict[str, Any] = {
        "id": str(report_id),
        "lon": round(lon, 6),
        "lat": round(lat, 6),
        "report_type": report_type,
        "hazard_class": hazard_class,
        "target_hazard_id": str(target_hazard_id) if target_hazard_id else None,
        "reporter_hash": reporter_hash,
        "created_at": iso_ms_z(created_at),
        "note": note,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def canonical_bytes_of(report: CommunityReport) -> bytes:
    lon, lat = report.geom.coordinates[0], report.geom.coordinates[1]
    return canonical_bytes(
        report_id=report.id,
        lon=lon,
        lat=lat,
        report_type=report.report_type,
        hazard_class=report.hazard_class,
        target_hazard_id=report.target_hazard_id,
        reporter_hash=report.reporter_hash,
        created_at=report.created_at,
        note=report.note,
    )


class DeviceKey:
    """A per-device Ed25519 key. The private half never leaves the device; this class
    exists for the simulator, tests and the dev relay."""

    def __init__(self, private: Ed25519PrivateKey | None = None) -> None:
        self._priv = private or Ed25519PrivateKey.generate()
        raw = self._priv.public_key().public_bytes_raw()
        self.pubkey_b64 = base64.b64encode(raw).decode()
        self.reporter_hash = hashlib.sha256(raw).hexdigest()

    @classmethod
    def from_seed(cls, seed: bytes) -> DeviceKey:
        """Deterministic key for scenarios (`sha256(label)` as the 32-byte seed)."""
        return cls(Ed25519PrivateKey.from_private_bytes(hashlib.sha256(seed).digest()))

    def sign(self, data: bytes) -> str:
        return base64.b64encode(self._priv.sign(data)).decode()

    def make_report(
        self,
        *,
        lon: float,
        lat: float,
        report_type: ReportType,
        hazard_class: HazardClass | None = None,
        target_hazard_id: UUID | None = None,
        note: str | None = None,
        created_at: datetime | None = None,
        report_id: UUID | None = None,
    ) -> CommunityReport:
        rid = report_id or uuid4()
        ts = created_at or datetime.now(UTC)
        lon, lat = round(lon, 6), round(lat, 6)
        data = canonical_bytes(
            report_id=rid,
            lon=lon,
            lat=lat,
            report_type=report_type,
            hazard_class=hazard_class,
            target_hazard_id=target_hazard_id,
            reporter_hash=self.reporter_hash,
            created_at=ts,
            note=note,
        )
        return CommunityReport(
            id=rid,
            geom=Point(coordinates=[lon, lat]),
            report_type=report_type,
            hazard_class=hazard_class,
            target_hazard_id=target_hazard_id,
            reporter_hash=self.reporter_hash,
            device_pubkey=self.pubkey_b64,
            signature=self.sign(data),
            created_at=ts,
            note=note,
        )


def verify_report(report: CommunityReport) -> tuple[bool, str]:
    """(valid, reason). Checks the reporter hash is the key's fingerprint and that the
    signature covers the canonical bytes of exactly what was posted."""
    try:
        raw = base64.b64decode(report.device_pubkey, validate=True)
        key = Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, TypeError) as exc:
        return False, f"bad public key: {exc}"
    if hashlib.sha256(raw).hexdigest() != report.reporter_hash:
        return False, "reporter_hash is not the fingerprint of device_pubkey"
    try:
        key.verify(base64.b64decode(report.signature, validate=True), canonical_bytes_of(report))
    except (InvalidSignature, ValueError, TypeError):
        return False, "signature does not match canonical report bytes"
    return True, "ok"
