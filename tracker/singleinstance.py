"""Verhindert, dass die App doppelt läuft (Windows-Mutex).

Die Sperre wird gehalten, solange der Prozess läuft – beim Self-Update gibt die alte Version sie
gezielt frei, damit die neue starten kann, während die alte noch auf deren Rückmeldung wartet.
"""
import ctypes
import time

NAME = 'GameReleaseTracker.SingleInstance'
ERROR_ALREADY_EXISTS = 183
_handle = None


def acquire(wait_seconds=0):
    """True, wenn diese Instanz die Sperre bekommen hat (ggf. bis zu wait_seconds warten)."""
    global _handle
    try:
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        return True
    deadline = time.time() + wait_seconds
    while True:
        handle = kernel32.CreateMutexW(None, False, NAME)
        if kernel32.GetLastError() != ERROR_ALREADY_EXISTS:
            _handle = handle
            return True
        kernel32.CloseHandle(handle)
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def release():
    global _handle
    if _handle:
        try:
            ctypes.windll.kernel32.CloseHandle(_handle)
        except AttributeError:
            pass
        _handle = None
