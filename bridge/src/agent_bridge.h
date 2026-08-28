#pragma once

#include <windows.h>

// Private message used to marshal requests from the socket worker onto SMACX's
// UI thread.  Engine data and engine functions must never be touched by the
// worker thread.
const UINT WM_SMACX_AGENT = WM_APP + 0x315;
const UINT WM_SMACX_AGENT_DEFERRED = WM_APP + 0x316;

void agent_bridge_start_once(HWND hwnd);
bool agent_bridge_handle_message(HWND hwnd, UINT msg);
void agent_bridge_stop();

// Fallback request service at the two stock DirectPlay await loops used by
// paired human diplomacy. Engine access remains on the game thread.
int __cdecl agent_network_wait_task();
int __cdecl agent_modal_wait_task();

// Attach fair, player-visible context to modal probe-incident choices while
// the native probe routine is blocked inside its popup loop.
void agent_set_probe_excuse_context(int offender_faction_id, int target_faction_id,
    int action_id, bool framed, bool pact);
void agent_clear_probe_excuse_context();

// Preserve the exact native unit/base/production tuple while an Alien
// Artifact's modal choice is active inside order_veh's nested message loop.
void agent_set_artifact_context(int unit_id, int base_id, int production_id,
    int production_cost);
void agent_clear_artifact_context();

// Mark the exact passive endgame presentation currently blocking the native
// victory pipeline. The semantic bridge can then expose one guarded advance
// action without synthesizing keyboard, mouse, coordinate, or pixel input.
void agent_begin_endgame_presentation(const char* phase);
void agent_end_endgame_presentation(const char* phase);

// Capture the exact chat line delivered by the native multiplayer receive
// pipeline, then preserve the stock on-screen presentation. The patch replaces
// only the receive call site, not the display implementation itself.
void __thiscall agent_chat_display(void* This, const char* text,
    int sender_faction_id);

// Preserve a DirectPlay connection prepared by the semantic LAN bootstrap
// while multiplayer_init initializes the surrounding lobby/game state.
int __thiscall agent_network_initialize(void* This, int mode);

// Preserve the native presentation in single-player. In LAN, queue its exact
// public technology identity semantically so the recipient never deadlocks in
// the stock private modal loop after an otherwise completed native transfer.
void __thiscall agent_new_technology_presentation(void* This, int technology_id,
    int faction_id);
