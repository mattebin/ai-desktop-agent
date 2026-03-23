from __future__ import annotations

import threading

STOP_EVENT = threading.Event()

def request_stop():
    STOP_EVENT.set()

def clear_stop():
    STOP_EVENT.clear()

def stop_requested():
    return STOP_EVENT.is_set()

def start_emergency_stop_listener():
    try:
        import keyboard
    except Exception:
        print("[SAFETY] keyboard package not installed, F12 stop disabled.")
        return

    def _listen():
        try:
            keyboard.add_hotkey("f12", request_stop)
            print("[SAFETY] F12 emergency stop enabled.")
            keyboard.wait()
        except Exception as e:
            print(f"[SAFETY] Failed to start hotkey listener: {e}")

    t = threading.Thread(target=_listen, daemon=True)
    t.start()
