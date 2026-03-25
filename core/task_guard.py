import threading
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class CoreTaskToken:
    token_id: int
    task_name: str


class CoreTaskGuard:
    def __init__(self):
        self._mu = threading.Lock()
        self._current: Optional[CoreTaskToken] = None
        self._next_id = 1

    def try_acquire(self, task_name: str) -> Tuple[Optional[CoreTaskToken], Optional[str]]:
        with self._mu:
            if self._current is not None:
                return None, self._current.task_name
            token = CoreTaskToken(self._next_id, task_name)
            self._next_id += 1
            self._current = token
            return token, None

    def release(self, token: Optional[CoreTaskToken]) -> bool:
        if token is None:
            return False
        with self._mu:
            if self._current is None or self._current.token_id != token.token_id:
                return False
            self._current = None
            return True

    def current_task_name(self) -> Optional[str]:
        with self._mu:
            return None if self._current is None else self._current.task_name


core_task_guard = CoreTaskGuard()
