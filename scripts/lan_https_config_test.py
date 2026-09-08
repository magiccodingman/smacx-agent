"""Exercise the real edge entrypoint with an isolated Caddyfile and fake Caddy."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ENTRYPOINT = Path(__file__).resolve().parents[1] / 'edge/entrypoint.sh'


class LanHttpsConfigTests(unittest.TestCase):
    def render(self, **settings):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'entrypoint'
            script.write_text(ENTRYPOINT.read_text().replace('/tmp/Caddyfile', str(root / 'Caddyfile')))
            caddy = root / 'caddy'
            caddy.write_text('#!/bin/sh\ncat "' + str(root / 'Caddyfile') + '"\n')
            caddy.chmod(0o755)
            env = {key: value for key, value in os.environ.items() if not key.startswith('SMACX_')}
            env.update(settings, PATH=directory + ':' + env['PATH'])
            return subprocess.run(['sh', str(script)], env=env, capture_output=True, text=True)

    def test_default_keeps_http(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('redir ', result.stdout)
        self.assertNotIn('tls internal', result.stdout)

    def test_lan_and_public_coexist(self):
        result = self.render(SMACX_LAN_HTTPS_HOST='10.26.26.104', SMACX_PUBLIC_HOSTNAME='planet.example.net')
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ('default_sni 10.26.26.104', 'skip_install_trust',
                     '@lan host 10.26.26.104', 'redir @lan https://10.26.26.104{uri} 308',
                     'redir @public https://planet.example.net{uri} 308',
                     'tls internal', 'planet.example.net {', 'reverse_proxy control-center:8080'):
            self.assertIn(text, result.stdout)
        self.assertEqual(result.stdout.count('tls internal'), 1)

    def test_custom_published_port(self):
        result = self.render(SMACX_LAN_HTTPS_HOST='planet.lan', SMACX_LAN_HTTPS_PORT='8443')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('redir @lan https://planet.lan:8443{uri} 308', result.stdout)
        self.assertIn('https://planet.lan {', result.stdout)  # container still listens on 443

    def test_rejects_configuration_injection_and_bad_ports(self):
        for name in ('SMACX_LAN_HTTPS_HOST', 'SMACX_PUBLIC_HOSTNAME'):
            for host in ('https://planet.lan', 'planet.lan:443', 'planet.lan\n}', 'a b', '{host}'):
                with self.subTest(name=name, host=host):
                    self.assertNotEqual(self.render(**{name: host}).returncode, 0)
        for port in ('0', '65536', 'abc', '443\n}'):
            self.assertNotEqual(self.render(SMACX_LAN_HTTPS_PORT=port).returncode, 0)

    def test_rejects_conflicting_hosts(self):
        self.assertNotEqual(self.render(SMACX_LAN_HTTPS_HOST='planet.lan',
                                       SMACX_PUBLIC_HOSTNAME='planet.lan').returncode, 0)


if __name__ == '__main__':
    unittest.main()
