#!/usr/bin/env python3
"""Replay a retained early-game save in an isolated worker; never resume its campaign."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import SmacxStore, MemoryScope
from smacx_worker_manager import WorkerManager


def emit(event, **data):
    print(json.dumps({"event": event, **data}, separators=(",", ":")), flush=True)


def main():
    game = os.environ["SMACX_TEST_GAME_SOURCE"]
    saved_volume = os.environ["SMACX_TEST_SAVE_VOLUME"]
    saved_path = os.environ.get("SMACX_TEST_SAVE_PATH", "game/saves/auto/Autosave_2104.sav.zst")
    image = os.environ.get("SMACX_TEST_WORKER_IMAGE", "smacx-agent-worker:pr54-action-progress")
    docker = DockerClient()
    docker.inspect_volume(saved_volume)  # Do not silently create a missing source.
    with tempfile.TemporaryDirectory(prefix="smacx-action-replay-") as tmp:
        control = ControlPlane(SmacxStore(Path(tmp)/"state.sqlite3"), Path(tmp)/"secrets")
        manager = WorkerManager(control, docker, worker_image=image)
        worker = None
        try:
            source = manager.validate_game_source(game, display_name="Isolated replay source")
            runtime = manager.ensure_bundled_runtime()
            control.store.ensure_agent("agent-action-replay", "Action replay")
            match = control.create_solo_match("Action replay", "agent-action-replay",
                match_id="match-action-replay", faction_id=7, faction_name="Peacekeeping Forces")
            scope = MemoryScope("match-action-replay", "agent-action-replay", match["perspective"]["perspective_id"])
            worker = manager.provision_worker(scope, source["game_source_id"], runtime["runtime_id"],
                autostart={"enabled": False, "startup_save": "replay", "faction_id": 7})
            copy = """from pathlib import Path
import sys,shutil,os,pwd
source=Path('/original')/sys.argv[1]
target=Path('/isolated/game/saves/agent/match-action-replay/replay.sav.zst')
target.parent.mkdir(parents=True,exist_ok=True)
shutil.copyfile(source,target)
owner=pwd.getpwnam("smacx")
for item in [Path("/isolated"), *Path("/isolated").rglob("*")]: os.chown(item,owner.pw_uid,owner.pw_gid)
"""
            subprocess.run(["docker","run","--rm","--user","0","--entrypoint","python3",
                "-v",saved_volume+":/original:ro","-v",worker["data_volume"]+":/isolated",
                image,"-c",copy,saved_path],check=True)
            manager.start_worker(worker["instance_id"], timeout=300)
            def call(op, **args):
                return manager._native_request(worker["instance_id"],op,timeout=30,**args)
            def snapshot():
                return call("semantic_snapshot")["snapshot"]
            def execute(choice):
                snap=snapshot()
                args={k:v for k,v in choice.items() if k in ("command","unit_id","base_id","target_tile_id","response")}
                result=call("semantic_command",match_id=snap["match_id"],session_id=snap["session_id"],expected_revision=snap["revision"],**args)
                if result.get("queued"):
                    for _ in range(80):
                        result_status=call("action_status",action_id=result["action_id"])
                        if result_status.get("action",{}).get("status") != "pending":
                            result["receipt"]=result_status
                            break
                        time.sleep(.1)
                emit("action",choice=args,result=result)
                return result
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                snap=snapshot()
                if snap.get("protocol",{}).get("phase")=="turn": break
                choices=call("semantic_choices",kind="interaction").get("choices",[])
                safe=[c for c in choices if c.get("command") in ("acknowledge_popup","continue_game")]
                if not safe:
                    emit("unhandled",protocol=snap.get("protocol"),interaction=snap.get("interaction"),choices=choices)
                    raise AssertionError("replay requires an explicit interaction handler")
                execute(safe[0])
            units=call("list_units",scope="own",limit=300).get("items",[])
            emit("loaded",turn=snap.get("turn"),ready=snap.get("ready_unit_refs"),
                units=[{k:u.get(k) for k in ("id","name","tile_id","hp","moves_remaining","movement_scale","order_name","ready")} for u in units])
            scout=next((u for u in units if u.get("tile_id")==2755),None)
            if scout is None: raise AssertionError("saved state does not contain the reported Scout position")
            uid=scout["id"]
            for action,target in [("return_to_base",None),("move_unit",2835),("move_unit",2756),("move_unit",2715),("move_unit",2794),("skip_unit",None)]:
                catalog=call("semantic_choices",kind="unit_actions",unit_id=uid)
                choice=next((c for c in catalog.get("choices",[]) if c.get("command")==action and (target is None or c.get("target_tile_id")==target)),None)
                if choice is None:
                    emit("not_offered",command=action,target=target)
                    continue
                execute(choice)
                emit("after",turn=snapshot().get("turn"),ready=snapshot().get("ready_unit_refs"))
            emit("replay_complete")
        finally:
            if worker:
                try:
                    manager.park_worker(worker["instance_id"])
                except Exception as exc:
                    emit("cleanup_error",error=str(exc)[:300])
                for name,purpose in ((worker["network"]["secret_volume"],"worker-secret"),(worker["data_volume"],"worker-data")):
                    docker.require_owned(docker.inspect_volume(name),manager.installation_id,purpose=purpose)
                    docker.remove_volume(name)

if __name__ == "__main__": main()
