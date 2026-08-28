// DirectDraw forwarding exports used by the contained two-client LAN harness.
//
// A disposable copy of terranx.exe changes only its DDRAW.dll import descriptor
// to agent.dll.  Copying thinker.dll to that name makes Windows load the bridge
// in-process, avoiding the Wine launcher remote-injection path that Linux Yama
// correctly rejects for a second Wine client.  The original executable and the
// normal thinker.exe launch path are unchanged.

#include <windows.h>

namespace {

HMODULE real_ddraw_module = NULL;

FARPROC real_ddraw_export(const char* name) {
    if (!real_ddraw_module) {
        char system_path[MAX_PATH] = {};
        UINT length = GetSystemDirectoryA(system_path, MAX_PATH);
        if (!length || length + sizeof("\\ddraw.dll") > MAX_PATH) {
            return NULL;
        }
        lstrcatA(system_path, "\\ddraw.dll");
        real_ddraw_module = LoadLibraryA(system_path);
    }
    return real_ddraw_module
        ? GetProcAddress(real_ddraw_module, name) : NULL;
}

} // namespace

extern "C" __declspec(dllexport) HRESULT WINAPI DirectDrawCreate(
void* guid, void** direct_draw, void* outer) {
    typedef HRESULT (WINAPI *FDirectDrawCreate)(void*, void**, void*);
    FDirectDrawCreate function = reinterpret_cast<FDirectDrawCreate>(
        real_ddraw_export("DirectDrawCreate"));
    return function
        ? function(guid, direct_draw, outer)
        : static_cast<HRESULT>(0x80004005L);
}

extern "C" __declspec(dllexport) HRESULT WINAPI DirectDrawEnumerateA(
void* callback, void* context) {
    typedef HRESULT (WINAPI *FDirectDrawEnumerateA)(void*, void*);
    FDirectDrawEnumerateA function = reinterpret_cast<FDirectDrawEnumerateA>(
        real_ddraw_export("DirectDrawEnumerateA"));
    return function
        ? function(callback, context)
        : static_cast<HRESULT>(0x80004005L);
}
