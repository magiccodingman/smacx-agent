#!/usr/bin/env python3
"""Verify saved-byte identity through compression and reject corrupt archives."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from smacx_worker_manager import SAVE_DIGEST_SCRIPT

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    saves = root / 'game' / 'saves'
    saves.mkdir(parents=True)
    path = saves / 'ckpt_0123456789abcdef_a.sav'
    content = bytes(range(256)) * 8192
    path.write_bytes(content)
    env = dict(os.environ, SMACX_STATE_ROOT=str(root), SMACX_SAVE_SLOT=path.stem)
    def run():
        return subprocess.run([sys.executable, '-c', SAVE_DIGEST_SCRIPT], env=env,
                              capture_output=True, text=True)
    expected = {'ok': True, 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}
    assert json.loads(run().stdout) == expected
    subprocess.run(['zstd', '-q', '--rm', str(path)], check=True)
    assert not path.exists()
    assert json.loads(run().stdout) == expected
    archive = path.with_suffix('.sav.zst')
    archive.write_bytes(archive.read_bytes()[:-5])
    failed = run()
    assert failed.returncode and not failed.stdout
    assert 'checkpoint_save_decompression_failed' in failed.stderr
    archive.unlink()
    assert 'checkpoint_save_file_missing' in run().stderr
print(json.dumps({'event': 'pass', 'plain_and_compressed_identity': True,
                  'corruption_and_missing_fail_closed': True}))
