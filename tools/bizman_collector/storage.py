from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_json(path: Path, value: Any) -> None:
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(path, payload)


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.sha_root = self.root / "sha256"

    @staticmethod
    def _digest_from_ref(ref: str) -> str:
        algorithm, separator, digest = ref.partition(":")
        if algorithm != "sha256" or not separator or len(digest) != 64:
            raise ValueError(f"invalid artifact reference: {ref!r}")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise ValueError(f"invalid artifact reference: {ref!r}") from exc
        return digest.casefold()

    def _path_for_digest(self, digest: str) -> Path:
        return self.sha_root / digest[:2] / digest

    def put_bytes(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        target = self._path_for_digest(digest)
        if not target.exists():
            _atomic_write_bytes(target, data)
        return f"sha256:{digest}"

    def read_bytes(self, ref: str) -> bytes:
        return self._path_for_digest(self._digest_from_ref(ref)).read_bytes()

    @property
    def count(self) -> int:
        if not self.sha_root.exists():
            return 0
        return sum(
            1 for path in self.sha_root.glob("*/*") if path.is_file()
        )


class SessionWriter:
    def __init__(self, data_dir: Path, manifest: dict[str, Any]):
        self.data_dir = Path(data_dir)
        self.manifest = manifest
        self.session_id = str(manifest["session_id"])
        started_at = str(manifest["started_at"])
        event_date = started_at[:10]
        self.manifest_path = (
            self.data_dir
            / "sessions"
            / self.session_id
            / "manifest.json"
        )
        self.event_path = (
            self.data_dir
            / "events"
            / event_date
            / f"{self.session_id}.jsonl"
        )
        self.event_rel = self.event_path.relative_to(
            self.data_dir
        ).as_posix()
        self._artifact_refs: set[str] = set()
        self._event_handle = None
        self._closed = False
        self._write_manifest()

    def _write_manifest(self) -> None:
        self.manifest["artifact_count"] = len(self._artifact_refs)
        _atomic_write_json(self.manifest_path, self.manifest)

    def record_artifact(self, ref: str) -> None:
        self._artifact_refs.add(ref)
        self.manifest["artifact_count"] = len(self._artifact_refs)

    def add_warning(self, message: str) -> None:
        warnings = self.manifest.setdefault("warnings", [])
        if message in warnings:
            return
        if len(warnings) >= 1000:
            return
        warnings.append(message)

    def update_protocol_artifact(
        self,
        *,
        sha256: str,
        artifact_ref: str,
    ) -> None:
        protocol = self.manifest.setdefault("protocol", {})
        protocol["sha256"] = sha256
        protocol["artifact_ref"] = artifact_ref
        self.record_artifact(artifact_ref)
        self._write_manifest()

    def append_event(self, event: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("session writer is closed")
        if event.get("session_id") != self.session_id:
            raise ValueError(
                "event session_id does not match writer session"
            )
        if self._event_handle is None:
            self.event_path.parent.mkdir(parents=True, exist_ok=True)
            self._event_handle = self.event_path.open(
                "a",
                encoding="utf-8",
                buffering=1,
            )
            event_files = self.manifest.setdefault("event_files", [])
            if self.event_rel not in event_files:
                event_files.append(self.event_rel)
                self._write_manifest()

        self._event_handle.write(
            json.dumps(
                event,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        self._event_handle.flush()

        for key in ("request_body_ref", "response_body_ref"):
            ref = event.get(key)
            if isinstance(ref, str) and ref.startswith("sha256:"):
                self.record_artifact(ref)

    def finalize(self, *, status: str = "completed") -> None:
        if self._closed:
            return
        if self._event_handle is not None:
            self._event_handle.flush()
            os.fsync(self._event_handle.fileno())
            self._event_handle.close()
            self._event_handle = None
        self.manifest["ended_at"] = _utc_now_iso()
        self.manifest["status"] = status
        self._write_manifest()
        self._closed = True
