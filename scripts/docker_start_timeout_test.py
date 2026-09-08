#!/usr/bin/env python3
"""Startup has a bounded operation budget; ordinary reads stay short, no retry."""
from unittest.mock import patch
from smacx_docker import DockerClient, DockerUnavailable

for configured in (15, 120):
    client = DockerClient(timeout=configured)
    with patch.object(client, '_request', return_value=(204, b'')) as request:
        client.start_container('owned-container')
        assert request.call_count == 1
        assert request.call_args.kwargs['timeout'] == max(configured, 90)
    with patch.object(client, '_request', side_effect=DockerUnavailable('timeout')) as request:
        try: client.start_container('owned-container')
        except DockerUnavailable: pass
        else: raise AssertionError('uncertain startup must remain visible')
        assert request.call_count == 1
    assert client.timeout == configured
print('Docker startup budget: passed; bounded timeout, unchanged read budget, no mutation retry')
