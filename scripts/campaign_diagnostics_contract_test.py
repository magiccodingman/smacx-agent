#!/usr/bin/env python3
import json
import gzip
from pathlib import Path
import tempfile
import zipfile
from smacx_store import SmacxStore
from smacx_diagnostics import DiagnosticWriter
from smacx_campaign_diagnostics import build_bundle, snapshot_journals
from smacx_journal import CampaignJournal
from smacx_store import MemoryScope

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);store=SmacxStore(root/"test.sqlite3")
    store.create_match(match_id="match-export-test",display_name="Export",mode="singleplayer")
    writer=DiagnosticWriter(root/"streams","match-export-test","sovereign")
    writer.emit("tool_requested",{"managed_name":"smac_decision","arguments":{}})
    foreign=DiagnosticWriter(root/"streams","match-other-test","sovereign")
    foreign.emit("tool_requested",{"private":"foreign secret"})
    with writer.path.open("ab") as output: output.write(b'{"unfinished":')
    packed=DiagnosticWriter(root/"streams","match-export-test","native-bridge",compress=True)
    packed.emit("managed_tool_returned",{"tool":"smac_execute_choice","result":{"ok":False,"error":{"code":"native_rejected"}}})
    journal=CampaignJournal(root/"campaigns")
    scope=MemoryScope("match-export-test","agent-test","perspective-test")
    event=journal.append(scope,"memory.goal",{"record":{"goal_key":"goal-test","title":"Saved goal"}})
    snapshot_journals(root/"campaigns",writer.directory/"journal",scope.match_id)
    result=build_bundle(store,"match-export-test",root/"diagnostics",[writer.directory])
    with zipfile.ZipFile(root/"diagnostics"/result["file_name"]) as archive:
        manifest=json.loads(archive.read("manifest.json"))
        assert any(row["reason"]=="partial_final_record" for row in manifest["gaps"])
        assert manifest["complete"] is False
        for name in archive.namelist():
            data=archive.read(name)
            if name.endswith(".gz"):data=gzip.decompress(data)
            assert "foreign secret" not in data.decode()
        assert json.loads(archive.read("state/matches.json"))[0]["match_id"]=="match-export-test"
        assert "smac_decision" in archive.read("gameplay.txt").decode()
        assert "Saved goal" in archive.read("gameplay.txt").decode()
        metrics=json.loads(archive.read("metrics.json"))
        assert metrics["failure_observations_by_layer"]["managed_tool_returned:native_rejected"]==1
        assert any(name.endswith(".jsonl.gz") for name in archive.namelist())
    # A damaged compressed member must remain downloadable as evidence rather
    # than turning the entire campaign export into an HTTP failure.
    damaged=writer.directory/"damaged.jsonl.gz"
    damaged.write_bytes(bytes.fromhex("1f8b08000000000000ff")+b"\xff"*24)
    damaged_result=build_bundle(store,"match-export-test",root/"diagnostics",[writer.directory])
    with zipfile.ZipFile(root/"diagnostics"/damaged_result["file_name"]) as archive:
        damaged_manifest=json.loads(archive.read("manifest.json"))
        assert any(row.get("file")==damaged.name and row["reason"]=="partial_compressed_tail"
                   for row in damaged_manifest["gaps"])
        assert any(name.endswith("damaged.jsonl.gz") for name in archive.namelist())
    print(json.dumps({"event":"pass","payload":{"match_scope_isolated":True,
        "partial_tail_reported":True,"manifest_honest":True,"bundle_readable":True}}))
