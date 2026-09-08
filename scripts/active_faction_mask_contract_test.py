"""Managed active-roster validation and worker environment contract."""
import json

from smacx_store import InvalidRecord
from smacx_worker_manager import WorkerManager


configured = WorkerManager._autostart({
    "enabled": True,
    "faction_id": 1,
    "faction_roster": list(range(7)),
    "active_faction_mask": 0x3E,
})
assert configured["active_faction_mask"] == 0x3E
assert configured["faction_roster"] == list(range(7))

for invalid in (True, 0, 1, 0xFF, "62"):
    try:
        WorkerManager._autostart({"active_faction_mask": invalid})
    except InvalidRecord as exc:
        assert str(exc) == "invalid_worker_active_faction_mask"
    else:
        raise AssertionError(f"accepted invalid active mask: {invalid!r}")

try:
    WorkerManager._autostart({
        "enabled": True, "faction_id": 1, "active_faction_mask": 0x04,
    })
except InvalidRecord as exc:
    assert str(exc) == "worker_faction_inactive"
else:
    raise AssertionError("accepted a solo worker whose sovereign faction is inactive")

print(json.dumps({
    "ok": True,
    "five_active_slots_mask": 0x3E,
    "invalid_masks_rejected": True,
    "inactive_sovereign_rejected": True,
}))
