#!/usr/bin/env python3
"""A historical inaccessible popup is never read as a live native object."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = (ROOT / 'bridge/src/gui_dialog.cpp').read_text()
    implementation = 'bool agent_popup_object_is_active(' + source.split(
        'bool agent_popup_object_is_active(', 1)[1].split(
        '\nuint64_t agent_popup_generation()', 1)[0]
    # Bind only fixed native-global addresses to test-owned storage; execute
    # the production lifetime predicates unchanged against protected memory.
    implementation = implementation.replace('*reinterpret_cast<Win**>(0x9B7AE0)', 'test_modal').replace(
        '*reinterpret_cast<BasePop**>(0x9B8D7C)', 'test_executing').replace(
        '*reinterpret_cast<int*>(0x9B8D00)', 'test_exec_depth')
    code = r'''
#include <windows.h>
#include <cstdint>
#include <cstdio>
struct Win {};
struct BasePop {};
int test_modal_depth = 0;
int* WinModalState = &test_modal_depth;
Win* test_modal = NULL;
BasePop* test_executing = NULL;
int test_exec_depth = 0;
BasePop* last_started_agent_popup;
''' + implementation + r'''
int main() {
    void* expired = VirtualAlloc(NULL, 4096, MEM_RESERVE | MEM_COMMIT, PAGE_NOACCESS);
    if (!expired) return 11;
    last_started_agent_popup = reinterpret_cast<BasePop*>(expired);
    if (agent_popup_object() || agent_popup_object_is_active(last_started_agent_popup)) return 12;
    BasePop active;
    *WinModalState = 1;
    test_modal = reinterpret_cast<Win*>(&active);
    if (agent_popup_object()) return 13; // unrelated modal cannot revive history
    last_started_agent_popup = &active;
    if (agent_popup_object() != &active) return 14;
    *WinModalState = 0;
    if (agent_popup_object()) return 15; // a stale slot alone is insufficient
    test_exec_depth = 1;
    test_executing = &active;
    if (agent_popup_object() != &active) return 16;
    test_exec_depth = 0;
    if (agent_popup_object() || agent_popup_object_is_active(reinterpret_cast<BasePop*>(2))) return 17;
    std::puts("native_popup_lifetime_passed");
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='smacx-popup-lifetime-') as tmp:
        path = Path(tmp)
        (path / 'test.cpp').write_text(code)
        subprocess.run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{path}:/test',
            '--entrypoint', 'i686-w64-mingw32-g++',
            os.environ.get('SMACX_TEST_BRIDGE_BUILD_IMAGE', 'smacx-agent-bridge-debug:request-stall'),
            '-static', '/test/test.cpp', '-o', '/test/test.exe'], check=True)
        result = subprocess.run(['docker', 'run', '--rm', '--user', '0', '--network', 'none',
            '-v', f'{path}:/test', '-e', 'WINEPREFIX=/tmp/popup-prefix', '-e', 'WINEDEBUG=-all',
            '--entrypoint', '/opt/proton/files/bin/wine',
            os.environ.get('SMACX_TEST_WORKER_IMAGE', 'smacx-agent-worker:dev'), '/test/test.exe'],
            capture_output=True, text=True, timeout=60)
        assert result.returncode == 0 and 'native_popup_lifetime_passed' in result.stdout, (
            result.returncode, result.stdout[-500:], result.stderr[-500:])
    print(json.dumps({'passed': True, 'expired_page_is_inaccessible': True,
        'unrelated_modal_does_not_reactivate_history': True,
        'live_modal_and_exec_membership_preserved': True,
        'classification': 'actual lifetime helpers with Windows protected memory; native replay pending'}))


if __name__ == '__main__':
    main()
