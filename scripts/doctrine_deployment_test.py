#!/usr/bin/env python3
"""Check built worker DLL against the control image's reviewed contract before deployment.

Runs short-lived, network-disabled image probes without game data or credentials.
This verifies the compiled fingerprint, not predicted game mechanics.
"""
import argparse
import json
import re
import subprocess


def verify(worker_image, control_image):
    def probe(image, code, *args):
        result = subprocess.run(['docker', 'run', '--rm', '--network=none', '--read-only',
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--entrypoint=python3',
            image, '-c', code, *args], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError('Image compatibility probe failed: ' + image)
        return result.stdout.strip()
    approved = probe(control_image,
        'import json;from pathlib import Path;print(json.loads(Path("/opt/smacx/src/doctrine/engine-compatibility.json").read_text())["engine_source_sha256"])')
    if not re.fullmatch('[a-f0-9]{64}', approved):
        raise RuntimeError('Control image has no valid reviewed engine fingerprint')
    present = probe(worker_image,
        'import sys;from pathlib import Path;print(int(sys.argv[1].encode() in Path("/opt/smacx/bridge/thinker.dll").read_bytes()))', approved)
    return {'schema': 'smacx.doctrine-deployment.v1', 'verified': present == '1',
        'worker_image': worker_image, 'control_image': control_image,
        'approved_engine_sha256': approved,
        'reason': 'compiled_fingerprint_matches' if present == '1' else 'worker_engine_contract_mismatch'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker-image', default='smacx-agent-worker:dev')
    parser.add_argument('--control-image', default='smacx-agent-control:dev')
    args = parser.parse_args()
    report = verify(args.worker_image, args.control_image)
    print(json.dumps(report))
    raise SystemExit(0 if report['verified'] else 2)
