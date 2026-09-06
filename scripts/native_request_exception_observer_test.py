#!/usr/bin/env python3
"""Exercise the bridge observer with real Windows exception dispatch under Wine."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = (ROOT / "bridge/src/agent_bridge.cpp").read_text()
    observer = source.split("LONG CALLBACK observe_request_exception(", 1)[1].split(
        "\nLRESULT CALLBACK agent_request_getmessage_hook", 1)[0]
    program = r"""
#include <windows.h>
#include <cstdio>
DWORD request_ui_thread_id;
bool request_in_progress = true;
volatile LONG request_execution_stage = 13;
volatile LONG request_exception_code = 0;
volatile LONG request_exception_stage = 0;
""" + "LONG CALLBACK observe_request_exception(" + observer + r"""
LONG CALLBACK handle_test_exception(EXCEPTION_POINTERS* p) {
    return p->ExceptionRecord->ExceptionCode == 0xE1350048u
        ? EXCEPTION_CONTINUE_EXECUTION : EXCEPTION_CONTINUE_SEARCH;
}
DWORD WINAPI other_thread(void*) {
    RaiseException(0xE1350048u, 0, 0, NULL);
    return 0;
}
int main() {
    request_ui_thread_id = GetCurrentThreadId();
    PVOID observer = AddVectoredExceptionHandler(1, observe_request_exception);
    PVOID handler = AddVectoredExceptionHandler(0, handle_test_exception);
    if (!observer || !handler) return 10;
    RaiseException(0xE1350048u, 0, 0, NULL);
    if (static_cast<DWORD>(request_exception_code) != 0xE1350048u
        || request_exception_stage != 13 || !request_in_progress) return 11;
    request_exception_code = 0;
    HANDLE other = CreateThread(NULL, 0, other_thread, NULL, 0, NULL);
    if (!other || WaitForSingleObject(other, 5000) != WAIT_OBJECT_0) return 12;
    CloseHandle(other);
    if (request_exception_code != 0) return 13;
    request_in_progress = false;
    RaiseException(0xE1350048u, 0, 0, NULL);
    if (request_exception_code != 0 || request_in_progress) return 14;
    RemoveVectoredExceptionHandler(observer);
    RemoveVectoredExceptionHandler(handler);
    std::puts("native_exception_observer_passed");
    return 0;
}
"""
    with tempfile.TemporaryDirectory(prefix="smacx-exception-observer-") as temporary:
        path = Path(temporary)
        path.chmod(0o777)
        (path / "test.cpp").write_text(program)
        subprocess.run(["docker", "run", "--rm", "--network", "none", "-v",
            f"{path}:/test", "--entrypoint", "i686-w64-mingw32-g++",
            os.environ.get("SMACX_TEST_BRIDGE_BUILD_IMAGE", "smacx-agent-bridge-debug:turn25"),
            "-static", "/test/test.cpp", "-o", "/test/test.exe"], check=True)
        result = subprocess.run(["docker", "run", "--rm", "--user", "0", "--network", "none", "-v",
            f"{path}:/test", "-e", "WINEPREFIX=/tmp/observer-prefix", "-e", "WINEDEBUG=-all",
            "--entrypoint", "/opt/proton/files/bin/wine",
            os.environ.get("SMACX_TEST_WORKER_IMAGE", "smacx-agent-worker:dev"),
            "/test/test.exe"], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0 and "native_exception_observer_passed" in result.stdout, (
            result.returncode, result.stdout[-1000:], result.stderr[-1000:])
    print(json.dumps({"passed": True, "real_windows_exception_dispatch": True,
        "continues_to_next_handler": True, "only_active_ui_request_recorded": True,
        "serialization_guard_unchanged": True, "gameplay_repair_proven": False}))


if __name__ == "__main__":
    main()
