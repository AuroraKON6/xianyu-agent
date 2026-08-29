from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping


def _now_iso(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() if now is None else now))


def build_scheduler_health_actions(
    account_profiles: Iterable[Mapping[str, Any]],
    scheduler_snapshot: Mapping[str, Any],
    *,
    stale_after_seconds: int = 1800,
    now: float | None = None,
) -> Dict[str, Any]:
    """Turn account scheduler state into a redacted operator action queue."""
    current = time.time() if now is None else float(now)
    accounts = [dict(profile) for profile in account_profiles or []]
    all_account_ids = {
        str(profile.get("cookie_id") or "").strip()
        for profile in accounts
        if str(profile.get("cookie_id") or "").strip()
    }
    enabled_account_ids = {
        str(profile.get("cookie_id") or "").strip()
        for profile in accounts
        if bool(profile.get("enabled", True)) and str(profile.get("cookie_id") or "").strip()
    }
    raw_profiles = (
        scheduler_snapshot.get("profiles")
        if isinstance(scheduler_snapshot.get("profiles"), dict)
        else {}
    )
    scheduler_account_ids = set()
    normalized_profiles: Dict[str, Dict[str, Any]] = {}
    for key, value in raw_profiles.items():
        profile = value if isinstance(value, dict) else {}
        account = str(profile.get("account") or profile.get("cookie_id") or key).strip()
        if not account:
            continue
        scheduler_account_ids.add(account)
        normalized_profiles[account] = dict(profile)

    missing_profiles = sorted(enabled_account_ids - scheduler_account_ids)
    due_profiles: list[str] = []
    failed_profiles: list[Dict[str, Any]] = []
    backing_off_profiles: list[Dict[str, Any]] = []
    stale_profiles: list[Dict[str, Any]] = []
    orphaned_profiles: list[str] = []
    actions: list[Dict[str, Any]] = []

    def add_action(
        priority: str,
        kind: str,
        account: str,
        title: str,
        detail: str,
        suggested_action: str,
    ) -> None:
        actions.append(
            {
                "priority": priority,
                "kind": kind,
                "account": account,
                "title": title,
                "detail": detail,
                "suggested_action": suggested_action,
            }
        )

    for account in missing_profiles:
        add_action(
            "medium",
            "scheduler_profile_missing",
            account,
            "Create message import scheduler profile",
            "This enabled account is not covered by scheduled message import.",
            "Bootstrap the account with the observe preset.",
        )

    for account, profile in sorted(normalized_profiles.items()):
        enabled = bool(profile.get("enabled"))
        if account in all_account_ids and account not in enabled_account_ids and enabled:
            orphaned_profiles.append(account)
            add_action(
                "low",
                "scheduler_profile_orphaned",
                account,
                "Pause scheduler for disabled account",
                "The account is disabled but its message import scheduler remains enabled.",
                "Pause this scheduler profile or re-enable the account after verification.",
            )
            continue
        if not enabled:
            continue

        failures = max(0, int(profile.get("consecutive_failures") or 0))
        backoff_seconds = max(0, int(profile.get("backoff_seconds") or 0))
        last_error = str(profile.get("last_error") or "").strip()
        due = bool(profile.get("due"))
        last_run_at = str(profile.get("last_run_at") or "").strip()

        if failures or last_error:
            failure = {
                "account": account,
                "consecutive_failures": failures,
                "backoff_seconds": backoff_seconds,
                "last_error": last_error,
            }
            failed_profiles.append(failure)
            add_action(
                "high",
                "scheduler_error",
                account,
                "Repair failed message import scheduler",
                last_error or f"The scheduler has failed {failures} consecutive time(s).",
                "Verify the account session and browser adapter, then run this profile once.",
            )

        if due:
            due_profiles.append(account)
            if not failures and not last_error:
                add_action(
                    "medium",
                    "scheduler_due",
                    account,
                    "Run due message import",
                    "The next scheduled polling time has arrived.",
                    "Run this scheduler profile now.",
                )
        elif backoff_seconds > 0:
            backing_off_profiles.append(
                {
                    "account": account,
                    "consecutive_failures": failures,
                    "backoff_seconds": backoff_seconds,
                }
            )
            if not failures and not last_error:
                add_action(
                    "low",
                    "scheduler_backoff",
                    account,
                    "Wait for scheduler backoff",
                    f"Automatic retry is delayed by {backoff_seconds} second(s).",
                    "Monitor the next automatic retry.",
                )

        if not last_run_at:
            stale_profiles.append({"account": account, "reason": "never_run", "age_seconds": None})
            if not due and not failures and not last_error:
                add_action(
                    "low",
                    "scheduler_never_run",
                    account,
                    "Verify scheduler first run",
                    "This enabled scheduler profile has not recorded a run yet.",
                    "Run this scheduler profile once and inspect its import result.",
                )
            continue

        last_run_timestamp = _parse_iso_timestamp(last_run_at)
        interval_seconds = max(30, int(profile.get("interval_seconds") or 300))
        stale_threshold = max(int(stale_after_seconds), interval_seconds * 3)
        if last_run_timestamp is not None and current - last_run_timestamp > stale_threshold:
            age_seconds = max(0, int(current - last_run_timestamp))
            stale_profiles.append({"account": account, "reason": "stale", "age_seconds": age_seconds})
            if not due and not failures and not last_error:
                add_action(
                    "low",
                    "scheduler_stale",
                    account,
                    "Review stale message import scheduler",
                    f"No successful scheduler record for {age_seconds} second(s).",
                    "Check the scheduler loop and run this profile once.",
                )

    priority_rank = {"high": 0, "medium": 1, "low": 2}
    actions.sort(key=lambda item: (priority_rank.get(str(item.get("priority")), 9), str(item.get("account"))))
    status = "healthy"
    if failed_profiles:
        status = "critical"
    elif missing_profiles or due_profiles or stale_profiles or orphaned_profiles or backing_off_profiles:
        status = "attention"
    elif not enabled_account_ids:
        status = "unconfigured"

    return {
        "status": status,
        "enabled_accounts": len(enabled_account_ids),
        "total_profiles": len(normalized_profiles),
        "missing_profiles": missing_profiles,
        "due_profiles": sorted(due_profiles),
        "failed_profiles": failed_profiles,
        "backing_off_profiles": backing_off_profiles,
        "stale_profiles": stale_profiles,
        "orphaned_profiles": sorted(orphaned_profiles),
        "actions": actions,
    }


