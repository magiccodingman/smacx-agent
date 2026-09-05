#!/usr/bin/env python3
"""Explicit review-time registration; never auto-approve a runtime ruleset."""
import hashlib,json,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
files=sorted([*root.glob('bridge/src/*.cpp'),*root.glob('bridge/src/*.h')])
manifest=''.join(p.name+':'+hashlib.sha256(p.read_bytes()).hexdigest()+'\n' for p in files)
value={'engine_source_sha256':hashlib.sha256(manifest.encode()).hexdigest(),'profile':'thinker-smacx-doctrine.v1',
       'scope':'reviewed bridge source plus compatible public alpha rules; unsupported gameplay INI overrides fail closed'}
target=root/'src/doctrine/engine-compatibility.json'
if '--register-reviewed' in sys.argv:target.write_text(json.dumps(value,indent=2)+'\n')
else:assert json.loads(target.read_text())==value,'Engine source changed: review compatibility before registering.'
print(json.dumps(value))
