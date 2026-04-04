from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock


@dataclass
class LockInfo:
    resource_id: str
    user_id: str
    username: str
    activity_type: str
    session_id: str
    locked_at: datetime
    last_heartbeat: datetime
    expires_at: datetime

    def as_dict(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "user_id": self.user_id,
            "username": self.username,
            "activity_type": self.activity_type,
            "session_id": self.session_id,
            "locked_at": self.locked_at.isoformat(),
            "last_heartbeat": self.last_heartbeat.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


class TransformLockManager:
    def __init__(self) -> None:
        self._locks: dict[str, LockInfo] = {}
        self._lock = RLock()

    def _ttl(self, activity: str) -> int:
        if activity == "viewing":
            return 120
        if activity == "upload":
            return 300
        return 300

    def _is_expired(self, lock: LockInfo) -> bool:
        return datetime.utcnow() > lock.expires_at

    def acquire(
        self,
        resource_id: str,
        user_id: str,
        username: str,
        session_id: str,
        activity_type: str = "transform",
    ) -> tuple[bool, dict | None]:
        now = datetime.utcnow()
        ttl = timedelta(seconds=self._ttl(activity_type))
        with self._lock:
            current = self._locks.get(resource_id)
            if current and self._is_expired(current):
                self._locks.pop(resource_id, None)
                current = None

            if current is None:
                info = LockInfo(
                    resource_id=resource_id,
                    user_id=user_id,
                    username=username,
                    activity_type=activity_type,
                    session_id=session_id,
                    locked_at=now,
                    last_heartbeat=now,
                    expires_at=now + ttl,
                )
                self._locks[resource_id] = info
                return True, info.as_dict()

            if current.user_id == user_id:
                current.last_heartbeat = now
                current.expires_at = now + ttl
                current.activity_type = activity_type
                current.session_id = session_id
                return True, current.as_dict()

            return False, current.as_dict()

    def refresh(self, resource_id: str, user_id: str) -> bool:
        with self._lock:
            current = self._locks.get(resource_id)
            if not current or self._is_expired(current):
                self._locks.pop(resource_id, None)
                return False
            if current.user_id != user_id:
                return False
            now = datetime.utcnow()
            current.last_heartbeat = now
            current.expires_at = now + timedelta(seconds=self._ttl(current.activity_type))
            return True

    def release(self, resource_id: str, user_id: str) -> bool:
        with self._lock:
            current = self._locks.get(resource_id)
            if current is None:
                return True
            if current.user_id != user_id:
                return False
            self._locks.pop(resource_id, None)
            return True

    def status(self, resource_id: str) -> dict | None:
        with self._lock:
            current = self._locks.get(resource_id)
            if not current:
                return None
            if self._is_expired(current):
                self._locks.pop(resource_id, None)
                return None
            return current.as_dict()


lock_manager = TransformLockManager()
