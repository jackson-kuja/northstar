"""Local session recorder for replayable Northstar debugging."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_RECORDINGS_ROOT = Path(
    os.getenv(
        "LOCAL_SESSION_RECORDING_DIR",
        Path(__file__).resolve().parents[1] / "data" / "sessions",
    )
)
LOCAL_SESSION_RECORDING_ENABLED = (
    os.getenv("LOCAL_SESSION_RECORDING_ENABLED", "1").strip().lower()
    not in {"0", "false", "no", "off"}
)
LOCAL_SESSION_RECORDING_STORE_AUDIO = (
    os.getenv("LOCAL_SESSION_RECORDING_STORE_AUDIO", "0").strip().lower()
    in {"1", "true", "yes", "on"}
)

_MIME_EXTENSION_MAP = {
    "application/json": ".json",
    "audio/pcm": ".pcm",
    "audio/pcm;rate=16000": ".pcm",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "text/plain": ".txt",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    text = "".join(
        character.lower() if character.isalnum() else "_"
        for character in str(value or "").strip()
    ).strip("_")
    return text or "artifact"


def _estimate_decoded_size(base64_data: str) -> int:
    if not base64_data:
        return 0
    padding = base64_data.count("=")
    return max(0, (len(base64_data) * 3) // 4 - padding)


def _extension_for_mime(mime_type: str | None) -> str:
    normalized = str(mime_type or "").strip().lower()
    if normalized in _MIME_EXTENSION_MAP:
        return _MIME_EXTENSION_MAP[normalized]
    base_type = normalized.split(";", 1)[0]
    return _MIME_EXTENSION_MAP.get(base_type, ".bin")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {"kind": "bytes", "length": len(value)}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return str(value)


class SessionRecorder:
    """Stores a replayable per-session event log on local disk."""

    def __init__(
        self,
        session_id: str,
        *,
        root: Path | None = None,
        enabled: bool = LOCAL_SESSION_RECORDING_ENABLED,
        store_audio: bool = LOCAL_SESSION_RECORDING_STORE_AUDIO,
    ):
        self.session_id = session_id
        self.root = Path(root or DEFAULT_RECORDINGS_ROOT)
        self.enabled = enabled
        self.store_audio = store_audio
        self.session_dir = self.root / session_id
        self.artifacts_dir = self.session_dir / "artifacts"
        self.events_path = self.session_dir / "events.jsonl"
        self.meta_path = self.session_dir / "meta.json"
        self._event_count = 0
        self._closed = False
        self._lock = asyncio.Lock()
        self._started_at = _utcnow()

        if self.enabled:
            self.root.mkdir(parents=True, exist_ok=True)
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
            self._write_meta_sync(
                {
                    "session_id": self.session_id,
                    "status": "active",
                    "started_at": self._started_at,
                    "closed_at": None,
                    "last_event_at": None,
                    "event_count": 0,
                    "store_audio": self.store_audio,
                    "root": str(self.root),
                    "session_dir": str(self.session_dir),
                }
            )

    async def log_event(
        self,
        *,
        source: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        json_artifacts: dict[str, Any] | None = None,
        base64_artifacts: dict[str, dict[str, Any]] | None = None,
        blob_artifacts: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled or self._closed:
            return None

        async with self._lock:
            sequence = self._event_count + 1
            recorded_at = _utcnow()
            event: dict[str, Any] = {
                "seq": sequence,
                "recorded_at": recorded_at,
                "session_id": self.session_id,
                "source": source,
                "type": event_type,
                "payload": _json_safe(payload or {}),
            }

            artifacts = await asyncio.to_thread(
                self._write_artifacts_sync,
                sequence,
                recorded_at,
                json_artifacts or {},
                base64_artifacts or {},
                blob_artifacts or {},
            )
            if artifacts:
                event["artifacts"] = artifacts

            await asyncio.to_thread(self._append_event_sync, event)
            self._event_count = sequence
            await asyncio.to_thread(
                self._write_meta_sync,
                {
                    "session_id": self.session_id,
                    "status": "active",
                    "started_at": self._started_at,
                    "closed_at": None,
                    "last_event_at": recorded_at,
                    "event_count": self._event_count,
                    "store_audio": self.store_audio,
                    "root": str(self.root),
                    "session_dir": str(self.session_dir),
                },
            )
            return event

    async def close(self, *, status: str = "closed") -> None:
        if not self.enabled or self._closed:
            return

        async with self._lock:
            closed_at = _utcnow()
            await asyncio.to_thread(
                self._write_meta_sync,
                {
                    "session_id": self.session_id,
                    "status": status,
                    "started_at": self._started_at,
                    "closed_at": closed_at,
                    "last_event_at": closed_at,
                    "event_count": self._event_count,
                    "store_audio": self.store_audio,
                    "root": str(self.root),
                    "session_dir": str(self.session_dir),
                },
            )
            self._closed = True

    def _append_event_sync(self, event: dict[str, Any]) -> None:
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True))
            handle.write("\n")

    def _write_meta_sync(self, payload: dict[str, Any]) -> None:
        with self.meta_path.open("w", encoding="utf-8") as handle:
            json.dump(_json_safe(payload), handle, ensure_ascii=True, indent=2)

    def _write_artifacts_sync(
        self,
        sequence: int,
        recorded_at: str,
        json_artifacts: dict[str, Any],
        base64_artifacts: dict[str, dict[str, Any]],
        blob_artifacts: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        del recorded_at

        for name, payload in json_artifacts.items():
            relative_path = Path("artifacts") / _slug(name) / f"{sequence:05d}_{_slug(name)}.json"
            absolute_path = self.session_dir / relative_path
            absolute_path.parent.mkdir(parents=True, exist_ok=True)
            with absolute_path.open("w", encoding="utf-8") as handle:
                json.dump(_json_safe(payload), handle, ensure_ascii=True, indent=2)
            records.append(
                {
                    "name": name,
                    "kind": "json",
                    "path": str(relative_path),
                    "url": self.artifact_url(relative_path),
                }
            )

        for name, artifact in base64_artifacts.items():
            base64_data = str(artifact.get("data") or "")
            if not base64_data:
                continue
            mime_type = str(artifact.get("mime_type") or artifact.get("mimeType") or "")
            relative_path = (
                Path("artifacts")
                / _slug(name)
                / f"{sequence:05d}_{_slug(name)}{_extension_for_mime(mime_type)}"
            )
            absolute_path = self.session_dir / relative_path
            absolute_path.parent.mkdir(parents=True, exist_ok=True)
            absolute_path.write_bytes(base64.b64decode(base64_data))
            records.append(
                {
                    "name": name,
                    "kind": "base64_blob",
                    "mime_type": mime_type,
                    "path": str(relative_path),
                    "url": self.artifact_url(relative_path),
                    "size_bytes": _estimate_decoded_size(base64_data),
                }
            )

        for name, artifact in blob_artifacts.items():
            blob_data = artifact.get("data")
            if not isinstance(blob_data, (bytes, bytearray)):
                continue
            mime_type = str(artifact.get("mime_type") or artifact.get("mimeType") or "")
            relative_path = (
                Path("artifacts")
                / _slug(name)
                / f"{sequence:05d}_{_slug(name)}{_extension_for_mime(mime_type)}"
            )
            absolute_path = self.session_dir / relative_path
            absolute_path.parent.mkdir(parents=True, exist_ok=True)
            absolute_path.write_bytes(bytes(blob_data))
            records.append(
                {
                    "name": name,
                    "kind": "blob",
                    "mime_type": mime_type,
                    "path": str(relative_path),
                    "url": self.artifact_url(relative_path),
                    "size_bytes": len(blob_data),
                }
            )

        return records

    def artifact_url(self, relative_path: str | Path) -> str:
        return f"/debug/files/{self.session_id}/{str(relative_path)}"


def load_session_meta(
    session_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    recordings_root = Path(root or DEFAULT_RECORDINGS_ROOT)
    meta_path = recordings_root / session_id / "meta.json"
    if not meta_path.exists():
        return None
    with meta_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return _json_safe(data)


def list_recorded_sessions(
    *,
    root: Path | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    recordings_root = Path(root or DEFAULT_RECORDINGS_ROOT)
    if not recordings_root.exists():
        return []

    sessions: list[dict[str, Any]] = []
    for session_dir in recordings_root.iterdir():
        if not session_dir.is_dir():
            continue
        meta = load_session_meta(session_dir.name, root=recordings_root)
        if meta is not None:
            sessions.append(meta)

    sessions.sort(
        key=lambda item: str(item.get("started_at") or ""),
        reverse=True,
    )
    return sessions[: max(1, limit)]


def read_session_events(
    session_id: str,
    *,
    root: Path | None = None,
    limit: int = 200,
    after_seq: int = 0,
) -> list[dict[str, Any]]:
    recordings_root = Path(root or DEFAULT_RECORDINGS_ROOT)
    events_path = recordings_root / session_id / "events.jsonl"
    if not events_path.exists():
        return []

    events: list[dict[str, Any]] = []
    with events_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            if int(event.get("seq") or 0) <= after_seq:
                continue
            events.append(_json_safe(event))

    if limit <= 0:
        return events
    return events[-limit:]