def _parse_iso_timestamp(value: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.timestamp()
    except (TypeError, ValueError, OSError):
        return None


class MessageImportSchedulerStore:
    """Persist browser message import scheduler config and last run state."""

    BOOTSTRAP_PRESETS: Dict[str, Dict[str, Any]] = {
        "high_touch": {
            "enabled": True,
            "interval_seconds": 60,
            "include_read": False,
            "min_unread": 1,
            "max_conversations": 30,
            "messages_per_conversation": 30,
            "item_id": "",
        },
        "observe": {
            "enabled": True,
            "interval_seconds": 600,
            "include_read": False,
            "min_unread": 1,
            "max_conversations": 10,
            "messages_per_conversation": 10,
            "item_id": "",
        },
        "paused": {
            "enabled": False,
            "interval_seconds": 300,
            "include_read": False,
            "min_unread": 1,
            "max_conversations": 5,
            "messages_per_conversation": 10,
            "item_id": "",
        },
    }

    PROFILE_DEFAULTS: Dict[str, Any] = {
        "enabled": False,
        "cookie_id": "default",
        "interval_seconds": 300,
        "include_read": False,
        "min_unread": 1,
        "max_conversations": 20,
        "messages_per_conversation": 20,
        "item_id": "",
        "run_count": 0,
        "last_started_at": "",
        "last_run_at": "",
        "last_error": "",
        "last_result": {},
        "next_due_at": 0.0,
        "consecutive_failures": 0,
        "backoff_seconds": 0,
        "updated_at": "",
    }
    DEFAULTS: Dict[str, Any] = {
        **PROFILE_DEFAULTS,
        "active_cookie_id": "default",
        "profiles": {},
    }

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def snapshot(self, *, cookie_id: str | None = None, now: float | None = None) -> Dict[str, Any]:
        current = time.time() if now is None else float(now)
        state = self._load()
        profile = self._profile_snapshot(state, cookie_id or str(state.get("active_cookie_id") or "default"), current)
        profile["active_cookie_id"] = str(state.get("active_cookie_id") or profile.get("cookie_id") or "default")
        profile["profiles"] = {
            key: self._profile_snapshot(state, key, current)
            for key in sorted((state.get("profiles") or {}).keys())
        }
        profile["due_profiles"] = [
            key for key, value in profile["profiles"].items() if value.get("enabled") and value.get("due")
        ]
        profile["total_profiles"] = len(profile["profiles"])
        return profile

    def configure(self, updates: Mapping[str, Any], *, now: float | None = None) -> Dict[str, Any]:
        state = self._load()
        cookie_id = str(updates.get("cookie_id") or state.get("active_cookie_id") or "default")
        return self.configure_profile(cookie_id, updates, now=now, activate=True)

    def configure_profile(
        self,
        cookie_id: str,
        updates: Mapping[str, Any],
        *,
        now: float | None = None,
        activate: bool = True,
    ) -> Dict[str, Any]:
        current = time.time() if now is None else float(now)
        state = self._load()
        cookie_id = str(cookie_id or updates.get("cookie_id") or "default")
        profile = self._get_profile(state, cookie_id)
        for key in (
            "enabled",
            "cookie_id",
            "interval_seconds",
            "include_read",
            "min_unread",
            "max_conversations",
            "messages_per_conversation",
            "item_id",
        ):
            if key in updates:
                profile[key] = updates[key]

        profile = self._sanitize_profile(profile, fallback_cookie_id=cookie_id)
        profile["updated_at"] = _now_iso(current)
        if profile["enabled"]:
            profile["next_due_at"] = current
        state["profiles"][str(profile.get("cookie_id") or cookie_id)] = profile
        self._drop_unused_default_profile(state)
        if activate:
            state["active_cookie_id"] = str(profile.get("cookie_id") or cookie_id)
        self._save(state)
        return self.snapshot(cookie_id=str(profile.get("cookie_id") or cookie_id), now=current)

    def request_payload(self, *, cookie_id: str | None = None) -> Dict[str, Any]:
        state = self._load()
        profile = self._get_profile(state, cookie_id or str(state.get("active_cookie_id") or "default"))
        return {
            "cookie_id": str(profile.get("cookie_id") or "default"),
            "include_read": bool(profile.get("include_read")),
            "min_unread": int(profile.get("min_unread") or 1),
            "max_conversations": int(profile.get("max_conversations") or 20),
            "messages_per_conversation": int(profile.get("messages_per_conversation") or 20),
            "item_id": str(profile.get("item_id") or ""),
        }

    def list_profiles(self, *, now: float | None = None) -> Dict[str, Any]:
        current = time.time() if now is None else float(now)
        state = self._load()
        profiles = {
            key: self._profile_snapshot(state, key, current)
            for key in sorted((state.get("profiles") or {}).keys())
        }
        return {
            "active_cookie_id": str(state.get("active_cookie_id") or "default"),
            "profiles": profiles,
            "due_profiles": [key for key, value in profiles.items() if value.get("enabled") and value.get("due")],
            "total_profiles": len(profiles),
        }

    def due_profiles(self, *, now: float | None = None) -> list[str]:
        return list(self.list_profiles(now=now).get("due_profiles") or [])

    def bootstrap_from_account_profiles(
        self,
        account_profiles: Iterable[Mapping[str, Any]],
        *,
        preset: str = "observe",
        overwrite: bool = False,
        include_disabled: bool = False,
        dry_run: bool = False,
        now: float | None = None,
    ) -> Dict[str, Any]:
        current = time.time() if now is None else float(now)
        preset_name = str(preset or "observe")
        if preset_name not in self.BOOTSTRAP_PRESETS:
            preset_name = "observe"
        preset_updates = dict(self.BOOTSTRAP_PRESETS[preset_name])
        state = self._load()
        raw_profiles = state.get("profiles") if isinstance(state.get("profiles"), dict) else {}

        created = 0
        updated = 0
        skipped = 0
        planned_profiles = []
        first_changed_cookie_id = ""

        for account in sorted(account_profiles or [], key=lambda item: str(item.get("cookie_id") or "")):
            cookie_id = str(account.get("cookie_id") or "").strip()
            enabled_account = bool(account.get("enabled", True))
            exists = bool(cookie_id and cookie_id in raw_profiles)
            action = "skip"
            reason = ""
            candidate = {}

            if not cookie_id:
                skipped += 1
                reason = "missing_cookie_id"
            elif not enabled_account and not include_disabled:
                skipped += 1
                reason = "account_disabled"
            elif exists and not overwrite:
                skipped += 1
                reason = "profile_exists"
                candidate = self._sanitize_profile(raw_profiles.get(cookie_id) or {}, fallback_cookie_id=cookie_id)
            else:
                action = "update" if exists else "create"
                if action == "create":
                    created += 1
                else:
                    updated += 1
                base_profile = raw_profiles.get(cookie_id) if isinstance(raw_profiles.get(cookie_id), dict) else {}
                candidate = self._sanitize_profile(base_profile or {"cookie_id": cookie_id}, fallback_cookie_id=cookie_id)
                candidate.update(preset_updates)
                candidate["cookie_id"] = cookie_id
                candidate = self._sanitize_profile(candidate, fallback_cookie_id=cookie_id)
                candidate["updated_at"] = _now_iso(current)
                if candidate["enabled"]:
                    candidate["next_due_at"] = current
                if not first_changed_cookie_id:
                    first_changed_cookie_id = cookie_id
                if not dry_run:
                    raw_profiles[cookie_id] = candidate

            planned_profiles.append(
                {
                    "cookie_id": cookie_id,
                    "account_enabled": enabled_account,
                    "action": action,
                    "reason": reason,
                    "profile": _public_profile_bootstrap_snapshot(candidate),
                }
            )

        if not dry_run:
            state["profiles"] = raw_profiles
            active_cookie_id = str(state.get("active_cookie_id") or "")
            if first_changed_cookie_id and active_cookie_id == "default" and first_changed_cookie_id != "default":
                state["active_cookie_id"] = first_changed_cookie_id
            self._drop_unused_default_profile(state)
            self._save(state)

        return {
            "ok": True,
            "type": "message_import_scheduler_bootstrap",
            "preset": preset_name,
            "dry_run": bool(dry_run),
            "overwrite": bool(overwrite),
            "include_disabled": bool(include_disabled),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "profiles": planned_profiles,
            "scheduler": self.snapshot(now=current),
        }

    def record_started(self, *, cookie_id: str | None = None, now: float | None = None) -> Dict[str, Any]:
        current = time.time() if now is None else float(now)
        state = self._load()
        cookie_id = cookie_id or str(state.get("active_cookie_id") or "default")
        profile = self._get_profile(state, cookie_id)
        profile["last_started_at"] = _now_iso(current)
        profile["last_error"] = ""
        profile["updated_at"] = _now_iso(current)
        state["profiles"][str(profile.get("cookie_id") or cookie_id)] = profile
        self._save(state)
        return self.snapshot(cookie_id=str(profile.get("cookie_id") or cookie_id), now=current)

    def record_result(
        self,
        result: Mapping[str, Any],
        *,
        cookie_id: str | None = None,
        now: float | None = None,
    ) -> Dict[str, Any]:
        current = time.time() if now is None else float(now)
        state = self._load()
        cookie_id = cookie_id or str(result.get("cookie_id") or state.get("active_cookie_id") or "default")
        profile = self._get_profile(state, cookie_id)
        profile["run_count"] = int(profile.get("run_count") or 0) + 1
        profile["last_run_at"] = _now_iso(current)
        profile["last_error"] = ""
        profile["last_result"] = _summarize_import_result(result)
        profile["consecutive_failures"] = 0
        profile["backoff_seconds"] = 0
        profile["next_due_at"] = current + max(30, int(profile.get("interval_seconds") or 300))
        profile["updated_at"] = _now_iso(current)
        state["profiles"][str(profile.get("cookie_id") or cookie_id)] = profile
        self._save(state)
        return self.snapshot(cookie_id=str(profile.get("cookie_id") or cookie_id), now=current)

    def record_error(self, error: str, *, cookie_id: str | None = None, now: float | None = None) -> Dict[str, Any]:
        current = time.time() if now is None else float(now)
        state = self._load()
        cookie_id = cookie_id or str(state.get("active_cookie_id") or "default")
        profile = self._get_profile(state, cookie_id)
        interval = max(30, int(profile.get("interval_seconds") or 300))
        failures = int(profile.get("consecutive_failures") or 0) + 1
        backoff_seconds = min(interval * (2 ** (failures - 1)), 3600)
        profile["run_count"] = int(profile.get("run_count") or 0) + 1
        profile["last_run_at"] = _now_iso(current)
        profile["last_error"] = str(error or "")
        profile["last_result"] = {}
        profile["consecutive_failures"] = failures
        profile["backoff_seconds"] = backoff_seconds
        profile["next_due_at"] = current + backoff_seconds
        profile["updated_at"] = _now_iso(current)
        state["profiles"][str(profile.get("cookie_id") or cookie_id)] = profile
        self._save(state)
        return self.snapshot(cookie_id=str(profile.get("cookie_id") or cookie_id), now=current)

    def _load(self) -> Dict[str, Any]:
        state = dict(self.DEFAULTS)
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    state.update(raw)
            except Exception:
                pass
        state["active_cookie_id"] = str(state.get("active_cookie_id") or state.get("cookie_id") or "default")
        raw_profiles = state.get("profiles") if isinstance(state.get("profiles"), dict) else {}
        profiles: Dict[str, Dict[str, Any]] = {}
        for key, value in raw_profiles.items():
            if isinstance(value, dict):
                profiles[str(key or value.get("cookie_id") or "default")] = self._sanitize_profile(
                    value,
                    fallback_cookie_id=str(key or "default"),
                )
        if not profiles:
            profiles[state["active_cookie_id"]] = self._sanitize_profile(
                state,
                fallback_cookie_id=state["active_cookie_id"],
            )
        if state["active_cookie_id"] not in profiles:
            profiles[state["active_cookie_id"]] = self._sanitize_profile(
                {"cookie_id": state["active_cookie_id"]},
                fallback_cookie_id=state["active_cookie_id"],
            )
        state["profiles"] = profiles
        active_profile = profiles[state["active_cookie_id"]]
        for key in self.PROFILE_DEFAULTS:
            state[key] = active_profile.get(key, self.PROFILE_DEFAULTS[key])
        return state

    def _save(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        active_cookie_id = str(state.get("active_cookie_id") or state.get("cookie_id") or "default")
        raw_profiles = state.get("profiles") if isinstance(state.get("profiles"), dict) else {}
        profiles = {
            str(key): self._sanitize_profile(value if isinstance(value, dict) else {}, fallback_cookie_id=str(key))
            for key, value in raw_profiles.items()
        }
        if active_cookie_id not in profiles:
            profiles[active_cookie_id] = self._sanitize_profile(
                {"cookie_id": active_cookie_id},
                fallback_cookie_id=active_cookie_id,
            )
        payload: Dict[str, Any] = {
            **profiles[active_cookie_id],
            "active_cookie_id": active_cookie_id,
            "profiles": profiles,
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_profile(self, state: Dict[str, Any], cookie_id: str) -> Dict[str, Any]:
        profiles = state.setdefault("profiles", {})
        cookie_id = str(cookie_id or "default")
        if cookie_id not in profiles:
            profiles[cookie_id] = self._sanitize_profile({"cookie_id": cookie_id}, fallback_cookie_id=cookie_id)
        return dict(profiles[cookie_id])

    def _profile_snapshot(self, state: Dict[str, Any], cookie_id: str, current: float) -> Dict[str, Any]:
        profile = self._get_profile(state, cookie_id)
        profile["due"] = bool(profile.get("enabled")) and float(profile.get("next_due_at") or 0) <= current
        return profile

    def _sanitize_profile(self, profile: Mapping[str, Any], *, fallback_cookie_id: str) -> Dict[str, Any]:
        sanitized = dict(self.PROFILE_DEFAULTS)
        sanitized.update({key: value for key, value in dict(profile).items() if key in self.PROFILE_DEFAULTS})
        sanitized["enabled"] = bool(sanitized.get("enabled"))
        sanitized["cookie_id"] = str(sanitized.get("cookie_id") or fallback_cookie_id or "default")
        sanitized["interval_seconds"] = max(30, int(sanitized.get("interval_seconds") or 300))
        sanitized["include_read"] = bool(sanitized.get("include_read"))
        sanitized["min_unread"] = max(0, int(sanitized.get("min_unread") or 0))
        sanitized["max_conversations"] = max(0, int(sanitized.get("max_conversations") or 0))
        sanitized["messages_per_conversation"] = max(0, int(sanitized.get("messages_per_conversation") or 0))
        sanitized["item_id"] = str(sanitized.get("item_id") or "")
        sanitized["run_count"] = max(0, int(sanitized.get("run_count") or 0))
        sanitized["last_started_at"] = str(sanitized.get("last_started_at") or "")
        sanitized["last_run_at"] = str(sanitized.get("last_run_at") or "")
        sanitized["last_error"] = str(sanitized.get("last_error") or "")
        sanitized["last_result"] = sanitized.get("last_result") if isinstance(sanitized.get("last_result"), dict) else {}
        sanitized["next_due_at"] = float(sanitized.get("next_due_at") or 0.0)
        sanitized["consecutive_failures"] = max(0, int(sanitized.get("consecutive_failures") or 0))
        sanitized["backoff_seconds"] = max(0, int(sanitized.get("backoff_seconds") or 0))
        sanitized["updated_at"] = str(sanitized.get("updated_at") or "")
        return sanitized

    def _drop_unused_default_profile(self, state: Dict[str, Any]) -> None:
        profiles = state.get("profiles") if isinstance(state.get("profiles"), dict) else {}
        default = profiles.get("default") if isinstance(profiles.get("default"), dict) else None
        if not default or len(profiles) <= 1:
            return
        if (
            not default.get("enabled")
            and int(default.get("run_count") or 0) == 0
            and not default.get("updated_at")
            and not default.get("last_started_at")
            and not default.get("last_run_at")
            and not default.get("last_error")
            and not default.get("last_result")
        ):
            profiles.pop("default", None)


def _summarize_import_result(result: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "type": str(result.get("type") or ""),
        "cookie_id": str(result.get("cookie_id") or ""),
        "conversations_total": int(result.get("conversations_total") or 0),
        "conversations_selected": int(result.get("conversations_selected") or 0),
        "conversations_skipped_read": int(result.get("conversations_skipped_read") or 0),
        "conversations_skipped_invalid": int(result.get("conversations_skipped_invalid") or 0),
        "created": int(result.get("created") or 0),
        "duplicates": int(result.get("duplicates") or 0),
        "skipped_outbound": int(result.get("skipped_outbound") or 0),
        "skipped_invalid": int(result.get("skipped_invalid") or 0),
        "failed": int(result.get("failed") or 0),
    }


def _public_profile_bootstrap_snapshot(profile: Mapping[str, Any]) -> Dict[str, Any]:
    if not profile:
        return {}
    return {
        "enabled": bool(profile.get("enabled")),
        "interval_seconds": int(profile.get("interval_seconds") or 0),
        "include_read": bool(profile.get("include_read")),
        "min_unread": int(profile.get("min_unread") or 0),
        "max_conversations": int(profile.get("max_conversations") or 0),
        "messages_per_conversation": int(profile.get("messages_per_conversation") or 0),
    }
