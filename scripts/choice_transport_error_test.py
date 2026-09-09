"""Exercise production enumeration through the real bridge-error adapter."""
from unittest.mock import patch
import smacx_mcp as m

identity = dict(match_id='match-test', session_id='session-test', revision='1')
snapshot = dict(identity, turn=2, protocol={'phase': 'turn'})
for kind in ('production', 'base_management', 'base_citizens', 'research'):
    for failure in ('transport', 'string', 'structured', 'invalid_base'):
        calls = []
        def bridge(op, **kwargs):
            calls.append(op)
            if op == 'semantic_snapshot':
                return {'ok': True, 'snapshot': snapshot}
            assert op == 'semantic_choices'
            if failure == 'transport':
                raise m.BridgeUnavailable('Invalid JSON in native reply')
            error = 'invalid_base' if failure == 'invalid_base' else 'native_failure'
            return {'ok': False, 'error': {'code': error} if failure == 'structured' else error}
        with patch.object(m, 'bridge_request', side_effect=bridge), \
             patch.object(m, 'MANAGED_ATTACHED', False), \
             patch.object(m, '_sovereign_gameplay_gate', return_value=None), \
             patch.object(m, '_implicit_turn_handoff', return_value=None), \
             patch.object(m, '_resolve_managed_selectors', return_value=({'base_id': 0}, {})):
            result = m.smac_choices(kind=kind, base_ref='base-home')
        assert result['ok'] is False, result
        assert calls == ['semantic_snapshot', 'semantic_choices'], calls
        error = result['error']
        code = error.get('code') if isinstance(error, dict) else error
        assert code == ('game_not_connected' if failure == 'transport' else
                        'invalid_base' if failure == 'invalid_base' else 'native_failure'), result
        if failure == 'invalid_base' and kind != 'research':
            assert result['required_next']['tool'] == 'smac_world'
print('choice transport errors: 16 cases passed; no actions or blind retries')
