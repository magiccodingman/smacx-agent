#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
redist="$project_dir/toolchain/directx_feb2010_redist.exe"
prefix="$project_dir/runtime/compatdata/pfx"
runtime="$HOME/.local/share/Steam/steamapps/common/SteamLinuxRuntime_4/run"
proton="$HOME/.local/share/Steam/steamapps/common/Proton - Experimental"
wine="$proton/files/bin/wine"
wineserver="$proton/files/bin/wineserver"
system32="$prefix/drive_c/windows/syswow64"
expected_sha256=f6d191e89a963d7cca34f169d30f49eab99c1ed3bb92da73ec43617caaa1e93f

actual_sha256=$(sha256sum "$redist" | awk '{print $1}')
if [ "$actual_sha256" != "$expected_sha256" ]; then
    echo "DirectX redistributable checksum mismatch" >&2
    exit 1
fi

dx_tmp_dir=$(mktemp -d)
trap 'rm -rf -- "$dx_tmp_dir"' EXIT HUP INT TERM
cabextract -q -d "$dx_tmp_dir" -L -F dxnt.cab "$redist"

for file in dplaysvr.exe dplayx.dll dpmodemx.dll dpnet.dll dpnhpast.dll dpnhupnp.dll dpnsvr.exe dpwsockx.dll; do
    # Proton creates these as links to its shared built-ins. Remove only the
    # per-prefix link so extraction cannot overwrite the Proton installation.
    if [ -L "$system32/$file" ]; then
        rm -- "$system32/$file"
    fi
    cabextract -q -d "$system32" -L -F "$file" "$dx_tmp_dir/dxnt.cab"
done

run_wine() {
    "$runtime" -- env DISPLAY="${DISPLAY:-:0}" WINE="$wine" WINESERVER="$wineserver" WINEPREFIX="$prefix" "$wine" "$@"
}

for dll in dplaysvr.exe dplayx dpmodemx dpnet dpnhpast dpnhupnp dpnsvr.exe dpwsockx; do
    run_wine reg add 'HKCU\Software\Wine\DllOverrides' /v "$dll" /d native /f
done

for dll in dplayx.dll dpnet.dll dpnhpast.dll dpnhupnp.dll; do
    run_wine 'C:\windows\syswow64\regsvr32.exe' /s 'C:\windows\syswow64\'"$dll"
done

echo "Native DirectPlay installed into $prefix"
