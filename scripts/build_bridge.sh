#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
llvm_bin="$project_dir/toolchain/llvm-mingw-20260826-ucrt-ubuntu-22.04-x86_64/bin"
python_bin="$project_dir/toolchain/py/bin"

PATH="$llvm_bin:$python_bin:$PATH" cmake -S "$project_dir/bridge" -B "$project_dir/bridge/build/agent" -G Ninja -DCMAKE_BUILD_TYPE=Release
PATH="$llvm_bin:$python_bin:$PATH" cmake --build "$project_dir/bridge/build/agent" -j2
cp -f "$project_dir/bridge/build/agent/thinker.dll" "$project_dir/runtime/game/thinker.dll"
cp -f "$project_dir/bridge/build/agent/thinker.exe" "$project_dir/runtime/game/thinker.exe"
if [ -d "$project_dir/runtime/game-lan-join" ]; then
    cp -f "$project_dir/bridge/build/agent/thinker.dll" "$project_dir/runtime/game-lan-join/thinker.dll"
    cp -f "$project_dir/bridge/build/agent/thinker.exe" "$project_dir/runtime/game-lan-join/thinker.exe"
    "$python_bin/python3" "$project_dir/scripts/prepare_lan_join_client.py" \
        --game-dir "$project_dir/runtime/game-lan-join"
fi
sha256sum "$project_dir/runtime/game/thinker.dll"
