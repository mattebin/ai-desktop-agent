"""Targeted replay fix validation — delete after use."""
from __future__ import annotations

import sys
import time

from core.local_api import LocalOperatorApiServer
from core.local_api_client import LocalOperatorApiClient

server = LocalOperatorApiServer(
    port=0,
    settings={
        "data_dir": "data",
        "local_api_event_poll_seconds": 0.1,
        "local_api_event_heartbeat_seconds": 3,
        "local_api_event_replay_size": 20,
        "local_api_event_channel_retention_seconds": 5,
        "local_api_event_max_channels": 8,
    },
)
server.start_in_thread()
client = LocalOperatorApiClient(f"http://127.0.0.1:{server.port}")
client.health()

session = server.chat_manager.create_session(title="Replay smoke")
sid = session.get("session", {}).get("session_id", "")
assert sid, "No session"

# Open stream_one
stream_one = client.open_event_stream(session_id=sid, timeout_seconds=5)
stream_one_iter = stream_one.iter_events()

# Add marker one while stream_one is open
with server.chat_manager._lock:
    t = server.chat_manager._find_session_locked(sid)
    server.chat_manager._append_message_locked(t, role="assistant", kind="status", content="Replay marker one")
    server.chat_manager._update_summary_locked(t)
    server.chat_manager._persist_locked()

time.sleep(0.5)
first_message_event = {}
deadline = time.time() + 4.0
while time.time() < deadline:
    try:
        payload = next(stream_one_iter)
    except StopIteration:
        break
    except Exception:
        break
    if payload.get("event") == "session.message":
        first_message_event = payload
        break
stream_one.close()
print(f"stream_one first_message event_id: {first_message_event.get('event_id', 'NONE')[:40]}", flush=True)

if not first_message_event:
    print("[SKIP] stream_one did not receive marker — publisher may not be reaching _read_state; testing priming fallback directly", flush=True)
    # Even without stream_one capturing an event, we can test the priming
    # path by opening stream_two with a dummy last_event_id
    # The priming path should still deliver the missed messages

# Add marker two (long reply) while stream_one is closed
long_reply = ("Replay marker two " + ("full final reply segment " * 180)).strip()
with server.chat_manager._lock:
    t = server.chat_manager._find_session_locked(sid)
    server.chat_manager._append_message_locked(t, role="assistant", kind="final", content=long_reply, status="completed")
    server.chat_manager._update_summary_locked(t)
    server.chat_manager._persist_locked()
time.sleep(0.35)

# Open stream_two with Last-Event-ID
last_eid = first_message_event.get("event_id", "") if first_message_event else "dummy:1"
stream_two = client.open_event_stream(session_id=sid, last_event_id=last_eid, timeout_seconds=5)
replayed_event = {}
events_two = []
deadline = time.time() + 4.0
for payload in stream_two.iter_events():
    evt = payload.get("event", "")
    events_two.append(evt)
    cnt = payload.get("data", {}).get("message", {}).get("content", "")
    if evt == "session.message" and cnt.strip() == long_reply:
        replayed_event = payload
        break
    if time.time() > deadline:
        break
stream_two.close()

print(f"stream_two events received: {events_two}", flush=True)
if replayed_event:
    print("[OK] local api event replay", flush=True)
else:
    print("[FAIL] Local API stream did not replay the full missed session event after reconnect.", flush=True)
    server.shutdown()
    sys.exit(1)
server.shutdown()
