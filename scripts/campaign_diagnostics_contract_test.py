#!/usr/bin/env python3
import json
import gzip
from pathlib import Path
import tempfile
import zipfile
from smacx_store import SmacxStore
from smacx_diagnostics import DiagnosticWriter
from smacx_campaign_diagnostics import build_bundle, snapshot_journals, zstd_lines
from smacx_specialists import SpecialistTraceStore
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
    journal.append(scope,"specialist.mission_failed",{
        "mission_id":"mission-expired", "failure":"timed_out",
        "reason":"mission_deadline_expired"})
    snapshot_journals(root/"campaigns",writer.directory/"journal",scope.match_id)
    trace=SpecialistTraceStore(writer.directory/'specialists').write(
        {'match_id':scope.match_id,'timeline_id':'timeline-test','mission_id':'mission-trace','faculty':'reference'},
        'attempt-test', [{'kind':'mcp_call','timestamp_unix':123,
            'payload':{'instrument':'reference_query','query':'Artifact'}},
            {'kind':'attempt_outcome','returncode':1,'stdout':'Emitted specialist explanation','stderr':'test error'}],
        outcome='provider_failed',generation=1)
    try:
        list(zstd_lines(Path(trace['content_path']).read_bytes(),max_bytes=16))
    except ValueError as exc:
        assert str(exc)=='decoded_trace_byte_limit'
    else:raise AssertionError('decoded trace limit not enforced')
    # An index groups category before time; unordered LIMIT silently starves
    # the later category. Timestamp order must win over category/insertion.
    with store.transaction() as connection:
        connection.executemany(
            "INSERT INTO world_telemetry(telemetry_id,match_id,category,metric,recorded_unix) VALUES(?,?,?,?,?)",
            [(f"telemetry-{i}", "match-export-test", "a-noise", "sample", i)
             for i in reversed(range(10005))] + [
                ("telemetry-latest", "match-export-test", "z-runtime", "sample", 20000),
                ("telemetry-foreign", "match-other-test", "z-runtime", "sample", 30000)])
    result=build_bundle(store,"match-export-test",root/"diagnostics",[writer.directory])
    with zipfile.ZipFile(root/"diagnostics"/result["file_name"]) as archive:
        manifest=json.loads(archive.read("manifest.json"))
        assert any(row["reason"]=="partial_final_record" for row in manifest["gaps"])
        assert manifest["complete"] is False
        telemetry = json.loads(archive.read("state/world_telemetry.json"))
        assert len(telemetry) == 10000
        assert telemetry[0]["telemetry_id"] == "telemetry-latest"
        assert telemetry[-1]["recorded_unix"] == 6
        assert all(row["match_id"] == "match-export-test" for row in telemetry)
        assert manifest["state_windows"]["world_telemetry"] == {
            "selection": "most_recent", "order": "recorded_unix DESC, rowid DESC",
            "row_limit": 10000, "retained_rows": 10000,
            "newest_timestamp": 20000, "oldest_timestamp": 6}
        assert any(row.get("table") == "world_telemetry" and row["reason"] == "row_limit"
                   for row in manifest["gaps"])

        for name in archive.namelist():
            data=archive.read(name)
            if name.endswith(".gz"):data=gzip.decompress(data)
            if name.endswith('.zst'):data=b''.join(zstd_lines(data))
            assert "foreign secret" not in data.decode()
        assert json.loads(archive.read("state/matches.json"))[0]["match_id"]=="match-export-test"
        assert "smac_decision" in archive.read("gameplay.txt").decode()
        assert "Saved goal" in archive.read("gameplay.txt").decode()
        metrics=json.loads(archive.read("metrics.json"))
        assert metrics["failure_observations_by_layer"]["managed_tool_returned:native_rejected"]==1
        assert metrics["failure_observations_by_layer"]["journal_event:specialist.mission_failed:timed_out"]==1
        assert "mission-expired" in archive.read("gameplay.txt").decode()
        transcript=archive.read('gameplay.txt').decode()
        assert '[reference-specialist]' in transcript and 'Emitted specialist explanation' in transcript
        assert '123.0 [reference-specialist]' in transcript
        assert metrics['failure_observations_by_layer']['specialist_attempt:nonzero_exit']==1
        assert metrics['actor_counts']['reference-specialist']==2
        assert any(name.endswith(".jsonl.gz") for name in archive.namelist())
    # A damaged compressed member must remain downloadable as evidence rather
    # than turning the entire campaign export into an HTTP failure.
    damaged=writer.directory/"damaged.jsonl.gz"
    damaged.write_bytes(bytes.fromhex("1f8b08000000000000ff")+b"\xff"*24)
    damaged_zstd=writer.directory/'damaged.jsonl.zst'
    damaged_zstd.write_bytes(b'not a zstd stream')
    damaged_result=build_bundle(store,"match-export-test",root/"diagnostics",[writer.directory])
    with zipfile.ZipFile(root/"diagnostics"/damaged_result["file_name"]) as archive:
        damaged_manifest=json.loads(archive.read("manifest.json"))
        assert any(row.get("file")==damaged.name and row["reason"]=="partial_compressed_tail"
                   for row in damaged_manifest["gaps"])
        assert any(name.endswith("damaged.jsonl.gz") for name in archive.namelist())
        assert any(row.get('file')==damaged_zstd.name and row['reason']=='invalid_zstd_trace'
                   for row in damaged_manifest['gaps'])
        assert any(name.endswith('damaged.jsonl.zst') for name in archive.namelist())
    print(json.dumps({"event":"pass","payload":{"match_scope_isolated":True,
        "partial_tail_reported":True,"manifest_honest":True,"bundle_readable":True}}))
