#!/usr/bin/env python3
"""Native queue convention remains explicit without manufacturing missing counts."""
from smacx_mcp import _production_catalog_context
for count in (1, 2, 10):
    q = _production_catalog_context({'queue': {'entries': count, 'capacity': 10}})['queue']
    assert q['includes_current_item'] is True and q['items_after_current'] == count - 1
    assert q['entries'] == count
for count in (None, True, 0, -1, 11, '1'):
    q = _production_catalog_context({'queue': {'entries': count}})['queue']
    assert 'includes_current_item' not in q and 'items_after_current' not in q
assert set(_production_catalog_context({})) == {"selection_completion_boundary"}
print('production queue context passed')
