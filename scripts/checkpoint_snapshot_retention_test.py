#!/usr/bin/env python3
"""Checkpoint snapshot ownership survives repeated timeline GC and retires safely."""
import json
from pathlib import Path
import tempfile
from semantic_consumer_contract_test import Fixture
from smacx_worker_manager import WorkerManager
from smacx_world_types import WorldIdentity


def main():
    with tempfile.TemporaryDirectory() as tmp:
        f = Fixture(Path(tmp))
        f.save()
        old = f.worlds.snapshot(f.scope, f.identity, journal_head_hash='a' * 64,
            journal_sequence=1, calculator_versions={'world': 'test'},
            pin_owner=('checkpoint', 'checkpoint-old'))
        for index in range(2):
            payload = f.worlds.verify_snapshot(old['snapshot_id'],
                journal_head_hash='a' * 64, journal_sequence=1)
            timeline = f'timeline-recovery-{index}'
            f.worlds.restore_projection_from_snapshot(f.scope, payload,
                target_timeline_id=timeline, journal_head_hash='a' * 64)
            f.worlds.discard_future(f.scope, timeline)
            assert Path(old['path']).is_file(), 'advertised checkpoint collected during restore'
        identity = WorldIdentity(f.scope.match_id, f.scope.perspective_id,
                                 timeline, f.identity.world_epoch)
        new = f.worlds.snapshot(f.scope, identity, journal_head_hash='b' * 64,
            journal_sequence=2, calculator_versions={'world': 'test'},
            pin_owner=('checkpoint', 'checkpoint-new'))
        # Another owner must survive checkpoint retirement, even when sharing
        # the same content. Publication precedes the production cleanup call.
        f.worlds.pin_snapshot(old['snapshot_id'], 'recovery', 'recovery-audit')
        manager = object.__new__(WorkerManager)
        manager.store = f.store
        manager._cleanup_recovery_snapshots(f.scope.match_id, 'checkpoint-new')
        assert Path(old['path']).is_file() and Path(new['path']).is_file()
        f.worlds.unpin_snapshot(old['snapshot_id'], 'recovery', 'recovery-audit')
        assert not Path(old['path']).exists()
        f.worlds.verify_snapshot(new['snapshot_id'], journal_head_hash='b' * 64, journal_sequence=2)
    print(json.dumps({'passed': True, 'repeated_timeline_gc_preserves_checkpoint': True,
        'replacement_cleanup_preserves_other_owner': True, 'final_owner_release_collects_old_snapshot': True}))


if __name__ == '__main__':
    main()
