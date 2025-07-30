import time
from typing import Optional, Dict
import threading


class InMemoryStorage:
    """
    In-memory storage verification kodlar va password reset kodlar uchun
    """

    def __init__(self):
        self._storage: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def set_code(self, key: str, code: str, expire_minutes: int) -> bool:
        try:
            with self._lock:
                expire_time = time.time() + (expire_minutes * 60)
                self._storage[key] = {
                    'code': code,
                    'expire_time': expire_time
                }
                print(f"[SET] {key} => {code} (expires in {expire_minutes} minutes)")  # <-- Qo‘shing
            return True
        except Exception as e:
            print(f"Storage xatolik: {e}")
            return False

    def get_code(self, key: str) -> Optional[str]:
        try:
            with self._lock:
                if key not in self._storage:
                    print(f"[GET] {key} not found")
                    return None

                data = self._storage[key]
                if time.time() > data['expire_time']:
                    # Muddati tugagan
                    print(f"[GET] {key} expired")
                    del self._storage[key]
                    return None

                print(f"[GET] {key} => {data['code']}")  # <-- Qo‘shing
                return data['code']
        except Exception as e:
            print(f"Storage xatolik: {e}")
            return None

    def delete_code(self, key: str) -> bool:
        """Kod o'chirish"""
        try:
            with self._lock:
                if key in self._storage:
                    del self._storage[key]
            return True
        except Exception as e:
            print(f"Storage xatolik: {e}")
            return False

    def cleanup_expired(self):
        """Muddati tugagan kodlarni tozalash"""
        try:
            with self._lock:
                current_time = time.time()
                expired_keys = [
                    key for key, data in self._storage.items()
                    if current_time > data['expire_time']
                ]
                for key in expired_keys:
                    del self._storage[key]
        except Exception as e:
            print(f"Cleanup xatolik: {e}")


# Global storage
storage = InMemoryStorage()
