#!/usr/bin/env python3
"""Compile the actual roster adapter with controlled native inputs; not a live save proof."""
import json
from pathlib import Path
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'bridge/src/patch.cpp').read_text()
    signature = 'int __thiscall NetWin_random_get(void*, int low, int high) {'
    body = signature + source.split(signature, 1)[1].split('\n}\n', 1)[0] + '\n}\n'
    pointer = '*reinterpret_cast<int*>(0x90E778)'
    assert body.count(pointer) == 1
    body = body.replace(pointer, 'controlled_game_type')
    harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#define __thiscall
int controlled_game_type = 0, draws = 0, cursor = 0;
uint32_t configured_mask = 127;
struct { uint32_t skip_random_factions; } conf;
unsigned long GetEnvironmentVariableA(const char*, char* result, unsigned long) {
    if (!configured_mask) return 0;
    std::strcpy(result, "127"); return 3;
}
int random_get(int low, int high) {
    if (++draws > 200000) throw std::runtime_error("native uniqueness loop cannot finish");
    return low + (cursor++ % (high - low));
}
''' + body + r'''
int main() {
    for (int type = 0; type <= 3; ++type) {
        controlled_game_type = type; draws = cursor = 0;
        // Random-roster exclusions must not erase explicit new-game choices,
        // nor reinterpret a loaded game's saved slot identifiers.
        conf.skip_random_factions = (1u << 3) | (1u << 7);
        uint32_t used = type == 0 ? 0 : 1;
        const int high = type == 0 ? 14 : 8;
        for (int slot = 0; slot < 7; ++slot) {
            int choice;
            do { choice = NetWin_random_get(nullptr, 0, high); }
            while (used & (1u << choice));
            used |= 1u << choice;
        }
        assert(used == (type == 0 ? 127u : 255u));
        assert(draws < 100);
    }
    controlled_game_type = 0; configured_mask = 0; draws = cursor = 0;
    conf.skip_random_factions = 1u << 3;
    for (int i = 0; i < 28; ++i) assert(NetWin_random_get(nullptr, 0, 14) != 3);
}
'''
    with tempfile.TemporaryDirectory(prefix='smacx-roster-contract-') as tmp:
        path = Path(tmp)
        (path / 'test.cpp').write_text(harness)
        subprocess.run(['c++', '-std=c++17', '-O2', str(path / 'test.cpp'), '-o', str(path / 'test')], check=True)
        subprocess.run([str(path / 'test')], check=True, timeout=5)
    print(json.dumps({'passed': True, 'evidence': 'compiled_native_adapter_controlled_rng',
                      'fresh_explicit_roster_preserved': True, 'saved_slot_seven_reachable': True,
                      'native_unique_selection_loop_bounded': True, 'ordinary_random_exclusions_preserved': True}))


if __name__ == '__main__':
    main()
