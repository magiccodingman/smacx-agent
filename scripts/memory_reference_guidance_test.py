#!/usr/bin/env python3
"""Provider schemas distinguish actor, observation and journal namespaces."""
from smacx_memory_contract import contract

claim = contract("claim")["input"]["properties"]
actor = claim["asserted_by_actor_id"]
event = claim["source_event_id"]
goal = contract("goal")["input"]["properties"]

assert actor["pattern"].startswith("^(?:faction-")
assert "actor-*" in actor["description"]
assert "Agent, seat, session, unit and base references are not actor IDs" in actor["description"]
assert "journal-*" in event["description"]
assert "observation-* IDs are not journal evidence IDs" in event["description"]
assert "description" not in goal["parent_goal_id"]
print("PASS: memory schemas separate actor IDs, journal evidence and record references")
