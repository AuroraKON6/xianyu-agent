from __future__ import annotations

import json
import time
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Mapping, Optional


CONNECTION_STATUSES = {
    "unknown",
    "disconnected",
    "connecting",
    "connected",
    "degraded",
    "manual_verification",
    "error",
}


def default_runtime_state(cookie_id: str) -> Dict[str, Any]:
    return {
        "cookie_id": str(cookie_id or "").strip(),
        "connection_status": "unknown",
        "last_heartbeat_at": 0.0,
        "last_message_at": 0.0,
        "last_auth_verified_at": 0.0,
        "last_token_refresh_at": 0.0,
        "last_cookie_refresh_at": 0.0,
        "token_fresh": False,
        "cookie_fresh": False,
        "reconnect_count": 0,
        "last_error": "",
        "manual_takeover_chats": [],
        "pending_verification_reason": "",
        "updated_at": 0.0,
    }


class AccountRuntimeStore:
    """Small JSON-backed account runtime state store.

    This is intentionally separate from account profiles: profiles describe what
    an account is configured to do, runtime describes what is happening now.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = RLock()
        self._states: Dict[str, Dict[str, Any]] = {}
        self._load()

    def list_public_states(self, cookie_ids: Optional[list[str]] = None) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            ids = list(cookie_ids) if cookie_ids is not None else sorted(self._states)
            return {cookie_id: self.get_public_state(cookie_id) for cookie_id in ids}

    def get_public_state(self, cookie_id: str) -> Dict[str, Any]:
        with self._lock:
            state = self._state_for(cookie_id)
            return self._public_state(state)

    def record_connection(self, cookie_id: str, status: str, *, timestamp: float | None = None) -> Dict[str, Any]:
        clean_status = str(status or "unknown").strip()
        if clean_status not in CONNECTION_STATUSES:
            raise ValueError(f"unsupported connection status: {clean_status}")
        return self._update(cookie_id, {"connection_status": clean_status}, timestamp=timestamp)

    def record_heartbeat(self, cookie_id: str, *, timestamp: float | None = None) -> Dict[str, Any]:
        ts = self._timestamp(timestamp)
        return self._update(cookie_id, {"last_heartbeat_at": ts, "connection_status": "connected"}, timestamp=ts)

    def record_message(self, cookie_id: str, *, timestamp: float | None = None) -> Dict[str, Any]:
        ts = self._timestamp(timestamp)
        return self._update(cookie_id, {"last_message_at": ts}, timestamp=ts)

    def record_authenticated_activity(
        self,
        cookie_id: str,
        *,
        timestamp: float | None = None,
    ) -> Dict[str, Any]:
        """Record a successful authenticated platform operation.

        A successful order/listing sync proves that the account session and its
        credentials were accepted at this timestamp. It does not claim that the
        credential was rotated or refreshed.
        """
        ts = self._timestamp(timestamp)
        return self._update(
            cookie_id,
            {
                "connection_status": "connected",
                "last_heartbeat_at": ts,
                "last_auth_verified_at": ts,
                "token_fresh": True,
                "cookie_fresh": True,
                "last_error": "",
                "pending_verification_reason": "",
            },
            timestamp=ts,
        )

    def record_token_refresh(
        self,
        cookie_id: str,
        *,
        fresh: bool = True,
        timestamp: float | None = None,
    ) -> Dict[str, Any]:
        ts = self._timestamp(timestamp)
        return self._update(cookie_id, {"last_token_refresh_at": ts, "token_fresh": bool(fresh)}, timestamp=ts)

    def record_cookie_refresh(
        self,
        cookie_id: str,
        *,
        fresh: bool = True,
        timestamp: float | None = None,
    ) -> Dict[str, Any]:
        ts = self._timestamp(timestamp)
        return self._update(cookie_id, {"last_cookie_refresh_at": ts, "cookie_fresh": bool(fresh)}, timestamp=ts)

    def record_reconnect(self, cookie_id: str, *, error: str = "", timestamp: float | None = None) -> Dict[str, Any]:
        with self._lock:
            state = self._state_for(cookie_id)
            state["reconnect_count"] = int(state.get("reconnect_count") or 0) + 1
            if error:
                state["last_error"] = str(error)[:200]
            state["connection_status"] = "connecting"
            state["updated_at"] = self._timestamp(timestamp)
            self._persist()
            return self._public_state(state)

    def record_error(
        self,
        cookie_id: str,
        error: str,
        *,
        pending_verification_reason: str = "",
        timestamp: float | None = None,
    ) -> Dict[str, Any]:
        return self._update(
            cookie_id,
            {
                "connection_status": "error",
                "last_error": str(error or "")[:200],
                "pending_verification_reason": str(pending_verification_reason or "")[:200],
            },
            timestamp=timestamp,
        )

    def set_manual_takeover(
        self,
        cookie_id: str,
        *,
        chat_id: str,
        reason: str = "",
        until: float | None = None,
        timestamp: float | None = None,
    ) -> Dict[str, Any]:
        with self._lock:
            state = self._state_for(cookie_id)
            chats = [item for item in state.get("manual_takeover_chats") or [] if isinstance(item, Mapping)]
            chat_text = str(chat_id or "").strip()
            chats = [item for item in chats if str(item.get("chat_id") or "") != chat_text]
            chats.append(
                {
                    "chat_id": chat_text,
                    "reason": str(reason or "")[:200],
                    "until": float(until or 0),
                    "updated_at": self._timestamp(timestamp),
                }
            )
            state["manual_takeover_chats"] = chats
            state["updated_at"] = self._timestamp(timestamp)
            self._persist()
            return self._public_state(state)

    def clear_manual_takeover(self, cookie_id: str, chat_id: str) -> Dict[str, Any]:
        with self._lock:
            state = self._state_for(cookie_id)
            chat_text = str(chat_id or "").strip()
            state["manual_takeover_chats"] = [
                item
                for item in state.get("manual_takeover_chats") or []
                if isinstance(item, Mapping) and str(item.get("chat_id") or "") != chat_text
            ]
            state["updated_at"] = time.time()
            self._persist()
            return self._public_state(state)

    def _update(self, cookie_id: str, values: Mapping[str, Any], *, timestamp: float | None = None) -> Dict[str, Any]:
        with self._lock:
            state = self._state_for(cookie_id)
            state.update(dict(values))
            state["updated_at"] = self._timestamp(timestamp)
            self._persist()
            return self._public_state(state)

    def _state_for(self, cookie_id: str) -> Dict[str, Any]:
        clean_cookie_id = str(cookie_id or "").strip()
        if not clean_cookie_id:
            raise ValueError("cookie_id is required")
        state = self._states.setdefault(clean_cookie_id, default_runtime_state(clean_cookie_id))
        return state

    def _public_state(self, state: Mapping[str, Any]) -> Dict[str, Any]:
        raw_chats = [item for item in state.get("manual_takeover_chats") or [] if isinstance(item, Mapping)]
        public_chats = [
            {
                "chat_ref": f"manual_{index + 1}",
                "reason": str(item.get("reason") or "")[:200],
                "until": float(item.get("until") or 0),
                "updated_at": float(item.get("updated_at") or 0),
            }
            for index, item in enumerate(raw_chats)
        ]
        return {
            "cookie_id": str(state.get("cookie_id") or ""),
            "connection_status": str(state.get("connection_status") or "unknown"),
            "last_heartbeat_at": float(state.get("last_heartbeat_at") or 0),
            "last_message_at": float(state.get("last_message_at") or 0),
            "last_auth_verified_at": float(state.get("last_auth_verified_at") or 0),
            "last_token_refresh_at": float(state.get("last_token_refresh_at") or 0),
            "last_cookie_refresh_at": float(state.get("last_cookie_refresh_at") or 0),
            "token_fresh": bool(state.get("token_fresh")),
            "cookie_fresh": bool(state.get("cookie_fresh")),
            "reconnect_count": int(state.get("reconnect_count") or 0),
            "last_error": str(state.get("last_error") or "")[:200],
            "manual_takeover_count": len(public_chats),
            "manual_takeover_chats": public_chats,
            "pending_verification_reason": str(state.get("pending_verification_reason") or "")[:200],
            "updated_at": float(state.get("updated_at") or 0),
        }

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, Mapping):
            return
        raw_states = data.get("accounts") if isinstance(data.get("accounts"), Mapping) else data
        if not isinstance(raw_states, Mapping):
            return
        for cookie_id, state in raw_states.items():
            if not isinstance(state, Mapping):
                continue
            clean_id = str(cookie_id or state.get("cookie_id") or "").strip()
            if not clean_id:
                continue
            merged = default_runtime_state(clean_id)
            merged.update(dict(state))
            merged["cookie_id"] = clean_id
            if merged.get("connection_status") not in CONNECTION_STATUSES:
                merged["connection_status"] = "unknown"
            self._states[clean_id] = merged

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps({"accounts": self._states}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)

    @staticmethod
    def _timestamp(timestamp: float | None) -> float:
        return time.time() if timestamp is None else float(timestamp)
