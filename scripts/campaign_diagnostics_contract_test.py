#!/usr/bin/env python3
import json
from pathlib import Path
import tempfile
import zipfile
from smacx_store import SmacxStore
from smacx_diagnostics import DiagnosticWriter
from smacx_campaign_diagnostics import build_bundle

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);store=SmacxStore(root/"test.sqlite3")
    store.create_match(match_id="match-export-test",display_name="Export",mode="singleplayer")
    writer=DiagnosticWriter(root/"streams","match-export-test","sovereign")
    writer.emit("tool_requested",{"managed_name":"smac_decision","arguments":{}})
    foreign=DiagnosticWriter(root/"streams","match-other-test","sovereign")
    foreign.emit("tool_requested",{"private":"foreign secret"})
    with writer.path.open("ab") as output: output.write(b'{"unfinished":')
    result=build_bundle(store,"match-export-test",root/"diagnostics",[writer.directory])
    with zipfile.ZipFile(root/"diagnostics"/result["file_name"]) as archive:
        manifest=json.loads(archive.read("manifest.json"))
        assert any(row["reason"]=="partial_final_record" for row in manifest["gaps"])
        assert manifest["complete"] is False
        for name in archive.namelist():
            assert "foreign secret" not in archive.read(name).decode()
        assert json.loads(archive.read("state/matches.json"))[0]["match_id"]=="match-export-test"
        assert "smac_decision" in archive.read("gameplay.txt").decode()
    print(json.dumps({"event":"pass","payload":{"match_scope_isolated":True,
        "partial_tail_reported":True,"manifest_honest":True,"bundle_readable":True}}))
