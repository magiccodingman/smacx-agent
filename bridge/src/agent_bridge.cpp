#include <winsock2.h>
#include <ws2tcpip.h>

#include "agent_bridge.h"
#include "base.h"
#include "basewin.h"
#include "faction.h"
#include "game.h"
#include "gui_dialog.h"
#include "main.h"
#include "map.h"
#include "move.h"
#include "net.h"
#include "path.h"
#include "probe.h"
#include "savegame.h"
#include "score.h"
#include "tech.h"
#include "veh.h"
#include "veh_action.h"
#include "veh_combat.h"

#include <algorithm>
#include <sstream>
#include <set>
#include <vector>

namespace {

const int DefaultPort = 47813;
const int MaxRequestBytes = 16384;
// Console::on_key_click handles this stock semantic command by invoking
// Console::end_my_turn (terranx.exe 0x518197..0x5181A8).
const int NativeEndTurnCommand = 0x2000D;
BasePop** const DefaultPopupA = reinterpret_cast<BasePop**>(0x9BC074);
BasePop** const DefaultPopupB = reinterpret_cast<BasePop**>(0x9BC078);
Win** const ModalStackCurrent = reinterpret_cast<Win**>(0x9B7AE0);
BasePop** const BasePopExecCurrent = reinterpret_cast<BasePop**>(0x9B8D7C);
int* const BasePopExecDepth = reinterpret_cast<int*>(0x9B8D00);
int* const BasePopFalloutLatch = reinterpret_cast<int*>(0x9B8CFC);

HANDLE worker_thread = NULL;
HANDLE response_event = NULL;
HHOOK request_getmessage_hook = NULL;
CRITICAL_SECTION request_lock;
bool lock_initialized = false;
volatile LONG started = 0;
volatile LONG stopping = 0;
volatile LONG request_getmessage_hits = 0;
volatile LONG request_network_wait_hits = 0;
volatile LONG request_modal_wait_hits = 0;
volatile LONG request_handler_hits = 0;
SOCKET listen_socket = INVALID_SOCKET;
HWND game_window = NULL;
std::string pending_request;
std::string pending_response;
std::string auth_token;
std::string agent_session_id;
std::string agent_match_id;
bool managed_human_controller = false;
bool request_pending = false;
bool request_in_progress = false;
int agent_modal_service_depth = 0;
uint64_t pending_sequence = 0;
uint64_t response_sequence = 0;
std::vector<int> pending_multiplayer_technology_presentations;

LRESULT CALLBACK agent_request_getmessage_hook(int code, WPARAM remove,
LPARAM message_pointer) {
    // Some stock paired diplomacy loops retrieve the main-window request but
    // consume it before ModWinProc. Service that already-retrieved message on
    // the authoritative game thread. request_in_progress prevents a native
    // loop entered by the request itself from re-entering the same operation.
    if (code >= 0 && remove == PM_REMOVE && message_pointer) {
        MSG* message = reinterpret_cast<MSG*>(message_pointer);
        if (message->message == WM_SMACX_AGENT) {
            InterlockedIncrement(&request_getmessage_hits);
            // The hook is running inside a stock private message/network
            // loop. A semantic read must not enter NetDaemon::await_exec a
            // second time from here; the surrounding loop is already the
            // authoritative packet pump and will resume after this bounded
            // request returns.
            ++agent_modal_service_depth;
            agent_bridge_handle_message(message->hwnd, message->message);
            --agent_modal_service_depth;
        }
    }
    return CallNextHookEx(request_getmessage_hook, code, remove, message_pointer);
}
int semantic_tile_id(int x, int y);
bool semantic_tile_coords(int tile_id, int* x, int* y);
// Monotonic bridge-side generation for accepted semantic mutations.  A pure
// state hash can repeat when an action is applied and then cancelled, which
// would make an older optimistic-concurrency token valid again.
uint64_t semantic_mutation_generation = 0;
const size_t MaxObservationEvents = 1024;
struct NativeObservationEvent {
    uint64_t sequence;
    int turn;
    int subject_a;
    int subject_b;
    int from_tile_id;
    int to_tile_id;
    int value_before;
    int value_after;
    bool continuous_visibility;
    char kind[32];
};
NativeObservationEvent observation_events[MaxObservationEvents] = {};
size_t observation_event_start = 0;
size_t observation_event_count = 0;
uint64_t next_observation_sequence = 1;
uint64_t lost_after_observation_sequence = 0;
std::string last_observed_action_revision;
uint64_t last_observed_popup_generation = 0;
// Secret Project IDs occupy the facility namespace above MaxFacilityNum;
// index these report-only registries by the complete native ID range.
int known_project_builder[SP_ID_Last + 1] = {};
bool known_project_builder_valid[SP_ID_Last + 1] = {};

void append_observation_event(const char* kind, int turn, int subject_a = -1,
int subject_b = -1, int from_tile_id = -1, int to_tile_id = -1,
int value_before = -1, int value_after = -1,
bool continuous_visibility = false) {
    if (!kind || !kind[0]) return;
    size_t index = 0;
    if (observation_event_count < MaxObservationEvents) {
        index = (observation_event_start + observation_event_count) % MaxObservationEvents;
        ++observation_event_count;
    } else {
        lost_after_observation_sequence = observation_events[observation_event_start].sequence;
        observation_event_start = (observation_event_start + 1) % MaxObservationEvents;
        index = (observation_event_start + observation_event_count - 1) % MaxObservationEvents;
    }
    NativeObservationEvent& event = observation_events[index];
    event.sequence = next_observation_sequence++;
    event.turn = turn;
    event.subject_a = subject_a;
    event.subject_b = subject_b;
    event.from_tile_id = from_tile_id;
    event.to_tile_id = to_tile_id;
    event.value_before = value_before;
    event.value_after = value_after;
    event.continuous_visibility = continuous_visibility;
    lstrcpynA(event.kind, kind, static_cast<int>(sizeof(event.kind)));
}

int semantic_project_id_from_name(const char* name) {
    if (!name || !name[0]) return -1;
    for (int project_id = SP_ID_First; project_id <= SP_ID_Last; ++project_id) {
        if (!strcmp(name, Facility[project_id].name)) return project_id;
    }
    return -1;
}

int semantic_faction_id_from_report_name(const char* name) {
    if (!name || !name[0]) return -1;
    for (int faction_id = 1; faction_id < MaxPlayerNum; ++faction_id) {
        if (!strcmp(name, MFactions[faction_id].noun_faction)
        || !strcmp(name, MFactions[faction_id].adj_name_faction)
        || !strcmp(name, MFactions[faction_id].formal_name_faction)
        || !strcmp(name, MFactions[faction_id].name_leader)) return faction_id;
    }
    return -1;
}

void capture_project_report_popup() {
    const uint64_t generation = agent_popup_generation();
    if (!generation || generation == last_observed_popup_generation) return;
    last_observed_popup_generation = generation;
    const char* label = agent_popup_last_started_label();
    int project_id = -1;
    int prior_project_id = -1;
    int faction_id = -1;
    const char* event_kind = NULL;
    if (!strcmp(label, "BEGINPROJECT")) {
        project_id = semantic_project_id_from_name(agent_popup_parse_string(3));
        faction_id = semantic_faction_id_from_report_name(agent_popup_parse_string(2));
        event_kind = "project_race_started";
    } else if (!strcmp(label, "CHANGEPROJECT")) {
        prior_project_id = semantic_project_id_from_name(agent_popup_parse_string(1));
        project_id = semantic_project_id_from_name(agent_popup_parse_string(3));
        faction_id = semantic_faction_id_from_report_name(agent_popup_parse_string(2));
        event_kind = "project_race_changed";
    } else if (!strcmp(label, "HALTPROJECT")) {
        project_id = semantic_project_id_from_name(agent_popup_parse_string(3));
        faction_id = semantic_faction_id_from_report_name(agent_popup_parse_string(2));
        event_kind = "project_race_halted";
    } else if (!strcmp(label, "SURVIVEPROJECT")) {
        project_id = semantic_project_id_from_name(agent_popup_parse_string(1));
        faction_id = semantic_faction_id_from_report_name(agent_popup_parse_string(0));
        event_kind = "project_race_continued";
    } else if (!strcmp(label, "DONEPROJECT")) {
        project_id = semantic_project_id_from_name(agent_popup_parse_string(1));
        faction_id = semantic_faction_id_from_report_name(agent_popup_parse_string(0));
        event_kind = "project_race_nearing_completion";
    }
    if (!event_kind || project_id < SP_ID_First || project_id > SP_ID_Last) return;
    if (prior_project_id >= SP_ID_First && prior_project_id <= SP_ID_Last) {
        known_project_builder_valid[prior_project_id] = false;
    }
    if (strcmp(event_kind, "project_race_halted")) {
        known_project_builder[project_id] = faction_id;
        known_project_builder_valid[project_id] = faction_id >= 1;
    } else {
        known_project_builder_valid[project_id] = false;
    }
    append_observation_event(event_kind, *CurrentTurn, project_id, faction_id,
                             -1, -1, prior_project_id, project_id);
}

void reset_project_report_memory() {
    last_observed_popup_generation = 0;
    memset(known_project_builder, 0, sizeof(known_project_builder));
    memset(known_project_builder_valid, 0, sizeof(known_project_builder_valid));
}

struct ObservedVehicleState {
    bool present = false;
    bool visible = false;
    int faction_id = -1;
    int unit_id = -1;
    int x = -1;
    int y = -1;
    int hitpoints = -1;
};

struct ObservedBaseState {
    bool present = false;
    bool visible = false;
    int faction_id = -1;
    int x = -1;
    int y = -1;
    int population = -1;
};

struct ObservedTileState {
    bool sampled = false;
    bool known = false;
    bool visible = false;
    uint32_t items = 0;
    int altitude = -1;
    int climate = -1;
    int owner = -1;
};

std::vector<ObservedVehicleState> observed_vehicles;
std::vector<ObservedBaseState> observed_bases;
std::vector<ObservedTileState> observed_tiles;
std::vector<ObservedVehicleState> sampled_vehicles;
std::vector<ObservedBaseState> sampled_bases;
size_t observed_tile_cursor = 0;
bool semantic_observation_shadow_ready = false;
// VEH rows are compacted with memmove on destruction. A private monotonic
// handle vector is compacted in the same hook, giving every surviving row a
// stable semantic identity without revealing its native array index.
std::vector<int> semantic_vehicle_handles;
int next_semantic_vehicle_handle = 1;
std::string field_string(const std::string& json, const char* name);
int field_int(const std::string& json, const char* name, int fallback);
std::string error_response(const char* error, const char* message);
void reset_semantic_observation_shadow();
bool game_active();

void ensure_semantic_vehicle_handles() {
    const size_t count = static_cast<size_t>(std::max(0, *VehCount));
    if (semantic_vehicle_handles.size() > count) {
        semantic_vehicle_handles.clear();
        observed_vehicles.clear();
        semantic_observation_shadow_ready = false;
    }
    while (semantic_vehicle_handles.size() < count) {
        // Zero is an unobserved private row.  Allocating handles only when a
        // unit is legitimately visible prevents hidden row layout/count from
        // perturbing provider-visible refs or action revisions.
        semantic_vehicle_handles.push_back(0);
    }
}

int semantic_vehicle_handle(int row) {
    ensure_semantic_vehicle_handles();
    if (row < 0 || row >= static_cast<int>(semantic_vehicle_handles.size())) return -1;
    if (!semantic_vehicle_handles[row])
        semantic_vehicle_handles[row] = next_semantic_vehicle_handle++;
    return semantic_vehicle_handles[row];
}

uint64_t semantic_vehicle_layout_hash() {
    // Platform-private proof that a semantic-handle vector belongs to this
    // exact restored native VEH layout. It is never provider-visible.
    uint64_t hash = 1469598103934665603ULL;
    auto mix = [&](uint64_t value) {
        hash ^= value;
        hash *= 1099511628211ULL;
    };
    mix(static_cast<uint32_t>(*CurrentTurn));
    mix(static_cast<uint32_t>(*CurrentPlayerFaction));
    mix(static_cast<uint32_t>(*VehCount));
    for (int row = 0; row < *VehCount; ++row) {
        VEH& veh = Vehs[row];
        mix(static_cast<uint32_t>(veh.faction_id));
        mix(static_cast<uint32_t>(veh.unit_id));
        mix(static_cast<uint32_t>(veh.x));
        mix(static_cast<uint32_t>(veh.y));
        mix(static_cast<uint32_t>(veh.home_base_id + 1));
        mix(static_cast<uint32_t>(veh.order));
        mix(static_cast<uint32_t>(veh.moves_spent));
        mix(static_cast<uint32_t>(veh.cur_hitpoints()));
    }
    return hash;
}

std::vector<int> field_int_array(const std::string& json, const char* name,
                                 bool* valid) {
    if (valid) *valid = false;
    std::vector<int> result;
    std::string needle = std::string("\"") + name + "\"";
    size_t pos = json.find(needle);
    if (pos == std::string::npos) return result;
    pos = json.find(':', pos + needle.size());
    if (pos == std::string::npos) return result;
    pos = json.find('[', pos + 1);
    if (pos == std::string::npos) return result;
    ++pos;
    while (pos < json.size()) {
        while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t'
        || json[pos] == '\r' || json[pos] == '\n' || json[pos] == ',')) ++pos;
        if (pos < json.size() && json[pos] == ']') {
            if (valid) *valid = true;
            return result;
        }
        if (pos >= json.size() || json[pos] < '0' || json[pos] > '9') return {};
        char* end = NULL;
        long value = strtol(json.c_str() + pos, &end, 10);
        if (!end || end == json.c_str() + pos || value < 0 || value > 0x7fffffffL)
            return {};
        result.push_back(static_cast<int>(value));
        if (result.size() > static_cast<size_t>(MaxVehNum)) return {};
        pos = static_cast<size_t>(end - json.c_str());
        while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t'
        || json[pos] == '\r' || json[pos] == '\n')) ++pos;
        if (pos < json.size() && json[pos] != ',' && json[pos] != ']') return {};
    }
    return {};
}

std::string semantic_identity_state_response(const std::string& request) {
    if (!game_active()) return error_response(
        "game_not_active", "Semantic identity state requires an active game.");
    std::string action = field_string(request, "action");
    if (action == "export") {
        ensure_semantic_vehicle_handles();
        std::ostringstream out;
        out << "{\"ok\":true,\"schema\":\"smacx.private-vehicle-identity.v1\""
            << ",\"turn\":" << *CurrentTurn
            << ",\"faction_id\":" << *CurrentPlayerFaction
            << ",\"native_validation_hash\":\""
            << semantic_vehicle_layout_hash() << "\""
            << ",\"next_semantic_vehicle_handle\":"
            << next_semantic_vehicle_handle << ",\"semantic_vehicle_handles\":[";
        for (size_t index = 0; index < semantic_vehicle_handles.size(); ++index) {
            if (index) out << ',';
            out << semantic_vehicle_handles[index];
        }
        out << "]}";
        return out.str();
    }
    if (action == "import") {
        if (field_string(request, "schema") != "smacx.private-vehicle-identity.v1")
            return error_response("identity_schema_mismatch", "Unsupported identity capsule.");
        const std::string expected = field_string(request, "native_validation_hash");
        if (expected.empty() || expected != std::to_string(semantic_vehicle_layout_hash()))
            return error_response("identity_native_state_mismatch",
                                  "Identity capsule does not match the restored native state.");
        bool valid = false;
        std::vector<int> handles = field_int_array(
            request, "semantic_vehicle_handles", &valid);
        const int next_handle = field_int(
            request, "next_semantic_vehicle_handle", -1);
        if (!valid || handles.size() != static_cast<size_t>(*VehCount))
            return error_response("identity_handle_count_mismatch",
                                  "Identity capsule handle count is invalid.");
        std::set<int> unique;
        int maximum = 0;
        for (size_t index = 0; index < handles.size(); ++index) {
            if (handles[index] <= 0) continue;
            unique.insert(handles[index]);
            maximum = std::max(maximum, handles[index]);
        }
        const size_t assigned = static_cast<size_t>(std::count_if(
            handles.begin(), handles.end(), [](int value) { return value > 0; }));
        if (unique.size() != assigned || next_handle <= maximum)
            return error_response("identity_handle_set_invalid",
                                  "Identity capsule handles are not unique and monotonic.");
        semantic_vehicle_handles.swap(handles);
        next_semantic_vehicle_handle = next_handle;
        reset_semantic_observation_shadow();
        return std::string("{\"ok\":true,\"restored\":true,\"handle_count\":")
            + std::to_string(semantic_vehicle_handles.size()) + '}';
    }
    return error_response("bad_identity_action", "Use export or import.");
}
int semantic_observation_faction = -1;
UINT_PTR semantic_observation_timer_id = 0;

void reset_semantic_observation_shadow() {
    observed_vehicles.clear();
    observed_bases.clear();
    observed_tiles.clear();
    sampled_vehicles.clear();
    sampled_bases.clear();
    observed_tile_cursor = 0;
    semantic_observation_shadow_ready = false;
    semantic_observation_faction = -1;
}

const size_t MaxChatEvents = 64;
struct ChatEvent {
    uint64_t sequence;
    bool outbound;
    bool broadcast;
    int sender_faction_id;
    int recipient_faction_id;
    int turn;
    std::string text;
    std::string client_message_id;
};
ChatEvent chat_events[MaxChatEvents];
size_t chat_event_count = 0;
uint64_t next_chat_sequence = 1;
volatile LONG lan_test_in_progress = 0;
volatile LONG lan_test_completed_stage = 0;
volatile LONG lan_test_open_hresult = 0;
volatile LONG lan_test_preconnected = 0;
volatile LONG lan_test_lobby_pending = 0;
volatile LONG lan_join_address_stage = 0;
volatile LONG lan_join_address_hresult = 0;
std::string lan_network_session_id;
std::string lan_client_operation_id;
std::string lan_lobby_operation_id;
std::string lan_lobby_operation_action;
std::string lan_pending_load_path;
int lan_pending_load_game_type = 3;
volatile LONG lan_pending_load_active = 0;
volatile LONG lan_pending_load_choice_seen = 0;
volatile LONG lan_pending_load_native_status = -1;
volatile LONG lan_pending_faction_choice_active = 0;
volatile LONG lan_pending_faction_choice_seen = 0;
volatile LONG lan_pending_faction_choice_id = -1;
volatile LONG lan_pending_faction_selector_result = -1;
volatile LONG lan_pending_faction_choice_before_validation = -1;
volatile LONG lan_pending_faction_validation_result = -1;
int pending_base_name_id = -1;
std::string pending_base_name;
int pending_research_focus_faction_id = -1;
int pending_research_focus_priority = -1;
bool pending_popup_transition = false;
uint64_t pending_popup_generation = 0;
std::string pending_popup_label;
BasePop* pending_popup_object = NULL;
bool pending_end_turn_completion = false;
int pending_end_turn_source_turn = -1;
std::string endgame_presentation_phase;
bool pending_endgame_presentation_advance = false;
uint64_t endgame_presentation_generation = 0;
int deferred_diplomacy_faction_id = -1;
int deferred_council_faction_id = -1;
int deferred_end_turn_faction_id = -1;
int deferred_end_turn_source_turn = -1;
UINT_PTR deferred_end_turn_timer_id = 0;
int deferred_nerve_staple_base_id = -1;
int deferred_obliterate_base_id = -1;
int deferred_obliterate_unit_id = -1;
int active_obliterate_base_id = -1;
int active_obliterate_unit_id = -1;
int active_obliterate_decision = -1;
int deferred_destroy_unit_id = -1;
int deferred_destroy_former_id = -1;
int deferred_destroy_owner_id = -1;
bool deferred_destroy_hostility_confirmed = false;
int deferred_move_unit_id = -1;
int deferred_move_direction = -1;
int deferred_move_x = -1;
int deferred_move_y = -1;
int deferred_probe_unit_id = -1;
int deferred_probe_base_id = -1;
int deferred_probe_target_unit_id = -1;
int deferred_probe_action_id = -1;
int deferred_probe_frame_faction_id = 0;
bool deferred_probe_enhanced = false;
int deferred_missile_unit_id = -1;
int deferred_missile_x = -1;
int deferred_missile_y = -1;
int test_loan_fixture_other_faction_id = -1;
bool test_loan_fixture_initialized = false;
int test_commerce_fixture_other_faction_id = -1;
bool test_commerce_fixture_initialized = false;
int active_probe_unit_id = -1;
int active_probe_base_id = -1;
bool active_probe_abort_requested = false;
bool observed_human_diplomacy_active = false;
DWORD human_diplomacy_settle_deadline = 0;
const DWORD HumanDiplomacySettleMs = 1000;
uint32_t next_deferred_action_id = 1;
bool game_active();
int first_owned_base(int faction_id);
bool human_turn_actionable(int faction_id);
BasePop* active_default_popup();
const char* semantic_popup_label();
std::string interaction_kind(int faction_id);
void update_human_diplomacy_lifecycle();

struct ArtifactInteractionContext {
    bool valid = false;
    int unit_id = -1;
    int base_id = -1;
    int production_id = 99999;
    int production_cost = 0;
};
ArtifactInteractionContext artifact_interaction;
int resolved_artifact_unit_id = -1;
bool resolved_artifact_consumed = false;

void begin_popup_transition(BasePop* popup) {
    pending_popup_transition = true;
    pending_popup_generation = agent_popup_generation();
    pending_popup_label = agent_popup_label();
    pending_popup_object = popup;
}

bool popup_transition_is_pending() {
    if (!pending_popup_transition) return false;
    if (!pending_popup_object
    || !Win_is_visible(reinterpret_cast<Win*>(pending_popup_object))
    || agent_popup_generation() != pending_popup_generation) {
        pending_popup_transition = false;
        pending_popup_generation = 0;
        pending_popup_label.clear();
        pending_popup_object = NULL;
        return false;
    }
    return true;
}
UINT_PTR council_vote_timer_id = 0;
int pending_council_proposal_id = -1;
int pending_council_faction_id = -1;
int pending_council_vote_value = 0;
int pending_council_timer_ticks = 0;
int pending_council_timer_stage = 0;
bool last_council_result_valid = false;
int last_council_proposal_id = -1;
int last_council_ballot_value = 0;
int last_council_result_state = 0;
int last_council_governor_faction_id = -1;
UINT_PTR energy_gift_timer_id = 0;
bool pending_energy_gift = false;
int pending_energy_gift_faction_id = -1;
int pending_energy_gift_other_id = -1;
int pending_energy_gift_amount = 0;
int pending_energy_gift_timer_ticks = 0;
bool pending_energy_gift_prompt_seen = false;
bool pending_energy_gift_receipt_seen = false;
UINT_PTR test_energy_gift_menu_timer_id = 0;
int test_energy_gift_menu_timer_ticks = 0;
UINT_PTR test_proposal_guard_menu_timer_id = 0;
int test_proposal_guard_menu_timer_ticks = 0;

typedef int(__thiscall *FCouncilWindowCastVote)(CouncilWindow*, int, int);
FCouncilWindowCastVote council_window_cast_vote =
    reinterpret_cast<FCouncilWindowCastVote>(0x424720);
typedef int(__thiscall *FCouncilWindowCommand)(CouncilWindow*, int);
FCouncilWindowCommand council_window_command =
    reinterpret_cast<FCouncilWindowCommand>(0x424870);

int active_council_window_proposal() {
    if (!Win_is_visible(reinterpret_cast<Win*>(CouncilWin))) return -1;
    int proposal = *reinterpret_cast<int*>(
        reinterpret_cast<char*>(CouncilWin) + 0xA1C);
    int state = *reinterpret_cast<int*>(
        reinterpret_cast<char*>(CouncilWin) + 0xA24);
    return proposal >= 0 && proposal < MaxProposalNum && state == 0 ? proposal : -1;
}

void clear_pending_council_vote() {
    if (council_vote_timer_id) KillTimer(NULL, council_vote_timer_id);
    council_vote_timer_id = 0;
    pending_council_proposal_id = -1;
    pending_council_faction_id = -1;
    pending_council_vote_value = 0;
    pending_council_timer_ticks = 0;
    pending_council_timer_stage = 0;
}

void clear_pending_energy_gift() {
    if (energy_gift_timer_id) KillTimer(NULL, energy_gift_timer_id);
    energy_gift_timer_id = 0;
    pending_energy_gift = false;
    pending_energy_gift_faction_id = -1;
    pending_energy_gift_other_id = -1;
    pending_energy_gift_amount = 0;
    pending_energy_gift_timer_ticks = 0;
}

VOID CALLBACK council_vote_timer_proc(HWND, UINT, UINT_PTR, DWORD) {
    if (++pending_council_timer_ticks > 800 || !game_active()) {
        clear_pending_council_vote();
        return;
    }
    int proposal = *reinterpret_cast<int*>(
        reinterpret_cast<char*>(CouncilWin) + 0xA1C);
    int state = *reinterpret_cast<int*>(
        reinterpret_cast<char*>(CouncilWin) + 0xA24);
    if (!Win_is_visible(reinterpret_cast<Win*>(CouncilWin))
    || proposal != pending_council_proposal_id) return;
    if (pending_council_timer_stage == 1 && state == 0) {
        council_window_cast_vote(
            CouncilWin, pending_council_faction_id, pending_council_vote_value);
        pending_council_timer_stage = 2;
        pending_council_timer_ticks = 0;
        return;
    }
    if (pending_council_timer_stage == 2 && state != 0
    && pending_council_timer_ticks >= 40) {
        last_council_result_valid = true;
        last_council_proposal_id = pending_council_proposal_id;
        last_council_ballot_value = pending_council_vote_value;
        last_council_result_state = state;
        last_council_governor_faction_id = *GovernorFaction;
        clear_pending_council_vote();
        council_window_command(CouncilWin, -2);
    }
}

struct DeferredActionState {
    uint32_t id;
    std::string command;
    std::string status;
    int native_result;
    int unit_id;
    int origin_x;
    int origin_y;
    int target_x;
    int target_y;
    int observed_x;
    int observed_y;
    std::string resolution;
};

DeferredActionState deferred_action = {};

struct ProbeExcuseContext {
    bool valid;
    int offender_faction_id;
    int target_faction_id;
    int action_id;
    bool framed;
    bool pact;
};

ProbeExcuseContext probe_excuse_context = {};

bool probe_excuse_label(const std::string& label) {
    return label == "EXCUSE" || label == "FRAMEEXCUSE"
        || label == "PACTEXCUSE" || label == "PACTFRAMEEXCUSE";
}

bool technology_trade_label(const std::string& label) {
    return label.size() == 10 && !label.compare(0, 9, "TRADETECH")
        && label[9] >= '0' && label[9] <= '5';
}

bool technology_demand_label(const std::string& label) {
    if (label.compare(0, 10, "DEMANDTECH")) return false;
    size_t pos = 10;
    if (pos >= label.size() || label[pos] < '0' || label[pos] > '9') return false;
    while (pos < label.size() && label[pos] >= '0' && label[pos] <= '9') ++pos;
    return pos == label.size() || (pos + 1 == label.size() && label[pos] == 'A');
}

bool technology_demand_counter_label(const std::string& label) {
    return label == "DEMANDTECH9A" || label == "DEMANDTECH10A"
        || label == "DEMANDTECH11A";
}

bool technology_demand_followup_label(const std::string& label) {
    return label == "DEMANDTECHAGAIN1" || label == "DEMANDTECHAGAIN2";
}

int technology_demand_variant(const std::string& label) {
    if (!technology_demand_label(label)) return -1;
    return atoi(label.c_str() + 10);
}

int demanded_technology_ids(const std::string& label, int ids[4]) {
    if (technology_demand_followup_label(label)) {
        ids[0] = *diplo_entry_id;
        return 1;
    }
    int variant = technology_demand_variant(label);
    if (variant < 0) return 0;
    int count = 0;
    if (variant == 6) {
        ids[count++] = *diplo_tech_id2; // Script $TECH6 only.
        return count;
    }
    ids[count++] = *diplo_entry_id; // Script $TECH0.
    if (variant == 12 || variant == 14 || variant == 15) {
        ids[count++] = *diplo_tech_id2; // Script $TECH6.
    }
    if (variant == 14 || variant == 15) {
        ids[count++] = *diplo_tech_id3; // Script $TECH8.
    }
    if (variant == 15) {
        ids[count++] = *diplo_tech_id4; // Script $TECH9.
    }
    return count;
}

bool demanded_technology_context_valid(const std::string& label, int faction_id) {
    int ids[4] = {-1, -1, -1, -1};
    int count = demanded_technology_ids(label, ids);
    if (count < 1) return false;
    for (int index = 0; index < count; ++index) {
        if (ids[index] < 0 || ids[index] >= MaxTechnologyNum
        || !(TechOwners[ids[index]] & (1 << faction_id))) return false;
        for (int prior = 0; prior < index; ++prior) {
            if (ids[prior] == ids[index]) return false;
        }
    }
    return true;
}

bool relationship_offer_label(const std::string& label) {
    return label == "FACTIONTREATY" || label == "FACTIONTRUCE"
        || label == "ALIENFACTIONTREATY" || label == "ALIENFACTIONTRUCE"
        || label == "SWEARAPACT";
}

bool attack_demand_label(const std::string& label) {
    return label.compare(0, 12, "DEMANDATTACK") == 0;
}

int joint_attack_counteroffer_tech_count(const std::string& label) {
    const char* prefix = "MAYBEWARTECH";
    size_t prefix_length = strlen(prefix);
    if (label.compare(0, prefix_length, prefix)) return -1;
    std::string suffix = label.substr(prefix_length);
    size_t digit = suffix.size();
    while (digit > 0 && suffix[digit - 1] >= '0' && suffix[digit - 1] <= '9') {
        --digit;
    }
    std::string variant = suffix.substr(0, digit);
    if (!variant.empty() && variant != "FAR" && variant != "PACT") return -1;
    if (digit == suffix.size()) return 1;
    if (suffix.size() != digit + 1 || suffix[digit] < '2' || suffix[digit] > '4') {
        return -1;
    }
    return suffix[digit] - '0';
}

bool joint_attack_energy_counteroffer_label(const std::string& label) {
    return label == "MAYBEWARPRICE";
}

bool bribe_demand_label(const std::string& label) {
    return label.compare(0, 12, "DEMANDBRIBE") == 0;
}

bool loan_offer_label(const std::string& label) {
    return label == "ENERGYLOAN1" || label == "ENERGYLOAN2"
        || label == "ASKFORLOAN1" || label == "ASKFORLOAN2";
}

bool technology_purchase_offer_label(const std::string& label) {
    return label == "BUYTECH0" || label == "BUYTECH1";
}

bool prototype_purchase_offer_label(const std::string& label) {
    return label == "BUYPROTO0";
}

bool commlink_purchase_offer_label(const std::string& label) {
    return label == "BUYCOMMLINK0";
}

bool base_purchase_offer_label(const std::string& label) {
    return label == "PAYBASESWAP";
}

bool base_technology_exchange_label(const std::string& label) {
    return label == "TECHBASESWAP";
}

bool commlink_sale_offer_label(const std::string& label) {
    return label == "ANYNEWS" || label == "ANYALIENNEWS";
}

bool commlink_technology_exchange_label(const std::string& label) {
    return label == "COMMLINKTECH";
}

int introduced_commlink_offer_mode(const std::string& label) {
    size_t prefix_length = !label.compare(0, 9, "METFRIEND") ? 9
        : !label.compare(0, 8, "METALIEN") ? 8 : 0;
    if (!prefix_length || label.size() <= prefix_length
    || label[prefix_length] < '0' || label[prefix_length] > '2') return -1;
    if (label.size() != prefix_length + 1
    && !(label.size() == prefix_length + 2
        && (label[prefix_length + 1] == 'a' || label[prefix_length + 1] == 'A'))) return -1;
    return label[prefix_length] - '0';
}

int enemy_map_offer_mode(const std::string& label) {
    return label == "ENEMYMAP1" ? 1 : label == "ENEMYMAP2" ? 2 : -1;
}

bool friendly_map_exchange_label(const std::string& label) {
    return label == "BOTHFRIEND" || label == "BOTHFRIENDALIEN";
}

bool incoming_council_vote_offer_label(const std::string& label) {
    return label == "VOTEFORME" || label == "VOTEFORMETECH";
}

int incoming_council_vote_candidate(int faction_id) {
    const char* title = agent_popup_parse_string(2);
    const char* name = agent_popup_parse_string(3);
    if (!title[0] || !name[0]) return -1;
    int match = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate == faction_id || !is_alive(candidate) || is_alien(candidate)
        || strcmp(title, MFactions[candidate].title_leader)
        || strcmp(name, MFactions[candidate].name_leader)) continue;
        if (match >= 0) return -1;
        match = candidate;
    }
    return match;
}

int council_vote_bargain_tech_count(const std::string& label) {
    const char* prefixes[] = {
        "BUYVOTEYEA", "BUYVOTENAY", "BUYVOTEABSTAIN", "BUYVOTEGOV",
    };
    for (const char* prefix : prefixes) {
        size_t length = strlen(prefix);
        if (label.compare(0, length, prefix)) continue;
        if (label.size() == length) return 0;
        if (label.size() == length + 1 && label[length] >= '1' && label[length] <= '4') {
            return label[length] - '0';
        }
    }
    return -1;
}

const char* council_vote_bargain_ballot(const std::string& label) {
    if (!label.compare(0, 10, "BUYVOTEYEA")) return "yea";
    if (!label.compare(0, 10, "BUYVOTENAY")) return "nay";
    if (!label.compare(0, 14, "BUYVOTEABSTAIN")) return "abstain";
    if (!label.compare(0, 10, "BUYVOTEGOV")) return "support_player_for_governor";
    return "unknown";
}

bool technology_sale_offer_label(const std::string& label) {
    return label == "ENERGYTECH0" || label == "ENERGYTECH1" || label == "ENERGYTECH2";
}

bool territorial_demand_label(const std::string& label) {
    return label.size() == 7 && !label.compare(0, 6, "GETOUT")
        && label[6] >= '1' && label[6] <= '5';
}

bool hostility_confirmation_label(const std::string& label) {
    return label == "BREAKINGTREATY" || label == "BREAKINGTRUCE"
        || label == "BEGINVENDETTA";
}

bool combat_confirmation_label(const std::string& label) {
    return label == "BADIDEA" || label == "GOODIDEA" || label == "GOODIDEA2"
        || label == "HASTY";
}

int territorial_incident_counterpart(int faction_id) {
    if (deferred_action.command == "move_unit") {
        int x = deferred_action.target_x;
        int y = deferred_action.target_y;
        for (int veh_id = veh_at(x, y); veh_id >= 0;
        veh_id = Vehs[veh_id].next_veh_id_stack) {
            if (Vehs[veh_id].faction_id != faction_id
            && (Vehs[veh_id].visibility & (1 << faction_id))) {
                return Vehs[veh_id].faction_id;
            }
        }
        MAP* sq = mapsq(x, y);
        int base_id = base_at(x, y);
        if (base_id >= 0 && sq && sq->is_visible(faction_id)
        && Bases[base_id].faction_id != faction_id) {
            return Bases[base_id].faction_id;
        }
        if (sq && sq->is_visible(faction_id) && sq->owner > 0
        && sq->owner != faction_id) return sq->owner;
    }
    int counterpart = *diplo_second_faction;
    return counterpart >= 1 && counterpart < MaxPlayerNum && counterpart != faction_id
        ? counterpart : -1;
}

const char* probe_action_name(int action_id) {
    switch (action_id) {
    case PRB_INFILTRATE_DATALINKS: return "infiltrate_datalinks";
    case PRB_PROCURE_RESEARCH_DATA: return "procure_research_data";
    case PRB_ACTIVATE_SABOTAGE_VIRUS: return "sabotage";
    case PRB_DRAIN_ENERGY_RESERVES: return "drain_energy_reserves";
    case PRB_INCITE_DRONE_RIOTS: return "incite_drone_riots";
    case PRB_ASSASSINATE_PROMINENT_RESEARCHERS: return "assassinate_researchers";
    case PRB_MIND_CONTROL_CITY: return "mind_control_base";
    case PRB_INTRODUCE_GENETIC_PLAGUE: return "genetic_plague";
    case PRB_FREE_CAPTURED_FACTION_LEADER: return "free_captured_leader";
    case PRB_MIND_CONTROL_UNIT: return "mind_control_unit";
    default: return "probe_action";
    }
}

bool faction_has_gene_warfare(int faction_id) {
    for (int tech_id = 0; tech_id < MaxTechnologyNum; ++tech_id) {
        if ((Tech[tech_id].flags & TFLAG_ALLOW_GENE_WARFARE)
        && has_tech(tech_id, faction_id)) return true;
    }
    return false;
}

std::string json_escape(const char* value) {
    std::ostringstream out;
    const unsigned char* p = reinterpret_cast<const unsigned char*>(value ? value : "");
    for (; *p; ++p) {
        switch (*p) {
        case '\"': out << "\\\""; break;
        case '\\': out << "\\\\"; break;
        case '\b': out << "\\b"; break;
        case '\f': out << "\\f"; break;
        case '\n': out << "\\n"; break;
        case '\r': out << "\\r"; break;
        case '\t': out << "\\t"; break;
        default:
            if (*p < 0x20 || *p >= 0x80) {
                char buf[8];
                snprintf(buf, sizeof(buf), "\\u%04x", static_cast<unsigned>(*p));
                out << buf;
            } else {
                out << static_cast<char>(*p);
            }
        }
    }
    return out.str();
}

std::string json_string(const char* value) {
    return std::string("\"") + json_escape(value) + "\"";
}

std::string field_string(const std::string& json, const char* name) {
    std::string needle = std::string("\"") + name + "\"";
    size_t pos = json.find(needle);
    if (pos == std::string::npos) return "";
    pos = json.find(':', pos + needle.size());
    if (pos == std::string::npos) return "";
    pos = json.find('\"', pos + 1);
    if (pos == std::string::npos) return "";
    std::string value;
    for (++pos; pos < json.size(); ++pos) {
        char c = json[pos];
        if (c == '\"') break;
        if (c == '\\' && pos + 1 < json.size()) {
            char escaped = json[++pos];
            if (escaped == 'n') value += '\n';
            else if (escaped == 'r') value += '\r';
            else if (escaped == 't') value += '\t';
            else value += escaped;
        } else {
            value += c;
        }
    }
    return value;
}

int field_int(const std::string& json, const char* name, int fallback) {
    std::string needle = std::string("\"") + name + "\"";
    size_t pos = json.find(needle);
    if (pos == std::string::npos) return fallback;
    pos = json.find(':', pos + needle.size());
    if (pos == std::string::npos) return fallback;
    return atoi(json.c_str() + pos + 1);
}

bool field_bool(const std::string& json, const char* name, bool fallback, bool* valid = NULL) {
    if (valid) *valid = false;
    std::string needle = std::string("\"") + name + "\"";
    size_t pos = json.find(needle);
    if (pos == std::string::npos) return fallback;
    pos = json.find(':', pos + needle.size());
    if (pos == std::string::npos) return fallback;
    ++pos;
    while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t'
    || json[pos] == '\r' || json[pos] == '\n')) ++pos;
    if (json.compare(pos, 4, "true") == 0) {
        if (valid) *valid = true;
        return true;
    }
    if (json.compare(pos, 5, "false") == 0) {
        if (valid) *valid = true;
        return false;
    }
    if (pos < json.size() && (json[pos] == '0' || json[pos] == '1')) {
        if (valid) *valid = true;
        return json[pos] == '1';
    }
    return fallback;
}

std::string error_response(const char* code, const char* message) {
    return std::string("{\"ok\":false,\"error\":{\"code\":") + json_string(code)
        + ",\"message\":" + json_string(message) + "}}";
}

void append_chat_event(bool outbound, bool broadcast, int sender_faction_id,
int recipient_faction_id, const char* text, const char* client_message_id = "") {
    if (chat_event_count == MaxChatEvents) {
        for (size_t i = 1; i < MaxChatEvents; ++i) {
            chat_events[i - 1] = chat_events[i];
        }
        --chat_event_count;
    }
    ChatEvent& event = chat_events[chat_event_count++];
    event.sequence = next_chat_sequence++;
    event.outbound = outbound;
    event.broadcast = broadcast;
    event.sender_faction_id = sender_faction_id;
    event.recipient_faction_id = recipient_faction_id;
    event.turn = game_active() ? *CurrentTurn : -1;
    event.text = text ? text : "";
    event.client_message_id = client_message_id ? client_message_id : "";
    append_observation_event(outbound ? "chat_outbound" : "chat_inbound",
        event.turn, sender_faction_id, recipient_faction_id);
}

const ChatEvent* find_outbound_chat(const std::string& client_message_id) {
    if (client_message_id.empty()) return NULL;
    for (size_t i = 0; i < chat_event_count; ++i) {
        if (chat_events[i].outbound
        && chat_events[i].client_message_id == client_message_id) {
            return &chat_events[i];
        }
    }
    return NULL;
}

void append_chat_event_json(std::ostringstream& out, const ChatEvent& event) {
    out << "{\"sequence\":" << event.sequence
        << ",\"direction\":" << json_string(event.outbound ? "outbound" : "inbound")
        << ",\"channel\":" << json_string(event.outbound
            ? (event.broadcast ? "broadcast" : "private") : "received")
        << ",\"sender_faction_id\":" << event.sender_faction_id
        << ",\"recipient_faction_id\":";
    if (event.recipient_faction_id >= 0) out << event.recipient_faction_id;
    else out << "null";
    out << ",\"turn\":" << event.turn
        << ",\"text\":" << json_string(event.text.c_str());
    if (!event.client_message_id.empty()) {
        out << ",\"client_message_id\":"
            << json_string(event.client_message_id.c_str());
    }
    out << '}';
}

void begin_deferred_action(const char* command, int unit_id = -1,
int origin_x = -1, int origin_y = -1, int target_x = -1, int target_y = -1) {
    deferred_action.id = next_deferred_action_id++;
    if (!next_deferred_action_id) next_deferred_action_id = 1;
    deferred_action.command = command;
    deferred_action.status = "pending";
    deferred_action.native_result = 0;
    deferred_action.unit_id = unit_id;
    deferred_action.origin_x = origin_x;
    deferred_action.origin_y = origin_y;
    deferred_action.target_x = target_x;
    deferred_action.target_y = target_y;
    deferred_action.observed_x = origin_x;
    deferred_action.observed_y = origin_y;
    deferred_action.resolution.clear();
    append_observation_event("deferred_action_queued", *CurrentTurn,
        static_cast<int>(deferred_action.id), unit_id);
}

std::string deferred_action_response(uint32_t requested_id = 0) {
    if (!deferred_action.id) {
        return "{\"ok\":true,\"action\":null}";
    }
    if (requested_id && requested_id != deferred_action.id) {
        return error_response("action_status_expired",
            "Only the most recently queued deferred action remains available; observe fresh state.");
    }
    std::ostringstream out;
    out << "{\"ok\":true,\"action\":{\"action_id\":" << deferred_action.id
        << ",\"command\":" << json_string(deferred_action.command.c_str())
        << ",\"status\":" << json_string(deferred_action.status.c_str())
        << ",\"native_result\":" << deferred_action.native_result;
    if (!deferred_action.resolution.empty()) {
        out << ",\"resolution\":" << json_string(deferred_action.resolution.c_str());
    }
    if (deferred_action.unit_id >= 0) {
        out << ",\"unit_id\":" << deferred_action.unit_id
            << ",\"origin_tile_id\":"
            << semantic_tile_id(deferred_action.origin_x, deferred_action.origin_y)
            << ",\"target_tile_id\":"
            << semantic_tile_id(deferred_action.target_x, deferred_action.target_y)
            << ",\"observed_tile_id\":"
            << semantic_tile_id(deferred_action.observed_x, deferred_action.observed_y);
    }
    out << "}}";
    return out.str();
}

bool active_faction(int& faction_id) {
    faction_id = *CurrentPlayerFaction;
    return faction_id >= 1 && faction_id <= 7;
}

bool game_active() {
    int faction_id = 0;
    return active_faction(faction_id) && *MapAreaTiles > 0 && *MapAreaX > 0
        && *MapAreaY > 0 && *MapTiles != NULL && *BaseCount > 0;
}

void refresh_deferred_end_turn_state() {
    if (deferred_end_turn_faction_id < 0) return;
    if (!game_active()) {
        deferred_end_turn_faction_id = -1;
        deferred_end_turn_source_turn = -1;
        deferred_action.status = "rejected";
        deferred_action.resolution = "game_no_longer_active";
        return;
    }
    if (*CurrentTurn == deferred_end_turn_source_turn) return;
    deferred_end_turn_faction_id = -1;
    deferred_end_turn_source_turn = -1;
    deferred_action.native_result = 1;
    deferred_action.status = "completed";
    deferred_action.resolution = "native_turn_advanced";
}

void CALLBACK deferred_end_turn_timer_proc(HWND, UINT, UINT_PTR timer_id, DWORD) {
    KillTimer(NULL, timer_id);
    deferred_end_turn_timer_id = 0;
    const int faction_id = deferred_end_turn_faction_id;
    const int source_turn = deferred_end_turn_source_turn;
    const bool actionable = game_active()
        && faction_id == *CurrentPlayerFaction
        && source_turn == *CurrentTurn
        && human_turn_actionable(faction_id);
    if (!actionable) {
        deferred_end_turn_faction_id = -1;
        deferred_end_turn_source_turn = -1;
        deferred_action.status = "rejected";
        deferred_action.resolution = "native_turn_no_longer_actionable";
        return;
    }
    // Run through the same native semantic event as the Turn Complete control,
    // but from a Windows timer dispatch after the bridge request has replied.
    // Console::on_key_click may remain synchronous through bot turns and modal
    // technology/research screens; with no bridge request frame underneath it,
    // those interactions remain independently observable and resolvable.
    Console_on_key_click(MapWin, 0, NativeEndTurnCommand);
    refresh_deferred_end_turn_state();
}

bool end_turn_completion_pending() {
    if (!pending_end_turn_completion) return false;
    if (!game_active() || *CurrentTurn != pending_end_turn_source_turn) {
        pending_end_turn_completion = false;
        pending_end_turn_source_turn = -1;
        return false;
    }
    // A native decision can interrupt the confirmed transition before the
    // turn counter advances (for example, an incoming contact). That new
    // modal supersedes the wait state and must be resolved before the engine
    // can finish the turn. Keep blocking ordinary mutations, but release this
    // narrow latch once a genuinely new interaction is actionable.
    std::string current_interaction = interaction_kind(*CurrentPlayerFaction);
    if (current_interaction != "turn"
    && current_interaction != "waiting_for_engine"
    && current_interaction != "waiting_for_turn"
    && (current_interaction != "popup"
        || strcmp(agent_popup_label(), "REALLYOVER"))) {
        pending_end_turn_completion = false;
        pending_end_turn_source_turn = -1;
        return false;
    }
    return true;
}

bool semantic_native_automation_active(const VEH& veh) {
    if (!(veh.state & VSTATE_ON_ALERT) || veh.unit_id < 0 || veh.unit_id >= MaxProtoNum) {
        return false;
    }
    return Units[veh.unit_id].plan == PLAN_TERRAFORM
        || veh.order_auto_type == ORDERA_BOMBING_RUN
        || veh.order_auto_type == ORDERA_ON_ALERT
        || veh.order_auto_type == ORDERA_AUTOMATE_AIR_DEFENSE;
}

int semantic_carrier_capacity(int veh_id) {
    if (veh_id < 0 || veh_id >= *VehCount
    || !has_abil(Vehs[veh_id].unit_id, ABL_CARRIER)) return 0;
    return max(1, veh_cargo(veh_id));
}

bool semantic_aircraft_boarded_on(int aircraft_id, int carrier_id) {
    if (aircraft_id < 0 || aircraft_id >= *VehCount
    || carrier_id < 0 || carrier_id >= *VehCount || aircraft_id == carrier_id) {
        return false;
    }
    VEH& aircraft = Vehs[aircraft_id];
    VEH& carrier = Vehs[carrier_id];
    return semantic_carrier_capacity(carrier_id) > 0
        && aircraft.triad() == TRIAD_AIR
        && aircraft.order == ORDER_SENTRY_BOARD
        && aircraft.waypoint_x[0] == carrier_id
        && aircraft.faction_id == carrier.faction_id
        && aircraft.x == carrier.x && aircraft.y == carrier.y;
}

bool semantic_unit_requires_decision(int veh_id) {
    if (veh_id < 0 || veh_id >= *VehCount) return false;
    VEH& veh = Vehs[veh_id];
    if (!veh_unmoved(veh_id)) {
        if (veh.triad() != TRIAD_AIR || veh.is_missile() || !veh.range()
        || veh.order != ORDER_NONE) return false;
        for (int carrier_id = veh_at(veh.x, veh.y); carrier_id >= 0;
        carrier_id = Vehs[carrier_id].next_veh_id_stack) {
            if (carrier_id != veh_id
            && Vehs[carrier_id].faction_id == veh.faction_id
            && semantic_carrier_capacity(carrier_id) > 0) return true;
        }
        return false;
    }
    if (veh.state & VSTATE_EXPLORE) return false;
    if (semantic_native_automation_active(veh)) return false;
    if (veh.order == ORDER_NONE) return true;
    if (veh.order != ORDER_SENTRY_BOARD || veh.waypoint_x[0] < 0
    || veh.waypoint_x[0] >= *VehCount || veh.waypoint_x[0] == veh_id) return false;
    VEH& transport = Vehs[veh.waypoint_x[0]];
    return transport.faction_id == veh.faction_id && transport.x == veh.x
        && transport.y == veh.y
        && (veh_cargo(veh.waypoint_x[0]) > 0
            || semantic_aircraft_boarded_on(veh_id, veh.waypoint_x[0]));
}

bool test_transport_fixture_initialized = false;
bool test_artillery_fixture_initialized = false;
bool test_probe_fixture_initialized = false;
bool test_psi_gate_fixture_initialized = false;
bool test_order_fixture_initialized = false;
bool test_return_home_fixture_initialized = false;
bool test_base_action_fixture_initialized = false;
bool test_terrain_destruction_fixture_initialized = false;
bool test_missile_fixture_initialized = false;
bool test_air_automation_fixture_initialized = false;
bool test_bombing_run_fixture_initialized = false;
bool test_hostility_fixture_initialized = false;
bool test_combat_confirmation_fixture_initialized = false;
bool test_nerve_gas_fixture_initialized = false;
int test_nerve_gas_attacker_id = -1;
int test_nerve_gas_defender_faction_id = -1;
int test_nerve_gas_atrocities_before = -1;
bool test_self_destruct_fixture_initialized = false;
int test_self_destruct_origin_x = -1;
int test_self_destruct_origin_y = -1;
int test_self_destruct_target_x = -1;
int test_self_destruct_target_y = -1;
int test_self_destruct_blast_damage = -1;
bool test_base_status_fixture_initialized = false;
int test_base_status_fixture_base_id = -1;
bool test_production_notice_fixture_initialized = false;
bool test_endgame_fixture_initialized = false;
int test_endgame_fixture_stage = -1;
bool test_full_endgame_fixture_initialized = false;
bool test_full_endgame_fixture_pending = false;
bool test_full_endgame_narrative = false;
bool test_full_endgame_result_captured = false;
bool test_full_endgame_final_score_done = false;
int test_full_endgame_control_turn_a = 0;
int test_full_endgame_control_turn_b = 0;
bool test_economic_victory_fixture_initialized = false;
bool deferred_corner_market_notice = false;
bool test_diplomatic_purchase_fixture_initialized = false;
int test_diplomatic_purchase_fixture_stage = -1;
int test_diplomatic_purchase_seller_id = -1;
int test_diplomatic_purchase_target_id = -1;
int test_diplomatic_purchase_prototype_id = -1;
bool test_base_purchase_fixture_initialized = false;
int test_base_purchase_fixture_stage = -1;
int test_base_purchase_seller_id = -1;
int test_base_purchase_base_id = -1;
bool test_base_obliteration_fixture_initialized = false;
int test_base_obliteration_base_id = -1;
bool test_single_unit_upgrade_fixture_initialized = false;
bool test_artifact_fixture_initialized = false;
bool test_council_bargain_fixture_initialized = false;
int test_council_bargain_fixture_stage = -1;
int test_council_bargain_other_id = -1;
bool test_energy_gift_fixture_initialized = false;
int test_energy_gift_fixture_stage = -1;
int test_energy_gift_other_id = -1;
bool test_unit_gift_fixture_initialized = false;
bool test_proposal_guard_fixture_initialized = false;
int test_proposal_guard_fixture_stage = -1;
int test_proposal_guard_other_id = -1;
bool test_incoming_vote_offer_fixture_initialized = false;
int test_incoming_vote_offer_fixture_stage = -1;
int test_incoming_vote_offer_other_id = -1;
bool test_incoming_vote_offer_technologies = false;
bool test_joint_attack_counteroffer_fixture_initialized = false;
int test_joint_attack_counteroffer_fixture_stage = -1;
int test_joint_attack_counteroffer_other_id = -1;
int test_joint_attack_counteroffer_target_id = -1;
bool test_technology_demand_fixture_initialized = false;
int test_technology_demand_fixture_stage = -1;
int test_technology_demand_other_id = -1;
int test_technology_demand_ids[4] = {-1, -1, -1, -1};
int test_technology_demand_distractor_id = -1;
int test_technology_demand_fixture_mode = 0; // 0=bundle, 1=energy counter, 2=tech counter
int test_technology_demand_initial_result = -1;
int test_technology_demand_followup_result = -1;

int owned_headquarters_base(int faction_id) {
    for (int base_id = 0; base_id < *BaseCount; ++base_id) {
        if (Bases[base_id].faction_id == faction_id
        && has_fac_built(FAC_HEADQUARTERS, base_id)) return base_id;
    }
    return -1;
}

void ensure_test_economic_victory_fixture() {
    if (test_economic_victory_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_economic[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_ECONOMIC_VICTORY", test_economic,
        sizeof(test_economic)) || strcmp(test_economic, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    int base_id = first_owned_base(faction_id);
    if (base_id < 0 || !human_turn_actionable(faction_id)) return;
    *GameRules |= RULES_VICTORY_ECONOMIC;
    int prerequisite = Rules->tech_preq_economic_victory;
    if (prerequisite >= 0 && prerequisite < MaxTechnologyNum) {
        TechOwners[prerequisite] |= 1 << faction_id;
    }
    set_fac(FAC_HEADQUARTERS, base_id, true);
    Faction& faction = Factions[faction_id];
    faction.corner_market_turn = 0;
    faction.corner_market_cost = 0;
    faction.energy_credits = max(faction.energy_credits, corner_market(faction_id) + 100);
    test_economic_victory_fixture_initialized = true;
}

void ensure_test_endgame_fixture() {
    if (test_endgame_fixture_initialized || test_endgame_fixture_stage >= 0
    || !game_active()) return;
    char test_mode[8] = {};
    char test_endgame[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_ENDGAME", test_endgame,
        sizeof(test_endgame)) || strcmp(test_endgame, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate) && !is_human(candidate)) {
            other = candidate;
            break;
        }
    }
    if (other < 0) return;
    *diplo_second_faction = other;
    parse_says(0, get_title(other), -1, -1);
    parse_says(1, get_name(other), -1, -1);
    parse_says(2, get_noun(other), -1, -1);
    test_endgame_fixture_initialized = true;
    test_endgame_fixture_stage = 0;
    PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
}

void ensure_test_full_endgame_fixture() {
    if (test_full_endgame_fixture_initialized
    || test_full_endgame_fixture_pending || !game_active()) return;
    char test_mode[8] = {};
    char test_endgame[16] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_FULL_ENDGAME", test_endgame,
        sizeof(test_endgame))
    || (strcmp(test_endgame, "1") && strcmp(test_endgame, "narrative"))) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    test_full_endgame_fixture_initialized = true;
    test_full_endgame_fixture_pending = true;
    test_full_endgame_narrative = !strcmp(test_endgame, "narrative");
    PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
}

void ensure_test_diplomatic_purchase_fixture() {
    char test_mode[8] = {};
    char test_purchases[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_DIPLO_PURCHASES", test_purchases,
        sizeof(test_purchases)) || strcmp(test_purchases, "1") || !game_active()) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    if (!test_diplomatic_purchase_fixture_initialized) {
        int seller = -1;
        int target = -1;
        for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
            if (candidate != faction_id && is_alive(candidate) && !is_human(candidate)) {
                if (seller < 0) seller = candidate;
                else {
                    target = candidate;
                    break;
                }
            }
        }
        if (seller < 0 || target < 0) return;
        int laser_tech = Weapon[WPN_LASER].preq_tech;
        if (laser_tech >= 0 && laser_tech < MaxTechnologyNum) {
            TechOwners[laser_tech] |= 1 << seller;
        }
        char design_name[] = "Harness Purchased Laser";
        int prototype_id = propose_proto(seller, CHS_INFANTRY, WPN_LASER,
            ARM_NO_ARMOR, 0, REC_FISSION, PLAN_AUTO_CALCULATE, design_name);
        if (prototype_id < seller * MaxProtoFactionNum
        || prototype_id >= min(MaxProtoNum, (seller + 1) * MaxProtoFactionNum)) return;
        Units[prototype_id].unit_flags |= UNIT_PROTOTYPED;
        treaty_on(faction_id, seller, DIPLO_COMMLINK);
        treaty_on(faction_id, seller, DIPLO_TREATY);
        treaty_on(seller, target, DIPLO_COMMLINK);
        treaty_off(faction_id, target, DIPLO_COMMLINK);
        treaty_off(target, faction_id, DIPLO_COMMLINK);
        Factions[faction_id].energy_credits = max(Factions[faction_id].energy_credits, 20000);
        *DiploFriction = 0;
        test_diplomatic_purchase_seller_id = seller;
        test_diplomatic_purchase_target_id = target;
        test_diplomatic_purchase_prototype_id = prototype_id;
        test_diplomatic_purchase_fixture_initialized = true;
        test_diplomatic_purchase_fixture_stage = 0;
    }
    if (test_diplomatic_purchase_fixture_stage == 0
    || test_diplomatic_purchase_fixture_stage == 2) {
        ++test_diplomatic_purchase_fixture_stage;
        PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
    }
}

void ensure_test_energy_gift_fixture() {
    if (test_energy_gift_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_gift[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_ENERGY_GIFT", test_gift,
        sizeof(test_gift)) || strcmp(test_gift, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate) && !is_human(candidate)) {
            other = candidate;
            break;
        }
    }
    if (other < 0) return;
    treaty_on(faction_id, other, DIPLO_COMMLINK);
    treaty_on(faction_id, other, DIPLO_TREATY);
    Factions[faction_id].energy_credits = 500;
    Factions[other].energy_credits = 1000;
    *diplo_second_faction = other;
    *diplo_current_proposal_id = DiploProposalMakeGift;
    *diplo_counter_proposal_id = DiploCounterEnergyPayment;
    *DiploFriction = 0;
    test_energy_gift_other_id = other;
    test_energy_gift_fixture_initialized = true;
    test_energy_gift_fixture_stage = 0;
    PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
}

void ensure_test_unit_gift_fixture() {
    if (test_unit_gift_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_units[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_UNIT_GIFT", test_units,
        sizeof(test_units)) || strcmp(test_units, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate) && !is_human(candidate)
        && !is_alien(candidate)) {
            other = candidate;
            break;
        }
    }
    int base_id = first_owned_base(faction_id);
    if (other < 0 || base_id < 0) return;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != other && candidate != faction_id) {
            treaty_off(faction_id, candidate, DIPLO_COMMLINK);
        }
    }
    treaty_off(faction_id, other, DIPLO_VENDETTA | DIPLO_TRUCE);
    treaty_on(faction_id, other, DIPLO_COMMLINK | DIPLO_TREATY | DIPLO_PACT);
    Factions[faction_id].diplo_spoke[other] = -100;
    Factions[other].diplo_spoke[faction_id] = -100;
    Factions[other].diplo_patience[faction_id] = 8;
    *DiploFriction = 0;
    int unit_x = -1;
    int unit_y = -1;
    for (int y = 0; y < *MapAreaY && unit_x < 0; ++y) {
        for (int x = y & 1; x < *MapAreaX && unit_x < 0; x += 2) {
            MAP* sq = mapsq(x, y);
            int range = map_range(Bases[base_id].x, Bases[base_id].y, x, y);
            if (!sq || is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0
            || range < 4 || range > 8) continue;
            unit_x = x;
            unit_y = y;
        }
    }
    if (unit_x < 0) return;
    int unit_id = veh_init(BSC_SCOUT_PATROL, faction_id, unit_x, unit_y);
    if (unit_id < 0) return;
    Vehs[unit_id].moves_spent = 0;
    spot_all(unit_id, 1);
    mapsq(unit_x, unit_y)->owner = other;
    test_unit_gift_fixture_initialized = true;
}

void ensure_test_proposal_guard_fixture() {
    if (test_proposal_guard_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_proposal[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_PROPOSAL_GUARD", test_proposal,
        sizeof(test_proposal)) || strcmp(test_proposal, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate) && !is_human(candidate)
        && !is_alien(candidate)) {
            other = candidate;
            break;
        }
    }
    if (other < 0) return;
    treaty_on(faction_id, other, DIPLO_COMMLINK | DIPLO_TREATY | DIPLO_PACT);
    *diplo_second_faction = other;
    test_proposal_guard_other_id = other;
    test_proposal_guard_fixture_initialized = true;
    test_proposal_guard_fixture_stage = 0;
    PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
}

void ensure_test_incoming_vote_offer_fixture() {
    if (test_incoming_vote_offer_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_offer[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_INCOMING_VOTE_OFFER", test_offer,
        sizeof(test_offer))
    || (strcmp(test_offer, "1") && strcmp(test_offer, "energy")
        && strcmp(test_offer, "tech"))) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate) && !is_human(candidate)
        && !is_alien(candidate)) {
            other = candidate;
            break;
        }
    }
    if (other < 0) return;
    treaty_on(faction_id, other, DIPLO_COMMLINK | DIPLO_TREATY);
    test_incoming_vote_offer_technologies = !strcmp(test_offer, "tech");
    if (test_incoming_vote_offer_technologies) {
        int first = -1;
        int second = -1;
        for (int tech_id = 0; tech_id < MaxTechnologyNum && second < 0; ++tech_id) {
            if (!Tech[tech_id].name[0]
            || (TechOwners[tech_id] & (1 << faction_id))) continue;
            if (first < 0) first = tech_id;
            else second = tech_id;
        }
        if (first < 0 || second < 0) return;
        TechOwners[first] |= 1 << other;
        TechOwners[second] |= 1 << other;
        *diplo_tech_id1 = first;
        *diplo_vote_offer_tech_id2 = second;
    } else {
        Factions[faction_id].energy_credits = 100;
        Factions[other].energy_credits = max(Factions[other].energy_credits, 1000);
    }
    test_incoming_vote_offer_other_id = other;
    test_incoming_vote_offer_fixture_initialized = true;
    test_incoming_vote_offer_fixture_stage = 0;
    PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
}

void ensure_test_joint_attack_counteroffer_fixture() {
    if (test_joint_attack_counteroffer_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_offer[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_JOINT_ATTACK_COUNTEROFFER",
        test_offer, sizeof(test_offer)) || strcmp(test_offer, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int other = -1;
    int target = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate == faction_id || !is_alive(candidate) || is_human(candidate)
        || is_alien(candidate)) continue;
        if (other < 0) other = candidate;
        else {
            target = candidate;
            break;
        }
    }
    if (other < 0 || target < 0) return;
    treaty_off(faction_id, other, DIPLO_VENDETTA | DIPLO_TRUCE);
    treaty_on(faction_id, other, DIPLO_COMMLINK | DIPLO_TREATY | DIPLO_PACT);
    treaty_off(other, target, DIPLO_VENDETTA | DIPLO_TRUCE | DIPLO_TREATY | DIPLO_PACT);
    treaty_on(other, target, DIPLO_COMMLINK);
    treaty_off(faction_id, target, DIPLO_TRUCE | DIPLO_TREATY | DIPLO_PACT);
    treaty_on(faction_id, target, DIPLO_COMMLINK | DIPLO_VENDETTA);
    Factions[faction_id].energy_credits = 20000;
    Factions[faction_id].diplo_spoke[other] = -100;
    Factions[other].diplo_spoke[faction_id] = -100;
    Factions[other].diplo_patience[faction_id] = 8;
    *DiploFriction = 0;
    *diplo_second_faction = other;
    *diplo_trade_faction_id = target;
    *diplo_counter_proposal_id = 2;
    *diplo_entry_id = -1;
    *diplo_tech_id2 = -1;
    *diplo_tech_id3 = -1;
    *diplo_tech_id4 = -1;
    test_joint_attack_counteroffer_other_id = other;
    test_joint_attack_counteroffer_target_id = target;
    test_joint_attack_counteroffer_fixture_initialized = true;
    test_joint_attack_counteroffer_fixture_stage = 0;
    PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
}

void ensure_test_base_purchase_fixture() {
    if (test_base_purchase_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_purchase[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_BASE_PURCHASE", test_purchase,
        sizeof(test_purchase)) || strcmp(test_purchase, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int seller = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate) && !is_human(candidate)) {
            seller = candidate;
            break;
        }
    }
    if (seller < 0) return;
    int base_x = -1;
    int base_y = -1;
    for (int y = 0; y < *MapAreaY && base_x < 0; ++y) {
        for (int x = y & 1; x < *MapAreaX; x += 2) {
            MAP* sq = mapsq(x, y);
            if (sq && !is_ocean(sq) && sq->base_who() < 0 && veh_at(x, y) < 0) {
                base_x = x;
                base_y = y;
                break;
            }
        }
    }
    if (base_x < 0) return;
    int base_id = mod_base_init(seller, base_x, base_y);
    if (base_id < 0 || base_id >= *BaseCount) return;
    strcpy_n(Bases[base_id].name, sizeof(BASE::name), "Harness Purchase Base");
    treaty_on(faction_id, seller, DIPLO_COMMLINK);
    treaty_on(faction_id, seller, DIPLO_TREATY);
    Factions[faction_id].energy_credits = max(Factions[faction_id].energy_credits, 20000);
    *diplo_second_faction = seller;
    *diplo_ask_base_swap_id = base_id;
    *diplo_current_proposal_id = DiploProposalBaseSwap;
    *diplo_counter_proposal_id = DiploCounterEnergyPayment;
    *DiploFriction = 0;
    test_base_purchase_seller_id = seller;
    test_base_purchase_base_id = base_id;
    test_base_purchase_fixture_initialized = true;
    test_base_purchase_fixture_stage = 0;
    PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
}

void ensure_test_base_obliteration_fixture() {
    if (test_base_obliteration_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_obliteration[16] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_BASE_OBLITERATION",
        test_obliteration, sizeof(test_obliteration))
    || (strcmp(test_obliteration, "1") && strcmp(test_obliteration, "objective")
        && strcmp(test_obliteration, "noatrocity"))) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int first_base_id = first_owned_base(faction_id);
    if (first_base_id < 0) return;
    if (!strcmp(test_obliteration, "objective")) {
        *GameRules |= RULES_SCN_VICT_ALL_BASE_COUNT_OBJ;
        test_base_obliteration_base_id = first_base_id;
        test_base_obliteration_fixture_initialized = true;
        return;
    }
    if (!strcmp(test_obliteration, "noatrocity")) {
        Rules->tgl_oblit_base_atrocity = 0;
    }
    int former_faction_id = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate)) {
            former_faction_id = candidate;
            break;
        }
    }
    if (former_faction_id < 0) return;
    int base_x = -1;
    int base_y = -1;
    for (int y = 0; y < *MapAreaY && base_x < 0; ++y) {
        for (int x = y & 1; x < *MapAreaX; x += 2) {
            MAP* sq = mapsq(x, y);
            int range = map_range(Bases[first_base_id].x, Bases[first_base_id].y, x, y);
            if (!sq || is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0
            || range < 4 || range > 8) continue;
            base_x = x;
            base_y = y;
            break;
        }
    }
    if (base_x < 0) return;
    int base_id = mod_base_init(former_faction_id, base_x, base_y);
    if (base_id < 0 || base_id >= *BaseCount) return;
    strcpy_n(Bases[base_id].name, sizeof(BASE::name), "Harness Oblit Base");
    Bases[base_id].pop_size = max(3, static_cast<int>(Bases[base_id].pop_size));
    net_cede_base(base_id, faction_id, 1);
    if (base_id >= *BaseCount || Bases[base_id].faction_id != faction_id) {
        if (base_id < *BaseCount) mod_base_kill(base_id);
        return;
    }
    // Peaceful cession correctly updates all ownership counts but records the
    // recipient as the former owner.  The fixture models a captured base so
    // the real atrocity path has a distinct, living victim faction.
    Bases[base_id].faction_id_former = former_faction_id;
    treaty_on(faction_id, former_faction_id, DIPLO_COMMLINK | DIPLO_VENDETTA);
    int unit_id = veh_init(BSC_SCOUT_PATROL, faction_id, base_x, base_y);
    if (unit_id < 0) {
        mod_base_kill(base_id);
        return;
    }
    Vehs[unit_id].moves_spent = 0;
    spot_all(unit_id, 1);
    test_base_obliteration_base_id = base_id;
    test_base_obliteration_fixture_initialized = true;
}

void ensure_test_council_bargain_fixture() {
    if (test_council_bargain_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_bargain[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_COUNCIL_BARGAIN", test_bargain,
        sizeof(test_bargain)) || strcmp(test_bargain, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate) && !is_human(candidate)
        && !is_alien(candidate)) {
            other = candidate;
            break;
        }
    }
    if (other < 0) return;
    treaty_on(faction_id, other, DIPLO_COMMLINK);
    treaty_on(faction_id, other, DIPLO_TREATY);
    treaty_on(faction_id, other, DIPLO_PACT);
    Factions[faction_id].energy_credits = max(Factions[faction_id].energy_credits, 20000);
    for (int tech_id = 0; tech_id < min(12, MaxTechnologyNum); ++tech_id) {
        if (Tech[tech_id].name[0]) {
            TechOwners[tech_id] |= 1 << faction_id;
            TechOwners[tech_id] &= ~(1 << other);
        }
    }
    *CouncilProposal = PROP_GLOBAL_TRADE_PACT;
    CouncilVoteState[other] = -2;
    test_council_bargain_other_id = other;
    test_council_bargain_fixture_initialized = true;
    test_council_bargain_fixture_stage = 0;
    PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
}

void ensure_test_loan_fixture() {
    if (test_loan_fixture_initialized || test_loan_fixture_other_faction_id >= 0
    || !game_active()) return;
    char test_mode[8] = {};
    char test_loans[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_LOANS", test_loans,
        sizeof(test_loans)) || strcmp(test_loans, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate) && !is_human(candidate)) {
            other = candidate;
            break;
        }
    }
    if (other < 0) return;
    treaty_on(faction_id, other, DIPLO_COMMLINK);
    treaty_on(faction_id, other, DIPLO_TREATY);
    treaty_on(faction_id, other, DIPLO_PACT);
    Factions[faction_id].energy_credits = 100;
    Factions[other].energy_credits = 2000;
    Factions[faction_id].loan_balance[other] = 0;
    Factions[faction_id].loan_payment[other] = 0;
    Factions[other].diplo_patience[faction_id] = 0;
    test_loan_fixture_other_faction_id = other;
    test_loan_fixture_initialized = true;
    PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
}

void ensure_test_commerce_fixture() {
    if (test_commerce_fixture_initialized || test_commerce_fixture_other_faction_id >= 0
    || !game_active()) return;
    char test_mode[8] = {};
    char test_commerce[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_COMMERCE", test_commerce,
        sizeof(test_commerce)) || strcmp(test_commerce, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int other = -1;
    int technology_id = -1;
    for (int candidate = 1; candidate < MaxPlayerNum && technology_id < 0; ++candidate) {
        if (candidate == faction_id || !is_alive(candidate) || is_human(candidate)) continue;
        for (int tech_id = 0; tech_id < MaxTechnologyNum; ++tech_id) {
            if ((TechOwners[tech_id] & (1 << candidate))
            && !(TechOwners[tech_id] & (1 << faction_id))) {
                other = candidate;
                technology_id = tech_id;
                break;
            }
        }
    }
    if (other < 0 || technology_id < 0) return;
    treaty_on(faction_id, other, DIPLO_COMMLINK);
    treaty_on(faction_id, other, DIPLO_TREATY);
    Factions[faction_id].energy_credits = 20000;
    *diplo_tech_id1 = technology_id;
    test_commerce_fixture_other_faction_id = other;
    test_commerce_fixture_initialized = true;
    PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
}

void ensure_test_technology_demand_fixture() {
    if (test_technology_demand_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_demand[16] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_TECH_DEMAND", test_demand,
        sizeof(test_demand))
    || (strcmp(test_demand, "1") && strcmp(test_demand, "energy")
        && strcmp(test_demand, "tech"))) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate) && !is_human(candidate)
        && !is_alien(candidate)) {
            other = candidate;
            break;
        }
    }
    if (other < 0) return;
    int selected = 0;
    for (int tech_id = 0; tech_id < MaxTechnologyNum && selected < 5; ++tech_id) {
        if (!Tech[tech_id].name[0]) continue;
        TechOwners[tech_id] |= 1 << faction_id;
        TechOwners[tech_id] &= ~(1 << other);
        if (selected < 4) test_technology_demand_ids[selected] = tech_id;
        else test_technology_demand_distractor_id = tech_id;
        ++selected;
    }
    if (selected < 5) return;
    test_technology_demand_fixture_mode = !strcmp(test_demand, "energy") ? 1
        : !strcmp(test_demand, "tech") ? 2 : 0;
    if (test_technology_demand_fixture_mode) {
        // The counter dialog names this as the counterpart's reciprocal
        // $TECH6, so it must not also be player-owned.
        TechOwners[test_technology_demand_ids[1]] &= ~(1 << faction_id);
        TechOwners[test_technology_demand_ids[1]] |= 1 << other;
    }
    treaty_on(faction_id, other, DIPLO_COMMLINK | DIPLO_TREATY);
    *diplo_second_faction = other;
    *diplo_entry_id = test_technology_demand_ids[0];
    *diplo_tech_id2 = test_technology_demand_ids[1];
    *diplo_tech_id3 = test_technology_demand_ids[2];
    *diplo_tech_id4 = test_technology_demand_ids[3];
    // This unrelated scratch slot was the source of the retired heuristic's
    // false fifth disclosure and must never appear in a demand bundle.
    *diplo_tech_id1 = test_technology_demand_distractor_id;
    test_technology_demand_other_id = other;
    test_technology_demand_fixture_initialized = true;
    test_technology_demand_fixture_stage = 0;
    PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
}

void ensure_test_hostility_fixture() {
    if (test_hostility_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_hostility[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_HOSTILITY", test_hostility,
        sizeof(test_hostility)) || strcmp(test_hostility, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    int base_id = first_owned_base(faction_id);
    if (base_id < 0) return;
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate) && !is_human(candidate)) {
            other = candidate;
            break;
        }
    }
    if (other < 0) return;
    int target_x = -1;
    int target_y = -1;
    for (int dir = 0; dir < 8; ++dir) {
        int x = wrap(Bases[base_id].x + BaseOffsetX[dir]);
        int y = Bases[base_id].y + BaseOffsetY[dir];
        MAP* sq = mapsq(x, y);
        if (sq && !is_ocean(sq) && base_at(x, y) < 0 && veh_at(x, y) < 0) {
            target_x = x;
            target_y = y;
            break;
        }
    }
    if (target_x < 0) return;
    int attacker_id = -1;
    for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
        VEH& veh = Vehs[veh_id];
        if (veh.faction_id == faction_id && veh.unit_id == BSC_SCOUT_PATROL
        && veh.x == Bases[base_id].x && veh.y == Bases[base_id].y) {
            attacker_id = veh_id;
            break;
        }
    }
    if (attacker_id < 0) return;
    // Establish contact before placing the defender. Creating a newly encountered
    // faction's vehicle can otherwise synchronously enter first-contact UI while a
    // bridge request owns the UI thread. Direct symmetric state is safe here because
    // this path is test-mode-only.
    const uint32_t clear_status = DIPLO_VENDETTA|DIPLO_TRUCE|DIPLO_PACT;
    Factions[faction_id].diplo_status[other] &= ~clear_status;
    Factions[other].diplo_status[faction_id] &= ~clear_status;
    Factions[faction_id].diplo_status[other] |= DIPLO_COMMLINK|DIPLO_TREATY;
    Factions[other].diplo_status[faction_id] |= DIPLO_COMMLINK|DIPLO_TREATY;
    int defender_id = -1;
    for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
        if (Vehs[veh_id].faction_id == other
        && Vehs[veh_id].unit_id == BSC_SCOUT_PATROL) {
            defender_id = veh_id;
            break;
        }
    }
    if (defender_id < 0) return;
    veh_put(defender_id, target_x, target_y);
    Vehs[defender_id].visibility |= 1 << faction_id;
    test_hostility_fixture_initialized = true;
}

void ensure_test_combat_confirmation_fixture() {
    if (test_combat_confirmation_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_combat[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_COMBAT_CONFIRMATION", test_combat,
        sizeof(test_combat)) || strcmp(test_combat, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    int base_id = first_owned_base(faction_id);
    if (base_id < 0 || !human_turn_actionable(faction_id)) return;
    int attacker_id = -1;
    for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
        VEH& veh = Vehs[veh_id];
        if (veh.faction_id == faction_id && veh.unit_id == BSC_SCOUT_PATROL
        && veh.x == Bases[base_id].x && veh.y == Bases[base_id].y) {
            attacker_id = veh_id;
            break;
        }
    }
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate) && !is_human(candidate)) {
            other = candidate;
            break;
        }
    }
    int target_x = -1;
    int target_y = -1;
    for (int dir = 0; dir < 8; ++dir) {
        int x = wrap(Bases[base_id].x + BaseOffsetX[dir]);
        int y = Bases[base_id].y + BaseOffsetY[dir];
        MAP* sq = mapsq(x, y);
        if (sq && !is_ocean(sq) && base_at(x, y) < 0 && veh_at(x, y) < 0) {
            target_x = x;
            target_y = y;
            break;
        }
    }
    if (attacker_id < 0 || other < 0 || target_x < 0) return;
    const uint32_t clear_status = DIPLO_VENDETTA|DIPLO_TRUCE|DIPLO_PACT;
    Factions[faction_id].diplo_status[other] &= ~clear_status;
    Factions[other].diplo_status[faction_id] &= ~clear_status;
    Factions[faction_id].diplo_status[other] |= DIPLO_COMMLINK|DIPLO_TREATY;
    Factions[other].diplo_status[faction_id] |= DIPLO_COMMLINK|DIPLO_TREATY;
    int defender_id = -1;
    for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
        if (Vehs[veh_id].faction_id == other
        && Vehs[veh_id].unit_id == BSC_SCOUT_PATROL) {
            defender_id = veh_id;
            break;
        }
    }
    if (defender_id < 0) return;
    veh_put(defender_id, target_x, target_y);
    char test_hasty[8] = {};
    bool hasty = GetEnvironmentVariableA("SMACX_AGENT_TEST_HASTY", test_hasty,
        sizeof(test_hasty)) && !strcmp(test_hasty, "1");
    if (hasty) {
        Vehs[attacker_id].damage_taken = 0;
        Vehs[attacker_id].moves_spent = static_cast<uint8_t>(
            max(1, veh_speed(attacker_id, 0) - 1));
        Vehs[defender_id].damage_taken = static_cast<uint8_t>(
            max(0, Vehs[defender_id].max_hitpoints() - 1));
    } else {
        Vehs[attacker_id].damage_taken = static_cast<uint8_t>(
            max(0, Vehs[attacker_id].max_hitpoints() - 1));
    }
    Vehs[defender_id].visibility |= 1 << faction_id;
    test_combat_confirmation_fixture_initialized = true;
}

void ensure_test_nerve_gas_fixture() {
    if (test_nerve_gas_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_nerve[16] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_NERVE_GAS", test_nerve,
        sizeof(test_nerve))
    || (strcmp(test_nerve, "conventional") && strcmp(test_nerve, "commit"))) return;
    int faction_id = *CurrentPlayerFaction;
    int base_id = first_owned_base(faction_id);
    if (base_id < 0 || !human_turn_actionable(faction_id)) return;
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate) && !is_human(candidate)) {
            other = candidate;
            break;
        }
    }
    int target_x = -1;
    int target_y = -1;
    for (int dir = 0; dir < 8; ++dir) {
        int x = wrap(Bases[base_id].x + BaseOffsetX[dir]);
        int y = Bases[base_id].y + BaseOffsetY[dir];
        MAP* sq = mapsq(x, y);
        if (sq && !is_ocean(sq) && base_at(x, y) < 0 && veh_at(x, y) < 0) {
            target_x = x;
            target_y = y;
            break;
        }
    }
    if (other < 0 || target_x < 0) return;
    const uint32_t clear_status = DIPLO_TREATY | DIPLO_TRUCE | DIPLO_PACT;
    Factions[faction_id].diplo_status[other] &= ~clear_status;
    Factions[other].diplo_status[faction_id] &= ~clear_status;
    // Vendetta alone is sufficient for a legal hostile move.  Giving both sides
    // a commlink here lets the synthetic opponent open COMM while this read
    // request is still in flight, which makes fixture creation look like a
    // transport timeout instead of an explicit interaction phase transition.
    Factions[faction_id].diplo_status[other] &= ~DIPLO_COMMLINK;
    Factions[other].diplo_status[faction_id] &= ~DIPLO_COMMLINK;
    Factions[faction_id].diplo_status[other] |= DIPLO_VENDETTA;
    Factions[other].diplo_status[faction_id] |= DIPLO_VENDETTA;
    const int component_techs[] = {
        Weapon[WPN_PARTICLE_IMPACTOR].preq_tech,
        Armor[ARM_SYNTHMETAL_ARMOR].preq_tech,
        Ability[ABL_ID_NERVE_GAS].preq_tech,
    };
    for (int tech_id : component_techs) {
        if (tech_id >= 0 && tech_id < MaxTechnologyNum) {
            TechOwners[tech_id] |= 1 << faction_id;
        }
    }
    char design_name[] = "Harness Nerve Unit";
    int prototype_id = propose_proto(faction_id, CHS_INFANTRY,
        WPN_PARTICLE_IMPACTOR, ARM_SYNTHMETAL_ARMOR, ABL_NERVE_GAS,
        REC_FISSION, PLAN_AUTO_CALCULATE, design_name);
    if (prototype_id < faction_id * MaxProtoFactionNum
    || prototype_id >= min(MaxProtoNum, (faction_id + 1) * MaxProtoFactionNum)) return;
    Units[prototype_id].unit_flags |= UNIT_PROTOTYPED;
    int attacker_id = veh_init(prototype_id, faction_id,
        Bases[base_id].x, Bases[base_id].y);
    int defender_id = veh_init(BSC_SCOUT_PATROL, other, target_x, target_y);
    if (attacker_id < 0 || defender_id < 0) return;
    Vehs[defender_id].visibility |= 1 << faction_id;
    test_nerve_gas_attacker_id = attacker_id;
    test_nerve_gas_defender_faction_id = other;
    test_nerve_gas_atrocities_before = Factions[faction_id].atrocities;
    test_nerve_gas_fixture_initialized = true;
}

void ensure_test_self_destruct_fixture() {
    if (test_self_destruct_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_self_destruct[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_SELF_DESTRUCT", test_self_destruct,
        sizeof(test_self_destruct)) || strcmp(test_self_destruct, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    int base_id = first_owned_base(faction_id);
    if (base_id < 0 || !human_turn_actionable(faction_id)) return;
    int origin_x = -1;
    int origin_y = -1;
    int target_x = -1;
    int target_y = -1;
    for (int y = 0; y < *MapAreaY && origin_x < 0; ++y) {
        for (int x = y & 1; x < *MapAreaX && origin_x < 0; x += 2) {
            MAP* sq = mapsq(x, y);
            int distance = map_range(Bases[base_id].x, Bases[base_id].y, x, y);
            if (!sq || is_ocean(sq) || base_at(x, y) >= 0 || veh_at(x, y) >= 0
            || distance < 2 || distance > 8) continue;
            for (int dir = 0; dir < 8; ++dir) {
                int tx = wrap(x + BaseOffsetX[dir]);
                int ty = y + BaseOffsetY[dir];
                MAP* target = mapsq(tx, ty);
                if (!target || is_ocean(target) || base_at(tx, ty) >= 0
                || veh_at(tx, ty) >= 0) continue;
                origin_x = x;
                origin_y = y;
                target_x = tx;
                target_y = ty;
                break;
            }
        }
    }
    if (origin_x < 0 || target_x < 0) return;
    const int component_techs[] = {
        Weapon[WPN_SINGULARITY_LASER].preq_tech,
        Armor[ARM_NO_ARMOR].preq_tech,
        Reactor[REC_SINGULARITY - 1].preq_tech,
    };
    for (int tech_id : component_techs) {
        if (tech_id >= 0 && tech_id < MaxTechnologyNum) {
            TechOwners[tech_id] |= 1 << faction_id;
        }
    }
    char design_name[] = "Harness Overload Unit";
    int prototype_id = propose_proto(faction_id, CHS_INFANTRY,
        WPN_SINGULARITY_LASER, ARM_NO_ARMOR, 0, REC_SINGULARITY,
        PLAN_OFFENSE, design_name);
    if (prototype_id < faction_id * MaxProtoFactionNum
    || prototype_id >= min(MaxProtoNum, (faction_id + 1) * MaxProtoFactionNum)) return;
    Units[prototype_id].unit_flags |= UNIT_PROTOTYPED;
    int attacker_id = veh_init(prototype_id, faction_id, origin_x, origin_y);
    int target_id = veh_init(BSC_MIND_WORMS, 0, target_x, target_y);
    if (attacker_id < 0 || target_id < 0) return;
    Vehs[attacker_id].moves_spent = 0;
    Vehs[attacker_id].order = ORDER_NONE;
    Vehs[target_id].visibility |= 1 << faction_id;
    mapsq(origin_x, origin_y)->visibility |= 1 << faction_id;
    mapsq(target_x, target_y)->visibility |= 1 << faction_id;
    test_self_destruct_origin_x = origin_x;
    test_self_destruct_origin_y = origin_y;
    test_self_destruct_target_x = target_x;
    test_self_destruct_target_y = target_y;
    test_self_destruct_blast_damage = clamp(
        weap_val(prototype_id, faction_id), 1, 20) * REC_SINGULARITY / 2;
    test_self_destruct_fixture_initialized = true;
}

void ensure_test_base_status_fixture() {
    if (test_base_status_fixture_initialized || test_base_status_fixture_base_id >= 0
    || !game_active()) return;
    char test_mode[8] = {};
    char test_status[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_BASE_STATUS", test_status,
        sizeof(test_status)) || strcmp(test_status, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    int base_id = first_owned_base(faction_id);
    if (base_id < 0 || !human_turn_actionable(faction_id)) return;
    test_base_status_fixture_initialized = true;
    test_base_status_fixture_base_id = base_id;
    PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
}

void ensure_test_production_notice_fixture() {
    if (test_production_notice_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_notice[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_PRODUCTION_NOTICE", test_notice,
        sizeof(test_notice)) || strcmp(test_notice, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    int base_id = first_owned_base(faction_id);
    if (base_id < 0 || !human_turn_actionable(faction_id)) return;
    int facility_id = FAC_RECREATION_COMMONS;
    int prerequisite = Facility[facility_id].preq_tech;
    if (prerequisite >= 0 && prerequisite < MaxTechnologyNum) {
        TechOwners[prerequisite] |= 1 << faction_id;
    }
    BASE& base = Bases[base_id];
    base.queue_items[0] = -facility_id;
    base.queue_size = 0;
    base.governor_flags |= GOV_ACTIVE|GOV_MANAGE_PRODUCTION;
    base.state_flags |= BSTATE_UNK_80000000;
    base.nutrients_accumulated = max(0, base.nutrients_accumulated);
    base.minerals_accumulated = 10000;
    *GameWarnings |= WARN_STOP_NEW_FAC_BUILT;
    test_production_notice_fixture_initialized = true;
}

const char* missile_kind(VEH& veh) {
    if (!veh.is_missile()) return "none";
    if (veh.is_planet_buster()) return "planet_buster";
    if (veh.plan() == PLAN_TECTONIC_MISSILE) return "tectonic";
    if (veh.plan() == PLAN_FUNGAL_MISSILE) return "fungal";
    return "conventional";
}

int missile_launch_range(int veh_id) {
    if (veh_id < 0 || veh_id >= *VehCount || !Vehs[veh_id].is_missile()) return 0;
    return max(0, (veh_speed(veh_id, 0) - Vehs[veh_id].moves_spent)
        / max(1, Rules->move_rate_roads));
}

bool visible_hostile_at(int faction_id, int x, int y, int* target_faction_id = NULL) {
    for (int target_id = veh_at(x, y); target_id >= 0;
    target_id = Vehs[target_id].next_veh_id_stack) {
        VEH& target = Vehs[target_id];
        if (target.faction_id != faction_id && !has_pact(faction_id, target.faction_id)
        && (target.visibility & (1 << faction_id))) {
            if (target_faction_id) *target_faction_id = target.faction_id;
            return true;
        }
    }
    return false;
}

bool missile_target_legal(int faction_id, int veh_id, int x, int y,
std::string* reason = NULL) {
    auto reject = [&](const char* value) {
        if (reason) *reason = value;
        return false;
    };
    if (veh_id < 0 || veh_id >= *VehCount || Vehs[veh_id].faction_id != faction_id
    || !Vehs[veh_id].is_missile() || !veh_unmoved(veh_id)) {
        return reject("missile_not_ready");
    }
    VEH& veh = Vehs[veh_id];
    MAP* target = mapsq(x, y);
    if (!target || map_range(veh.x, veh.y, x, y) > missile_launch_range(veh_id)) {
        return reject("target_not_visible_or_in_range");
    }
    const char* kind = missile_kind(veh);
    if (!strcmp(kind, "conventional")) {
        int target_faction_id = -1;
        if (!visible_hostile_at(faction_id, x, y, &target_faction_id)
        || !at_war(faction_id, target_faction_id)) {
            return reject("conventional_target_must_be_visible_hostile_at_war");
        }
        return true;
    }
    if (!strcmp(kind, "tectonic") || !strcmp(kind, "fungal")) {
        if (!target->is_visible(faction_id)) {
            return reject("target_not_visible_or_in_range");
        }
        // The native target cursor forbids delivery payloads on a base or unit.
        // Checking the actual stack here can reject a seemingly empty fog-edge
        // tile, but never discloses an occupant or any hidden identity.
        if (base_at(x, y) >= 0 || veh_at(x, y) >= 0) {
            return reject("delivery_payload_requires_empty_tile");
        }
        int owner = target->owner;
        if (owner > 0 && owner != faction_id && !at_war(faction_id, owner)) {
            return reject("resolve_diplomacy_before_targeting_foreign_territory");
        }
        return true;
    }
    if (!strcmp(kind, "planet_buster")) {
        int blast_radius = veh.reactor_type();
        bool hostile_found = false;
        for (auto& point : iterate_tiles(x, y, 0, TableRange[blast_radius])) {
            int bx = point.x;
            int by = point.y;
            MAP* blast = mapsq(bx, by);
            if (!blast || (!blast->is_visible(faction_id)
            && !visible_hostile_at(faction_id, bx, by))) continue;
            int base_id = base_at(bx, by);
            if (base_id >= 0) {
                int owner = Bases[base_id].faction_id;
                if (owner == faction_id || has_pact(faction_id, owner)) {
                    return reject("visible_owned_or_pact_asset_in_blast_radius");
                }
                hostile_found |= at_war(faction_id, owner);
            }
            for (int target_id = veh_at(bx, by); target_id >= 0;
            target_id = Vehs[target_id].next_veh_id_stack) {
                VEH& other = Vehs[target_id];
                if (other.faction_id == faction_id) {
                    return reject("visible_owned_or_pact_asset_in_blast_radius");
                }
                if (!(other.visibility & (1 << faction_id))) continue;
                if (has_pact(faction_id, other.faction_id)) {
                    return reject("visible_owned_or_pact_asset_in_blast_radius");
                }
                hostile_found |= at_war(faction_id, other.faction_id);
            }
        }
        return hostile_found || reject("no_visible_hostile_asset_in_blast_radius");
    }
    return reject("unsupported_missile_payload");
}

bool facility_recyclable_at_base(int faction_id, int base_id, int facility_id) {
    if (faction_id < 1 || base_id < 0 || base_id >= *BaseCount
    || Bases[base_id].faction_id != faction_id
    || facility_id < Fac_ID_First || facility_id > Fac_ID_Last
    || !has_fac_built(static_cast<FacilityId>(facility_id), base_id)
    || Bases[base_id].state_flags & BSTATE_FACILITY_SCRAPPED
    || facility_id == FAC_HEADQUARTERS
    || (facility_id == FAC_PRESSURE_DOME && is_ocean(&Bases[base_id]))
    || has_free_facility(static_cast<FacilityId>(facility_id), faction_id)) {
        return false;
    }
    return true;
}

int facility_recycle_refund(const BASE& base, int facility_id) {
    if (facility_id == FAC_NETWORK_NODE
    && base.state_flags & BSTATE_ARTIFACT_LINKED) return 0;
    return 5 * Facility[facility_id].cost;
}

void ensure_test_transport_fixture() {
    if (test_transport_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_transport[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_TRANSPORT", test_transport,
        sizeof(test_transport)) || strcmp(test_transport, "1")) return;
    test_transport_fixture_initialized = true;
    int faction_id = *CurrentPlayerFaction;
    int ocean_x = -1;
    int ocean_y = -1;
    for (int y = 0; y < *MapAreaY && ocean_x < 0; ++y) {
        for (int x = y & 1; x < *MapAreaX && ocean_x < 0; x += 2) {
            MAP* sq = mapsq(x, y);
            if (!sq || !is_ocean(sq) || sq->base_who() >= 0) continue;
            for (int dir = 0; dir < 8; ++dir) {
                int tx = wrap(x + BaseOffsetX[dir]);
                int ty = y + BaseOffsetY[dir];
                MAP* target = mapsq(tx, ty);
                if (target && !is_ocean(target) && target->base_who() < 0) {
                    ocean_x = x;
                    ocean_y = y;
                    break;
                }
            }
        }
    }
    if (ocean_x < 0) return;
    int transport_id = veh_init(BSC_TRANSPORT_FOIL, faction_id, ocean_x, ocean_y);
    int boarded_id = veh_init(BSC_SCOUT_PATROL, faction_id, ocean_x, ocean_y);
    int waiting_id = veh_init(BSC_SCOUT_PATROL, faction_id, ocean_x, ocean_y);
    if (transport_id < 0 || boarded_id < 0 || waiting_id < 0) return;
    set_board_to(boarded_id, transport_id);
    spot_all(transport_id, 1);
    spot_all(boarded_id, 1);
    spot_all(waiting_id, 1);
}

void ensure_test_artifact_fixture() {
    if (test_artifact_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_artifact[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_ARTIFACT", test_artifact,
        sizeof(test_artifact)) || strcmp(test_artifact, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    int base_id = first_owned_base(faction_id);
    if (base_id < 0 || !human_turn_actionable(faction_id)) return;
    int spawn_x = -1;
    int spawn_y = -1;
    for (int direction = 0; direction < 8; ++direction) {
        int x = wrap(Bases[base_id].x + BaseOffsetX[direction]);
        int y = Bases[base_id].y + BaseOffsetY[direction];
        MAP* sq = mapsq(x, y);
        if (sq && !is_ocean(sq) && sq->base_who() < 0) {
            spawn_x = x;
            spawn_y = y;
            break;
        }
    }
    if (spawn_x < 0) return;
    set_fac(FAC_NETWORK_NODE, base_id, true);
    Bases[base_id].state_flags &=
        ~(BSTATE_ARTIFACT_LINKED | BSTATE_ARTIFACT_ALREADY_LINKED);
    int artifact_id = veh_init(BSC_ALIEN_ARTIFACT, faction_id, spawn_x, spawn_y);
    if (artifact_id < 0) return;
    spot_all(artifact_id, 1);
    draw_tile(spawn_x, spawn_y, 2);
    test_artifact_fixture_initialized = true;
}

void ensure_test_artillery_fixture() {
    if (test_artillery_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_artillery[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_ARTILLERY", test_artillery,
        sizeof(test_artillery)) || strcmp(test_artillery, "1")) return;
    test_artillery_fixture_initialized = true;
    int faction_id = *CurrentPlayerFaction;
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate)) {
            other = candidate;
            break;
        }
    }
    if (other < 0) return;
    const int component_techs[] = {
        Weapon[WPN_PARTICLE_IMPACTOR].preq_tech,
        Armor[ARM_SYNTHMETAL_ARMOR].preq_tech,
        Ability[ABL_ID_ARTILLERY].preq_tech,
    };
    for (int tech_id : component_techs) {
        if (tech_id >= 0 && tech_id < MaxTechnologyNum) {
            TechOwners[tech_id] |= 1 << faction_id;
        }
    }
    char design_name[] = "Harness Artillery";
    int prototype_id = propose_proto(faction_id, CHS_INFANTRY,
        WPN_PARTICLE_IMPACTOR, ARM_SYNTHMETAL_ARMOR, ABL_ARTILLERY,
        REC_FISSION, PLAN_AUTO_CALCULATE, design_name);
    if (prototype_id < faction_id * MaxProtoFactionNum
    || prototype_id >= (faction_id + 1) * MaxProtoFactionNum) return;
    int attacker_x = -1;
    int attacker_y = -1;
    int target_x = -1;
    int target_y = -1;
    for (int ay = 0; ay < *MapAreaY && attacker_x < 0; ++ay) {
        for (int ax = ay & 1; ax < *MapAreaX && attacker_x < 0; ax += 2) {
            MAP* attacker_sq = mapsq(ax, ay);
            if (!attacker_sq || is_ocean(attacker_sq) || attacker_sq->base_who() >= 0
            || veh_at(ax, ay) >= 0) continue;
            for (int ty = 0; ty < *MapAreaY && attacker_x < 0; ++ty) {
                for (int tx = ty & 1; tx < *MapAreaX; tx += 2) {
                    MAP* target_sq = mapsq(tx, ty);
                    if (!target_sq || is_ocean(target_sq) || target_sq->base_who() >= 0
                    || veh_at(tx, ty) >= 0 || map_range(ax, ay, tx, ty) != 2) continue;
                    attacker_x = ax;
                    attacker_y = ay;
                    target_x = tx;
                    target_y = ty;
                    break;
                }
            }
        }
    }
    if (attacker_x < 0) return;
    Units[prototype_id].unit_flags |= UNIT_PROTOTYPED;
    int attacker_id = veh_init(prototype_id, faction_id, attacker_x, attacker_y);
    int target_id = veh_init(BSC_SCOUT_PATROL, other, target_x, target_y);
    if (attacker_id < 0 || target_id < 0) return;
    treaty_on(faction_id, other, DIPLO_COMMLINK);
    treaty_on(faction_id, other, DIPLO_VENDETTA);
    Vehs[target_id].visibility |= 1 << faction_id;
    spot_all(attacker_id, 1);
}

void ensure_test_probe_fixture() {
    if (test_probe_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_probe[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_PROBE", test_probe,
        sizeof(test_probe)) || strcmp(test_probe, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    int target_base_id = -1;
    int probe_x = -1;
    int probe_y = -1;
    for (int base_id = 0; base_id < *BaseCount; ++base_id) {
        if (Bases[base_id].faction_id != faction_id
        && Bases[base_id].faction_id > 0 && is_alive(Bases[base_id].faction_id)
        && !is_ocean(&Bases[base_id])) {
            for (int dir = 0; dir < 8; ++dir) {
                int x = wrap(Bases[base_id].x + BaseOffsetX[dir]);
                int y = Bases[base_id].y + BaseOffsetY[dir];
                MAP* sq = mapsq(x, y);
                if (sq && !is_ocean(sq) && sq->base_who() < 0 && veh_at(x, y) < 0) {
                    target_base_id = base_id;
                    probe_x = x;
                    probe_y = y;
                    break;
                }
            }
            if (target_base_id >= 0) break;
        }
    }
    if (target_base_id < 0) {
        int other = -1;
        for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
            if (candidate != faction_id && is_alive(candidate)) {
                other = candidate;
                break;
            }
        }
        int base_x = -1;
        int base_y = -1;
        for (int y = 0; y < *MapAreaY && base_x < 0; ++y) {
            for (int x = y & 1; x < *MapAreaX && base_x < 0; x += 2) {
                MAP* sq = mapsq(x, y);
                if (!sq || is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0) continue;
                for (int dir = 0; dir < 8; ++dir) {
                    int px = wrap(x + BaseOffsetX[dir]);
                    int py = y + BaseOffsetY[dir];
                    MAP* probe_sq = mapsq(px, py);
                    if (probe_sq && !is_ocean(probe_sq)
                    && probe_sq->base_who() < 0 && veh_at(px, py) < 0) {
                        base_x = x;
                        base_y = y;
                        break;
                    }
                }
            }
        }
        if (other < 0 || base_x < 0) return;
        target_base_id = mod_base_init(other, base_x, base_y);
        if (target_base_id >= 0) {
            for (int dir = 0; dir < 8; ++dir) {
                int x = wrap(Bases[target_base_id].x + BaseOffsetX[dir]);
                int y = Bases[target_base_id].y + BaseOffsetY[dir];
                MAP* sq = mapsq(x, y);
                if (sq && !is_ocean(sq) && sq->base_who() < 0 && veh_at(x, y) < 0) {
                    probe_x = x;
                    probe_y = y;
                    break;
                }
            }
        }
    }
    if (target_base_id < 0 || probe_x < 0) return;
    BASE& target = Bases[target_base_id];
    int probe_id = veh_init(BSC_PROBE_TEAM, faction_id, probe_x, probe_y);
    if (probe_id < 0) return;
    Vehs[probe_id].morale = MORALE_ELITE;
    set_fac(FAC_PERIMETER_DEFENSE, target_base_id, true);
    char test_plague[8] = {};
    if (GetEnvironmentVariableA("SMACX_AGENT_TEST_PROBE_PLAGUE", test_plague,
        sizeof(test_plague)) && !strcmp(test_plague, "1")) {
        for (int tech_id = 0; tech_id < MaxTechnologyNum; ++tech_id) {
            if (Tech[tech_id].flags & TFLAG_ALLOW_GENE_WARFARE) {
                TechOwners[tech_id] |= 1 << faction_id;
                break;
            }
        }
        target.pop_size = max(6, static_cast<int>(target.pop_size));
    }
    char test_leader[8] = {};
    if (GetEnvironmentVariableA("SMACX_AGENT_TEST_PROBE_LEADER", test_leader,
        sizeof(test_leader)) && !strcmp(test_leader, "1")) {
        set_fac(FAC_HEADQUARTERS, target_base_id, true);
        int captured_faction_id = -1;
        for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
            if (candidate != faction_id && candidate != target.faction_id && is_alive(candidate)) {
                captured_faction_id = candidate;
                break;
            }
        }
        if (captured_faction_id > 0) {
            for (int slot = 0; slot < MaxPlayerNum - 1; ++slot) {
                if (!Monuments[target.faction_id].data2[slot][9]) {
                    Monuments[target.faction_id].data2[slot][7] = captured_faction_id;
                    Monuments[target.faction_id].data2[slot][9] = 1;
                    set_alive(captured_faction_id, false);
                    Factions[target.faction_id].eliminated_count++;
                    break;
                }
            }
        }
    }
    int target_faction_id = target.faction_id;
    for (int dir = 0; dir < 8; ++dir) {
        int x = wrap(probe_x + BaseOffsetX[dir]);
        int y = probe_y + BaseOffsetY[dir];
        MAP* sq = mapsq(x, y);
        if (!sq || is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0) continue;
        int target_unit_id = veh_init(BSC_SCOUT_PATROL, target_faction_id, x, y);
        if (target_unit_id >= 0) Vehs[target_unit_id].visibility |= 1 << faction_id;
        break;
    }
    Factions[faction_id].energy_credits = 1000;
    treaty_on(faction_id, target_faction_id, DIPLO_COMMLINK);
    treaty_on(faction_id, target_faction_id, DIPLO_VENDETTA);
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && candidate != target_faction_id && is_alive(candidate)) {
            treaty_on(faction_id, candidate, DIPLO_COMMLINK);
            break;
        }
    }
    target.visibility |= 1 << faction_id;
    spot_all(probe_id, 1);
    test_probe_fixture_initialized = true;
}

void ensure_test_psi_gate_fixture() {
    if (test_psi_gate_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_gate[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_PSI_GATE", test_gate,
        sizeof(test_gate)) || strcmp(test_gate, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    int source_base_id = first_owned_base(faction_id);
    if (source_base_id < 0 || is_ocean(&Bases[source_base_id])) return;
    int target_base_id = -1;
    for (int base_id = 0; base_id < *BaseCount; ++base_id) {
        if (base_id != source_base_id && Bases[base_id].faction_id == faction_id
        && !is_ocean(&Bases[base_id])) {
            target_base_id = base_id;
            break;
        }
    }
    if (target_base_id < 0) {
        for (int y = 0; y < *MapAreaY && target_base_id < 0; ++y) {
            for (int x = y & 1; x < *MapAreaX && target_base_id < 0; x += 2) {
                MAP* sq = mapsq(x, y);
                if (!sq || is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0
                || map_range(Bases[source_base_id].x, Bases[source_base_id].y, x, y) < 6) continue;
                target_base_id = mod_base_init(faction_id, x, y);
            }
        }
    }
    if (target_base_id < 0) return;
    set_fac(FAC_PSI_GATE, source_base_id, 1);
    set_fac(FAC_PSI_GATE, target_base_id, 1);
    Bases[source_base_id].state_flags &= ~BSTATE_PSI_GATE_USED;
    Bases[target_base_id].state_flags &= ~BSTATE_PSI_GATE_USED;
    int veh_id = veh_init(BSC_SCOUT_PATROL, faction_id,
        Bases[source_base_id].x, Bases[source_base_id].y);
    if (veh_id < 0) return;
    spot_all(veh_id, 1);
    test_psi_gate_fixture_initialized = true;
}

bool create_test_order_fixture() {
    if (test_order_fixture_initialized || !game_active()) return false;
    int faction_id = *CurrentPlayerFaction;
    int base_id = -1;
    for (int candidate = 0; candidate < *BaseCount; ++candidate) {
        if (Bases[candidate].faction_id == faction_id && !is_ocean(&Bases[candidate])) {
            base_id = candidate;
            break;
        }
    }
    if (base_id < 0) return false;
    int former_id = veh_init(BSC_FORMERS, faction_id, Bases[base_id].x, Bases[base_id].y);
    int patrol_id = veh_init(BSC_SCOUT_PATROL, faction_id, Bases[base_id].x, Bases[base_id].y);
    if (former_id < 0 || patrol_id < 0) return false;
    spot_all(former_id, 1);
    spot_all(patrol_id, 1);
    test_order_fixture_initialized = true;
    return true;
}

void ensure_test_order_fixture() {
    char test_mode[8] = {};
    char test_orders[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_ORDERS", test_orders,
        sizeof(test_orders)) || strcmp(test_orders, "1")) return;
    create_test_order_fixture();
}

std::string test_identity_compaction_fixture_response() {
    char enabled[8] = {};
    if (!GetEnvironmentVariableA("SMACX_ACCEPTANCE_OWN_UNIT_COMPACTION", enabled,
        sizeof(enabled)) || strcmp(enabled, "1")) {
        return error_response("acceptance_fixture_disabled",
            "The private native identity-compaction fixture is disabled.");
    }
    if (!create_test_order_fixture()) {
        return error_response("acceptance_fixture_unavailable",
            "The private native identity-compaction fixture could not be created.");
    }
    return "{\"ok\":true,\"fixture\":\"own_unit_compaction\"}";
}

void ensure_test_return_home_fixture() {
    if (test_return_home_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_return[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_RETURN_HOME", test_return,
        sizeof(test_return)) || strcmp(test_return, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int base_id = -1;
    for (int candidate = 0; candidate < *BaseCount; ++candidate) {
        if (Bases[candidate].faction_id == faction_id && !is_ocean(&Bases[candidate])) {
            base_id = candidate;
            break;
        }
    }
    if (base_id < 0) return;
    int target_x = -1;
    int target_y = -1;
    int base_region = region_at(Bases[base_id].x, Bases[base_id].y);
    for (int y = 0; y < *MapAreaY && target_x < 0; ++y) {
        for (int x = y & 1; x < *MapAreaX; x += 2) {
            MAP* sq = mapsq(x, y);
            int distance = map_range(Bases[base_id].x, Bases[base_id].y, x, y);
            if (!sq || is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0
            || distance < 2 || distance > 4 || region_at(x, y) != base_region) continue;
            target_x = x;
            target_y = y;
            break;
        }
    }
    if (target_x < 0) return;
    int veh_id = veh_init(BSC_SCOUT_PATROL, faction_id, target_x, target_y);
    if (veh_id < 0) return;
    Vehs[veh_id].moves_spent = 0;
    Vehs[veh_id].home_base_id = base_id;
    spot_all(veh_id, 1);
    test_return_home_fixture_initialized = true;
}

void ensure_test_terrain_destruction_fixture() {
    if (test_terrain_destruction_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_destroy[16] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_TERRAIN_DESTRUCTION",
        test_destroy, sizeof(test_destroy))
    || (strcmp(test_destroy, "1") && strcmp(test_destroy, "foreign")
        && strcmp(test_destroy, "pact"))) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int owned_base_id = first_owned_base(faction_id);
    if (owned_base_id < 0) return;
    int territory_base_id = owned_base_id;
    int territory_owner = faction_id;
    bool foreign = strcmp(test_destroy, "1") != 0;
    if (foreign) {
        for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
            if (candidate != faction_id && is_alive(candidate)) {
                territory_owner = candidate;
                break;
            }
        }
        if (territory_owner == faction_id) return;
        territory_base_id = -1;
        for (int y = 0; y < *MapAreaY && territory_base_id < 0; ++y) {
            for (int x = y & 1; x < *MapAreaX; x += 2) {
                MAP* sq = mapsq(x, y);
                int range = map_range(Bases[owned_base_id].x, Bases[owned_base_id].y, x, y);
                if (!sq || is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0
                || range < 6 || range > 10) continue;
                territory_base_id = mod_base_init(territory_owner, x, y);
                if (territory_base_id >= 0) {
                    strcpy_n(Bases[territory_base_id].name, sizeof(BASE::name),
                        "Harness Demolition Border");
                }
                break;
            }
        }
        if (territory_base_id < 0) return;
        const uint32_t clear = DIPLO_VENDETTA | DIPLO_TRUCE
            | DIPLO_TREATY | DIPLO_PACT;
        Factions[faction_id].diplo_status[territory_owner] &= ~clear;
        Factions[territory_owner].diplo_status[faction_id] &= ~clear;
        treaty_on(faction_id, territory_owner,
            DIPLO_COMMLINK | DIPLO_TREATY | DIPLO_UNK_8000000);
        if (!strcmp(test_destroy, "pact")) {
            treaty_on(faction_id, territory_owner, DIPLO_PACT);
        }
    }
    int target_x = -1;
    int target_y = -1;
    for (int dir = 0; dir < 8; ++dir) {
        int x = wrap(Bases[territory_base_id].x + BaseOffsetX[dir]);
        int y = Bases[territory_base_id].y + BaseOffsetY[dir];
        MAP* sq = mapsq(x, y);
        if (!sq || is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0) continue;
        if (foreign) sq->owner = territory_owner;
        int owner = whose_territory(faction_id, x, y, 0, 0);
        if (owner != territory_owner) continue;
        target_x = x;
        target_y = y;
        break;
    }
    if (target_x < 0) return;
    bit_set(target_x, target_y,
        BIT_FARM | BIT_SOIL_ENRICHER | BIT_ROAD | BIT_MAGTUBE | BIT_SENSOR, true);
    int veh_id = veh_init(BSC_SCOUT_PATROL, faction_id, target_x, target_y);
    if (veh_id < 0) return;
    Vehs[veh_id].moves_spent = 0;
    Vehs[veh_id].order = ORDER_NONE;
    spot_all(veh_id, 1);
    test_terrain_destruction_fixture_initialized = true;
}

void ensure_test_base_action_fixture() {
    if (test_base_action_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_actions[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_BASE_ACTIONS", test_actions,
        sizeof(test_actions)) || strcmp(test_actions, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    int base_id = first_owned_base(faction_id);
    if (base_id < 0) return;
    Bases[base_id].nerve_staple_turns_left = 0;
    Bases[base_id].nerve_staple_count = 0;
    set_fac(FAC_RECREATION_COMMONS, base_id, true);
    Bases[base_id].state_flags &= ~BSTATE_FACILITY_SCRAPPED;
    set_base(base_id);
    base_compute(1);
    test_base_action_fixture_initialized = true;
}

void ensure_test_missile_fixture() {
    if (test_missile_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_missiles[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_MISSILES", test_missiles,
        sizeof(test_missiles)) || strcmp(test_missiles, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    int base_id = first_owned_base(faction_id);
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate)) {
            other = candidate;
            break;
        }
    }
    if (base_id < 0 || other < 0) return;
    int hostile_x = -1;
    int hostile_y = -1;
    int hostile_x_2 = -1;
    int hostile_y_2 = -1;
    for (int y = 0; y < *MapAreaY && hostile_x < 0; ++y) {
        for (int x = y & 1; x < *MapAreaX && hostile_x < 0; x += 2) {
            MAP* sq = mapsq(x, y);
            int distance = map_range(Bases[base_id].x, Bases[base_id].y, x, y);
            if (!sq || is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0
            || distance < 4 || distance > 10) continue;
            hostile_x = x;
            hostile_y = y;
        }
    }
    if (hostile_x < 0) return;
    for (int y = 0; y < *MapAreaY && hostile_x_2 < 0; ++y) {
        for (int x = y & 1; x < *MapAreaX && hostile_x_2 < 0; x += 2) {
            MAP* sq = mapsq(x, y);
            int distance = map_range(Bases[base_id].x, Bases[base_id].y, x, y);
            if (!sq || is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0
            || distance < 4 || distance > 10
            || map_range(hostile_x, hostile_y, x, y) < 4) continue;
            hostile_x_2 = x;
            hostile_y_2 = y;
        }
    }
    if (hostile_x_2 < 0) return;
    const struct MissileFixtureDesign {
        int weapon_id;
        const char* name;
    } designs[] = {
        {WPN_MISSILE_LAUNCHER, "Harness Conventional Missile"},
        {WPN_TECTONIC_PAYLOAD, "Harness Tectonic Missile"},
        {WPN_FUNGAL_PAYLOAD, "Harness Fungal Missile"},
        {WPN_PLANET_BUSTER, "Harness Planet Buster"},
    };
    for (const MissileFixtureDesign& design : designs) {
        int prototype_id = propose_proto(faction_id, CHS_MISSILE, design.weapon_id,
            ARM_NO_ARMOR, 0, REC_FISSION, PLAN_AUTO_CALCULATE,
            const_cast<char*>(design.name));
        if (prototype_id < 0) return;
        Units[prototype_id].unit_flags |= UNIT_PROTOTYPED;
        int missile_id = veh_init(prototype_id, faction_id,
            Bases[base_id].x, Bases[base_id].y);
        if (missile_id < 0) return;
        spot_all(missile_id, 1);
    }
    int hostile_id = veh_init(BSC_SCOUT_PATROL, other, hostile_x, hostile_y);
    int hostile_id_2 = veh_init(BSC_SCOUT_PATROL, other, hostile_x_2, hostile_y_2);
    if (hostile_id < 0 || hostile_id_2 < 0) return;
    Vehs[hostile_id].visibility |= 1 << faction_id;
    Vehs[hostile_id_2].visibility |= 1 << faction_id;
    treaty_on(faction_id, other, DIPLO_COMMLINK);
    treaty_on(faction_id, other, DIPLO_VENDETTA);
    spot_all(hostile_id, 1);
    spot_all(hostile_id_2, 1);
    test_missile_fixture_initialized = true;
}

void ensure_test_air_automation_fixture() {
    if (test_air_automation_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_air[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_AIR_AUTOMATION", test_air,
        sizeof(test_air)) || strcmp(test_air, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int source_base_id = first_owned_base(faction_id);
    if (source_base_id < 0) return;
    int target_base_id = -1;
    for (int base_id = 0; base_id < *BaseCount; ++base_id) {
        if (base_id == source_base_id || Bases[base_id].faction_id != faction_id) continue;
        int distance = map_range(Bases[source_base_id].x, Bases[source_base_id].y,
            Bases[base_id].x, Bases[base_id].y);
        if (distance >= 2 && distance <= 4) {
            target_base_id = base_id;
            break;
        }
    }
    if (target_base_id < 0) {
        for (int y = 0; y < *MapAreaY && target_base_id < 0; ++y) {
            for (int x = y & 1; x < *MapAreaX && target_base_id < 0; x += 2) {
                MAP* sq = mapsq(x, y);
                int distance = map_range(Bases[source_base_id].x,
                    Bases[source_base_id].y, x, y);
                if (!sq || is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0
                || distance < 2 || distance > 4) continue;
                target_base_id = mod_base_init(faction_id, x, y);
            }
        }
    }
    if (target_base_id < 0) return;
    strcpy_n(Bases[target_base_id].name, sizeof(BASE::name), "Harness Air Recovery");
    mapsq(Bases[target_base_id].x, Bases[target_base_id].y)->visibility |= 1 << faction_id;
    spot_base(target_base_id, faction_id);
    MAP* recovery_airbase = NULL;
    for (int y = 0; y < *MapAreaY && !recovery_airbase; ++y) {
        for (int x = y & 1; x < *MapAreaX && !recovery_airbase; x += 2) {
            MAP* sq = mapsq(x, y);
            int distance = map_range(Bases[source_base_id].x,
                Bases[source_base_id].y, x, y);
            if (!sq || is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0
            || distance < 2 || distance > 4) continue;
            recovery_airbase = sq;
        }
    }
    if (!recovery_airbase) return;
    recovery_airbase->items |= BIT_AIRBASE;
    recovery_airbase->owner = faction_id;
    recovery_airbase->visibility |= 1 << faction_id;
    int unsafe_base_id = -1;
    int unsafe_x = -1;
    int unsafe_y = -1;
    int unsafe_distance = -1;
    for (int y = 0; y < *MapAreaY; ++y) {
        for (int x = y & 1; x < *MapAreaX; x += 2) {
            MAP* sq = mapsq(x, y);
            int distance = map_range(Bases[source_base_id].x,
                Bases[source_base_id].y, x, y);
            if (!sq || sq->base_who() >= 0 || veh_at(x, y) >= 0
            || distance <= unsafe_distance) continue;
            unsafe_x = x;
            unsafe_y = y;
            unsafe_distance = distance;
        }
    }
    if (unsafe_x >= 0) unsafe_base_id = mod_base_init(faction_id, unsafe_x, unsafe_y);
    if (unsafe_base_id < 0) return;
    strcpy_n(Bases[unsafe_base_id].name, sizeof(BASE::name), "Harness Unsafe Air Base");
    mapsq(Bases[unsafe_base_id].x, Bases[unsafe_base_id].y)->visibility |= 1 << faction_id;
    spot_base(unsafe_base_id, faction_id);
    const int component_techs[] = {
        Chassis[CHS_NEEDLEJET].preq_tech,
        Chassis[CHS_FOIL].preq_tech,
        Weapon[WPN_LASER].preq_tech,
        Armor[ARM_NO_ARMOR].preq_tech,
        Ability[ABL_ID_AIR_SUPERIORITY].preq_tech,
        Ability[ABL_ID_CLEAN_REACTOR].preq_tech,
        Ability[ABL_ID_CARRIER].preq_tech,
        Reactor[REC_FISSION - 1].preq_tech,
    };
    for (int tech_id : component_techs) {
        if (tech_id >= 0 && tech_id < MaxTechnologyNum) {
            TechOwners[tech_id] |= 1 << faction_id;
        }
    }
    char design_name[] = "Harness Interceptor";
    int prototype_id = propose_proto(faction_id, CHS_NEEDLEJET, WPN_LASER,
        ARM_NO_ARMOR,
        static_cast<VehAblFlag>(ABL_AIR_SUPERIORITY | ABL_CLEAN_REACTOR),
        REC_FISSION, PLAN_AIR_SUPERIORITY, design_name);
    if (prototype_id < faction_id * MaxProtoFactionNum
    || prototype_id >= min(MaxProtoNum, (faction_id + 1) * MaxProtoFactionNum)) return;
    Units[prototype_id].unit_flags |= UNIT_PROTOTYPED;
    int veh_id = veh_init(prototype_id, faction_id,
        Bases[source_base_id].x, Bases[source_base_id].y);
    if (veh_id < 0) return;
    Vehs[veh_id].moves_spent = 0;
    Vehs[veh_id].movement_turns = 0;
    Vehs[veh_id].order = ORDER_NONE;
    Vehs[veh_id].morale = MORALE_ELITE;
    Vehs[veh_id].home_base_id = source_base_id;
    spot_all(veh_id, 1);

    int carrier_x = -1;
    int carrier_y = -1;
    int staging_x = -1;
    int staging_y = -1;
    for (int y = 0; y < *MapAreaY && carrier_x < 0; ++y) {
        for (int x = y & 1; x < *MapAreaX && carrier_x < 0; x += 2) {
            MAP* sq = mapsq(x, y);
            if (!sq || !is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0) continue;
            bool ocean_exit = false;
            int candidate_staging_x = -1;
            int candidate_staging_y = -1;
            for (int dir = 0; dir < 8; ++dir) {
                int nx = wrap(x + BaseOffsetX[dir]);
                int ny = y + BaseOffsetY[dir];
                MAP* neighbor = mapsq(nx, ny);
                if (!neighbor || neighbor->base_who() >= 0 || veh_at(nx, ny) >= 0) continue;
                if (is_ocean(neighbor)) ocean_exit = true;
                else if (candidate_staging_x < 0) {
                    candidate_staging_x = nx;
                    candidate_staging_y = ny;
                }
            }
            if (ocean_exit && candidate_staging_x >= 0) {
                carrier_x = x;
                carrier_y = y;
                staging_x = candidate_staging_x;
                staging_y = candidate_staging_y;
            }
        }
    }
    if (carrier_x >= 0) {
        MAP* carrier_sq = mapsq(carrier_x, carrier_y);
        MAP* staging_sq = mapsq(staging_x, staging_y);
        carrier_sq->owner = faction_id;
        carrier_sq->visibility |= 1 << faction_id;
        staging_sq->items |= BIT_AIRBASE;
        staging_sq->owner = faction_id;
        staging_sq->visibility |= 1 << faction_id;
        char carrier_name[] = "Harness Carrier";
        int carrier_prototype_id = propose_proto(faction_id, CHS_FOIL, WPN_LASER,
            ARM_NO_ARMOR, static_cast<VehAblFlag>(ABL_CARRIER), REC_FISSION,
            PLAN_NAVAL_TRANSPORT, carrier_name);
        if (carrier_prototype_id >= faction_id * MaxProtoFactionNum
        && carrier_prototype_id < min(MaxProtoNum,
            (faction_id + 1) * MaxProtoFactionNum)) {
            Units[carrier_prototype_id].carry_capacity = 2;
            Units[carrier_prototype_id].unit_flags |= UNIT_PROTOTYPED;
            int carrier_id = veh_init(carrier_prototype_id, faction_id,
                carrier_x, carrier_y);
            if (carrier_id >= 0) {
                Vehs[carrier_id].moves_spent = 0;
                Vehs[carrier_id].order = ORDER_NONE;
                spot_all(carrier_id, 1);
                for (int index = 0; index < 2; ++index) {
                    int remote_id = veh_init(prototype_id, faction_id,
                        staging_x, staging_y);
                    if (remote_id >= 0) {
                        Vehs[remote_id].moves_spent = 0;
                        Vehs[remote_id].movement_turns = 0;
                        Vehs[remote_id].order = ORDER_NONE;
                        Vehs[remote_id].morale = MORALE_ELITE;
                        Vehs[remote_id].home_base_id = source_base_id;
                        spot_all(remote_id, 1);
                    }
                }
                int deck_id = veh_init(prototype_id, faction_id,
                    carrier_x, carrier_y);
                if (deck_id >= 0) {
                    Vehs[deck_id].movement_turns = 0;
                    Vehs[deck_id].order = ORDER_NONE;
                    Vehs[deck_id].morale = MORALE_ELITE;
                    // Exercise the semantic rule that boarding itself needs no
                    // movement even after an aircraft has spent its full turn.
                    Vehs[deck_id].moves_spent = veh_speed(deck_id, 0);
                    Vehs[deck_id].home_base_id = source_base_id;
                    spot_all(deck_id, 1);
                }
            }
        }
    }
    test_air_automation_fixture_initialized = true;
}

void ensure_test_bombing_run_fixture() {
    if (test_bombing_run_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_bombing[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_BOMBING_RUN", test_bombing,
        sizeof(test_bombing)) || strcmp(test_bombing, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    if (!human_turn_actionable(faction_id)) return;
    int source_base_id = first_owned_base(faction_id);
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate) && !is_human(candidate)) {
            other = candidate;
            break;
        }
    }
    if (source_base_id < 0 || other < 0) return;
    int target_x = -1;
    int target_y = -1;
    for (int distance = 2; distance <= 3 && target_x < 0; ++distance) {
        for (int y = 0; y < *MapAreaY && target_x < 0; ++y) {
            for (int x = y & 1; x < *MapAreaX; x += 2) {
                MAP* sq = mapsq(x, y);
                if (!sq || is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0
                || map_range(Bases[source_base_id].x, Bases[source_base_id].y,
                    x, y) != distance) continue;
                target_x = x;
                target_y = y;
                break;
            }
        }
    }
    if (target_x < 0) return;
    int target_base_id = mod_base_init(other, target_x, target_y);
    if (target_base_id < 0 || target_base_id >= *BaseCount) return;
    strcpy_n(Bases[target_base_id].name, sizeof(BASE::name), "Harness Bombing Target");
    MAP* target_sq = mapsq(target_x, target_y);
    target_sq->visibility |= 1 << faction_id;
    spot_base(target_base_id, faction_id);
    treaty_on(faction_id, other, DIPLO_COMMLINK | DIPLO_VENDETTA);
    treaty_on(other, faction_id, DIPLO_COMMLINK | DIPLO_VENDETTA);

    const int component_techs[] = {
        Chassis[CHS_NEEDLEJET].preq_tech,
        Weapon[WPN_LASER].preq_tech,
        Armor[ARM_NO_ARMOR].preq_tech,
        Reactor[REC_FISSION - 1].preq_tech,
    };
    for (int tech_id : component_techs) {
        if (tech_id >= 0 && tech_id < MaxTechnologyNum) {
            TechOwners[tech_id] |= 1 << faction_id;
        }
    }
    char design_name[] = "Harness Bomber";
    int prototype_id = propose_proto(faction_id, CHS_NEEDLEJET, WPN_LASER,
        ARM_NO_ARMOR, 0, REC_FISSION, PLAN_OFFENSE, design_name);
    if (prototype_id < faction_id * MaxProtoFactionNum
    || prototype_id >= min(MaxProtoNum, (faction_id + 1) * MaxProtoFactionNum)) return;
    Units[prototype_id].unit_flags |= UNIT_PROTOTYPED;
    int veh_id = veh_init(prototype_id, faction_id,
        Bases[source_base_id].x, Bases[source_base_id].y);
    if (veh_id < 0) return;
    Vehs[veh_id].moves_spent = 0;
    Vehs[veh_id].movement_turns = 0;
    Vehs[veh_id].order = ORDER_NONE;
    Vehs[veh_id].state &= ~VSTATE_ON_ALERT;
    Vehs[veh_id].home_base_id = source_base_id;
    spot_all(veh_id, 1);
    test_bombing_run_fixture_initialized = true;
}

bool human_turn_actionable(int faction_id) {
    return game_active() && *CurrentFaction == faction_id
        && !*WinModalState && !*PopupDialogState && !*GameHalted;
}

void ensure_test_single_unit_upgrade_fixture() {
    if (test_single_unit_upgrade_fixture_initialized || !game_active()) return;
    char test_mode[8] = {};
    char test_upgrade[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_SINGLE_UNIT_UPGRADE", test_upgrade,
        sizeof(test_upgrade)) || strcmp(test_upgrade, "1")) return;
    int faction_id = *CurrentPlayerFaction;
    int base_id = first_owned_base(faction_id);
    if (base_id < 0 || !human_turn_actionable(faction_id)) return;
    char design_name[] = "Harness Single Upgrade";
    int target_id = propose_proto(faction_id, CHS_INFANTRY, WPN_LASER,
        ARM_SYNTHMETAL_ARMOR, 0, REC_FISSION, PLAN_AUTO_CALCULATE, design_name);
    if (target_id < faction_id * MaxProtoFactionNum
    || target_id >= min(MaxProtoNum, (faction_id + 1) * MaxProtoFactionNum)) return;
    Units[target_id].unit_flags |= UNIT_PROTOTYPED;
    BASE& base = Bases[base_id];
    for (int index = 0; index < 2; ++index) {
        int veh_id = veh_init(BSC_SCOUT_PATROL, faction_id, base.x, base.y);
        if (veh_id < 0) return;
        Vehs[veh_id].moves_spent = 0;
        Vehs[veh_id].order = ORDER_NONE;
    }
    Factions[faction_id].energy_credits = max(Factions[faction_id].energy_credits, 1000);
    test_single_unit_upgrade_fixture_initialized = true;
}

bool terrain_destruction_unit_eligible(int veh_id) {
    if (veh_id < 0 || veh_id >= *VehCount || !veh_unmoved(veh_id)
    || !veh_ready(veh_id)) return false;
    VEH& veh = Vehs[veh_id];
    MAP* sq = mapsq(veh.x, veh.y);
    if (!sq || sq->base_who() >= 0) return false;
    UNIT& unit = Units[veh.unit_id];
    if (!unit.is_combat_unit() && unit.weapon_mode() != WMODE_TERRAFORM) return false;
    return unit.triad() == TRIAD_AIR
        || (unit.triad() == TRIAD_SEA) == is_ocean(sq);
}

bool self_destruct_unit_eligible(int faction_id, int veh_id) {
    if (veh_id < 0 || veh_id >= *VehCount
    || Vehs[veh_id].faction_id != faction_id
    || !human_turn_actionable(faction_id)
    || !semantic_unit_requires_decision(veh_id)) return false;
    VEH& veh = Vehs[veh_id];
    return veh.order == ORDER_NONE && base_at(veh.x, veh.y) < 0
        && !(veh.flags & VFLAG_IS_OBJECTIVE);
}

bool single_unit_upgrade_path_legal(int faction_id, int source_id, int target_id) {
    if (faction_id <= 0 || faction_id >= MaxPlayerNum
    || source_id < 0 || source_id >= MaxProtoNum
    || target_id < faction_id * MaxProtoFactionNum
    || target_id >= min(MaxProtoNum, (faction_id + 1) * MaxProtoFactionNum)
    || source_id == target_id) return false;
    UNIT& source = Units[source_id];
    UNIT& target = Units[target_id];
    if (!target.is_active() || (target.obsolete_factions & (1 << faction_id))) return false;
    if (source_id < MaxProtoFactionNum
    && (source.offense_value() < 0 || source_id == BSC_SPORE_LAUNCHER)) return false;
    if (source.chassis_id != target.chassis_id
    || ((source.ability_flags & ABL_ARTILLERY)
        != (target.ability_flags & ABL_ARTILLERY))) return false;
    if (source.plan <= PLAN_RECON) {
        if (target.plan > PLAN_RECON) return false;
    } else if (source.plan != target.plan) {
        return false;
    }
    if (source.offense_value()) {
        if (target.offense_value() < source.offense_value()) return false;
    } else if (source.weapon_id != target.weapon_id) {
        return false;
    }
    if (target.defense_value() < source.defense_value()) {
        if (target.defense_value() == 1) return false;
        if (source.offense_value()
        && target.offense_value() <= source.offense_value()) return false;
    }
    return true;
}

bool terrain_destruction_item_available(uint32_t items, int former_id) {
    if (former_id < FORMER_FARM || former_id >= FORMER_MONOLITH
    || former_id == FORMER_REMOVE_FUNGUS || former_id == FORMER_PLANT_FUNGUS) {
        return false;
    }
    uint32_t bit = Terraform[former_id].bit;
    if (!bit || !(items & bit)) return false;
    // The native Console_destroy menu makes the compound enhancement the
    // only selectable layer until it is removed.
    if (former_id == FORMER_FARM && (items & BIT_SOIL_ENRICHER)) return false;
    if (former_id == FORMER_ROAD && (items & BIT_MAGTUBE)) return false;
    return true;
}

struct GovernorPermissionSpec {
    const char* key;
    uint32_t mask;
    const char* meaning;
};

const GovernorPermissionSpec GovernorPermissions[] = {
    {"multiple_priorities", GOV_MULTI_PRIORITIES, "Allow more than one Explore/Discover/Build/Conquer priority."},
    {"exploration_units", GOV_MAY_PROD_EXPLORE_VEH, "Allow units intended for exploration."},
    {"land_combat_units", GOV_MAY_PROD_LAND_COMBAT, "Allow offensive land combat units."},
    {"naval_combat_units", GOV_MAY_PROD_NAVAL_COMBAT, "Allow offensive naval combat units."},
    {"air_combat_units", GOV_MAY_PROD_AIR_COMBAT, "Allow offensive air combat units."},
    {"native_life_units", GOV_MAY_PROD_NATIVE, "Allow native life forms."},
    {"land_defense_units", GOV_MAY_PROD_LAND_DEFENSE, "Allow defensive land units."},
    {"air_defense_units", GOV_MAY_PROD_AIR_DEFENSE, "Allow Air Superiority defenders."},
    {"prototype_units", GOV_MAY_PROD_PROTOTYPE, "Allow units whose design is not yet prototyped."},
    {"transport_units", GOV_MAY_PROD_TRANSPORT, "Allow transport units."},
    {"probe_units", GOV_MAY_PROD_PROBES, "Allow probe teams."},
    {"terraformer_units", GOV_MAY_PROD_TERRAFORMERS, "Allow land or sea Formers."},
    {"colony_pods", GOV_MAY_PROD_COLONY_POD, "Allow Colony Pods."},
    {"facilities", GOV_MAY_PROD_FACILITIES, "Allow ordinary base facilities."},
    {"force_psych", GOV_MAY_FORCE_PSYCH, "Allow the governor to use psych specialists when needed."},
    {"secret_projects", GOV_MAY_PROD_SP, "Allow Secret Projects."},
    {"hurry_production", GOV_MAY_HURRY_PRODUCTION, "Allow spending energy to hurry production."},
};

const GovernorPermissionSpec* governor_permission(const std::string& key) {
    for (const GovernorPermissionSpec& permission : GovernorPermissions) {
        if (key == permission.key) return &permission;
    }
    return NULL;
}

void append_governor_permissions(std::ostringstream& out, uint32_t flags) {
    out << '{';
    bool comma = false;
    for (const GovernorPermissionSpec& permission : GovernorPermissions) {
        if (comma) out << ',';
        comma = true;
        out << json_string(permission.key) << ':'
            << ((flags & permission.mask) ? "true" : "false");
    }
    out << '}';
}

bool deferred_native_action_pending() {
    return deferred_diplomacy_faction_id >= 0 || deferred_council_faction_id >= 0
        || deferred_nerve_staple_base_id >= 0 || deferred_obliterate_base_id >= 0
        || deferred_destroy_unit_id >= 0
        || deferred_move_unit_id >= 0
        || deferred_probe_unit_id >= 0 || deferred_missile_unit_id >= 0
        || pending_council_timer_stage > 0;
}

bool safe_path_component(const std::string& value, size_t max_length) {
    if (value.empty() || value.size() > max_length) return false;
    for (unsigned char c : value) {
        if (!(isalnum(c) || c == '-' || c == '_')) return false;
    }
    return true;
}

bool safe_scenario_id(const std::string& value) {
    if (value.empty() || value.size() > 512) return false;
    if (value.size() < 3 || _stricmp(value.c_str() + value.size() - 3, ".SC")) {
        return false;
    }
    bool component_start = true;
    size_t component_length = 0;
    for (unsigned char c : value) {
        if (c == '/' || c == '\\') {
            if (component_start || component_length > 96) return false;
            component_start = true;
            component_length = 0;
            continue;
        }
        if (component_start && (c == '.' || c == ' ')) return false;
        if (!(isalnum(c) || c == ' ' || c == '_' || c == '-' || c == '.'
            || c == '\'' || c == '(' || c == ')')) {
            return false;
        }
        component_start = false;
        ++component_length;
    }
    return !component_start && component_length <= 96
        && value.find("..") == std::string::npos;
}

std::string agent_scenario_path(const std::string& scenario_id) {
    std::string path = "scenarios\\";
    for (char c : scenario_id) path += c == '/' ? '\\' : c;
    return path;
}

std::string agent_save_path(const std::string& slot) {
    return std::string("saves\\agent\\") + agent_match_id + "\\" + slot + ".sav";
}

bool ensure_agent_save_directory() {
    if (!safe_path_component(agent_match_id, 80)) return false;
    CreateDirectoryA("saves", NULL);
    CreateDirectoryA("saves\\agent", NULL);
    std::string match_directory = std::string("saves\\agent\\") + agent_match_id;
    if (CreateDirectoryA(match_directory.c_str(), NULL)) return true;
    return GetLastError() == ERROR_ALREADY_EXISTS;
}

std::string semantic_not_actionable() {
    return error_response("not_actionable",
        "The human faction is not in a non-modal actionable turn. Read snapshot.interaction before retrying.");
}

std::string production_name(int item_id) {
    if (item_id >= 0 && item_id < MaxProtoNum) return Units[item_id].name;
    int facility_id = -item_id;
    if (facility_id >= Fac_ID_First && facility_id <= SP_ID_Last && Facility[facility_id].name) {
        return Facility[facility_id].name;
    }
    return "Unknown production";
}

bool production_item_buildable(int faction_id, int base_id, int item_id, int queue_count = 0) {
    if (item_id >= 0) {
        return item_id < MaxProtoNum && Units[item_id].name[0]
            && mod_veh_avail(item_id, faction_id, base_id)
            && can_build_unit(base_id, item_id);
    }
    int facility_id = -item_id;
    return facility_id >= Fac_ID_First && facility_id <= SP_ID_Last
        && mod_facility_avail(static_cast<FacilityId>(facility_id), faction_id,
            base_id, queue_count)
        && can_build(base_id, facility_id);
}

const char* unit_order_name(int order) {
    switch (order) {
    case ORDER_NONE: return "none";
    case ORDER_SENTRY_BOARD: return "sentry_or_boarded";
    case ORDER_HOLD: return "hold";
    case ORDER_CONVOY: return "convoy";
    case ORDER_MOVE_TO: return "go_to";
    case ORDER_ROAD_TO: return "road_to";
    case ORDER_MAGTUBE_TO: return "magtube_to";
    default:
        return order >= VehOrderFormerFirst && order <= VehOrderFormerLast
            ? "terraform" : "other";
    }
}

const char* semantic_unit_order_name(const VEH& veh) {
    if (veh.state & VSTATE_EXPLORE) return "auto_explore";
    if (semantic_native_automation_active(veh)) {
        switch (veh.order_auto_type) {
        case ORDERA_TERRA_AUTO_FULL: return "auto_former_full";
        case ORDERA_TERRA_AUTO_ROAD: return "auto_former_roads";
        case ORDERA_TERRA_AUTO_MAGTUBE: return "auto_former_magtubes";
        case ORDERA_TERRA_AUTOIMPROVE_BASE: return "auto_former_improve_home_base";
        case ORDERA_TERRA_FARM_SOLAR_ROAD: return "auto_former_farm_solar_road";
        case ORDERA_TERRA_FARM_MINE_ROAD: return "auto_former_farm_mine_road";
        case ORDERA_TERRA_AUTO_FUNGUS_REM: return "auto_former_remove_fungus";
        case ORDERA_TERRA_AUTO_SENSOR: return "auto_former_sensors";
        case ORDERA_BOMBING_RUN: return "bombing_run";
        case ORDERA_ON_ALERT: return "on_alert";
        case ORDERA_AUTOMATE_AIR_DEFENSE: return "auto_air_defense";
        default: return "native_automation";
        }
    }
    return unit_order_name(veh.order);
}

int former_automation_mode_id(const std::string& mode) {
    if (mode == "full") return ORDERA_TERRA_AUTO_FULL;
    if (mode == "roads") return ORDERA_TERRA_AUTO_ROAD;
    if (mode == "magtubes") return ORDERA_TERRA_AUTO_MAGTUBE;
    if (mode == "improve_home_base") return ORDERA_TERRA_AUTOIMPROVE_BASE;
    if (mode == "farm_solar_road") return ORDERA_TERRA_FARM_SOLAR_ROAD;
    if (mode == "farm_mine_road") return ORDERA_TERRA_FARM_MINE_ROAD;
    if (mode == "remove_fungus") return ORDERA_TERRA_AUTO_FUNGUS_REM;
    if (mode == "sensors") return ORDERA_TERRA_AUTO_SENSOR;
    return -1;
}

bool former_automation_mode_available(int faction_id, VEH& veh, int mode_id) {
    if (!veh.is_former()) return false;
    MAP* sq = mapsq(veh.x, veh.y);
    if (!sq) return false;
    bool ocean = is_ocean(sq);
    switch (mode_id) {
    case ORDERA_TERRA_AUTO_FULL:
        return true;
    case ORDERA_TERRA_AUTO_ROAD:
        return has_terra(FORMER_ROAD, ocean, faction_id);
    case ORDERA_TERRA_AUTO_MAGTUBE:
        return has_terra(FORMER_MAGTUBE, ocean, faction_id);
    case ORDERA_TERRA_AUTOIMPROVE_BASE:
        return veh.home_base_id >= 0 && veh.home_base_id < *BaseCount
            && Bases[veh.home_base_id].faction_id == faction_id;
    case ORDERA_TERRA_FARM_SOLAR_ROAD:
        return has_terra(FORMER_FARM, ocean, faction_id)
            && has_terra(FORMER_SOLAR, ocean, faction_id)
            && has_terra(FORMER_ROAD, ocean, faction_id);
    case ORDERA_TERRA_FARM_MINE_ROAD:
        return has_terra(FORMER_FARM, ocean, faction_id)
            && has_terra(FORMER_MINE, ocean, faction_id)
            && has_terra(FORMER_ROAD, ocean, faction_id);
    case ORDERA_TERRA_AUTO_FUNGUS_REM:
        return has_terra(FORMER_REMOVE_FUNGUS, ocean, faction_id);
    case ORDERA_TERRA_AUTO_SENSOR:
        return has_terra(FORMER_SENSOR, ocean, faction_id);
    default:
        return false;
    }
}

int semantic_return_base_candidate(int faction_id, int veh_id) {
    if (veh_id < 0 || veh_id >= *VehCount) return -1;
    VEH& veh = Vehs[veh_id];
    // Air-unit recovery can also select carriers and standalone airbases.  It
    // remains withheld until those native candidate classes receive the same
    // fair-play prediction and deterministic coverage as bases.
    if (veh.faction_id != faction_id || veh.triad() == TRIAD_AIR
    || base_at(veh.x, veh.y) >= 0) return -1;
    int candidate_id = -1;
    int candidate_distance = 9999;
    int veh_region = region_at(veh.x, veh.y);
    for (int base_id = 0; base_id < *BaseCount; ++base_id) {
        BASE& base = Bases[base_id];
        if (base.faction_id != faction_id && !has_pact(faction_id, base.faction_id)) continue;
        if (!is_known(base.x, base.y, faction_id)) continue;
        if (veh.triad() == TRIAD_LAND && region_at(base.x, base.y) != veh_region) continue;
        if (veh.triad() == TRIAD_SEA && !base_on_sea(base_id, veh_region)) continue;
        int distance = map_range(veh.x, veh.y, base.x, base.y);
        if (distance < candidate_distance) {
            candidate_id = base_id;
            candidate_distance = distance;
        }
    }
    return candidate_id;
}

int semantic_air_safe_range(int veh_id) {
    if (veh_id < 0 || veh_id >= *VehCount || Rules->move_rate_roads <= 0) return -1;
    VEH& veh = Vehs[veh_id];
    if (veh.triad() != TRIAD_AIR || veh.is_missile()) return -1;
    // Gravships and any other non-missile range-zero aircraft do not consume
    // the Needlejet/Copter fuel clock.
    if (!veh.range()) return 9999;
    int speed = veh_speed(veh_id, 0);
    int moves_left = speed - veh.moves_spent;
    int total_moves = clamp(moves_left, 0, 999)
        + (veh.range() - veh.movement_turns - 1) * speed;
    // Do not rely on the native emergency damage extension for range-one
    // aircraft: semantic routing promises an ordinary, non-sacrificial
    // recovery path.
    return max(0, total_moves / Rules->move_rate_roads);
}

int semantic_air_full_safe_range(int veh_id) {
    if (veh_id < 0 || veh_id >= *VehCount || Rules->move_rate_roads <= 0) return -1;
    VEH& veh = Vehs[veh_id];
    if (veh.triad() != TRIAD_AIR || veh.is_missile()) return -1;
    if (!veh.range()) return 9999;
    return max(0, (static_cast<int>(veh.range()) * veh_speed(veh_id, 0))
        / Rules->move_rate_roads);
}

bool semantic_bombing_run_unit_eligible(int faction_id, int veh_id) {
    if (veh_id < 0 || veh_id >= *VehCount) return false;
    VEH& veh = Vehs[veh_id];
    return !*MultiplayerActive && veh.faction_id == faction_id
        && veh.triad() == TRIAD_AIR && !veh.is_missile()
        && veh.is_combat_unit() && veh_unmoved(veh_id)
        && veh.order == ORDER_NONE && !(veh.state & VSTATE_ON_ALERT);
}

bool semantic_bombing_run_target_eligible(int faction_id, int veh_id,
int target_x, int target_y, std::string* reason = NULL) {
    auto reject = [&](const char* value) {
        if (reason) *reason = value;
        return false;
    };
    if (!semantic_bombing_run_unit_eligible(faction_id, veh_id)) {
        return reject("unit_not_ready_bomber");
    }
    MAP* sq = mapsq(target_x, target_y);
    if (!sq || !sq->is_visible(faction_id)) return reject("target_not_currently_visible");
    int base_id = base_at(target_x, target_y);
    if (base_id < 0 || base_id >= *BaseCount) return reject("target_is_not_a_base");
    int owner = Bases[base_id].faction_id;
    if (owner < 1 || owner == faction_id || !is_alive(owner)) {
        return reject("target_is_not_a_living_foreign_base");
    }
    if (!at_war(faction_id, owner)) return reject("target_owner_not_at_vendetta");
    int safe_range = semantic_air_safe_range(veh_id);
    if (safe_range >= 0
    && 2 * map_range(Vehs[veh_id].x, Vehs[veh_id].y, target_x, target_y) > safe_range) {
        return reject("target_exceeds_non_sacrificial_round_trip_range");
    }
    return true;
}

int semantic_carrier_inbound_count(int carrier_id, int exclude_aircraft_id = -1) {
    if (semantic_carrier_capacity(carrier_id) <= 0) return 0;
    VEH& carrier = Vehs[carrier_id];
    int count = 0;
    for (int aircraft_id = 0; aircraft_id < *VehCount; ++aircraft_id) {
        if (aircraft_id == exclude_aircraft_id || aircraft_id == carrier_id) continue;
        VEH& aircraft = Vehs[aircraft_id];
        if (aircraft.faction_id == carrier.faction_id
        && aircraft.triad() == TRIAD_AIR && !aircraft.is_missile() && aircraft.range()
        && aircraft.order == ORDER_MOVE_TO
        && aircraft.waypoint_x[0] == carrier.x
        && aircraft.waypoint_y[0] == carrier.y) ++count;
    }
    return count;
}

int semantic_carrier_unboarded_count(int carrier_id, int exclude_aircraft_id = -1) {
    if (semantic_carrier_capacity(carrier_id) <= 0) return 0;
    VEH& carrier = Vehs[carrier_id];
    int count = 0;
    for (int aircraft_id = veh_at(carrier.x, carrier.y); aircraft_id >= 0;
    aircraft_id = Vehs[aircraft_id].next_veh_id_stack) {
        if (aircraft_id == exclude_aircraft_id || aircraft_id == carrier_id) continue;
        VEH& aircraft = Vehs[aircraft_id];
        if (aircraft.faction_id == carrier.faction_id
        && aircraft.triad() == TRIAD_AIR && !aircraft.is_missile() && aircraft.range()
        && !semantic_aircraft_boarded_on(aircraft_id, carrier_id)) ++count;
    }
    return count;
}

int semantic_carrier_dependency_count(int carrier_id) {
    return semantic_carrier_inbound_count(carrier_id)
        + semantic_carrier_unboarded_count(carrier_id);
}

bool semantic_carrier_recovery_eligible(int faction_id, int aircraft_id,
int carrier_id, std::string* reason = NULL) {
    const char* failure = NULL;
    if (*MultiplayerActive) {
        failure = "carrier recovery is withheld in multiplayer until native synchronization is validated";
    } else if (aircraft_id < 0 || aircraft_id >= *VehCount
    || Vehs[aircraft_id].faction_id != faction_id) {
        failure = "unit_id must identify an owned aircraft";
    } else if (carrier_id < 0 || carrier_id >= *VehCount || carrier_id == aircraft_id
    || Vehs[carrier_id].faction_id != faction_id
    || semantic_carrier_capacity(carrier_id) <= 0) {
        failure = "target_unit_id must identify an owned carrier";
    } else {
        VEH& aircraft = Vehs[aircraft_id];
        VEH& carrier = Vehs[carrier_id];
        int safe_range = semantic_air_safe_range(aircraft_id);
        int loaded = veh_cargo_loaded(carrier_id);
        int inbound = semantic_carrier_inbound_count(carrier_id, aircraft_id);
        bool carrier_available = (carrier.order == ORDER_NONE && veh_unmoved(carrier_id))
            || (carrier.order == ORDER_HOLD
                && semantic_carrier_dependency_count(carrier_id) > 0);
        if (aircraft.triad() != TRIAD_AIR || aircraft.is_missile() || !aircraft.range()) {
            failure = "only a fuel-limited non-missile aircraft can recover to a carrier";
        } else if (!veh_unmoved(aircraft_id) || aircraft.order != ORDER_NONE) {
            failure = "the aircraft must be ready and free of persistent orders";
        } else if (aircraft.x == carrier.x && aircraft.y == carrier.y) {
            failure = "the aircraft is already co-located; use board_carrier";
        } else if (!carrier_available) {
            failure = "the carrier must be ready or already held for an active recovery";
        } else if (loaded + inbound >= semantic_carrier_capacity(carrier_id)) {
            failure = "the carrier has no unreserved aircraft capacity";
        } else if (safe_range < 0
        || map_range(aircraft.x, aircraft.y, carrier.x, carrier.y) > safe_range) {
            failure = "the carrier is beyond the aircraft's remaining safe fuel range";
        }
    }
    if (reason) *reason = failure ? failure : "legal";
    return !failure;
}

bool semantic_friendly_air_refuel_tile(int faction_id, int x, int y) {
    MAP* sq = mapsq(x, y);
    if (!sq || !sq->is_airbase()) return false;
    int base_id = base_at(x, y);
    int owner = base_id >= 0 ? Bases[base_id].faction_id : sq->owner;
    if (base_id < 0 && !sq->is_visible(faction_id)) return false;
    return owner < 1 || owner == faction_id || has_pact(faction_id, owner);
}

bool semantic_go_to_tile_eligible(int faction_id, int veh_id, int x, int y,
std::string* reason = NULL) {
    const char* failure = NULL;
    if (veh_id < 0 || veh_id >= *VehCount || Vehs[veh_id].faction_id != faction_id) {
        failure = "unit_id must identify an owned unit";
    } else if (!mapsq(x, y) || !is_known(x, y, faction_id)) {
        failure = "the destination must be an on-map tile known to the player faction";
    } else if (Vehs[veh_id].x == x && Vehs[veh_id].y == y) {
        failure = "the unit is already on this tile";
    } else {
        VEH& veh = Vehs[veh_id];
        if (veh.triad() == TRIAD_AIR && !veh.is_missile() && veh.range()) {
            int safe_range = semantic_air_safe_range(veh_id);
            int distance = map_range(veh.x, veh.y, x, y);
            bool origin_refuels = semantic_friendly_air_refuel_tile(
                faction_id, veh.x, veh.y);
            bool target_refuels = semantic_friendly_air_refuel_tile(
                faction_id, x, y);
            bool target_has_owned_carrier = false;
            for (int target_id = veh_at(x, y); target_id >= 0;
            target_id = Vehs[target_id].next_veh_id_stack) {
                if (Vehs[target_id].faction_id == faction_id
                && semantic_carrier_capacity(target_id) > 0) {
                    target_has_owned_carrier = true;
                }
            }
            if (target_has_owned_carrier) {
                failure = "use target_unit_id and recover_to_carrier so deck capacity is reserved and the carrier is held";
            } else if (safe_range < 0) {
                failure = "this air unit does not use ordinary recoverable aircraft fuel rules";
            } else if (target_refuels && distance > safe_range) {
                failure = "the refueling destination is beyond the aircraft's remaining safe fuel range";
            } else if (!target_refuels && !origin_refuels) {
                failure = "an aircraft already away from a friendly refueling tile must recover before accepting a persistent non-refueling route";
            } else if (!target_refuels && distance > safe_range / 2) {
                failure = "the non-refueling destination is beyond the aircraft's remaining safe round-trip range";
            }
        }
    }
    if (reason) *reason = failure ? failure : "legal";
    return !failure;
}

bool semantic_go_to_base_eligible(int faction_id, int veh_id, int base_id,
std::string* reason = NULL) {
    const char* failure = NULL;
    if (veh_id < 0 || veh_id >= *VehCount || Vehs[veh_id].faction_id != faction_id) {
        failure = "unit_id must identify an owned unit";
    } else if (base_id < 0 || base_id >= *BaseCount
    || Bases[base_id].faction_id != faction_id) {
        failure = "base_id must identify an owned base";
    } else {
        VEH& veh = Vehs[veh_id];
        BASE& base = Bases[base_id];
        if (!is_known(base.x, base.y, faction_id)) {
            failure = "the owned base is not currently known";
        } else if (veh.x == base.x && veh.y == base.y) {
            failure = "the unit is already at this base";
        } else if (veh.triad() == TRIAD_LAND
        && region_at(veh.x, veh.y) != region_at(base.x, base.y)) {
            failure = "a land unit cannot route to this disconnected land region";
        } else if (veh.triad() == TRIAD_SEA
        && !base_on_sea(base_id, region_at(veh.x, veh.y))) {
            failure = "a sea unit cannot enter this base from its current ocean region";
        } else if (veh.triad() == TRIAD_AIR) {
            int safe_range = semantic_air_safe_range(veh_id);
            if (safe_range < 0) {
                failure = "this air unit does not use ordinary recoverable aircraft fuel rules";
            } else if (map_range(veh.x, veh.y, base.x, base.y) > safe_range) {
                failure = "this base is beyond the aircraft's remaining safe fuel range";
            }
        }
    }
    if (reason) *reason = failure ? failure : "legal";
    return !failure;
}

bool semantic_has_base_destination(int faction_id, int veh_id) {
    for (int base_id = 0; base_id < *BaseCount; ++base_id) {
        if (semantic_go_to_base_eligible(faction_id, veh_id, base_id)) return true;
    }
    return false;
}

bool semantic_air_defense_eligible(VEH& veh) {
    return veh.triad() == TRIAD_AIR && !veh.is_missile()
        && veh.is_combat_unit() && has_abil(veh.unit_id, ABL_AIR_SUPERIORITY);
}

const char* social_category_key(int category) {
    static const char* keys[MaxSocialCatNum] = {
        "politics", "economics", "values", "future"
    };
    return category >= 0 && category < MaxSocialCatNum ? keys[category] : "unknown";
}

const char* social_effect_key(int effect) {
    static const char* keys[MaxSocialEffectNum] = {
        "economy", "efficiency", "support", "talent", "morale", "police",
        "growth", "planet", "probe", "industry", "research"
    };
    return effect >= 0 && effect < MaxSocialEffectNum ? keys[effect] : "unknown";
}

void append_social_models(std::ostringstream& out, const CSocialCategory& models) {
    out << '{';
    for (int category = 0; category < MaxSocialCatNum; ++category) {
        if (category) out << ',';
        int model = models.models[category];
        out << json_string(social_category_key(category)) << ":{\"model_id\":" << model
            << ",\"name\":" << json_string(model >= 0 && model < MaxSocialModelNum
                ? SocialField[category].soc_name[model] : "Unknown") << '}';
    }
    out << '}';
}

void append_social_effects(std::ostringstream& out, const CSocialEffect& effects,
bool omit_zero = false) {
    out << '{';
    bool comma = false;
    for (int effect = 0; effect < MaxSocialEffectNum; ++effect) {
        if (omit_zero && effects.values[effect] == 0) continue;
        if (comma) out << ',';
        comma = true;
        out << json_string(social_effect_key(effect)) << ':' << effects.values[effect];
    }
    out << '}';
}

bool reviewed_information_popup(const std::string& label) {
    bool vendetta_statement = label.size() > 8 && label.compare(0, 8, "VENDETTA") == 0
        && label[8] >= '0' && label[8] <= '9';
    // Unity-pod outcomes beginning GOODY* and OGREPOD report an outcome that
    // has already been resolved by the engine. The separate MONOLITH dialog
    // is intentionally excluded because it asks the player what to do.
    bool resolved_unity_pod = label.compare(0, 5, "GOODY") == 0 || label == "OGREPOD";
    bool resolved_technology_notice = label == "WEDEVELOP" || label == "WEACQUIRE"
        || label == "THEYDEVELOP" || label == "THEYACQUIRE" || label == "NEWSTUFF"
        // tech_achieved has already awarded the first-discovery research
        // burst before either THESECRET notice is displayed. Script.txt
        // defines text only and the stock BasePop supplies one OK action.
        || label == "THESECRET0" || label == "THESECRET1";
    bool resolved_probe_notice = label == "DETECTINFILTRATE" || label == "TOOKENERGY"
        || label == "TOOKNOENERGY" || label == "DECIPHERED1" || label == "STOLEMAP1"
        || label == "PRODVIRUS1" || label == "FACVIRUS1" || label == "GENETICWARFARE1";
    bool resolved_project_notice = label == "BEGINPROJECT" || label == "CHANGEPROJECT"
        || label == "HALTPROJECT" || label == "SURVIVEPROJECT" || label == "DONEPROJECT"
        || label == "SEIZEPROJECT" || label == "LOSEPROJECT";
    bool resolved_base_capture_notice = label.compare(0, 9, "SEIZEBASE") == 0
        || label.compare(0, 11, "LIBERATEBASE") == 0 || label == "RENAMEBASE"
        || label == "OBLITTED" || label == "OBLITTED2";
    bool resolved_elimination_notice = label.compare(0, 7, "WIPEOUT") == 0
        || label == "TRACKED";
    bool resolved_diplomacy_notice = label == "PROTORIVAL" || label == "NOTORIETY"
        || label == "DIPLOREFUSE" || label == "DIPLOREFUSE1"
        || label == "DIPLOBUSY" || label == "DIPLOBUSY1"
        || label == "DIPLONOVENDETTA" || label == "NOCOMMLINK"
        || label == "MAKEPACT" || label == "MAKETREATY0" || label == "MAKETREATY1"
        || label == "MAKETRUCE" || label == "COMMERCE" || label == "PACT"
        || label == "BREAKINGPACT" || label == "GOTOUT" || label == "GOTOUT1"
        || label == "BOTHOUT" || label == "TIMETOLEAVE" || label == "TERRITORY"
        || label == "INOURTERRITORY" || label == "INOURTERRITORY2"
        || label == "RENEWTRUCE" || label == "ENDTRUCE"
        || label == "INCITED0" || label == "INCITED1"
        || label == "INCITEDPACT" || label == "INCITEDTREATY"
        || label == "MILD" || label == "MILDTREATY"
        || label == "REBUFFEDPACT" || label == "REBUFFEDTREATY"
        || label == "WHATSUP" || label == "WHATSUPPACT"
        || label == "WHATSUPTREATY" || label == "HELLO"
        || label == "HELLOPACT" || label == "HELLOTREATY"
        || label.compare(0, 6, "GOAWAY") == 0;
    bool resolved_commerce_notice = label == "BUYPROTOHIGH0"
        || label == "BUYCOMMLINKHIGH0" || label == "BUYTECHHIGH0"
        || label == "BUYTECHHIGH1" || label == "TECHFREEBIE"
        || label == "TECHSLAVE" || label == "REJCOMMLINK"
        || label == "REJSELLNONE" || label == "REJSELLAFFORD"
        || label == "REJSELLSECOND" || label == "SELLSECOND"
        || label.compare(0, 11, "REJTECHHATE") == 0
        || label.compare(0, 11, "REJTECHFEAR") == 0
        || label.compare(0, 15, "REJTECHADVANCED") == 0
        || label.compare(0, 11, "REJTECHPROJ") == 0
        || label.compare(0, 12, "REJTECHLATER") == 0;
    bool resolved_base_status = label == "STARVE" || label == "ENERGYCONVOY"
        || label == "POPULATIONLIMIT" || label == "LOWNUTRIENT"
        || label == "LOWNUTRIENTFARMS" || label == "LOWNUTRIENTFORMERS"
        || label == "LOWNUTRIENTSPEC1" || label == "LOWNUTRIENTSPEC2"
        || label == "DRONERIOTS" || label == "DRONERIOTS2"
        || label == "DRONERIOT" || label == "DRONERIOTSOVER"
        || label == "DRONERIOTSOVER2" || label == "GOLDENAGE"
        || label == "GOLDENAGEOVER" || label == "POWERSHORT"
        || label == "PSYCHREQUEST";
    bool resolved_production_notice = label == "PRODUCE" || label == "PRODUCEQ"
        || label == "PRODUCEG" || label == "PRODUCEX" || label == "PRODUCEXQ"
        || label == "PRODUCEXG" || label == "PRODUCE2" || label == "PRODUCE3"
        || label == "PRODUCEPROTO" || label == "PRODUCEPROTOQ"
        || label == "PRODUCEPROTOG";
    return label.compare(0, 10, "PLANETFALL") == 0
        || label == "SIMULYOU" || label == "SIMULWHOSE"
        || label == "ALIENSARRIVE" || label == "SURPRISE"
        // Multiplayer's turn clock raises this local, one-button notice after
        // the shared timer has already crossed its threshold. Acknowledging it
        // only dismisses presentation on this client.
        || label == "TIMEWARNING"
        // The base-support routine has already disbanded the named unit before
        // this warning is shown.  NOSUPPORT therefore reports a completed
        // outcome; it does not offer the player a decision.
        || label == "NOSUPPORT" || label == "WEASELEDOUT" || label == "HALFBRIBE"
        || label == "SEEMONOLITH" || label == "MONOLITH0" || label == "MONOLITHHEAL"
        || label == "CALLSCOUNCIL"
        || label == "COUNCILOPEN" || label == "COUNCILHOTPASS" || label == "COUNCILHOTFAIL"
        || label == "COUNCILHOTVETO" || label == "COUNCILHOTGOVWIN"
        || label == "COUNCILHOTGOVLOSE" || label == "COUNCILHOTGOVNONE"
        || vendetta_statement || resolved_unity_pod || resolved_technology_notice
        || resolved_probe_notice || resolved_project_notice || resolved_base_capture_notice
        || resolved_elimination_notice || resolved_diplomacy_notice
        || resolved_commerce_notice || resolved_base_status || resolved_production_notice;
}

const char* base_status_event(const std::string& label) {
    if (label == "STARVE") return "starvation_population_loss";
    if (label == "ENERGYCONVOY") return "energy_convoy_cancelled";
    if (label == "POPULATIONLIMIT") return "population_limit_reached";
    if (label.compare(0, 11, "LOWNUTRIENT") == 0) return "nutrient_shortage_warning";
    if (label == "DRONERIOTS" || label == "DRONERIOTS2") return "drone_riots_started";
    if (label == "DRONERIOT") return "drone_riot_facility_destroyed";
    if (label == "DRONERIOTSOVER" || label == "DRONERIOTSOVER2") return "drone_riots_ended";
    if (label == "GOLDENAGE") return "golden_age_started";
    if (label == "GOLDENAGEOVER") return "golden_age_ended";
    if (label == "POWERSHORT") return "power_shortage_facility_destroyed";
    if (label == "PSYCHREQUEST") return "governor_psych_request";
    if (label == "OBLITTED" || label == "OBLITTED2") return "base_obliterated";
    return NULL;
}

bool production_completion_label(const std::string& label) {
    return label == "PRODUCE" || label == "PRODUCEQ" || label == "PRODUCEG"
        || label == "PRODUCEX" || label == "PRODUCEXQ" || label == "PRODUCEXG"
        || label == "PRODUCE2" || label == "PRODUCE3"
        || label == "PRODUCEPROTO" || label == "PRODUCEPROTOQ"
        || label == "PRODUCEPROTOG";
}

void apply_deferred_semantics() {
    if (pending_base_name_id >= 0 && !*WinModalState && !*PopupDialogState) {
        if (pending_base_name_id < *BaseCount && !pending_base_name.empty()) {
            strcpy_n(Bases[pending_base_name_id].name, sizeof(BASE::name), pending_base_name.c_str());
        }
        pending_base_name_id = -1;
        pending_base_name.clear();
    }
    if (pending_research_focus_faction_id >= 1
    && pending_research_focus_faction_id < MaxPlayerNum
    && pending_research_focus_priority >= TCAT_GROWTH
    && pending_research_focus_priority <= TCAT_POWER
    && !*WinModalState && !*PopupDialogState) {
        const int faction_id = pending_research_focus_faction_id;
        const int priority = pending_research_focus_priority;
        pending_research_focus_faction_id = -1;
        pending_research_focus_priority = -1;
        Faction& faction = Factions[faction_id];
        faction.AI_growth = priority == TCAT_GROWTH;
        faction.AI_tech = priority == TCAT_TECH;
        faction.AI_wealth = priority == TCAT_WEALTH;
        faction.AI_power = priority == TCAT_POWER;
        if (*MultiplayerActive) {
            // TECHRANDOM's stock caller sends this same command after reading
            // the checkbox result. Reassert at the first post-modal boundary
            // so a semantic value cannot be replaced by stale widget state,
            // then use the native packet path to inform every peer.
            synch_ai(faction_id);
        }
    }
}

int first_owned_base(int faction_id) {
    for (int base_id = 0; base_id < *BaseCount; ++base_id) {
        if (Bases[base_id].faction_id == faction_id) return base_id;
    }
    return -1;
}

bool first_base_name_modal(int faction_id) {
    const char* label = agent_popup_label();
    return (*WinModalState || *PopupDialogState)
        // Alien Crossfire can begin an expansion faction on a non-zero
        // internal turn even though the public year is still the opening
        // year. FIRSTBASE is the authoritative native discriminator and can
        // precede the engine assigning tech_research_id (notably for Alien
        // factions). Requiring research first creates an impossible modal
        // cycle. Retain that guard only for builds where the label is absent.
        && (!strcmp(label, "FIRSTBASE") || (!label[0] && *CurrentTurn == 0
            && Factions[faction_id].tech_research_id >= 0))
        && first_owned_base(faction_id) >= 0;
}

bool native_technology_presentation_active() {
    if (!*WinModalState || !NetTechWin
    || !Win_is_visible(reinterpret_cast<Win*>(NetTechWin))) return false;
    // Stock Alien Crossfire NetTechWindow vtable. Do not treat an unrelated
    // modal object as the passive technology presentation.
    return *reinterpret_cast<uintptr_t*>(NetTechWin) == 0x66CE5C;
}

bool technology_presentation_active() {
    return !pending_multiplayer_technology_presentations.empty()
        || native_technology_presentation_active();
}

bool human_diplomacy_window_active() {
    if (!DiploWin) return false;
    Win* window = reinterpret_cast<Win*>(DiploWin);
    return (*DiploWinState && Win_is_visible(window))
        || (*WinModalState && *ModalStackCurrent == window);
}

void update_human_diplomacy_lifecycle() {
    const bool active = human_diplomacy_window_active();
    if (active) {
        observed_human_diplomacy_active = true;
        human_diplomacy_settle_deadline = 0;
        return;
    }
    if (observed_human_diplomacy_active) {
        // Human multiplayer diplomacy completes through several native
        // packets after the paired windows close.  Hold the semantic turn at
        // a distinct phase so a model cannot race a strategic mutation into
        // that packet tail.
        observed_human_diplomacy_active = false;
        human_diplomacy_settle_deadline = GetTickCount()
            + HumanDiplomacySettleMs;
    }
    if (human_diplomacy_settle_deadline
    && static_cast<LONG>(GetTickCount()
        - human_diplomacy_settle_deadline) >= 0) {
        human_diplomacy_settle_deadline = 0;
    }
}

bool human_diplomacy_settling() {
    update_human_diplomacy_lifecycle();
    return human_diplomacy_settle_deadline != 0;
}

int human_diplomacy_participant(int side) {
    if (!DiploWin || side < 0 || side > 1) return -1;
    return *reinterpret_cast<int*>(reinterpret_cast<char*>(DiploWin)
        + (side ? 0xAB8 : 0xAB4));
}

int human_diplomacy_local_side(int faction_id) {
    if (!human_diplomacy_window_active()) return -1;
    if (human_diplomacy_participant(0) == faction_id) return 0;
    if (human_diplomacy_participant(1) == faction_id) return 1;
    return -1;
}

int human_diplomacy_clause_count(int side) {
    if (!DiploWin || side < 0 || side > 1) return 0;
    int count = *reinterpret_cast<int*>(reinterpret_cast<char*>(DiploWin)
        + 0xA1C + side * 4);
    return max(0, min(count, 8));
}

int human_diplomacy_acceptance(int side) {
    if (!DiploWin || side < 0 || side > 1) return -1;
    return *reinterpret_cast<int*>(reinterpret_cast<char*>(DiploWin)
        + 0xA24 + side * 4);
}

int human_diplomacy_clause_type(int side, int index) {
    if (!DiploWin || side < 0 || side > 1
    || index < 0 || index >= human_diplomacy_clause_count(side)) return -1;
    return *reinterpret_cast<int*>(reinterpret_cast<char*>(DiploWin)
        + 0xA2C + (side * 8 + index) * 4);
}

int human_diplomacy_clause_value(int side, int index) {
    if (!DiploWin || side < 0 || side > 1
    || index < 0 || index >= human_diplomacy_clause_count(side)) return -1;
    return *reinterpret_cast<int*>(reinterpret_cast<char*>(DiploWin)
        + 0xA6C + (side * 8 + index) * 4);
}

const char* human_diplomacy_clause_name(int type) {
    switch (type) {
    case 0: return "technology";
    case 1: return "energy";
    case 2: return "pact";
    case 3: return "treaty";
    case 4: return "truce";
    case 5: return "joint_attack";
    default: return "unknown";
    }
}

bool human_diplomacy_has_clause(int side, int type) {
    for (int index = 0; index < human_diplomacy_clause_count(side); ++index) {
        if (human_diplomacy_clause_type(side, index) == type) return true;
    }
    return false;
}

bool human_diplomacy_has_clause_value(int side, int type, int value) {
    for (int index = 0; index < human_diplomacy_clause_count(side); ++index) {
        if (human_diplomacy_clause_type(side, index) == type
        && human_diplomacy_clause_value(side, index) == value) return true;
    }
    return false;
}

void append_human_diplomacy_clauses(std::ostringstream& out) {
    out << '[';
    bool comma = false;
    for (int side = 0; side < 2; ++side) {
        for (int index = 0; index < human_diplomacy_clause_count(side); ++index) {
            if (comma) out << ',';
            comma = true;
            int type = human_diplomacy_clause_type(side, index);
            int value = human_diplomacy_clause_value(side, index);
            out << "{\"offering_faction_id\":"
                << human_diplomacy_participant(side)
                << ",\"clause\":" << json_string(human_diplomacy_clause_name(type))
                << ",\"native_type\":" << type
                << ",\"value\":" << value;
            if (type == 0 && value >= 0 && value < MaxTechnologyNum) {
                out << ",\"technology_id\":" << value
                    << ",\"technology_name\":" << json_string(Tech[value].name);
            } else if (type == 1) {
                out << ",\"energy_credits\":" << value;
            } else if (type == 5 && value >= 1 && value < MaxPlayerNum) {
                out << ",\"target_faction_id\":" << value
                    << ",\"target_faction_name\":"
                    << json_string(MFactions[value].formal_name_faction);
            }
            out << '}';
        }
    }
    out << ']';
}

bool human_technology_proposal_legal(int faction_id, int tech_id) {
    int side = human_diplomacy_local_side(faction_id);
    int other = side < 0 ? -1 : human_diplomacy_participant(1 - side);
    return side >= 0 && other >= 1 && other < MaxPlayerNum
        && is_human(other) && is_alive(other)
        && human_diplomacy_acceptance(side) == 0
        && human_diplomacy_clause_count(side) < 8
        && tech_id >= 0 && tech_id < MaxTechnologyNum
        && Tech[tech_id].name[0]
        && (TechOwners[tech_id] & (1 << faction_id))
        && !(TechOwners[tech_id] & (1 << other))
        && !human_diplomacy_has_clause_value(side, 0, tech_id);
}

bool human_energy_proposal_legal(int faction_id, int amount) {
    int side = human_diplomacy_local_side(faction_id);
    int other = side < 0 ? -1 : human_diplomacy_participant(1 - side);
    return side >= 0 && other >= 1 && other < MaxPlayerNum
        && is_human(other) && is_alive(other)
        && human_diplomacy_acceptance(side) == 0
        && human_diplomacy_clause_count(side) < 8
        && !human_diplomacy_has_clause(side, 1)
        && amount >= 1 && amount <= Factions[faction_id].energy_credits;
}

bool human_joint_attack_proposal_legal(int faction_id, int target_faction_id) {
    int side = human_diplomacy_local_side(faction_id);
    int other = side < 0 ? -1 : human_diplomacy_participant(1 - side);
    return side >= 0 && other >= 1 && other < MaxPlayerNum
        && is_human(other) && is_alive(other)
        && human_diplomacy_acceptance(side) == 0
        && human_diplomacy_clause_count(side) < 8
        && target_faction_id >= 1 && target_faction_id < MaxPlayerNum
        && target_faction_id != faction_id && target_faction_id != other
        && is_alive(target_faction_id)
        && has_treaty(faction_id, target_faction_id, DIPLO_COMMLINK)
        && !human_diplomacy_has_clause_value(side, 5, target_faction_id);
}

bool human_treaty_proposal_legal(int faction_id) {
    int side = human_diplomacy_local_side(faction_id);
    int other_side = side < 0 ? -1 : 1 - side;
    int other = other_side < 0 ? -1 : human_diplomacy_participant(other_side);
    if (side < 0 || other < 1 || other >= MaxPlayerNum
    || !is_human(other) || !is_alive(other)
    || !has_treaty(faction_id, other, DIPLO_COMMLINK)
    || human_diplomacy_acceptance(side) != 0
    || human_diplomacy_clause_count(side) >= 8
    || human_diplomacy_has_clause(side, 3)) return false;
    int relationship = Factions[faction_id].diplo_status[other];
    return !(relationship
        & (DIPLO_VENDETTA | DIPLO_TRUCE | DIPLO_TREATY | DIPLO_PACT));
}

bool human_pact_proposal_legal(int faction_id) {
    int side = human_diplomacy_local_side(faction_id);
    int other = side < 0 ? -1 : human_diplomacy_participant(1 - side);
    if (side < 0 || other < 1 || other >= MaxPlayerNum
    || !is_human(other) || !is_alive(other)
    || human_diplomacy_acceptance(side) != 0
    || human_diplomacy_clause_count(side) >= 8
    || human_diplomacy_has_clause(side, 2)) return false;
    int relationship = Factions[faction_id].diplo_status[other];
    return (relationship & DIPLO_COMMLINK)
        && (relationship & DIPLO_TREATY)
        && !(relationship & (DIPLO_VENDETTA | DIPLO_TRUCE | DIPLO_PACT));
}

bool human_truce_proposal_legal(int faction_id) {
    int side = human_diplomacy_local_side(faction_id);
    int other = side < 0 ? -1 : human_diplomacy_participant(1 - side);
    if (side < 0 || other < 1 || other >= MaxPlayerNum
    || !is_human(other) || !is_alive(other)
    || human_diplomacy_acceptance(side) != 0
    || human_diplomacy_clause_count(side) >= 8
    || human_diplomacy_has_clause(side, 4)) return false;
    int relationship = Factions[faction_id].diplo_status[other];
    return (relationship & DIPLO_COMMLINK)
        && (relationship & DIPLO_VENDETTA)
        && !(relationship & (DIPLO_TRUCE | DIPLO_TREATY | DIPLO_PACT));
}

int technology_presentation_tech_id() {
    if (!pending_multiplayer_technology_presentations.empty()) {
        return pending_multiplayer_technology_presentations.front();
    }
    if (!native_technology_presentation_active()) return -1;
    int tech_id = *reinterpret_cast<int*>(
        reinterpret_cast<char*>(NetTechWin) + 0xB24);
    return tech_id >= 0 && tech_id < MaxTechnologyNum ? tech_id : -1;
}

std::string interaction_kind(int faction_id) {
    refresh_deferred_end_turn_state();
    update_human_diplomacy_lifecycle();
    if (popup_transition_is_pending()) {
        return "waiting_for_engine";
    }
    if (deferred_native_action_pending()) {
        return "waiting_for_engine";
    }
    if (human_diplomacy_window_active()) {
        return "human_diplomacy";
    }
    if (!pending_multiplayer_technology_presentations.empty()) {
        return "technology_presentation";
    }
    if (*WinModalState || *PopupDialogState) {
        if (first_base_name_modal(faction_id)) return "first_base_name";
        if (technology_presentation_active()) return "technology_presentation";
        if (semantic_popup_label()[0]
        && (endgame_presentation_phase.empty() || active_default_popup())) {
            return "popup";
        }
        if (!endgame_presentation_phase.empty()) {
            if (endgame_presentation_phase == "victory_movie") {
                return "unsupported_modal";
            }
            return pending_endgame_presentation_advance
                ? "waiting_for_engine" : "endgame_presentation";
        }
        if (semantic_popup_label()[0]) return "popup";
        if (Factions[faction_id].tech_research_id < 0) {
            if (*MultiplayerActive) return "waiting_for_engine";
            return (*GameRules & RULES_BLIND_RESEARCH) ? "research_priority" : "research_choice";
        }
        if (first_base_name_modal(faction_id)) return "first_base_name";
        return "unsupported_modal";
    }
    if (deferred_end_turn_faction_id >= 0) return "waiting_for_engine";
    if (!endgame_presentation_phase.empty()) {
        if (endgame_presentation_phase == "victory_movie") {
            return "unsupported_modal";
        }
        return pending_endgame_presentation_advance
            ? "waiting_for_engine" : "endgame_presentation";
    }
    if (human_diplomacy_settling()) return "waiting_for_engine";
    if (*GameHalted) return "waiting_for_engine";
    if (*CurrentFaction != faction_id) return "waiting_for_turn";
    return "turn";
}

const char* semantic_victory_type_name(int victory_type) {
    switch (victory_type) {
    case VIC_NONE: return "none";
    case VIC_TRANSCEND_PLR: return "transcendence_solo";
    case VIC_TRANSCEND_UNK: return "transcendence_unknown";
    case VIC_TRANSCEND_LOSS: return "transcendence_loss";
    case VIC_UNIFY_SOLO: return "unification_solo";
    case VIC_UNIFY_COOP: return "unification_cooperative";
    case VIC_DIPLOMATIC_SOLO: return "diplomatic_solo";
    case VIC_LOST_CAPTURE: return "eliminated_capture";
    case VIC_TIME_LIMIT: return "time_limit";
    case VIC_SUDDEN_DEATH: return "scenario_sudden_death";
    case VIC_DIPLOMATIC_COOP: return "diplomatic_cooperative";
    case VIC_DIPLOMATIC_LOSS: return "diplomatic_loss";
    case VIC_ECONOMIC_SOLO: return "economic_solo";
    case VIC_ECONOMIC_COOP: return "economic_cooperative";
    case VIC_ECONOMIC_LOSS: return "economic_loss";
    case VIC_LOST_REMOVE: return "eliminated";
    case VIC_ALIEN_SOLO: return "alien_solo";
    case VIC_ALIEN_COOP: return "alien_cooperative";
    case VIC_ALIEN_LOSS: return "alien_loss";
    default: return "unknown";
    }
}

const char* semantic_victory_result(int victory_type) {
    switch (victory_type) {
    case VIC_TRANSCEND_PLR:
    case VIC_UNIFY_SOLO:
    case VIC_UNIFY_COOP:
    case VIC_DIPLOMATIC_SOLO:
    case VIC_DIPLOMATIC_COOP:
    case VIC_ECONOMIC_SOLO:
    case VIC_ECONOMIC_COOP:
    case VIC_ALIEN_SOLO:
    case VIC_ALIEN_COOP:
        return "win";
    case VIC_TRANSCEND_LOSS:
    case VIC_LOST_CAPTURE:
    case VIC_DIPLOMATIC_LOSS:
    case VIC_ECONOMIC_LOSS:
    case VIC_LOST_REMOVE:
    case VIC_ALIEN_LOSS:
        return "loss";
    default:
        // The stock engine does not retain enough universal winner identity
        // here to classify time-limit, scenario sudden-death, or the unused
        // transcendence-unknown path without guessing.
        return "unknown";
    }
}

void append_turn_protocol(std::ostringstream& out, int faction_id, int ready_units) {
    std::string interaction = interaction_kind(faction_id);
    out << "\"phase\":";
    if (interaction == "waiting_for_turn" || interaction == "waiting_for_engine") {
        out << "\"wait\",\"required_action\":\"wait_then_observe\","
            << "\"available_choice_kinds\":[]";
    } else if (interaction == "unsupported_modal") {
        out << "\"capability_gap\",\"required_action\":\"report_capability_gap_and_stop\","
            << "\"available_choice_kinds\":[]";
    } else if (interaction != "turn") {
        out << "\"interaction\",\"required_action\":\"resolve_interaction\","
            << "\"available_choice_kinds\":[\"interaction\"]";
    } else {
        out << "\"turn\",\"required_action\":"
            << json_string(ready_units > 0
                ? "resolve_ready_units_before_end_turn"
                : "manage_strategy_or_end_turn")
            << ",\"available_choice_kinds\":[\"research\",\"energy_allocation\","
            << "\"social_engineering\",\"diplomacy\",\"council\",\"unit_design\",\"production\",\"base_management\",\"base_citizens\",\"unit_actions\","
            << "\"game_management\"]"
            << ",\"end_turn_blocked\":" << (ready_units > 0 ? "true" : "false")
            << ",\"ready_unit_count\":" << ready_units;
    }
    out << ",\"contract\":\"Observe, enumerate one legal choice family, execute at most one guarded command, then observe again.\"";
}

bool semantic_interaction_command(const std::string& command) {
    static const char* commands[] = {
        "acknowledge_popup", "respond_to_contact", "continue_diplomacy",
        "propose_human_relationship", "propose_human_technology",
        "propose_human_energy", "propose_human_joint_attack",
        "respond_human_diplomacy",
        "finish_human_diplomacy",
        "choose_diplomacy_option", "give_energy_gift",
        "choose_diplomacy_target", "choose_diplomacy_base_target",
        "cancel_diplomacy_selection", "respond_to_diplomatic_offer",
        "respond_to_council_vote_bargain", "respond_to_incoming_vote_offer",
        "respond_to_territorial_incident", "respond_to_combat_confirmation",
        "respond_to_nerve_gas", "respond_to_end_turn_confirmation",
        "respond_to_base_obliteration", "respond_to_supreme_leader",
        "respond_to_game_over", "advance_endgame_presentation",
        "advance_technology_presentation",
        "respond_to_design_offer",
        "respond_to_artifact", "respond_to_monolith",
        "respond_to_probe_incident", "choose_probe_sabotage_target",
        "respond_to_probe_sabotage_warning", "choose_captive_leader",
        "choose_council_proposal", "cast_council_vote",
        "set_first_base_name", "choose_research_priority", "choose_research",
    };
    for (const char* candidate : commands) {
        if (command == candidate) return true;
    }
    return false;
}

uint32_t semantic_base_state_flags(const BASE& base) {
    // Several native/Thinker bookkeeping bits change while the event loop is
    // settling even though no fair-play choice has changed. Only retain flags
    // which are observed by the semantic surface or alter a command's native
    // legality. This keeps the optimistic-concurrency token meaningful rather
    // than turning repaint/repair bookkeeping into a false stale state.
    return static_cast<uint32_t>(base.state_flags) & (
        BSTATE_DRONE_RIOTS_ACTIVE
        | BSTATE_GOLDEN_AGE_ACTIVE
        | BSTATE_COMBAT_LOSS_LAST_TURN
        | BSTATE_RESEARCH_DATA_STOLEN
        | BSTATE_FACILITY_SCRAPPED
        | BSTATE_ARTIFACT_LINKED
        | BSTATE_RENAME_BASE
        | BSTATE_GENETIC_PLAGUE_INTRO
        | BSTATE_ENERGY_RESERVES_DRAINED
        | BSTATE_PRODUCTION_DONE
        | BSTATE_NET_LOCKED
        | BSTATE_PSI_GATE_USED
        | BSTATE_HURRY_PRODUCTION);
}

uint32_t semantic_vehicle_state_flags(const VEH& veh) {
    // Unknown repair/animation/AI bookkeeping bits are deliberately omitted.
    // Patrol is mixed separately as a derived predicate so its unknown native
    // bit cannot churn the revision while the unit is not actually patrolling.
    return static_cast<uint32_t>(veh.state) & (
        VSTATE_IN_TRANSPORT
        | VSTATE_HAS_MOVED
        | VSTATE_REQUIRES_SUPPORT
        | VSTATE_MADE_AIRDROP
        | VSTATE_DESIGNATE_DEFENDER
        | VSTATE_MONOLITH_UPGRADED
        | VSTATE_ON_ALERT
        | VSTATE_EXPLORE
        | VSTATE_USED_NERVE_GAS
        | VSTATE_PACIFISM_DRONE
        | VSTATE_PACIFISM_FREE_SKIP
        | VSTATE_WORKING);
}

std::string semantic_revision() {
    // Opaque optimistic-concurrency token over fair, action-relevant state.
    // Clear a completed popup handoff before hashing so clients do not retain a
    // synthetic transition state after the native modal has actually changed.
    popup_transition_is_pending();
    update_human_diplomacy_lifecycle();
    uint64_t hash = 1469598103934665603ULL;
    auto mix = [&](uint64_t value) {
        hash ^= value;
        hash *= 1099511628211ULL;
    };
    auto mix_text = [&](const char* value) {
        const unsigned char* p = reinterpret_cast<const unsigned char*>(value ? value : "");
        while (*p) mix(*p++);
        mix(0xff);
    };
    mix(static_cast<uint32_t>(*CurrentTurn));
    mix(static_cast<uint32_t>(*CurrentFaction));
    mix(static_cast<uint32_t>(*CurrentPlayerFaction));
    mix(static_cast<uint32_t>(*WinModalState));
    mix(static_cast<uint32_t>(*PopupDialogState));
    mix(static_cast<uint32_t>(*GameHalted));
    mix(semantic_mutation_generation);
    mix(static_cast<uint32_t>(pending_multiplayer_technology_presentations.size()));
    for (size_t index = 0;
    index < pending_multiplayer_technology_presentations.size(); ++index) {
        mix(static_cast<uint32_t>(
            pending_multiplayer_technology_presentations[index] + 1));
    }
    mix(static_cast<uint32_t>(human_diplomacy_settling() ? 1 : 0));
    if (human_diplomacy_window_active()) {
        for (int side = 0; side < 2; ++side) {
            mix(static_cast<uint32_t>(human_diplomacy_participant(side) + 1));
            mix(static_cast<uint32_t>(human_diplomacy_acceptance(side) + 1));
            int clause_count = human_diplomacy_clause_count(side);
            mix(static_cast<uint32_t>(clause_count));
            for (int index = 0; index < clause_count; ++index) {
                mix(static_cast<uint32_t>(
                    human_diplomacy_clause_type(side, index) + 1));
                mix(static_cast<uint32_t>(
                    human_diplomacy_clause_value(side, index) + 1));
            }
        }
    }
    mix_text(semantic_popup_label());
    mix(static_cast<uint32_t>(pending_popup_transition ? 1 : 0));
    mix(pending_popup_generation);
    mix_text(endgame_presentation_phase.c_str());
    mix(static_cast<uint32_t>(pending_endgame_presentation_advance));
    mix(endgame_presentation_generation);
    mix(static_cast<uint32_t>(deferred_diplomacy_faction_id + 1));
    mix(static_cast<uint32_t>(deferred_council_faction_id + 1));
    auto base_ref_token = [&](int base_id) {
        return base_id >= 0 && base_id < *BaseCount
            ? semantic_tile_id(Bases[base_id].x, Bases[base_id].y) + 1 : 0;
    };
    auto unit_ref_token = [&](int veh_id) {
        return veh_id >= 0 && veh_id < *VehCount
            ? semantic_vehicle_handle(veh_id) + 1 : 0;
    };
    mix(static_cast<uint32_t>(base_ref_token(deferred_nerve_staple_base_id)));
    mix(static_cast<uint32_t>(base_ref_token(deferred_obliterate_base_id)));
    mix(static_cast<uint32_t>(unit_ref_token(deferred_obliterate_unit_id)));
    mix(static_cast<uint32_t>(base_ref_token(active_obliterate_base_id)));
    mix(static_cast<uint32_t>(unit_ref_token(active_obliterate_unit_id)));
    mix(static_cast<uint32_t>(active_obliterate_decision + 1));
    mix(static_cast<uint32_t>(unit_ref_token(deferred_destroy_unit_id)));
    mix(static_cast<uint32_t>(unit_ref_token(deferred_destroy_former_id)));
    mix(static_cast<uint32_t>(deferred_destroy_owner_id + 2));
    mix(static_cast<uint32_t>(deferred_destroy_hostility_confirmed));
    mix(static_cast<uint32_t>(unit_ref_token(deferred_move_unit_id)));
    mix(static_cast<uint32_t>(unit_ref_token(deferred_probe_unit_id)));
    mix(static_cast<uint32_t>(base_ref_token(deferred_probe_base_id)));
    mix(static_cast<uint32_t>(unit_ref_token(deferred_probe_target_unit_id)));
    mix(static_cast<uint32_t>(deferred_probe_action_id + 2));
    mix(static_cast<uint32_t>(unit_ref_token(deferred_missile_unit_id)));
    mix(static_cast<uint32_t>(deferred_missile_x + 1));
    mix(static_cast<uint32_t>(deferred_missile_y + 1));
    mix(static_cast<uint32_t>(unit_ref_token(active_probe_unit_id)));
    mix(static_cast<uint32_t>(base_ref_token(active_probe_base_id)));
    mix(static_cast<uint32_t>(active_probe_abort_requested));
    mix(static_cast<uint32_t>(deferred_move_direction + 1));
    mix(static_cast<uint32_t>(deferred_move_x + 1));
    mix(static_cast<uint32_t>(deferred_move_y + 1));
    mix(static_cast<uint32_t>(last_council_result_valid));
    mix(static_cast<uint32_t>(last_council_proposal_id + 1));
    mix(static_cast<uint32_t>(last_council_ballot_value + 4));
    mix(static_cast<uint32_t>(last_council_result_state + 4));
    mix(static_cast<uint32_t>(last_council_governor_faction_id + 1));
    mix(deferred_action.id);
    mix_text(deferred_action.status.c_str());
    mix(static_cast<uint32_t>(deferred_action.native_result));
    if (*WinModalState || *PopupDialogState) {
        mix(static_cast<uint32_t>(*diplo_second_faction + 1));
        mix(static_cast<uint32_t>(*diplo_third_faction + 1));
        mix(static_cast<uint32_t>(*diplo_intel_faction + 1));
        mix(static_cast<uint32_t>(*diplo_tech_id1 + 1));
        mix(static_cast<uint32_t>(*diplo_vote_offer_tech_id2 + 1));
        mix(static_cast<uint32_t>(*diplo_tech_id2 + 1));
        mix(static_cast<uint32_t>(*diplo_tech_id3 + 1));
        mix(static_cast<uint32_t>(*diplo_tech_id4 + 1));
        mix(static_cast<uint32_t>(*diplo_entry_id + 1));
        mix(static_cast<uint32_t>(*diplo_tech_faction + 1));
        mix(static_cast<uint32_t>(*diplo_trade_faction_id + 1));
        mix(static_cast<uint32_t>(*diplo_ask_base_swap_id + 1));
        mix(static_cast<uint32_t>(*diplo_bid_base_swap_id + 1));
        mix(static_cast<uint32_t>(*diplo_current_proposal_id + 1));
        mix(static_cast<uint32_t>(*diplo_counter_proposal_id + 1));
        mix(static_cast<uint32_t>(ParseNumTable[0]));
        mix(static_cast<uint32_t>(ParseNumTable[1]));
        mix(static_cast<uint32_t>(ParseNumTable[2]));
    }
    if (probe_excuse_context.valid) {
        mix(static_cast<uint32_t>(probe_excuse_context.offender_faction_id));
        mix(static_cast<uint32_t>(probe_excuse_context.target_faction_id));
        mix(static_cast<uint32_t>(probe_excuse_context.action_id));
        mix(static_cast<uint32_t>(probe_excuse_context.framed));
        mix(static_cast<uint32_t>(probe_excuse_context.pact));
    }
    int faction_id = *CurrentPlayerFaction;
    if (faction_id >= 1 && faction_id < MaxPlayerNum) {
        Faction& faction = Factions[faction_id];
        mix(static_cast<uint32_t>(faction.energy_credits));
        mix(static_cast<uint32_t>(faction.tech_research_id));
        mix(static_cast<uint32_t>(faction.tech_accumulated));
        mix(static_cast<uint32_t>(faction.SE_alloc_labs));
        mix(static_cast<uint32_t>(faction.SE_alloc_psych));
        mix(static_cast<uint32_t>(faction.SE_upheaval_cost_paid));
        mix(static_cast<uint32_t>(faction.corner_market_turn));
        mix(static_cast<uint32_t>(faction.corner_market_cost));
        const CSocialCategory* selected =
            reinterpret_cast<const CSocialCategory*>(&faction.SE_Politics_pending);
        const CSocialCategory* established =
            reinterpret_cast<const CSocialCategory*>(&faction.SE_Politics);
        for (int category = 0; category < MaxSocialCatNum; ++category) {
            mix(static_cast<uint32_t>(selected->models[category]));
            mix(static_cast<uint32_t>(established->models[category]));
        }
        mix(static_cast<uint32_t>(faction.AI_growth));
        mix(static_cast<uint32_t>(faction.AI_tech));
        mix(static_cast<uint32_t>(faction.AI_wealth));
        mix(static_cast<uint32_t>(faction.AI_power));
        for (int tech_id = 0; tech_id < MaxTechnologyNum; ++tech_id) {
            if (TechOwners[tech_id] & (1 << faction_id)) mix(static_cast<uint32_t>(tech_id + 1));
        }
        int proto_begin = faction_id * MaxProtoFactionNum;
        int proto_end = min(MaxProtoNum, proto_begin + MaxProtoFactionNum);
        for (int unit_id = proto_begin; unit_id < proto_end; ++unit_id) {
            UNIT& unit = Units[unit_id];
            if (!unit.name[0] && !unit.unit_flags) continue;
            mix(static_cast<uint32_t>(unit_id));
            mix(static_cast<uint32_t>(unit.unit_flags));
            mix(static_cast<uint32_t>(unit.ability_flags));
            mix(static_cast<uint32_t>(unit.chassis_id));
            mix(static_cast<uint32_t>(unit.weapon_id));
            mix(static_cast<uint32_t>(unit.armor_id));
            mix(static_cast<uint32_t>(unit.reactor_id));
            mix(static_cast<uint32_t>(faction.units_active[unit_id]));
            mix(static_cast<uint32_t>(faction.units_queue[unit_id]));
            mix_text(unit.name);
        }
        for (int other = 1; other < MaxPlayerNum; ++other) {
            if (other == faction_id || !(faction.diplo_status[other] & DIPLO_COMMLINK)) continue;
            mix(static_cast<uint32_t>(other));
            mix(static_cast<uint32_t>(faction.diplo_status[other]));
            mix(static_cast<uint32_t>(faction.diplo_spoke[other]));
            mix(static_cast<uint32_t>(faction.loan_balance[other]));
            mix(static_cast<uint32_t>(faction.loan_payment[other]));
            mix(static_cast<uint32_t>(Factions[other].loan_balance[faction_id]));
            mix(static_cast<uint32_t>(Factions[other].loan_payment[faction_id]));
        }
        for (int i = 0; i < *BaseCount; ++i) {
            BASE& base = Bases[i];
            if (base.faction_id != faction_id) {
                MAP* sq = mapsq(base.x, base.y);
                if (sq && sq->is_visible(faction_id)) {
                    mix(static_cast<uint32_t>(base.x));
                    mix(static_cast<uint32_t>(base.y));
                    mix(static_cast<uint32_t>(base.faction_id));
                }
                continue;
            }
            mix(static_cast<uint32_t>(base.x));
            mix(static_cast<uint32_t>(base.y));
            mix(static_cast<uint32_t>(base.pop_size));
            mix(static_cast<uint32_t>(base.queue_size));
            for (int queue_index = 0; queue_index <= base.queue_size
            && queue_index < 10; ++queue_index) {
                mix(static_cast<uint32_t>(base.queue_items[queue_index]));
            }
            mix(static_cast<uint32_t>(base.minerals_accumulated));
            mix(static_cast<uint32_t>(base.mineral_surplus));
            mix(semantic_base_state_flags(base));
            mix(static_cast<uint32_t>(base.governor_flags));
            mix(static_cast<uint32_t>(base.worked_tiles));
            mix(static_cast<uint32_t>(base.specialist_total));
            mix(static_cast<uint32_t>(base.specialist_types[0]));
            mix(static_cast<uint32_t>(base.specialist_types[1]));
            for (size_t facility_word = 0;
            facility_word < sizeof(base.facilities_built); ++facility_word) {
                mix(static_cast<uint32_t>(base.facilities_built[facility_word]));
            }
            mix_text(base.name);
        }
        for (int i = 0; i < *VehCount; ++i) {
            VEH& veh = Vehs[i];
            bool visible = veh.faction_id == faction_id || (veh.visibility & (1 << faction_id));
            if (!visible) continue;
            mix(static_cast<uint32_t>(semantic_vehicle_handle(i)));
            mix(static_cast<uint32_t>(veh.faction_id));
            mix(static_cast<uint32_t>(veh.unit_id));
            mix(static_cast<uint32_t>(veh.x));
            mix(static_cast<uint32_t>(veh.y));
            MAP* occupied_sq = mapsq(veh.x, veh.y);
            if (occupied_sq && occupied_sq->is_visible(faction_id)) {
                mix(static_cast<uint32_t>(occupied_sq->items));
            }
            if (veh.faction_id == faction_id) {
                mix(static_cast<uint32_t>(veh.order));
                mix(static_cast<uint32_t>(veh.order_auto_type));
                mix(static_cast<uint32_t>(veh.waypoint_count));
                mix(static_cast<uint32_t>(veh.is_patrol_order() ? 1 : 0));
                mix(static_cast<uint32_t>(veh.moves_spent));
                mix(static_cast<uint32_t>(base_ref_token(veh.home_base_id)));
                mix(static_cast<uint32_t>(veh.waypoint_x[0] + 2));
                mix(static_cast<uint32_t>(veh.waypoint_y[0] + 2));
                mix(semantic_vehicle_state_flags(veh));
            }
            mix(static_cast<uint32_t>(veh.cur_hitpoints()));
        }
    }
    return std::to_string(hash);
}

void CALLBACK semantic_observation_timer_proc(HWND, UINT, UINT_PTR, DWORD) {
    // Runs only on the native UI thread.  It samples bounded semantic state,
    // never waits, allocates no provider payload, and emits no animation rows.
    if (!lock_initialized || !game_active()) {
        reset_semantic_observation_shadow();
        reset_project_report_memory();
        semantic_vehicle_handles.clear();
        return;
    }
    const int faction_id = *CurrentPlayerFaction;
    if (faction_id < 1 || faction_id >= MaxPlayerNum) {
        reset_semantic_observation_shadow();
        return;
    }
    if (semantic_observation_faction != faction_id) {
        reset_semantic_observation_shadow();
        reset_project_report_memory();
        semantic_observation_faction = faction_id;
    }

    capture_project_report_popup();
    ensure_semantic_vehicle_handles();
    sampled_vehicles.assign(
        static_cast<size_t>(std::max(0, *VehCount)), ObservedVehicleState());
    for (int index = 0; index < *VehCount; ++index) {
        VEH& veh = Vehs[index];
        ObservedVehicleState& current = sampled_vehicles[index];
        current.present = true;
        current.visible = veh.faction_id == faction_id
            || (veh.visibility & (1 << faction_id));
        current.faction_id = veh.faction_id;
        current.unit_id = veh.unit_id;
        current.x = veh.x;
        current.y = veh.y;
        current.hitpoints = veh.cur_hitpoints();
        if (!semantic_observation_shadow_ready
        || index >= static_cast<int>(observed_vehicles.size())) continue;
        const ObservedVehicleState& prior = observed_vehicles[index];
        const bool same_native_row = prior.present
            && prior.faction_id == current.faction_id
            && prior.unit_id == current.unit_id;
        if (!same_native_row) {
            if (current.visible) append_observation_event(
                "visible_unit_appeared", *CurrentTurn,
                semantic_vehicle_handle(index), current.faction_id,
                -1, semantic_tile_id(current.x, current.y));
            continue;
        }
        if (prior.visible && current.visible
        && (prior.x != current.x || prior.y != current.y)) {
            append_observation_event(
                "visible_unit_moved", *CurrentTurn,
                semantic_vehicle_handle(index), current.faction_id,
                semantic_tile_id(prior.x, prior.y),
                semantic_tile_id(current.x, current.y), -1, -1, true);
        }
        if (prior.visible && current.visible
        && prior.hitpoints != current.hitpoints) {
            append_observation_event(
                "visible_unit_damaged", *CurrentTurn,
                semantic_vehicle_handle(index), current.faction_id,
                semantic_tile_id(current.x, current.y),
                semantic_tile_id(current.x, current.y),
                prior.hitpoints, current.hitpoints, true);
        }
        if (prior.visible && !current.visible) {
            append_observation_event(
                "visible_unit_lost", *CurrentTurn,
                semantic_vehicle_handle(index), current.faction_id,
                semantic_tile_id(prior.x, prior.y));
        } else if (!prior.visible && current.visible) {
            append_observation_event(
                "visible_unit_appeared", *CurrentTurn,
                semantic_vehicle_handle(index), current.faction_id,
                -1, semantic_tile_id(current.x, current.y));
        }
    }

    sampled_bases.assign(
        static_cast<size_t>(std::max(0, *BaseCount)), ObservedBaseState());
    for (int index = 0; index < *BaseCount; ++index) {
        BASE& base = Bases[index];
        MAP* square = mapsq(base.x, base.y);
        ObservedBaseState& current = sampled_bases[index];
        current.present = true;
        current.visible = base.faction_id == faction_id
            || (square && square->is_visible(faction_id));
        current.faction_id = base.faction_id;
        current.x = base.x;
        current.y = base.y;
        current.population = base.pop_size;
        if (!semantic_observation_shadow_ready
        || index >= static_cast<int>(observed_bases.size())) continue;
        const ObservedBaseState& prior = observed_bases[index];
        if (prior.present && prior.x == current.x && prior.y == current.y
        && prior.visible && current.visible) {
            if (prior.faction_id != current.faction_id) {
                append_observation_event(
                    "visible_base_captured", *CurrentTurn, index, current.faction_id,
                    semantic_tile_id(current.x, current.y),
                    semantic_tile_id(current.x, current.y),
                    prior.faction_id, current.faction_id, true);
            } else if (prior.population != current.population) {
                append_observation_event(
                    "visible_base_changed", *CurrentTurn, index, current.faction_id,
                    semantic_tile_id(current.x, current.y),
                    semantic_tile_id(current.x, current.y),
                    prior.population, current.population, true);
            }
        }
    }

    if (observed_tiles.size() != static_cast<size_t>(std::max(0, *MapAreaTiles))) {
        observed_tiles.assign(static_cast<size_t>(std::max(0, *MapAreaTiles)), {});
        observed_tile_cursor = 0;
    }
    const size_t tile_count = observed_tiles.size();
    const size_t sample_count = std::min<size_t>(512, tile_count);
    for (size_t scanned = 0; scanned < sample_count; ++scanned) {
        const size_t tile_id = (observed_tile_cursor + scanned) % tile_count;
        int x = -1, y = -1;
        if (!semantic_tile_coords(static_cast<int>(tile_id), &x, &y)) continue;
        MAP* square = mapsq(x, y);
        if (!square) continue;
        ObservedTileState current;
        current.sampled = true;
        current.known = is_known(x, y, faction_id);
        if (current.known) {
            current.visible = square->is_visible(faction_id);
            current.items = current.visible ? square->items : square->visible_items[faction_id - 1];
            current.altitude = current.visible ? square->alt_level() : -1;
            current.climate = current.visible ? square->climate : -1;
            current.owner = current.visible ? square->owner : -1;
        }
        const ObservedTileState prior = observed_tiles[tile_id];
        if (semantic_observation_shadow_ready && prior.sampled && prior.known
        && current.known && prior.visible != current.visible) {
            append_observation_event(
                "known_tile_visibility", *CurrentTurn, static_cast<int>(tile_id), -1,
                static_cast<int>(tile_id), static_cast<int>(tile_id),
                prior.visible ? 1 : 0, current.visible ? 1 : 0, true);
        } else if (semantic_observation_shadow_ready && prior.sampled && prior.known
        && current.known && prior.visible == current.visible && (prior.items != current.items
            || prior.altitude != current.altitude || prior.climate != current.climate
            || prior.owner != current.owner)) {
            append_observation_event(
                "known_tile_changed", *CurrentTurn, static_cast<int>(tile_id), -1,
                static_cast<int>(tile_id), static_cast<int>(tile_id),
                static_cast<int>(prior.items), static_cast<int>(current.items), true);
        }
        observed_tiles[tile_id] = current;
    }
    if (tile_count) observed_tile_cursor = (observed_tile_cursor + sample_count) % tile_count;
    observed_vehicles.swap(sampled_vehicles);
    observed_bases.swap(sampled_bases);
    semantic_observation_shadow_ready = true;
}

std::string observation_feed_response(const std::string& request) {
    // The collector calls this independently of sovereign decisions. Capturing
    // a revision transition is bounded and records no full native structures.
    std::string current_revision = game_active() ? semantic_revision() : "menu";
    if (current_revision != last_observed_action_revision) {
        append_observation_event("perspective_changed",
            game_active() ? *CurrentTurn : -1);
        last_observed_action_revision = current_revision;
    }
    uint64_t after = static_cast<uint64_t>(std::max(0,
        field_int(request, "after_sequence", 0)));
    int limit = std::min(256, std::max(1, field_int(request, "limit", 128)));
    uint64_t oldest = observation_event_count
        ? observation_events[observation_event_start].sequence
        : next_observation_sequence;
    bool incomplete = after && after + 1 < oldest;
    std::ostringstream out;
    out << "{\"ok\":true,\"schema\":\"smacx.native-observation-feed.v1\""
        << ",\"action_revision\":" << json_string(current_revision.c_str())
        << ",\"continuity\":" << json_string(incomplete ? "incomplete" : "complete")
        << ",\"lost_after_observation_sequence\":";
    if (incomplete) out << after;
    else if (lost_after_observation_sequence) out << lost_after_observation_sequence;
    else out << "null";
    out << ",\"reconciliation_required\":" << (incomplete ? "true" : "false")
        << ",\"events\":[";
    int emitted = 0;
    uint64_t last = after;
    for (size_t offset = 0; offset < observation_event_count && emitted < limit; ++offset) {
        const NativeObservationEvent& event = observation_events[
            (observation_event_start + offset) % MaxObservationEvents];
        if (event.sequence <= after) continue;
        if (emitted++) out << ',';
        out << "{\"sequence\":" << event.sequence
            << ",\"kind\":" << json_string(event.kind)
            << ",\"turn\":" << event.turn
            << ",\"subject_a\":" << event.subject_a
            << ",\"subject_b\":" << event.subject_b
            << ",\"from_tile_id\":" << event.from_tile_id
            << ",\"to_tile_id\":" << event.to_tile_id
            << ",\"value_before\":" << event.value_before
            << ",\"value_after\":" << event.value_after
            << ",\"continuous_visibility\":"
            << (event.continuous_visibility ? "true" : "false") << '}';
        last = event.sequence;
    }
    out << "],\"next_sequence\":" << last
        << ",\"has_more\":";
    bool has_more = false;
    if (observation_event_count) {
        const NativeObservationEvent& newest = observation_events[
            (observation_event_start + observation_event_count - 1) % MaxObservationEvents];
        has_more = newest.sequence > last;
    }
    out << (has_more ? "true" : "false") << '}';
    return out.str();
}

std::string validate_semantic_guard(const std::string& request) {
    std::string session = field_string(request, "session_id");
    std::string match = field_string(request, "match_id");
    std::string expected = field_string(request, "expected_revision");
    if (session.empty() || match.empty() || expected.empty()) {
        return error_response("missing_state_guard",
            "Mutating commands require match_id, session_id, and expected_revision from the latest snapshot.");
    }
    if (session != agent_session_id || match != agent_match_id) {
        return std::string("{\"ok\":false,\"error\":{\"code\":\"wrong_game_identity\","
            "\"message\":\"The command targets a different match or process session. Observe again.\"},"
            "\"current_match_id\":") + json_string(agent_match_id.c_str())
            + ",\"current_session_id\":" + json_string(agent_session_id.c_str()) + '}';
    }
    std::string current = semantic_revision();
    if (expected != current) {
        return std::string("{\"ok\":false,\"error\":{\"code\":\"stale_state\","
            "\"message\":\"Fair game state changed after observation; inspect a fresh snapshot and choices.\"},"
            "\"current_revision\":") + json_string(current.c_str()) + '}';
    }
    return "";
}

BasePop* active_default_popup() {
    BasePop* started_popup = agent_popup_object();
    if (reinterpret_cast<uintptr_t>(started_popup) >= 0x10000
    && Win_is_visible(reinterpret_cast<Win*>(started_popup))) return started_popup;
    BasePop* popup_a = *DefaultPopupA;
    BasePop* popup_b = *DefaultPopupB;
    if (reinterpret_cast<uintptr_t>(popup_b) >= 0x10000
    && Win_is_visible(reinterpret_cast<Win*>(popup_b))) return popup_b;
    if (reinterpret_cast<uintptr_t>(popup_a) >= 0x10000
    && Win_is_visible(reinterpret_cast<Win*>(popup_a))) return popup_a;
    // Win_get_key_window returns an engine window identifier for some
    // transient NetMsg notifications (commonly the small integer 2), but
    // returns the actual object address for several tracked
    // BasePop notices whose visible bit is not yet set.  Accept only a
    // pointer-shaped value while a BasePop label recorded by mod_BasePop_start
    // is active; never cast a small numeric id.
    uintptr_t key_window = static_cast<uintptr_t>(Win_get_key_window());
    // CouncilWin is a fixed in-image object whose parent fields are not valid
    // in every lifetime phase (notably after the native Hall of Fame path).
    // Comparing the key object is sufficient and avoids recursively inspecting
    // a dormant CouncilWin as if it were currently visible.
    if (key_window == reinterpret_cast<uintptr_t>(CouncilWin)) return NULL;
    if (key_window >= 0x10000 && agent_popup_label()[0]
    && endgame_presentation_phase.empty()
    && (*WinModalState || *PopupDialogState)) {
        return reinterpret_cast<BasePop*>(key_window);
    }
    return NULL;
}

const char* semantic_popup_label() {
    const char* active = agent_popup_label();
    if (active[0]) return active;

    // NetMsg's timed multiplayer warning executes through a transient BasePop
    // object rather than leaving the object passed to mod_BasePop_start marked
    // visible. Recover its engine script identifier only while that transient
    // object is the actively executing default popup. The narrow lifetime and
    // exact label guard prevent a closed popup from being resurrected over an
    // unrelated modal.
    BasePop* popup = active_default_popup();
    const char* last_started = agent_popup_last_started_label();
    if (popup && *BasePopExecDepth > 0 && popup == *BasePopExecCurrent
    && !strcmp(last_started, "TIMEWARNING")) {
        return last_started;
    }
    return "";
}

ListBox* popup_choice_control(BasePop* popup) {
    if (!popup) return NULL;
    // BasePop::on_button_clicked (terranx.exe 0x60431F..0x604351)
    // selects from the RadioButton group at +0x2228 when mode +0x2350 is 1;
    // otherwise it selects from the ordinary ListBox at +0x21D0. Addressing
    // only the ListBox misses native radio-group selectors.
    return *reinterpret_cast<int*>(reinterpret_cast<char*>(popup) + 0x2350) == 1
        ? reinterpret_cast<ListBox*>(reinterpret_cast<char*>(popup) + 0x2228)
        : reinterpret_cast<ListBox*>(&popup->dialogs);
}

int popup_choice_count(BasePop* popup) {
    if (!popup) return -1;
    char* list = reinterpret_cast<char*>(popup_choice_control(popup));
    uintptr_t vtable = *reinterpret_cast<uintptr_t*>(list);
    if (!vtable) return -1;
    int adjustment = *reinterpret_cast<int*>(vtable + 8);
    // The diplomacy Popup uses a multiple-inheritance ListBox subobject whose
    // vtable adjustment is 0xBA0; ordinary BasePop dialogs commonly use a
    // small adjustment. Both values are supplied by the native vtable.
    if (adjustment < -4096 || adjustment > 4096) return -1;
    int count = *reinterpret_cast<int*>(list + adjustment + 0xcc);
    return count >= 0 && count <= 64 ? count : -1;
}

bool popup_information_only() {
    BasePop* popup = active_default_popup();
    if (!popup || popup_choice_count(popup) != 0) return false;
    int flags = *reinterpret_cast<int*>(reinterpret_cast<char*>(popup) + 0x30a8);
    return !(flags & (PopDialogBtnCancel | PopDialogTextInput));
}

bool narrative_intro_popup(const std::string& label) {
    if (label != "INTRO" || !popup_information_only()) return false;
    int other = *diplo_second_faction;
    // Script.txt reuses INTRO for a faction/scenario presentation and for an
    // AI leader's greeting. Only the latter has a valid non-human diplomatic
    // counterpart. Treating the presentation as diplomacy exposes a command
    // that the multiplayer allowlist correctly refuses and strands the game
    // in its opening modal.
    return other < 1 || other >= MaxPlayerNum || is_human(other);
}

bool close_active_endgame_presentation() {
    if (endgame_presentation_phase.empty()
    || endgame_presentation_phase == "victory_movie"
    || pending_endgame_presentation_advance || active_default_popup()) return false;
    uintptr_t key_window = static_cast<uintptr_t>(Win_get_key_window());
    if (key_window < 0x10000) return false;
    Win* window = reinterpret_cast<Win*>(key_window);
    if (!Win_is_visible(window)) return false;
    uintptr_t vtable = *reinterpret_cast<uintptr_t*>(window);
    HMODULE module = GetModuleHandleA(NULL);
    if (!module) return false;
    PIMAGE_DOS_HEADER dos = reinterpret_cast<PIMAGE_DOS_HEADER>(module);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return false;
    PIMAGE_NT_HEADERS nt = reinterpret_cast<PIMAGE_NT_HEADERS>(
        reinterpret_cast<char*>(module) + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return false;
    uintptr_t image_start = reinterpret_cast<uintptr_t>(module);
    uintptr_t image_end = image_start + nt->OptionalHeader.SizeOfImage;
    if (vtable < image_start || vtable + 0xEC >= image_end) return false;
    typedef void(__thiscall *EndgameWindowClose)(void*);
    EndgameWindowClose close_window = *reinterpret_cast<EndgameWindowClose*>(
        vtable + 0xE8);
    uintptr_t close_address = reinterpret_cast<uintptr_t>(close_window);
    if (close_address < image_start || close_address >= image_end) return false;
    pending_endgame_presentation_advance = true;
    ++endgame_presentation_generation;
    close_window(window);
    return true;
}

void submit_popup_choice(BasePop* popup, int choice_position) {
    // Script replies are represented in two native forms. A non-empty embedded
    // choice control needs its displayed row selected before invoking OK.
    // Button-only X_dialog replies have no choice-control nodes and use the
    // direct reviewed completion path below.
    ListBox* choices = popup_choice_control(popup);
    int choice_count = popup_choice_count(popup);
    if (choice_count > 0) ListBox_set_selected_pos(choices, choice_position, 1);
    begin_popup_transition(popup);
    if (choice_count > 0 || choice_position == 0) {
        BasePop_on_button_clicked(popup, 0);
        return;
    }
    // Button-only script dialogs have no ListBox/RadioButton nodes. Their
    // native click handler writes the button result to BasePop+0xA40 before
    // running the common completion tail. A synthetic call to
    // BasePop_on_button_clicked cannot name that button and resets the result
    // to zero, so reproduce the reviewed tail at 0x60438A..0x6043C2 after
    // writing the exact guarded result. This invokes only native window
    // callbacks; it sends no keyboard, mouse, or coordinate input.
    *reinterpret_cast<int*>(reinterpret_cast<char*>(popup) + 0xA40) = choice_position;
    *reinterpret_cast<int*>(reinterpret_cast<char*>(popup) + 0x3100) = 0;
    typedef void(__thiscall *PopupCompletion)(void*);
    void* callback = *reinterpret_cast<void**>(
        reinterpret_cast<char*>(popup) + 0xA14);
    void* completion_target = callback ? callback : popup;
    uintptr_t completion_vtable = *reinterpret_cast<uintptr_t*>(completion_target);
    PopupCompletion complete = *reinterpret_cast<PopupCompletion*>(
        completion_vtable + 0xE8);
    complete(completion_target);
    int flags = *reinterpret_cast<int*>(reinterpret_cast<char*>(popup) + 0x30A8);
    if (flags & 0x4000) {
        uintptr_t popup_vtable = *reinterpret_cast<uintptr_t*>(popup);
        PopupCompletion dispose = *reinterpret_cast<PopupCompletion*>(popup_vtable + 8);
        dispose(popup);
    }
}

bool popup_has_choice_id(BasePop* popup, int choice_id) {
    if (!popup) return false;
    char* list = reinterpret_cast<char*>(popup_choice_control(popup));
    uintptr_t vtable = *reinterpret_cast<uintptr_t*>(list);
    if (!vtable) return false;
    int adjustment = *reinterpret_cast<int*>(vtable + 8);
    if (adjustment < -4096 || adjustment > 4096) return false;
    int count = *reinterpret_cast<int*>(list + adjustment + 0xcc);
    if (count < 0 || count > 64) return false;
    char* node = *reinterpret_cast<char**>(list + adjustment + 0xc4);
    for (int position = 0; position < count && node; ++position) {
        if (*reinterpret_cast<int*>(node + 4) == choice_id) return true;
        node = *reinterpret_cast<char**>(node + 0xc);
    }
    return false;
}

bool submit_popup_choice_id(BasePop* popup, int choice_id) {
    if (!popup_has_choice_id(popup, choice_id)) return false;
    ListBox* choices = popup_choice_control(popup);
    ListBox_set_selected_id(choices, choice_id);
    begin_popup_transition(popup);
    BasePop_on_button_clicked(popup, 0);
    return true;
}

VOID CALLBACK energy_gift_timer_proc(HWND, UINT, UINT_PTR, DWORD) {
    if (!pending_energy_gift || !game_active()) {
        clear_pending_energy_gift();
        return;
    }
    ++pending_energy_gift_timer_ticks;
    const char* label = agent_popup_last_started_label();
    BasePop* active = active_default_popup();
    if (!active || !label[0]) return;
    if (!strcmp(label, "OFFERENERGY")) {
        // make_gift initializes the text field to the current treasury. The
        // atomic command temporarily narrows that treasury to the requested
        // amount, so accepting the untouched native default preserves all
        // native validation, diplomacy bookkeeping, and network hooks.
        pending_energy_gift_prompt_seen = true;
        begin_popup_transition(active);
        BasePop_on_button_clicked(active, 0);
        return;
    }
    if (!strncmp(label, "GAVEENERGY", 10)) {
        pending_energy_gift_receipt_seen = true;
        begin_popup_transition(active);
        BasePop_on_button_clicked(active, 0);
    }
}

VOID CALLBACK test_energy_gift_menu_timer_proc(HWND, UINT, UINT_PTR, DWORD) {
    if (++test_energy_gift_menu_timer_ticks > 400 || !game_active()) {
        if (test_energy_gift_menu_timer_id) {
            KillTimer(NULL, test_energy_gift_menu_timer_id);
            test_energy_gift_menu_timer_id = 0;
        }
        return;
    }
    if (strcmp(agent_popup_label(), "COUNTER1")) return;
    BasePop* active = active_default_popup();
    if (!active || popup_choice_count(active) != 0) return;
    ListBox* choices = reinterpret_cast<ListBox*>(&active->dialogs);
    ListBox_item(choices, "Cancel", 0);
    ListBox_item(choices, "Energy payment", DiploCounterEnergyPayment);
    ListBox_update_changes(choices);
    KillTimer(NULL, test_energy_gift_menu_timer_id);
    test_energy_gift_menu_timer_id = 0;
}

VOID CALLBACK test_proposal_guard_menu_timer_proc(HWND, UINT, UINT_PTR, DWORD) {
    if (++test_proposal_guard_menu_timer_ticks > 400 || !game_active()) {
        if (test_proposal_guard_menu_timer_id) {
            KillTimer(NULL, test_proposal_guard_menu_timer_id);
            test_proposal_guard_menu_timer_id = 0;
        }
        return;
    }
    if (strcmp(agent_popup_label(), "PROPOSAL")) return;
    BasePop* active = active_default_popup();
    if (!active || popup_choice_count(active) != 0) return;
    ListBox* choices = reinterpret_cast<ListBox*>(&active->dialogs);
    ListBox_item(choices, "Cancel", 0);
    ListBox_item(choices, "Offer trained military units", 12);
    ListBox_update_changes(choices);
    KillTimer(NULL, test_proposal_guard_menu_timer_id);
    test_proposal_guard_menu_timer_id = 0;
}

void append_probe_sabotage_choices(std::ostringstream& out, BasePop* popup) {
    bool comma = false;
    auto append_choice = [&](int target_id, const char* target_kind, const char* name,
    const char* meaning) {
        if (!popup_has_choice_id(popup, target_id)) return;
        if (comma) out << ',';
        comma = true;
        out << "{\"id\":\"probe_sabotage_target:" << target_id
            << "\",\"command\":\"choose_probe_sabotage_target\","
            << "\"sabotage_target_id\":" << target_id
            << ",\"target_kind\":" << json_string(target_kind)
            << ",\"name\":" << json_string(name)
            << ",\"meaning\":" << json_string(meaning) << '}';
    };
    if (active_probe_base_id >= 0 && active_probe_base_id < *BaseCount) {
        append_choice(0, "production",
            production_name(Bases[active_probe_base_id].queue_items[0]).c_str(),
            "Target the base's current production project.");
    }
    for (int facility_id = Fac_ID_First; facility_id <= Fac_ID_Last; ++facility_id) {
        append_choice(facility_id, "facility", Facility[facility_id].name,
            "Target this facility revealed by the native post-entry sabotage menu.");
    }
    append_choice(98, "planet_buster_silos", label_get(1028),
        "Target Planet Buster silos revealed by the native post-entry sabotage menu.");
    append_choice(99, "abort", "Abort mission",
        "Attempt to withdraw the probe team without sabotaging a target.");
    if (!comma) {
        out << "{\"id\":\"probe_sabotage:no_reviewed_targets\","
            "\"kind\":\"capability_status\",\"supported\":false,"
            "\"meaning\":\"No reviewed sabotage target id matched the native menu.\"}";
    }
}

struct NamedDiplomacyOption {
    int id;
    const char* name;
    const char* meaning;
};

struct NamedCouncilProposal {
    int id;
    const char* name;
};

const NamedCouncilProposal CouncilProposals[] = {
    {PROP_ELECT_PLANETARY_GOVERNOR, "elect_planetary_governor"},
    {PROP_UNITE_SUPREME_LEADER, "unite_behind_supreme_leader"},
    {PROP_SALVAGE_UNITY_CORE, "salvage_unity_reactor_core"},
    {PROP_GLOBAL_TRADE_PACT, "enact_global_trade_pact"},
    {PROP_REPEAL_GLOBAL_TRADE_PACT, "repeal_global_trade_pact"},
    {PROP_LAUNCH_SOLAR_SHADE, "launch_solar_shade"},
    {PROP_INCREASE_SOLAR_SHADE, "increase_solar_shade"},
    {PROP_MELT_POLAR_CAPS, "melt_polar_caps"},
    {PROP_REPEAL_UN_CHARTER, "repeal_un_charter"},
    {PROP_REINSTATE_UN_CHARTER, "reinstate_un_charter"},
};

const NamedCouncilProposal* council_proposal(int proposal_id) {
    for (size_t i = 0; i < sizeof(CouncilProposals) / sizeof(CouncilProposals[0]); ++i) {
        if (CouncilProposals[i].id == proposal_id) return &CouncilProposals[i];
    }
    return NULL;
}

void append_council_proposal_choices(std::ostringstream& out, BasePop* popup) {
    bool comma = false;
    for (size_t i = 0; i < sizeof(CouncilProposals) / sizeof(CouncilProposals[0]); ++i) {
        const NamedCouncilProposal& proposal = CouncilProposals[i];
        if (!popup_has_choice_id(popup, proposal.id)) continue;
        if (comma) out << ',';
        comma = true;
        out << "{\"id\":\"council_proposal:" << proposal.id
            << "\",\"command\":\"choose_council_proposal\",\"proposal_id\":"
            << proposal.id << ",\"name\":" << json_string(proposal.name)
            << ",\"display_name\":" << json_string(Proposal[proposal.id].name)
            << ",\"description\":" << json_string(Proposal[proposal.id].description);
        if (proposal.id == PROP_ELECT_PLANETARY_GOVERNOR
        || proposal.id == PROP_UNITE_SUPREME_LEADER) {
            out << ",\"ballot\":{\"type\":\"candidate\",\"candidates\":[";
            bool candidate_comma = false;
            for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
                if (!is_alive(candidate) || is_alien(candidate) || !eligible(candidate)) continue;
                if (candidate_comma) out << ',';
                candidate_comma = true;
                out << "{\"faction_id\":" << candidate << ",\"faction_name\":"
                    << json_string(MFactions[candidate].formal_name_faction)
                    << ",\"leader_name\":" << json_string(MFactions[candidate].name_leader) << '}';
            }
            out << "]}}";
        } else {
            out << ",\"ballot\":{\"type\":\"yea_nay\",\"responses\":[\"yea\",\"nay\"]}}";
        }
    }
    if (!comma) {
        out << "{\"id\":\"council:no_reviewed_proposals\",\"kind\":\"capability_status\","
            "\"supported\":false,\"meaning\":\"The native Council menu exposed no reviewed proposal id.\"}";
    }
}

const NamedDiplomacyOption DiplomacyMenuOptions[] = {
    {0, "finish", "End this diplomatic conversation."},
    {1, "repeat_trade", "Offer another trade under the same terms."},
    {2, "make_proposal", "Open the proposal menu."},
    {3, "make_another_proposal", "Open the proposal menu again."},
    {4, "coordinate_battle_plans", "Coordinate military targets with this faction."},
    {5, "request_end_vendetta", "Ask this faction to end its Vendetta against one of your friends."},
    {6, "request_council_vote", "Seek this faction's vote on a pending Council proposal."},
    {7, "renounce_pact", "End your Pact of Brotherhood with this faction."},
    {8, "demand_withdrawal", "Demand that this faction leave your territory."},
};

const NamedDiplomacyOption ProposalMenuOptions[] = {
    {0, "cancel", "Return without making a proposal."},
    {1, "give_gift", "Offer an energy gift as a token of goodwill."},
    {2, "propose_pact", "Propose a Pact of Brotherhood."},
    {3, "propose_treaty", "Propose a Treaty of Friendship."},
    {4, "request_research", "Request access to research data."},
    {5, "buy_prototype", "Ask to buy prototype information."},
    {13, "request_commlink", "Request another faction's commlink frequency."},
    {6, "request_energy", "Request energy credits."},
    {11, "repay_loan", "Repay an outstanding loan balance."},
    {7, "trade_maps", "Propose an exchange of survey data."},
    {8, "propose_joint_attack", "Propose a joint attack against another faction."},
    {9, "demand_base", "Demand that this faction cede a base."},
};

const NamedDiplomacyOption GiftMenuOptions[] = {
    {0, "cancel", "Return without offering a gift."},
    {6, "loan_payments", "Offer a native schedule of loan payments."},
    {1, "goodwill", "Offer goodwill and friendship."},
    {2, "name_price", "Ask the counterpart to name a price."},
    {3, "threaten", "Threaten the counterpart instead of giving a gift."},
    {7, "cancel_pact", "Threaten to cancel the current Pact."},
    {4, "research_data", "Offer one item of research data."},
    {9, "all_research_data", "Offer all owned research data."},
    {5, "energy_payment", "Offer a chosen amount of energy credits."},
    {8, "give_base", "Offer control of one eligible base."},
};

const NamedDiplomacyOption* diplomacy_option(const std::string& label,
const std::string& name) {
    const NamedDiplomacyOption* options = NULL;
    size_t count = 0;
    if (label == "DIPLO") {
        options = DiplomacyMenuOptions;
        count = sizeof(DiplomacyMenuOptions) / sizeof(DiplomacyMenuOptions[0]);
    } else if (label == "PROPOSAL") {
        options = ProposalMenuOptions;
        count = sizeof(ProposalMenuOptions) / sizeof(ProposalMenuOptions[0]);
    } else if (label == "COUNTER1") {
        options = GiftMenuOptions;
        count = sizeof(GiftMenuOptions) / sizeof(GiftMenuOptions[0]);
    }
    for (size_t i = 0; i < count; ++i) {
        if (name == options[i].name) return &options[i];
    }
    return NULL;
}

void append_diplomacy_popup_choices(std::ostringstream& out, BasePop* popup,
const std::string& label) {
    const NamedDiplomacyOption* options = label == "DIPLO" ? DiplomacyMenuOptions
        : label == "PROPOSAL" ? ProposalMenuOptions : GiftMenuOptions;
    size_t count = label == "DIPLO"
        ? sizeof(DiplomacyMenuOptions) / sizeof(DiplomacyMenuOptions[0])
        : label == "PROPOSAL"
            ? sizeof(ProposalMenuOptions) / sizeof(ProposalMenuOptions[0])
            : sizeof(GiftMenuOptions) / sizeof(GiftMenuOptions[0]);
    bool comma = false;
    for (size_t i = 0; i < count; ++i) {
        if (!popup_has_choice_id(popup, options[i].id)) continue;
        if (comma) out << ',';
        comma = true;
        if (label == "COUNTER1" && options[i].id == DiploCounterEnergyPayment) {
            int available = max(0, Factions[*CurrentPlayerFaction].energy_credits);
            out << "{\"id\":\"diplomacy:energy_payment\"," 
                "\"command\":\"give_energy_gift\",\"amount_min\":1,\"amount_max\":"
                << available << ",\"meaning\":" << json_string(options[i].meaning)
                << '}';
            continue;
        }
        out << "{\"id\":\"diplomacy:" << options[i].name
            << "\",\"command\":\"choose_diplomacy_option\",\"option\":"
            << json_string(options[i].name) << ",\"native_option_id\":"
            << options[i].id << ",\"meaning\":" << json_string(options[i].meaning) << '}';
    }
    if (!comma) {
        out << "{\"id\":\"diplomacy:no_native_options\",\"kind\":\"capability_status\","
            << "\"supported\":false,\"meaning\":\"The native menu exposed no reviewed semantic option.\"}";
    }
}

void append_trade_technology_context(std::ostringstream& out, int faction_id,
const std::string& label) {
    int tech_ids[] = {*diplo_tech_id1, *diplo_tech_id2};
    int owned = -1;
    int unowned = -1;
    for (size_t i = 0; i < sizeof(tech_ids) / sizeof(tech_ids[0]); ++i) {
        int tech_id = tech_ids[i];
        if (tech_id < 0 || tech_id >= MaxTechnologyNum) continue;
        if (TechOwners[tech_id] & (1 << faction_id)) owned = tech_id;
        else unowned = tech_id;
    }
    int variant = label[9] - '0';
    out << ",\"terms\":{\"player_gives\":";
    if (variant == 4 || variant == 5) {
        out << "{\"kind\":\"world_map\"}";
    } else if (owned >= 0) {
        out << "{\"kind\":\"technology\",\"tech_id\":" << owned
            << ",\"name\":" << json_string(Tech[owned].name) << '}';
    } else {
        out << "null";
    }
    out << ",\"player_receives\":";
    if (variant == 3 || variant == 5) {
        out << "{\"kind\":\"world_map\"}";
    } else if (unowned >= 0) {
        out << "{\"kind\":\"technology\",\"tech_id\":" << unowned
            << ",\"name\":" << json_string(Tech[unowned].name) << '}';
    } else {
        out << "null";
    }
    out << '}';
}

void append_demand_technology_context(std::ostringstream& out, int faction_id,
const std::string& label) {
    int candidates[4] = {-1, -1, -1, -1};
    int count = demanded_technology_ids(label, candidates);
    out << ",\"player_gives\":[";
    bool comma = false;
    for (int i = 0; i < count; ++i) {
        int tech_id = candidates[i];
        if (tech_id < 0 || tech_id >= MaxTechnologyNum
        || !(TechOwners[tech_id] & (1 << faction_id))) continue;
        if (comma) out << ',';
        comma = true;
        out << "{\"kind\":\"technology\",\"tech_id\":" << tech_id
            << ",\"name\":" << json_string(Tech[tech_id].name) << '}';
    }
    out << "],\"demanded_count\":" << count
        << ",\"context_complete\":"
        << (demanded_technology_context_valid(label, faction_id) ? "true" : "false");
}

int fair_current_vehicle(int faction_id) {
    int veh_id = *CurrentVehID;
    if (veh_id < 0 || veh_id >= *VehCount) return -1;
    VEH& veh = Vehs[veh_id];
    return (veh.faction_id == faction_id || (veh.visibility & (1 << faction_id))) ? veh_id : -1;
}

std::string status_response() {
    int faction_id = 0;
    const bool has_faction = active_faction(faction_id);
    const bool in_game = game_active();
    std::ostringstream out;
    out << "{\"ok\":true,\"bridge\":{\"name\":\"smacx-agent\",\"version\":2}"
        << ",\"identity\":{\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str()) << '}'
        << ",\"state\":{\"screen\":" << json_string(in_game ? "game" : "menu")
        << ",\"in_game\":" << (in_game ? "true" : "false")
        << ",\"game_halted\":" << (*GameHalted ? "true" : "false")
        << ",\"multiplayer_active\":" << (*MultiplayerActive ? "true" : "false")
        << ",\"modal\":" << ((*WinModalState || *PopupDialogState) ? "true" : "false")
        << ",\"turn\":" << (in_game ? *CurrentTurn : -1)
        << ",\"current_vehicle_id\":" << (in_game ? fair_current_vehicle(faction_id) : -1)
        << ",\"faction_id\":" << (has_faction ? faction_id : -1) << "},"
        << "\"multiplayer_semantics\":{\"policy\":\"fail_closed_allowlist\","
        << "\"gameplay_commands_validated\":[\"see semantic_snapshot.multiplayer_semantics.validated_commands\"],"
        << "\"gameplay_mutations_allowed\":" << (in_game ? "true" : "false") << ','
        << "\"reason\":\"Validated semantic commands are available when their fresh decision-frame choices permit them; unlisted command families remain fail-closed. This is a static capability policy, not a state-synchronization error.\"}}";
    return out.str();
}

std::string human_ui_state_response() {
    if (!managed_human_controller) {
        return error_response("human_ui_state_unavailable",
            "Native MENU state is private to a managed human worker.");
    }
    Menu* menu = MapWin ? &MapWin->oMainMenu : NULL;
    int visible_submenus = 0;
    int visible_submenu_index = -1;
    if (menu) {
        int count = std::max(0, std::min(menu->iBaseMenuItemCount, 15));
        for (int index = 0; index < count; ++index) {
            CMenu* submenu = menu->aMainMenuItems[index].poSubMenu;
            if (submenu && Win_is_visible(reinterpret_cast<Win*>(&submenu->field_0))) {
                ++visible_submenus;
                visible_submenu_index = index;
            }
        }
    }
    bool in_game = game_active();
    bool modal = *WinModalState || *PopupDialogState
        || (DiploWin && Win_is_visible(reinterpret_cast<Win*>(DiploWin)));
    // The top-level MENU list is the main buffered menu window itself; its
    // child CMenu windows appear only after the player selects GAME, MAP,
    // ACTION, and so on.  Therefore the safe rail state is the visible main
    // menu with no child submenu and no modal/native page on top of it.
    bool native_menu_visible = menu
        && Win_is_visible(reinterpret_cast<Win*>(&menu->oWinBuffed));
    bool native_page_open = in_game && MapWin && !modal
        && visible_submenus == 0
        && Win_get_key_window()
            != static_cast<int>(reinterpret_cast<intptr_t>(&MapWin->oMainWin.field_4));
    bool root_menu_open = in_game && native_menu_visible
        && visible_submenus == 0 && !modal && !native_page_open;
    const char* popup_label = semantic_popup_label();
    std::ostringstream out;
    out << "{\"ok\":true,\"schema\":\"smacx.human-ui.v1\""
        << ",\"controller_kind\":\"human\""
        << ",\"lifecycle\":" << json_string(in_game ? "game" : "menu")
        << ",\"root_menu_open\":" << (root_menu_open ? "true" : "false")
        << ",\"native_menu_visible\":"
        << (native_menu_visible ? "true" : "false")
        << ",\"visible_submenu_count\":" << visible_submenus
        << ",\"visible_submenu_index\":" << visible_submenu_index
        << ",\"selected_hitbox_tag\":" << (menu ? menu->iHitBoxTagClicked : -1)
        << ",\"modal_open\":" << (modal ? "true" : "false")
        << ",\"popup_label\":" << json_string(popup_label)
        << ",\"lifecycle_intent\":"
        << json_string(!strcmp(popup_label, "REALLYQUIT")
            ? "prevent_native_quit" : "none")
        << ",\"native_page_open\":"
        << (native_page_open ? "true" : "false")
        << ",\"rail_allowed\":" << (root_menu_open ? "true" : "false")
        << '}';
    return out.str();
}

std::string human_ui_control_response(const std::string& request) {
    if (!managed_human_controller) {
        return error_response("human_ui_control_unavailable",
            "Native human controls are private to a managed human worker.");
    }
    std::string action = field_string(request, "action");
    if (action != "cancel_native_quit") {
        return error_response("invalid_human_ui_action",
            "Only cancel_native_quit is available through this guarded portal control.");
    }
    if (strcmp(semantic_popup_label(), "REALLYQUIT")) {
        return error_response("native_quit_not_active",
            "The native quit confirmation is not active.");
    }
    BasePop* popup = active_default_popup();
    if (!popup) {
        return error_response("popup_unavailable",
            "The native quit confirmation object is unavailable.");
    }
    // REALLYQUIT choice zero is the native 'Oops, no, wait!' path. Submit the
    // engine choice directly so the synchronous modal stack unwinds correctly.
    submit_popup_choice(popup, 0);
    return "{\"ok\":true,\"action\":\"cancel_native_quit\","
        "\"popup_label\":\"REALLYQUIT\",\"prevented\":true}";
}

bool valid_chat_identifier(const std::string& value) {
    if (value.empty() || value.size() > 64) return false;
    for (size_t i = 0; i < value.size(); ++i) {
        unsigned char c = static_cast<unsigned char>(value[i]);
        if (!isalnum(c) && c != '-' && c != '_' && c != '.') return false;
    }
    return true;
}

bool valid_chat_text(const std::string& value) {
    if (value.empty() || value.size() > 240) return false;
    for (size_t i = 0; i < value.size(); ++i) {
        unsigned char c = static_cast<unsigned char>(value[i]);
        if (c < 0x20 || c > 0x7e) return false;
    }
    return true;
}

int lan_player_count();
int lan_local_player_index();
int lan_host_player_index();
const char* lan_player_name(int index);
uint32_t lan_player_id(int index);
int lan_player_faction(int index);
int lan_player_index_for_faction(int faction_id);

int pump_native_network_packets(int limit = 64) {
    int processed = 0;
    if (game_active() && *MultiplayerActive) {
        while (processed < limit && NetDaemon_receive(NetState)) ++processed;
    } else if (*MultiplayerActive) {
        // The stock lobby is cooperatively polled too.  Processing its native
        // DirectPlay queue here makes participant/config revisions converge
        // even when no coordinate-oriented lobby control is being exercised.
        while (processed < limit && NetDaemon_receive(NetState)) ++processed;
    }
    return processed;
}

std::string semantic_chat_response(const std::string& request) {
    std::string action = field_string(request, "action");
    if (action != "list" && action != "send") {
        return error_response("bad_chat_action", "Supported chat actions are list and send.");
    }
    if (action == "send") {
        std::string match = field_string(request, "match_id");
        std::string session = field_string(request, "session_id");
        if (match.empty() || session.empty()) {
            return error_response("missing_chat_identity",
                "Sending chat requires the match_id and session_id returned by smac_chat(list).");
        }
        if (match != agent_match_id || session != agent_session_id) {
            return error_response("wrong_game_identity",
                "The chat send targets a different match or process session. List chat again.");
        }
        if (!game_active() || !*MultiplayerActive) {
            return error_response("multiplayer_chat_unavailable",
                "Native chat can be sent only inside a genuinely active multiplayer game.");
        }
        std::string client_message_id = field_string(request, "client_message_id");
        if (!valid_chat_identifier(client_message_id)) {
            return error_response("invalid_client_message_id",
                "Use a unique 1 through 64 character ASCII letter, digit, dot, hyphen, or underscore id.");
        }
        const ChatEvent* duplicate = find_outbound_chat(client_message_id);
        if (duplicate) {
            std::ostringstream repeated;
            repeated << "{\"ok\":true,\"duplicate\":true,\"sent\":false,\"event\":";
            append_chat_event_json(repeated, *duplicate);
            repeated << '}';
            return repeated.str();
        }
        std::string text = field_string(request, "text");
        if (!valid_chat_text(text)) {
            return error_response("invalid_chat_text",
                "Chat text must contain 1 through 240 printable ASCII characters.");
        }
        int sender = *CurrentPlayerFaction;
        int recipient = field_int(request, "recipient_faction_id", 0);
        int sender_player_index = lan_player_index_for_faction(sender);
        if (sender < 1 || sender >= MaxPlayerNum
        || sender_player_index < 1) {
            return error_response("local_player_not_connected",
                "The current faction has no DirectPlay participant record in this match.");
        }
        bool broadcast = recipient == 0;
        if (!broadcast && (recipient < 1 || recipient >= MaxPlayerNum
        || recipient == sender || lan_player_index_for_faction(recipient) < 1
        || !has_treaty(sender, recipient, DIPLO_COMMLINK))) {
            return error_response("chat_recipient_unavailable",
                "Choose broadcast recipient_faction_id=0 or a contacted DirectPlay participant returned as private_eligible by smac_chat(list).");
        }
        int native_send_result = message_chat(
            broadcast ? 0x4E00 : 0x1E00, recipient, &text[0]);
        if (native_send_result <= 0) {
            return error_response("native_chat_send_failed",
                "DirectPlay did not queue the native chat packet. List chat again before retrying.");
        }
        append_chat_event(true, broadcast, sender,
            broadcast ? -1 : recipient, text.c_str(), client_message_id.c_str());
        const ChatEvent& event = chat_events[chat_event_count - 1];
        std::ostringstream sent;
        sent << "{\"ok\":true,\"duplicate\":false,\"sent\":true"
            << ",\"native_send_result\":" << native_send_result
            << ",\"target_player_id\":" << (broadcast ? 0
                : AlphaNet_who_2_pid(NetState, recipient))
            << ",\"event\":";
        append_chat_event_json(sent, event);
        sent << '}';
        return sent.str();
    }

    bool active_multiplayer = game_active() && *MultiplayerActive;
    // SMAC's DirectPlay integration is cooperatively polled.  The stock UI
    // drains this queue from selected turn/input paths; a nonvisual observer
    // must do the same or packets can remain queued despite a native send.
    int received_packets_processed = active_multiplayer
        && agent_modal_service_depth == 0
        ? pump_native_network_packets() : 0;
    uint64_t after_sequence = static_cast<uint64_t>(
        std::max(0, field_int(request, "after_sequence", 0)));
    int local_faction = game_active() ? *CurrentPlayerFaction : -1;
    std::ostringstream out;
    out << "{\"ok\":true,\"identity\":{\"match_id\":"
        << json_string(agent_match_id.c_str()) << ",\"session_id\":"
        << json_string(agent_session_id.c_str()) << "},\"multiplayer_active\":"
        << (active_multiplayer ? "true" : "false")
        << ",\"can_send\":" << (active_multiplayer ? "true" : "false")
        << ",\"received_packets_processed\":" << received_packets_processed
        << ",\"local_faction_id\":" << local_faction
        << ",\"latest_sequence\":" << (next_chat_sequence - 1)
        << ",\"transport\":{\"mode\":"
        << (active_multiplayer ? *reinterpret_cast<int*>(0x93E8D0) : -1)
        << ",\"local_player_id\":"
        << (active_multiplayer ? *reinterpret_cast<uint32_t*>(0x93D4F0) : 0)
        << ",\"host_player_id\":"
        << (active_multiplayer ? *reinterpret_cast<uint32_t*>(0x93D4F4) : 0)
        << ",\"active_faction_mask\":"
        << (active_multiplayer ? (*dword_93E960 & 0xff) : 0)
        << ",\"chat_mute_mask\":"
        << (active_multiplayer ? (*reinterpret_cast<uint32_t*>(0x7FFF74) & 0xff) : 0)
        << '}'
        << ",\"participants\":[";
    bool comma = false;
    const int player_count = active_multiplayer ? lan_player_count() : 0;
    for (int player_index = 1; player_index <= player_count; ++player_index) {
        int faction_id = lan_player_faction(player_index);
        if (faction_id < 1 || faction_id >= MaxPlayerNum) continue;
        if (comma) out << ',';
        comma = true;
        char player_name[32] = {};
        memcpy(player_name, lan_player_name(player_index), 31);
        out << "{\"network_player_index\":" << player_index
            << ",\"player_id\":" << lan_player_id(player_index)
            << ",\"player_name\":" << json_string(player_name)
            << ",\"faction_id\":" << faction_id
            << ",\"faction_name\":"
            << json_string(MFactions[faction_id].formal_name_faction)
            << ",\"private_eligible\":"
            << ((faction_id == local_faction
                || (local_faction >= 1 && local_faction < MaxPlayerNum
                    && has_treaty(local_faction, faction_id, DIPLO_COMMLINK)))
                ? "true" : "false")
            << ",\"local\":" << (faction_id == local_faction ? "true" : "false")
            << '}';
    }
    out << "],\"messages\":[";
    comma = false;
    for (size_t i = 0; i < chat_event_count; ++i) {
        if (chat_events[i].sequence <= after_sequence) continue;
        if (comma) out << ',';
        comma = true;
        append_chat_event_json(out, chat_events[i]);
    }
    out << "]}";
    return out.str();
}

std::string guid_identifier(const GUID& guid) {
    char value[48] = {};
    snprintf(value, sizeof(value),
        "%08lx-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        static_cast<unsigned long>(guid.Data1), guid.Data2, guid.Data3,
        guid.Data4[0], guid.Data4[1], guid.Data4[2], guid.Data4[3],
        guid.Data4[4], guid.Data4[5], guid.Data4[6], guid.Data4[7]);
    return value;
}

const int LanPlayerRecordStride = 0x19c;
const int LanSetupRecordStride = 0x17c;

int lan_player_count() {
    return clamp(*reinterpret_cast<int*>(0x93D4F8), 0, 7);
}

int lan_local_player_index() {
    if (!*MultiplayerActive) return -1;
    int player_id = *reinterpret_cast<int*>(0x93D4F0);
    int index = AlphaNet_pid_2_idx(NetState, player_id);
    return index >= 1 && index <= lan_player_count() ? index : -1;
}

int lan_host_player_index() {
    if (!*MultiplayerActive) return -1;
    int player_id = *reinterpret_cast<int*>(0x93D4F4);
    int index = AlphaNet_pid_2_idx(NetState, player_id);
    return index >= 1 && index <= lan_player_count() ? index : -1;
}

const char* lan_player_name(int index) {
    return reinterpret_cast<const char*>(
        0x93D4FC + index * LanPlayerRecordStride);
}

uint32_t lan_player_id(int index) {
    if (index < 1 || index > lan_player_count()) return 0;
    return *reinterpret_cast<uint32_t*>(
        0x93D51C + index * LanPlayerRecordStride);
}

int lan_player_faction(int index) {
    if (index < 1 || index > lan_player_count()) return -1;
    const uint32_t player_id = lan_player_id(index);
    int faction_id = player_id ? AlphaNet_pid_2_who(NetState, player_id) : -1;
    if (faction_id < 1 || faction_id >= MaxPlayerNum) {
        faction_id = static_cast<signed char>(
            *reinterpret_cast<unsigned char*>(
                0x93D520 + index * LanPlayerRecordStride));
    }
    return faction_id >= 1 && faction_id < MaxPlayerNum ? faction_id : -1;
}

int lan_player_index_for_faction(int faction_id) {
    if (!*MultiplayerActive || faction_id < 1 || faction_id >= MaxPlayerNum) {
        return -1;
    }
    const uint32_t player_id = AlphaNet_who_2_pid(NetState, faction_id);
    const int index = player_id ? AlphaNet_pid_2_idx(NetState, player_id) : 0;
    if (index >= 1 && index <= lan_player_count()
    && lan_player_faction(index) == faction_id) {
        return index;
    }
    for (int candidate = 1; candidate <= lan_player_count(); ++candidate) {
        if (lan_player_faction(candidate) == faction_id) return candidate;
    }
    return -1;
}

unsigned char* lan_setup_record(int index) {
    return reinterpret_cast<unsigned char*>(
        0x90DB98 + index * LanSetupRecordStride);
}

unsigned char* lan_faction_choice_records() {
    return NetWin ? *reinterpret_cast<unsigned char**>(
        reinterpret_cast<char*>(NetWin) + 0x772C) : NULL;
}

int lan_faction_choice_count() {
    unsigned char* records = lan_faction_choice_records();
    return records ? static_cast<unsigned char>(records[1]) : 0;
}

bool lan_faction_choice_selectable(int choice_id) {
    unsigned char* records = lan_faction_choice_records();
    int count = lan_faction_choice_count();
    if (!records || choice_id < 0 || choice_id >= count
    || records[choice_id * 0x190 + 9] == 0) {
        return false;
    }
    int game_type = *reinterpret_cast<int*>(0x90E778);
    if (game_type == 0 || game_type == 2 || game_type == 3) {
        for (int index = 1; index <= lan_player_count(); ++index) {
            if (static_cast<signed char>(lan_setup_record(index)[3]) == choice_id) {
                return false;
            }
        }
    }
    return true;
}

int lan_required_faction_choice(int player_index) {
    if (player_index < 1 || player_index > lan_player_count()
    || *reinterpret_cast<int*>(0x90E778) != 3) {
        return -1;
    }
    const int count = lan_faction_choice_count();
    unsigned char* records = lan_faction_choice_records();
    // A loaded campaign reconstructs AlphaNet's player-to-faction binding
    // before anyone claims a selector row. Its faction slot is the selector
    // record id (1..7). Per-player save-token fields are copied before join
    // assignment and are not authoritative for a newly joined client.
    const int faction_id = lan_player_faction(player_index);
    return records && faction_id >= 1 && faction_id < count
        && records[faction_id * 0x190 + 9] != 0 ? faction_id : -1;
}

bool lan_player_ready(int index) {
    return index >= 1 && index <= lan_player_count()
        && lan_setup_record(index)[1] != 0;
}

uint64_t lan_lobby_revision_value() {
    // The lobby can change asynchronously when DirectPlay adds/removes a
    // participant.  Hash native player/config state instead of relying only
    // on a bridge-side mutation counter, so an old command is rejected after
    // a peer joins, leaves, changes readiness, or changes its setup record.
    uint64_t value = 1469598103934665603ULL;
    const int count = lan_player_count();
    const int scalar_values[] = {
        count,
        *reinterpret_cast<int*>(0x93D4F0),
        *reinterpret_cast<int*>(0x93D4F4),
        *reinterpret_cast<int*>(0x93E8C0),
        *reinterpret_cast<int*>(0x90E778),
    };
    for (size_t i = 0; i < sizeof(scalar_values) / sizeof(scalar_values[0]); ++i) {
        uint32_t part = static_cast<uint32_t>(scalar_values[i]);
        for (int byte = 0; byte < 4; ++byte) {
            value ^= (part >> (byte * 8)) & 0xff;
            value *= 1099511628211ULL;
        }
    }
    const unsigned char* settings = reinterpret_cast<unsigned char*>(0x90E8E0);
    for (int pos = 0; pos < 24; ++pos) {
        value ^= settings[pos];
        value *= 1099511628211ULL;
    }
    for (int index = 1; index <= count; ++index) {
        const char* name = lan_player_name(index);
        for (int pos = 0; pos < 31 && name[pos]; ++pos) {
            value ^= static_cast<unsigned char>(name[pos]);
            value *= 1099511628211ULL;
        }
        const unsigned char* setup = lan_setup_record(index);
        for (int pos = 0; pos < 5; ++pos) {
            value ^= setup[pos];
            value *= 1099511628211ULL;
        }
    }
    return value;
}

std::string lan_lobby_revision() {
    char text[24] = {};
    snprintf(text, sizeof(text), "lobby-%016llx",
        static_cast<unsigned long long>(lan_lobby_revision_value()));
    return text;
}

const char* lan_difficulty_name(int id) {
    const char* names[] = {
        "citizen", "specialist", "talent", "librarian",
        "thinker", "transcend", "random"
    };
    return id >= 0 && id < 7 ? names[id] : "unknown";
}

const char* lan_map_size_name(int id) {
    const char* names[] = {"tiny", "small", "standard", "large", "huge"};
    if (id == -1) return "random";
    return id >= 0 && id < 5 ? names[id] : "custom_or_unknown";
}

const char* lan_world_level_name(int id) {
    const char* names[] = {"low", "average", "high"};
    if (id == -1) return "random";
    return id >= 0 && id < 3 ? names[id] : "unknown";
}

const char* lan_time_control_name(int id) {
    const char* names[] = {"none", "tight", "standard", "moderate", "loose", "custom"};
    return id >= 0 && id < 6 ? names[id] : "unknown";
}

struct LanRuleChoice {
    const char* name;
    uint32_t bit;
    bool inverted;
};

const LanRuleChoice lan_rule_choices[] = {
    {"victory_transcendence", RULES_VICTORY_TRANSCENDENCE, false},
    {"victory_conquest", RULES_VICTORY_CONQUEST, false},
    {"victory_diplomatic", RULES_VICTORY_DIPLOMATIC, false},
    {"victory_economic", RULES_VICTORY_ECONOMIC, false},
    {"victory_cooperative", RULES_VICTORY_COOPERATIVE, false},
    {"do_or_die", RULES_DO_OR_DIE, false}, {"look_first", RULES_LOOK_FIRST, false},
    {"tech_stagnation", RULES_TECH_STAGNATION, false},
    {"spoils_of_war", RULES_SPOILS_OF_WAR, false},
    {"blind_research", RULES_BLIND_RESEARCH, false},
    {"intense_rivalry", RULES_INTENSE_RIVALRY, false},
    {"unity_survey", RULES_NO_UNITY_SURVEY, true},
    {"unity_scattering", RULES_NO_UNITY_SCATTERING, true},
    {"random_events", RULES_BELL_CURVE, true},
    {"time_warp", RULES_TIME_WARP, false}, {"ironman", RULES_IRONMAN, false},
};

void append_lan_named_rules(std::ostringstream& out, uint32_t choices) {
    out << '{';
    bool comma = false;
    for (const LanRuleChoice& rule : lan_rule_choices) {
        if (comma) out << ',';
        bool enabled = (choices & rule.bit) != 0;
        if (rule.inverted) enabled = !enabled;
        out << json_string(rule.name) << ':' << (enabled ? "true" : "false");
        comma = true;
    }
    out << '}';
}

const char* lan_game_type_name(int id) {
    const char* names[] = {
        "new_game", "scenario", "multiplayer_scenario", "load"
    };
    return id >= 0 && id < 4 ? names[id] : "unknown";
}

void append_named_game_rules(std::ostringstream& out, uint32_t rules, uint32_t state) {
    out << "{\"victory_transcendence\":"
        << ((rules & RULES_VICTORY_TRANSCENDENCE) ? "true" : "false")
        << ",\"victory_conquest\":" << ((rules & RULES_VICTORY_CONQUEST) ? "true" : "false")
        << ",\"victory_diplomatic\":" << ((rules & RULES_VICTORY_DIPLOMATIC) ? "true" : "false")
        << ",\"victory_economic\":" << ((rules & RULES_VICTORY_ECONOMIC) ? "true" : "false")
        << ",\"victory_cooperative\":" << ((rules & RULES_VICTORY_COOPERATIVE) ? "true" : "false")
        << ",\"do_or_die\":" << ((rules & RULES_DO_OR_DIE) ? "true" : "false")
        << ",\"look_first\":" << ((rules & RULES_LOOK_FIRST) ? "true" : "false")
        << ",\"tech_stagnation\":" << ((rules & RULES_TECH_STAGNATION) ? "true" : "false")
        << ",\"spoils_of_war\":" << ((rules & RULES_SPOILS_OF_WAR) ? "true" : "false")
        << ",\"blind_research\":" << ((rules & RULES_BLIND_RESEARCH) ? "true" : "false")
        << ",\"intense_rivalry\":" << ((rules & RULES_INTENSE_RIVALRY) ? "true" : "false")
        << ",\"unity_survey\":" << ((rules & RULES_NO_UNITY_SURVEY) ? "false" : "true")
        << ",\"unity_scattering\":" << ((rules & RULES_NO_UNITY_SCATTERING) ? "false" : "true")
        << ",\"random_events\":" << ((rules & RULES_BELL_CURVE) ? "false" : "true")
        << ",\"time_warp\":" << ((rules & RULES_TIME_WARP) ? "true" : "false")
        << ",\"ironman\":" << ((rules & RULES_IRONMAN) ? "true" : "false")
        << ",\"random_leader_personalities\":"
        << ((state & STATE_RAND_FAC_LEADER_PERSONALITIES) ? "true" : "false")
        << ",\"random_leader_agendas\":"
        << ((state & STATE_RAND_FAC_LEADER_SOCIAL_AGENDA) ? "true" : "false") << '}';
}

bool native_lan_lobby_active() {
    // Loading a multiplayer checkpoint reconstructs live map/faction data
    // before the stock Multiplayer Setup window is dismissed. game_active()
    // is therefore true behind that lobby. Identify the lobby by its exact
    // native modal owner and halt state instead of guessing from loaded data.
    return *MultiplayerActive && *WinModalState && *GameHalted
        && *ModalStackCurrent == reinterpret_cast<Win*>(NetWin);
}

struct LanProfile {
    const char* id;
    int difficulty;
    int map_size;
};

const LanProfile lan_profiles[] = {
    {"tiny_citizen", 0, 0},
    {"small_easy", 0, 1},
    {"standard_librarian", 3, 2},
    {"large_thinker", 4, 3},
    {"huge_transcend", 5, 4},
};

const LanProfile* lan_profile_named(const std::string& id) {
    for (const LanProfile& profile : lan_profiles) {
        if (id == profile.id) return &profile;
    }
    return nullptr;
}

const LanProfile* lan_active_profile() {
    const signed char* settings = reinterpret_cast<signed char*>(0x90E8E0);
    for (const LanProfile& profile : lan_profiles) {
        if (settings[2] == profile.difficulty && settings[6] == profile.map_size) {
            return &profile;
        }
    }
    return nullptr;
}

void append_lan_settings(std::ostringstream& out) {
    const signed char* settings = reinterpret_cast<signed char*>(0x90E8E0);
    const LanProfile* active_profile = lan_active_profile();
    out << "{\"profile\":"
        << json_string(active_profile ? active_profile->id : "custom")
        << ",\"difficulty\":{\"id\":" << static_cast<int>(settings[2])
        << ",\"name\":" << json_string(lan_difficulty_name(settings[2])) << '}'
        << ",\"map_size\":{\"id\":" << static_cast<int>(settings[6])
        << ",\"name\":" << json_string(lan_map_size_name(settings[6])) << '}'
        << ",\"world\":{\"ocean_coverage\":{\"id\":"
        << static_cast<int>(settings[7]) << ",\"level\":"
        << json_string(lan_world_level_name(settings[7]))
        << "},\"erosive_forces\":{\"id\":" << static_cast<int>(settings[8])
        << ",\"level\":" << json_string(lan_world_level_name(settings[8]))
        << "},\"native_life\":{\"id\":" << static_cast<int>(settings[9])
        << ",\"level\":" << json_string(lan_world_level_name(settings[9]))
        << "},\"cloud_cover\":{\"id\":" << static_cast<int>(settings[10])
        << ",\"level\":" << json_string(lan_world_level_name(settings[10]))
        << "}},\"time_control\":{\"id\":" << static_cast<int>(settings[3])
        << ",\"name\":" << json_string(lan_time_control_name(settings[3])) << '}'
        << ",\"game_rules_mask\":"
        << *reinterpret_cast<const uint32_t*>(settings + 12)
        << ",\"more_rules_mask\":"
        << *reinterpret_cast<const uint32_t*>(settings + 16)
        << ",\"rules\":";
    append_lan_named_rules(out, *reinterpret_cast<const uint32_t*>(settings + 12));
    out << '}';
}

void append_lan_lobby_state(std::ostringstream& out) {
    const int count = lan_player_count();
    const int local_index = lan_local_player_index();
    const int host_index = lan_host_player_index();
    const bool local_host = local_index >= 1 && local_index == host_index;
    const LanProfile* active_profile = lan_active_profile();
    bool every_client_ready = count >= 2;
    for (int index = 1; index <= count; ++index) {
        if (index != host_index && !lan_player_ready(index)) {
            every_client_ready = false;
        }
    }
    out << ",\"lobby\":{\"revision\":"
        << json_string(lan_lobby_revision().c_str())
        << ",\"role\":" << json_string(local_host ? "host" : "client")
        << ",\"game_type\":"
        << json_string(lan_game_type_name(*reinterpret_cast<int*>(0x90E778)))
        << ",\"local_player_index\":" << local_index
        << ",\"host_player_index\":" << host_index
        << ",\"participant_count\":" << count
        << ",\"settings\":";
    append_lan_settings(out);
    out
        << ",\"participants\":[";
    for (int index = 1; index <= count; ++index) {
        if (index > 1) out << ',';
        const unsigned char* setup = lan_setup_record(index);
        char name[32] = {};
        memcpy(name, lan_player_name(index), 31);
        const char* directplay_name = Net_get_player_name(
            NetState, lan_player_id(index));
        int faction_choice = static_cast<signed char>(setup[3]);
        int required_faction_choice = lan_required_faction_choice(index);
        out << "{\"player_index\":" << index
            << ",\"player_id\":" << lan_player_id(index)
            << ",\"name\":" << json_string(name)
            << ",\"directplay_name\":"
            << json_string(directplay_name ? directplay_name : "")
            << ",\"local\":" << (index == local_index ? "true" : "false")
            << ",\"host\":" << (index == host_index ? "true" : "false")
            << ",\"ready\":" << (lan_player_ready(index) ? "true" : "false")
            << ",\"difficulty_id\":" << static_cast<int>(setup[2])
            << ",\"faction_id\":";
        int bound_faction_id = lan_player_faction(index);
        if (bound_faction_id < 1) out << "null";
        else out << bound_faction_id;
        out
            << ",\"faction_choice_id\":";
        if (faction_choice < 0) out << "null";
        else out << faction_choice;
        out << ",\"required_faction_choice_id\":";
        if (required_faction_choice < 0) out << "null";
        else out << required_faction_choice;
        out << '}';
    }
    out << "],\"native_selector_record_ids\":[";
    bool faction_choice_comma = false;
    for (int choice_id = 0; choice_id < lan_faction_choice_count(); ++choice_id) {
        if (!lan_faction_choice_selectable(choice_id)) continue;
        if (faction_choice_comma) out << ',';
        out << choice_id;
        faction_choice_comma = true;
    }
    out << "],\"all_clients_ready\":"
        << (every_client_ready ? "true" : "false")
        << ",\"legal_actions\":[";
    bool any_client_ready = false;
    for (int index = 1; index <= count; ++index) {
        any_client_ready |= index != host_index && lan_player_ready(index);
    }
    if (local_index >= 1 && !local_host) {
        bool action_comma = false;
        int local_choice = static_cast<signed char>(lan_setup_record(local_index)[3]);
        int local_faction = lan_player_faction(local_index);
        int required_choice = lan_required_faction_choice(local_index);
        if ((*reinterpret_cast<int*>(0x90E778) == 0
            || *reinterpret_cast<int*>(0x90E778) == 2
            || *reinterpret_cast<int*>(0x90E778) == 3)
        && !lan_player_ready(local_index)
        && (*reinterpret_cast<int*>(0x90E778) == 2 || local_faction >= 1)
        && local_choice < 0
        && (*reinterpret_cast<int*>(0x90E778) != 3 || required_choice >= 0)) {
            out << "{\"action\":\"select_faction\",\"parameters\":{"
                   "\"faction_choice_id\":{\"type\":\"integer\",\"enum\":[";
            if (*reinterpret_cast<int*>(0x90E778) == 3) {
                out << required_choice;
            } else {
                bool comma = false;
                for (int choice_id = 0; choice_id < lan_faction_choice_count(); ++choice_id) {
                    if (!lan_faction_choice_selectable(choice_id)) continue;
                    if (comma) out << ',';
                    out << choice_id;
                    comma = true;
                }
            }
            out << "]}},\"requires\":[\"match_id\",\"session_id\","
                   "\"expected_lobby_revision\",\"client_operation_id\"]}";
            action_comma = true;
        }
        if (action_comma) out << ',';
        out << "{\"action\":\"set_ready\",\"ready\":"
            << (lan_player_ready(local_index) ? "false" : "true")
            << ",\"requires\":[\"match_id\",\"session_id\","
               "\"expected_lobby_revision\",\"client_operation_id\"]}";
    } else if (local_host) {
        bool action_comma = false;
        if (count == 1
        && *reinterpret_cast<int*>(0x90E778) != 3) {
            out << "{\"action\":\"load_save\","
                   "\"parameters\":{\"slot\":{\"type\":\"string\","
                   "\"pattern\":\"^[A-Za-z0-9_-]{1,32}$\"}},"
                   "\"requires\":[\"match_id\",\"session_id\","
                   "\"expected_lobby_revision\",\"client_operation_id\"]}";
            action_comma = true;
        }
        if (count == 1
        && *reinterpret_cast<int*>(0x90E778) != 2
        && *reinterpret_cast<int*>(0x90E778) != 3) {
            if (action_comma) out << ',';
            out << "{\"action\":\"load_scenario\","
                   "\"parameters\":{\"scenario_id\":{\"type\":\"string\","
                   "\"maxLength\":512}},"
                   "\"requires\":[\"match_id\",\"session_id\","
                   "\"expected_lobby_revision\",\"client_operation_id\"]}";
            action_comma = true;
        }
        int local_choice = local_index >= 1
            ? static_cast<signed char>(lan_setup_record(local_index)[3]) : -1;
        int local_faction = lan_player_faction(local_index);
        int required_choice = lan_required_faction_choice(local_index);
        if ((*reinterpret_cast<int*>(0x90E778) == 0
            || *reinterpret_cast<int*>(0x90E778) == 2
            || *reinterpret_cast<int*>(0x90E778) == 3)
        && !lan_player_ready(local_index)
        && (*reinterpret_cast<int*>(0x90E778) == 2 || local_faction >= 1)
        && local_choice < 0
        && (*reinterpret_cast<int*>(0x90E778) != 3 || required_choice >= 0)) {
            if (action_comma) out << ',';
            out << "{\"action\":\"select_faction\",\"parameters\":{"
                   "\"faction_choice_id\":{\"type\":\"integer\",\"enum\":[";
            if (*reinterpret_cast<int*>(0x90E778) == 3) {
                out << required_choice;
            } else {
                bool comma = false;
                for (int choice_id = 0; choice_id < lan_faction_choice_count(); ++choice_id) {
                    if (!lan_faction_choice_selectable(choice_id)) continue;
                    if (comma) out << ',';
                    out << choice_id;
                    comma = true;
                }
            }
            out << "]}},\"requires\":[\"match_id\",\"session_id\","
                   "\"expected_lobby_revision\",\"client_operation_id\"]}";
            action_comma = true;
        }
        if (!any_client_ready && *reinterpret_cast<int*>(0x90E778) == 0) {
            if (action_comma) out << ',';
            out << "{\"action\":\"configure\",\"parameters\":{\"profile\":{"
                   "\"type\":\"string\",\"enum\":[";
            bool profile_comma = false;
            for (const LanProfile& profile : lan_profiles) {
                if (active_profile && std::string(active_profile->id) == profile.id) continue;
                if (profile_comma) out << ',';
                out << json_string(profile.id);
                profile_comma = true;
            }
            if (profile_comma) out << ',';
            out << "\"custom\"";
            out << "]}},"
                   "\"requires\":[\"match_id\",\"session_id\","
                   "\"expected_lobby_revision\",\"client_operation_id\"]}";
            action_comma = true;
        }
        if (count > 1) {
            if (action_comma) out << ',';
            out << "{\"action\":\"drop_player\",\"parameters\":{"
                   "\"player_index\":{\"type\":\"integer\",\"enum\":[";
            bool player_comma = false;
            for (int index = 1; index <= count; ++index) {
                if (index == host_index) continue;
                if (player_comma) out << ',';
                out << index;
                player_comma = true;
            }
            out << "]},\"expected_player_name\":{\"type\":\"string\","
                   "\"minLength\":1,\"maxLength\":31}},\"requires\":["
                   "\"match_id\",\"session_id\",\"expected_lobby_revision\","
                   "\"client_operation_id\"]}";
            action_comma = true;
        }
        if (every_client_ready) {
            if (action_comma) out << ',';
            out << "{\"action\":\"start\",\"requires\":[\"match_id\","
               "\"session_id\",\"expected_lobby_revision\","
               "\"client_operation_id\"]}";
        }
    }
    out << "]}";
}

struct LanDirectPlaySessionDesc {
    DWORD size;
    DWORD flags;
    GUID instance;
    GUID application;
    DWORD max_players;
    DWORD current_players;
    char* session_name;
    char* password;
    DWORD reserved1;
    DWORD reserved2;
    DWORD user1;
    DWORD user2;
    DWORD user3;
    DWORD user4;
};

struct LanDiscoveredSession {
    LanDirectPlaySessionDesc description;
    GUID instance;
    std::string name;
    int current_players;
    int max_players;
    uint32_t flags;
};

struct LanCompoundAddressElement {
    GUID data_type;
    DWORD data_size;
    void* data;
};

typedef HRESULT(WINAPI *FLanDirectPlayLobbyCreate)(
    GUID*, void**, void*, void*, DWORD);
typedef HRESULT(__stdcall *FLanQueryInterface)(
    void*, const GUID*, void**);
typedef ULONG(__stdcall *FLanRelease)(void*);
typedef HRESULT(__stdcall *FLanCreateCompoundAddress)(
    void*, const LanCompoundAddressElement*, DWORD, void*, DWORD*);
typedef HRESULT(__stdcall *FLanInitializeConnection)(
    void*, void*, DWORD);
typedef HRESULT(__stdcall *FLanGetCaps)(void*, void*, DWORD);

HRESULT lan_release(void* object) {
    if (!object) return 0;
    void** vtable = *reinterpret_cast<void***>(object);
    return static_cast<HRESULT>(
        reinterpret_cast<FLanRelease>(vtable[2])(object));
}

int lan_join_service_at_address(const std::string& host_address) {
    InterlockedExchange(&lan_join_address_stage, 0);
    InterlockedExchange(&lan_join_address_hresult, 0);
    sockaddr_in parsed = {};
    if (InetPtonA(AF_INET, host_address.c_str(), &parsed.sin_addr) != 1) {
        return 16;
    }
    HMODULE dplayx = GetModuleHandleA("dplayx.dll");
    FLanDirectPlayLobbyCreate lobby_create = dplayx
        ? reinterpret_cast<FLanDirectPlayLobbyCreate>(
            GetProcAddress(dplayx, "DirectPlayLobbyCreateA")) : NULL;
    if (!lobby_create) return 7;

    const GUID iid_direct_play_4a = {
        0x0AB1C531, 0x4745, 0x11D1,
        {0xA7, 0xA1, 0x00, 0x00, 0xF8, 0x03, 0xAB, 0xFC}
    };
    const GUID clsid_direct_play = {
        0xD1EB6D20, 0x8923, 0x11D0,
        {0x9D, 0x97, 0x00, 0xA0, 0xC9, 0x0A, 0x43, 0xCB}
    };
    const GUID iid_lobby_2a = {
        0x1BB4AF80, 0xA303, 0x11D0,
        {0x9C, 0x4F, 0x00, 0xA0, 0xC9, 0x05, 0x42, 0x5E}
    };
    const GUID tcp_ip = {
        0x36E95EE0, 0x8577, 0x11CF,
        {0x96, 0x0C, 0x00, 0x80, 0xC7, 0x53, 0x4E, 0x82}
    };
    const GUID address_service_provider = {
        0x07D916C0, 0xE0AF, 0x11CF,
        {0x9C, 0x4E, 0x00, 0xA0, 0xC9, 0x05, 0x42, 0x5E}
    };
    const GUID address_inet = {
        0xC4A54DA0, 0xE0AF, 0x11CF,
        {0x9C, 0x4E, 0x00, 0xA0, 0xC9, 0x05, 0x42, 0x5E}
    };

    void* direct_play_4 = NULL;
    HRESULT result = CoCreateInstance(clsid_direct_play, NULL,
        CLSCTX_INPROC_SERVER, iid_direct_play_4a, &direct_play_4);
    InterlockedExchange(&lan_join_address_stage, 1);
    InterlockedExchange(&lan_join_address_hresult, result);
    InterlockedExchange(&lan_join_address_stage, 2);
    if (result || !direct_play_4) {
        lan_release(direct_play_4);
        return 1;
    }

    void* lobby_1 = NULL;
    void* lobby_2 = NULL;
    result = lobby_create(NULL, &lobby_1, NULL, NULL, 0);
    InterlockedExchange(&lan_join_address_stage, 3);
    InterlockedExchange(&lan_join_address_hresult, result);
    if (!result && lobby_1) {
        void** vtable = *reinterpret_cast<void***>(lobby_1);
        result = reinterpret_cast<FLanQueryInterface>(vtable[0])(
            lobby_1, &iid_lobby_2a, &lobby_2);
        InterlockedExchange(&lan_join_address_stage, 4);
        InterlockedExchange(&lan_join_address_hresult, result);
    }
    lan_release(lobby_1);
    if (result || !lobby_2) {
        lan_release(lobby_2);
        lan_release(direct_play_4);
        return 1;
    }

    LanCompoundAddressElement elements[2] = {};
    elements[0].data_type = address_service_provider;
    elements[0].data_size = sizeof(tcp_ip);
    elements[0].data = const_cast<GUID*>(&tcp_ip);
    elements[1].data_type = address_inet;
    elements[1].data_size = static_cast<DWORD>(host_address.size() + 1);
    elements[1].data = const_cast<char*>(host_address.c_str());
    void** lobby_vtable = *reinterpret_cast<void***>(lobby_2);
    FLanCreateCompoundAddress create_address =
        reinterpret_cast<FLanCreateCompoundAddress>(lobby_vtable[14]);
    DWORD address_size = 0;
    create_address(lobby_2, elements, 2, NULL, &address_size);
    InterlockedExchange(&lan_join_address_stage, 5);
    std::vector<unsigned char> address(address_size);
    result = address_size
        ? create_address(lobby_2, elements, 2, &address[0], &address_size)
        : static_cast<HRESULT>(0x80004005L);
    InterlockedExchange(&lan_join_address_stage, 6);
    InterlockedExchange(&lan_join_address_hresult, result);
    lan_release(lobby_2);
    if (result) {
        lan_release(direct_play_4);
        return 1;
    }

    void** direct_play_vtable = *reinterpret_cast<void***>(direct_play_4);
    result = reinterpret_cast<FLanInitializeConnection>(
        direct_play_vtable[38])(direct_play_4, &address[0], 0);
    InterlockedExchange(&lan_join_address_stage, 7);
    InterlockedExchange(&lan_join_address_hresult, result);
    if (result) {
        lan_release(direct_play_4);
        return 1;
    }

    *reinterpret_cast<void**>(0x9BE600) = direct_play_4;
    *reinterpret_cast<void**>(reinterpret_cast<char*>(NetState) + 0x1c)
        = direct_play_4;
    memcpy(reinterpret_cast<char*>(NetState) + 0x6ec,
        &tcp_ip, sizeof(tcp_ip));
    unsigned char caps[40] = {};
    *reinterpret_cast<DWORD*>(caps) = sizeof(caps);
    result = reinterpret_cast<FLanGetCaps>(direct_play_vtable[14])(
        direct_play_4, caps, 1);
    InterlockedExchange(&lan_join_address_stage, 8);
    InterlockedExchange(&lan_join_address_hresult, result);
    if (result) {
        AlphaNet_close(NetState);
        return 1;
    }
    *reinterpret_cast<DWORD*>(reinterpret_cast<char*>(NetState) + 0xd8)
        &= 0xEFFFFFFF;
    DWORD timeout = *reinterpret_cast<DWORD*>(0x9BE4D8);
    if (!timeout) timeout = 100;
    for (int index = 0; index < 16; ++index) {
        *reinterpret_cast<DWORD*>(reinterpret_cast<char*>(NetState)
            + 0x160 + index * 0x58) = timeout;
    }
    *reinterpret_cast<DWORD*>(reinterpret_cast<char*>(NetState) + 0x6d4)
        = timeout;
    return 0;
}

typedef BOOL(__stdcall *FLanEnumSessionsCallback)(
    const LanDirectPlaySessionDesc*, DWORD*, DWORD, void*);
typedef HRESULT(__stdcall *FLanEnumSessions)(
    void*, LanDirectPlaySessionDesc*, DWORD,
    FLanEnumSessionsCallback, void*, DWORD);

struct LanEnumerationContext {
    LanDiscoveredSession* sessions;
    int capacity;
    int count;
};

BOOL __stdcall agent_lan_enum_session(
const LanDirectPlaySessionDesc* description, DWORD*, DWORD, void* raw_context) {
    LanEnumerationContext* context =
        reinterpret_cast<LanEnumerationContext*>(raw_context);
    if (!description || !context || context->count >= context->capacity) {
        return FALSE;
    }
    LanDiscoveredSession& session = context->sessions[context->count++];
    session.description = *description;
    session.instance = description->instance;
    session.name = description->session_name
        ? description->session_name : "";
    session.flags = description->flags;
    session.max_players = static_cast<int>(description->max_players);
    session.current_players = static_cast<int>(description->current_players);
    return TRUE;
}

std::string semantic_lan_join_response(const std::string& request,
bool test_fixture) {
    std::string action = field_string(request, "action");
    if (action != "discover" && action != "join") {
        return error_response("invalid_lan_join_action",
            "Use action=discover or action=join.");
    }
    if (game_active() || *MultiplayerActive || lan_test_preconnected
    || lan_test_lobby_pending || lan_test_in_progress) {
        return error_response("lan_join_requires_menu",
            "Session discovery and joining are legal only from an idle menu with no network operation in progress.");
    }
    std::string player_name = field_string(request, "player_name");
    std::string requested_session_id = field_string(
        request, "network_session_id");
    std::string host_address = field_string(request, "host_address");
    std::string client_operation_id = field_string(
        request, "client_operation_id");
    if (action == "join"
    && (!valid_chat_text(player_name) || player_name.size() > 31)) {
        return error_response("invalid_lan_player_name",
            "player_name must contain 1 through 31 printable ASCII characters.");
    }
    if (action == "join" && requested_session_id.empty()) {
        return error_response("missing_network_session_id",
            "Join one exact network_session_id returned by a fresh discovery.");
    }
    if (action == "join" && !valid_chat_identifier(client_operation_id)) {
        return error_response("invalid_lan_operation_id",
            "Join requires one unique 1 through 64 character client_operation_id. Reuse it only after an uncertain response.");
    }
    if (!test_fixture && host_address.empty()) {
        return error_response("lan_host_address_required",
            "Public LAN discovery and join require the host's exact IPv4 address; local-network broadcast discovery is not reliable under Proton.");
    }
    if (!host_address.empty()) {
        sockaddr_in parsed = {};
        if (InetPtonA(AF_INET, host_address.c_str(), &parsed.sin_addr) != 1) {
            return error_response("invalid_lan_host_address",
                "host_address must be one exact IPv4 address.");
        }
    }

    InterlockedExchange(&lan_test_in_progress, 1);
    InterlockedExchange(&lan_test_completed_stage, 0);
    typedef int(__thiscall *FNetworkInitialize)(void*, int);
    int network_initialize_status =
        reinterpret_cast<FNetworkInitialize>(0x52DF30)(NetState, 1);
    InterlockedExchange(&lan_test_completed_stage, 1);
    int init_status = -1;
    int service_status = -1;
    int poll_status = -1;
    int join_status = 0;
    LanDiscoveredSession sessions[32] = {};
    int session_count = 0;
    if (!network_initialize_status) {
        init_status = Net_init(
            NetState, reinterpret_cast<GUID*>(0x689218), 3, 9);
        InterlockedExchange(&lan_test_completed_stage, 2);
    }
    if (!network_initialize_status && !init_status) {
        unsigned char service[24] = {};
        const GUID tcp_ip = {
            0x36E95EE0, 0x8577, 0x11CF,
            {0x96, 0x0C, 0x00, 0x80, 0xC7, 0x53, 0x4E, 0x82}
        };
        memcpy(service + 8, &tcp_ip, sizeof(tcp_ip));
        service_status = host_address.empty()
            ? Net_join_service(
                NetState, reinterpret_cast<ServiceStruct*>(service))
            : lan_join_service_at_address(host_address);
        InterlockedExchange(&lan_test_completed_stage, 3);
    }
    if (!network_initialize_status && !init_status && !service_status) {
        LanDirectPlaySessionDesc filter = {};
        filter.size = sizeof(filter);
        GUID* application_guid = *reinterpret_cast<GUID**>(
            reinterpret_cast<char*>(NetState) + 0x6e8);
        if (application_guid) filter.application = *application_guid;
        LanEnumerationContext context = {sessions, 32, 0};
        void* direct_play = *reinterpret_cast<void**>(0x9BE600);
        void** vtable = direct_play
            ? *reinterpret_cast<void***>(direct_play) : NULL;
        // Match Net_on_player_added (0x62E420), the game's own session-list
        // routine: DirectPlay's synchronous AVAILABLE enumeration.  Wine's
        // legacy TCP/IP provider blocks inside the ASYNC call instead of
        // returning and invoking callbacks later, whereas this is the exact
        // mode used by the stock join flow.
        HRESULT enumeration_result = vtable
            ? reinterpret_cast<FLanEnumSessions>(vtable[13])(
                direct_play, &filter, 1000, agent_lan_enum_session,
                &context, 0x1) // AVAILABLE
            : static_cast<HRESULT>(0x80004003);
        InterlockedExchange(&lan_test_completed_stage, 4);
        poll_status = enumeration_result == 0
            ? 0 : static_cast<int>(enumeration_result);
        session_count = context.count;
        if (action == "join" && !poll_status) {
            for (int i = 0; i < session_count; ++i) {
                if (guid_identifier(sessions[i].instance)
                != requested_session_id) continue;
                // The game's session-list adapter stores a 12-byte string
                // object followed by the 16-byte instance GUID.  Net_join_session
                // adds 12 before copying that GUID into DPSESSIONDESC2; passing
                // a raw DPSESSIONDESC2 here would shift every field and make
                // DirectPlay attempt to open a fabricated instance.
                unsigned char native_session[12 + sizeof(GUID)] = {};
                memcpy(native_session + 12, &sessions[i].instance,
                    sizeof(sessions[i].instance));
                join_status = Net_join_session(
                    NetState,
                    reinterpret_cast<SessionStruct*>(
                        native_session),
                    &player_name[0], NULL);
                InterlockedExchange(&lan_test_completed_stage, 5);
                break;
            }
        }
    }

    bool joined = action == "join" && join_status;
    if (joined) {
        GUID network_guid = *reinterpret_cast<GUID*>(
            reinterpret_cast<char*>(NetState) + 0x70c);
        lan_network_session_id = guid_identifier(network_guid);
        lan_client_operation_id = client_operation_id;
        InterlockedExchange(&lan_test_preconnected, 1);
        InterlockedExchange(&lan_test_lobby_pending, 1);
        PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
    } else {
        AlphaNet_close(NetState);
    }
    InterlockedExchange(&lan_test_in_progress, 0);

    bool setup_succeeded = !network_initialize_status && !init_status
        && !service_status && !poll_status;
    std::ostringstream out;
    out << "{\"ok\":" << (setup_succeeded ? "true" : "false")
        << ",\"action\":" << json_string(action.c_str())
        << ",\"network_initialize_status\":" << network_initialize_status
        << ",\"init_status\":" << init_status
        << ",\"service_status\":" << service_status
        << ",\"address_stage\":" << lan_join_address_stage
        << ",\"address_hresult\":"
        << static_cast<unsigned long>(lan_join_address_hresult)
        << ",\"poll_status\":" << poll_status
        << ",\"sessions\":[";
    for (int i = 0; i < session_count; ++i) {
        if (i) out << ',';
        out << "{\"network_session_id\":"
            << json_string(guid_identifier(sessions[i].instance).c_str())
            << ",\"name\":" << json_string(sessions[i].name.c_str())
            << ",\"current_players\":" << sessions[i].current_players
            << ",\"max_players\":" << sessions[i].max_players
            << ",\"joinable\":"
            << (sessions[i].current_players < sessions[i].max_players
                ? "true" : "false") << '}';
    }
    out << "]";
    if (action == "join") {
        bool found = false;
        for (int i = 0; i < session_count; ++i) {
            if (guid_identifier(sessions[i].instance)
            == requested_session_id) found = true;
        }
        out << ",\"requested_network_session_id\":"
            << json_string(requested_session_id.c_str())
            << ",\"session_found\":" << (found ? "true" : "false")
            << ",\"join_status\":" << join_status
            << ",\"joined\":" << (joined ? "true" : "false")
            << ",\"lobby_launch_queued\":"
            << (joined ? "true" : "false")
            << ",\"identity\":{\"match_id\":"
            << json_string(agent_match_id.c_str()) << ",\"session_id\":"
            << json_string(agent_session_id.c_str())
            << ",\"network_session_id\":"
            << json_string(lan_network_session_id.c_str()) << '}';
    }
    out << '}';
    return out.str();
}

std::string semantic_lan_host_response(const std::string& request,
bool test_fixture) {
    char test_mode[8] = {};
    char test_lan[8] = {};
    if (test_fixture
    && (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode,
        sizeof(test_mode)) || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_LAN_HOST", test_lan,
        sizeof(test_lan)) || strcmp(test_lan, "1"))) {
        return error_response("test_mode_disabled",
            "The contained DirectPlay host fixture is disabled.");
    }
    std::string action = field_string(request, "action");
    if (action == "status") {
        int received_packets_processed = *MultiplayerActive
            && agent_modal_service_depth == 0
            ? pump_native_network_packets() : 0;
        const bool lobby_active = native_lan_lobby_active();
        const char* lifecycle = lobby_active ? "lobby"
            : (game_active() ? "game" : (*MultiplayerActive ? "lobby" : (lan_test_in_progress
                ? "hosting" : (lan_test_lobby_pending
                    ? "starting_lobby" : "menu"))));
        std::ostringstream status;
        status << "{\"ok\":true,\"identity\":{\"match_id\":"
            << json_string(agent_match_id.c_str()) << ",\"session_id\":"
            << json_string(agent_session_id.c_str())
            << ",\"network_session_id\":"
            << json_string(lan_network_session_id.c_str())
            << "},\"lifecycle\":" << json_string(lifecycle)
            << ",\"in_progress\":"
            << (lan_test_in_progress ? "true" : "false")
            << ",\"completed_stage\":" << lan_test_completed_stage
            << ",\"direct_open_hresult\":"
            << static_cast<unsigned long>(lan_test_open_hresult)
            << ",\"preconnected\":"
            << (lan_test_preconnected ? "true" : "false")
            << ",\"lobby_pending\":"
            << (lan_test_lobby_pending ? "true" : "false")
            << ",\"multiplayer_active\":"
            << (*MultiplayerActive ? "true" : "false")
            << ",\"native_window_state\":{\"win_modal\":"
            << (*WinModalState ? "true" : "false")
            << ",\"game_halted\":" << (*GameHalted ? "true" : "false")
            << ",\"popup_modal\":" << (*PopupDialogState ? "true" : "false")
            << ",\"modal_is_net_window\":"
            << (*ModalStackCurrent == reinterpret_cast<Win*>(NetWin)
                ? "true" : "false") << '}'
            << ",\"received_packets_processed\":"
            << received_packets_processed;
        if (lobby_active || (*MultiplayerActive && !game_active())) {
            append_lan_lobby_state(status);
        } else if (*MultiplayerActive && game_active()) {
            const int local_index = lan_local_player_index();
            const int host_index = lan_host_player_index();
            status << ",\"network\":{\"role\":"
                << json_string(local_index >= 1 && local_index == host_index
                    ? "host" : "client")
                << ",\"local_player_index\":" << local_index
                << ",\"host_player_index\":" << host_index
                << ",\"local_faction_choice_id\":";
            int local_faction_choice_id = local_index >= 1
                ? static_cast<signed char>(lan_setup_record(local_index)[3]) : -1;
            if (local_faction_choice_id < 0) status << "null";
            else status << local_faction_choice_id;
            status
                << "},\"game_settings\":{\"difficulty\":{\"id\":"
                << *DiffLevel << ",\"name\":"
                << json_string(lan_difficulty_name(*DiffLevel))
                << "},\"map_width\":" << *MapAreaX
                << ",\"map_height\":" << *MapAreaY
                << ",\"lobby_configuration\":";
            append_lan_settings(status);
            status << '}';
        }
        status << '}';
        return status.str();
    }
    if (action == "join"
    && !lan_client_operation_id.empty()
    && field_string(request, "client_operation_id") == lan_client_operation_id
    && field_string(request, "network_session_id") == lan_network_session_id
    && *MultiplayerActive) {
        return std::string("{\"ok\":true,\"action\":\"join\",\"duplicate\":true,")
            + "\"joined\":true,\"lobby_launch_queued\":false,\"identity\":{\"match_id\":"
            + json_string(agent_match_id.c_str()) + ",\"session_id\":"
            + json_string(agent_session_id.c_str()) + ",\"network_session_id\":"
            + json_string(lan_network_session_id.c_str()) + "}}";
    }
    if (action == "discover" || action == "join") {
        return semantic_lan_join_response(request, test_fixture);
    }
    if (action == "load_save" || action == "load_scenario"
    || action == "select_faction" || action == "drop_player" || action == "configure"
    || action == "set_ready" || action == "start") {
        if (!native_lan_lobby_active() || lan_test_lobby_pending) {
            return error_response("lan_lobby_action_unavailable",
                "Configure, ready, and start are legal only inside the active native Multiplayer Setup lobby.");
        }
        std::string match = field_string(request, "match_id");
        std::string session = field_string(request, "session_id");
        if (match != agent_match_id || session != agent_session_id) {
            return error_response("wrong_game_identity",
                "The lobby action targets a different match or process session. Read smac_lan(status) again.");
        }
        std::string client_operation_id = field_string(
            request, "client_operation_id");
        if (!valid_chat_identifier(client_operation_id)) {
            return error_response("invalid_lan_operation_id",
                "Lobby mutation requires one unique 1 through 64 character client_operation_id. Reuse it only after an uncertain response.");
        }
        if (client_operation_id == lan_lobby_operation_id
        && action == lan_lobby_operation_action) {
            std::ostringstream duplicate;
            duplicate << "{\"ok\":true,\"duplicate\":true,\"action\":"
                << json_string(action.c_str())
                << ",\"identity\":{\"match_id\":"
                << json_string(agent_match_id.c_str()) << ",\"session_id\":"
                << json_string(agent_session_id.c_str())
                << ",\"network_session_id\":"
                << json_string(lan_network_session_id.c_str()) << "}}";
            return duplicate.str();
        }
        std::string expected_revision = field_string(
            request, "expected_lobby_revision");
        std::string current_revision = lan_lobby_revision();
        if (expected_revision.empty() || expected_revision != current_revision) {
            return std::string("{\"ok\":false,\"error\":{\"code\":")
                + json_string(expected_revision.empty()
                    ? "missing_lobby_guard" : "stale_lobby_state")
                + ",\"message\":\"Read smac_lan(status) and copy its current lobby revision before mutating the lobby.\"}"
                + ",\"current_lobby_revision\":"
                + json_string(current_revision.c_str()) + '}';
        }
        const int count = lan_player_count();
        const int local_index = lan_local_player_index();
        const int host_index = lan_host_player_index();
        const bool local_host = local_index >= 1 && local_index == host_index;
        if (local_index < 1 || host_index < 1) {
            return error_response("lan_lobby_identity_unavailable",
                "The native lobby has not assigned stable local and host participants yet.");
        }
        if (action == "drop_player") {
            if (!local_host) {
                return error_response("lan_drop_player_host_only",
                    "Only the native session host can remove a lobby participant.");
            }
            int target_index = field_int(request, "player_index", -1);
            std::string expected_name = field_string(request, "expected_player_name");
            if (target_index < 1 || target_index > count || target_index == host_index) {
                return error_response("invalid_lan_drop_target",
                    "player_index must identify one current non-host lobby participant.");
            }
            if (expected_name != lan_player_name(target_index)) {
                return error_response("stale_lan_drop_target",
                    "The guarded participant name changed; read fresh lobby state before removal.");
            }
        } else if (action == "load_save" || action == "load_scenario") {
            if (!local_host) {
                return error_response("lan_load_host_only",
                    "Only the native session host can load a multiplayer campaign or scenario.");
            }
            if (count != 1) {
                return error_response("lan_load_requires_host_only_lobby",
                    "Load the multiplayer campaign before any clients join its lobby.");
            }
            if (*reinterpret_cast<int*>(0x90E778) == 2
            || *reinterpret_cast<int*>(0x90E778) == 3) {
                return error_response("lan_save_already_loaded",
                    "This native lobby has already loaded a multiplayer campaign or scenario.");
            }
            std::string path;
            if (action == "load_save") {
                std::string slot = field_string(request, "slot");
                if (!safe_path_component(slot, 32)
                || !safe_path_component(agent_match_id, 80)) {
                    return error_response("invalid_save_slot",
                        "Load slots must contain 1 through 32 ASCII letters, digits, hyphens, or underscores.");
                }
                path = agent_save_path(slot);
            } else {
                std::string scenario_id = field_string(request, "scenario_id");
                if (!safe_scenario_id(scenario_id)) {
                    return error_response("invalid_scenario_id",
                        "scenario_id must be one safe relative .SC path returned by the legal-copy scenario catalog.");
                }
                path = agent_scenario_path(scenario_id);
            }
            if (GetFileAttributesA(path.c_str()) == INVALID_FILE_ATTRIBUTES) {
                return error_response(action == "load_save" ? "save_not_found" : "scenario_not_found",
                    action == "load_save"
                        ? "The named multiplayer checkpoint does not exist in this host worker's match-scoped storage."
                        : "The named scenario does not exist in this worker's validated legal game copy.");
            }
        } else if (action == "select_faction") {
            int game_type = *reinterpret_cast<int*>(0x90E778);
            if (game_type != 0 && game_type != 2 && game_type != 3) {
                return error_response("lan_faction_selection_unavailable",
                    "Guarded faction selection is unavailable in this native lobby type.");
            }
            if (lan_player_ready(local_index)) {
                return error_response("lan_faction_selection_requires_unready",
                    "Clear Ready before selecting a multiplayer faction.");
            }
            int faction_choice_id = field_int(request, "faction_choice_id", -1);
            int required_choice_id = lan_required_faction_choice(local_index);
            if (faction_choice_id < 0
            || faction_choice_id >= lan_faction_choice_count()
            || !lan_faction_choice_selectable(faction_choice_id)) {
                return error_response("invalid_lan_faction_choice",
                    "faction_choice_id must identify a currently selectable native lobby record.");
            }
            if (game_type == 3
            && (required_choice_id < 0 || faction_choice_id != required_choice_id)) {
                return error_response("wrong_loaded_faction_choice",
                    "faction_choice_id must match this player's restored native network faction binding.");
            }
            for (int index = 1; index <= count; ++index) {
                int choice = static_cast<signed char>(lan_setup_record(index)[3]);
                if (index != local_index && choice == faction_choice_id) {
                    return error_response("lan_faction_already_selected",
                        "Another lobby participant has already selected that faction.");
                }
            }
        } else if (action == "configure") {
            if (!local_host) {
                return error_response("lan_configure_host_only",
                    "Only the native session host can configure match settings.");
            }
            for (int index = 1; index <= count; ++index) {
                if (index != host_index && lan_player_ready(index)) {
                    return error_response("lan_configure_requires_unready_clients",
                        "Every joined client must be unready before the host changes match settings.");
                }
            }
            if (*reinterpret_cast<int*>(0x90E778) != 0) {
                return error_response("lan_configure_new_game_only",
                    "Scenario and loaded-campaign settings are authoritative and cannot be overwritten in the lobby.");
            }
            std::string profile = field_string(request, "profile");
            const LanProfile* requested_profile = lan_profile_named(profile);
            if (!requested_profile && profile != "custom") {
                return error_response("unsupported_lan_profile",
                    "Choose one guarded profile or custom as returned by the fresh lobby legal_actions.");
            }
            if (profile == "custom") {
                if (request.find("\"random_leader_personalities\"") != std::string::npos
                || request.find("\"random_leader_agendas\"") != std::string::npos) {
                    return error_response("unsupported_custom_lan_rule",
                        "Random leader personality and agenda flags are not part of the synchronized native LAN rules record.");
                }
                const int difficulty = field_int(request, "difficulty", -1);
                const int time_control = field_int(request, "time_control", -1);
                const int world_size = field_int(request, "world_size", -1);
                const int ocean = field_int(request, "ocean_coverage", -1);
                const int erosion = field_int(request, "erosive_forces", -1);
                const int native_life = field_int(request, "native_life", -1);
                const int clouds = field_int(request, "cloud_cover", -1);
                if (difficulty < 0 || difficulty > 5
                || time_control < 0 || time_control > 4
                || world_size < 0 || world_size > 4
                || ocean < 0 || ocean > 2 || erosion < 0 || erosion > 2
                || native_life < 0 || native_life > 2 || clouds < 0 || clouds > 2) {
                    return error_response("invalid_custom_lan_settings",
                        "Custom LAN settings require difficulty 0..5, time_control 0..4, world_size 0..4, and four world levels 0..2.");
                }
                for (const LanRuleChoice& rule : lan_rule_choices) {
                    bool valid = false;
                    field_bool(request, rule.name, false, &valid);
                    if (request.find(std::string("\"") + rule.name + "\"")
                        != std::string::npos && !valid) {
                        return error_response("invalid_custom_lan_rule",
                            "Every supplied named LAN rule must be a boolean.");
                    }
                }
            }
            const LanProfile* current_profile = lan_active_profile();
            if (requested_profile && current_profile && profile == current_profile->id) {
                return error_response("lan_profile_already_active",
                    "The requested guarded profile is already active. Follow the fresh lobby legal_actions.");
            }
        } else if (action == "set_ready") {
            if (local_host) {
                return error_response("lan_ready_client_only",
                    "The host starts the match; only a joining client can set its Ready state.");
            }
            bool desired = field_bool(request, "ready", false);
            bool current = lan_player_ready(local_index);
            if (desired == current) {
                return error_response("lan_ready_already_set",
                    "The local client already has that Ready state. Read the fresh lobby legal_actions.");
            }
        } else {
            if (!local_host) {
                return error_response("lan_start_host_only",
                    "Only the native session host can start the match.");
            }
            if (count < 2) {
                return error_response("lan_start_requires_peer",
                    "This guarded LAN path requires at least one joined client before start.");
            }
            for (int index = 1; index <= count; ++index) {
                if (index != host_index && !lan_player_ready(index)) {
                    return error_response("lan_clients_not_ready",
                        "Every joined client must report ready before the host can start.");
                }
            }
        }

        if (action == "drop_player") {
            int target_index = field_int(request, "player_index", -1);
            std::string expected_name = field_string(request, "expected_player_name");
            uint32_t target_player_id = lan_player_id(target_index);
            // The second native flag is the no-confirmation path. Passing zero
            // opens SMACX's blocking "drop player?" dialog, which cannot be
            // part of a semantic host operation.
            int native_result = target_player_id
                ? Net_drop_player(NetState, target_player_id, 1) : 0;
            if (!native_result) {
                return error_response("native_lan_drop_player_failed",
                    "The native lobby did not remove the guarded participant.");
            }
            pump_native_network_packets();
            lan_lobby_operation_id = client_operation_id;
            lan_lobby_operation_action = action;
            return std::string("{\"ok\":true,\"duplicate\":false,"
                "\"action\":\"drop_player\",\"player_index\":")
                + std::to_string(target_index) + ",\"player_name\":"
                + json_string(expected_name.c_str())
                + ",\"pixels_or_ui_input_used\":false}";
        }

        if (action == "load_save" || action == "load_scenario") {
            std::string source_id = field_string(
                request, action == "load_save" ? "slot" : "scenario_id");
            lan_pending_load_path = action == "load_save"
                ? agent_save_path(source_id) : agent_scenario_path(source_id);
            lan_pending_load_game_type = action == "load_save" ? 3 : 2;
            InterlockedExchange(&lan_pending_load_choice_seen, 0);
            InterlockedExchange(&lan_pending_load_native_status, -1);
            InterlockedExchange(&lan_pending_load_active, 1);
            // Invoke the stock named Type-of-Game handler. The two narrowly
            // patched calls choose Load and supply this exact match-scoped
            // path; all deserialization, faction reconstruction, setup-packet
            // generation, and lobby refresh remain the game's own code.
            typedef void(__thiscall *FLanGameType)(void*);
            reinterpret_cast<FLanGameType>(0x47D300)(
                reinterpret_cast<void*>(0x80A6F8));
            InterlockedExchange(&lan_pending_load_active, 0);
            int choice_seen = lan_pending_load_choice_seen;
            int native_status = lan_pending_load_native_status;
            lan_pending_load_path.clear();
            if (!choice_seen || native_status != SAVE_LOAD_VALID
            || *reinterpret_cast<int*>(0x90E778) != lan_pending_load_game_type) {
                return std::string("{\"ok\":false,\"error\":{\"code\":\"native_lan_load_failed\","
                    "\"message\":\"The stock multiplayer lobby did not accept the named checkpoint.\"},"
                    "\"choice_seen\":") + (choice_seen ? "true" : "false")
                    + ",\"native_status\":" + std::to_string(native_status)
                    + ",\"game_type\":"
                    + json_string(lan_game_type_name(
                        *reinterpret_cast<int*>(0x90E778))) + '}';
            }
            lan_lobby_operation_id = client_operation_id;
            lan_lobby_operation_action = action;
            std::ostringstream loaded;
            loaded << "{\"ok\":true,\"duplicate\":false,"
                << "\"action\":" << json_string(action.c_str()) << ','
                << (action == "load_save" ? "\"slot\":" : "\"scenario_id\":")
                << json_string(source_id.c_str())
                << ",\"native_status\":" << native_status
                << ",\"game_type\":"
                << json_string(lan_game_type_name(lan_pending_load_game_type))
                << ",\"turn\":" << *CurrentTurn
                << ",\"year\":" << game_year(*CurrentTurn)
                << ",\"pixels_or_ui_input_used\":false,\"identity\":{\"match_id\":"
                << json_string(agent_match_id.c_str()) << ",\"session_id\":"
                << json_string(agent_session_id.c_str())
                << ",\"network_session_id\":"
                << json_string(lan_network_session_id.c_str()) << "}}";
            return loaded.str();
        }

        if (action == "select_faction") {
            int faction_choice_id = field_int(request, "faction_choice_id", -1);
            InterlockedExchange(&lan_pending_faction_choice_id, faction_choice_id);
            typedef void(__thiscall *FLanFactionChoice)(void*, int);
            // The guarded call-site hook returns the row that the stock source
            // list assigns to this network-binding-derived record. The native
            // handler then maps and validates it exactly once without opening
            // its private presentation modal.
            InterlockedExchange(&lan_pending_faction_choice_seen, 0);
            InterlockedExchange(&lan_pending_faction_choice_before_validation, -1);
            InterlockedExchange(&lan_pending_faction_validation_result, -1);
            InterlockedExchange(&lan_pending_faction_selector_result, -1);
            InterlockedExchange(&lan_pending_faction_choice_active, 1);
            reinterpret_cast<FLanFactionChoice>(0x47C530)(
                reinterpret_cast<void*>(0x80A6F8), local_index);
            InterlockedExchange(&lan_pending_faction_choice_active, 0);
            int selector_result = static_cast<int>(
                lan_pending_faction_selector_result);
            if (selector_result < 0
            || !lan_pending_faction_choice_seen
            || lan_pending_faction_choice_before_validation != faction_choice_id
            || lan_pending_faction_validation_result == 0) {
                return std::string("{\"ok\":false,\"error\":{\"code\":\"native_lan_faction_selection_failed\","
                    "\"message\":\"The stock Multiplayer Setup faction handler did not accept the restored faction.\"},"
                    "\"requested_faction_choice_id\":") + std::to_string(faction_choice_id)
                    + ",\"observed_before_validation\":"
                    + std::to_string(static_cast<int>(
                        lan_pending_faction_choice_before_validation))
                    + ",\"native_validation_result\":"
                    + std::to_string(static_cast<int>(
                        lan_pending_faction_validation_result)) + '}';
            }
            lan_lobby_operation_id = client_operation_id;
            lan_lobby_operation_action = action;
            return std::string("{\"ok\":true,\"duplicate\":false,\"action\":\"select_faction\","
                "\"faction_choice_id\":") + std::to_string(faction_choice_id)
                + ",\"selector_result\":" + std::to_string(selector_result)
                + ",\"native_handler\":\"NetWindow::choose_faction\","
                  "\"pixels_or_ui_input_used\":false}";
        }

        if (action == "configure") {
            std::string profile_id = field_string(request, "profile");
            const LanProfile* profile = lan_profile_named(profile_id);
            if (!profile && profile_id != "custom") {
                return error_response("unsupported_lan_profile",
                    "The requested guarded profile disappeared before native execution.");
            }
            unsigned char previous_settings[24] = {};
            unsigned char* native_settings =
                reinterpret_cast<unsigned char*>(0x90E8E0);
            memcpy(previous_settings, native_settings, sizeof(previous_settings));
            if (profile) {
                // A named convenience profile changes only difficulty and map size.
                native_settings[2] = static_cast<unsigned char>(profile->difficulty);
                native_settings[6] = static_cast<unsigned char>(profile->map_size);
            } else {
                native_settings[2] = static_cast<unsigned char>(
                    field_int(request, "difficulty", 0));
                native_settings[3] = static_cast<unsigned char>(
                    field_int(request, "time_control", 0));
                native_settings[6] = static_cast<unsigned char>(
                    field_int(request, "world_size", 0));
                native_settings[7] = static_cast<unsigned char>(
                    field_int(request, "ocean_coverage", 1));
                native_settings[8] = static_cast<unsigned char>(
                    field_int(request, "erosive_forces", 1));
                native_settings[9] = static_cast<unsigned char>(
                    field_int(request, "native_life", 1));
                native_settings[10] = static_cast<unsigned char>(
                    field_int(request, "cloud_cover", 1));
                uint32_t& rule_choices = *reinterpret_cast<uint32_t*>(native_settings + 12);
                for (const LanRuleChoice& rule : lan_rule_choices) {
                    bool valid = false;
                    bool enabled = field_bool(request, rule.name, false, &valid);
                    if (!valid) continue;
                    bool set_bit = rule.inverted ? !enabled : enabled;
                    if (set_bit) rule_choices |= rule.bit;
                    else rule_choices &= ~rule.bit;
                }
            }
            struct LanConfigurationPacket {
                uint16_t type;
                uint16_t reserved;
                uint32_t sender;
                uint32_t timestamp;
                uint32_t sequence;
                unsigned char settings[24];
            } packet = {};
            packet.type = 0x2F02;
            memcpy(packet.settings, native_settings, sizeof(packet.settings));
            const uint32_t host_player_id =
                *reinterpret_cast<uint32_t*>(0x93D4F4);
            int native_send_result = Net_send(NetState, &packet,
                sizeof(packet), host_player_id, 1);
            if (native_send_result <= 0) {
                memcpy(native_settings, previous_settings,
                    sizeof(previous_settings));
                return error_response("native_lan_config_send_failed",
                    "DirectPlay did not queue the guarded lobby configuration; the local settings were rolled back.");
            }
            lan_lobby_operation_id = client_operation_id;
            lan_lobby_operation_action = action;
            std::ostringstream configured;
            configured << "{\"ok\":true,\"duplicate\":false,"
                << "\"action\":\"configure\",\"profile\":"
                << json_string(profile ? profile->id : "custom") << ','
                << "\"native_packet_type\":12034,\"native_send_result\":"
                << native_send_result
                << ",\"pixels_or_ui_input_used\":false,\"settings\":";
            append_lan_settings(configured);
            configured << ",\"identity\":{\"match_id\":"
                << json_string(agent_match_id.c_str()) << ",\"session_id\":"
                << json_string(agent_session_id.c_str())
                << ",\"network_session_id\":"
                << json_string(lan_network_session_id.c_str()) << "}}";
            return configured.str();
        }

        // Stock Multiplayer Setup's named Ready/Start action is semantic item
        // 10.  It toggles a client's readiness and sends packet 0x2F04, or on
        // the host validates every peer then sends the complete 0x0F04 setup.
        // Calling this native handler avoids pixels, coordinates, keys, and
        // fabricated state while preserving the game's own network protocol.
        typedef void(__thiscall *FLanLobbyReadyStart)(void*, int);
        reinterpret_cast<FLanLobbyReadyStart>(0x47B450)(
            reinterpret_cast<void*>(0x80A6F8), 10);
        lan_lobby_operation_id = client_operation_id;
        lan_lobby_operation_action = action;
        std::ostringstream accepted;
        accepted << "{\"ok\":true,\"duplicate\":false,\"action\":"
            << json_string(action.c_str())
            << ",\"native_semantic_item_id\":10,\"pixels_or_ui_input_used\":false"
            << ",\"identity\":{\"match_id\":"
            << json_string(agent_match_id.c_str()) << ",\"session_id\":"
            << json_string(agent_session_id.c_str())
            << ",\"network_session_id\":"
            << json_string(lan_network_session_id.c_str()) << "}}";
        return accepted.str();
    }
    if (!test_fixture && action != "host") {
        return error_response("invalid_lan_action",
            "Use action=status, host, discover, join, load_save, select_faction, drop_player, configure, set_ready, or start as permitted by the current legal_actions.");
    }
    std::string client_operation_id = field_string(
        request, "client_operation_id");
    if (!test_fixture && !valid_chat_identifier(client_operation_id)) {
        return error_response("invalid_client_operation_id",
            "client_operation_id must contain 1 through 64 ASCII letters, digits, dots, hyphens, or underscores and must be reused after an uncertain response.");
    }
    if (!test_fixture && !lan_client_operation_id.empty()
    && client_operation_id == lan_client_operation_id
    && (*MultiplayerActive || lan_test_preconnected
        || lan_test_lobby_pending || game_active())) {
        std::ostringstream duplicate;
        duplicate << "{\"ok\":true,\"duplicate\":true,\"identity\":{\"match_id\":"
            << json_string(agent_match_id.c_str()) << ",\"session_id\":"
            << json_string(agent_session_id.c_str())
            << ",\"network_session_id\":"
            << json_string(lan_network_session_id.c_str())
            << "},\"lobby_launch_queued\":true}";
        return duplicate.str();
    }
    if (game_active() || *MultiplayerActive) {
        return error_response("lan_host_requires_menu",
            "Hosting is legal only from an inactive menu. Read semantic_lan(status) before retrying.");
    }
    std::string session_name = field_string(request, "session_name");
    std::string player_name = field_string(request, "player_name");
    int max_stage = test_fixture ? field_int(request, "max_stage", 5) : 5;
    bool direct_open_diagnostic = test_fixture && field_bool(
        request, "direct_open_diagnostic", false);
    bool launch_lobby = !test_fixture
        || field_bool(request, "launch_lobby", false);
    if (!valid_chat_text(session_name) || session_name.size() > 31
    || !valid_chat_text(player_name) || player_name.size() > 31) {
        return error_response("invalid_lan_fixture_names",
            "Session and player names must contain 1 through 31 printable ASCII characters.");
    }
    if (max_stage < 1 || max_stage > 5) {
        return error_response("invalid_lan_fixture_stage",
            "max_stage must be 1 (setup), 2 (init), 3 (service), 4 (create), or 5 (join).");
    }
    if (launch_lobby && max_stage != 5) {
        return error_response("lan_lobby_requires_join",
            "The contained lobby launch requires max_stage=5.");
    }

    InterlockedExchange(&lan_test_in_progress, 1);
    InterlockedExchange(&lan_test_completed_stage, 0);
    InterlockedExchange(&lan_test_open_hresult, 0);
    typedef int(__thiscall *FNetworkInitialize)(void*, int);
    int network_initialize_status = -1;
    int setup_status = 0;
    if (launch_lobby) {
        network_initialize_status =
            reinterpret_cast<FNetworkInitialize>(0x52DF30)(NetState, 1);
        setup_status = network_initialize_status;
    } else {
        setup_status = AlphaNet_setup(NetState, 1);
    }
    int init_status = -1;
    int service_status = -1;
    int session_pointer = 0;
    int join_status = 0;
    int completed_stage = 1;
    InterlockedExchange(&lan_test_completed_stage, completed_stage);
    if (!setup_status && max_stage >= 2) {
        init_status = Net_init(
            NetState, reinterpret_cast<GUID*>(0x689218), 3, 9);
        completed_stage = 2;
        InterlockedExchange(&lan_test_completed_stage, completed_stage);
    }
    if (!setup_status && !init_status && max_stage >= 3) {
        unsigned char service[24] = {};
        // DPSPGUID_TCPIP = 36E95EE0-8577-11CF-960C-0080C7534E82.
        const GUID tcp_ip = {
            0x36E95EE0, 0x8577, 0x11CF,
            {0x96, 0x0C, 0x00, 0x80, 0xC7, 0x53, 0x4E, 0x82}
        };
        memcpy(service + 8, &tcp_ip, sizeof(tcp_ip));
        service_status = Net_join_service(
            NetState, reinterpret_cast<ServiceStruct*>(service));
        completed_stage = 3;
        InterlockedExchange(&lan_test_completed_stage, completed_stage);
        if (!service_status && max_stage >= 4) {
            if (direct_open_diagnostic) {
                struct DirectPlaySessionDesc {
                    DWORD size;
                    DWORD flags;
                    GUID instance;
                    GUID application;
                    DWORD max_players;
                    DWORD current_players;
                    char* session_name;
                    char* password;
                    DWORD reserved1;
                    DWORD reserved2;
                    DWORD user1;
                    DWORD user2;
                    DWORD user3;
                    DWORD user4;
                } desc = {};
                desc.size = sizeof(desc);
                desc.flags = 4;
                desc.application = *reinterpret_cast<GUID*>(
                    reinterpret_cast<char*>(NetState) + 0x6e8);
                desc.max_players = 7;
                desc.session_name = &session_name[0];
                desc.user1 = *reinterpret_cast<DWORD*>(
                    reinterpret_cast<char*>(NetState) + 0x44);
                void* direct_play = *reinterpret_cast<void**>(0x9BE600);
                void** vtable = direct_play
                    ? *reinterpret_cast<void***>(direct_play) : NULL;
                typedef HRESULT(__stdcall *DirectPlayOpen)(
                    void*, DirectPlaySessionDesc*, DWORD);
                HRESULT open_hresult = vtable
                    ? reinterpret_cast<DirectPlayOpen>(vtable[24])(
                        direct_play, &desc, 2)
                    : static_cast<HRESULT>(0x80004003);
                InterlockedExchange(&lan_test_open_hresult,
                    static_cast<LONG>(open_hresult));
                session_pointer = open_hresult == 0 ? 1 : 0;
            } else {
                session_pointer = Net_create_session(
                    NetState, &session_name[0], 7, NULL);
            }
            completed_stage = 4;
            InterlockedExchange(&lan_test_completed_stage, completed_stage);
            if (session_pointer && max_stage >= 5) {
                join_status = Net_join_session(
                    NetState, reinterpret_cast<SessionStruct*>(session_pointer),
                    &player_name[0], NULL);
                completed_stage = 5;
                InterlockedExchange(&lan_test_completed_stage, completed_stage);
            }
        }
    }
    int native_player_id = *reinterpret_cast<int*>(0x93D4F0);
    bool lobby_launch_queued = launch_lobby && join_status;
    if (lobby_launch_queued) {
        GUID network_guid = *reinterpret_cast<GUID*>(
            reinterpret_cast<char*>(NetState) + 0x70c);
        lan_network_session_id = guid_identifier(network_guid);
        if (!test_fixture) lan_client_operation_id = client_operation_id;
        InterlockedExchange(&lan_test_preconnected, 1);
        InterlockedExchange(&lan_test_lobby_pending, 1);
        PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
    } else {
        AlphaNet_close(NetState);
    }
    InterlockedExchange(&lan_test_in_progress, 0);
    bool stage_succeeded = !setup_status
        && (max_stage < 2 || !init_status)
        && (max_stage < 3 || !service_status)
        && (max_stage < 4 || session_pointer)
        && (max_stage < 5 || join_status);
    std::ostringstream out;
    out << "{\"ok\":" << (stage_succeeded ? "true" : "false")
        << ",\"identity\":{\"match_id\":"
        << json_string(agent_match_id.c_str()) << ",\"session_id\":"
        << json_string(agent_session_id.c_str())
        << ",\"network_session_id\":"
        << json_string(lan_network_session_id.c_str()) << "}"
        << ",\"setup_status\":" << setup_status
        << ",\"network_initialize_status\":" << network_initialize_status
        << ",\"init_status\":" << init_status
        << ",\"service_status\":" << service_status
        << ",\"session_created\":" << (session_pointer ? "true" : "false")
        << ",\"join_status\":" << join_status
        << ",\"requested_stage\":" << max_stage
        << ",\"completed_stage\":" << completed_stage
        << ",\"direct_open_hresult\":"
        << static_cast<unsigned long>(lan_test_open_hresult)
        << ",\"native_player_id\":" << native_player_id
        << ",\"lobby_launch_queued\":"
        << (lobby_launch_queued ? "true" : "false")
        << ",\"closed_after_fixture\":"
        << (lobby_launch_queued ? "false" : "true") << '}';
    return out.str();
}

std::string observe_response() {
    if (!game_active()) return status_response();
    int faction_id = *CurrentPlayerFaction;
    Faction& faction = Factions[faction_id];
    int own_bases = 0;
    int own_units = 0;
    for (int i = 0; i < *BaseCount; ++i) if (Bases[i].faction_id == faction_id) ++own_bases;
    for (int i = 0; i < *VehCount; ++i) if (Vehs[i].faction_id == faction_id) ++own_units;
    std::ostringstream out;
    int current_vehicle_id = fair_current_vehicle(faction_id);
    bool can_act = *CurrentFaction == faction_id && !*WinModalState && !*PopupDialogState;
    out << "{\"ok\":true,\"observation\":{\"screen\":\"game\",\"turn\":" << *CurrentTurn
        << ",\"faction\":{\"id\":" << faction_id
        << ",\"name\":" << json_string(MFactions[faction_id].formal_name_faction)
        << ",\"energy_credits\":" << faction.energy_credits
        << ",\"bases\":" << own_bases << ",\"units\":" << own_units << "}"
        << ",\"ui\":{\"current_vehicle_id\":" << current_vehicle_id
        << ",\"can_act\":" << (can_act ? "true" : "false")
        << ",\"modal\":" << ((*WinModalState || *PopupDialogState) ? "true" : "false")
        << "},\"fair_play\":{\"perspective_faction\":" << faction_id
        << ",\"hidden_engine_state_excluded\":true}}}";
    return out.str();
}

// A match-local map object reference.  The row-major native MAP index is
// stable for the lifetime of a generated/loaded map and lets semantic clients
// select tiles without ever handling native x/y coordinates.
int semantic_tile_id(int x, int y) {
    if (!mapsq(x, y) || !MapAreaX || *MapAreaX <= 0) return -1;
    return (x + *MapAreaX * y) / 2;
}

bool semantic_tile_coords(int tile_id, int* x, int* y) {
    if (!x || !y || !MapAreaX || !MapAreaY
    || *MapAreaX <= 0 || *MapAreaY <= 0 || tile_id < 0
    || tile_id >= (*MapAreaX * *MapAreaY) / 2) return false;
    int row_width = *MapAreaX / 2;
    if (row_width <= 0) return false;
    int ty = tile_id / row_width;
    int tx = 2 * (tile_id % row_width) + (ty & 1);
    if (!mapsq(tx, ty)) return false;
    *x = tx;
    *y = ty;
    return true;
}

bool semantic_request_tile(const std::string& request, int* tile_id, int* x, int* y) {
    int requested = field_int(request, "target_tile_id", -1);
    if (!semantic_tile_coords(requested, x, y)) return false;
    if (tile_id) *tile_id = requested;
    return true;
}

int semantic_road_edge_cost() {
    return conf.magtube_movement_rate > 0 ? conf.road_movement_rate : 1;
}

int semantic_magtube_edge_cost() {
    return conf.magtube_movement_rate > 0 ? 1 : 0;
}

int semantic_fungus_movement_cost(VEH& veh) {
    const int scale = std::max(1, Rules->move_rate_roads);
    const int road = semantic_road_edge_cost();
    const bool xeno = veh.faction_id > 0
        && has_project(FAC_XENOEMPATHY_DOME, veh.faction_id);
    if (veh.triad() == TRIAD_SEA) {
        return (veh.unit_id == BSC_SEALURK || veh.unit_id == BSC_ISLE_OF_THE_DEEP || xeno)
            ? scale : 3 * scale;
    }
    if (veh.triad() != TRIAD_LAND) return scale;
    if (xeno || !veh.faction_id || veh.unit_id == BSC_MIND_WORMS
    || veh.unit_id == BSC_SPORE_LAUNCHER) return road;
    if (Units[veh.unit_id].chassis_id == CHS_HOVERTANK
    || has_abil(veh.unit_id, ABL_ANTIGRAV_STRUTS)) return scale;
    int cost = 3 * scale;
    if (Units[veh.unit_id].plan == PLAN_TERRAFORM
    || Units[veh.unit_id].plan == PLAN_ARTIFACT
    || Factions[veh.faction_id].SE_planet > 0) {
        cost = std::max(scale, proto_speed(veh.unit_id));
    }
    if (conf.fast_fungus_movement > 0) {
        cost = std::min(std::max(proto_speed(veh.unit_id), scale), cost);
    }
    return cost;
}

bool semantic_fungus_connects_to_road(VEH& veh) {
    return veh.faction_id > 0 && has_project(FAC_XENOEMPATHY_DOME, veh.faction_id);
}

bool semantic_ignores_rough_movement(VEH& veh) {
    return veh.triad() == TRIAD_LAND
        && (Units[veh.unit_id].chassis_id == CHS_HOVERTANK
            || has_abil(veh.unit_id, ABL_ANTIGRAV_STRUTS));
}

std::string bases_response() {
    if (!game_active()) return error_response("not_in_game", "Start or load a game first.");
    ensure_test_base_action_fixture();
    ensure_test_base_obliteration_fixture();
    ensure_test_base_status_fixture();
    ensure_test_production_notice_fixture();
    int faction_id = *CurrentPlayerFaction;
    int offset = std::max(0, field_int(pending_request, "offset", 0));
    int limit = std::min(200, std::max(1, field_int(pending_request, "limit", 50)));
    int matched = 0;
    int emitted = 0;
    std::ostringstream out;
    out << "{\"ok\":true,\"kind\":\"bases\",\"items\":[";
    for (int i = 0; i < *BaseCount && emitted < limit; ++i) {
        BASE& base = Bases[i];
        if (base.faction_id != faction_id) continue;
        if (matched++ < offset) continue;
        set_base(i);
        base_compute(1);
        if (emitted++) out << ',';
        out << "{\"id\":" << i << ",\"name\":" << json_string(base.name)
            << ",\"tile_id\":" << semantic_tile_id(base.x, base.y)
            << ",\"is_ocean\":" << (is_ocean(&base) ? "true" : "false")
            << ",\"coastal\":" << (coast_tiles(base.x, base.y) ? "true" : "false")
            << ",\"population\":" << static_cast<int>(base.pop_size)
            << ",\"psi_gate_ready\":"
            << (has_fac_built(FAC_PSI_GATE, i)
                && !(base.state_flags & BSTATE_PSI_GATE_USED) ? "true" : "false")
            << ",\"citizens\":{\"talents\":" << base.talent_total
            << ",\"drones\":" << base.drone_total
            << ",\"superdrones\":" << base.superdrone_total
            << ",\"specialists\":[";
        for (int specialist = 0; specialist < base.specialist_total; ++specialist) {
            if (specialist) out << ',';
            int citizen_id = clamp(base.specialist_type(specialist), 0, MaxSpecialistNum - 1);
            out << "{\"citizen_id\":" << citizen_id << ",\"name\":"
                << json_string(Citizen[citizen_id].singular_name) << '}';
        }
        out << "]},\"nutrients\":{\"intake\":" << base.nutrient_intake_2
            << ",\"consumption\":" << base.nutrient_consumption
            << ",\"surplus\":" << base.nutrient_surplus
            << ",\"accumulated\":" << base.nutrients_accumulated << '}'
            << ",\"minerals\":{\"intake\":" << base.mineral_intake_2
            << ",\"consumption\":" << base.mineral_consumption
            << ",\"unit_support_cost\":" << *BaseForcesMaintCost
            << ",\"surplus\":" << base.mineral_surplus
            << ",\"accumulated\":" << base.minerals_accumulated << '}'
            << ",\"energy\":{\"intake\":" << base.energy_intake_2
            << ",\"surplus\":" << base.energy_surplus
            << ",\"economy\":" << base.economy_total
            << ",\"psych\":" << base.psych_total
            << ",\"labs\":" << base.labs_total << '}'
            << ",\"eco_damage\":" << base.eco_damage
            << ",\"production_id\":" << base.queue_items[0]
            << ",\"production_name\":" << json_string(production_name(base.queue_items[0]).c_str())
            << ",\"production_cost\":" << mineral_cost(i, base.queue_items[0])
            << ",\"production_queue\":[";
        for (int queue_index = 0; queue_index <= base.queue_size && queue_index < 10; ++queue_index) {
            if (queue_index) out << ',';
            int item_id = base.queue_items[queue_index];
            out << "{\"position\":" << queue_index << ",\"item_id\":" << item_id
                << ",\"name\":" << json_string(production_name(item_id).c_str()) << '}';
        }
        out << "],\"governor\":{\"active\":"
            << ((base.governor_flags & GOV_ACTIVE) ? "true" : "false")
            << ",\"manage_citizens\":"
            << ((base.governor_flags & GOV_MANAGE_CITIZENS) ? "true" : "false")
            << ",\"manage_production\":"
            << ((base.governor_flags & GOV_MANAGE_PRODUCTION) ? "true" : "false")
            << ",\"new_units_automated\":"
            << ((base.governor_flags & GOV_NEW_VEH_FULLY_AUTO) ? "true" : "false")
            << ",\"priorities\":{\"explore\":"
            << ((base.governor_flags & GOV_PRIORITY_EXPLORE) ? "true" : "false")
            << ",\"discover\":" << ((base.governor_flags & GOV_PRIORITY_DISCOVER) ? "true" : "false")
            << ",\"build\":" << ((base.governor_flags & GOV_PRIORITY_BUILD) ? "true" : "false")
            << ",\"conquer\":" << ((base.governor_flags & GOV_PRIORITY_CONQUER) ? "true" : "false") << "},\"permissions\":";
        append_governor_permissions(out, base.governor_flags);
        out << '}'
            << ",\"facilities\":[";
        bool facility_comma = false;
        for (int facility_id = Fac_ID_First; facility_id < SP_ID_First; ++facility_id) {
            if (!has_fac_built(static_cast<FacilityId>(facility_id), i)) continue;
            if (facility_comma) out << ',';
            facility_comma = true;
            out << "{\"facility_id\":" << facility_id << ",\"name\":"
                << json_string(Facility[facility_id].name)
                << ",\"maintenance\":" << fac_maint(facility_id, faction_id) << '}';
        }
        out << ']'
            << ",\"base_radius\":[";
        bool radius_comma = false;
        // The owned-base citizen screen is a legitimate report surface for
        // its 21 production squares.  Publish semantic tile references and
        // computed yields, never native coordinates.
        for (int tile_index = 0; tile_index < 21; ++tile_index) {
            int tile_x = 0;
            int tile_y = 0;
            MAP* tile = next_tile(base.x, base.y, tile_index, &tile_x, &tile_y);
            if (!tile || !tile->is_visible(faction_id)) continue;
            if (radius_comma) out << ',';
            radius_comma = true;
            const bool worked = (base.worked_tiles & (1 << tile_index)) != 0;
            out << "{\"location_ref\":\"location-" << semantic_tile_id(tile_x, tile_y)
                << "\",\"worked\":" << (worked ? "true" : "false")
                << ",\"yields\":{\"nutrients\":"
                << mod_crop_yield(faction_id, i, tile_x, tile_y, 0)
                << ",\"minerals\":" << mod_mine_yield(faction_id, i, tile_x, tile_y, 0)
                << ",\"energy\":" << mod_energy_yield(faction_id, i, tile_x, tile_y, 0)
                << "}}";
        }
        out << ']'
            << ",\"drone_riots\":" << (base.drone_riots_active() ? "true" : "false")
            << ",\"facility_recycled_this_turn\":"
            << ((base.state_flags & BSTATE_FACILITY_SCRAPPED) ? "true" : "false")
            << ",\"nerve_stapling\":{\"turns_left\":"
            << static_cast<int>(base.nerve_staple_turns_left)
            << ",\"attempt_count\":" << base.nerve_staple_count << "}}";
    }
    out << "],\"offset\":" << offset << ",\"limit\":" << limit
        << ",\"next_offset\":" << (emitted == limit ? offset + emitted : -1) << '}';
    return out.str();
}

std::string units_response() {
    if (!game_active()) return error_response("not_in_game", "Start or load a game first.");
    ensure_test_artifact_fixture();
    ensure_test_transport_fixture();
    ensure_test_artillery_fixture();
    ensure_test_probe_fixture();
    ensure_test_psi_gate_fixture();
    ensure_test_order_fixture();
    ensure_test_return_home_fixture();
    ensure_test_terrain_destruction_fixture();
    ensure_test_single_unit_upgrade_fixture();
    ensure_test_missile_fixture();
    ensure_test_air_automation_fixture();
    ensure_test_bombing_run_fixture();
    ensure_test_hostility_fixture();
    ensure_test_combat_confirmation_fixture();
    ensure_test_nerve_gas_fixture();
    ensure_test_self_destruct_fixture();
    int faction_id = *CurrentPlayerFaction;
    bool include_visible = field_string(pending_request, "scope") == "visible";
    int offset = std::max(0, field_int(pending_request, "offset", 0));
    int limit = std::min(300, std::max(1, field_int(pending_request, "limit", 100)));
    int matched = 0;
    int emitted = 0;
    std::ostringstream out;
    out << "{\"ok\":true,\"kind\":\"units\",\"scope\":"
        << json_string(include_visible ? "visible" : "own") << ",\"items\":[";
    for (int i = 0; i < *VehCount && emitted < limit; ++i) {
        VEH& veh = Vehs[i];
        bool owned = veh.faction_id == faction_id;
        bool visible = owned || (veh.visibility & (1 << faction_id));
        if (!owned && !(include_visible && visible)) continue;
        if (matched++ < offset) continue;
        if (emitted++) out << ',';
        out << "{\"id\":" << i << ",\"name\":" << json_string(veh.name())
            << ",\"owner\":" << static_cast<int>(veh.faction_id)
            << ",\"tile_id\":" << semantic_tile_id(veh.x, veh.y)
            << ",\"hp\":" << veh.cur_hitpoints() << ",\"max_hp\":" << veh.max_hitpoints();
        if (owned) {
            const char* triad = veh.triad() == TRIAD_LAND ? "land"
                : veh.triad() == TRIAD_SEA ? "sea" : "air";
            int carrier_capacity = semantic_carrier_capacity(i);
            int boarded_on = -1;
            if (veh.order == ORDER_SENTRY_BOARD && veh.waypoint_x[0] >= 0
            && veh.waypoint_x[0] < *VehCount && veh.waypoint_x[0] != i) {
                VEH& transport = Vehs[veh.waypoint_x[0]];
                if (transport.faction_id == faction_id && transport.x == veh.x
                && transport.y == veh.y
                && (veh_cargo(veh.waypoint_x[0]) > 0
                    || semantic_aircraft_boarded_on(i, veh.waypoint_x[0]))) {
                    boarded_on = veh.waypoint_x[0];
                }
            }
            out << ",\"prototype_id\":" << veh.unit_id
                << ",\"triad\":" << json_string(triad)
                << ",\"movement_points\":" << veh_speed(i, 0)
                << ",\"movement_scale\":" << Rules->move_rate_roads
                << ",\"moves_remaining\":" << std::max(0, veh_speed(i, 0) - static_cast<int>(veh.moves_spent))
                << ",\"road_movement_cost\":" << semantic_road_edge_cost()
                << ",\"magtube_movement_cost\":" << semantic_magtube_edge_cost()
                << ",\"fungus_movement_cost\":" << semantic_fungus_movement_cost(veh)
                << ",\"fungus_connects_to_road\":"
                << (semantic_fungus_connects_to_road(veh) ? "true" : "false")
                << ",\"ignores_rough_movement\":"
                << (semantic_ignores_rough_movement(veh) ? "true" : "false")
                << ",\"air_range\":" << static_cast<int>(veh.range())
                << ",\"air_fuel_turns_used\":" << static_cast<int>(veh.movement_turns)
                << ",\"air_safe_range\":" << semantic_air_safe_range(i)
                << ",\"air_full_safe_range\":" << semantic_air_full_safe_range(i)
                << ",\"air_origin_refuels\":"
                << (semantic_friendly_air_refuel_tile(faction_id, veh.x, veh.y)
                    ? "true" : "false")
                << ",\"airdrop_ready\":"
                << (can_airdrop(i, mapsq(veh.x, veh.y)) ? "true" : "false")
                << ",\"airdrop_range\":" << drop_range(faction_id)
                << ",\"abilities\":[";
            bool ability_comma = false;
            struct SemanticAbility { VehAblFlag flag; const char* name; };
            const SemanticAbility semantic_abilities[] = {
                {ABL_CLOAKED, "cloaked"}, {ABL_AMPHIBIOUS, "amphibious"},
                {ABL_DROP_POD, "drop_pod"}, {ABL_ANTIGRAV_STRUTS, "antigrav_struts"},
                {ABL_CLEAN_REACTOR, "clean_reactor"},
                {ABL_FUEL_NANOCELLS, "fuel_nanocells"},
            };
            for (const auto& ability : semantic_abilities) {
                if (!has_abil(veh.unit_id, ability.flag)) continue;
                if (ability_comma) out << ',';
                ability_comma = true;
                out << json_string(ability.name);
            }
            out << ']'
                << ",\"roles\":{\"colony\":" << (veh.is_colony() ? "true" : "false")
                << ",\"former\":" << (veh.is_former() ? "true" : "false")
                << ",\"combat\":" << (veh.is_combat_unit() ? "true" : "false")
                << ",\"probe\":" << (veh.is_probe() ? "true" : "false")
                << ",\"supply\":" << (veh.is_supply() ? "true" : "false")
                << ",\"transport\":" << (veh.is_transport() ? "true" : "false")
                << ",\"carrier\":" << (carrier_capacity > 0 ? "true" : "false")
                << ",\"artillery\":" << (can_arty(veh.unit_id, true) ? "true" : "false")
                << ",\"missile\":" << (veh.is_missile() ? "true" : "false")
                << ",\"missile_kind\":" << json_string(missile_kind(veh))
                << ",\"native_life\":" << (veh.is_native_unit() ? "true" : "false")
                << ",\"wild_native\":" << (veh.is_native_unit() && !veh.faction_id ? "true" : "false")
                << ",\"controlled_native\":" << (veh.is_native_unit() && veh.faction_id ? "true" : "false")
                << ",\"progenitor_force\":" << (!veh.is_native_unit() && is_alien(veh.faction_id) ? "true" : "false")
                << ",\"airdrop_capable\":" << (has_abil(veh.unit_id, ABL_DROP_POD) ? "true" : "false")
                << ",\"airdrop_used\":" << ((veh.state & VSTATE_MADE_AIRDROP) ? "true" : "false")
                << ",\"amphibious\":" << (has_abil(veh.unit_id, ABL_AMPHIBIOUS) ? "true" : "false")
                << ",\"cloaked\":" << (has_abil(veh.unit_id, ABL_CLOAKED) ? "true" : "false")
                << ",\"boarded\":" << (boarded_on >= 0 ? "true" : "false") << '}'
                << ",\"combat_values\":{\"offense\":" << veh.offense_value()
                << ",\"defense\":" << veh.defense_value()
                << ",\"morale_level\":" << static_cast<int>(veh.morale) << '}'
                << ",\"cargo\":{\"capacity\":"
                << (carrier_capacity > 0 ? carrier_capacity : veh_cargo(i))
                << ",\"loaded\":" << veh_cargo_loaded(i);
            if (carrier_capacity > 0) {
                out << ",\"kind\":\"aircraft_carrier\",\"inbound_reserved\":"
                    << semantic_carrier_inbound_count(i)
                    << ",\"unboarded_co_located\":"
                    << semantic_carrier_unboarded_count(i)
                    << ",\"recovery_locked\":"
                    << (semantic_carrier_dependency_count(i) > 0 ? "true" : "false");
            }
            out << '}'
                << ",\"order\":" << static_cast<int>(veh.order)
                << ",\"order_name\":" << json_string(semantic_unit_order_name(veh))
                << ",\"order_auto_type\":" << static_cast<int>(veh.order_auto_type)
                << ",\"moves_spent\":" << static_cast<int>(veh.moves_spent)
                << ",\"home_base_id\":" << veh.home_base_id
                << ",\"requires_support\":"
                << ((veh.state & VSTATE_REQUIRES_SUPPORT) ? "true" : "false")
                << ",\"convoy_resource\":";
            if (veh.order == ORDER_CONVOY) {
                out << json_string(veh.order_auto_type == RSC_NUTRIENT ? "nutrients"
                    : veh.order_auto_type == RSC_MINERAL ? "minerals"
                    : veh.order_auto_type == RSC_ENERGY ? "energy" : "unknown");
            } else out << "null";
            out
                << ",\"convoy_amount\":"
                << (veh.order == ORDER_CONVOY
                    ? contribution(i, veh.order_auto_type) : 0)
                << ",\"transport_unit_id\":" << boarded_on
                << ",\"designated_defender\":"
                << ((veh.state & VSTATE_DESIGNATE_DEFENDER) ? "true" : "false")
                << ",\"ready\":" << (semantic_unit_requires_decision(i) ? "true" : "false")
                << ",\"selected\":" << (i == fair_current_vehicle(faction_id) ? "true" : "false");
        }
        out << '}';
    }
    out << "],\"offset\":" << offset << ",\"limit\":" << limit
        << ",\"next_offset\":" << (emitted == limit ? offset + emitted : -1) << '}';
    return out.str();
}

std::string factions_response() {
    if (!game_active()) return error_response("not_in_game", "Start or load a game first.");
    ensure_test_loan_fixture();
    int faction_id = *CurrentPlayerFaction;
    std::ostringstream out;
    out << "{\"ok\":true,\"kind\":\"factions\",\"items\":[";
    bool comma = false;
    for (int other = 1; other < MaxPlayerNum; ++other) {
        if (other != faction_id && (!is_alive(other)
        || !has_treaty(faction_id, other, DIPLO_COMMLINK))) continue;
        if (comma) out << ',';
        comma = true;
        int status = Factions[faction_id].diplo_status[other];
        out << "{\"id\":" << other
            << ",\"faction_name\":" << json_string(MFactions[other].formal_name_faction)
            << ",\"leader_name\":" << json_string(MFactions[other].name_leader)
            << ",\"alien\":" << (is_alien(other) ? "true" : "false")
            << ",\"relations\":{\"commlink\":" << ((status & DIPLO_COMMLINK) ? "true" : "false")
            << ",\"vendetta\":" << ((status & DIPLO_VENDETTA) ? "true" : "false")
            << ",\"truce\":" << ((status & DIPLO_TRUCE) ? "true" : "false")
            << ",\"treaty\":" << ((status & DIPLO_TREATY) ? "true" : "false")
            << ",\"pact\":" << ((status & DIPLO_PACT) ? "true" : "false")
            << ",\"infiltrated\":" << ((status & DIPLO_HAVE_INFILTRATOR) ? "true" : "false")
            << "},\"last_spoke_turn\":" << Factions[faction_id].diplo_spoke[other]
            << ",\"loans\":{\"own_balance_owed_to_them\":"
            << Factions[faction_id].loan_balance[other]
            << ",\"own_payment_per_turn\":" << Factions[faction_id].loan_payment[other]
            << ",\"their_balance_owed_to_us\":" << Factions[other].loan_balance[faction_id]
            << ",\"their_payment_per_turn\":" << Factions[other].loan_payment[faction_id]
            << "}}";
    }
    out << "],\"fair_play_note\":\"Only the player faction and factions with an acquired commlink are listed.\"}";
    return out.str();
}

std::string technologies_response() {
    if (!game_active()) return error_response("not_in_game", "Start or load a game first.");
    ensure_test_commerce_fixture();
    int faction_id = *CurrentPlayerFaction;
    std::ostringstream out;
    out << "{\"ok\":true,\"kind\":\"technologies\",\"items\":[";
    bool comma = false;
    for (int tech_id = 0; tech_id < MaxTechnologyNum; ++tech_id) {
        if (!(TechOwners[tech_id] & (1 << faction_id))) continue;
        if (comma) out << ',';
        comma = true;
        out << "{\"id\":" << tech_id << ",\"name\":" << json_string(Tech[tech_id].name)
            << ",\"category\":" << tech_category(tech_id) << '}';
    }
    out << "],\"fair_play_note\":\"Only technologies already owned by the player faction are listed; a hidden blind-research target is never exposed.\"}";
    return out.str();
}

std::string test_technology_demand_status_response() {
    char test_mode[8] = {};
    char test_demand[16] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_TECH_DEMAND", test_demand,
        sizeof(test_demand))
    || (strcmp(test_demand, "1") && strcmp(test_demand, "energy")
        && strcmp(test_demand, "tech"))) {
        return error_response("test_mode_disabled",
            "Technology-demand fixture status exists only in explicit contained test mode.");
    }
    int expected_count = test_technology_demand_fixture_mode ? 1 : 4;
    int acquired = 0;
    if (test_technology_demand_other_id >= 1) {
        for (int index = 0; index < expected_count; ++index) {
            int tech_id = test_technology_demand_ids[index];
            if (tech_id >= 0 && tech_id < MaxTechnologyNum
            && (TechOwners[tech_id] & (1 << test_technology_demand_other_id))) ++acquired;
        }
    }
    bool distractor_acquired = test_technology_demand_other_id >= 1
        && test_technology_demand_distractor_id >= 0
        && test_technology_demand_distractor_id < MaxTechnologyNum
        && (TechOwners[test_technology_demand_distractor_id]
            & (1 << test_technology_demand_other_id));
    std::ostringstream out;
    out << "{\"ok\":true,\"fixture\":\"technology_demand\",\"stage\":"
        << test_technology_demand_fixture_stage << ",\"counterpart_faction_id\":"
        << test_technology_demand_other_id << ",\"mode\":"
        << json_string(test_technology_demand_fixture_mode == 1 ? "energy"
            : test_technology_demand_fixture_mode == 2 ? "tech" : "bundle")
        << ",\"initial_dialog_result\":" << test_technology_demand_initial_result
        << ",\"followup_dialog_result\":" << test_technology_demand_followup_result
        << ",\"active_popup_label\":" << json_string(agent_popup_label())
        << ",\"last_started_popup_label\":"
        << json_string(agent_popup_last_started_label())
        << ",\"demanded_technology_ids\":[";
    for (int index = 0; index < expected_count; ++index) {
        if (index) out << ',';
        out << test_technology_demand_ids[index];
    }
    out << "],\"distractor_technology_id\":" << test_technology_demand_distractor_id
        << ",\"counterpart_demanded_acquired\":" << acquired
        << ",\"counterpart_distractor_acquired\":"
        << (distractor_acquired ? "true" : "false") << '}';
    return out.str();
}

std::string test_nerve_gas_status_response() {
    char test_mode[8] = {};
    char test_nerve[16] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_NERVE_GAS", test_nerve,
        sizeof(test_nerve))
    || (strcmp(test_nerve, "conventional") && strcmp(test_nerve, "commit"))) {
        return error_response("test_mode_disabled",
            "Nerve-gas fixture status exists only in explicit contained test mode.");
    }
    int faction_id = game_active() ? *CurrentPlayerFaction : -1;
    int current_atrocities = faction_id >= 1 ? Factions[faction_id].atrocities : -1;
    bool attacker_alive = false;
    bool used_nerve_gas = false;
    for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
        VEH& veh = Vehs[veh_id];
        if (veh.faction_id != faction_id || strcmp(veh.name(), "Harness Nerve Unit")) continue;
        attacker_alive = true;
        used_nerve_gas = (veh.state & VSTATE_USED_NERVE_GAS) != 0;
        break;
    }
    std::ostringstream out;
    out << "{\"ok\":true,\"fixture\":\"nerve_gas_combat\",\"mode\":"
        << json_string(test_nerve) << ",\"attacker_unit_id\":"
        << test_nerve_gas_attacker_id << ",\"defender_faction_id\":"
        << test_nerve_gas_defender_faction_id << ",\"atrocities_before\":"
        << test_nerve_gas_atrocities_before << ",\"atrocities_after\":"
        << current_atrocities << ",\"attacker_alive\":"
        << (attacker_alive ? "true" : "false") << ",\"used_nerve_gas\":"
        << (used_nerve_gas ? "true" : "false") << '}';
    return out.str();
}

std::string test_self_destruct_status_response() {
    char test_mode[8] = {};
    char test_self_destruct[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode, sizeof(test_mode))
    || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_SELF_DESTRUCT", test_self_destruct,
        sizeof(test_self_destruct)) || strcmp(test_self_destruct, "1")) {
        return error_response("test_mode_disabled",
            "Self-destruct fixture status exists only in explicit contained test mode.");
    }
    int faction_id = game_active() ? *CurrentPlayerFaction : -1;
    bool attacker_alive = false;
    bool target_alive = false;
    for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
        VEH& veh = Vehs[veh_id];
        if (veh.faction_id == faction_id
        && !strcmp(veh.name(), "Harness Overload Unit")) attacker_alive = true;
        if (veh.faction_id == 0 && veh.unit_id == BSC_MIND_WORMS
        && veh.x == test_self_destruct_target_x
        && veh.y == test_self_destruct_target_y) target_alive = true;
    }
    std::ostringstream out;
    out << "{\"ok\":true,\"fixture\":\"self_destruct\","
        "\"origin_tile_id\":"
        << semantic_tile_id(test_self_destruct_origin_x, test_self_destruct_origin_y)
        << ",\"target_tile_id\":"
        << semantic_tile_id(test_self_destruct_target_x, test_self_destruct_target_y)
        << ",\"blast_damage\":" << test_self_destruct_blast_damage
        << ",\"attacker_alive\":" << (attacker_alive ? "true" : "false")
        << ",\"visible_native_target_alive\":"
        << (target_alive ? "true" : "false") << '}';
    return out.str();
}

std::string test_full_endgame_status_response() {
    std::ostringstream out;
    out << "{\"ok\":true,\"test_full_endgame\":{"
        "\"initialized\":" << (test_full_endgame_fixture_initialized ? "true" : "false")
        << ",\"pending\":" << (test_full_endgame_fixture_pending ? "true" : "false")
        << ",\"presentation_phase\":"
        << json_string(endgame_presentation_phase.c_str())
        << ",\"advance_pending\":"
        << (pending_endgame_presentation_advance ? "true" : "false")
        << ",\"result_captured\":"
        << (test_full_endgame_result_captured ? "true" : "false")
        << ",\"game_done\":"
        << ((*GameState & STATE_GAME_DONE) ? "true" : "false")
        << ",\"final_score_done\":"
        << (test_full_endgame_result_captured
            ? (test_full_endgame_final_score_done ? "true" : "false")
            : ((*GameState & STATE_FINAL_SCORE_DONE) ? "true" : "false"))
        << ",\"control_turn_a\":" << (test_full_endgame_result_captured
            ? test_full_endgame_control_turn_a : *ControlTurnA)
        << ",\"control_turn_b\":" << (test_full_endgame_result_captured
            ? test_full_endgame_control_turn_b : *ControlTurnB) << "}}";
    return out.str();
}

std::string test_network_sync_status_response() {
    char test_mode[8] = {};
    char test_lan[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode,
        sizeof(test_mode)) || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_LAN_HOST", test_lan,
        sizeof(test_lan)) || strcmp(test_lan, "1")) {
        return error_response("test_mode_disabled",
            "The contained multiplayer synchronization probe is disabled.");
    }
    if (*MultiplayerActive && agent_modal_service_depth == 0) {
        pump_native_network_packets();
    }
    std::ostringstream out;
    out << "{\"ok\":true,\"turn\":" << *CurrentTurn
        << ",\"current_faction_id\":" << *CurrentFaction
        << ",\"local_faction_id\":" << *CurrentPlayerFaction
        << ",\"win_modal\":" << (*WinModalState ? "true" : "false")
        << ",\"popup_dialog\":" << (*PopupDialogState ? "true" : "false")
        << ",\"game_halted\":" << (*GameHalted ? "true" : "false")
        << ",\"interaction\":"
        << json_string(interaction_kind(*CurrentPlayerFaction).c_str())
        << ",\"diplo_window\":{\"active\":"
        << (human_diplomacy_window_active() ? "true" : "false")
        << ",\"state\":" << *DiploWinState;
    if (human_diplomacy_window_active()) {
        out << ",\"initiator_faction_id\":" << *reinterpret_cast<int*>(
            reinterpret_cast<char*>(DiploWin) + 0xAB4)
            << ",\"counterpart_faction_id\":" << *reinterpret_cast<int*>(
            reinterpret_cast<char*>(DiploWin) + 0xAB8);
    }
    out << "},\"diplo_pairs\":[";
    bool diplo_pair_comma = false;
    for (int first = 1; first < MaxPlayerNum; ++first) {
        if (!is_human(first)) continue;
        for (int second = 1; second < MaxPlayerNum; ++second) {
            if (second == first || !is_human(second)) continue;
            if (diplo_pair_comma) out << ',';
            diplo_pair_comma = true;
            out << "{\"from\":" << first << ",\"to\":" << second
                << ",\"pending\":" << DiploStateB[first][second]
                << ",\"relationship_status\":"
                << Factions[first].diplo_status[second]
                << ",\"records\":[";
            for (int index = 0; index < 36; ++index) {
                if (index) out << ',';
                out << DiploStateC[first][second][index];
            }
            out << "]}";
        }
    }
    out << "],\"vehicles\":[";
    for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
        if (veh_id) out << ',';
        VEH& veh = Vehs[veh_id];
        out << "{\"id\":" << veh_id
            << ",\"faction_id\":" << static_cast<int>(veh.faction_id)
            << ",\"prototype_id\":" << static_cast<int>(veh.unit_id)
            << ",\"tile_id\":" << semantic_tile_id(veh.x, veh.y)
            << ",\"moves_spent\":" << static_cast<int>(veh.moves_spent)
            << ",\"damage_taken\":" << static_cast<int>(veh.damage_taken)
            << ",\"order\":" << static_cast<int>(veh.order)
            << ",\"state\":" << static_cast<unsigned int>(veh.state) << '}';
    }
    out << "],\"bases\":[";
    for (int base_id = 0; base_id < *BaseCount; ++base_id) {
        if (base_id) out << ',';
        BASE& base = Bases[base_id];
        out << "{\"id\":" << base_id
            << ",\"faction_id\":" << static_cast<int>(base.faction_id)
            << ",\"tile_id\":" << semantic_tile_id(base.x, base.y)
            << ",\"current_item_id\":" << base.queue_items[0]
            << ",\"queue_size\":" << static_cast<int>(base.queue_size)
            << ",\"minerals_accumulated\":" << base.minerals_accumulated
            << ",\"governor_flags\":"
            << static_cast<unsigned int>(base.governor_flags)
            << ",\"worked_tiles\":" << static_cast<unsigned int>(base.worked_tiles)
            << ",\"specialist_total\":" << static_cast<int>(base.specialist_total)
            << ",\"specialist_adjust\":" << static_cast<int>(base.specialist_adjust)
            << ",\"specialist_types\":[";
        for (int specialist = 0; specialist < base.specialist_total; ++specialist) {
            if (specialist) out << ',';
            out << base.specialist_type(specialist);
        }
        out << ']'
            << ",\"queue_items\":[";
        for (int position = 0; position <= base.queue_size && position < 10;
        ++position) {
            if (position) out << ',';
            out << base.queue_items[position];
        }
        out << "]}";
    }
    out << "],\"factions\":[";
    for (int faction_id = 1; faction_id < MaxPlayerNum; ++faction_id) {
        if (faction_id > 1) out << ',';
        Faction& faction = Factions[faction_id];
        out << "{\"id\":" << faction_id
            << ",\"energy\":" << faction.energy_credits
            << ",\"allocation\":{\"economy\":"
            << (10 - faction.SE_alloc_labs - faction.SE_alloc_psych)
            << ",\"psych\":" << faction.SE_alloc_psych
            << ",\"labs\":" << faction.SE_alloc_labs << "}"
            << ",\"research_priority\":"
            << (faction.AI_growth ? TCAT_GROWTH
                : faction.AI_tech ? TCAT_TECH
                : faction.AI_wealth ? TCAT_WEALTH
                : faction.AI_power ? TCAT_POWER : -1)
            << ",\"social_pending\":["
            << faction.SE_Politics_pending << ','
            << faction.SE_Economics_pending << ','
            << faction.SE_Values_pending << ','
            << faction.SE_Future_pending << ']'
            << ",\"social_established\":["
            << faction.SE_Politics << ',' << faction.SE_Economics << ','
            << faction.SE_Values << ',' << faction.SE_Future << ']'
            << ",\"social_upheaval_paid\":" << faction.SE_upheaval_cost_paid
            << ",\"base_governor_adv\":"
            << static_cast<unsigned int>(faction.base_governor_adv)
            << ",\"units_queue_hash\":";
        uint32_t units_queue_hash = 2166136261u;
        for (int unit_id = 0; unit_id < MaxProtoNum; ++unit_id) {
            units_queue_hash ^= static_cast<uint32_t>(faction.units_queue[unit_id]);
            units_queue_hash *= 16777619u;
        }
        out << units_queue_hash << '}';
    }
    out << "]}";
    return out.str();
}

std::string test_social_engineering_fixture_response(const std::string& request) {
    char test_mode[8] = {};
    char test_lan[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode,
        sizeof(test_mode)) || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_LAN_HOST", test_lan,
        sizeof(test_lan)) || strcmp(test_lan, "1")) {
        return error_response("test_mode_disabled",
            "The contained social-engineering fixture is disabled.");
    }
    int faction_id = field_int(request, "faction_id", -1);
    if (!game_active() || faction_id < 1 || faction_id >= MaxPlayerNum) {
        return error_response("invalid_social_fixture",
            "The fixture requires an active game and a player faction id.");
    }
    for (int category = 0; category < MaxSocialCatNum; ++category) {
        for (int model = 1; model < MaxSocialModelNum; ++model) {
            int prerequisite = SocialField[category].soc_preq_tech[model];
            if (prerequisite < 0 || prerequisite >= MaxTechnologyNum) continue;
            TechOwners[prerequisite] |= 1 << faction_id;
            if (!society_avail(category, model, faction_id)) continue;
            std::ostringstream out;
            out << "{\"ok\":true,\"faction_id\":" << faction_id
                << ",\"category_id\":" << category << ",\"category\":"
                << json_string(social_category_key(category))
                << ",\"model_id\":" << model << ",\"model_name\":"
                << json_string(SocialField[category].soc_name[model])
                << ",\"prerequisite_tech_id\":" << prerequisite << '}';
            return out.str();
        }
    }
    return error_response("social_fixture_unavailable",
        "No non-default social model could be made legal for this faction.");
}

std::string test_lan_combat_fixture_response(const std::string& request) {
    char test_mode[8] = {};
    char test_lan[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode,
        sizeof(test_mode)) || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_LAN_HOST", test_lan,
        sizeof(test_lan)) || strcmp(test_lan, "1")) {
        return error_response("test_mode_disabled",
            "The contained LAN combat fixture is disabled.");
    }
    int faction_id = field_int(request, "faction_id", -1);
    if (!game_active() || faction_id < 1 || faction_id >= MaxPlayerNum) {
        return error_response("invalid_combat_fixture",
            "The fixture requires an active game and a player faction id.");
    }
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate == faction_id || is_human(candidate) || !is_alive(candidate)) continue;
        for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
            if (Vehs[veh_id].faction_id != candidate) continue;
            other = candidate;
            break;
        }
        if (other >= 0) break;
    }
    int origin_x = -1;
    int origin_y = -1;
    int target_x = -1;
    int target_y = -1;
    for (int y = 0; y < *MapAreaY && origin_x < 0; ++y) {
        for (int x = y & 1; x < *MapAreaX && origin_x < 0; x += 2) {
            MAP* origin = mapsq(x, y);
            if (!origin || is_ocean(origin) || base_at(x, y) >= 0
            || veh_at(x, y) >= 0) continue;
            for (int dir = 0; dir < 8; ++dir) {
                int tx = wrap(x + BaseOffsetX[dir]);
                int ty = y + BaseOffsetY[dir];
                MAP* target = mapsq(tx, ty);
                if (!target || is_ocean(target) || base_at(tx, ty) >= 0
                || veh_at(tx, ty) >= 0) continue;
                origin_x = x;
                origin_y = y;
                target_x = tx;
                target_y = ty;
                break;
            }
        }
    }
    if (other < 0 || origin_x < 0 || target_x < 0) {
        return error_response("combat_fixture_unavailable",
            "No deterministic adjacent land combat tuple was available.");
    }
    const uint32_t clear_status = DIPLO_TRUCE | DIPLO_TREATY | DIPLO_PACT;
    Factions[faction_id].diplo_status[other] &= ~clear_status;
    Factions[other].diplo_status[faction_id] &= ~clear_status;
    Factions[faction_id].diplo_status[other] |= DIPLO_COMMLINK | DIPLO_VENDETTA;
    Factions[other].diplo_status[faction_id] |= DIPLO_COMMLINK | DIPLO_VENDETTA;
    // Reuse stable, initially synchronized vehicle records.  Appending test
    // vehicles is unsafe after autonomous AI upkeep because peers may have
    // different local high-water marks even while both human factions are
    // synchronized, which would assign different network vehicle ids.
    int attacker_id = -1;
    int defender_id = -1;
    for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
        if (attacker_id < 0 && Vehs[veh_id].faction_id == faction_id
        && Vehs[veh_id].unit_id == BSC_SCOUT_PATROL) attacker_id = veh_id;
        if (defender_id < 0 && Vehs[veh_id].faction_id == other) {
            defender_id = veh_id;
        }
    }
    if (attacker_id < 0 || defender_id < 0) {
        return error_response("combat_fixture_unavailable",
            "The deterministic test combat units could not be created.");
    }
    veh_put(attacker_id, origin_x, origin_y);
    veh_put(defender_id, target_x, target_y);
    VEH& attacker = Vehs[attacker_id];
    VEH& defender = Vehs[defender_id];
    attacker.unit_id = BSC_SCOUT_PATROL;
    defender.unit_id = BSC_SCOUT_PATROL;
    attacker.moves_spent = 0;
    attacker.damage_taken = 0;
    attacker.order = ORDER_NONE;
    attacker.state &= ~(VSTATE_HAS_MOVED | VSTATE_MADE_AIRDROP
        | VSTATE_EXPLORE | VSTATE_ON_ALERT | VSTATE_WORKING);
    defender.moves_spent = 0;
    defender.order = ORDER_NONE;
    defender.state &= ~(VSTATE_HAS_MOVED | VSTATE_MADE_AIRDROP
        | VSTATE_EXPLORE | VSTATE_ON_ALERT | VSTATE_WORKING);
    defender.damage_taken = static_cast<uint8_t>(
        max(0, defender.max_hitpoints() - 1));
    attacker.visibility |= 1 << faction_id;
    defender.visibility |= 1 << faction_id;
    mapsq(origin_x, origin_y)->visibility |= 1 << faction_id;
    mapsq(target_x, target_y)->visibility |= 1 << faction_id;
    std::ostringstream out;
    out << "{\"ok\":true,\"attacker_faction_id\":" << faction_id
        << ",\"attacker_unit_id\":" << attacker_id
        << ",\"defender_faction_id\":" << other
        << ",\"defender_unit_id\":" << defender_id
        << ",\"origin_tile_id\":"
        << semantic_tile_id(attacker.x, attacker.y)
        << ",\"target_tile_id\":" << semantic_tile_id(target_x, target_y)
        << ",\"already_at_war\":true,\"defender_reduced_to_one_hit_point\":true}";
    return out.str();
}

std::string test_lan_diplomacy_fixture_response(const std::string& request) {
    char test_mode[8] = {};
    char test_lan[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode,
        sizeof(test_mode)) || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_LAN_HOST", test_lan,
        sizeof(test_lan)) || strcmp(test_lan, "1")) {
        return error_response("test_mode_disabled",
            "The contained LAN diplomacy fixture is disabled.");
    }
    int faction_id = field_int(request, "faction_id", -1);
    int other = field_int(request, "counterpart_faction_id", -1);
    if (!game_active() || faction_id < 1 || faction_id >= MaxPlayerNum
    || other < 1 || other >= MaxPlayerNum || other == faction_id
    || !is_human(faction_id) || !is_human(other)) {
        return error_response("invalid_diplomacy_fixture",
            "The fixture requires two distinct live human faction ids in an active game.");
    }
    Factions[faction_id].diplo_status[other] |= DIPLO_COMMLINK;
    Factions[other].diplo_status[faction_id] |= DIPLO_COMMLINK;
    std::string initial_relationship = field_string(
        request, "initial_relationship");
    if (initial_relationship == "treaty"
    || initial_relationship == "vendetta") {
        const uint32_t relationship_bits = DIPLO_VENDETTA | DIPLO_TRUCE
            | DIPLO_TREATY | DIPLO_PACT;
        Factions[faction_id].diplo_status[other] &= ~relationship_bits;
        Factions[other].diplo_status[faction_id] &= ~relationship_bits;
        uint32_t initial_bit = initial_relationship == "treaty"
            ? DIPLO_TREATY : DIPLO_VENDETTA;
        Factions[faction_id].diplo_status[other] |= initial_bit;
        Factions[other].diplo_status[faction_id] |= initial_bit;
    }
    std::string trade_fixture = field_string(request, "trade_fixture");
    int technology_id = -1;
    int joint_attack_target = -1;
    if (trade_fixture == "technology") {
        for (int candidate = 0; candidate < MaxTechnologyNum; ++candidate) {
            if (Tech[candidate].name[0]) {
                technology_id = candidate;
                break;
            }
        }
        if (technology_id < 0) {
            return error_response("missing_diplomacy_trade_fixture",
                "No valid technology exists for the contained human trade fixture.");
        }
        TechOwners[technology_id] |= 1 << faction_id;
        TechOwners[technology_id] &= ~(1 << other);
    } else if (trade_fixture == "energy") {
        Factions[faction_id].energy_credits = 500;
        Factions[other].energy_credits = 100;
    } else if (trade_fixture == "joint_attack") {
        for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
            if (candidate != faction_id && candidate != other
            && is_alive(candidate)) {
                joint_attack_target = candidate;
                break;
            }
        }
        if (joint_attack_target < 0) {
            return error_response("missing_diplomacy_trade_fixture",
                "No live third faction exists for the contained joint-attack fixture.");
        }
        Factions[faction_id].diplo_status[joint_attack_target] |= DIPLO_COMMLINK;
        Factions[joint_attack_target].diplo_status[faction_id] |= DIPLO_COMMLINK;
    }
    std::ostringstream out;
    out << "{\"ok\":true,\"faction_id\":" << faction_id
        << ",\"counterpart_faction_id\":" << other
        << ",\"symmetric_commlink\":true,\"initial_relationship\":"
        << json_string(initial_relationship.c_str())
        << ",\"trade_fixture\":" << json_string(trade_fixture.c_str());
    if (technology_id >= 0) {
        out << ",\"technology_id\":" << technology_id
            << ",\"technology_name\":" << json_string(Tech[technology_id].name);
    } else if (trade_fixture == "energy") {
        out << ",\"donor_energy_credits\":"
            << Factions[faction_id].energy_credits
            << ",\"recipient_energy_credits\":"
            << Factions[other].energy_credits;
    } else if (joint_attack_target >= 0) {
        out << ",\"target_faction_id\":" << joint_attack_target
            << ",\"target_faction_name\":"
            << json_string(MFactions[joint_attack_target].formal_name_faction);
    }
    out << '}';
    return out.str();
}

std::string test_lan_ai_contact_fixture_response(const std::string& request) {
    char test_mode[8] = {};
    char test_lan[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode,
        sizeof(test_mode)) || strcmp(test_mode, "1")
    || !GetEnvironmentVariableA("SMACX_AGENT_TEST_LAN_HOST", test_lan,
        sizeof(test_lan)) || strcmp(test_lan, "1")) {
        return error_response("test_mode_disabled",
            "The contained LAN AI-contact fixture is disabled.");
    }
    int faction_id = field_int(request, "faction_id", -1);
    if (!game_active() || faction_id < 1 || faction_id >= MaxPlayerNum
    || !is_human(faction_id)) {
        return error_response("invalid_ai_contact_fixture",
            "The fixture requires a live human faction in an active LAN game.");
    }
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate)
        && !is_human(candidate) && !is_alien(candidate)) {
            other = candidate;
            break;
        }
    }
    if (other < 0) {
        return error_response("missing_ai_contact_fixture",
            "No live non-alien AI faction is available for this contained fixture.");
    }
    const uint32_t relationship_bits = DIPLO_VENDETTA | DIPLO_TRUCE
        | DIPLO_TREATY | DIPLO_PACT;
    Factions[faction_id].diplo_status[other] &= ~relationship_bits;
    Factions[other].diplo_status[faction_id] &= ~relationship_bits;
    treaty_on(faction_id, other, DIPLO_COMMLINK | DIPLO_TREATY | DIPLO_PACT);
    Factions[faction_id].diplo_spoke[other] = -100;
    Factions[other].diplo_spoke[faction_id] = -100;
    Factions[other].diplo_patience[faction_id] = 0;
    *DiploFriction = 0;
    std::ostringstream out;
    out << "{\"ok\":true,\"faction_id\":" << faction_id
        << ",\"counterpart_faction_id\":" << other
        << ",\"counterpart_human_controlled\":false,"
        << "\"relationship\":\"pact\",\"commlink\":true}";
    return out.str();
}

std::string test_airdrop_legality_fixture_response() {
    char test_mode[8] = {};
    if (!GetEnvironmentVariableA("SMACX_ACCEPTANCE_AIRDROP_LEGALITY", test_mode,
        sizeof(test_mode)) || strcmp(test_mode, "1")) {
        return error_response("test_mode_disabled",
            "The contained native airdrop legality fixture is disabled.");
    }
    if (!game_active()) return error_response("not_in_game", "Start a game first.");
    const int faction_id = *CurrentPlayerFaction;
    int other = -1;
    for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
        if (candidate != faction_id && is_alive(candidate)) {
            other = candidate;
            break;
        }
    }
    if (other < 0) return error_response("missing_fixture_faction",
        "No live counterpart faction is available for the airdrop fixture.");
    std::vector<std::pair<int, int>> sites;
    for (int y = 0; y < *MapAreaY && sites.size() < 5; ++y) {
        for (int x = y & 1; x < *MapAreaX && sites.size() < 5; x += 2) {
            MAP* sq = mapsq(x, y);
            if (!sq || is_ocean(sq) || sq->base_who() >= 0 || veh_at(x, y) >= 0) continue;
            bool separated = true;
            for (const auto& site : sites) {
                if (map_range(site.first, site.second, x, y) <= 1) {
                    separated = false;
                    break;
                }
            }
            if (separated) sites.push_back({x, y});
        }
    }
    if (sites.size() < 5) return error_response("missing_fixture_sites",
        "Five distinct empty land sites are required for the airdrop fixture.");
    const uint32_t relationship_bits = DIPLO_VENDETTA | DIPLO_TRUCE
        | DIPLO_TREATY | DIPLO_PACT;
    auto set_relationship = [&](uint32_t bit) {
        Factions[faction_id].diplo_status[other] &= ~relationship_bits;
        Factions[other].diplo_status[faction_id] &= ~relationship_bits;
        Factions[faction_id].diplo_status[other] |= bit;
        Factions[other].diplo_status[faction_id] |= bit;
    };
    auto spawn_occupant = [&](size_t site_index) {
        int id = veh_init(BSC_SCOUT_PATROL, other,
                          sites[site_index].first, sites[site_index].second);
        if (id >= 0) spot_all(id, 1);
        return id;
    };
    set_relationship(DIPLO_VENDETTA);
    if (spawn_occupant(0) < 0) return error_response("fixture_spawn_failed",
        "Could not create the hostile airdrop target.");
    MAP* hostile = mapsq(sites[0].first, sites[0].second);
    const bool hostile_combat = allow_airdrop(
        sites[0].first, sites[0].second, faction_id, true, hostile);
    const bool hostile_noncombat = allow_airdrop(
        sites[0].first, sites[0].second, faction_id, false, hostile);

    set_relationship(DIPLO_PACT);
    if (spawn_occupant(1) < 0) return error_response("fixture_spawn_failed",
        "Could not create the Pact airdrop target.");
    MAP* pact = mapsq(sites[1].first, sites[1].second);
    const bool pact_combat = allow_airdrop(
        sites[1].first, sites[1].second, faction_id, true, pact);
    const bool pact_noncombat = allow_airdrop(
        sites[1].first, sites[1].second, faction_id, false, pact);

    set_relationship(DIPLO_TREATY);
    if (spawn_occupant(2) < 0) return error_response("fixture_spawn_failed",
        "Could not create the treaty airdrop target.");
    MAP* treaty = mapsq(sites[2].first, sites[2].second);
    const bool treaty_combat = allow_airdrop(
        sites[2].first, sites[2].second, faction_id, true, treaty);
    const bool treaty_noncombat = allow_airdrop(
        sites[2].first, sites[2].second, faction_id, false, treaty);

    set_relationship(0);
    if (spawn_occupant(3) < 0) return error_response("fixture_spawn_failed",
        "Could not create the unknown-relation airdrop target.");
    MAP* unknown = mapsq(sites[3].first, sites[3].second);
    const bool unknown_combat = allow_airdrop(
        sites[3].first, sites[3].second, faction_id, true, unknown);
    const bool unknown_noncombat = allow_airdrop(
        sites[3].first, sites[3].second, faction_id, false, unknown);

    set_relationship(DIPLO_VENDETTA);
    const int defense_base_id = mod_base_init(
        other, sites[4].first, sites[4].second);
    if (defense_base_id < 0) return error_response("fixture_base_failed",
        "Could not create the Aerospace Complex target.");
    Bases[defense_base_id].facilities_built[FAC_AEROSPACE_COMPLEX / 8]
        |= 1 << (FAC_AEROSPACE_COMPLEX % 8);
    MAP* defended = mapsq(sites[4].first, sites[4].second);
    const bool aerospace_defended = !allow_airdrop(
        sites[4].first, sites[4].second, faction_id, true, defended);
    Bases[defense_base_id].facilities_built[FAC_AEROSPACE_COMPLEX / 8]
        &= ~(1 << (FAC_AEROSPACE_COMPLEX % 8));
    const int component_techs[] = {
        Chassis[CHS_NEEDLEJET].preq_tech,
        Weapon[WPN_LASER].preq_tech,
        Armor[ARM_NO_ARMOR].preq_tech,
        Ability[ABL_ID_AIR_SUPERIORITY].preq_tech,
        Reactor[REC_FISSION - 1].preq_tech,
    };
    for (int tech_id : component_techs) {
        if (tech_id >= 0 && tech_id < MaxTechnologyNum) {
            TechOwners[tech_id] |= 1 << other;
        }
    }
    char interceptor_name[] = "Harness Airdrop Defender";
    const int interceptor_proto = propose_proto(
        other, CHS_NEEDLEJET, WPN_LASER, ARM_NO_ARMOR,
        ABL_AIR_SUPERIORITY, REC_FISSION, PLAN_AIR_SUPERIORITY,
        interceptor_name);
    if (interceptor_proto < other * MaxProtoFactionNum
    || interceptor_proto >= min(MaxProtoNum, (other + 1) * MaxProtoFactionNum)) {
        return error_response("fixture_interceptor_failed",
            "Could not create the Air Superiority defender prototype.");
    }
    Units[interceptor_proto].unit_flags |= UNIT_PROTOTYPED;
    if (veh_init(interceptor_proto, other, sites[4].first, sites[4].second) < 0) {
        return error_response("fixture_interceptor_failed",
            "Could not station the Air Superiority defender.");
    }
    const bool air_superiority_defended = !allow_airdrop(
        sites[4].first, sites[4].second, faction_id, true, defended);

    std::ostringstream out;
    out << "{\"ok\":true,\"hostile_combat\":"
        << (hostile_combat ? "true" : "false")
        << ",\"hostile_noncombat\":" << (hostile_noncombat ? "true" : "false")
        << ",\"pact_combat\":" << (pact_combat ? "true" : "false")
        << ",\"pact_noncombat\":" << (pact_noncombat ? "true" : "false")
        << ",\"treaty_combat\":" << (treaty_combat ? "true" : "false")
        << ",\"treaty_noncombat\":" << (treaty_noncombat ? "true" : "false")
        << ",\"unknown_combat\":" << (unknown_combat ? "true" : "false")
        << ",\"unknown_noncombat\":" << (unknown_noncombat ? "true" : "false")
        << ",\"aerospace_defended\":" << (aerospace_defended ? "true" : "false")
        << ",\"air_superiority_defended\":"
        << (air_superiority_defended ? "true" : "false")
        << '}';
    return out.str();
}

std::string item_names(uint32_t items) {
    struct NamedBit { uint32_t bit; const char* name; };
    const NamedBit named[] = {
        {BIT_BASE_IN_TILE, "base"}, {BIT_VEH_IN_TILE, "vehicle"}, {BIT_ROAD, "road"},
        {BIT_MAGTUBE, "magtube"}, {BIT_MINE, "mine"}, {BIT_FUNGUS, "fungus"},
        {BIT_SOLAR, "solar_collector"}, {BIT_RIVER, "river"}, {BIT_BONUS_RES, "resource_bonus"},
        {BIT_BUNKER, "bunker"}, {BIT_MONOLITH, "monolith"}, {BIT_FARM, "farm"},
        {BIT_ENERGY_RES, "energy_resource"}, {BIT_MINERAL_RES, "mineral_resource"},
        {BIT_AIRBASE, "airbase"}, {BIT_SOIL_ENRICHER, "soil_enricher"}, {BIT_FOREST, "forest"},
        {BIT_CONDENSER, "condenser"}, {BIT_ECH_MIRROR, "echelon_mirror"},
        {BIT_THERMAL_BORE, "thermal_borehole"}, {BIT_SUPPLY_POD, "supply_pod"},
        {BIT_NUTRIENT_RES, "nutrient_resource"}, {BIT_SENSOR, "sensor"}
    };
    std::ostringstream out;
    out << '[';
    bool comma = false;
    for (size_t i = 0; i < sizeof(named) / sizeof(named[0]); ++i) {
        if (items & named[i].bit) {
            if (comma) out << ',';
            comma = true;
            out << json_string(named[i].name);
        }
    }
    out << ']';
    return out.str();
}

std::string tiles_response(const std::string& request) {
    if (!game_active()) return error_response("not_in_game", "Start or load a game first.");
    int faction_id = *CurrentPlayerFaction;
    int center_tile_id = field_int(request, "center_tile_id", -1);
    int center_x = -1;
    int center_y = -1;
    bool requested_center_valid = semantic_tile_coords(
        center_tile_id, &center_x, &center_y);
    int radius = std::min(16, std::max(0, field_int(request, "radius", 3)));
    if (center_x < 0 || center_y < 0) {
        int selected_id = fair_current_vehicle(faction_id);
        if (selected_id >= 0) {
            center_x = Vehs[selected_id].x;
            center_y = Vehs[selected_id].y;
        } else {
            std::ostringstream error;
            error << "{\"ok\":false,\"error\":{\"code\":\"missing_center\","
                << "\"message\":\"Provide center_tile_id from a fair-play base, unit, or tile record when no vehicle is selected.\"},"
                << "\"received_center_tile_id\":" << center_tile_id
                << ",\"requested_center_valid\":"
                << (requested_center_valid ? "true" : "false")
                << ",\"request_contains_center_tile_id\":"
                << (request.find("center_tile_id") != std::string::npos ? "true" : "false")
                << ",\"map_width\":" << *MapAreaX
                << ",\"map_height\":" << *MapAreaY << '}';
            return error.str();
        }
    }
    int emitted = 0;
    std::ostringstream out;
    out << "{\"ok\":true,\"kind\":\"tiles\",\"center\":{\"tile_id\":"
        << semantic_tile_id(center_x, center_y) << "},\"items\":[";
    for (int y = center_y - radius; y <= center_y + radius; ++y) {
        for (int x = center_x - radius * 2; x <= center_x + radius * 2; ++x) {
            if (map_range(center_x, center_y, x, y) > radius) continue;
            int wx = wrap(x);
            MAP* sq = mapsq(wx, y);
            if (!sq || !is_known(wx, y, faction_id)) continue;
            bool visible = sq->is_visible(faction_id);
            uint32_t remembered_items = visible ? sq->items : sq->visible_items[faction_id - 1];
            if (emitted++) out << ',';
            out << "{\"tile_id\":" << semantic_tile_id(wx, y)
                << ",\"visible_now\":" << (visible ? "true" : "false")
                << ",\"features\":" << item_names(remembered_items);
            if (visible) {
                out << ",\"altitude\":" << sq->alt_level()
                    << ",\"is_ocean\":" << (sq->alt_level() < ALT_SHORE_LINE ? "true" : "false")
                    << ",\"rainfall\":" << ((sq->climate >> 3) & 3)
                    << ",\"temperature\":" << (sq->climate & 7)
                    << ",\"rockiness\":" << sq->rocky_level()
                    << ",\"owner\":" << static_cast<int>(sq->owner);
            }
            out << '}';
        }
    }
    out << "],\"fair_play_note\":\"Non-visible tiles expose only remembered features; current terrain and owner are omitted.\"}";
    return out.str();
}

struct SemanticAirdropTarget {
    int tile_id;
    int distance;
    int base_owner;
    int unit_owner;
};

struct SemanticAirdropTargetReceipt {
    bool available = false;
    int legal_count = 0;
    bool truncated = false;
    std::vector<SemanticAirdropTarget> targets;
};

// One authoritative enumerator serves both provider-safe observation and the
// executable semantic action surface. Omitted anti-drop reasons remain hidden;
// membership is the current native legality receipt.
SemanticAirdropTargetReceipt semantic_airdrop_target_receipt(
int veh_id, int maximum_targets = 128) {
    SemanticAirdropTargetReceipt receipt;
    if (veh_id < 0 || veh_id >= *VehCount) return receipt;
    VEH& veh = Vehs[veh_id];
    const int faction_id = *CurrentPlayerFaction;
    if (veh.faction_id != faction_id
    || !can_airdrop(veh_id, mapsq(veh.x, veh.y))) return receipt;
    receipt.available = true;
    const int range = drop_range(faction_id);
    for (int distance = 0; distance <= range; ++distance) {
        for (int y = 0; y < *MapAreaY; ++y) {
            for (int x = y & 1; x < *MapAreaX; x += 2) {
                if (map_range(veh.x, veh.y, x, y) != distance) continue;
                MAP* sq = mapsq(x, y);
                if (!sq || !sq->is_visible(faction_id) || is_ocean(sq)
                || !allow_airdrop(x, y, faction_id, veh.is_combat_unit(), sq)) continue;
                const int unit_owner = sq->veh_who();
                const int base_owner = sq->base_who();
                const bool unsafe_relation =
                    (unit_owner >= 0 && unit_owner != faction_id
                        && !has_pact(faction_id, unit_owner)
                        && !at_war(faction_id, unit_owner))
                    || (base_owner >= 0 && base_owner != faction_id
                        && !has_pact(faction_id, base_owner)
                        && !at_war(faction_id, base_owner));
                if (unsafe_relation) continue;
                ++receipt.legal_count;
                if (static_cast<int>(receipt.targets.size()) >= maximum_targets) continue;
                receipt.targets.push_back({semantic_tile_id(x, y), distance,
                    base_owner, unit_owner});
            }
        }
    }
    receipt.truncated = receipt.legal_count > static_cast<int>(receipt.targets.size());
    return receipt;
}

std::string perspective_world_page_response(const std::string& request) {
    if (!game_active()) return error_response("not_in_game", "Start or load a game first.");
    const int faction_id = *CurrentPlayerFaction;
    const std::string domain = field_string(request, "domain");
    const int cursor = std::max(0, field_int(request, "cursor", 0));
    const int limit = std::min(256, std::max(1, field_int(request, "limit", 128)));
    const std::string revision = semantic_revision();
    std::ostringstream out;
    out << "{\"ok\":true,\"schema\":\"smacx.perspective-world-page.v1\""
        << ",\"domain\":" << json_string(domain.c_str())
        << ",\"action_revision\":" << json_string(revision.c_str())
        << ",\"turn\":" << *CurrentTurn << ",\"year\":" << *CurrentMissionYear
        << ",\"faction_id\":" << faction_id;
    if (domain == "summary") {
        out << ",\"map\":{\"width\":" << *MapAreaX
            << ",\"height\":" << *MapAreaY
            << ",\"horizontal_wrap\":" << (map_is_flat() ? "false" : "true")
            << ",\"known_all\":" << (map_is_known(faction_id) ? "true" : "false")
            << "},\"unity_survey\":"
            << ((*GameRules & RULES_NO_UNITY_SURVEY) ? "false" : "true")
            << ",\"is_governor\":"
            << (*GovernorFaction == faction_id ? "true" : "false")
            << ",\"items\":[],\"next_cursor\":null}";
        return out.str();
    }
    out << ",\"items\":[";
    int emitted = 0;
    int next_cursor = -1;
    if (domain == "tiles") {
        int index = cursor;
        for (; index < *MapAreaTiles && emitted < limit; ++index) {
            int x = -1, y = -1;
            if (!semantic_tile_coords(index, &x, &y) || !is_known(x, y, faction_id)) continue;
            MAP* sq = mapsq(x, y);
            if (!sq) continue;
            if (emitted++) out << ',';
            const bool visible = sq->is_visible(faction_id);
            const uint32_t remembered = visible ? sq->items : sq->visible_items[faction_id - 1];
            out << "{\"tile_id\":" << index << ",\"x\":" << x << ",\"y\":" << y
                << ",\"visible_now\":" << (visible ? "true" : "false")
                << ",\"features\":" << item_names(remembered);
            if (visible) {
                out << ",\"altitude\":" << sq->alt_level()
                    << ",\"is_ocean\":" << (is_ocean(sq) ? "true" : "false")
                    << ",\"rainfall\":" << ((sq->climate >> 3) & 3)
                    << ",\"temperature\":" << (sq->climate & 7)
                    << ",\"rockiness\":" << sq->rocky_level()
                    << ",\"owner_ref\":";
                if (sq->owner >= 0 && sq->owner < MaxPlayerNum) {
                    out << json_string((std::string("faction-")
                        + std::to_string(static_cast<int>(sq->owner))).c_str());
                } else out << "null";
            }
            out << '}';
        }
        next_cursor = index < *MapAreaTiles ? index : -1;
    } else if (domain == "bases") {
        int index = cursor;
        for (; index < *BaseCount && emitted < limit; ++index) {
            BASE& base = Bases[index];
            MAP* square = mapsq(base.x, base.y);
            const bool owned = base.faction_id == faction_id;
            const bool visible = square && square->is_visible(faction_id);
            if (!owned && !visible) continue;
            if (emitted++) out << ',';
            out << "{\"id\":" << index << ",\"base_ref\":\"base-location-"
                << semantic_tile_id(base.x, base.y) << "\",\"tile_id\":"
                << semantic_tile_id(base.x, base.y) << ",\"owned\":"
                << (owned ? "true" : "false") << ",\"visible_now\":"
                << (visible ? "true" : "false") << ",\"name\":"
                << json_string(base.name) << ",\"owner_ref\":\"faction-"
                << static_cast<int>(base.faction_id) << "\""
                << ",\"coastal\":"
                << (coast_tiles(base.x, base.y) ? "true" : "false");
            if (owned) {
                set_base(index);
                base_compute(1);
                out << ",\"population\":" << static_cast<int>(base.pop_size)
                    << ",\"nutrient_surplus\":" << base.nutrient_surplus
                    << ",\"mineral_surplus\":" << base.mineral_surplus
                    << ",\"energy_surplus\":" << base.energy_surplus
                    << ",\"minerals_accumulated\":" << base.minerals_accumulated
                    << ",\"minerals\":{\"intake\":" << base.mineral_intake_2
                    << ",\"consumption\":" << base.mineral_consumption
                    << ",\"unit_support_cost\":" << *BaseForcesMaintCost
                    << ",\"surplus\":" << base.mineral_surplus
                    << ",\"accumulated\":" << base.minerals_accumulated << '}'
                    << ",\"production_id\":" << base.queue_items[0]
                    << ",\"production_name\":"
                    << json_string(production_name(base.queue_items[0]).c_str())
                    << ",\"eco_damage\":" << base.eco_damage
                    << ",\"drone_riots\":"
                    << (base.drone_riots_active() ? "true" : "false")
                    << ",\"facilities\":[";
                bool facility_comma = false;
                for (int facility_id = Fac_ID_First; facility_id < SP_ID_First;
                ++facility_id) {
                    if (!has_fac_built(static_cast<FacilityId>(facility_id), index)) continue;
                    if (facility_comma) out << ',';
                    facility_comma = true;
                    out << "{\"facility_id\":" << facility_id << ",\"name\":"
                        << json_string(Facility[facility_id].name)
                        << ",\"maintenance\":" << fac_maint(facility_id, faction_id) << '}';
                }
                out << "],\"base_radius\":[";
                bool radius_comma = false;
                for (int tile_index = 0; tile_index < 21; ++tile_index) {
                    int tile_x = 0;
                    int tile_y = 0;
                    MAP* tile = next_tile(base.x, base.y, tile_index, &tile_x, &tile_y);
                    if (!tile || !tile->is_visible(faction_id)) continue;
                    if (radius_comma) out << ',';
                    radius_comma = true;
                    out << "{\"location_ref\":\"location-"
                        << semantic_tile_id(tile_x, tile_y)
                        << "\",\"worked\":"
                        << ((base.worked_tiles & (1 << tile_index)) ? "true" : "false")
                        << ",\"yields\":{\"nutrients\":"
                        << mod_crop_yield(faction_id, index, tile_x, tile_y, 0)
                        << ",\"minerals\":"
                        << mod_mine_yield(faction_id, index, tile_x, tile_y, 0)
                        << ",\"energy\":"
                        << mod_energy_yield(faction_id, index, tile_x, tile_y, 0)
                        << "}}";
                }
                out << ']';
            }
            out << '}';
        }
        next_cursor = index < *BaseCount ? index : -1;
    } else if (domain == "units") {
        ensure_semantic_vehicle_handles();
        int index = cursor;
        for (; index < *VehCount && emitted < limit; ++index) {
            VEH& veh = Vehs[index];
            const bool owned = veh.faction_id == faction_id;
            const bool visible = owned || (veh.visibility & (1 << faction_id));
            if (!visible) continue;
            if (emitted++) out << ',';
            const int stable_handle = semantic_vehicle_handle(index);
            const SemanticAirdropTargetReceipt airdrop_receipt = owned
                ? semantic_airdrop_target_receipt(index)
                : SemanticAirdropTargetReceipt{};
            out << "{\"id\":" << index
                << ",\"own_unit_ref\":";
            if (owned) out << json_string(
                (std::string("own-unit-") + std::to_string(stable_handle)).c_str());
            else out << "null";
            out
                << ",\"native_observation_key\":" << json_string(
                    (std::string("vehicle-handle-") + std::to_string(stable_handle)).c_str())
                << ",\"tile_id\":" << semantic_tile_id(veh.x, veh.y)
                << ",\"owned\":" << (owned ? "true" : "false")
                << ",\"owner_ref\":\"faction-" << static_cast<int>(veh.faction_id)
                << "\",\"name\":" << json_string(veh.name())
                << ",\"hp\":" << veh.cur_hitpoints()
                << ",\"max_hp\":" << veh.max_hitpoints()
                << ",\"triad\":" << json_string(
                    veh.triad() == TRIAD_LAND ? "land" : veh.triad() == TRIAD_SEA ? "sea" : "air")
                << ",\"movement_points\":" << veh_speed(index, 0)
                << ",\"movement_scale\":" << Rules->move_rate_roads
                << ",\"road_movement_cost\":" << semantic_road_edge_cost()
                << ",\"magtube_movement_cost\":" << semantic_magtube_edge_cost()
                << ",\"fungus_movement_cost\":" << semantic_fungus_movement_cost(veh)
                << ",\"fungus_connects_to_road\":"
                << (semantic_fungus_connects_to_road(veh) ? "true" : "false")
                << ",\"ignores_rough_movement\":"
                << (semantic_ignores_rough_movement(veh) ? "true" : "false")
                << ",\"air_range\":" << static_cast<int>(veh.range())
                << ",\"air_fuel_turns_used\":" << static_cast<int>(veh.movement_turns)
                << ",\"air_safe_range\":" << semantic_air_safe_range(index)
                << ",\"air_full_safe_range\":" << semantic_air_full_safe_range(index)
                << ",\"air_origin_refuels\":"
                << (owned && semantic_friendly_air_refuel_tile(
                    faction_id, veh.x, veh.y) ? "true" : "false")
                << ",\"airdrop_ready\":"
                << (airdrop_receipt.available ? "true" : "false")
                << ",\"airdrop_range\":" << (owned ? drop_range(faction_id) : -1);
            if (airdrop_receipt.available) {
                out << ",\"airdrop_target_tile_ids\":[";
                for (size_t target_index = 0;
                     target_index < airdrop_receipt.targets.size(); ++target_index) {
                    if (target_index) out << ',';
                    out << airdrop_receipt.targets[target_index].tile_id;
                }
                out << "]"
                    << ",\"airdrop_target_count\":" << airdrop_receipt.legal_count
                    << ",\"airdrop_targets_truncated\":"
                    << (airdrop_receipt.truncated ? "true" : "false");
            }
            out << ",\"abilities\":[";
            bool page_ability_comma = false;
            struct PageAbility { VehAblFlag flag; const char* name; };
            const PageAbility page_abilities[] = {
                {ABL_CLOAKED, "cloaked"}, {ABL_AMPHIBIOUS, "amphibious"},
                {ABL_DROP_POD, "drop_pod"}, {ABL_ANTIGRAV_STRUTS, "antigrav_struts"},
                {ABL_CLEAN_REACTOR, "clean_reactor"},
                {ABL_FUEL_NANOCELLS, "fuel_nanocells"},
            };
            for (const auto& ability : page_abilities) {
                if (!has_abil(veh.unit_id, ability.flag)) continue;
                if (page_ability_comma) out << ',';
                page_ability_comma = true;
                out << json_string(ability.name);
            }
            out << ']'
                << ",\"roles\":{\"combat\":" << (veh.is_combat_unit() ? "true" : "false")
                << ",\"probe\":" << (veh.is_probe() ? "true" : "false")
                << ",\"supply\":" << (veh.is_supply() ? "true" : "false")
                << ",\"transport\":" << (veh.is_transport() ? "true" : "false")
                << ",\"planet_life\":" << (veh.is_native_unit() ? "true" : "false")
                << ",\"wild_native\":" << (veh.is_native_unit() && !veh.faction_id ? "true" : "false")
                << ",\"controlled_native\":" << (veh.is_native_unit() && veh.faction_id ? "true" : "false")
                << ",\"progenitor_force\":" << (!veh.is_native_unit() && is_alien(veh.faction_id) ? "true" : "false")
                << ",\"airdrop_capable\":" << (has_abil(veh.unit_id, ABL_DROP_POD) ? "true" : "false")
                << ",\"airdrop_used\":" << ((veh.state & VSTATE_MADE_AIRDROP) ? "true" : "false")
                << ",\"amphibious\":" << (has_abil(veh.unit_id, ABL_AMPHIBIOUS) ? "true" : "false")
                << ",\"cloaked\":" << (has_abil(veh.unit_id, ABL_CLOAKED) ? "true" : "false")
                << '}';
            if (owned) {
                out << ",\"moves_spent\":" << static_cast<int>(veh.moves_spent)
                    << ",\"requires_support\":"
                    << ((veh.state & VSTATE_REQUIRES_SUPPORT) ? "true" : "false")
                    << ",\"cargo\":{\"capacity\":" << veh_cargo(index)
                    << ",\"loaded\":" << veh_cargo_loaded(index) << '}'
                    << ",\"convoy_resource\":";
                if (veh.order == ORDER_CONVOY) {
                    out << json_string(veh.order_auto_type == RSC_NUTRIENT ? "nutrients"
                        : veh.order_auto_type == RSC_MINERAL ? "minerals"
                        : veh.order_auto_type == RSC_ENERGY ? "energy" : "unknown");
                } else out << "null";
                out
                    << ",\"convoy_amount\":"
                    << (veh.order == ORDER_CONVOY
                        ? contribution(index, veh.order_auto_type) : 0)
                    << ",\"home_base_ref\":";
                if (veh.home_base_id >= 0 && veh.home_base_id < *BaseCount) {
                    out << "\"base-location-"
                        << semantic_tile_id(Bases[veh.home_base_id].x, Bases[veh.home_base_id].y)
                        << "\"";
                } else out << "null";
                out << ",\"order_name\":" << json_string(semantic_unit_order_name(veh));
            }
            out << '}';
        }
        next_cursor = index < *VehCount ? index : -1;
    } else if (domain == "factions") {
        int index = std::max(1, cursor);
        for (; index < MaxPlayerNum && emitted < limit; ++index) {
            if (index != faction_id && (!is_alive(index)
            || !has_treaty(faction_id, index, DIPLO_COMMLINK))) continue;
            if (emitted++) out << ',';
            const int status = Factions[faction_id].diplo_status[index];
            const bool infiltrated = (status & DIPLO_HAVE_INFILTRATOR) != 0;
            const bool pact = (status & DIPLO_PACT) != 0;
            const bool governor_report = *GovernorFaction == faction_id
                && !MFactions[index].is_alien();
            const bool project_report = has_project(FAC_EMPATH_GUILD, faction_id);
            // These channels mirror the stock faction-profile/diplomacy
            // report predicates. Pact exposes energy reserves; infiltration
            // or the Planetary Governor's non-Progenitor report exposes the
            // wider faction intelligence surface. The external entitlement
            // boundary independently verifies the selected channel.
            const char* profile_channel = infiltrated ? "infiltration"
                : project_report ? "project_intelligence"
                : governor_report ? "governor" : "";
            const char* energy_channel = infiltrated ? "infiltration"
                : pact ? "pact_shared" : project_report ? "project_intelligence"
                : governor_report ? "governor" : "";
            out << "{\"id\":" << index << ",\"faction_ref\":\"faction-" << index
                << "\",\"owned\":" << (index == faction_id ? "true" : "false")
                << ",\"faction_name\":" << json_string(MFactions[index].formal_name_faction)
                << ",\"leader_name\":" << json_string(MFactions[index].name_leader)
                << ",\"relations\":{\"commlink\":"
                << ((status & DIPLO_COMMLINK) ? "true" : "false")
                << ",\"vendetta\":" << ((status & DIPLO_VENDETTA) ? "true" : "false")
                << ",\"truce\":" << ((status & DIPLO_TRUCE) ? "true" : "false")
                << ",\"treaty\":" << ((status & DIPLO_TREATY) ? "true" : "false")
                << ",\"pact\":" << ((status & DIPLO_PACT) ? "true" : "false")
                << ",\"infiltrated\":"
                << ((status & DIPLO_HAVE_INFILTRATOR) ? "true" : "false") << "}"
                << ",\"entitled_fields\":{"
                << "\"pact_shared_vision\":{\"value\":true,\"channel\":\"pact_shared\",\"owner_ref\":\"faction-"
                << index << "\"},"
                << "\"foreign_energy_credits\":{\"value\":" << Factions[index].energy_credits
                << ",\"channel\":" << json_string(energy_channel)
                << ",\"owner_ref\":\"faction-" << index << "\"},"
                << "\"foreign_research_technology_id\":{\"value\":" << Factions[index].tech_research_id
                << ",\"channel\":" << json_string(profile_channel)
                << ",\"owner_ref\":\"faction-" << index << "\"},"
                << "\"foreign_research_accumulated\":{\"value\":" << Factions[index].tech_accumulated
                << ",\"channel\":" << json_string(profile_channel)
                << ",\"owner_ref\":\"faction-" << index << "\"},"
                << "\"foreign_satellites\":{\"value\":{\"nutrient\":" << Factions[index].satellites_nutrient
                << ",\"mineral\":" << Factions[index].satellites_mineral
                << ",\"energy\":" << Factions[index].satellites_energy
                << ",\"orbital_defense\":" << Factions[index].satellites_ODP
                << "},\"channel\":" << json_string(profile_channel)
                << ",\"owner_ref\":\"faction-" << index << "\"}"
                << "}}";
        }
        next_cursor = index < MaxPlayerNum ? index : -1;
    } else {
        return error_response("invalid_world_domain",
            "Use summary, tiles, bases, units, or factions.");
    }
    out << "],\"next_cursor\":";
    if (next_cursor >= 0) out << next_cursor;
    else out << "null";
    out << ",\"bounded_native_page\":true}";
    return out.str();
}

std::string semantic_snapshot_response() {
    if (!game_active()) return status_response();
    refresh_deferred_end_turn_state();
    if (*MultiplayerActive && agent_modal_service_depth == 0) {
        pump_native_network_packets();
    }
    ensure_test_full_endgame_fixture();
    ensure_test_endgame_fixture();
    ensure_test_energy_gift_fixture();
    ensure_test_proposal_guard_fixture();
    ensure_test_incoming_vote_offer_fixture();
    ensure_test_joint_attack_counteroffer_fixture();
    ensure_test_technology_demand_fixture();
    ensure_test_diplomatic_purchase_fixture();
    ensure_test_base_purchase_fixture();
    ensure_test_base_obliteration_fixture();
    ensure_test_council_bargain_fixture();
    int faction_id = *CurrentPlayerFaction;
    Faction& faction = Factions[faction_id];
    int own_bases = 0;
    int own_units = 0;
    int ready_units = 0;
    for (int i = 0; i < *BaseCount; ++i) {
        if (Bases[i].faction_id == faction_id) ++own_bases;
    }
    for (int i = 0; i < *VehCount; ++i) {
        if (Vehs[i].faction_id == faction_id) {
            ++own_units;
            if (semantic_unit_requires_decision(i)) ++ready_units;
        }
    }
    const bool blind_research = *GameRules & RULES_BLIND_RESEARCH;
    int research_id = faction.tech_research_id;
    int research_cost = research_id >= 0 ? mod_tech_rate(faction_id) : -1;
    int research_progress = research_cost > 0
        ? clamp((100 * faction.tech_accumulated) / research_cost, 0, 100) : 0;
    int research_priority = faction.AI_growth ? TCAT_GROWTH
        : faction.AI_tech ? TCAT_TECH
        : faction.AI_wealth ? TCAT_WEALTH
        : faction.AI_power ? TCAT_POWER : -1;
    const CSocialCategory& selected =
        *reinterpret_cast<const CSocialCategory*>(&faction.SE_Politics_pending);
    const CSocialCategory& established =
        *reinterpret_cast<const CSocialCategory*>(&faction.SE_Politics);
    CSocialEffect social_effects;
    social_calc(const_cast<CSocialCategory*>(&selected), &social_effects, faction_id, false, false);
    const int native_time_control = *MultiplayerActive
        ? static_cast<int>(reinterpret_cast<signed char*>(0x90E8E0)[3]) : 0;
    std::ostringstream out;
    out << "{\"ok\":true,\"snapshot\":{\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str())
        << ",\"revision\":" << json_string(semantic_revision().c_str())
        << ",\"turn\":" << *CurrentTurn
        << ",\"year\":" << *CurrentMissionYear
        << ",\"faction\":{\"id\":" << faction_id
        << ",\"name\":" << json_string(MFactions[faction_id].formal_name_faction)
        << ",\"energy_credits\":" << faction.energy_credits
        << ",\"bases\":" << own_bases << ",\"units\":" << own_units
        << ",\"ready_units\":" << ready_units << '}'
        << ",\"game_settings\":{\"difficulty\":{\"id\":" << *DiffLevel
        << ",\"name\":" << json_string(lan_difficulty_name(*DiffLevel))
        << "},\"map\":{\"size_id\":" << *MapSizePlanet
        << ",\"size_name\":" << json_string(lan_map_size_name(*MapSizePlanet))
        << ",\"width\":" << *MapAreaX << ",\"height\":" << *MapAreaY
        << ",\"ocean_coverage\":" << *MapOceanCoverage
        << ",\"erosive_forces\":" << *MapErosiveForces
        << ",\"native_life\":" << *MapNativeLifeForms
        << ",\"cloud_cover\":" << *MapCloudCover << "},\"time_control\":{\"id\":"
        << native_time_control << ",\"name\":"
        << json_string(lan_time_control_name(native_time_control))
        << "},\"rules\":";
    append_named_game_rules(out, *GameRules, *GameState);
    char scenario_id[520] = {};
    bool scenario_launch = GetEnvironmentVariableA(
        "SMACX_AGENT_STARTUP_SCENARIO", scenario_id, sizeof(scenario_id));
    if (!scenario_launch) {
        scenario_launch = GetEnvironmentVariableA(
            "SMACX_AGENT_LAN_SCENARIO", scenario_id, sizeof(scenario_id));
    }
    scenario_launch = scenario_launch || ((*GameState & STATE_IS_SCENARIO) != 0);
    out << "},\"scenario\":{\"active\":" << (scenario_launch ? "true" : "false")
        << ",\"scenario_id\":";
    if (scenario_launch) out << json_string(scenario_id);
    else out << "null";
    out << ",\"forced_current_difficulty\":"
        << ((*GameRules & RULES_SCN_FORCE_CURRENT_DIFF_LEVEL) ? "true" : "false")
        << ",\"forced_current_faction\":"
        << ((*GameRules & RULES_SCN_FORCE_PLAYER_PLAY_CURRENT_FACT) ? "true" : "false")
        << ",\"technology_trading\":"
        << ((*GameRules & RULES_SCN_NO_TECH_TRADING) ? "false" : "true")
        << ",\"technology_advances\":"
        << ((*GameRules & RULES_SCN_NO_TECH_ADVANCES) ? "false" : "true")
        << ",\"colony_pods\":"
        << ((*GameRules & RULES_SCN_NO_COLONY_PODS) ? "false" : "true")
        << ",\"terraforming\":"
        << ((*GameRules & RULES_SCN_NO_TERRAFORMING) ? "false" : "true")
        << ",\"native_life\":"
        << ((*GameRules & RULES_SCN_NO_NATIVE_LIFE) ? "false" : "true")
        << ",\"secret_project_building\":"
        << ((*GameRules & RULES_SCN_NO_BUILDING_SP) ? "false" : "true")
        << ",\"planetary_council\":"
        << ((*GameMoreRules & MRULES_NO_PLANETARY_COUNCIL) ? "false" : "true")
        << ",\"social_engineering\":"
        << ((*GameMoreRules & MRULES_NO_SOCIAL_ENGINEERING) ? "false" : "true")
        << ",\"objective_required\":" << *ObjectiveReqVictory
        << ",\"objective_sudden_death\":" << *ObjectivesSuddenDeathVictory
        << ",\"ending_mission_year\":" << *EndingMissionYear << '}'
        << ",\"movement_rules\":{\"road_movement_scale\":" << Rules->move_rate_roads
        << ",\"road_edge_cost\":"
        << (conf.magtube_movement_rate > 0 ? conf.road_movement_rate : 1)
        << ",\"magtube_edge_cost\":"
        << (conf.magtube_movement_rate > 0 ? 1 : 0)
        << ",\"max_airdrop_range\":" << Rules->max_airdrop_rng_wo_orbital_insert
        << "}"
        << ",\"ecology\":{\"sea_level\":" << *MapSeaLevel
        << ",\"sea_level_council_pressure\":" << *MapSeaLevelCouncil
        << ",\"sunspot_duration\":" << *SunspotDuration
        << ",\"perihelion_active\":"
        << ((*GameState & STATE_PERIHELION_ACTIVE) ? "true" : "false")
        << ",\"volcano_erupted\":"
        << ((*GameState & STATE_VOLCANO_ERUPTED) ? "true" : "false") << '}'
        << ",\"own_planetary_state\":{\"tectonic_detonations\":"
        << TectonicDetonationCount[faction_id]
        << ",\"random_event_id\":" << faction.net_random_event
        << ",\"transcendent_thoughts\":" << faction.tech_count_transcendent << '}'
        << ",\"victory_posture\":{\"enabled\":{\"conquest\":"
        << ((*GameRules & RULES_VICTORY_CONQUEST) ? "true" : "false")
        << ",\"economic\":" << ((*GameRules & RULES_VICTORY_ECONOMIC) ? "true" : "false")
        << ",\"diplomatic\":" << ((*GameRules & RULES_VICTORY_DIPLOMATIC) ? "true" : "false")
        << ",\"transcendence\":" << ((*GameRules & RULES_VICTORY_TRANSCENDENCE) ? "true" : "false")
        << ",\"cooperative\":" << ((*GameRules & RULES_VICTORY_COOPERATIVE) ? "true" : "false")
        << "},\"economic\":{\"active\":" << (faction.corner_market_active() ? "true" : "false")
        << ",\"completion_turn\":" << faction.corner_market_turn
        << ",\"committed_energy\":" << faction.corner_market_cost << "}"
        << ",\"diplomatic\":{\"own_council_votes\":" << council_votes(faction_id)
        << ",\"governor\":" << (*GovernorFaction == faction_id ? "true" : "false") << "}"
        << ",\"transcendence\":{\"active\":"
        << (transcending(faction_id) ? "true" : "false") << "}"
        << ",\"scenario_objectives\":{\"owned_or_cooperative_count\":"
        << num_objectives(faction_id, *GameRules & RULES_VICTORY_COOPERATIVE)
        << ",\"required\":" << *ObjectiveReqVictory
        << ",\"sudden_death_required\":" << *ObjectivesSuddenDeathVictory << "}"
        << ",\"alien_crossfire\":{\"progenitor_faction\":"
        << (is_alien(faction_id) ? "true" : "false")
        << ",\"current_victory_type_id\":" << *GameVictoryType << "}}"
        << ",\"known_project_races\":[";
    bool race_comma = false;
    for (int project_id = SP_ID_First; project_id <= SP_ID_Last; ++project_id) {
        uint32_t word = 0;
        uint32_t bit = 0;
        bitmask(project_id - SP_ID_First, &word, &bit);
        if (word >= 8 || !(faction.secret_project_intel[word] & bit)) continue;
        if (race_comma) out << ',';
        race_comma = true;
        out << "{\"project_id\":" << project_id << ",\"name\":"
            << json_string(Facility[project_id].name);
        if (known_project_builder_valid[project_id]) {
            out << ",\"builder_ref\":\"faction-"
                << known_project_builder[project_id]
                << "\",\"builder_identity\":\"observed_report\"";
        } else {
            out << ",\"builder_identity\":\"unknown\"";
        }
        out << ",\"source\":\"public_report\"}";
    }
    out << ']'
        << ",\"governor_faction_id\":" << *GovernorFaction
        << ",\"intelligence_entitlements\":{\"empath_guild_reports\":"
        << (has_project(FAC_EMPATH_GUILD, faction_id) ? "true" : "false") << '}'
        << ",\"own_orbitals\":{\"nutrient\":" << faction.satellites_nutrient
        << ",\"mineral\":" << faction.satellites_mineral
        << ",\"energy\":" << faction.satellites_energy
        << ",\"orbital_defense\":" << faction.satellites_ODP << '}'
        << ",\"public_projects\":[";
    bool project_comma = false;
    for (int project_id = SP_ID_First; project_id <= SP_ID_Last; ++project_id) {
        const int project_base_id = SecretProjects[project_id - SP_ID_First];
        if (project_base_id < 0 || project_base_id >= *BaseCount) continue;
        if (project_comma) out << ',';
        project_comma = true;
        out << "{\"project_id\":" << project_id
            << ",\"name\":" << json_string(Facility[project_id].name)
            << ",\"owner_ref\":\"faction-"
            << Bases[project_base_id].faction_id << "\"}";
    }
    out << ']'
        << ",\"ready_unit_refs\":[";
    bool ready_comma = false;
    for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
        VEH& veh = Vehs[veh_id];
        if (veh.faction_id != faction_id || !semantic_unit_requires_decision(veh_id)) continue;
        if (ready_comma) out << ',';
        ready_comma = true;
        out << "{\"own_unit_ref\":" << json_string(
            (std::string("own-unit-")
             + std::to_string(semantic_vehicle_handle(veh_id))).c_str())
            << ",\"location_ref\":\"location-"
            << semantic_tile_id(veh.x, veh.y) << "\""
            << ",\"name\":" << json_string(veh.name())
            << ",\"roles\":{\"colony\":" << (veh.is_colony() ? "true" : "false")
            << ",\"former\":" << (veh.is_former() ? "true" : "false")
            << ",\"combat\":" << (veh.is_combat_unit() ? "true" : "false")
            << ",\"probe\":" << (veh.is_probe() ? "true" : "false")
            << ",\"supply\":" << (veh.is_supply() ? "true" : "false")
            << ",\"transport\":" << (veh.is_transport() ? "true" : "false")
            << "}}";
    }
    out << ']'
        << ",\"economy\":{\"allocation\":{\"economy\":"
        << 10 - faction.SE_alloc_labs - faction.SE_alloc_psych
        << ",\"psych\":" << faction.SE_alloc_psych
        << ",\"labs\":" << faction.SE_alloc_labs << "},\"turn_totals\":{"
        << "\"economy\":" << faction.energy_surplus_total
        << ",\"labs\":" << faction.labs_total << "}}"
        << ",\"research\":{\"enabled\":"
        << ((*GameRules & RULES_SCN_NO_TECH_ADVANCES) ? "false" : "true")
        << ",\"blind\":" << (blind_research ? "true" : "false")
        << ",\"tech_id\":";
    if (blind_research) {
        out << "null,\"tech_name\":null,\"priority\":" << research_priority
            << ",\"progress_percent\":" << research_progress
            << ",\"accumulated\":" << faction.tech_accumulated
            << ",\"cost\":" << research_cost;
    } else {
        out << research_id << ",\"tech_name\":"
            << json_string(research_id >= 0 && research_id < MaxTechnologyNum
                ? Tech[research_id].name : "None")
            << ",\"accumulated\":" << faction.tech_accumulated
            << ",\"cost\":" << research_cost;
    }
    out << '}'
        << ",\"social_engineering\":{\"enabled\":"
        << ((*GameMoreRules & MRULES_NO_SOCIAL_ENGINEERING) ? "false" : "true")
        << ",\"selected\":";
    append_social_models(out, selected);
    out << ",\"established\":";
    append_social_models(out, established);
    out << ",\"effective_ratings\":";
    append_social_effects(out, social_effects);
    out << ",\"upheaval_cost_paid_this_turn\":" << faction.SE_upheaval_cost_paid << '}'
        << ",\"last_deferred_action\":";
    if (!deferred_action.id) {
        out << "null";
    } else {
        out << "{\"action_id\":" << deferred_action.id
            << ",\"command\":" << json_string(deferred_action.command.c_str())
            << ",\"status\":" << json_string(deferred_action.status.c_str())
            << ",\"native_result\":" << deferred_action.native_result;
        if (!deferred_action.resolution.empty()) {
            out << ",\"resolution\":" << json_string(deferred_action.resolution.c_str());
        }
        if (deferred_action.unit_id >= 0) {
            // A completed destructive action may already have compacted VEH,
            // so its former row is neither a safe identity nor resolvable to
            // a current semantic handle. Locations retain the useful outcome
            // without publishing a raw or potentially reassigned row ID.
            out << ",\"origin_tile_id\":"
                << semantic_tile_id(deferred_action.origin_x, deferred_action.origin_y)
                << ",\"target_tile_id\":"
                << semantic_tile_id(deferred_action.target_x, deferred_action.target_y)
                << ",\"observed_tile_id\":"
                << semantic_tile_id(deferred_action.observed_x, deferred_action.observed_y);
        }
        out << '}';
    }
    out << ",\"last_council_result\":";
    if (!last_council_result_valid) {
        out << "null";
    } else {
        const char* result = last_council_result_state == 1 ? "passed"
            : last_council_result_state == -2 ? "vetoed" : "failed";
        out << "{\"proposal_id\":" << last_council_proposal_id
            << ",\"proposal_name\":"
            << json_string(last_council_proposal_id >= 0
                && last_council_proposal_id < MaxProposalNum
                ? Proposal[last_council_proposal_id].name : "Unknown")
            << ",\"player_ballot\":";
        if (last_council_proposal_id == PROP_ELECT_PLANETARY_GOVERNOR
        || last_council_proposal_id == PROP_UNITE_SUPREME_LEADER) {
            out << "{\"candidate_faction_id\":" << last_council_ballot_value << '}';
        } else {
            out << "{\"response\":"
                << json_string(last_council_ballot_value == -1 ? "yea" : "nay") << '}';
        }
        out << ",\"result\":" << json_string(result)
            << ",\"governor_faction_id\":" << last_council_governor_faction_id << '}';
    }
    BasePop* tracked_popup = agent_popup_object();
    BasePop* default_popup = active_default_popup();
    Win* modal_stack_popup = *ModalStackCurrent;
    BasePop* exec_popup = *BasePopExecCurrent;
    out
        << ",\"outcome\":{\"game_completed\":"
        << ((*GameState & STATE_GAME_DONE) ? "true" : "false")
        << ",\"final_score_completed\":"
        << ((*GameState & STATE_FINAL_SCORE_DONE) ? "true" : "false")
        << ",\"victory_type_id\":" << *GameVictoryType
        << ",\"victory_type\":"
        << json_string(semantic_victory_type_name(*GameVictoryType))
        << ",\"perspective_result\":"
        << json_string((*GameState & STATE_GAME_DONE)
            ? semantic_victory_result(*GameVictoryType) : "in_progress") << '}'
        << ",\"interaction\":{\"kind\":" << json_string(interaction_kind(faction_id).c_str())
        << ",\"popup_label\":" << json_string(semantic_popup_label())
        << ",\"instance_id\":" << agent_popup_generation()
        << ",\"can_command\":" << (human_turn_actionable(faction_id) ? "true" : "false")
        << ",\"modal\":" << ((*WinModalState || *PopupDialogState) ? "true" : "false")
        << ",\"engine_state\":{\"win_modal\":" << (*WinModalState ? "true" : "false")
        << ",\"popup_dialog\":" << (*PopupDialogState ? "true" : "false")
        << ",\"game_halted\":" << (*GameHalted ? "true" : "false")
        << ",\"human_diplomacy_settling\":"
        << (human_diplomacy_settling() ? "true" : "false")
        << ",\"popup_transition_pending\":"
        << (popup_transition_is_pending() ? "true" : "false")
        << ",\"tracked_popup_visible\":"
        << (tracked_popup && Win_is_visible(reinterpret_cast<Win*>(tracked_popup))
            ? "true" : "false")
        << ",\"default_popup_present\":" << (default_popup ? "true" : "false")
        << ",\"default_popup_visible\":"
        << (default_popup && Win_is_visible(reinterpret_cast<Win*>(default_popup))
            ? "true" : "false")
        << ",\"modal_stack_present\":" << (modal_stack_popup ? "true" : "false")
        << ",\"diplo_win_state\":" << (*DiploWinState ? "true" : "false")
        << ",\"diplo_win_visible\":"
        << (DiploWin && Win_is_visible(reinterpret_cast<Win*>(DiploWin))
            ? "true" : "false")
        << ",\"modal_stack_is_diplo_win\":"
        << (DiploWin && modal_stack_popup == reinterpret_cast<Win*>(DiploWin)
            ? "true" : "false")
        << ",\"basepop_exec_present\":" << (exec_popup ? "true" : "false")
        << ",\"tracked_is_default\":"
        << (tracked_popup && tracked_popup == default_popup ? "true" : "false")
        << ",\"tracked_is_modal_stack\":"
        << (tracked_popup && reinterpret_cast<Win*>(tracked_popup) == modal_stack_popup
            ? "true" : "false")
        << ",\"default_is_modal_stack\":"
        << (default_popup && reinterpret_cast<Win*>(default_popup) == modal_stack_popup
            ? "true" : "false")
        << ",\"tracked_is_basepop_exec\":"
        << (tracked_popup && tracked_popup == exec_popup ? "true" : "false")
        << ",\"default_is_basepop_exec\":"
        << (default_popup && default_popup == exec_popup ? "true" : "false")
        << ",\"basepop_exec_depth\":" << *BasePopExecDepth
        << ",\"basepop_fallout_latch\":" << *BasePopFalloutLatch
        << ",\"last_started_popup_label\":"
        << json_string(agent_popup_last_started_label())
        << ",\"research_selected\":"
        << (Factions[faction_id].tech_research_id >= 0 ? "true" : "false")
        << ",\"current_faction_id\":" << *CurrentFaction
        << ",\"research_lifecycle\":{\"local_selection_count\":"
        << agent_mp_local_research_selections
        << ",\"remote_wait_count\":" << agent_mp_remote_research_waits
        << ",\"blind_pick_count\":" << agent_mp_blind_research_picks
        << ",\"async_sync_count\":" << agent_mp_async_research_syncs
        << ",\"last_faction_id\":" << agent_mp_last_research_faction
        << ",\"last_priority\":" << agent_mp_last_research_priority << "}}}"
        << ",\"protocol\":{";
    append_turn_protocol(out, faction_id, ready_units);
    out << '}'
        << ",\"multiplayer_semantics\":{\"active\":"
        << (*MultiplayerActive ? "true" : "false")
        << ",\"policy\":\"fail_closed_allowlist\",\"validated_commands\":["
        << "\"acknowledge_popup:reviewed_information_only\","
        << "\"advance_technology_presentation:passive_native_window\","
        << "\"choose_research_priority:TECHRANDOM\","
        << "\"open_diplomacy:native_human_handshake_or_ai_channel\","
        << "\"propose_human_relationship:treaty_pact_or_truce\","
        << "\"propose_human_technology:exact_owned_technology\","
        << "\"propose_human_energy:bounded_owned_credits\","
        << "\"propose_human_joint_attack:exact_contacted_third_faction\","
        << "\"respond_human_diplomacy:accept_or_decline_complete_offer\","
        << "\"finish_human_diplomacy:native_end_transmission\","
        << "\"respond_to_contact:accept_or_decline_ai_channel\","
        << "\"continue_diplomacy:ai_greeting\","
        << "\"respond_to_diplomatic_offer:reject_technology_or_relationship_offer\","
        << "\"respond_to_diplomatic_offer:introduced_commlink_accept_or_reject\","
        << "\"choose_diplomacy_option:finish_ai_conversation\","
        << "\"move_unit:adjacent_safe_or_at_war_combat_v2\","
        << "\"skip_unit:native_synch_veh\","
        << "\"hold_unit:native_synch_veh\","
        << "\"sentry_unit:native_synch_veh\","
        << "\"end_turn:native_console_path\","
        << "\"set_production:native_synch_base_and_leader\","
        << "\"queue_production:native_synch_base_and_leader\","
        << "\"remove_queued_production:native_synch_base_and_leader\","
        << "\"clear_production_queue:native_synch_base_and_leader\","
        << "\"set_base_governor:native_synch_base_and_leader\","
        << "\"set_governor_permission:native_synch_base_and_leader\","
        << "\"convert_worker_to_specialist:native_synch_base_and_leader\","
        << "\"assign_specialist_to_tile:native_synch_base_and_leader\","
        << "\"set_specialist_type:native_synch_base_and_leader\","
        << "\"set_energy_allocation:native_synch_alloc\","
        << "\"set_research_priority:native_synch_ai\","
        << "\"set_social_engineering:native_synch_soc_and_net_energy\"],"
        << "\"mutations_allowed\":"
        << (*MultiplayerActive ? "\"allowlisted_only\"" : "true") << '}'
        << ",\"fair_play\":{\"perspective_faction\":" << faction_id
        << ",\"hidden_engine_state_excluded\":true}}}";
    return out.str();
}

std::string research_choices_response(int faction_id) {
    std::ostringstream out;
    out << "{\"ok\":true,\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str())
        << ",\"revision\":" << json_string(semantic_revision().c_str())
        << ",\"kind\":\"research\",\"blind\":"
        << ((*GameRules & RULES_BLIND_RESEARCH) ? "true" : "false") << ",\"choices\":[";
    bool comma = false;
    if (*GameRules & RULES_BLIND_RESEARCH) {
        const char* names[] = {"Explore", "Discover", "Build", "Conquer"};
        for (int priority = 0; priority < 4; ++priority) {
            if (priority) out << ',';
            out << "{\"id\":\"research_priority:" << priority
                << "\",\"command\":\"set_research_priority\",\"priority\":" << priority
                << ",\"name\":" << json_string(names[priority]) << '}';
        }
    } else {
        for (int tech_id = 0; tech_id < MaxTechnologyNum; ++tech_id) {
            if (!tech_avail(tech_id, faction_id)) continue;
            if (comma) out << ',';
            comma = true;
            out << "{\"id\":\"tech:" << tech_id << "\",\"tech_id\":" << tech_id
                << ",\"name\":" << json_string(Tech[tech_id].name)
                << ",\"category\":" << tech_category(tech_id) << '}';
        }
    }
    out << "]}";
    return out.str();
}

std::string energy_allocation_choices_response(int faction_id) {
    Faction& faction = Factions[faction_id];
    int economy = 10 - faction.SE_alloc_labs - faction.SE_alloc_psych;
    std::ostringstream out;
    out << "{\"ok\":true,\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str())
        << ",\"revision\":" << json_string(semantic_revision().c_str())
        << ",\"kind\":\"energy_allocation\",\"current\":{\"economy\":" << economy
        << ",\"psych\":" << faction.SE_alloc_psych
        << ",\"labs\":" << faction.SE_alloc_labs << "},"
        << "\"command\":\"set_energy_allocation\","
        << "\"constraints\":{\"unit\":\"tenths\",\"minimum_each\":0,"
        << "\"maximum_each\":10,\"required_sum\":10},"
        << "\"presets\":["
        << "{\"name\":\"balanced\",\"economy\":5,\"psych\":0,\"labs\":5},"
        << "{\"name\":\"research\",\"economy\":4,\"psych\":0,\"labs\":6},"
        << "{\"name\":\"economy\",\"economy\":6,\"psych\":0,\"labs\":4},"
        << "{\"name\":\"psych_support\",\"economy\":4,\"psych\":2,\"labs\":4}]}";
    return out.str();
}

std::string social_engineering_choices_response(int faction_id) {
    Faction& faction = Factions[faction_id];
    const CSocialCategory& selected =
        *reinterpret_cast<const CSocialCategory*>(&faction.SE_Politics_pending);
    const CSocialCategory& established =
        *reinterpret_cast<const CSocialCategory*>(&faction.SE_Politics);
    CSocialEffect effective;
    social_calc(const_cast<CSocialCategory*>(&selected), &effective, faction_id, false, false);
    int selected_total_cost = social_upheaval(faction_id, const_cast<CSocialCategory*>(&selected));
    std::ostringstream out;
    out << "{\"ok\":true,\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str())
        << ",\"revision\":" << json_string(semantic_revision().c_str())
        << ",\"kind\":\"social_engineering\",\"enabled\":"
        << ((*GameMoreRules & MRULES_NO_SOCIAL_ENGINEERING) ? "false" : "true")
        << ",\"energy_credits\":" << faction.energy_credits << ",\"selected\":";
    append_social_models(out, selected);
    out << ",\"established\":";
    append_social_models(out, established);
    out << ",\"effective_ratings\":";
    append_social_effects(out, effective);
    out << ",\"upheaval\":{\"target_total_cost\":" << selected_total_cost
        << ",\"paid_this_turn\":" << faction.SE_upheaval_cost_paid
        << ",\"additional_cost_for_selected\":"
        << selected_total_cost - faction.SE_upheaval_cost_paid
        << ",\"formula\":\"For N categories differing from established policy: (N+1)^3*difficulty; Alien factions pay 50% more. The amount already paid this turn is subtracted; reverting can refund it.\"}"
        << ",\"command\":{\"name\":\"set_social_engineering\","
        << "\"parameters\":[\"politics\",\"economics\",\"values\",\"future\"],"
        << "\"atomic\":true},\"categories\":[";
    for (int category = 0; category < MaxSocialCatNum; ++category) {
        if (category) out << ',';
        out << "{\"category_id\":" << category << ",\"key\":"
            << json_string(social_category_key(category)) << ",\"name\":"
            << json_string(SocialField[category].field_name) << ",\"options\":[";
        bool comma = false;
        for (int model = 0; model < MaxSocialModelNum; ++model) {
            if (!society_avail(category, model, faction_id)) continue;
            if (comma) out << ',';
            comma = true;
            CSocialCategory alternative = selected;
            alternative.models[category] = model;
            int total_cost = social_upheaval(faction_id, &alternative);
            out << "{\"model_id\":" << model << ",\"name\":"
                << json_string(SocialField[category].soc_name[model])
                << ",\"selected\":"
                << (selected.models[category] == model ? "true" : "false")
                << ",\"intrinsic_effects\":";
            append_social_effects(out, SocialField[category].soc_effect[model], true);
            out << ",\"target_total_cost_if_only_this_category_changes\":" << total_cost
                << ",\"additional_cost_if_only_this_category_changes\":"
                << total_cost - faction.SE_upheaval_cost_paid << '}';
        }
        out << "]}";
    }
    out << "]}";
    return out.str();
}

std::string production_choices_response(int faction_id, int base_id) {
    if (base_id < 0 || base_id >= *BaseCount || Bases[base_id].faction_id != faction_id) {
        return error_response("invalid_base", "base_id must identify a base owned by the human faction.");
    }
    set_base(base_id);
    base_compute(1);
    BASE& base = Bases[base_id];
    int current_item_cost = mineral_cost(base_id, base.queue_items[0]);
    int hurry_minerals = max(0, current_item_cost - base.minerals_accumulated);
    int full_hurry_cost = hurry_cost(base_id, base.queue_items[0], hurry_minerals);
    bool hurry_legal = !*MultiplayerActive && base.can_hurry_item()
        && hurry_minerals > 0 && full_hurry_cost > 0;
    int available_energy = max(0, Factions[faction_id].energy_credits
        - Factions[faction_id].hurry_cost_total);
    std::ostringstream out;
    out << "{\"ok\":true,\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str())
        << ",\"revision\":" << json_string(semantic_revision().c_str())
        << ",\"kind\":\"production\",\"base_id\":" << base_id
        << ",\"base_name\":" << json_string(base.name)
        << ",\"current\":{\"item_id\":" << base.queue_items[0]
        << ",\"name\":" << json_string(production_name(base.queue_items[0]).c_str())
        << ",\"mineral_cost\":" << current_item_cost
        << ",\"minerals_accumulated\":" << base.minerals_accumulated
        << ",\"mineral_surplus\":" << base.mineral_surplus << "},"
        << "\"queue\":{" << "\"entries\":" << base.queue_size + 1
        << ",\"capacity\":10,\"append_command\":\"queue_production\"},"
        << "\"hurry\":{\"legal\":" << (hurry_legal ? "true" : "false")
        << ",\"affordable\":" << (hurry_legal && full_hurry_cost <= available_energy ? "true" : "false")
        << ",\"minerals_added\":" << hurry_minerals
        << ",\"energy_cost\":" << full_hurry_cost
        << ",\"available_energy\":" << available_energy << "},\"choices\":[";
    bool comma = false;
    for (int unit_id = 0; unit_id < MaxProtoNum; ++unit_id) {
        if (!Units[unit_id].name[0] || !mod_veh_avail(unit_id, faction_id, base_id)
        || !can_build_unit(base_id, unit_id)) continue;
        if (comma) out << ',';
        comma = true;
        out << "{\"id\":\"production:" << unit_id
            << "\",\"command\":\"set_production\",\"base_id\":" << base_id
            << ",\"item_id\":" << unit_id
            << ",\"kind\":\"unit\",\"name\":" << json_string(Units[unit_id].name)
            << ",\"mineral_cost\":" << mineral_cost(base_id, unit_id) << '}';
    }
    for (int facility_id = Fac_ID_First; facility_id <= SP_ID_Last; ++facility_id) {
        int item_id = -facility_id;
        if (!can_build(base_id, facility_id)) continue;
        if (comma) out << ',';
        comma = true;
        out << "{\"id\":\"production:" << item_id
            << "\",\"command\":\"set_production\",\"base_id\":" << base_id
            << ",\"item_id\":" << item_id
            << ",\"kind\":" << json_string(facility_id >= SP_ID_First ? "project" : "facility")
            << ",\"name\":" << json_string(Facility[facility_id].name)
            << ",\"mineral_cost\":" << mineral_cost(base_id, item_id) << '}';
    }
    if (hurry_legal && full_hurry_cost <= available_energy) {
        if (comma) out << ',';
        out << "{\"id\":\"hurry_production:" << base_id
            << "\",\"command\":\"hurry_production\",\"base_id\":" << base_id
            << ",\"energy_cost\":" << full_hurry_cost
            << ",\"minerals_added\":" << hurry_minerals
            << ",\"meaning\":\"Pay the full native hurry cost to complete current production.\"}";
    }
    out << "]}";
    return out.str();
}

std::string base_management_choices_response(int faction_id, int base_id) {
    ensure_test_base_action_fixture();
    ensure_test_base_obliteration_fixture();
    if (base_id < 0 || base_id >= *BaseCount || Bases[base_id].faction_id != faction_id) {
        return error_response("invalid_base", "base_id must identify a base owned by the human faction.");
    }
    set_base(base_id);
    base_compute(1);
    BASE& base = Bases[base_id];
    std::ostringstream out;
    out << "{\"ok\":true,\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str())
        << ",\"revision\":" << json_string(semantic_revision().c_str())
        << ",\"kind\":\"base_management\",\"base_id\":" << base_id
        << ",\"base_name\":" << json_string(base.name)
        << ",\"current\":{\"governor\":{\"active\":"
        << ((base.governor_flags & GOV_ACTIVE) ? "true" : "false")
        << ",\"manage_citizens\":"
        << ((base.governor_flags & GOV_MANAGE_CITIZENS) ? "true" : "false")
        << ",\"manage_production\":"
        << ((base.governor_flags & GOV_MANAGE_PRODUCTION) ? "true" : "false")
        << ",\"new_units_automated\":"
        << ((base.governor_flags & GOV_NEW_VEH_FULLY_AUTO) ? "true" : "false")
        << ",\"priorities\":{\"explore\":"
        << ((base.governor_flags & GOV_PRIORITY_EXPLORE) ? "true" : "false")
        << ",\"discover\":" << ((base.governor_flags & GOV_PRIORITY_DISCOVER) ? "true" : "false")
        << ",\"build\":" << ((base.governor_flags & GOV_PRIORITY_BUILD) ? "true" : "false")
        << ",\"conquer\":" << ((base.governor_flags & GOV_PRIORITY_CONQUER) ? "true" : "false")
        << "},\"permissions\":";
    append_governor_permissions(out, base.governor_flags);
    out << "},\"nerve_stapling\":{\"turns_left\":"
        << static_cast<int>(base.nerve_staple_turns_left)
        << ",\"attempt_count\":" << base.nerve_staple_count
        << ",\"police_allows\":" << (can_staple(base_id) ? "true" : "false")
        << "},\"facility_recycled_this_turn\":"
        << ((base.state_flags & BSTATE_FACILITY_SCRAPPED) ? "true" : "false")
        << ",\"queue\":[";
    for (int position = 0; position <= base.queue_size && position < 10; ++position) {
        if (position) out << ',';
        int item_id = base.queue_items[position];
        out << "{\"position\":" << position << ",\"item_id\":" << item_id
            << ",\"name\":" << json_string(production_name(item_id).c_str()) << '}';
    }
    out << "]},\"choices\":[";
    if (*MultiplayerActive) {
        bool comma = true;
        bool governor_active = base.governor_flags & GOV_ACTIVE;
        bool governor_citizens = base.governor_flags & GOV_MANAGE_CITIZENS;
        bool governor_production = base.governor_flags & GOV_MANAGE_PRODUCTION;
        out << "{\"id\":\"base:governor_toggle_active\","
            << "\"command\":\"set_base_governor\",\"base_id\":" << base_id
            << ",\"active\":" << (governor_active ? 0 : 1)
            << ",\"manage_citizens\":" << (governor_citizens ? 1 : 0)
            << ",\"manage_production\":" << (governor_production ? 1 : 0)
            << ",\"current_active\":" << (governor_active ? "true" : "false")
            << ",\"meaning\":\"Toggle only the governor's master active state while preserving its management areas.\"}";
        for (const GovernorPermissionSpec& permission : GovernorPermissions) {
            bool enabled = base.governor_flags & permission.mask;
            out << ",{\"id\":\"base:governor_permission:" << permission.key
                << ':' << (enabled ? 0 : 1)
                << "\",\"command\":\"set_governor_permission\",\"base_id\":"
                << base_id << ",\"governor_permission\":"
                << json_string(permission.key)
                << ",\"active\":" << (enabled ? 0 : 1)
                << ",\"current\":" << (enabled ? "true" : "false")
                << ",\"meaning\":" << json_string(permission.meaning) << '}';
        }
        int queue_position = base.queue_size + 1;
        if (base.queue_size < 9) {
            for (int unit_id = 0; unit_id < MaxProtoNum; ++unit_id) {
                if (!Units[unit_id].name[0]
                || !production_item_buildable(
                    faction_id, base_id, unit_id, queue_position)) continue;
                if (comma) out << ',';
                comma = true;
                out << "{\"id\":\"base:queue_append:" << unit_id
                    << "\",\"command\":\"queue_production\",\"base_id\":" << base_id
                    << ",\"item_id\":" << unit_id << ",\"kind\":\"unit\",\"name\":"
                    << json_string(Units[unit_id].name)
                    << ",\"queue_position\":" << queue_position << '}';
            }
            for (int facility_id = Fac_ID_First;
            facility_id <= SP_ID_Last; ++facility_id) {
                int item_id = -facility_id;
                if (!production_item_buildable(
                    faction_id, base_id, item_id, queue_position)) continue;
                if (comma) out << ',';
                comma = true;
                out << "{\"id\":\"base:queue_append:" << item_id
                    << "\",\"command\":\"queue_production\",\"base_id\":" << base_id
                    << ",\"item_id\":" << item_id << ",\"kind\":"
                    << json_string(facility_id >= SP_ID_First ? "project" : "facility")
                    << ",\"name\":" << json_string(Facility[facility_id].name)
                    << ",\"queue_position\":" << queue_position << '}';
            }
        }
        for (int position = 1; position <= base.queue_size && position < 10; ++position) {
            if (comma) out << ',';
            comma = true;
            int item_id = base.queue_items[position];
            out << "{\"id\":\"base:queue_remove:" << position
                << "\",\"command\":\"remove_queued_production\",\"base_id\":"
                << base_id << ",\"queue_position\":" << position
                << ",\"item_id\":" << item_id << ",\"name\":"
                << json_string(production_name(item_id).c_str()) << '}';
        }
        if (base.queue_size > 0) {
            if (comma) out << ',';
            out << "{\"id\":\"base:queue_clear\","
                << "\"command\":\"clear_production_queue\",\"base_id\":"
                << base_id << ",\"removed_count\":" << base.queue_size << '}';
        }
        out << "]}";
        return out.str();
    }
    bool governor_active = base.governor_flags & GOV_ACTIVE;
    bool governor_citizens = base.governor_flags & GOV_MANAGE_CITIZENS;
    bool governor_production = base.governor_flags & GOV_MANAGE_PRODUCTION;
    out << "{\"id\":\"base:rename\",\"command\":\"rename_base\","
        << "\"parameters\":{\"name\":{\"type\":\"string\",\"min_length\":1,\"max_length\":24}},"
        << "\"supported_in_multiplayer\":false},"
        << "{\"id\":\"base:governor_toggle_active\","
        << "\"command\":\"set_base_governor\",\"base_id\":" << base_id
        << ",\"active\":" << (governor_active ? 0 : 1)
        << ",\"manage_citizens\":" << (governor_citizens ? 1 : 0)
        << ",\"manage_production\":" << (governor_production ? 1 : 0)
        << ",\"meaning\":\"Toggle the governor master state while preserving both management areas.\"},"
        << "{\"id\":\"base:governor_toggle_citizens\","
        << "\"command\":\"set_base_governor\",\"base_id\":" << base_id
        << ",\"active\":" << (governor_active ? 1 : 0)
        << ",\"manage_citizens\":" << (governor_citizens ? 0 : 1)
        << ",\"manage_production\":" << (governor_production ? 1 : 0)
        << ",\"meaning\":\"Toggle citizen management while preserving the other governor controls.\"},"
        << "{\"id\":\"base:governor_toggle_production\","
        << "\"command\":\"set_base_governor\",\"base_id\":" << base_id
        << ",\"active\":" << (governor_active ? 1 : 0)
        << ",\"manage_citizens\":" << (governor_citizens ? 1 : 0)
        << ",\"manage_production\":" << (governor_production ? 0 : 1)
        << ",\"meaning\":\"Toggle production management while preserving the other governor controls.\"},"
        << "{\"id\":\"base:governor_permissions\",\"kind\":\"information\","
        << "\"meaning\":\"The following exact toggles control what this governor and the faction default for new bases may choose.\"}";
    for (const GovernorPermissionSpec& permission : GovernorPermissions) {
        bool enabled = base.governor_flags & permission.mask;
        bool recalculate = (base.governor_flags & (GOV_ACTIVE | GOV_MANAGE_PRODUCTION))
            == (GOV_ACTIVE | GOV_MANAGE_PRODUCTION);
        out << ",{\"id\":\"base:governor_permission:" << permission.key
            << ':' << (enabled ? 0 : 1)
            << "\",\"command\":\"set_governor_permission\",\"base_id\":" << base_id
            << ",\"governor_permission\":" << json_string(permission.key)
            << ",\"active\":" << (enabled ? 0 : 1)
            << ",\"current\":" << (enabled ? "true" : "false")
            << ",\"will_recalculate_production\":"
            << (recalculate ? "true" : "false")
            << ",\"affects_future_new_bases\":true,\"meaning\":"
            << json_string(permission.meaning) << '}';
    }
    int queue_position = base.queue_size + 1;
    if (base.queue_size < 9) {
        for (int unit_id = 0; unit_id < MaxProtoNum; ++unit_id) {
            if (!Units[unit_id].name[0]
            || !production_item_buildable(
                faction_id, base_id, unit_id, queue_position)) continue;
            out << ",{\"id\":\"base:queue_append:" << unit_id
                << "\",\"command\":\"queue_production\",\"base_id\":" << base_id
                << ",\"item_id\":" << unit_id << ",\"kind\":\"unit\",\"name\":"
                << json_string(Units[unit_id].name)
                << ",\"queue_position\":" << queue_position << '}';
        }
        for (int facility_id = Fac_ID_First; facility_id <= SP_ID_Last; ++facility_id) {
            int item_id = -facility_id;
            if (!production_item_buildable(
                faction_id, base_id, item_id, queue_position)) continue;
            out << ",{\"id\":\"base:queue_append:" << item_id
                << "\",\"command\":\"queue_production\",\"base_id\":" << base_id
                << ",\"item_id\":" << item_id << ",\"kind\":"
                << json_string(facility_id >= SP_ID_First ? "project" : "facility")
                << ",\"name\":" << json_string(Facility[facility_id].name)
                << ",\"queue_position\":" << queue_position << '}';
        }
    }
    for (int position = 1; position <= base.queue_size && position < 10; ++position) {
        int item_id = base.queue_items[position];
        out << ",{\"id\":\"base:queue_remove:" << position
            << "\",\"command\":\"remove_queued_production\",\"base_id\":"
            << base_id << ",\"queue_position\":" << position
            << ",\"item_id\":" << item_id << ",\"name\":"
            << json_string(production_name(item_id).c_str()) << '}';
    }
    if (base.queue_size > 0) {
        out << ",{\"id\":\"base:queue_clear\","
            << "\"command\":\"clear_production_queue\",\"base_id\":"
            << base_id
            << ",\"meaning\":\"Remove all future entries while retaining current production.\"}";
    }
    if (can_staple(base_id) && !base.nerve_staple_turns_left) {
        out << ",{\"id\":\"base:nerve_staple\",\"command\":\"nerve_staple\"," 
            << "\"base_id\":" << base_id
            << ",\"confirm_atrocity\":1,\"atrocity\":true,"
            << "\"meaning\":\"Commit a nerve-stapling atrocity at this base. Native success, duration, sanctions, and notices remain authoritative.\"}";
    }
    for (int facility_id = Fac_ID_First; facility_id <= Fac_ID_Last; ++facility_id) {
        if (!facility_recyclable_at_base(faction_id, base_id, facility_id)) continue;
        int refund = facility_recycle_refund(base, facility_id);
        out << ",{\"id\":\"base:recycle:" << facility_id
            << "\",\"command\":\"recycle_facility\",\"base_id\":" << base_id
            << ",\"facility_id\":" << facility_id
            << ",\"facility_name\":" << json_string(Facility[facility_id].name)
            << ",\"energy_refund\":" << refund
            << ",\"confirm_recycle\":1,\"destructive\":true,\"meaning\":"
            << json_string(refund
                ? "Permanently scrap this built facility for the exact native energy refund; only one facility may be recycled at this base this turn."
                : "Permanently destroy this artifact-linked facility for no refund; only one facility may be recycled at this base this turn.")
            << '}';
    }
    int obliteration_unit_id = -1;
    for (int unit_id = veh_at(base.x, base.y); unit_id >= 0;
    unit_id = Vehs[unit_id].next_veh_id_stack) {
        if (Vehs[unit_id].faction_id == faction_id) {
            obliteration_unit_id = unit_id;
            break;
        }
    }
    if (!*MultiplayerActive && obliteration_unit_id >= 0 && !is_objective(base_id)) {
        int former_faction_id = base.faction_id_former;
        bool atrocity = Rules->tgl_oblit_base_atrocity != 0;
        out << ",{\"id\":\"base:obliterate:" << base_id
            << "\",\"command\":\"obliterate_base\",\"base_id\":" << base_id
            << ",\"unit_id\":" << obliteration_unit_id
            << ",\"population\":" << static_cast<int>(base.pop_size)
            << ",\"former_faction_id\":" << former_faction_id;
        if (former_faction_id >= 1 && former_faction_id < MaxPlayerNum) {
            out << ",\"former_faction_name\":"
                << json_string(MFactions[former_faction_id].formal_name_faction);
        }
        out << ",\"confirmation_follows\":true,"
            << "\"atrocity_under_current_rules\":" << (atrocity ? "true" : "false")
            << ",\"destructive\":false,\"consequential\":false,\"meaning\":"
            << json_string(atrocity
                ? "Open the native confirmation for permanently obliterating this non-objective owned base. No destruction occurs until the separate semantic proceed choice explicitly accepts obliteration and atrocity consequences."
                : "Open the native confirmation for permanently obliterating this non-objective owned base. No destruction occurs until the separate semantic proceed choice explicitly confirms it.")
            << '}';
    }
    out << "]}";
    return out.str();
}

bool specialist_available_at_base(const BASE& base, int citizen_id) {
    return citizen_id >= 0 && citizen_id < MaxSpecialistNum
        && has_tech(Citizen[citizen_id].preq_tech, base.faction_id)
        && !has_tech(Citizen[citizen_id].obsol_tech, base.faction_id)
        && (Citizen[citizen_id].psych_bonus >= 2
            || base.pop_size >= Rules->min_base_size_specialists);
}

std::string base_citizen_choices_response(int faction_id, int base_id) {
    if (base_id < 0 || base_id >= *BaseCount || Bases[base_id].faction_id != faction_id) {
        return error_response("invalid_base", "base_id must identify a base owned by the human faction.");
    }
    set_base(base_id);
    base_compute(1);
    BASE& base = Bases[base_id];
    bool governor_manages = (base.governor_flags & (GOV_ACTIVE | GOV_MANAGE_CITIZENS))
        == (GOV_ACTIVE | GOV_MANAGE_CITIZENS);
    std::ostringstream out;
    out << "{\"ok\":true,\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str())
        << ",\"revision\":" << json_string(semantic_revision().c_str())
        << ",\"kind\":\"base_citizens\",\"base_id\":" << base_id
        << ",\"base_name\":" << json_string(base.name)
        << ",\"governor_manages_citizens\":" << (governor_manages ? "true" : "false")
        << ",\"population\":" << static_cast<int>(base.pop_size)
        << ",\"specialists\":[";
    for (int specialist = 0; specialist < base.specialist_total; ++specialist) {
        if (specialist) out << ',';
        int citizen_id = clamp(base.specialist_type(specialist), 0, MaxSpecialistNum - 1);
        out << "{\"specialist_index\":" << specialist << ",\"citizen_id\":" << citizen_id
            << ",\"name\":" << json_string(Citizen[citizen_id].singular_name) << '}';
    }
    out << "],\"available_specialist_types\":[";
    bool specialist_comma = false;
    for (int citizen_id = 0; citizen_id < MaxSpecialistNum; ++citizen_id) {
        if (!specialist_available_at_base(base, citizen_id)) continue;
        if (specialist_comma) out << ',';
        specialist_comma = true;
        out << "{\"citizen_id\":" << citizen_id << ",\"name\":"
            << json_string(Citizen[citizen_id].singular_name)
            << ",\"economy\":" << Citizen[citizen_id].econ_bonus
            << ",\"psych\":" << Citizen[citizen_id].psych_bonus
            << ",\"labs\":" << Citizen[citizen_id].labs_bonus << '}';
    }
    out << "],\"tiles\":[";
    bool tile_comma = false;
    for (int tile_index = 0; tile_index < 21; ++tile_index) {
        int x = 0;
        int y = 0;
        MAP* sq = next_tile(base.x, base.y, tile_index, &x, &y);
        if (!sq || !sq->is_visible(faction_id)) continue;
        if (tile_comma) out << ',';
        tile_comma = true;
        bool worked = base.worked_tiles & (1 << tile_index);
        int blocked_flags = BaseTileFlags[tile_index]
            & (BR_NOT_AVAILABLE | BR_NOT_VISIBLE | BR_BASE_IN_TILE
                | BR_VEH_IN_TILE | BR_FOREIGN_TILE | BR_WORKER_ACTIVE);
        bool assignable = tile_index > 0 && (!blocked_flags || worked);
        out << "{\"tile_index\":" << tile_index
            << ",\"tile_id\":" << semantic_tile_id(x, y)
            << ",\"worked\":" << (worked ? "true" : "false")
            << ",\"assignable\":" << (assignable ? "true" : "false")
            << ",\"yields\":{\"nutrients\":"
            << mod_crop_yield(faction_id, base_id, x, y, 0)
            << ",\"minerals\":" << mod_mine_yield(faction_id, base_id, x, y, 0)
            << ",\"energy\":" << mod_energy_yield(faction_id, base_id, x, y, 0) << "}}";
    }
    out << "],\"choices\":[";
    if (governor_manages) {
        out << "{\"id\":\"citizens:governor_managed\",\"kind\":\"capability_status\","
            << "\"supported\":false,\"meaning\":\"Disable citizen management with set_base_governor before assigning workers or specialists manually.\"}";
    } else {
        bool choice_comma = false;
        for (int tile_index = 1; tile_index < 21; ++tile_index) {
            if (!(base.worked_tiles & (1 << tile_index))) continue;
            for (int citizen_id = 0; citizen_id < MaxSpecialistNum; ++citizen_id) {
                if (!specialist_available_at_base(base, citizen_id)
                || base.specialist_total >= MaxBaseSpecNum) continue;
                if (choice_comma) out << ',';
                choice_comma = true;
                out << "{\"id\":\"citizens:worker_to_specialist:" << tile_index
                    << ':' << citizen_id
                    << "\",\"command\":\"convert_worker_to_specialist\",\"base_id\":"
                    << base_id << ",\"tile_index\":" << tile_index
                    << ",\"citizen_id\":" << citizen_id << ",\"specialist_name\":"
                    << json_string(Citizen[citizen_id].singular_name) << '}';
            }
        }
        for (int specialist = 0; specialist < base.specialist_total; ++specialist) {
            for (int tile_index = 1; tile_index < 21; ++tile_index) {
                int x = 0;
                int y = 0;
                MAP* sq = next_tile(base.x, base.y, tile_index, &x, &y);
                int blocked_flags = BaseTileFlags[tile_index]
                    & (BR_NOT_AVAILABLE | BR_NOT_VISIBLE | BR_BASE_IN_TILE
                        | BR_VEH_IN_TILE | BR_FOREIGN_TILE | BR_WORKER_ACTIVE);
                if (!sq || !sq->is_visible(faction_id) || blocked_flags
                || (base.worked_tiles & (1 << tile_index))) continue;
                if (choice_comma) out << ',';
                choice_comma = true;
                out << "{\"id\":\"citizens:specialist_to_worker:" << specialist
                    << ':' << tile_index
                    << "\",\"command\":\"assign_specialist_to_tile\",\"base_id\":"
                    << base_id << ",\"specialist_index\":" << specialist
                    << ",\"tile_index\":" << tile_index
                    << ",\"tile_id\":" << semantic_tile_id(x, y) << '}';
            }
            int current_type = base.specialist_type(specialist);
            for (int citizen_id = 0; citizen_id < MaxSpecialistNum; ++citizen_id) {
                if (citizen_id == current_type
                || !specialist_available_at_base(base, citizen_id)) continue;
                if (choice_comma) out << ',';
                choice_comma = true;
                out << "{\"id\":\"citizens:change_specialist:" << specialist
                    << ':' << citizen_id
                    << "\",\"command\":\"set_specialist_type\",\"base_id\":"
                    << base_id << ",\"specialist_index\":" << specialist
                    << ",\"citizen_id\":" << citizen_id << ",\"specialist_name\":"
                    << json_string(Citizen[citizen_id].singular_name) << '}';
            }
        }
        if (!choice_comma) {
            out << "{\"id\":\"citizens:no_manual_change\",\"kind\":\"capability_status\","
                << "\"supported\":false,\"meaning\":\"No legal manual citizen reassignment is available at this base now.\"}";
        }
    }
    out << "]}";
    return out.str();
}

bool multiplayer_safe_move_target(int faction_id, int veh_id,
int target_x, int target_y) {
    if (faction_id < 1 || veh_id < 0 || veh_id >= *VehCount
    || Vehs[veh_id].faction_id != faction_id
    || !semantic_unit_requires_decision(veh_id)) return false;
    VEH& veh = Vehs[veh_id];
    MAP* sq = mapsq(target_x, target_y);
    if (!sq || !sq->is_visible(faction_id) || goody_at(target_x, target_y)) {
        return false;
    }
    int direction = -1;
    for (int dir = 0; dir < 8; ++dir) {
        if (wrap(veh.x + BaseOffsetX[dir]) == target_x
        && veh.y + BaseOffsetY[dir] == target_y) direction = dir;
    }
    if (direction < 0) return false;
    // This first validated LAN movement family excludes every transition
    // which could open contact, combat, territory, pod, capture, or boarding
    // continuations. Those become separate named interaction regressions.
    int base_id = base_at(target_x, target_y);
    if (base_id >= 0 && Bases[base_id].faction_id != faction_id) return false;
    if (sq->owner >= 1 && sq->owner != faction_id) return false;
    for (int other_id = veh_at(target_x, target_y); other_id >= 0;
    other_id = Vehs[other_id].next_veh_id_stack) {
        if (Vehs[other_id].faction_id != faction_id) return false;
    }
    return veh.triad() == TRIAD_AIR || sq->is_base()
        || (is_ocean(sq) == (veh.triad() == TRIAD_SEA));
}

bool multiplayer_combat_move_target(int faction_id, int veh_id,
int target_x, int target_y, int* target_faction_id = NULL) {
    if (faction_id < 1 || veh_id < 0 || veh_id >= *VehCount
    || Vehs[veh_id].faction_id != faction_id
    || !semantic_unit_requires_decision(veh_id)
    || !Vehs[veh_id].is_combat_unit()) return false;
    VEH& attacker = Vehs[veh_id];
    MAP* sq = mapsq(target_x, target_y);
    if (!sq || !sq->is_visible(faction_id) || goody_at(target_x, target_y)) {
        return false;
    }
    bool adjacent = false;
    for (int dir = 0; dir < 8; ++dir) {
        adjacent |= wrap(attacker.x + BaseOffsetX[dir]) == target_x
            && attacker.y + BaseOffsetY[dir] == target_y;
    }
    if (!adjacent) return false;
    bool terrain_compatible = attacker.triad() == TRIAD_AIR || sq->is_base()
        || (is_ocean(sq) == (attacker.triad() == TRIAD_SEA));
    if (!terrain_compatible) return false;
    for (int target_id = veh_at(target_x, target_y); target_id >= 0;
    target_id = Vehs[target_id].next_veh_id_stack) {
        int other = Vehs[target_id].faction_id;
        if (other != faction_id && !has_pact(faction_id, other)
        && at_war(faction_id, other)
        && (Vehs[target_id].visibility & (1 << faction_id))) {
            if (target_faction_id) *target_faction_id = other;
            return true;
        }
    }
    return false;
}

std::string multiplayer_unit_choices_response(int faction_id, int veh_id) {
    if (veh_id < 0 || veh_id >= *VehCount
    || Vehs[veh_id].faction_id != faction_id) {
        return error_response("invalid_unit",
            "unit_id must identify a unit owned by the local human faction.");
    }
    if (!human_turn_actionable(faction_id)) return semantic_not_actionable();
    VEH& veh = Vehs[veh_id];
    std::ostringstream out;
    out << "{\"ok\":true,\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str())
        << ",\"revision\":" << json_string(semantic_revision().c_str())
        << ",\"kind\":\"unit_actions\",\"unit_id\":" << veh_id
        << ",\"unit_name\":" << json_string(veh.name())
        << ",\"at\":{\"tile_id\":" << semantic_tile_id(veh.x, veh.y)
        << "},\"multiplayer_validation\":\"adjacent_move_and_combat_v2\",\"choices\":[";
    bool comma = false;
    if (semantic_unit_requires_decision(veh_id)) {
        for (int dir = 0; dir < 8; ++dir) {
            int x = wrap(veh.x + BaseOffsetX[dir]);
            int y = veh.y + BaseOffsetY[dir];
            bool safe_move = multiplayer_safe_move_target(
                faction_id, veh_id, x, y);
            int target_faction = -1;
            bool combat_move = multiplayer_combat_move_target(
                faction_id, veh_id, x, y, &target_faction);
            if (!safe_move && !combat_move) continue;
            if (comma) out << ',';
            comma = true;
            MAP* sq = mapsq(x, y);
            out << "{\"id\":\"move:" << semantic_tile_id(x, y)
                << "\",\"command\":\"move_unit\",\"unit_id\":" << veh_id
                << ",\"target_tile_id\":" << semantic_tile_id(x, y)
                << ",\"direction_id\":" << dir
                << ",\"known\":true,\"visible_now\":true,\"safe_local_move\":"
                << (safe_move ? "true" : "false")
                << ",\"combat\":" << (combat_move ? "true" : "false");
            if (combat_move) {
                out << ",\"target_faction_id\":" << target_faction
                    << ",\"already_at_war\":true,\"consequential\":true";
            }
            out
                << ",\"is_ocean\":" << (is_ocean(sq) ? "true" : "false")
                << ",\"features\":" << item_names(sq->items) << '}';
        }
        if (comma) out << ',';
        comma = true;
        out << "{\"id\":\"skip:" << veh_id
            << "\",\"command\":\"skip_unit\",\"unit_id\":" << veh_id
            << ",\"meaning\":\"Spend this unit's remaining movement for the current turn only.\"},"
            << "{\"id\":\"hold:" << veh_id
            << "\",\"command\":\"hold_unit\",\"unit_id\":" << veh_id
            << ",\"meaning\":\"Hold indefinitely until explicitly activated.\"},"
            << "{\"id\":\"sentry:" << veh_id
            << "\",\"command\":\"sentry_unit\",\"unit_id\":" << veh_id
            << ",\"meaning\":\"Sentry until the native game wakes the unit for nearby danger.\"}";
    }
    if (comma) out << ',';
    out << "{\"id\":\"multiplayer:remaining_unit_actions\","
        << "\"kind\":\"capability_status\",\"supported\":false,"
        << "\"meaning\":\"Visible adjacent safe movement and already-at-war conventional combat are validated. Contact, treaty-breaking hostility, pods, capture, and boarding remain separate capability families.\"}"
        << "],\"ready\":"
        << (semantic_unit_requires_decision(veh_id) ? "true" : "false") << '}';
    return out.str();
}

std::string unit_choices_response(int faction_id, int veh_id, int base_target_id = -1,
int target_tile_id = -1, int target_unit_id = -1) {
    ensure_test_terrain_destruction_fixture();
    ensure_test_single_unit_upgrade_fixture();
    ensure_test_return_home_fixture();
    ensure_test_air_automation_fixture();
    ensure_test_bombing_run_fixture();
    if (veh_id < 0 || veh_id >= *VehCount || Vehs[veh_id].faction_id != faction_id) {
        return error_response("invalid_unit", "unit_id must identify a unit owned by the human faction.");
    }
    VEH& veh = Vehs[veh_id];
    int target_x = -1;
    int target_y = -1;
    bool target_tile_valid = semantic_tile_coords(target_tile_id, &target_x, &target_y);
    int boarded_transport_id = -1;
    if (veh.order == ORDER_SENTRY_BOARD && veh.waypoint_x[0] >= 0
    && veh.waypoint_x[0] < *VehCount && veh.waypoint_x[0] != veh_id) {
        VEH& transport = Vehs[veh.waypoint_x[0]];
        if (transport.faction_id == faction_id && transport.x == veh.x
        && transport.y == veh.y && (veh_cargo(veh.waypoint_x[0]) > 0
            || semantic_aircraft_boarded_on(veh_id, veh.waypoint_x[0]))) {
            boarded_transport_id = veh.waypoint_x[0];
        }
    }
    std::ostringstream out;
    out << "{\"ok\":true,\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str())
        << ",\"revision\":" << json_string(semantic_revision().c_str())
        << ",\"kind\":\"unit_actions\",\"unit_id\":" << veh_id
        << ",\"unit_name\":" << json_string(veh.name()) << ",\"at\":{\"tile_id\":"
        << semantic_tile_id(veh.x, veh.y) << "},\"roles\":{\"colony\":"
        << (veh.is_colony() ? "true" : "false") << ",\"former\":"
        << (veh.is_former() ? "true" : "false") << ",\"combat\":"
        << (veh.is_combat_unit() ? "true" : "false") << ",\"probe\":"
        << (veh.is_probe() ? "true" : "false") << ",\"supply\":"
        << (veh.is_supply() ? "true" : "false") << ",\"transport\":"
        << (veh.is_transport() ? "true" : "false") << ",\"artillery\":"
        << (can_arty(veh.unit_id, true) ? "true" : "false")
        << ",\"missile\":" << (veh.is_missile() ? "true" : "false")
        << ",\"missile_kind\":" << json_string(missile_kind(veh))
        << ",\"air_interceptor\":"
        << (semantic_air_defense_eligible(veh) ? "true" : "false")
        << ",\"carrier\":"
        << (semantic_carrier_capacity(veh_id) > 0 ? "true" : "false")
        << ",\"airdrop_capable\":" << (has_abil(veh.unit_id, ABL_DROP_POD) ? "true" : "false")
        << ",\"boarded\":" << (boarded_transport_id >= 0 ? "true" : "false")
        << ",\"designated_defender\":"
        << ((veh.state & VSTATE_DESIGNATE_DEFENDER) ? "true" : "false")
        << "},\"order\":{\"id\":"
        << static_cast<int>(veh.order) << ",\"name\":"
        << json_string(semantic_unit_order_name(veh)) << "},\"choices\":[";
    bool comma = false;
    if (semantic_carrier_capacity(veh_id) > 0
    && semantic_carrier_dependency_count(veh_id) > 0) {
        out << "{\"id\":\"carrier_recovery_lock:" << veh_id
            << "\",\"kind\":\"rule_status\",\"supported\":true,"
            << "\"carrier_unit_id\":" << veh_id
            << ",\"inbound_aircraft\":" << semantic_carrier_inbound_count(veh_id)
            << ",\"unboarded_co_located_aircraft\":"
            << semantic_carrier_unboarded_count(veh_id)
            << ",\"meaning\":\"This carrier is mechanically held until every inbound aircraft arrives and boards or cancels, and every co-located unboarded aircraft boards or moves away.\"}"
            << "],\"ready\":false,\"reason\":\"carrier_recovery_lock\"}";
        return out.str();
    }
    if (boarded_transport_id >= 0
    && semantic_carrier_capacity(boarded_transport_id) > 0) {
        out << "{\"id\":\"leave_carrier:" << veh_id
            << "\",\"command\":\"activate_unit\",\"unit_id\":" << veh_id
            << ",\"transport_unit_id\":" << boarded_transport_id
            << ",\"meaning\":\"Leave the carrier deck at its current tile and reactivate this refueled aircraft.\"},"
            << "{\"id\":\"remain_boarded:" << veh_id
            << "\",\"command\":\"remain_boarded\",\"unit_id\":" << veh_id
            << ",\"transport_unit_id\":" << boarded_transport_id
            << ",\"meaning\":\"Spend this aircraft's remaining turn while keeping it aboard the carrier.\"}"
            << "],\"ready\":true,\"reason\":\"boarded_carrier\"}";
        return out.str();
    }
    if (boarded_transport_id >= 0 && Vehs[boarded_transport_id].triad() == TRIAD_SEA) {
        for (int dir = 0; dir < 8; ++dir) {
            int x = wrap(veh.x + BaseOffsetX[dir]);
            int y = veh.y + BaseOffsetY[dir];
            MAP* sq = mapsq(x, y);
            if (!sq || !is_known(x, y, faction_id)
            || (is_ocean(sq) && sq->base_who() < 0)) continue;
            bool blocked = false;
            if (sq->is_visible(faction_id)) {
                int owner = sq->veh_who();
                blocked = owner >= 0 && owner != faction_id
                    && !has_pact(faction_id, owner) && !veh.is_combat_unit()
                    && !veh.is_probe();
            }
            if (blocked) continue;
            if (comma) out << ',';
            comma = true;
            int destination_tile_id = semantic_tile_id(x, y);
            out << "{\"id\":\"disembark:" << destination_tile_id
                << "\",\"command\":\"disembark_unit\",\"unit_id\":" << veh_id
                << ",\"transport_unit_id\":" << boarded_transport_id
                << ",\"target_tile_id\":" << destination_tile_id
                << ",\"known\":true,\"meaning\":\"Wake this passenger and make one native move from its sea transport onto adjacent land.\"}";
        }
        if (comma) out << ',';
        out << "{\"id\":\"remain_boarded:" << veh_id
            << "\",\"command\":\"remain_boarded\",\"unit_id\":" << veh_id
            << ",\"transport_unit_id\":" << boarded_transport_id
            << ",\"meaning\":\"Spend this passenger's remaining turn while keeping it aboard.\"}";
        out << "],\"ready\":true"
            << ",\"reason\":\"boarded_transport\"}";
        return out.str();
    }
    if (veh.state & VSTATE_EXPLORE) {
        out << "{\"id\":\"activate:" << veh_id
            << "\",\"command\":\"activate_unit\",\"unit_id\":" << veh_id
            << ",\"meaning\":\"Cancel native automated exploration and return this unit to direct semantic control.\"}"
            << "],\"ready\":false,\"reason\":\"auto_explore\"}";
        return out.str();
    }
    if (semantic_native_automation_active(veh)) {
        const char* automation = semantic_unit_order_name(veh);
        out << "{\"id\":\"activate:" << veh_id
            << "\",\"command\":\"activate_unit\",\"unit_id\":" << veh_id
            << ",\"meaning\":" << json_string(veh.is_former()
                ? "Cancel this native automated-former policy and return the former to direct semantic control."
                : veh.order_auto_type == ORDERA_BOMBING_RUN
                    ? "Cancel this native bombing-run policy and return the aircraft to direct semantic control."
                    : "Cancel this native On Alert automation and return the unit to direct semantic control.")
            << "}],\"ready\":false,\"reason\":" << json_string(automation) << '}';
        return out.str();
    }
    if (veh.order != ORDER_NONE && !veh_jail(veh_id)) {
        out << "{\"id\":\"activate:" << veh_id
            << "\",\"command\":\"activate_unit\",\"unit_id\":" << veh_id
            << ",\"meaning\":\"Cancel the persistent order and reactivate the unit under native movement rules.\"}"
            << "],\"ready\":false,\"reason\":\"persistent_order\"}";
        return out.str();
    }
    if (!veh_unmoved(veh_id) && veh.triad() == TRIAD_AIR
    && !veh.is_missile() && veh.range()) {
        int co_located_carrier_id = -1;
        for (int carrier_id = veh_at(veh.x, veh.y); carrier_id >= 0;
        carrier_id = Vehs[carrier_id].next_veh_id_stack) {
            if (carrier_id != veh_id && Vehs[carrier_id].faction_id == faction_id
            && semantic_carrier_capacity(carrier_id) > 0) {
                co_located_carrier_id = carrier_id;
                if (carrier_id == target_unit_id) break;
            }
        }
        if (co_located_carrier_id >= 0) {
            if (target_unit_id < 0) {
                out << "{\"id\":\"carrier_target:query\",\"kind\":\"carrier_target_query\","
                    << "\"legal\":null,\"parameters\":[\"target_unit_id\"],"
                    << "\"requirements\":\"The co-located owned carrier that this aircraft must board before it moves.\","
                    << "\"meaning\":\"This aircraft has no movement remaining, but boarding is still legal. Query the co-located carrier object.\"}"
                    << "],\"ready\":true,\"reason\":\"carrier_boarding_required\"}";
                return out.str();
            }
            bool exact_carrier = target_unit_id == co_located_carrier_id;
            bool capacity_available = exact_carrier
                && veh_cargo_loaded(target_unit_id)
                    + semantic_carrier_inbound_count(target_unit_id, veh_id)
                    < semantic_carrier_capacity(target_unit_id);
            if (capacity_available) {
                out << "{\"id\":\"board_carrier:" << veh_id << ':' << target_unit_id
                    << "\",\"command\":\"board_carrier\",\"unit_id\":" << veh_id
                    << ",\"target_unit_id\":" << target_unit_id
                    << ",\"carrier_name\":" << json_string(Vehs[target_unit_id].name())
                    << ",\"capacity\":" << semantic_carrier_capacity(target_unit_id)
                    << ",\"loaded\":" << veh_cargo_loaded(target_unit_id)
                    << ",\"inbound_reserved\":"
                    << semantic_carrier_inbound_count(target_unit_id, veh_id)
                    << ",\"fuel_safe\":true,\"movement_required\":false,"
                    << "\"meaning\":\"Board and refuel this arrived aircraft even though it has no movement remaining.\"}"
                    << "],\"ready\":true,\"reason\":\"carrier_boarding_required\"}";
            } else {
                out << "{\"id\":\"carrier_target:invalid\",\"kind\":\"carrier_target_query\","
                    << "\"legal\":false,\"target_unit_id\":" << target_unit_id
                    << ",\"reason\":\"The target is not the co-located owned carrier or has no unreserved deck capacity.\"}"
                    << "],\"ready\":false,\"reason\":\"carrier_boarding_blocked\"}";
            }
            return out.str();
        }
    }
    if (!veh_unmoved(veh_id)) {
        if (boarded_transport_id >= 0 && Vehs[boarded_transport_id].triad() == TRIAD_SEA) {
            for (int dir = 0; dir < 8; ++dir) {
                int x = wrap(veh.x + BaseOffsetX[dir]);
                int y = veh.y + BaseOffsetY[dir];
                MAP* sq = mapsq(x, y);
                if (!sq || !is_known(x, y, faction_id)
                || (is_ocean(sq) && sq->base_who() < 0)) continue;
                bool blocked = false;
                if (sq->is_visible(faction_id)) {
                    int owner = sq->veh_who();
                    blocked = owner >= 0 && owner != faction_id
                        && !has_pact(faction_id, owner) && !veh.is_combat_unit()
                        && !veh.is_probe();
                }
                if (blocked) continue;
                if (comma) out << ',';
                comma = true;
                int destination_tile_id = semantic_tile_id(x, y);
                out << "{\"id\":\"disembark:" << destination_tile_id
                    << "\",\"command\":\"disembark_unit\",\"unit_id\":" << veh_id
                    << ",\"transport_unit_id\":" << boarded_transport_id
                    << ",\"target_tile_id\":" << destination_tile_id
                    << ",\"known\":true,\"meaning\":\"Wake this passenger and make one native move from its sea transport onto adjacent land.\"}";
            }
        } else if (veh.order != ORDER_NONE && !veh_jail(veh_id)) {
            out << "{\"id\":\"activate:" << veh_id
                << "\",\"command\":\"activate_unit\",\"unit_id\":" << veh_id
                << ",\"meaning\":\"Cancel the persistent order and reactivate the unit under native movement rules.\"}";
        }
        out << "],\"ready\":false,\"reason\":"
            << json_string(boarded_transport_id >= 0 ? "boarded_transport"
                : veh_jail(veh_id) ? "in_transport_cannot_disembark_here"
                : veh.order != ORDER_NONE ? "persistent_order" : "unit_not_ready") << '}';
        return out.str();
    }
    if (veh.is_missile()) {
        if (target_tile_id >= 0) {
            std::string reason;
            bool legal = target_tile_valid && missile_target_legal(
                faction_id, veh_id, target_x, target_y, &reason);
            if (!target_tile_valid) reason = "invalid_tile_id";
            if (legal) {
                out << "{\"id\":\"missile_launch:" << target_tile_id
                    << "\",\"command\":\"launch_missile\","
                    << "\"unit_id\":" << veh_id << ",\"target_tile_id\":"
                    << target_tile_id << ",\"missile_kind\":"
                    << json_string(missile_kind(veh))
                    << ",\"range\":" << map_range(veh.x, veh.y, target_x, target_y)
                    << ",\"consumes_unit\":true";
                if (veh.is_planet_buster()) {
                    out << ",\"confirm_atrocity\":1,\"atrocity\":true,"
                        << "\"blast_radius\":" << veh.reactor_type();
                }
                out << ",\"meaning\":" << json_string(veh.is_planet_buster()
                    ? "Launch this Planet Buster through the native atrocity and defense routines. The missile is consumed and every tile in the reactor-sized blast may be destroyed."
                    : veh.plan() == PLAN_TECTONIC_MISSILE
                        ? "Launch this Tectonic Missile at the reviewed visible empty tile; native orbital/flechette defense and terrain effects decide the result."
                    : veh.plan() == PLAN_FUNGAL_MISSILE
                        ? "Launch this Fungal Missile at the reviewed visible empty tile; native defense, fungus, improvement destruction, and life spawning decide the result."
                    : "Launch this conventional missile against the visible at-war stack through native combat. The missile is consumed.")
                    << '}';
            } else {
                out << "{\"id\":\"missile_target:invalid\","
                    << "\"kind\":\"target_query\",\"legal\":false,\"reason\":"
                    << json_string(reason.c_str())
                    << ",\"meaning\":\"Choose another candidate from fair-play visible state and query it before launching.\"}";
            }
            comma = true;
        } else {
            out << "{\"id\":\"missile_target:query\",\"kind\":\"target_query\","
                << "\"legal\":null,\"parameters\":[\"target_tile_id\"],\"max_range\":"
                << missile_launch_range(veh_id) << ",\"missile_kind\":"
                << json_string(missile_kind(veh))
                << ",\"requirements\":" << json_string(veh.is_planet_buster()
                    ? "Query a visible target whose blast contains a visible at-war asset and no visible owned or pact asset."
                    : veh.plan() == PLAN_TECTONIC_MISSILE || veh.plan() == PLAN_FUNGAL_MISSILE
                        ? "Query a visible empty tile in own, neutral, or at-war territory."
                        : "Query the coordinates of a visible at-war unit stack.")
                << ",\"meaning\":\"Call unit_actions again with this unit_id and one target_tile_id from fair-play tile state to obtain an exact guarded launch tuple.\"}";
            comma = true;
        }
    }
    for (int dir = 0; dir < 8; ++dir) {
        int x = wrap(veh.x + BaseOffsetX[dir]);
        int y = veh.y + BaseOffsetY[dir];
        MAP* sq = mapsq(x, y);
        if (!sq) continue;
        bool visible = sq->is_visible(faction_id);
        bool visible_non_pact_unit = false;
        bool friendly_transport_space = false;
        if (visible) {
            for (int other_id = veh_at(x, y); other_id >= 0;
            other_id = Vehs[other_id].next_veh_id_stack) {
                VEH& other = Vehs[other_id];
                if (other.faction_id == faction_id && veh_cargo(other_id) > 0
                && veh_cargo_loaded(other_id) < veh_cargo(other_id)) {
                    friendly_transport_space = true;
                }
                if (other.faction_id != faction_id
                && !has_pact(faction_id, other.faction_id)) {
                    visible_non_pact_unit = true;
                }
            }
            bool terrain_compatible = veh.triad() == TRIAD_AIR || sq->is_base()
                || (is_ocean(sq) == (veh.triad() == TRIAD_SEA));
            bool can_board = veh.triad() == TRIAD_LAND && is_ocean(sq)
                && friendly_transport_space;
            if ((!terrain_compatible && !can_board)
            || (visible_non_pact_unit && !veh.is_combat_unit() && !veh.is_probe())) {
                continue;
            }
        }
        if (comma) out << ',';
        comma = true;
        int destination_tile_id = semantic_tile_id(x, y);
        out << "{\"id\":\"move:" << destination_tile_id
            << "\",\"command\":\"move_unit\",\"unit_id\":" << veh_id
            << ",\"target_tile_id\":" << destination_tile_id
            << ",\"direction_id\":" << dir
            << ",\"known\":" << (is_known(x, y, faction_id) ? "true" : "false")
            << ",\"visible_now\":" << (visible ? "true" : "false");
        if (visible) {
            out << ",\"is_ocean\":" << (is_ocean(sq) ? "true" : "false")
                << ",\"features\":" << item_names(sq->items)
                << ",\"may_initiate_combat_or_contact\":"
                << (visible_non_pact_unit ? "true" : "false")
                << ",\"boards_transport\":"
                << (friendly_transport_space && veh.triad() == TRIAD_LAND ? "true" : "false");
        }
        out << '}';
    }
    if (comma) out << ',';
    out << "{\"id\":\"skip:" << veh_id
        << "\",\"command\":\"skip_unit\",\"unit_id\":" << veh_id << '}';
    out << ",{\"id\":\"hold:" << veh_id
        << "\",\"command\":\"hold_unit\",\"unit_id\":" << veh_id
        << ",\"meaning\":\"Hold indefinitely until explicitly activated.\"}"
        << ",{\"id\":\"sentry:" << veh_id
        << "\",\"command\":\"sentry_unit\",\"unit_id\":" << veh_id
        << ",\"meaning\":\"Sentry until the native game wakes the unit for nearby danger.\"}";
    if (target_tile_id < 0) {
        out << ",{\"id\":\"tile_target:query\",\"kind\":\"tile_target_query\","
            << "\"legal\":null,\"parameters\":[\"target_tile_id\"],"
            << "\"meaning\":\"Choose one fair-play tile_id, then call unit_actions again to obtain exact persistent route and patrol choices without coordinates.\"}";
    } else {
        std::string route_reason;
        bool route_legal = target_tile_valid && semantic_go_to_tile_eligible(
            faction_id, veh_id, target_x, target_y, &route_reason);
        if (!target_tile_valid) route_reason = "unknown_or_invalid_tile";
        if (!route_legal) {
            out << ",{\"id\":\"tile_target:invalid\",\"kind\":\"tile_target_query\","
                << "\"legal\":false,\"target_tile_id\":" << target_tile_id
                << ",\"reason\":" << json_string(route_reason.c_str()) << '}';
        } else {
            out << ",{\"id\":\"go_to_tile:" << veh_id << ':' << target_tile_id
                << "\",\"command\":\"go_to\",\"unit_id\":" << veh_id
                << ",\"target_tile_id\":" << target_tile_id
                << ",\"persistent\":true";
            if (veh.triad() == TRIAD_AIR && !veh.is_missile() && veh.range()) {
                bool destination_refuels = semantic_friendly_air_refuel_tile(
                    faction_id, target_x, target_y);
                out << ",\"fuel_safe\":true,\"destination_refuels\":"
                    << (destination_refuels ? "true" : "false")
                    << ",\"remaining_safe_range\":" << semantic_air_safe_range(veh_id)
                    << ",\"route_kind\":" << json_string(
                        destination_refuels ? "air_recovery" : "air_round_trip");
            }
            out << ",\"meaning\":\"Set a persistent native go-to order to this exact known tile object after triad and aircraft fuel-safety validation.\"}";
            if (valid_patrol(veh_id, target_x, target_y)) {
                out << ",{\"id\":\"patrol_tile:" << veh_id << ':' << target_tile_id
                    << "\",\"command\":\"patrol_unit\",\"unit_id\":" << veh_id
                    << ",\"target_tile_id\":" << target_tile_id
                    << ",\"persistent\":true,\"meaning\":\"Create a native repeating patrol to this exact rule-compatible known tile object.\"}";
            }
        }
    }
    int return_base_id = semantic_return_base_candidate(faction_id, veh_id);
    if (!*MultiplayerActive && return_base_id >= 0) {
        out << ",{\"id\":\"return_to_base:" << veh_id << ':' << return_base_id
            << "\",\"command\":\"return_to_base\",\"unit_id\":" << veh_id
            << ",\"base_id\":" << return_base_id << ",\"base_name\":"
            << json_string(Bases[return_base_id].name)
            << ",\"distance\":" << map_range(veh.x, veh.y,
                Bases[return_base_id].x, Bases[return_base_id].y)
            << ",\"persistent\":true,\"native_route_selection\":true,"
            << "\"meaning\":\"Order this land or sea unit to return to the native-selected nearest known friendly base without supplying map coordinates.\"}";
    }
    if (base_target_id >= 0) {
        std::string reason;
        if (semantic_go_to_base_eligible(faction_id, veh_id, base_target_id, &reason)) {
            BASE& base = Bases[base_target_id];
            out << ",{\"id\":\"go_to_base:" << veh_id << ':' << base_target_id
                << "\",\"command\":\"go_to_base\",\"unit_id\":" << veh_id
                << ",\"base_id\":" << base_target_id << ",\"base_name\":"
                << json_string(base.name) << ",\"distance\":"
                << map_range(veh.x, veh.y, base.x, base.y)
                << ",\"known_owned_base\":true,\"persistent\":true";
            if (veh.triad() == TRIAD_AIR) {
                out << ",\"fuel_safe\":true,\"fuel_limited\":"
                    << (veh.range() ? "true" : "false");
                if (veh.range()) out << ",\"remaining_safe_range\":"
                    << semantic_air_safe_range(veh_id);
            }
            out << ",\"meaning\":\"Route this unit to the exact known owned base by game object ID; the bridge resolves its location and validates terrain and aircraft fuel safety.\"}";
        } else {
            out << ",{\"id\":\"base_target:invalid\",\"kind\":\"base_target_query\"," 
                << "\"legal\":false,\"base_id\":" << base_target_id
                << ",\"reason\":" << json_string(reason.c_str())
                << ",\"meaning\":\"Choose another owned base_id from fair-play base state and query it before routing.\"}";
        }
    } else if (semantic_has_base_destination(faction_id, veh_id)) {
        out << ",{\"id\":\"base_target:query\",\"kind\":\"base_target_query\"," 
            << "\"legal\":null,\"parameters\":[\"base_id\"],"
            << "\"requirements\":\"A different known owned base compatible with this unit's triad and, for aircraft, within remaining safe fuel range.\"," 
            << "\"meaning\":\"Call unit_actions again with this unit_id and one base_id from smac_list(kind=bases) to obtain an exact guarded coordinate-free route action.\"}";
    }
    if (veh.triad() == TRIAD_AIR && !veh.is_missile() && veh.range()) {
        if (target_unit_id >= 0) {
            std::string carrier_reason;
            bool owned_carrier = target_unit_id < *VehCount
                && target_unit_id != veh_id
                && Vehs[target_unit_id].faction_id == faction_id
                && semantic_carrier_capacity(target_unit_id) > 0;
            bool co_located = owned_carrier
                && Vehs[target_unit_id].x == veh.x
                && Vehs[target_unit_id].y == veh.y;
            if (co_located && veh_unmoved(veh_id) && veh.order == ORDER_NONE
            && veh_cargo_loaded(target_unit_id)
                + semantic_carrier_inbound_count(target_unit_id, veh_id)
                < semantic_carrier_capacity(target_unit_id)) {
                out << ",{\"id\":\"board_carrier:" << veh_id << ':' << target_unit_id
                    << "\",\"command\":\"board_carrier\",\"unit_id\":" << veh_id
                    << ",\"target_unit_id\":" << target_unit_id
                    << ",\"carrier_name\":" << json_string(Vehs[target_unit_id].name())
                    << ",\"capacity\":" << semantic_carrier_capacity(target_unit_id)
                    << ",\"loaded\":" << veh_cargo_loaded(target_unit_id)
                    << ",\"inbound_reserved\":"
                    << semantic_carrier_inbound_count(target_unit_id, veh_id)
                    << ",\"fuel_safe\":true,\"meaning\":\"Board this co-located aircraft onto the exact carrier object and refuel it.\"}";
            } else if (!co_located && semantic_carrier_recovery_eligible(
                faction_id, veh_id, target_unit_id, &carrier_reason)) {
                int carrier_distance = map_range(veh.x, veh.y,
                    Vehs[target_unit_id].x, Vehs[target_unit_id].y);
                out << ",{\"id\":\"recover_to_carrier:" << veh_id << ':'
                    << target_unit_id << "\",\"command\":\"recover_to_carrier\","
                    << "\"unit_id\":" << veh_id
                    << ",\"target_unit_id\":" << target_unit_id
                    << ",\"carrier_name\":" << json_string(Vehs[target_unit_id].name())
                    << ",\"target_tile_id\":"
                    << semantic_tile_id(Vehs[target_unit_id].x, Vehs[target_unit_id].y)
                    << ",\"distance\":" << carrier_distance
                    << ",\"remaining_safe_range\":" << semantic_air_safe_range(veh_id)
                    << ",\"capacity\":" << semantic_carrier_capacity(target_unit_id)
                    << ",\"loaded\":" << veh_cargo_loaded(target_unit_id)
                    << ",\"inbound_reserved\":"
                    << semantic_carrier_inbound_count(target_unit_id, veh_id)
                    << ",\"fuel_safe\":true,\"persistent\":true,"
                    << "\"carrier_will_be_held\":true,\"route_kind\":\"carrier_recovery\","
                    << "\"meaning\":\"Reserve one deck slot, hold this exact carrier in place, and route the aircraft to its current tile. Board after arrival.\"}";
            } else {
                if (!owned_carrier) carrier_reason = "target_unit_id must identify an owned carrier";
                else if (co_located) carrier_reason =
                    "the co-located carrier has no unreserved aircraft capacity";
                out << ",{\"id\":\"carrier_target:invalid\",\"kind\":\"carrier_target_query\","
                    << "\"legal\":false,\"target_unit_id\":" << target_unit_id
                    << ",\"reason\":" << json_string(carrier_reason.c_str())
                    << ",\"meaning\":\"Choose another owned carrier from fair-play unit state or free deck capacity before retrying.\"}";
            }
        } else {
            bool has_carrier = false;
            for (int carrier_id = 0; carrier_id < *VehCount; ++carrier_id) {
                if (carrier_id != veh_id && Vehs[carrier_id].faction_id == faction_id
                && semantic_carrier_capacity(carrier_id) > 0) has_carrier = true;
            }
            if (has_carrier) {
                out << ",{\"id\":\"carrier_target:query\",\"kind\":\"carrier_target_query\","
                    << "\"legal\":null,\"parameters\":[\"target_unit_id\"],"
                    << "\"requirements\":\"An owned carrier with unreserved deck capacity that is co-located or within remaining safe fuel range.\","
                    << "\"meaning\":\"Call unit_actions again with this aircraft unit_id and one carrier target_unit_id from smac_list(kind=units) to obtain an exact guarded board or recovery action.\"}";
            }
        }
    }
    if (*MultiplayerActive && veh.triad() == TRIAD_AIR && !veh.is_missile()
    && veh.is_combat_unit()) {
        out << ",{\"id\":\"bombing_run:multiplayer_unvalidated\","
            "\"kind\":\"capability_status\",\"supported\":false,"
            "\"meaning\":\"Persistent bombing runs are withheld in LAN games until their native packet path is validated end to end.\"}";
    } else if (semantic_bombing_run_unit_eligible(faction_id, veh_id)) {
        for (int base_id = 0; base_id < *BaseCount; ++base_id) {
            BASE& target = Bases[base_id];
            std::string bombing_reason;
            if (!semantic_bombing_run_target_eligible(faction_id, veh_id,
                target.x, target.y, &bombing_reason)) continue;
            int target_id = semantic_tile_id(target.x, target.y);
            out << ",{\"id\":\"bombing_run:" << veh_id << ':' << target_id
                << "\",\"command\":\"set_bombing_run\",\"unit_id\":" << veh_id
                << ",\"target_tile_id\":" << target_id
                << ",\"target_base_name\":" << json_string(target.name)
                << ",\"target_faction_id\":" << static_cast<int>(target.faction_id)
                << ",\"target_faction_name\":"
                << json_string(MFactions[target.faction_id].formal_name_faction)
                << ",\"distance\":"
                << map_range(veh.x, veh.y, target.x, target.y)
                << ",\"remaining_safe_range\":" << semantic_air_safe_range(veh_id)
                << ",\"persistent\":true,\"native_automation\":true,"
                "\"fuel_policy\":\"non_sacrificial_round_trip\","
                "\"meaning\":\"Assign the native repeating bombing-run policy to this currently visible Vendetta base. Activate the aircraft later to cancel it.\"}";
        }
    }
    if (veh.is_combat_unit()) {
        out << ",{\"id\":\"auto_explore:" << veh_id
            << "\",\"command\":\"auto_explore_unit\",\"unit_id\":" << veh_id
            << ",\"persistent\":true,\"native_automation\":true,"
            << "\"meaning\":\"Delegate this combat unit's ongoing exploration to the normal native Explore order until activated again.\"}";
        if (!*MultiplayerActive) {
            bool designated = veh.state & VSTATE_DESIGNATE_DEFENDER;
            out << ",{\"id\":\"designated_defender:" << veh_id << ':'
                << (designated ? 0 : 1)
                << "\",\"command\":\"set_designated_defender\",\"unit_id\":" << veh_id
                << ",\"active\":" << (designated ? 0 : 1)
                << ",\"current\":" << (designated ? "true" : "false")
                << ",\"consumes_turn\":false,\"native_role\":true,"
                << "\"meaning\":" << json_string(designated
                    ? "Remove this unit's native designated-defender preference without consuming its turn."
                    : "Mark this unit as a preferred native stack defender without consuming its turn.")
                << '}';
            out << ",{\"id\":\"on_alert:" << veh_id
                << "\",\"command\":\"set_unit_on_alert\",\"unit_id\":" << veh_id
                << ",\"persistent\":true,\"native_automation\":true,"
                << "\"meaning\":\"Place this combat unit under the native On Alert policy until explicitly activated.\"}";
            if (semantic_air_defense_eligible(veh)) {
                out << ",{\"id\":\"air_defense:" << veh_id
                    << "\",\"command\":\"automate_air_defense\",\"unit_id\":" << veh_id
                    << ",\"persistent\":true,\"native_automation\":true,"
                    << "\"meaning\":\"Delegate this Air Superiority aircraft to the native Automate Air Defense policy until explicitly activated.\"}";
            }
        }
    }
    if (veh.is_former() && !*MultiplayerActive) {
        struct FormerModeChoice {
            int id;
            const char* key;
            const char* meaning;
        };
        const FormerModeChoice modes[] = {
            {ORDERA_TERRA_AUTO_FULL, "full", "Let the native former AI select general improvements and destinations."},
            {ORDERA_TERRA_AUTO_ROAD, "roads", "Let the native former AI prioritize road construction."},
            {ORDERA_TERRA_AUTO_MAGTUBE, "magtubes", "Let the native former AI prioritize Mag Tube construction."},
            {ORDERA_TERRA_AUTOIMPROVE_BASE, "improve_home_base", "Let the native former AI improve tiles near this former's owned support base."},
            {ORDERA_TERRA_FARM_SOLAR_ROAD, "farm_solar_road", "Let the native former AI apply its Farm, Solar Collector, and Road sequence."},
            {ORDERA_TERRA_FARM_MINE_ROAD, "farm_mine_road", "Let the native former AI apply its Farm, Mine, and Road sequence."},
            {ORDERA_TERRA_AUTO_FUNGUS_REM, "remove_fungus", "Let the native former AI prioritize removing xenofungus."},
            {ORDERA_TERRA_AUTO_SENSOR, "sensors", "Let the native former AI prioritize Sensor Array construction."},
        };
        for (const FormerModeChoice& mode : modes) {
            if (!former_automation_mode_available(faction_id, veh, mode.id)) continue;
            out << ",{\"id\":\"automate_former:" << veh_id << ':' << mode.id
                << "\",\"command\":\"automate_former\",\"unit_id\":" << veh_id
                << ",\"automation_mode\":" << json_string(mode.key)
                << ",\"native_mode_id\":" << mode.id
                << ",\"persistent\":true,\"native_automation\":true,"
                << "\"meaning\":" << json_string(mode.meaning) << '}';
        }
    }
    int source_prototype_id = veh.unit_id;
    int target_begin = faction_id * MaxProtoFactionNum;
    int target_end = min(MaxProtoNum, target_begin + MaxProtoFactionNum);
    for (int target_id = target_begin; target_id < target_end; ++target_id) {
        if (!single_unit_upgrade_path_legal(faction_id, source_prototype_id, target_id)) continue;
        int energy_cost = 10 * mod_upgrade_cost(faction_id, target_id, source_prototype_id);
        out << ",{\"id\":\"upgrade_unit:" << veh_id << ':' << target_id
            << "\",\"command\":\"upgrade_unit\",\"unit_id\":" << veh_id
            << ",\"source_prototype_id\":" << source_prototype_id
            << ",\"source_name\":" << json_string(Units[source_prototype_id].name)
            << ",\"target_prototype_id\":" << target_id
            << ",\"target_name\":" << json_string(Units[target_id].name)
            << ",\"energy_cost\":" << energy_cost
            << ",\"energy_credits\":" << Factions[faction_id].energy_credits
            << ",\"affordable\":"
            << (energy_cost <= Factions[faction_id].energy_credits ? "true" : "false")
            << ",\"prototyped\":" << (Units[target_id].is_prototyped() ? "true" : "false")
            << ",\"confirm_upgrade\":1,\"consequential\":true,\"consumes_turn\":true,"
            << "\"meaning\":\"Upgrade only this ready vehicle to the selected native-legal design at the quoted energy cost, then consume its remaining turn.\"}";
    }
    if (veh.is_former() && veh.triad() == TRIAD_LAND
    && terrain_avail(FORMER_ROAD, 0, faction_id)) {
        bool exact_land_target = target_tile_valid
            && is_known(target_x, target_y, faction_id)
            && !is_ocean(mapsq(target_x, target_y))
            && (target_x != veh.x || target_y != veh.y);
        if (exact_land_target) {
            out << ",{\"id\":\"road_to_tile:" << veh_id << ':' << target_tile_id
                << "\",\"command\":\"build_road_to\",\"unit_id\":" << veh_id
                << ",\"target_tile_id\":" << target_tile_id
                << ",\"infrastructure\":\"road\",\"persistent\":true,"
                << "\"meaning\":\"Set the native persistent Road To order to this exact known land tile object.\"}";
            if (terrain_avail(FORMER_MAGTUBE, 0, faction_id)) {
                out << ",{\"id\":\"magtube_to_tile:" << veh_id << ':' << target_tile_id
                    << "\",\"command\":\"build_road_to\",\"unit_id\":" << veh_id
                    << ",\"target_tile_id\":" << target_tile_id
                    << ",\"infrastructure\":\"magtube\",\"persistent\":true,"
                    << "\"meaning\":\"Set the native persistent Mag Tube To order to this exact known land tile object.\"}";
            }
        }
    }
    int psi_source_base_id = base_at(veh.x, veh.y);
    if (psi_source_base_id >= 0 && Bases[psi_source_base_id].faction_id == faction_id
    && can_use_teleport(psi_source_base_id) && !veh.moves_spent) {
        for (int target_base_id = 0; target_base_id < *BaseCount; ++target_base_id) {
            BASE& target = Bases[target_base_id];
            if (target_base_id == psi_source_base_id || target.faction_id != faction_id
            || !has_fac_built(FAC_PSI_GATE, target_base_id)) continue;
            bool compatible = veh.triad() == TRIAD_AIR
                || (veh.triad() == TRIAD_LAND && !is_ocean(&target))
                || (veh.triad() == TRIAD_SEA && coast_tiles(target.x, target.y));
            if (!compatible) continue;
            out << ",{\"id\":\"psi_gate:" << psi_source_base_id << ':' << target_base_id
                << "\",\"command\":\"use_psi_gate\",\"unit_id\":" << veh_id
                << ",\"source_base_id\":" << psi_source_base_id
                << ",\"destination_base_id\":" << target_base_id
                << ",\"destination_name\":" << json_string(target.name)
                << ",\"destination_tile_id\":" << semantic_tile_id(target.x, target.y)
                << ",\"meaning\":\"Transfer this ready unit through the native Psi Gate network; both endpoint gates become used for the turn.\"}";
        }
    }
    if (veh.triad() == TRIAD_LAND && boarded_transport_id < 0) {
        for (int transport_id = veh_at(veh.x, veh.y); transport_id >= 0;
        transport_id = Vehs[transport_id].next_veh_id_stack) {
            if (transport_id == veh_id || Vehs[transport_id].faction_id != faction_id
            || veh_cargo(transport_id) <= 0
            || veh_cargo_loaded(transport_id) >= veh_cargo(transport_id)) continue;
            out << ",{\"id\":\"board:" << transport_id << ':' << veh_id
                << "\",\"command\":\"board_transport\",\"unit_id\":" << veh_id
                << ",\"transport_unit_id\":" << transport_id
                << ",\"remaining_capacity\":"
                << veh_cargo(transport_id) - veh_cargo_loaded(transport_id) << '}';
        }
    }
    if (veh.is_probe()) {
        for (int dir = 0; dir < 8; ++dir) {
            int x = wrap(veh.x + BaseOffsetX[dir]);
            int y = veh.y + BaseOffsetY[dir];
            MAP* sq = mapsq(x, y);
            if (!sq || !sq->is_visible(faction_id)) continue;
            for (int target_id = veh_at(x, y); target_id >= 0;
            target_id = Vehs[target_id].next_veh_id_stack) {
                VEH& target = Vehs[target_id];
                if (target.faction_id == faction_id || has_pact(faction_id, target.faction_id)
                || !(target.visibility & (1 << faction_id))) continue;
                int cost = probe_unit_mind_control_cost(veh_id, target_id);
                if (cost < 0 || Factions[faction_id].energy_credits < cost) continue;
                for (int enhanced = 0; enhanced <= 1; ++enhanced) {
                    out << ",{\"id\":\"probe_subvert:" << target_id << ':' << enhanced
                        << "\",\"command\":\"execute_probe_subversion\",\"unit_id\":" << veh_id
                        << ",\"target_unit_id\":" << target_id
                        << ",\"target_unit_name\":" << json_string(target.name())
                        << ",\"target_tile_id\":" << semantic_tile_id(x, y)
                        << ",\"target_faction_id\":" << static_cast<int>(target.faction_id)
                        << ",\"energy_cost\":" << cost << ",\"enhanced\":" << enhanced
                        << ",\"confirm_probe_incident\":1,\"diplomatic_risk\":"
                        << (has_treaty(faction_id, target.faction_id,
                            DIPLO_TRUCE|DIPLO_TREATY|DIPLO_PACT) ? "true" : "false")
                        << ",\"meaning\":" << json_string(enhanced
                            ? "Attempt Total Subversion at higher probe difficulty so the probe team can survive a success."
                            : "Subvert this visible isolated unit at the quoted native energy cost.") << '}';
                }
            }
        }
        for (int dir = 0; dir < 8; ++dir) {
            int x = wrap(veh.x + BaseOffsetX[dir]);
            int y = veh.y + BaseOffsetY[dir];
            MAP* sq = mapsq(x, y);
            int target_base_id = sq ? base_at(x, y) : -1;
            if (!sq || !sq->is_visible(faction_id) || target_base_id < 0
            || target_base_id >= *BaseCount
            || Bases[target_base_id].faction_id == faction_id
            || has_pact(faction_id, Bases[target_base_id].faction_id)) continue;
            int target_faction_id = Bases[target_base_id].faction_id;
            bool terrain_mismatch = is_ocean(mapsq(veh.x, veh.y))
                != is_ocean(&Bases[target_base_id]);
            if (terrain_mismatch && !has_abil(veh.unit_id, ABL_AMPHIBIOUS)) continue;
            bool treaty_risk = has_treaty(faction_id, target_faction_id,
                DIPLO_TRUCE|DIPLO_TREATY|DIPLO_PACT);
            bool target_hq = has_fac_built(FAC_HEADQUARTERS, target_base_id);
            for (int action_id = PRB_INFILTRATE_DATALINKS;
            action_id <= PRB_FREE_CAPTURED_FACTION_LEADER; ++action_id) {
                if (action_id == PRB_INFILTRATE_DATALINKS
                && (conf.counter_espionage ? probe_has_renew(faction_id, target_faction_id)
                    : has_treaty(faction_id, target_faction_id, DIPLO_HAVE_INFILTRATOR))) continue;
                if (action_id == PRB_PROCURE_RESEARCH_DATA
                && !Rules->tgl_probe_steal_tech) continue;
                if (action_id == PRB_ASSASSINATE_PROMINENT_RESEARCHERS && !target_hq) continue;
                if (action_id == PRB_MIND_CONTROL_CITY && target_hq) continue;
                if (action_id == PRB_INTRODUCE_GENETIC_PLAGUE
                && !faction_has_gene_warfare(faction_id)) continue;
                if (action_id == PRB_FREE_CAPTURED_FACTION_LEADER
                && (!*ExpansionEnabled || *MultiplayerActive || !target_hq
                    || captured_leaders(target_faction_id).empty())) continue;
                int mind_control_cost = -1;
                if (action_id == PRB_MIND_CONTROL_CITY) {
                    mind_control_cost = mod_mind_control(target_base_id, faction_id, 0);
                    if (mind_control_cost < 0
                    || Factions[faction_id].energy_credits < mind_control_cost) continue;
                }
                out << ",{\"id\":\"probe:" << target_base_id << ':' << action_id
                    << ":standard\",\"command\":\"execute_probe_mission\",\"unit_id\":" << veh_id
                    << ",\"target_base_id\":" << target_base_id
                    << ",\"target_tile_id\":" << semantic_tile_id(x, y)
                    << ",\"target_faction_id\":" << target_faction_id
                    << ",\"action_id\":" << action_id
                    << ",\"mission\":" << json_string(probe_action_name(action_id))
                    << ",\"enhanced\":0,\"frame_faction_id\":0"
                    << ",\"confirm_probe_incident\":1"
                    << ",\"diplomatic_risk\":" << (treaty_risk ? "true" : "false");
                if (action_id == PRB_INTRODUCE_GENETIC_PLAGUE) {
                    out << ",\"confirm_atrocity\":1,\"atrocity\":true";
                }
                if (mind_control_cost >= 0) {
                    out << ",\"energy_cost\":" << mind_control_cost;
                }
                out << ",\"meaning\":" << json_string(
                    action_id == PRB_INTRODUCE_GENETIC_PLAGUE
                        ? "Commit a genetic-warfare atrocity against this base after explicit atrocity confirmation; casualties, sanctions, survival, and retaliation remain native."
                    : action_id == PRB_FREE_CAPTURED_FACTION_LEADER
                        ? "Attempt to penetrate this Headquarters and rescue a captive leader. Identity choices remain hidden until the native post-success FREEWHO interaction."
                        : "Execute the selected native probe mission against this visible adjacent base. The outcome and survival roll remain native.") << '}';
                if (action_id == PRB_ACTIVATE_SABOTAGE_VIRUS) {
                    out << ",{\"id\":\"probe:" << target_base_id << ':' << action_id
                        << ":targeted\",\"command\":\"execute_probe_mission\",\"unit_id\":" << veh_id
                        << ",\"target_base_id\":" << target_base_id
                        << ",\"target_tile_id\":" << semantic_tile_id(x, y)
                        << ",\"target_faction_id\":" << target_faction_id
                        << ",\"action_id\":" << action_id
                        << ",\"mission\":\"targeted_sabotage\",\"enhanced\":1,"
                        << "\"frame_faction_id\":0,\"confirm_probe_incident\":1,"
                        << "\"diplomatic_risk\":" << (treaty_risk ? "true" : "false")
                        << ",\"meaning\":\"Request targeted sabotage. The native rate check may instead force random havoc; only if targeting is available will the game reveal its real target menu as a semantic interaction.\"}";
                }
                if (action_id >= PRB_PROCURE_RESEARCH_DATA
                && action_id <= PRB_ASSASSINATE_PROMINENT_RESEARCHERS) {
                    for (int frame_id = 1; frame_id < MaxPlayerNum; ++frame_id) {
                        if (frame_id == faction_id || frame_id == target_faction_id
                        || !is_alive(frame_id)
                        || !has_treaty(faction_id, frame_id, DIPLO_COMMLINK)) continue;
                        out << ",{\"id\":\"probe:" << target_base_id << ':' << action_id
                            << ":frame:" << frame_id
                            << "\",\"command\":\"execute_probe_mission\",\"unit_id\":" << veh_id
                            << ",\"target_base_id\":" << target_base_id
                            << ",\"target_tile_id\":" << semantic_tile_id(x, y)
                            << ",\"target_faction_id\":" << target_faction_id
                            << ",\"action_id\":" << action_id
                            << ",\"mission\":" << json_string(probe_action_name(action_id))
                            << ",\"enhanced\":0,\"frame_faction_id\":" << frame_id
                            << ",\"frame_faction_name\":"
                            << json_string(MFactions[frame_id].formal_name_faction)
                            << ",\"confirm_probe_incident\":1"
                            << ",\"diplomatic_risk\":" << (treaty_risk ? "true" : "false")
                            << ",\"meaning\":\"Execute the mission and, only if the native success roll creates a framing opportunity, blame this already-contacted faction.\"}";
                    }
                }
                if (action_id == PRB_MIND_CONTROL_CITY) {
                    out << ",{\"id\":\"probe:" << target_base_id << ':' << action_id
                        << ":total\",\"command\":\"execute_probe_mission\",\"unit_id\":" << veh_id
                        << ",\"target_base_id\":" << target_base_id
                        << ",\"target_tile_id\":" << semantic_tile_id(x, y)
                        << ",\"target_faction_id\":" << target_faction_id
                        << ",\"action_id\":" << action_id
                        << ",\"mission\":" << json_string(probe_action_name(action_id))
                        << ",\"enhanced\":1,\"frame_faction_id\":0"
                        << ",\"confirm_probe_incident\":1"
                        << ",\"diplomatic_risk\":" << (treaty_risk ? "true" : "false")
                        << ",\"energy_cost\":" << mind_control_cost
                        << ",\"meaning\":\"Attempt native Total Thought Control at higher probe difficulty; if successful, it avoids an automatic vendetta.\"}";
                }
            }
        }
    }
    const SemanticAirdropTargetReceipt airdrop_receipt =
        semantic_airdrop_target_receipt(veh_id);
    if (airdrop_receipt.available) {
        int range = drop_range(faction_id);
        std::ostringstream targets;
        for (size_t index = 0; index < airdrop_receipt.targets.size(); ++index) {
            if (index) targets << ',';
            const SemanticAirdropTarget& target = airdrop_receipt.targets[index];
            targets << "{\"target_tile_id\":" << target.tile_id
                << ",\"range\":" << target.distance
                << ",\"base_owner\":" << target.base_owner
                << ",\"unit_owner\":" << target.unit_owner << '}';
        }
        out << ",{\"id\":\"airdrop:" << veh_id
            << "\",\"command\":\"airdrop_unit\",\"unit_id\":" << veh_id
            << ",\"max_range\":" << range << ",\"targets\":[" << targets.str()
            << "],\"target_count\":" << airdrop_receipt.legal_count
            << ",\"targets_truncated\":" << (airdrop_receipt.truncated ? "true" : "false")
            << ",\"meaning\":\"Execute one native air drop to a currently visible, rule-validated land target.\"}";
    }
    if (can_arty(veh.unit_id, true)) {
        int range = arty_range(veh.unit_id);
        for (int target_id = 0; target_id < *VehCount; ++target_id) {
            VEH& target = Vehs[target_id];
            if (target.faction_id == faction_id || has_pact(faction_id, target.faction_id)
            || !(target.visibility & (1 << faction_id))
            || map_range(veh.x, veh.y, target.x, target.y) > range) continue;
            bool first_at_tile = true;
            for (int prior = 0; prior < target_id; ++prior) {
                if (Vehs[prior].x == target.x && Vehs[prior].y == target.y
                && Vehs[prior].faction_id != faction_id
                && !has_pact(faction_id, Vehs[prior].faction_id)
                && (Vehs[prior].visibility & (1 << faction_id))) {
                    first_at_tile = false;
                    break;
                }
            }
            if (!first_at_tile) continue;
            int artillery_tile_id = semantic_tile_id(target.x, target.y);
            out << ",{\"id\":\"artillery:" << artillery_tile_id
                << "\",\"command\":\"artillery_attack\",\"unit_id\":" << veh_id
                << ",\"target_tile_id\":" << artillery_tile_id
                << ",\"range\":" << map_range(veh.x, veh.y, target.x, target.y)
                << ",\"visible_target_faction_id\":" << static_cast<int>(target.faction_id)
                << ",\"meaning\":\"Bombard the visible non-pact stack on this tile through the native artillery combat routine.\"}";
        }
    }
    if (veh.is_colony()) {
        if (can_build_base(veh.x, veh.y, faction_id, veh.triad())) {
            out << ",{\"id\":\"found_base:" << veh_id
                << "\",\"command\":\"found_base\",\"unit_id\":" << veh_id
                << ",\"meaning\":\"Found a base on this legal native-validated site; the Colony Pod is consumed.\"}";
        } else {
            const char* reason = "native_site_illegal";
            int nearest_known_base_range = 9999;
            MAP* site = mapsq(veh.x, veh.y);
            if (base_at(veh.x, veh.y) >= 0) {
                reason = "current_tile_has_base";
            } else {
                for (int base_id = 0; base_id < *BaseCount; ++base_id) {
                    BASE& base = Bases[base_id];
                    MAP* base_site = mapsq(base.x, base.y);
                    if (base.faction_id != faction_id
                    && (!base_site || !base_site->is_visible(faction_id))) continue;
                    nearest_known_base_range = min(nearest_known_base_range,
                        map_range(veh.x, veh.y, base.x, base.y));
                }
                if (nearest_known_base_range < conf.base_spacing) {
                    reason = "too_close_to_known_base";
                } else if (site && site->items & BIT_THERMAL_BORE) {
                    reason = "thermal_borehole_site";
                } else if (site && ((veh.triad() != TRIAD_SEA
                    && site->alt_level() < ALT_SHORE_LINE)
                    || (veh.triad() == TRIAD_SEA
                        && site->alt_level() != ALT_OCEAN
                        && site->alt_level() != ALT_OCEAN_SHELF))) {
                    reason = "incompatible_land_or_ocean_site";
                }
            }
            out << ",{\"id\":\"settlement:unavailable:" << veh_id
                << "\",\"kind\":\"rule_status\",\"available\":false,\"unit_id\":"
                << veh_id << ",\"reason\":" << json_string(reason)
                << ",\"minimum_base_range\":" << conf.base_spacing;
            if (nearest_known_base_range < 9999) {
                out << ",\"nearest_known_base_range\":" << nearest_known_base_range;
            }
            out << ",\"meaning\":\"The native rules do not allow this Colony Pod to found a base on its current tile. Move it and request fresh unit choices; this status is not a command.\"}";
        }
    }
    if (veh.is_former()) {
        MAP* sq = mapsq(veh.x, veh.y);
        int ocean = sq && is_ocean(sq);
        for (int former_id = FORMER_FARM; sq && sq->base_who() < 0
        && former_id <= FORMER_MONOLITH; ++former_id) {
            if (!terrain_avail(static_cast<FormerItem>(former_id), ocean, faction_id)) continue;
            out << ",{\"id\":\"terraform:" << former_id
                << "\",\"command\":\"terraform\",\"unit_id\":" << veh_id
                << ",\"former_id\":" << former_id << ",\"name\":"
                << json_string(ocean ? Terraform[former_id].name_sea : Terraform[former_id].name) << '}';
        }
    }
    if (terrain_destruction_unit_eligible(veh_id)) {
        MAP* sq = mapsq(veh.x, veh.y);
        uint32_t items = sq ? sq->items : 0;
        int owner = whose_territory(faction_id, veh.x, veh.y, 0, 0);
        bool foreign = owner >= 1 && owner != faction_id;
        bool pact = foreign && has_treaty(faction_id, owner, DIPLO_PACT);
        bool at_vendetta = foreign && has_treaty(faction_id, owner, DIPLO_VENDETTA);
        int available_count = 0;
        for (int former_id = FORMER_FARM; former_id < FORMER_MONOLITH; ++former_id) {
            if (terrain_destruction_item_available(items, former_id)) ++available_count;
        }
        if (available_count && *MultiplayerActive) {
            out << ",{\"id\":\"terrain_destruction:multiplayer_unvalidated\","
                "\"kind\":\"capability_status\",\"supported\":false,"
                "\"meaning\":\"Terrain-improvement destruction is withheld in LAN games until its native selection and diplomacy packets are validated end to end.\"}";
        } else if (available_count && pact) {
            out << ",{\"id\":\"terrain_destruction:pact_blocked\","
                "\"kind\":\"rule_status\",\"available\":false,"
                "\"unit_id\":" << veh_id
                << ",\"former_ids_present\":[";
            bool id_comma = false;
            for (int former_id = FORMER_FARM; former_id < FORMER_MONOLITH; ++former_id) {
                if (!terrain_destruction_item_available(items, former_id)) continue;
                if (id_comma) out << ',';
                id_comma = true;
                out << former_id;
            }
            out << "],"
                "\"territory_owner_faction_id\":" << owner
                << ",\"territory_owner_faction_name\":"
                << json_string(MFactions[owner].formal_name_faction)
                << ",\"reason\":\"pact_forbids_hostile_action\","
                "\"meaning\":\"The native game forbids hostile destruction in Pact territory; renegotiate the Pact first.\"}";
        } else {
            for (int former_id = FORMER_FARM; former_id < FORMER_MONOLITH; ++former_id) {
                if (!terrain_destruction_item_available(items, former_id)) continue;
                out << ",{\"id\":\"destroy_terrain:" << former_id << ':' << veh_id
                    << "\",\"command\":\"destroy_terrain_improvement\",\"unit_id\":"
                    << veh_id << ",\"former_id\":" << former_id
                    << ",\"name\":" << json_string(
                        is_ocean(sq) ? Terraform[former_id].name_sea : Terraform[former_id].name)
                    << ",\"confirm_destruction\":1,\"destructive\":true,"
                    "\"foreign_territory\":" << (foreign ? "true" : "false")
                    << ",\"territory_owner_faction_id\":" << owner;
                if (foreign) {
                    out << ",\"territory_owner_faction_name\":"
                        << json_string(MFactions[owner].formal_name_faction)
                        << ",\"already_at_vendetta\":" << (at_vendetta ? "true" : "false");
                    if (!at_vendetta) {
                        out << ",\"confirm_hostility\":1,\"consequential\":true";
                    }
                }
                out << ",\"meaning\":" << json_string(foreign && !at_vendetta
                    ? "Permanently destroy this exact improvement and explicitly accept the native break with its territorial owner."
                    : "Permanently destroy this exact improvement on the unit's current tile through the native destruction routine.")
                    << '}';
            }
        }
    }
    if (veh.is_supply()) {
        const ResType resources[] = {RES_NUTRIENT, RES_MINERAL, RES_ENERGY};
        const char* names[] = {"nutrients", "minerals", "energy"};
        for (int resource_index = 0; resource_index < 3; ++resource_index) {
            ResType resource = resources[resource_index];
            if (!can_convoy(veh_id, resource)) continue;
            out << ",{\"id\":\"convoy:" << names[resource_index] << ':' << veh_id
                << "\",\"command\":\"convoy_resource\",\"unit_id\":" << veh_id
                << ",\"resource\":" << json_string(names[resource_index]) << '}';
        }
    }
    MAP* transfer_sq = mapsq(veh.x, veh.y);
    int transfer_target = transfer_sq ? transfer_sq->owner : -1;
    bool transfer_air_compatible = veh.triad() != TRIAD_AIR
        || (transfer_sq && transfer_sq->is_airbase());
    bool transfer_cargo_safe = boarded_transport_id < 0
        && (!veh.is_transport() || veh_cargo_loaded(veh_id) == 0);
    if (!*MultiplayerActive && transfer_sq && transfer_sq->is_visible(faction_id)
    && transfer_target >= 1
    && transfer_target < MaxPlayerNum && transfer_target != faction_id
    && is_alive(transfer_target) && has_pact(faction_id, transfer_target)
    && transfer_air_compatible && transfer_cargo_safe) {
        out << ",{\"id\":\"give_unit:" << veh_id << ':' << transfer_target
            << "\",\"command\":\"give_unit\",\"unit_id\":" << veh_id
            << ",\"faction_id\":" << transfer_target
            << ",\"faction_name\":"
            << json_string(MFactions[transfer_target].formal_name_faction)
            << ",\"confirm_transfer\":1,\"consequential\":true,"
            "\"meaning\":\"Transfer this unit through the native ownership-change routine to the Pact faction whose territory contains its current tile.\"}";
    }
    int local_base_id = base_at(veh.x, veh.y);
    if (local_base_id >= 0 && Bases[local_base_id].faction_id == faction_id
    && veh.home_base_id != local_base_id) {
        out << ",{\"id\":\"rehome:" << local_base_id << ':' << veh_id
            << "\",\"command\":\"rehome_unit\",\"unit_id\":" << veh_id
            << ",\"base_id\":" << local_base_id << ",\"base_name\":"
            << json_string(Bases[local_base_id].name)
            << ",\"meaning\":\"Change this unit's support home to the owned base on its current tile.\"}";
    }
    if (self_destruct_unit_eligible(faction_id, veh_id)) {
        if (*MultiplayerActive) {
            out << ",{\"id\":\"self_destruct:multiplayer_unvalidated\","
                "\"kind\":\"capability_status\",\"supported\":false,"
                "\"meaning\":\"Self-destruct is withheld in LAN games until its native network packet is validated end to end.\"}";
        } else {
            int blast_damage = clamp(weap_val(veh.unit_id, faction_id), 1, 20)
                * Units[veh.unit_id].reactor_id / 2;
            out << ",{\"id\":\"self_destruct:" << veh_id
                << "\",\"command\":\"self_destruct_unit\",\"unit_id\":" << veh_id
                << ",\"confirm_self_destruct\":1,\"destructive\":true,"
                "\"consequential\":true,\"blast_damage\":" << blast_damage
                << ",\"blast_radius_tiles\":1,"
                "\"meaning\":\"Permanently destroy this unit and apply the native reactor-overload blast to every non-base stack on its tile and adjacent tiles.\"},"
                "{\"id\":\"self_destruct:context\",\"kind\":\"information\","
                "\"source_unit_id\":" << veh_id << ",\"source_tile_id\":"
                << semantic_tile_id(veh.x, veh.y) << ",\"blast_damage\":"
                << blast_damage << ",\"blast_radius_tiles\":1,"
                "\"known_affected_units\":[";
            bool affected_comma = false;
            for (int affected_id = 0; affected_id < *VehCount; ++affected_id) {
                VEH& affected = Vehs[affected_id];
                if (base_at(affected.x, affected.y) >= 0
                || map_range(veh.x, veh.y, affected.x, affected.y) > 1
                || (affected.faction_id != faction_id
                    && !(affected.visibility & (1 << faction_id)))) continue;
                if (affected_comma) out << ',';
                affected_comma = true;
                bool source = affected_id == veh_id;
                out << "{\"unit_id\":" << affected_id
                    << ",\"name\":" << json_string(affected.name())
                    << ",\"owner_faction_id\":"
                    << static_cast<int>(affected.faction_id)
                    << ",\"tile_id\":" << semantic_tile_id(affected.x, affected.y)
                    << ",\"current_hitpoints\":" << affected.cur_hitpoints()
                    << ",\"source_unit\":" << (source ? "true" : "false")
                    << ",\"projected_lethal\":"
                    << (source || blast_damage >= affected.cur_hitpoints()
                        ? "true" : "false") << '}';
            }
            out << "]}";
        }
    }
    out << ",{\"id\":\"disband:" << veh_id
        << "\",\"command\":\"disband_unit\",\"unit_id\":" << veh_id
        << ",\"requires\":{\"confirm_disband\":1},"
        << "\"destructive\":true,\"meaning\":\"Permanently disband this unit. Unit ids may shift afterward; observe again.\"}";
    out << "]}";
    return out.str();
}

std::string diplomacy_choices_response(int faction_id) {
    ensure_test_unit_gift_fixture();
    std::ostringstream out;
    out << "{\"ok\":true,\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str())
        << ",\"revision\":" << json_string(semantic_revision().c_str())
        << ",\"kind\":\"diplomacy\",\"choices\":[";
    bool comma = false;
    for (int other = 1; other < MaxPlayerNum; ++other) {
        if (other == faction_id || !is_alive(other)
        || !has_treaty(faction_id, other, DIPLO_COMMLINK)) continue;
        if (comma) out << ',';
        comma = true;
        int status = Factions[faction_id].diplo_status[other];
        out << "{\"id\":\"open_diplomacy:" << other << "\",\"faction_id\":"
            << other << ",\"faction_name\":"
            << json_string(MFactions[other].formal_name_faction)
            << ",\"leader_name\":" << json_string(MFactions[other].name_leader)
            << ",\"alien\":" << (is_alien(other) ? "true" : "false")
            << ",\"human_controlled\":" << (is_human(other) ? "true" : "false")
            << ",\"relations\":{\"vendetta\":"
            << ((status & DIPLO_VENDETTA) ? "true" : "false")
            << ",\"truce\":" << ((status & DIPLO_TRUCE) ? "true" : "false")
            << ",\"treaty\":" << ((status & DIPLO_TREATY) ? "true" : "false")
            << ",\"pact\":" << ((status & DIPLO_PACT) ? "true" : "false") << '}';
        out << ",\"command\":\"open_diplomacy\",\"supported\":true,"
            << "\"meaning\":\"Open the native diplomatic channel with this contacted faction.\"}";
    }
    out << "],\"fair_play_note\":\"Only living factions whose commlink the player has acquired are exposed.\"}";
    return out.str();
}

std::string council_choices_response(int faction_id) {
    bool available = !(*GameState & STATE_GAME_DONE) && can_call_council(faction_id, 0);
    std::ostringstream out;
    out << "{\"ok\":true,\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str())
        << ",\"revision\":" << json_string(semantic_revision().c_str())
        << ",\"kind\":\"council\",\"choices\":[";
    if (available) {
        out << "{\"id\":\"council:convene\",\"command\":\"convene_council\","
            "\"meaning\":\"Open the native, rule-validated Planetary Council proposal menu.\"}";
    } else {
        out << "{\"id\":\"council:unavailable\",\"kind\":\"capability_status\","
            "\"supported\":false,\"meaning\":\"The native game reports that this faction cannot convene the Council now.\"}";
    }
    out << "],\"own_votes\":" << council_votes(faction_id)
        << ",\"governor_faction_id\":" << *GovernorFaction << '}';
    return out.str();
}

int prototype_queue_references(int faction_id, int unit_id) {
    int count = 0;
    for (int base_id = 0; base_id < *BaseCount; ++base_id) {
        BASE& base = Bases[base_id];
        if (base.faction_id != faction_id) continue;
        for (int position = 0; position <= base.queue_size && position < 10; ++position) {
            if (base.queue_items[position] == unit_id) ++count;
        }
    }
    return count;
}

bool owned_custom_prototype(int faction_id, int unit_id) {
    return unit_id >= faction_id * MaxProtoFactionNum
        && unit_id < min(MaxProtoNum, (faction_id + 1) * MaxProtoFactionNum);
}

bool prototype_available_to_faction(int faction_id, int unit_id) {
    return unit_id >= 0 && unit_id < MaxProtoNum && Units[unit_id].name[0]
        && mod_veh_avail(unit_id, faction_id, -1);
}

bool valid_design_components(int faction_id, int chassis_id, int weapon_id,
int armor_id, int reactor_id, int ability_id_1, int ability_id_2,
uint32_t& ability_flags, std::string& reason) {
    if (chassis_id < 0 || chassis_id >= MaxChassisNum
    || !has_tech(Chassis[chassis_id].preq_tech, faction_id)) {
        reason = "chassis_id is not in the currently unlocked chassis catalog.";
        return false;
    }
    if (weapon_id < 0 || weapon_id >= MaxWeaponNum
    || !has_tech(Weapon[weapon_id].preq_tech, faction_id)) {
        reason = "weapon_id is not in the currently unlocked weapon catalog.";
        return false;
    }
    if (armor_id < 0 || armor_id >= MaxArmorNum
    || !has_tech(Armor[armor_id].preq_tech, faction_id)) {
        reason = "armor_id is not in the currently unlocked armor catalog.";
        return false;
    }
    if (reactor_id < REC_FISSION || reactor_id > REC_SINGULARITY
    || !has_tech(Reactor[reactor_id - 1].preq_tech, faction_id)) {
        reason = "reactor_id is not in the currently unlocked reactor catalog.";
        return false;
    }
    bool missile_chassis = Chassis[chassis_id].missile != 0;
    bool missile_payload = weapon_id == WPN_PLANET_BUSTER
        || weapon_id == WPN_CONVENTIONAL_PAYLOAD
        || weapon_id == WPN_TECTONIC_PAYLOAD
        || weapon_id == WPN_FUNGAL_PAYLOAD;
    if (missile_chassis != missile_payload) {
        reason = "Missile payloads require the Missile chassis, and the Missile chassis requires a missile payload.";
        return false;
    }
    ability_flags = 0;
    const int ability_ids[] = {ability_id_1, ability_id_2};
    for (int index = 0; index < 2; ++index) {
        int ability_id = ability_ids[index];
        if (ability_id < 0) continue;
        if (ability_id >= MaxAbilityNum
        || !has_ability(faction_id, static_cast<VehAbl>(ability_id),
            static_cast<VehChassis>(chassis_id), static_cast<VehWeapon>(weapon_id))) {
            reason = "An ability is locked or incompatible with the selected chassis and weapon role.";
            return false;
        }
        uint32_t bit = 1u << ability_id;
        if (ability_flags & bit) {
            reason = "The same special ability cannot occupy both ability slots.";
            return false;
        }
        ability_flags |= bit;
    }
    return true;
}

void append_prototype_summary(std::ostringstream& out, int faction_id, int unit_id) {
    UNIT& unit = Units[unit_id];
    int active_units = veh_count(faction_id, unit_id);
    int queued = prototype_queue_references(faction_id, unit_id);
    out << "{\"prototype_id\":" << unit_id
        << ",\"name\":" << json_string(unit.name)
        << ",\"custom\":" << (unit_id >= MaxProtoFactionNum ? "true" : "false")
        << ",\"prototyped\":" << (unit.is_prototyped() ? "true" : "false")
        << ",\"chassis_id\":" << static_cast<int>(unit.chassis_id)
        << ",\"weapon_id\":" << static_cast<int>(unit.weapon_id)
        << ",\"armor_id\":" << static_cast<int>(unit.armor_id)
        << ",\"reactor_id\":" << static_cast<int>(unit.reactor_id)
        << ",\"ability_flags\":" << unit.ability_flags
        << ",\"mineral_rows\":" << static_cast<int>(unit.cost)
        << ",\"active_unit_count\":" << active_units
        << ",\"production_queue_references\":" << queued;
    if (owned_custom_prototype(faction_id, unit_id)) {
        out << ",\"retire_legal\":"
            << (active_units == 0 && queued == 0 ? "true" : "false");
    }
    out << '}';
}

std::string unit_design_choices_response(int faction_id) {
    int custom_begin = faction_id * MaxProtoFactionNum;
    int custom_end = min(MaxProtoNum, custom_begin + MaxProtoFactionNum);
    int active_custom = 0;
    for (int unit_id = custom_begin; unit_id < custom_end; ++unit_id) {
        if (Units[unit_id].is_active()) ++active_custom;
    }
    std::ostringstream out;
    out << "{\"ok\":true,\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str())
        << ",\"revision\":" << json_string(semantic_revision().c_str())
        << ",\"kind\":\"unit_design\",\"energy_credits\":"
        << Factions[faction_id].energy_credits
        << ",\"mutations_supported\":" << (*MultiplayerActive ? "false" : "true")
        << ",\"custom_slots\":{\"active\":" << active_custom
        << ",\"capacity\":" << MaxProtoFactionNum
        << ",\"available\":" << MaxProtoFactionNum - active_custom << "},"
        << "\"constraints\":{\"max_selected_abilities\":2,"
        << "\"component_ids_must_come_from_catalogs\":true,"
        << "\"missile_chassis_requires_missile_payload\":true,"
        << "\"mutations_require_fresh_revision\":true,"
        << "\"retirement_and_upgrade_require_confirmation\":true},"
        << "\"commands\":{"
        << "\"create\":{\"name\":\"create_unit_design\",\"parameters\":[\"chassis_id\",\"weapon_id\",\"armor_id\",\"reactor_id\",\"ability_id_1\",\"ability_id_2\",\"name\"]},"
        << "\"retire\":{\"name\":\"retire_unit_design\",\"parameters\":[\"prototype_id\",\"confirm_retire\"]},"
        << "\"upgrade\":{\"name\":\"upgrade_prototype\",\"parameters\":[\"source_prototype_id\",\"target_prototype_id\",\"confirm_upgrade\"],"
        << "\"meaning\":\"Upgrade every owned unit and production-queue reference of one combat prototype to a non-downgrading owned custom design.\"}},"
        << "\"catalogs\":{\"chassis\":[";
    bool comma = false;
    for (int id = 0; id < MaxChassisNum; ++id) {
        if (!has_tech(Chassis[id].preq_tech, faction_id)) continue;
        if (comma) out << ',';
        comma = true;
        out << "{\"chassis_id\":" << id << ",\"name\":"
            << json_string(Chassis[id].offsv1_name)
            << ",\"triad\":" << static_cast<int>(Chassis[id].triad)
            << ",\"speed\":" << static_cast<int>(Chassis[id].speed)
            << ",\"missile\":" << (Chassis[id].missile ? "true" : "false") << '}';
    }
    out << "],\"weapons\":[";
    comma = false;
    for (int id = 0; id < MaxWeaponNum; ++id) {
        if (!has_tech(Weapon[id].preq_tech, faction_id)) continue;
        if (comma) out << ',';
        comma = true;
        out << "{\"weapon_id\":" << id << ",\"name\":" << json_string(Weapon[id].name)
            << ",\"short_name\":" << json_string(Weapon[id].name_short)
            << ",\"offense\":" << static_cast<int>(Weapon[id].offense_value)
            << ",\"mode\":" << static_cast<int>(Weapon[id].mode)
            << ",\"cost\":" << static_cast<int>(Weapon[id].cost) << '}';
    }
    out << "],\"armor\":[";
    comma = false;
    for (int id = 0; id < MaxArmorNum; ++id) {
        if (!has_tech(Armor[id].preq_tech, faction_id)) continue;
        if (comma) out << ',';
        comma = true;
        out << "{\"armor_id\":" << id << ",\"name\":" << json_string(Armor[id].name)
            << ",\"short_name\":" << json_string(Armor[id].name_short)
            << ",\"defense\":" << static_cast<int>(Armor[id].defense_value)
            << ",\"cost\":" << static_cast<int>(Armor[id].cost) << '}';
    }
    out << "],\"reactors\":[";
    comma = false;
    for (int id = REC_FISSION; id <= REC_SINGULARITY; ++id) {
        if (!has_tech(Reactor[id - 1].preq_tech, faction_id)) continue;
        if (comma) out << ',';
        comma = true;
        out << "{\"reactor_id\":" << id << ",\"name\":"
            << json_string(Reactor[id - 1].name)
            << ",\"power\":" << Reactor[id - 1].power << '}';
    }
    out << "],\"abilities\":[";
    comma = false;
    for (int id = 0; id < MaxAbilityNum; ++id) {
        if (!has_tech(Ability[id].preq_tech, faction_id)) continue;
        if (comma) out << ',';
        comma = true;
        out << "{\"ability_id\":" << id << ",\"name\":" << json_string(Ability[id].name)
            << ",\"abbreviation\":" << json_string(Ability[id].abbreviation)
            << ",\"compatibility_flags\":" << Ability[id].flags << '}';
    }
    out << "]},\"available_prototypes\":[";
    comma = false;
    for (int unit_id = 0; unit_id < MaxProtoNum; ++unit_id) {
        if (!prototype_available_to_faction(faction_id, unit_id)) continue;
        if (comma) out << ',';
        comma = true;
        append_prototype_summary(out, faction_id, unit_id);
    }
    out << "]}";
    return out.str();
}

std::string game_management_choices_response() {
    ensure_test_economic_victory_fixture();
    int faction_id = *CurrentPlayerFaction;
    int ready_units = 0;
    for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
        if (Vehs[veh_id].faction_id == faction_id
        && semantic_unit_requires_decision(veh_id)) ++ready_units;
    }
    std::ostringstream out;
    out << "{\"ok\":true,\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str())
        << ",\"revision\":" << json_string(semantic_revision().c_str())
        << ",\"kind\":\"game_management\",\"choices\":[";
    if (*MultiplayerActive) {
        const int local_player_index = lan_local_player_index();
        const int host_player_index = lan_host_player_index();
        if (local_player_index >= 1 && local_player_index == host_player_index) {
            out << "{\"id\":\"save:slot\",\"command\":\"save_game\","
                "\"parameters\":{\"slot\":{\"type\":\"string\",\"pattern\":\"^[A-Za-z0-9_-]{1,32}$\"}},"
                "\"native_host_only\":true,"
                "\"meaning\":\"Save the active multiplayer campaign from its native DirectPlay host into a match-scoped named slot. Existing data in the same slot is replaced.\"}";
        } else {
            out << "{\"id\":\"save:host_only\",\"kind\":\"capability_status\","
                "\"supported\":false,\"native_host_only\":true,"
                "\"meaning\":\"Only the native DirectPlay host may save this multiplayer campaign.\"}";
        }
    } else {
        out << "{\"id\":\"save:slot\",\"command\":\"save_game\","
            "\"parameters\":{\"slot\":{\"type\":\"string\",\"pattern\":\"^[A-Za-z0-9_-]{1,32}$\"}},"
            "\"meaning\":\"Save this match into a match-scoped named slot. Existing data in the same slot is replaced.\"}";
    }
    Faction& faction = Factions[faction_id];
    bool economic_enabled = *GameRules & RULES_VICTORY_ECONOMIC;
    bool economic_tech = economic_enabled
        && has_tech(Rules->tech_preq_economic_victory, faction_id);
    int headquarters_base_id = owned_headquarters_base(faction_id);
    if (faction.corner_market_active() && faction.corner_market_cost > 0) {
        out << ",{\"id\":\"economic_victory:active\",\"kind\":\"information\","
            "\"completion_turn\":" << faction.corner_market_turn
            << ",\"completion_year\":" << game_year(faction.corner_market_turn)
            << ",\"committed_energy\":" << faction.corner_market_cost
            << ",\"meaning\":\"The Global Energy Market plan is active; protect the Headquarters until its completion turn.\"}";
    } else if (economic_tech && headquarters_base_id >= 0
    && !(*GameState & STATE_GAME_DONE)) {
        int cost = corner_market(faction_id);
        if (faction.energy_credits >= cost) {
            out << ",{\"id\":\"economic_victory:initiate\","
                "\"command\":\"corner_global_energy_market\",\"confirm_corner_market\":1,"
                "\"consequential\":true,\"cost\":" << cost
                << ",\"available_energy\":" << faction.energy_credits
                << ",\"headquarters_base_id\":" << headquarters_base_id
                << ",\"turns_to_completion\":"
                << Rules->turns_corner_global_energy_market
                << ",\"meaning\":\"Commit the quoted energy and begin the native economic-victory countdown.\"}";
        } else {
            out << ",{\"id\":\"economic_victory:insufficient_energy\","
                "\"kind\":\"information\",\"required_energy\":" << cost
                << ",\"available_energy\":" << faction.energy_credits
                << ",\"headquarters_base_id\":" << headquarters_base_id << '}';
        }
    } else if (economic_enabled && economic_tech && headquarters_base_id < 0) {
        out << ",{\"id\":\"economic_victory:headquarters_required\","
            "\"kind\":\"information\",\"meaning\":\"Build a Headquarters before attempting to corner the Global Energy Market.\"}";
    }
    if (end_turn_completion_pending()) {
        out << ",{\"id\":\"turn:transition_pending\",\"kind\":\"capability_status\","
            "\"supported\":false,\"meaning\":\"The confirmed native end-turn request is still completing. Wait for a new turn or interaction; do not submit another command.\"}";
    } else if (!ready_units) {
        out << ",{\"id\":\"turn:end\",\"command\":\"end_turn\","
            "\"meaning\":\"End the human faction's turn after every ready unit decision has been resolved.\"}";
    } else {
        out << ",{\"id\":\"turn:end_blocked\",\"kind\":\"capability_status\","
            "\"supported\":false,\"ready_unit_count\":" << ready_units
            << ",\"meaning\":\"End turn is blocked until every ready unit has a semantic decision or persistent order.\"}";
        if (!*MultiplayerActive) {
            out << ",{\"id\":\"turn:skip_all_ready\",\"command\":\"skip_all_ready_units\","
                "\"ready_unit_count\":" << ready_units
                << ",\"ready_unit_ids\":[";
            bool unit_comma = false;
            for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
                if (Vehs[veh_id].faction_id != faction_id
                || !semantic_unit_requires_decision(veh_id)) continue;
                if (unit_comma) out << ',';
                unit_comma = true;
                out << veh_id;
            }
            out << "],\"confirm_skip_all_ready\":1,\"consequential\":true,"
                "\"native_auto_end_turn_possible\":true,"
                "\"meaning\":\"Deliberately spend the remainder of every listed ready unit's current turn. Use this only after deciding that none needs movement, orders, boarding, or another semantic action; native preferences may immediately begin the next turn when the last unit is skipped.\"}";
        }
    }
    out << "]}";
    return out.str();
}

std::string semantic_choices_response(const std::string& request) {
    if (!game_active()) return error_response("not_in_game", "Start or load a game first.");
    std::string kind = field_string(request, "kind");
    // Reading the local native interaction is safe in multiplayer and is
    // required to identify the next command family that needs validation.
    // Strategic choice families remain fail-closed until their mutations pass
    // a real two-client synchronization regression.
    if (*MultiplayerActive && kind != "interaction" && kind != "unit_actions"
    && kind != "game_management" && kind != "production"
    && kind != "base_management" && kind != "base_citizens"
    && kind != "energy_allocation"
    && kind != "research" && kind != "social_engineering"
    && kind != "diplomacy") {
        return error_response("multiplayer_choices_not_validated",
            "Only local interaction choices and the currently validated safe unit-action subset are readable in multiplayer. Other strategic families remain fail-closed until each command family passes a two-client synchronization regression. Do not use UI input; report the missing validated command family to the orchestrator.");
    }
    int faction_id = *CurrentPlayerFaction;
    if (kind == "interaction" && popup_transition_is_pending()) {
        return error_response("popup_transition_pending",
            "The submitted popup action is still crossing the native event loop. Wait and observe; do not submit the same choice again.");
    }
    if (kind == "interaction") {
        const char* label = semantic_popup_label();
        int council_window_proposal = active_council_window_proposal();
        std::ostringstream out;
        out << "{\"ok\":true,\"match_id\":" << json_string(agent_match_id.c_str())
            << ",\"session_id\":" << json_string(agent_session_id.c_str())
            << ",\"revision\":" << json_string(semantic_revision().c_str())
            << ",\"kind\":\"interaction\",\"interaction\":"
            << json_string(interaction_kind(faction_id).c_str()) << ",\"popup_label\":"
            << json_string(label) << ",\"instance_id\":" << agent_popup_generation()
            << ",\"choices\":[";
        if (human_diplomacy_window_active()) {
            int initiator = human_diplomacy_participant(0);
            int counterpart = human_diplomacy_participant(1);
            bool first = true;
            if (human_treaty_proposal_legal(faction_id)) {
                out << "{\"id\":\"human_diplomacy:propose_treaty\","
                    "\"command\":\"propose_human_relationship\","
                    "\"relationship\":\"treaty\","
                    "\"meaning\":\"Send and commit one exact Treaty of Friendship offer through the native human-diplomacy transmission.\"}";
                first = false;
            }
            if (human_pact_proposal_legal(faction_id)) {
                if (!first) out << ',';
                out << "{\"id\":\"human_diplomacy:propose_pact\","
                    "\"command\":\"propose_human_relationship\","
                    "\"relationship\":\"pact\","
                    "\"meaning\":\"Send and commit one exact Pact of Brotherhood offer through the native human-diplomacy transmission.\"}";
                first = false;
            }
            if (human_truce_proposal_legal(faction_id)) {
                if (!first) out << ',';
                out << "{\"id\":\"human_diplomacy:propose_truce\","
                    "\"command\":\"propose_human_relationship\","
                    "\"relationship\":\"truce\","
                    "\"meaning\":\"Send and commit one exact Blood Truce offer through the native human-diplomacy transmission.\"}";
                first = false;
            }
            for (int tech_id = 0; tech_id < MaxTechnologyNum; ++tech_id) {
                if (!human_technology_proposal_legal(faction_id, tech_id)) continue;
                if (!first) out << ',';
                out << "{\"id\":\"human_diplomacy:offer_technology:"
                    << tech_id << "\",\"command\":\"propose_human_technology\","
                    "\"technology_id\":" << tech_id << ",\"technology_name\":"
                    << json_string(Tech[tech_id].name)
                    << ",\"meaning\":\"Send and commit this exact player-owned technology offer through the native human-diplomacy transmission.\"}";
                first = false;
            }
            if (human_energy_proposal_legal(faction_id, 1)) {
                if (!first) out << ',';
                out << "{\"id\":\"human_diplomacy:offer_energy\","
                    "\"command\":\"propose_human_energy\","
                    "\"amount_min\":1,\"amount_max\":"
                    << Factions[faction_id].energy_credits
                    << ",\"amount_parameter\":\"amount\","
                    "\"meaning\":\"Send and commit one exact caller-selected amount of player-owned energy credits through the native human-diplomacy transmission.\"}";
                first = false;
            }
            for (int target = 1; target < MaxPlayerNum; ++target) {
                if (!human_joint_attack_proposal_legal(faction_id, target)) continue;
                if (!first) out << ',';
                out << "{\"id\":\"human_diplomacy:propose_joint_attack:"
                    << target << "\",\"command\":\"propose_human_joint_attack\","
                    "\"target_faction_id\":" << target
                    << ",\"target_faction_name\":"
                    << json_string(MFactions[target].formal_name_faction)
                    << ",\"meaning\":\"Send and commit an exact joint-attack proposal naming this contacted, live third faction through the native human-diplomacy transmission.\"}";
                first = false;
            }
            int local_side = human_diplomacy_local_side(faction_id);
            int incoming_side = local_side < 0 ? -1 : 1 - local_side;
            if (incoming_side >= 0
            && human_diplomacy_acceptance(local_side) == 0
            && (human_diplomacy_clause_count(local_side) > 0
                || human_diplomacy_clause_count(incoming_side) > 0)) {
                if (!first) out << ',';
                out << "{\"id\":\"human_diplomacy:accept\","
                    "\"command\":\"respond_human_diplomacy\","
                    "\"response\":\"accept\","
                    "\"meaning\":\"Accept the complete current native human-diplomacy offer, including both sides' clauses.\"},"
                    "{\"id\":\"human_diplomacy:decline\","
                    "\"command\":\"respond_human_diplomacy\","
                    "\"response\":\"decline\","
                    "\"meaning\":\"Decline the complete current native human-diplomacy offer.\"}";
                first = false;
            }
            if (!first) out << ',';
            out << "{\"id\":\"human_diplomacy:finish\","
                "\"command\":\"finish_human_diplomacy\","
                "\"meaning\":\"Send the currently composed native diplomatic packet and close this human diplomacy window.\"},"
                "{\"id\":\"human_diplomacy:context\",\"kind\":\"information\","
                "\"initiator_faction_id\":" << initiator
                << ",\"counterpart_faction_id\":" << counterpart
                << ",\"local_faction_id\":" << faction_id
                << ",\"local_acceptance_state\":"
                << (local_side < 0 ? -1
                    : human_diplomacy_acceptance(local_side))
                << ",\"counterpart_acceptance_state\":"
                << (incoming_side < 0 ? -1
                    : human_diplomacy_acceptance(incoming_side))
                << ",\"clauses\":";
            append_human_diplomacy_clauses(out);
            out << '}';
        } else if (technology_presentation_active()) {
            int tech_id = technology_presentation_tech_id();
            out << "{\"id\":\"technology_presentation:advance\","
                "\"command\":\"advance_technology_presentation\","
                "\"meaning\":\"Close this passive native technology presentation after recording the newly learned technology.\"},"
                "{\"id\":\"technology_presentation:context\",\"kind\":\"information\"";
            if (tech_id >= 0) {
                out << ",\"technology_id\":" << tech_id
                    << ",\"technology_name\":" << json_string(Tech[tech_id].name);
            }
            out << ",\"source_notice\":"
                << json_string(agent_popup_last_started_label()) << '}';
        } else if (council_window_proposal >= 0) {
            bool candidate_ballot = council_window_proposal == PROP_ELECT_PLANETARY_GOVERNOR
                || council_window_proposal == PROP_UNITE_SUPREME_LEADER;
            bool comma = false;
            if (candidate_ballot) {
                for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
                    if (!is_alive(candidate) || is_alien(candidate) || !eligible(candidate)) continue;
                    if (comma) out << ',';
                    comma = true;
                    out << "{\"id\":\"council_window_candidate:" << candidate
                        << "\",\"command\":\"cast_council_vote\",\"candidate_faction_id\":"
                        << candidate << ",\"faction_name\":"
                        << json_string(MFactions[candidate].formal_name_faction)
                        << ",\"leader_name\":" << json_string(MFactions[candidate].name_leader) << '}';
                }
            } else {
                out << "{\"id\":\"council_window_vote:yea\",\"command\":\"cast_council_vote\","
                    "\"response\":\"yea\",\"meaning\":\"Vote YEA on the active Council motion.\"},"
                    "{\"id\":\"council_window_vote:nay\",\"command\":\"cast_council_vote\","
                    "\"response\":\"nay\",\"meaning\":\"Vote NAY on the active Council motion.\"}";
                comma = true;
            }
            if (comma) out << ',';
            out << "{\"id\":\"council_window:agenda\",\"kind\":\"information\","
                "\"proposal_id\":" << council_window_proposal << ",\"proposal_name\":"
                << json_string(Proposal[council_window_proposal].name) << '}';
        } else if (first_base_name_modal(faction_id)) {
            int base_id = first_owned_base(faction_id);
            out << "{\"id\":\"first_base_name:submit\",\"command\":\"set_first_base_name\"," 
                "\"base_id\":" << base_id << ",\"suggested_name\":"
                << json_string(Bases[base_id].name) << '}';
        } else if (!endgame_presentation_phase.empty()
        && endgame_presentation_phase != "victory_movie"
        && !active_default_popup()) {
            out << "{\"id\":\"endgame_presentation:advance\"," 
                "\"command\":\"advance_endgame_presentation\",\"phase\":"
                << json_string(endgame_presentation_phase.c_str())
                << ",\"meaning\":\"Close this passive native victory presentation and advance to the next score, rating, Hall of Fame, replay, or final decision stage.\"},"
                "{\"id\":\"endgame_presentation:context\",\"kind\":\"information\"," 
                "\"phase\":" << json_string(endgame_presentation_phase.c_str())
                << ",\"game_completed\":"
                << ((*GameState & STATE_GAME_DONE) ? "true" : "false")
                << ",\"meaning\":\"This stage is presentation-only; no strategic or unit mutation is legal until it closes.\"}";
        } else if (!strcmp(label, "REALLYOVER")) {
            out << "{\"id\":\"end_turn_confirmation:cancel\","
                "\"command\":\"respond_to_end_turn_confirmation\",\"response\":\"cancel\","
                "\"meaning\":\"Cancel the pending native end-turn request and return to unit decisions.\"},"
                "{\"id\":\"end_turn_confirmation:proceed\","
                "\"command\":\"respond_to_end_turn_confirmation\",\"response\":\"proceed\","
                "\"meaning\":\"Confirm the already requested end turn while leaving persistent semantic orders and native automation in effect.\"}";
        } else if (!strcmp(label, "VIRUS")) {
            BasePop* active = active_default_popup();
            if (active) append_probe_sabotage_choices(out, active);
        } else if (!strcmp(label, "FREEWHO")) {
            BasePop* active = active_default_popup();
            bool comma = false;
            for (int captive_id = 1; captive_id < MaxPlayerNum; ++captive_id) {
                if (!active || !popup_has_choice_id(active, captive_id)) continue;
                if (comma) out << ',';
                comma = true;
                out << "{\"id\":\"captive_leader:" << captive_id
                    << "\",\"command\":\"choose_captive_leader\","
                    << "\"captive_faction_id\":" << captive_id
                    << ",\"faction_name\":"
                    << json_string(MFactions[captive_id].formal_name_faction)
                    << ",\"leader_name\":" << json_string(MFactions[captive_id].name_leader)
                    << ",\"meaning\":\"Liberate this leader revealed by the native post-success rescue menu.\"}";
            }
            if (!comma) {
                out << "{\"id\":\"captive_leader:no_reviewed_candidates\","
                    "\"kind\":\"capability_status\",\"supported\":false,"
                    "\"meaning\":\"No reviewed captive faction id matched the native rescue menu.\"}";
            }
        } else if ((!strcmp(label, "OBLIT") || !strcmp(label, "OBLITOK"))
        && active_obliterate_base_id >= 0 && active_obliterate_unit_id >= 0) {
            int base_id = active_obliterate_base_id;
            bool atrocity = !strcmp(label, "OBLIT");
            out << "{\"id\":\"base_obliteration:cancel\","
                "\"command\":\"respond_to_base_obliteration\",\"response\":\"cancel\","
                "\"meaning\":\"Cancel the native base-obliteration command without changing the base.\"},"
                "{\"id\":\"base_obliteration:proceed\","
                "\"command\":\"respond_to_base_obliteration\",\"response\":\"proceed\","
                "\"confirm_obliteration\":1,";
            if (atrocity) out << "\"confirm_atrocity\":1,";
            out << "\"destructive\":true,\"consequential\":true,"
                "\"atrocity_under_current_rules\":" << (atrocity ? "true" : "false")
                << ",\"meaning\":" << json_string(atrocity
                    ? "Permanently destroy this base through the native command and accept its atrocity consequences."
                    : "Permanently destroy this base through the native command; current rules do not classify it as an atrocity.")
                << "},"
                "{\"id\":\"base_obliteration:context\",\"kind\":\"information\","
                "\"base_id\":" << base_id << ",\"unit_id\":"
                << active_obliterate_unit_id;
            if (base_id >= 0 && base_id < *BaseCount) {
                out << ",\"base_name\":" << json_string(Bases[base_id].name)
                    << ",\"population\":" << static_cast<int>(Bases[base_id].pop_size)
                    << ",\"former_faction_id\":"
                    << static_cast<int>(Bases[base_id].faction_id_former);
            }
            out << '}';
        } else if (!strcmp(label, "MILVIRUS") || !strcmp(label, "HQVIRUS")) {
            out << "{\"id\":\"probe_sabotage_warning:abort\","
                "\"command\":\"respond_to_probe_sabotage_warning\",\"response\":\"abort\","
                "\"meaning\":\"Abort after the native warning about the target's extra security.\"},"
                "{\"id\":\"probe_sabotage_warning:proceed\","
                "\"command\":\"respond_to_probe_sabotage_warning\",\"response\":\"proceed\","
                "\"meaning\":\"Proceed despite the native extra-difficulty warning.\"}";
        } else if (!strcmp(label, "USENERVE")) {
            int attacker_id = deferred_action.command == "move_unit"
                && deferred_action.status == "pending" ? deferred_action.unit_id : -1;
            bool valid_attacker = attacker_id >= 0 && attacker_id < *VehCount
                && Vehs[attacker_id].faction_id == faction_id
                && has_abil(Vehs[attacker_id].unit_id, ABL_NERVE_GAS);
            out << "{\"id\":\"nerve_gas:conventional\","
                "\"command\":\"respond_to_nerve_gas\",\"response\":\"conventional\","
                "\"meaning\":\"Refuse chemical weapons and continue this attack conventionally.\"}";
            if (valid_attacker) {
                out << ",{\"id\":\"nerve_gas:commit\","
                    "\"command\":\"respond_to_nerve_gas\",\"response\":\"commit\","
                    "\"confirm_atrocity\":1,\"atrocity\":true,\"consequential\":true,"
                    "\"meaning\":\"Arm the unit's Nerve Gas Pods for this attack after explicitly accepting atrocity, sanctions, diplomatic, and casualty consequences.\"}";
            }
            out << ",{\"id\":\"nerve_gas:context\",\"kind\":\"information\","
                "\"action_id\":" << deferred_action.id
                << ",\"attacker_unit_id\":" << attacker_id
                << ",\"target_tile_id\":"
                << semantic_tile_id(deferred_action.target_x, deferred_action.target_y)
                << ",\"charter_atrocity_rules_active\":"
                << (un_charter() ? "true" : "false");
            if (valid_attacker) {
                out << ",\"attacker_name\":" << json_string(Vehs[attacker_id].name())
                    << ",\"prototype_id\":" << Vehs[attacker_id].unit_id;
            }
            int defender_id = veh_at(deferred_action.target_x, deferred_action.target_y);
            if (defender_id >= 0 && defender_id < *VehCount
            && Vehs[defender_id].faction_id != faction_id
            && (Vehs[defender_id].visibility & (1 << faction_id))) {
                out << ",\"visible_defender\":{\"unit_id\":" << defender_id
                    << ",\"name\":" << json_string(Vehs[defender_id].name())
                    << ",\"owner_faction_id\":"
                    << static_cast<int>(Vehs[defender_id].faction_id) << '}';
            }
            out << '}';
        } else if (combat_confirmation_label(label)) {
            bool hasty = !strcmp(label, "HASTY");
            out << "{\"id\":\"combat_odds:cancel\","
                "\"command\":\"respond_to_combat_confirmation\",\"response\":\"cancel\","
                "\"meaning\":" << json_string(hasty
                    ? "Await preparations and cancel this reduced-strength assault."
                    : "Cancel the queued attack and preserve the attacking unit's remaining state.") << "},"
                "{\"id\":\"combat_odds:proceed\","
                "\"command\":\"respond_to_combat_confirmation\",\"response\":\"proceed\","
                "\"confirm_attack\":1,\"consequential\":true,"
                "\"meaning\":" << json_string(hasty
                    ? "Order the hasty assault after explicitly accepting its reduced strength."
                    : "Proceed with the native combat after explicitly accepting the displayed odds.") << "},"
                "{\"id\":\"combat_odds:context\",\"kind\":\"information\","
                "\"risk_assessment\":" << json_string(hasty ? "hasty_assault"
                    : !strcmp(label, "BADIDEA") ? "strongly_against"
                    : "confirmation_requested")
                << ",\"action_id\":" << deferred_action.id;
            if (deferred_action.command == "move_unit") {
                out << ",\"attacker_unit_id\":" << deferred_action.unit_id
                    << ",\"target_tile_id\":"
                    << semantic_tile_id(deferred_action.target_x, deferred_action.target_y);
                int defender_id = veh_at(deferred_action.target_x, deferred_action.target_y);
                if (defender_id >= 0 && defender_id < *VehCount
                && Vehs[defender_id].faction_id != faction_id
                && (Vehs[defender_id].visibility & (1 << faction_id))) {
                    out << ",\"visible_defender\":{\"unit_id\":" << defender_id
                        << ",\"name\":" << json_string(Vehs[defender_id].name())
                        << ",\"owner_faction_id\":"
                        << static_cast<int>(Vehs[defender_id].faction_id) << '}';
                }
            }
            if (hasty) {
                out << ",\"displayed_strength\":{\"current\":"
                    << agent_popup_parse_number(0) << ",\"full\":"
                    << agent_popup_parse_number(1) << '}';
            } else if (strcmp(label, "BADIDEA") && agent_popup_parse_string(2)[0]
            && agent_popup_parse_string(3)[0]) {
                out << ",\"displayed_odds\":{\"attacker\":"
                    << json_string(agent_popup_parse_string(2))
                    << ",\"defender\":" << json_string(agent_popup_parse_string(3)) << '}';
            }
            out << '}';
        } else if (territorial_demand_label(label)) {
            int counterpart = territorial_incident_counterpart(faction_id);
            out << "{\"id\":\"territorial_demand:withdraw\","
                "\"command\":\"respond_to_territorial_incident\",\"response\":\"withdraw\","
                "\"meaning\":\"Comply and let the native game withdraw the offending units to eligible bases.\"},"
                "{\"id\":\"territorial_demand:mutual\","
                "\"command\":\"respond_to_territorial_incident\",\"response\":\"mutual_withdrawal\","
                "\"meaning\":\"Request reciprocal withdrawal of both factions' trespassing units.\"},"
                "{\"id\":\"territorial_demand:refuse\","
                "\"command\":\"respond_to_territorial_incident\",\"response\":\"refuse\","
                "\"confirm_hostility\":1,\"consequential\":true,\"meaning\":\"Refuse to withdraw; native diplomacy may break the treaty or begin Vendetta.\"},"
                "{\"id\":\"territorial_demand:context\",\"kind\":\"information\","
                "\"incident_type\":\"foreign_withdrawal_demand\","
                "\"counterpart_faction_id\":" << counterpart
                << ",\"counterpart_faction_name\":"
                << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                    ? MFactions[counterpart].formal_name_faction : "Unknown") << '}';
        } else if (!strcmp(label, "THISLANDISMYLAND")) {
            int counterpart = territorial_incident_counterpart(faction_id);
            out << "{\"id\":\"border_claim:cancel_base\","
                "\"command\":\"respond_to_territorial_incident\",\"response\":\"cancel\","
                "\"meaning\":\"Cancel founding the contested base.\"},"
                "{\"id\":\"border_claim:proceed\","
                "\"command\":\"respond_to_territorial_incident\",\"response\":\"proceed\","
                "\"confirm_hostility\":1,\"consequential\":true,\"meaning\":\"Found the base despite the claim and accept native diplomatic consequences.\"},"
                "{\"id\":\"border_claim:context\",\"kind\":\"information\","
                "\"incident_type\":\"contested_base_site\","
                "\"counterpart_faction_id\":" << counterpart
                << ",\"counterpart_faction_name\":"
                << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                    ? MFactions[counterpart].formal_name_faction : "Unknown") << '}';
        } else if (hostility_confirmation_label(label)) {
            int counterpart = territorial_incident_counterpart(faction_id);
            out << "{\"id\":\"hostility:cancel\","
                "\"command\":\"respond_to_territorial_incident\",\"response\":\"cancel\","
                "\"meaning\":\"Cancel the triggering attack or hostile entry and preserve current relations.\"},"
                "{\"id\":\"hostility:declare_vendetta\","
                "\"command\":\"respond_to_territorial_incident\","
                "\"response\":\"declare_vendetta\",\"confirm_hostility\":1,\"consequential\":true,"
                "\"meaning\":\"Break the current peace agreement and let the native hostile action continue.\"},"
                "{\"id\":\"hostility:context\",\"kind\":\"information\","
                "\"incident_type\":" << json_string(!strcmp(label, "BREAKINGTREATY")
                    ? "break_treaty" : !strcmp(label, "BREAKINGTRUCE")
                        ? "break_truce" : "begin_vendetta")
                << ",\"counterpart_faction_id\":" << counterpart
                << ",\"counterpart_faction_name\":"
                << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                    ? MFactions[counterpart].formal_name_faction : "Unknown") << '}';
        } else if (!strcmp(label, "TECHRANDOM")) {
            const char* names[] = {"Explore", "Discover", "Build", "Conquer"};
            for (int priority = 0; priority < 4; ++priority) {
                if (priority) out << ',';
                out << "{\"id\":\"research_priority:" << priority
                    << "\",\"command\":\"choose_research_priority\",\"priority\":"
                    << priority << ",\"name\":" << json_string(names[priority])
                    << ",\"focus_mode\":\"single_area\",\"meaning\":"
                    << json_string("Replace the current blind-research focus with exactly this one area; the native multiplayer synchronization packet is sent after confirmation.")
                    << '}';
            }
        } else if (reviewed_information_popup(label)) {
            out << "{\"id\":\"popup:acknowledge\",\"command\":\"acknowledge_popup\","
                "\"meaning\":\"Acknowledge this reviewed information-only game notification.\"}";
            const char* base_event = base_status_event(label);
            if (base_event) {
                int base_id = *CurrentBaseID;
                out << ",{\"id\":\"base_status:context\",\"kind\":\"information\","
                    "\"event\":" << json_string(base_event);
                if (base_id >= 0 && base_id < *BaseCount
                && Bases[base_id].faction_id == faction_id) {
                    out << ",\"base_id\":" << base_id << ",\"base_name\":"
                        << json_string(Bases[base_id].name);
                }
                out << '}';
            }
            if (production_completion_label(label)) {
                int base_id = -1;
                const char* completed_base_name = agent_popup_parse_string(0);
                const char* completed_item_name = agent_popup_parse_string(1);
                for (int candidate = 0; candidate < *BaseCount; ++candidate) {
                    if (Bases[candidate].faction_id == faction_id
                    && !strcmp(Bases[candidate].name, completed_base_name)) {
                        base_id = candidate;
                        break;
                    }
                }
                out << ",{\"id\":\"production_complete:context\","
                    "\"kind\":\"information\",\"event\":"
                    << json_string(!strncmp(label, "PRODUCEPROTO", 12)
                        ? "prototype_completed" : "production_completed")
                    << ",\"base_name\":" << json_string(completed_base_name)
                    << ",\"item_name\":" << json_string(completed_item_name)
                    << ",\"governor_managed\":"
                    << ((label[0] && label[strlen(label) - 1] == 'G') ? "true" : "false");
                if (base_id >= 0) out << ",\"base_id\":" << base_id;
                if (label[0] && label[strlen(label) - 1] == 'Q') {
                    out << ",\"queue_items_remaining\":"
                        << max(0, agent_popup_parse_number(1));
                }
                out << '}';
            }
            if ((!strcmp(label, "CALLSCOUNCIL") || !strncmp(label, "COUNCILHOT", 10))
            && CouncilProposal[faction_id] >= 0 && CouncilProposal[faction_id] < MaxProposalNum) {
                int proposal = CouncilProposal[faction_id];
                out << ",{\"id\":\"council:agenda\",\"kind\":\"information\","
                    "\"proposal_id\":" << proposal << ",\"proposal_name\":"
                    << json_string(Proposal[proposal].name) << '}';
            }
            if (!strncmp(label, "VENDETTA", 8)) {
                int other = *diplo_second_faction;
                if (other >= 1 && other < MaxPlayerNum) {
                    out << ",{\"id\":\"diplomacy:war_declaration\",\"kind\":\"information\","
                        "\"event\":\"vendetta_declared\",\"faction_id\":" << other
                        << ",\"faction_name\":" << json_string(MFactions[other].formal_name_faction)
                        << ",\"leader_name\":" << json_string(MFactions[other].name_leader) << '}';
                }
            }
        } else if (!strcmp(label, "COMM") || !strcmp(label, "COMMDIPLO")) {
            int other = *diplo_second_faction;
            if (!*MultiplayerActive || (other >= 1 && other < MaxPlayerNum
            && !is_human(other))) {
                out << "{\"id\":\"contact:accept\",\"command\":\"respond_to_contact\","
                    "\"response\":\"accept\",\"meaning\":\"Open live diplomacy with the caller.\"},";
            }
            out << "{\"id\":\"contact:decline\",\"command\":\"respond_to_contact\","
                "\"response\":\"decline\",\"meaning\":\"Close this incoming channel.\"}";
            if (!*MultiplayerActive && !strcmp(label, "COMMDIPLO")) {
                out << ",{\"id\":\"contact:later\",\"command\":\"respond_to_contact\","
                    "\"response\":\"later\",\"meaning\":\"Decline now but permit a later call.\"},"
                    "{\"id\":\"contact:block\",\"command\":\"respond_to_contact\","
                    "\"response\":\"block\",\"meaning\":\"Refuse further messages from this leader.\"}";
            }
            if (*MultiplayerActive && (other < 1 || other >= MaxPlayerNum
            || is_human(other))) {
                out << ",{\"id\":\"contact:accept_pending_validation\","
                    "\"kind\":\"capability_status\",\"supported\":false,"
                    "\"meaning\":\"Opening an AI conversation from an incoming multiplayer call remains blocked until its full two-client negotiation chain is validated.\"}";
            }
            if (other >= 1 && other < MaxPlayerNum) {
                out << ",{\"id\":\"contact:counterpart\",\"kind\":\"information\","
                    "\"faction_id\":" << other << ",\"faction_name\":"
                    << json_string(MFactions[other].formal_name_faction)
                    << ",\"leader_name\":" << json_string(MFactions[other].name_leader) << '}';
            }
        } else if (narrative_intro_popup(label)) {
            out << "{\"id\":\"popup:acknowledge\","
                "\"command\":\"acknowledge_popup\","
                "\"meaning\":\"Acknowledge this information-only faction introduction.\"},"
                "{\"id\":\"popup:context\",\"kind\":\"information\","
                "\"event\":\"faction_introduction\"}";
        } else if (!strncmp(label, "INTRONEW", 8) || !strncmp(label, "INTRO", 5)) {
            int other = *diplo_second_faction;
            out << "{\"id\":\"diplomacy:continue\",\"command\":\"continue_diplomacy\","
                "\"meaning\":\"Continue after the counterpart's diplomatic greeting.\"}";
            if (other >= 1 && other < MaxPlayerNum) {
                out << ",{\"id\":\"diplomacy:speaker\",\"kind\":\"information\","
                    "\"faction_id\":" << other << ",\"faction_name\":"
                    << json_string(MFactions[other].formal_name_faction)
                    << ",\"leader_name\":" << json_string(MFactions[other].name_leader) << '}';
            }
        } else if (!strcmp(label, "DIPLO") || !strcmp(label, "PROPOSAL")
        || !strcmp(label, "COUNTER1")) {
            BasePop* active = active_default_popup();
            if (active) append_diplomacy_popup_choices(out, active, label);
        } else if (!strcmp(label, "PROPOSECOMMLINK")
        || !strcmp(label, "PROPOSEATTACK")) {
            BasePop* active = active_default_popup();
            const char* target_kind = !strcmp(label, "PROPOSECOMMLINK")
                ? "commlink" : "joint_attack";
            bool comma = false;
            for (int target = 1; target < MaxPlayerNum; ++target) {
                if (!active || !popup_has_choice_id(active, target)) continue;
                if (comma) out << ',';
                comma = true;
                out << "{\"id\":\"diplomacy_target:" << target_kind << ':' << target
                    << "\",\"command\":\"choose_diplomacy_target\","
                    "\"target_kind\":" << json_string(target_kind)
                    << ",\"faction_id\":" << target << ",\"faction_name\":"
                    << json_string(MFactions[target].formal_name_faction)
                    << ",\"leader_name\":" << json_string(MFactions[target].name_leader)
                    << ",\"meaning\":" << json_string(!strcmp(label, "PROPOSECOMMLINK")
                        ? "Ask the counterpart for this faction's commlink frequency."
                        : "Propose this faction as the target of a joint attack.") << '}';
            }
            if (comma) out << ',';
            out << "{\"id\":\"diplomacy_target:cancel\","
                "\"command\":\"cancel_diplomacy_selection\","
                "\"meaning\":\"Cancel this target selection and return to the conversation.\"}";
        } else if (!strcmp(label, "PROPOSEBASE")) {
            BasePop* active = active_default_popup();
            bool comma = false;
            for (int base_id = 0; base_id < *BaseCount; ++base_id) {
                if (!active || !popup_has_choice_id(active, base_id)) continue;
                if (comma) out << ',';
                comma = true;
                out << "{\"id\":\"diplomacy_base_target:" << base_id
                    << "\",\"command\":\"choose_diplomacy_base_target\","
                    "\"target_base_id\":" << base_id << ",\"base_name\":"
                    << json_string(Bases[base_id].name) << ",\"owner_faction_id\":"
                    << static_cast<int>(Bases[base_id].faction_id)
                    << ",\"meaning\":\"Demand this exact base revealed by the native negotiation list.\"}";
            }
            if (comma) out << ',';
            out << "{\"id\":\"diplomacy_base_target:cancel\","
                "\"command\":\"cancel_diplomacy_selection\","
                "\"meaning\":\"Cancel this base selection and return to the conversation.\"}";
        } else if (friendly_map_exchange_label(label)) {
            int counterpart = *diplo_second_faction;
            int target = *diplo_intel_faction;
            int tech_id = *diplo_entry_id;
            out << "{\"id\":\"friendly_map:reject\","
                "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                "\"meaning\":\"Decline the offered map-for-technology exchange.\"},"
                "{\"id\":\"friendly_map:accept\","
                "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\","
                "\"technology_id\":" << tech_id << ",\"technology_owned\":"
                << (tech_id >= 0 && tech_id < MaxTechnologyNum
                    && (TechOwners[tech_id] & (1 << faction_id)) ? "true" : "false")
                << ",\"meaning\":\"Transmit the exact named owned technology for the target faction's territory map.\"},"
                "{\"id\":\"friendly_map:terms\",\"kind\":\"information\","
                "\"offer_type\":\"territory_map_for_technology\","
                "\"counterpart_faction_id\":" << counterpart
                << ",\"counterpart_faction_name\":"
                << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                    ? MFactions[counterpart].formal_name_faction : "Unknown")
                << ",\"target_faction_id\":" << target
                << ",\"target_faction_name\":"
                << json_string(target >= 1 && target < MaxPlayerNum
                    ? MFactions[target].formal_name_faction : "Unknown")
                << ",\"technology_id\":" << tech_id;
            if (tech_id >= 0 && tech_id < MaxTechnologyNum
            && (TechOwners[tech_id] & (1 << faction_id))) {
                out << ",\"technology_name\":" << json_string(Tech[tech_id].name);
            }
            out << '}';
        } else if (enemy_map_offer_mode(label) >= 0) {
            int mode = enemy_map_offer_mode(label);
            int counterpart = *diplo_second_faction;
            int target = *diplo_intel_faction;
            int price = mode == 1 ? agent_popup_parse_number(0) : 0;
            int tech_id = mode == 2 ? *diplo_entry_id : -1;
            out << "{\"id\":\"enemy_map:reject\","
                "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                "\"meaning\":\"Decline the offered intelligence map.\"},"
                "{\"id\":\"enemy_map:accept\","
                "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\"";
            if (mode == 1) {
                out << ",\"energy_credits\":" << price << ",\"affordable\":"
                    << (price >= 0 && Factions[faction_id].energy_credits >= price
                        ? "true" : "false");
            } else {
                out << ",\"technology_id\":" << tech_id << ",\"technology_owned\":"
                    << (tech_id >= 0 && tech_id < MaxTechnologyNum
                        && (TechOwners[tech_id] & (1 << faction_id)) ? "true" : "false");
            }
            out << ",\"meaning\":" << json_string(mode == 1
                    ? "Pay the exact quoted energy price for the target faction's installation map."
                    : "Transmit the exact named owned technology for the target faction's installation map.")
                << "},{\"id\":\"enemy_map:terms\",\"kind\":\"information\","
                "\"offer_type\":\"enemy_installation_map\",\"payment_type\":"
                << json_string(mode == 1 ? "energy" : "technology")
                << ",\"counterpart_faction_id\":" << counterpart
                << ",\"counterpart_faction_name\":"
                << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                    ? MFactions[counterpart].formal_name_faction : "Unknown")
                << ",\"target_faction_id\":" << target
                << ",\"target_faction_name\":"
                << json_string(target >= 1 && target < MaxPlayerNum
                    ? MFactions[target].formal_name_faction : "Unknown");
            if (mode == 1) {
                out << ",\"energy_credits\":" << price;
            } else {
                out << ",\"technology_id\":" << tech_id;
                if (tech_id >= 0 && tech_id < MaxTechnologyNum
                && (TechOwners[tech_id] & (1 << faction_id))) {
                    out << ",\"technology_name\":" << json_string(Tech[tech_id].name);
                }
            }
            out << '}';
        } else if (introduced_commlink_offer_mode(label) >= 0) {
            int mode = introduced_commlink_offer_mode(label);
            int counterpart = *diplo_second_faction;
            int target = *diplo_tech_faction;
            int price = mode == 2 ? agent_popup_parse_number(0) : 0;
            int tech_id = mode == 1 ? *diplo_entry_id : -1;
            out << "{\"id\":\"introduced_commlink:reject\","
                "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                "\"meaning\":\"Decline the offered third-party commlink frequency.\"},"
                "{\"id\":\"introduced_commlink:accept\","
                "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\"";
            if (mode == 2) {
                out << ",\"energy_credits\":" << price << ",\"affordable\":"
                    << (price >= 0 && Factions[faction_id].energy_credits >= price
                        ? "true" : "false");
            } else if (mode == 1) {
                out << ",\"technology_id\":" << tech_id << ",\"technology_owned\":"
                    << (tech_id >= 0 && tech_id < MaxTechnologyNum
                        && (TechOwners[tech_id] & (1 << faction_id)) ? "true" : "false");
            }
            out << ",\"meaning\":" << json_string(mode == 0
                    ? "Accept this commlink frequency as a free introduction."
                    : mode == 1
                        ? "Transmit the exact named owned technology for this commlink frequency."
                        : "Pay the exact quoted energy price for this commlink frequency.")
                << "},{\"id\":\"introduced_commlink:terms\",\"kind\":\"information\","
                "\"offer_type\":\"introduced_commlink\",\"payment_type\":"
                << json_string(mode == 0 ? "free" : mode == 1 ? "technology" : "energy")
                << ",\"counterpart_faction_id\":" << counterpart
                << ",\"counterpart_faction_name\":"
                << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                    ? MFactions[counterpart].formal_name_faction : "Unknown")
                << ",\"target_faction_id\":" << target
                << ",\"target_faction_name\":"
                << json_string(target >= 1 && target < MaxPlayerNum
                    ? MFactions[target].formal_name_faction : "Unknown");
            if (mode == 2) {
                out << ",\"energy_credits\":" << price;
            } else if (mode == 1) {
                out << ",\"technology_id\":" << tech_id;
                if (tech_id >= 0 && tech_id < MaxTechnologyNum
                && (TechOwners[tech_id] & (1 << faction_id))) {
                    out << ",\"technology_name\":" << json_string(Tech[tech_id].name);
                }
            }
            out << '}';
        } else if (council_vote_bargain_tech_count(label) >= 0) {
            int tech_count = council_vote_bargain_tech_count(label);
            int counterpart = *diplo_second_faction;
            int price = agent_popup_parse_number(0);
            int tech_ids[] = {
                *diplo_entry_id, *diplo_tech_id2, *diplo_tech_id3, *diplo_tech_id4,
            };
            bool valid_technologies = tech_count > 0;
            for (int index = 0; index < tech_count; ++index) {
                int tech_id = tech_ids[index];
                if (tech_id < 0 || tech_id >= MaxTechnologyNum
                || !(TechOwners[tech_id] & (1 << faction_id))) {
                    valid_technologies = false;
                }
            }
            out << "{\"id\":\"council_vote_bargain:reject\","
                "\"command\":\"respond_to_council_vote_bargain\",\"payment\":\"none\","
                "\"meaning\":\"Decline this requested price without purchasing the vote.\"},"
                "{\"id\":\"council_vote_bargain:energy\","
                "\"command\":\"respond_to_council_vote_bargain\",\"payment\":\"energy\","
                "\"energy_credits\":" << price << ",\"affordable\":"
                << (price >= 0 && Factions[faction_id].energy_credits >= price ? "true" : "false")
                << ",\"meaning\":\"Pay the exact native energy price for this vote commitment.\"}";
            if (valid_technologies) {
                out << ",{\"id\":\"council_vote_bargain:technologies\","
                    "\"command\":\"respond_to_council_vote_bargain\",\"payment\":\"technologies\","
                    "\"technology_ids\":[";
                for (int index = 0; index < tech_count; ++index) {
                    if (index) out << ',';
                    out << tech_ids[index];
                }
                out << "],\"meaning\":\"Transmit the complete named technology bundle for this vote commitment.\"}";
            }
            out << ",{\"id\":\"council_vote_bargain:terms\",\"kind\":\"information\","
                "\"offer_type\":\"council_vote_bargain\",\"requested_ballot\":"
                << json_string(council_vote_bargain_ballot(label))
                << ",\"counterpart_faction_id\":" << counterpart
                << ",\"counterpart_faction_name\":"
                << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                    ? MFactions[counterpart].formal_name_faction : "Unknown")
                << ",\"energy_credits\":" << price << ",\"technologies\":[";
            for (int index = 0; index < tech_count; ++index) {
                if (index) out << ',';
                int tech_id = tech_ids[index];
                out << "{\"technology_id\":" << tech_id;
                if (tech_id >= 0 && tech_id < MaxTechnologyNum
                && (TechOwners[tech_id] & (1 << faction_id))) {
                    out << ",\"technology_name\":" << json_string(Tech[tech_id].name)
                        << ",\"owned\":true";
                } else {
                    out << ",\"owned\":false";
                }
                out << '}';
            }
            out << "]}";
        } else if (incoming_council_vote_offer_label(label)) {
            int candidate = incoming_council_vote_candidate(faction_id);
            bool technology_offer = !strcmp(label, "VOTEFORMETECH");
            int tech1 = *diplo_tech_id1;
            int tech2 = *diplo_vote_offer_tech_id2;
            bool valid_technologies = !technology_offer
                || (tech1 >= 0 && tech1 < MaxTechnologyNum
                    && tech2 >= 0 && tech2 < MaxTechnologyNum && tech1 != tech2);
            out << "{\"id\":\"incoming_vote_offer:reject\","
                "\"command\":\"respond_to_incoming_vote_offer\",\"response\":\"reject\","
                "\"meaning\":\"Reject the offered payment and keep the Council ballot uncommitted by this bargain.\"}";
            if (candidate >= 1 && valid_technologies) {
                out << ",{\"id\":\"incoming_vote_offer:accept\","
                    "\"command\":\"respond_to_incoming_vote_offer\",\"response\":\"accept\","
                    "\"candidate_faction_id\":" << candidate
                    << ",\"confirm_vote_commitment\":1,\"consequential\":true";
                if (technology_offer) {
                    out << ",\"technology_ids\":[" << tech1 << ',' << tech2 << ']';
                } else {
                    out << ",\"energy_credits_received\":"
                        << max(0, agent_popup_parse_number(0));
                }
                out << ",\"meaning\":\"Accept the exact offered payment and commit the current Council ballot to the named candidate.\"}";
            }
            out << ",{\"id\":\"incoming_vote_offer:terms\",\"kind\":\"information\","
                "\"offer_type\":" << json_string(technology_offer
                    ? "council_vote_for_technologies" : "council_vote_for_energy")
                << ",\"candidate_faction_id\":" << candidate
                << ",\"candidate_name\":" << json_string(candidate >= 1
                    ? MFactions[candidate].formal_name_faction : "Unknown")
                << ",\"candidate_leader_name\":"
                << json_string(agent_popup_parse_string(3));
            if (technology_offer) {
                out << ",\"technologies\":[{\"technology_id\":" << tech1;
                if (tech1 >= 0 && tech1 < MaxTechnologyNum) {
                    out << ",\"technology_name\":" << json_string(Tech[tech1].name);
                }
                out << "},{\"technology_id\":" << tech2;
                if (tech2 >= 0 && tech2 < MaxTechnologyNum) {
                    out << ",\"technology_name\":" << json_string(Tech[tech2].name);
                }
                out << "}]";
            } else {
                out << ",\"energy_credits_received\":"
                    << max(0, agent_popup_parse_number(0));
            }
            out << '}';
        } else if (!strcmp(label, "COUNCILISSUES")) {
            BasePop* active = active_default_popup();
            if (active) append_council_proposal_choices(out, active);
        } else if (!strcmp(label, "COUNCILVOTE")) {
            out << "{\"id\":\"council_vote:yea\",\"command\":\"cast_council_vote\","
                "\"response\":\"yea\",\"meaning\":\"Vote YEA on the active motion.\"},"
                "{\"id\":\"council_vote:nay\",\"command\":\"cast_council_vote\","
                "\"response\":\"nay\",\"meaning\":\"Vote NAY on the active motion.\"}";
        } else if (!strcmp(label, "COUNCILVOTEGOV")) {
            BasePop* active = active_default_popup();
            bool comma = false;
            for (int candidate = 1; candidate < MaxPlayerNum; ++candidate) {
                if (!is_alive(candidate) || is_alien(candidate)
                || !popup_has_choice_id(active, candidate)) continue;
                if (comma) out << ',';
                comma = true;
                out << "{\"id\":\"council_candidate:" << candidate
                    << "\",\"command\":\"cast_council_vote\",\"candidate_faction_id\":"
                    << candidate << ",\"faction_name\":"
                    << json_string(MFactions[candidate].formal_name_faction)
                    << ",\"leader_name\":" << json_string(MFactions[candidate].name_leader) << '}';
            }
            if (!comma) {
                out << "{\"id\":\"council:no_reviewed_candidates\",\"kind\":\"capability_status\","
                    "\"supported\":false,\"meaning\":\"No reviewed candidate id matched the native ballot.\"}";
            }
        } else if (technology_trade_label(label)) {
            out << "{\"id\":\"diplomatic_offer:reject\","
                << "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                << "\"meaning\":\"Reject the proposed exchange.\"},"
                << "{\"id\":\"diplomatic_offer:accept\","
                << "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\","
                << "\"meaning\":\"Accept the proposed exchange exactly as shown.\"},"
                << "{\"id\":\"diplomatic_offer:terms\",\"kind\":\"information\","
                << "\"offer_type\":\"technology_or_map_exchange\"";
            append_trade_technology_context(out, faction_id, label);
            out << '}';
        } else if (technology_demand_label(label)) {
            bool demand_context_valid = demanded_technology_context_valid(label, faction_id);
            out << "{\"id\":\"diplomatic_demand:reject\","
                << "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                << "\"meaning\":\"Refuse the demand for research data.\"}";
            if (demand_context_valid) {
                out << ",{\"id\":\"diplomatic_demand:concede\","
                    << "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\","
                    << "\"meaning\":\"Transmit the complete demanded research bundle without bargaining.\"}";
                if (technology_demand_counter_label(label)) {
                    int price = max(0, agent_popup_parse_number(0));
                    int reciprocal = *diplo_tech_id2;
                    int counterpart = *diplo_second_faction;
                    out << ",{\"id\":\"diplomatic_demand:counter_energy\","
                        << "\"command\":\"respond_to_diplomatic_offer\","
                        << "\"response\":\"counter\",\"payment\":\"energy\","
                        << "\"energy_credits_requested\":" << price
                        << ",\"meaning\":\"Ask the counterpart to pay the native quoted energy price for the demanded technology.\"}";
                    if (reciprocal >= 0 && reciprocal < MaxTechnologyNum
                    && counterpart >= 1 && counterpart < MaxPlayerNum
                    && (TechOwners[reciprocal] & (1 << counterpart))
                    && !(TechOwners[reciprocal] & (1 << faction_id))) {
                        out << ",{\"id\":\"diplomatic_demand:counter_technology\","
                            << "\"command\":\"respond_to_diplomatic_offer\","
                            << "\"response\":\"counter\",\"payment\":\"technologies\","
                            << "\"technology_id\":" << reciprocal
                            << ",\"technology_name\":" << json_string(Tech[reciprocal].name)
                            << ",\"meaning\":\"Request the exact native reciprocal technology named by this dialog.\"}";
                    }
                }
            } else {
                out << ",{\"id\":\"diplomatic_demand:context_invalid\","
                    << "\"kind\":\"capability_status\",\"supported\":false,"
                    << "\"meaning\":\"The exact native demand bundle is incomplete or no longer owned. Refuse safely or report a capability gap; do not concede.\"}";
            }
            out << ",{\"id\":\"diplomatic_demand:terms\",\"kind\":\"information\","
                << "\"offer_type\":\"technology_demand\"";
            append_demand_technology_context(out, faction_id, label);
            out << '}';
        } else if (technology_demand_followup_label(label)) {
            bool demand_context_valid = demanded_technology_context_valid(label, faction_id);
            out << "{\"id\":\"diplomatic_demand_followup:reject\","
                << "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                << "\"meaning\":\"End this demand after the counterpart rejects the counteroffer.\"}";
            if (demand_context_valid) {
                out << ",{\"id\":\"diplomatic_demand_followup:concede\","
                    << "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\","
                    << "\"meaning\":\"Concede the original single-technology demand after the rejected counteroffer.\"}";
            }
            out << ",{\"id\":\"diplomatic_demand_followup:terms\","
                << "\"kind\":\"information\",\"offer_type\":\"technology_demand_followup\","
                << "\"rejected_counter_type\":"
                << json_string(!strcmp(label, "DEMANDTECHAGAIN1") ? "energy" : "technology");
            append_demand_technology_context(out, faction_id, label);
            out << '}';
        } else if (relationship_offer_label(label)) {
            bool treaty = strstr(label, "TREATY") != NULL;
            bool pact = !strcmp(label, "SWEARAPACT");
            int other = *diplo_second_faction;
            out << "{\"id\":\"diplomatic_relation:accept\","
                << "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\","
                << "\"meaning\":" << json_string(pact
                    ? "Accept the proposed Pact of Brotherhood."
                    : treaty ? "Accept the proposed Treaty of Friendship."
                    : "Accept the proposed Blood Truce.") << "},"
                << "{\"id\":\"diplomatic_relation:reject\","
                << "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                << "\"meaning\":" << json_string(pact
                    ? "Decline the proposed Pact of Brotherhood."
                    : treaty ? "Decline the proposed Treaty of Friendship."
                    : "Reject the proposed Blood Truce and continue Vendetta.") << "},"
                << "{\"id\":\"diplomatic_relation:terms\",\"kind\":\"information\","
                << "\"offer_type\":" << json_string(
                    pact ? "pact" : treaty ? "treaty" : "truce")
                << ",\"counterpart_faction_id\":" << other
                << ",\"counterpart_faction_name\":"
                << json_string(other >= 1 && other < MaxPlayerNum
                    ? MFactions[other].formal_name_faction : "Unknown") << '}';
        } else if (attack_demand_label(label)) {
            int requester = *diplo_second_faction;
            int target = *diplo_third_faction;
            out << "{\"id\":\"diplomatic_war_request:reject\","
                << "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                << "\"meaning\":\"Refuse to join the requested Vendetta.\"},"
                << "{\"id\":\"diplomatic_war_request:accept\","
                << "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\","
                << "\"meaning\":\"Agree to join the requested Vendetta against the named faction.\"},"
                << "{\"id\":\"diplomatic_war_request:terms\",\"kind\":\"information\","
                << "\"offer_type\":\"join_vendetta_request\",\"requester_faction_id\":"
                << requester << ",\"requester_faction_name\":"
                << json_string(requester >= 1 && requester < MaxPlayerNum
                    ? MFactions[requester].formal_name_faction : "Unknown")
                << ",\"target_faction_id\":" << target << ",\"target_faction_name\":"
                << json_string(target >= 1 && target < MaxPlayerNum
                    ? MFactions[target].formal_name_faction : "Unknown") << '}';
        } else if (joint_attack_energy_counteroffer_label(label)
        || joint_attack_counteroffer_tech_count(label) > 0) {
            bool energy_payment = joint_attack_energy_counteroffer_label(label);
            int counterpart = *diplo_second_faction;
            int target = *diplo_trade_faction_id;
            int price = energy_payment ? agent_popup_parse_number(0) : 0;
            int tech_count = energy_payment ? 0 : joint_attack_counteroffer_tech_count(label);
            int tech_ids[] = {
                *diplo_entry_id, *diplo_tech_id2, *diplo_tech_id3, *diplo_tech_id4,
            };
            bool valid_payment = energy_payment
                ? price >= 0 && Factions[faction_id].energy_credits >= price
                : tech_count >= 1;
            for (int index = 0; index < tech_count; ++index) {
                int tech_id = tech_ids[index];
                if (tech_id < 0 || tech_id >= MaxTechnologyNum
                || !(TechOwners[tech_id] & (1 << faction_id))) {
                    valid_payment = false;
                }
            }
            out << "{\"id\":\"joint_attack_counteroffer:reject\","
                "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                "\"meaning\":\"Reject this price and do not purchase the requested joint Vendetta.\"}";
            if (valid_payment) {
                out << ",{\"id\":\"joint_attack_counteroffer:accept\","
                    "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\",";
                if (energy_payment) {
                    out << "\"energy_credits\":" << price;
                } else {
                    out << "\"technology_ids\":[";
                    for (int index = 0; index < tech_count; ++index) {
                        if (index) out << ',';
                        out << tech_ids[index];
                    }
                    out << ']';
                }
                out << ",\"consequential\":true,"
                    "\"meaning\":\"Pay the complete native counteroffer and induce the counterpart to begin Vendetta against the named target.\"}";
            }
            out << ",{\"id\":\"joint_attack_counteroffer:terms\","
                "\"kind\":\"information\",\"offer_type\":\"joint_attack_counteroffer\","
                "\"payment_type\":" << json_string(energy_payment ? "energy" : "technologies")
                << ",\"counterpart_faction_id\":" << counterpart
                << ",\"counterpart_faction_name\":"
                << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                    ? MFactions[counterpart].formal_name_faction : "Unknown")
                << ",\"target_faction_id\":" << target
                << ",\"target_faction_name\":"
                << json_string(target >= 1 && target < MaxPlayerNum
                    ? MFactions[target].formal_name_faction : "Unknown");
            if (energy_payment) {
                out << ",\"energy_credits\":" << price
                    << ",\"affordable\":" << (valid_payment ? "true" : "false");
            } else {
                out << ",\"technologies\":[";
                for (int index = 0; index < tech_count; ++index) {
                    if (index) out << ',';
                    int tech_id = tech_ids[index];
                    out << "{\"technology_id\":" << tech_id;
                    if (tech_id >= 0 && tech_id < MaxTechnologyNum) {
                        out << ",\"technology_name\":" << json_string(Tech[tech_id].name)
                            << ",\"owned\":"
                            << ((TechOwners[tech_id] & (1 << faction_id)) ? "true" : "false");
                    } else {
                        out << ",\"owned\":false";
                    }
                    out << '}';
                }
                out << ']';
            }
            out << '}';
        } else if (bribe_demand_label(label) || !strcmp(label, "WEASELOUT")) {
            int requester = *diplo_second_faction;
            int full_amount = ParseNumTable[0];
            int counter_amount = ParseNumTable[1];
            out << "{\"id\":\"diplomatic_energy_demand:reject\","
                << "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                << "\"meaning\":\"Refuse the energy-credit demand.\"},"
                << "{\"id\":\"diplomatic_energy_demand:pay_full\","
                << "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\","
                << "\"energy_credits\":" << full_amount
                << ",\"affordable\":" << (Factions[faction_id].energy_credits >= full_amount ? "true" : "false")
                << ",\"meaning\":\"Pay the demanded amount.\"}";
            if (bribe_demand_label(label)) {
                out << ",{\"id\":\"diplomatic_energy_demand:counter\","
                    << "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"counter\","
                    << "\"energy_credits\":" << counter_amount
                    << ",\"affordable\":" << (Factions[faction_id].energy_credits >= counter_amount ? "true" : "false")
                    << ",\"meaning\":\"Offer the lower amount shown by the native negotiation.\"}";
            }
            out << ",{\"id\":\"diplomatic_energy_demand:terms\",\"kind\":\"information\","
                << "\"offer_type\":\"energy_demand\",\"requester_faction_id\":" << requester
                << ",\"requester_faction_name\":"
                << json_string(requester >= 1 && requester < MaxPlayerNum
                    ? MFactions[requester].formal_name_faction : "Unknown")
                << ",\"full_amount\":" << full_amount;
            if (bribe_demand_label(label)) out << ",\"counter_amount\":" << counter_amount;
            out << '}';
        } else if (prototype_purchase_offer_label(label)) {
            int counterpart = *diplo_second_faction;
            int prototype_id = *diplo_tech_id1 - 97;
            int price = agent_popup_parse_number(0);
            const char* prototype_name = agent_popup_parse_string(0);
            if (prototype_id < 0 || prototype_id >= MaxProtoNum
            || !Units[prototype_id].name[0]) {
                out << "{\"id\":\"prototype_purchase:invalid\","
                    "\"kind\":\"capability_status\",\"supported\":false,"
                    "\"meaning\":\"The native offer did not expose a reviewed prototype id.\"}";
            } else {
                out << "{\"id\":\"prototype_purchase:reject\","
                    "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                    "\"meaning\":\"Decline this prototype-plan purchase.\"},"
                    "{\"id\":\"prototype_purchase:accept\","
                    "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\","
                    "\"prototype_id\":" << prototype_id << ",\"energy_credits\":" << price
                    << ",\"affordable\":"
                    << (price >= 0 && Factions[faction_id].energy_credits >= price ? "true" : "false")
                    << ",\"meaning\":\"Pay the quoted price and acquire this exact unit design through the native trade.\"},"
                    "{\"id\":\"prototype_purchase:terms\",\"kind\":\"information\","
                    "\"offer_type\":\"prototype_purchase\",\"counterpart_faction_id\":"
                    << counterpart << ",\"counterpart_faction_name\":"
                    << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                        ? MFactions[counterpart].formal_name_faction : "Unknown")
                    << ",\"prototype_id\":" << prototype_id << ",\"prototype_name\":"
                    << json_string(Units[prototype_id].name)
                    << ",\"offer_display_name\":"
                    << json_string(prototype_name[0] ? prototype_name : Units[prototype_id].name)
                    << ",\"energy_credits\":" << price << '}';
            }
        } else if (commlink_purchase_offer_label(label)) {
            int counterpart = *diplo_second_faction;
            int target = *diplo_tech_id1 - 89;
            int price = agent_popup_parse_number(0);
            if (target < 1 || target >= MaxPlayerNum || target == faction_id) {
                out << "{\"id\":\"commlink_purchase:invalid\","
                    "\"kind\":\"capability_status\",\"supported\":false,"
                    "\"meaning\":\"The native offer did not expose a reviewed target faction id.\"}";
            } else {
                out << "{\"id\":\"commlink_purchase:reject\","
                    "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                    "\"meaning\":\"Decline this commlink-frequency purchase.\"},"
                    "{\"id\":\"commlink_purchase:accept\","
                    "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\","
                    "\"target_faction_id\":" << target << ",\"energy_credits\":" << price
                    << ",\"affordable\":"
                    << (price >= 0 && Factions[faction_id].energy_credits >= price ? "true" : "false")
                    << ",\"meaning\":\"Pay the quoted price and acquire this faction's commlink through the native trade.\"},"
                    "{\"id\":\"commlink_purchase:terms\",\"kind\":\"information\","
                    "\"offer_type\":\"commlink_purchase\",\"counterpart_faction_id\":"
                    << counterpart << ",\"counterpart_faction_name\":"
                    << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                        ? MFactions[counterpart].formal_name_faction : "Unknown")
                    << ",\"target_faction_id\":" << target << ",\"target_faction_name\":"
                    << json_string(MFactions[target].formal_name_faction)
                    << ",\"energy_credits\":" << price << '}';
            }
        } else if (commlink_sale_offer_label(label)) {
            int counterpart = *diplo_second_faction;
            int target = *diplo_trade_faction_id;
            int price = agent_popup_parse_number(0);
            bool valid_target = target >= 1 && target < MaxPlayerNum && target != faction_id
                && has_treaty(faction_id, target, DIPLO_COMMLINK);
            out << "{\"id\":\"commlink_sale:reject\","
                "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                "\"meaning\":\"Decline to forward this commlink frequency.\"}";
            if (valid_target) {
                out << ",{\"id\":\"commlink_sale:accept\","
                    "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\","
                    "\"target_faction_id\":" << target << ",\"energy_credits_received\":"
                    << price << ",\"meaning\":\"Forward this known commlink for the quoted native payment.\"}";
            }
            out << ",{\"id\":\"commlink_sale:terms\",\"kind\":\"information\","
                "\"offer_type\":\"commlink_sale\",\"counterpart_faction_id\":"
                << counterpart << ",\"counterpart_faction_name\":"
                << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                    ? MFactions[counterpart].formal_name_faction : "Unknown")
                << ",\"target_faction_id\":" << target;
            if (valid_target) {
                out << ",\"target_faction_name\":"
                    << json_string(MFactions[target].formal_name_faction);
            }
            out << ",\"energy_credits_received\":" << price
                << ",\"transfer_available\":" << (valid_target ? "true" : "false") << '}';
        } else if (commlink_technology_exchange_label(label)) {
            int counterpart = *diplo_second_faction;
            int target = *diplo_trade_faction_id;
            int tech_id = *diplo_entry_id;
            bool valid_target = target >= 1 && target < MaxPlayerNum && target != faction_id
                && !has_treaty(faction_id, target, DIPLO_COMMLINK);
            bool valid_tech = tech_id >= 0 && tech_id < MaxTechnologyNum
                && (TechOwners[tech_id] & (1 << faction_id));
            out << "{\"id\":\"commlink_technology_exchange:reject\","
                "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                "\"meaning\":\"Decline this commlink-for-research exchange.\"}";
            if (valid_target && valid_tech) {
                out << ",{\"id\":\"commlink_technology_exchange:accept\","
                    "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\","
                    "\"target_faction_id\":" << target << ",\"technology_id\":" << tech_id
                    << ",\"meaning\":\"Transmit the named owned technology and acquire this commlink through the native exchange.\"}";
            }
            out << ",{\"id\":\"commlink_technology_exchange:terms\","
                "\"kind\":\"information\",\"offer_type\":\"commlink_for_technology\","
                "\"counterpart_faction_id\":" << counterpart
                << ",\"counterpart_faction_name\":"
                << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                    ? MFactions[counterpart].formal_name_faction : "Unknown")
                << ",\"target_faction_id\":" << target;
            if (valid_target) {
                out << ",\"target_faction_name\":"
                    << json_string(MFactions[target].formal_name_faction);
            }
            out << ",\"technology_id\":" << tech_id;
            if (valid_tech) out << ",\"technology_name\":" << json_string(Tech[tech_id].name);
            out << ",\"exchange_available\":"
                << (valid_target && valid_tech ? "true" : "false") << '}';
        } else if (base_purchase_offer_label(label)
        || base_technology_exchange_label(label)) {
            bool energy_payment = base_purchase_offer_label(label);
            int counterpart = *diplo_second_faction;
            int base_id = *diplo_ask_base_swap_id;
            int price = energy_payment ? agent_popup_parse_number(0) : 0;
            if (base_id < 0 || base_id >= *BaseCount
            || Bases[base_id].faction_id != counterpart) {
                out << "{\"id\":\"base_purchase:invalid\","
                    "\"kind\":\"capability_status\",\"supported\":false,"
                    "\"meaning\":\"The native offer did not expose a reviewed counterpart-owned base.\"}";
            } else {
                out << "{\"id\":\"base_purchase:reject\","
                    "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                    "\"meaning\":\"Reject this base-transfer offer.\"},"
                    "{\"id\":\"base_purchase:accept\","
                    "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\","
                    "\"target_base_id\":" << base_id;
                if (energy_payment) {
                    out << ",\"energy_credits\":" << price << ",\"affordable\":"
                        << (price >= 0 && Factions[faction_id].energy_credits >= price
                            ? "true" : "false");
                } else {
                    out << ",\"shares_all_owned_technologies\":true";
                }
                out << ",\"meaning\":" << json_string(energy_payment
                        ? "Pay the exact native price and acquire this base."
                        : "Acquire this base in exchange for sharing every currently owned technology with the counterpart.")
                    << "},{\"id\":\"base_purchase:terms\",\"kind\":\"information\","
                    "\"offer_type\":" << json_string(energy_payment
                        ? "base_purchase" : "base_for_all_technologies")
                    << ",\"counterpart_faction_id\":" << counterpart
                    << ",\"counterpart_faction_name\":"
                    << json_string(MFactions[counterpart].formal_name_faction)
                    << ",\"target_base_id\":" << base_id << ",\"base_name\":"
                    << json_string(Bases[base_id].name);
                if (energy_payment) out << ",\"energy_credits\":" << price;
                else out << ",\"shares_all_owned_technologies\":true";
                out << '}';
            }
        } else if (technology_purchase_offer_label(label)) {
            int counterpart = *diplo_second_faction;
            int tech_id = *diplo_tech_id1;
            int price = ParseNumTable[0];
            if (tech_id < 0 || tech_id >= MaxTechnologyNum) {
                out << "{\"id\":\"technology_purchase:invalid\","
                    "\"kind\":\"capability_status\",\"supported\":false,"
                    "\"meaning\":\"The native offer did not expose a reviewed technology id.\"}";
            } else {
                out << "{\"id\":\"technology_purchase:reject\","
                    "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                    "\"meaning\":\"Decline this technology purchase.\"},"
                    "{\"id\":\"technology_purchase:accept\","
                    "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\","
                    "\"technology_id\":" << tech_id << ",\"energy_credits\":" << price
                    << ",\"affordable\":"
                    << (Factions[faction_id].energy_credits >= price ? "true" : "false")
                    << ",\"meaning\":\"Pay the quoted price and acquire this technology through the native trade.\"},"
                    "{\"id\":\"technology_purchase:terms\",\"kind\":\"information\","
                    "\"offer_type\":\"technology_purchase\",\"counterpart_faction_id\":"
                    << counterpart << ",\"counterpart_faction_name\":"
                    << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                        ? MFactions[counterpart].formal_name_faction : "Unknown")
                    << ",\"technology_id\":" << tech_id << ",\"technology_name\":"
                    << json_string(Tech[tech_id].name) << ",\"category\":"
                    << tech_category(tech_id) << ",\"energy_credits\":" << price << '}';
            }
        } else if (technology_sale_offer_label(label)) {
            int counterpart = *diplo_second_faction;
            int primary = *diplo_entry_id;
            int alternate = *diplo_tech_id2;
            int price = ParseNumTable[0];
            if (primary < 0 || primary >= MaxTechnologyNum) {
                out << "{\"id\":\"technology_sale:invalid\","
                    "\"kind\":\"capability_status\",\"supported\":false,"
                    "\"meaning\":\"The native offer did not expose a reviewed technology id.\"}";
            } else {
                out << "{\"id\":\"technology_sale:reject\","
                    "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"reject\","
                    "\"meaning\":\"Decline to sell research data.\"},"
                    "{\"id\":\"technology_sale:accept\","
                    "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"accept\","
                    "\"technology_id\":" << primary << ",\"energy_credits\":" << price
                    << ",\"meaning\":\"Sell the named technology for the quoted native payment.\"}";
                if (!strcmp(label, "ENERGYTECH2") && alternate >= 0
                && alternate < MaxTechnologyNum) {
                    out << ",{\"id\":\"technology_sale:alternate\","
                        "\"command\":\"respond_to_diplomatic_offer\",\"response\":\"counter\","
                        "\"technology_id\":" << alternate << ",\"energy_credits\":" << price
                        << ",\"meaning\":\"Offer the alternate named technology for the same quoted payment.\"}";
                }
                out << ",{\"id\":\"technology_sale:terms\",\"kind\":\"information\","
                    "\"offer_type\":\"technology_sale\",\"counterpart_faction_id\":"
                    << counterpart << ",\"counterpart_faction_name\":"
                    << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                        ? MFactions[counterpart].formal_name_faction : "Unknown")
                    << ",\"technology_id\":" << primary << ",\"technology_name\":"
                    << json_string(Tech[primary].name) << ",\"category\":"
                    << tech_category(primary) << ",\"energy_credits\":" << price;
                if (!strcmp(label, "ENERGYTECH2") && alternate >= 0
                && alternate < MaxTechnologyNum) {
                    out << ",\"alternate_technology_id\":" << alternate
                        << ",\"alternate_technology_name\":"
                        << json_string(Tech[alternate].name);
                }
                out << '}';
            }
        } else if (loan_offer_label(label)) {
            bool player_borrows = !strncmp(label, "ENERGYLOAN", 10);
            int counterpart = *diplo_second_faction;
            int principal = ParseNumTable[0];
            int payment = ParseNumTable[1];
            int years = ParseNumTable[2];
            out << "{\"id\":\"loan:reject\",\"command\":\"respond_to_diplomatic_offer\","
                << "\"response\":\"reject\",\"meaning\":\"Decline this loan without changing either treasury.\"},"
                << "{\"id\":\"loan:accept_full\",\"command\":\"respond_to_diplomatic_offer\","
                << "\"response\":\"accept\",\"principal\":" << principal
                << ",\"affordable\":" << (player_borrows
                    || Factions[faction_id].energy_credits >= principal ? "true" : "false")
                << ",\"meaning\":" << json_string(player_borrows
                    ? "Accept the exact principal and repayment schedule offered by the lender."
                    : "Lend the requested principal under the exact native repayment schedule.") << '}';
            if (!player_borrows) {
                out << ",{\"id\":\"loan:offer_half\",\"command\":\"respond_to_diplomatic_offer\","
                    << "\"response\":\"counter\",\"principal\":" << principal / 2
                    << ",\"affordable\":"
                    << (Factions[faction_id].energy_credits >= principal / 2 ? "true" : "false")
                    << ",\"native_adjusted_repayment\":true,"
                    << "\"meaning\":\"Counter with half the requested principal; the native negotiation adjusts the repayment schedule.\"}";
            }
            out << ",{\"id\":\"loan:terms\",\"kind\":\"information\","
                << "\"offer_type\":\"loan_offer\",\"direction\":"
                << json_string(player_borrows ? "player_borrows" : "player_lends")
                << ",\"counterpart_faction_id\":" << counterpart
                << ",\"counterpart_faction_name\":"
                << json_string(counterpart >= 1 && counterpart < MaxPlayerNum
                    ? MFactions[counterpart].formal_name_faction : "Unknown")
                << ",\"principal\":" << principal << ",\"payment_per_turn\":"
                << payment << ",\"term_turns\":" << years
                << ",\"scheduled_total\":" << payment * years << '}';
        } else if (!strcmp(label, "ASKSEEDESIGN")) {
            out << "{\"id\":\"design_offer:decline\",\"command\":\"respond_to_design_offer\"," 
                "\"response\":\"decline\",\"meaning\":\"Continue without opening the Unit Workshop.\"},"
                "{\"id\":\"design_offer:open\",\"command\":\"respond_to_design_offer\","
                "\"response\":\"open\","
                "\"meaning\":\"Close the coordinate-oriented window offer and continue in the semantic Unit Workshop via smac_choices(kind=unit_design).\"}";
        } else if (!strcmp(label, "ARTIFACT") && artifact_interaction.valid) {
            int unit_id = artifact_interaction.unit_id;
            int base_id = artifact_interaction.base_id;
            BasePop* active = active_default_popup();
            bool valid = active && unit_id >= 0 && unit_id < *VehCount
                && base_id >= 0 && base_id < *BaseCount
                && Vehs[unit_id].faction_id == faction_id
                && Vehs[unit_id].plan() == PLAN_ARTIFACT
                && Bases[base_id].faction_id == faction_id
                && Bases[base_id].queue_items[0] == artifact_interaction.production_id;
            if (!valid) {
                out << "{\"id\":\"artifact:context_changed\","
                    "\"kind\":\"capability_status\",\"supported\":false,"
                    "\"meaning\":\"The native Artifact context changed; do not guess a choice. Report a capability gap.\"}";
            } else {
                out << "{\"id\":\"artifact:no_action\",\"command\":\"respond_to_artifact\","
                    "\"response\":\"no_action\","
                    "\"meaning\":\"Keep the Alien Artifact intact at the base without using its special power.\"}";
                if (popup_has_choice_id(active, 1)) {
                    out << ",{\"id\":\"artifact:link_technology\","
                        "\"command\":\"respond_to_artifact\","
                        "\"response\":\"link_technology\",\"confirm_consume_artifact\":1,"
                        "\"consumes_artifact\":true,\"effect\":\"discover_random_available_technology\","
                        "\"meaning\":\"Permanently link this Artifact through the native base facility and discover one available technology.\"}";
                }
                if (popup_has_choice_id(active, 2)) {
                    int item_id = artifact_interaction.production_id;
                    const char* target_kind = item_id <= -SP_ID_First
                        ? "secret_project" : "unprototyped_unit";
                    int contribution = 5 * Units[unit_id >= 0 ? Vehs[unit_id].unit_id
                        : BSC_ALIEN_ARTIFACT].cost;
                    out << ",{\"id\":\"artifact:accelerate_production\","
                        "\"command\":\"respond_to_artifact\","
                        "\"response\":\"accelerate_production\",\"confirm_consume_artifact\":1,"
                        "\"consumes_artifact\":true,\"target_kind\":"
                        << json_string(target_kind) << ",\"production_id\":" << item_id
                        << ",\"production_name\":"
                        << json_string(production_name(item_id).c_str())
                        << ",\"minerals_before\":" << Bases[base_id].minerals_accumulated
                        << ",\"mineral_cost\":" << artifact_interaction.production_cost
                        << ",\"mineral_contribution\":" << contribution
                        << ",\"meaning\":\"Permanently consume this Artifact to add its exact native mineral contribution to the named Secret Project or unprototyped unit.\"}";
                }
                out << ",{\"id\":\"artifact:context\",\"kind\":\"information\","
                    "\"unit_id\":" << unit_id << ",\"base_id\":" << base_id
                    << ",\"base_name\":" << json_string(Bases[base_id].name)
                    << ",\"production_id\":" << artifact_interaction.production_id
                    << ",\"production_name\":"
                    << json_string(production_name(artifact_interaction.production_id).c_str())
                    << '}';
            }
        } else if (!strcmp(label, "MONOLITH")) {
            out << "{\"id\":\"monolith:leave\",\"command\":\"respond_to_monolith\","
                "\"response\":\"leave\",\"meaning\":\"Leave the monolith uninvestigated.\"},"
                "{\"id\":\"monolith:investigate\",\"command\":\"respond_to_monolith\","
                "\"response\":\"investigate\",\"meaning\":\"Investigate this monolith now.\"},"
                "{\"id\":\"monolith:always\",\"command\":\"respond_to_monolith\","
                "\"response\":\"always\",\"meaning\":\"Investigate now and automatically investigate future monoliths.\"}";
        } else if (!strcmp(label, "ACCEDE") || !strcmp(label, "ACCEDECOOP")) {
            int other = *diplo_second_faction;
            out << "{\"id\":\"supreme_leader:accede\"," 
                "\"command\":\"respond_to_supreme_leader\",\"response\":\"accede\"," 
                "\"meaning\":\"Accept the Council result and enter the native diplomatic or cooperative victory path.\"},"
                "{\"id\":\"supreme_leader:defy\"," 
                "\"command\":\"respond_to_supreme_leader\",\"response\":\"defy\"," 
                "\"confirm_defiance\":1,\"consequential\":true,"
                "\"meaning\":\"Defy the Council result and continue against the united opposing factions.\"},"
                "{\"id\":\"supreme_leader:context\",\"kind\":\"information\"," 
                "\"cooperative_victory_available\":"
                << (!strcmp(label, "ACCEDECOOP") ? "true" : "false")
                << ",\"elected_faction_id\":" << other
                << ",\"elected_faction_name\":"
                << json_string(other >= 1 && other < MaxPlayerNum
                    ? MFactions[other].formal_name_faction : "Unknown") << '}';
        } else if (!strcmp(label, "GAMEOVERMAN")) {
            out << "{\"id\":\"game_over:finish\",\"command\":\"respond_to_game_over\"," 
                "\"response\":\"finish\",\"meaning\":\"Finish this completed game and return through the native exit path.\"},"
                "{\"id\":\"game_over:continue\",\"command\":\"respond_to_game_over\"," 
                "\"response\":\"continue\",\"meaning\":\"Keep playing the completed game for additional turns.\"}";
        } else if (probe_excuse_label(label)) {
            bool pact = label == std::string("PACTEXCUSE")
                || label == std::string("PACTFRAMEEXCUSE");
            out << "{\"id\":\"probe_incident:forgive\",\"command\":\"respond_to_probe_incident\","
                << "\"response\":" << json_string(pact ? "tolerate" : "forgive")
                << ",\"meaning\":" << json_string(pact
                    ? "Issue a rebuke but preserve the pact."
                    : "Overlook this probe offense for now.") << "},"
                << "{\"id\":\"probe_incident:retaliate\",\"command\":\"respond_to_probe_incident\","
                << "\"response\":" << json_string(pact ? "renounce_pact" : "declare_vendetta")
                << ",\"meaning\":" << json_string(pact
                    ? "Renounce the pact because of this probe offense."
                    : "Declare Vendetta because of this probe offense.") << '}';
            if (probe_excuse_context.valid) {
                int offender = probe_excuse_context.offender_faction_id;
                out << ",{\"id\":\"probe_incident:context\",\"kind\":\"information\","
                    << "\"offender_faction_id\":" << offender
                    << ",\"offender_faction_name\":"
                    << json_string(offender >= 1 && offender < MaxPlayerNum
                        ? MFactions[offender].formal_name_faction : "Unknown")
                    << ",\"probe_action\":"
                    << json_string(probe_action_name(probe_excuse_context.action_id))
                    << ",\"framed_incident\":"
                    << (probe_excuse_context.framed ? "true" : "false")
                    << ",\"pact_at_risk\":" << (probe_excuse_context.pact ? "true" : "false") << '}';
            }
        } else if (label[0] && popup_information_only()) {
            out << "{\"id\":\"popup:acknowledge\",\"command\":\"acknowledge_popup\","
                "\"meaning\":\"Acknowledge an engine-confirmed information-only popup with no alternatives.\"}";
        } else if (!label[0] && Factions[faction_id].tech_research_id < 0
        && (*GameRules & RULES_BLIND_RESEARCH)) {
            const char* names[] = {"Explore", "Discover", "Build", "Conquer"};
            for (int priority = 0; priority < 4; ++priority) {
                if (priority) out << ',';
                out << "{\"id\":\"research_priority:" << priority
                    << "\",\"command\":\"choose_research_priority\",\"priority\":" << priority
                    << ",\"name\":" << json_string(names[priority]) << '}';
            }
        }
        out << "]}";
        return out.str();
    }
    if (!human_turn_actionable(faction_id)) {
        return error_response("wrong_choice_phase",
            "Only interaction choices are available outside the human faction's actionable turn. Follow snapshot.protocol.");
    }
    if (kind == "research") return research_choices_response(faction_id);
    if (kind == "energy_allocation") return energy_allocation_choices_response(faction_id);
    if (kind == "social_engineering") return social_engineering_choices_response(faction_id);
    if (kind == "diplomacy") return diplomacy_choices_response(faction_id);
    if (kind == "council") return council_choices_response(faction_id);
    if (kind == "unit_design") return unit_design_choices_response(faction_id);
    if (kind == "production") return production_choices_response(faction_id, field_int(request, "base_id", -1));
    if (kind == "base_management") return base_management_choices_response(faction_id, field_int(request, "base_id", -1));
    if (kind == "base_citizens") return base_citizen_choices_response(faction_id, field_int(request, "base_id", -1));
    if (kind == "unit_actions") {
        if (*MultiplayerActive) return multiplayer_unit_choices_response(
            faction_id, field_int(request, "unit_id", -1));
        return unit_choices_response(
            faction_id, field_int(request, "unit_id", -1),
            field_int(request, "base_id", -1),
            field_int(request, "target_tile_id", -1),
            field_int(request, "target_unit_id", -1));
    }
    if (kind == "game_management") return game_management_choices_response();
    return error_response("bad_choice_kind", "Supported choice kinds: interaction, research, energy_allocation, social_engineering, diplomacy, council, unit_design, production, base_management, base_citizens, unit_actions, game_management.");
}

std::string semantic_command_response(const std::string& request) {
    if (!game_active()) return error_response("not_in_game", "Start or load a game first.");
    std::string guard_error = validate_semantic_guard(request);
    if (!guard_error.empty()) return guard_error;
    if (popup_transition_is_pending()) {
        return error_response("popup_transition_pending",
            "The submitted popup action is still crossing the native event loop. Wait and observe; do not submit another command yet.");
    }
    int faction_id = *CurrentPlayerFaction;
    std::string command = field_string(request, "command");
    // PLANETFALL is a local, information-only opening notice on every human
    // client. It changes no shared simulation state, but still travels through
    // the guarded semantic command path and native popup callback.
    std::string active_label = semantic_popup_label();
    int multiplayer_move_tile_id = -1;
    int multiplayer_move_x = -1;
    int multiplayer_move_y = -1;
    bool validated_multiplayer_move = command == "move_unit"
        && semantic_request_tile(request, &multiplayer_move_tile_id,
            &multiplayer_move_x, &multiplayer_move_y)
        && (multiplayer_safe_move_target(faction_id,
                field_int(request, "unit_id", -1),
                multiplayer_move_x, multiplayer_move_y)
            || multiplayer_combat_move_target(faction_id,
                field_int(request, "unit_id", -1),
                multiplayer_move_x, multiplayer_move_y));
    int multiplayer_finish_unit_id = field_int(request, "unit_id", -1);
    bool validated_multiplayer_finish =
        (command == "skip_unit" || command == "hold_unit" || command == "sentry_unit")
        && multiplayer_finish_unit_id >= 0
        && multiplayer_finish_unit_id < *VehCount
        && Vehs[multiplayer_finish_unit_id].faction_id == faction_id
        && semantic_unit_requires_decision(multiplayer_finish_unit_id);
    int multiplayer_ready_units = 0;
    for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
        if (Vehs[veh_id].faction_id == faction_id
        && semantic_unit_requires_decision(veh_id)) ++multiplayer_ready_units;
    }
    bool validated_multiplayer_end_turn = command == "end_turn"
        && human_turn_actionable(faction_id) && multiplayer_ready_units == 0;
    bool validated_multiplayer_save = command == "save_game"
        && human_turn_actionable(faction_id)
        && lan_local_player_index() >= 1
        && lan_local_player_index() == lan_host_player_index();
    int multiplayer_production_base_id = field_int(request, "base_id", -1);
    int multiplayer_production_item_id = field_int(request, "item_id", 99999);
    bool validated_multiplayer_production = command == "set_production"
        && multiplayer_production_base_id >= 0
        && multiplayer_production_base_id < *BaseCount
        && Bases[multiplayer_production_base_id].faction_id == faction_id;
    if (validated_multiplayer_production) {
        set_base(multiplayer_production_base_id);
        base_compute(1);
        validated_multiplayer_production = production_item_buildable(
            faction_id, multiplayer_production_base_id,
            multiplayer_production_item_id);
    }
    int multiplayer_economy = field_int(request, "economy", -1);
    int multiplayer_psych = field_int(request, "psych", -1);
    int multiplayer_labs = field_int(request, "labs", -1);
    bool validated_multiplayer_allocation = command == "set_energy_allocation"
        && multiplayer_economy >= 0 && multiplayer_economy <= 10
        && multiplayer_psych >= 0 && multiplayer_psych <= 10
        && multiplayer_labs >= 0 && multiplayer_labs <= 10
        && multiplayer_economy + multiplayer_psych + multiplayer_labs == 10;
    int multiplayer_research_priority = field_int(request, "priority", -1);
    bool validated_multiplayer_research_priority =
        command == "set_research_priority"
        && (*GameRules & RULES_BLIND_RESEARCH)
        && multiplayer_research_priority >= TCAT_GROWTH
        && multiplayer_research_priority <= TCAT_POWER;
    int multiplayer_diplomacy_other = field_int(request, "faction_id", -1);
    bool validated_multiplayer_open_diplomacy = command == "open_diplomacy"
        && multiplayer_diplomacy_other >= 1
        && multiplayer_diplomacy_other < MaxPlayerNum
        && multiplayer_diplomacy_other != faction_id
        && is_alive(multiplayer_diplomacy_other)
        && has_treaty(faction_id, multiplayer_diplomacy_other, DIPLO_COMMLINK)
        && human_turn_actionable(faction_id)
        && !deferred_native_action_pending();
    std::string multiplayer_human_relationship = field_string(
        request, "relationship");
    bool validated_multiplayer_human_relationship =
        command == "propose_human_relationship"
        && ((multiplayer_human_relationship == "treaty"
                && human_treaty_proposal_legal(faction_id))
            || (multiplayer_human_relationship == "pact"
                && human_pact_proposal_legal(faction_id))
            || (multiplayer_human_relationship == "truce"
                && human_truce_proposal_legal(faction_id)));
    int multiplayer_human_technology = field_int(request, "tech_id", -1);
    bool validated_multiplayer_human_technology =
        command == "propose_human_technology"
        && human_technology_proposal_legal(
            faction_id, multiplayer_human_technology);
    int multiplayer_human_energy = field_int(request, "amount", -1);
    bool validated_multiplayer_human_energy =
        command == "propose_human_energy"
        && human_energy_proposal_legal(faction_id, multiplayer_human_energy);
    int multiplayer_human_joint_attack = field_int(
        request, "target_faction_id", -1);
    bool validated_multiplayer_human_joint_attack =
        command == "propose_human_joint_attack"
        && human_joint_attack_proposal_legal(
            faction_id, multiplayer_human_joint_attack);
    int multiplayer_human_diplomacy_side =
        human_diplomacy_local_side(faction_id);
    std::string multiplayer_human_diplomacy_response =
        field_string(request, "response");
    bool validated_multiplayer_human_diplomacy_response =
        command == "respond_human_diplomacy"
        && multiplayer_human_diplomacy_side >= 0
        && human_diplomacy_acceptance(
            multiplayer_human_diplomacy_side) == 0
        && (human_diplomacy_clause_count(
                multiplayer_human_diplomacy_side) > 0
            || human_diplomacy_clause_count(
                1 - multiplayer_human_diplomacy_side) > 0)
        && (multiplayer_human_diplomacy_response == "accept"
            || multiplayer_human_diplomacy_response == "decline");
    int multiplayer_contact_other = *diplo_second_faction;
    bool validated_multiplayer_contact_response =
        command == "respond_to_contact"
        && (field_string(request, "response") == "decline"
            || (field_string(request, "response") == "accept"
                && multiplayer_contact_other >= 1
                && multiplayer_contact_other < MaxPlayerNum
                && !is_human(multiplayer_contact_other)))
        && (active_label == "COMM" || active_label == "COMMDIPLO")
        && active_default_popup();
    bool validated_multiplayer_ai_greeting = command == "continue_diplomacy"
        && (!active_label.compare(0, 8, "INTRONEW")
            || !active_label.compare(0, 5, "INTRO"))
        && multiplayer_contact_other >= 1
        && multiplayer_contact_other < MaxPlayerNum
        && !is_human(multiplayer_contact_other)
        && active_default_popup();
    bool validated_multiplayer_finish_ai_diplomacy =
        command == "choose_diplomacy_option"
        && field_string(request, "option") == "finish"
        && active_label == "DIPLO"
        && multiplayer_contact_other >= 1
        && multiplayer_contact_other < MaxPlayerNum
        && !is_human(multiplayer_contact_other)
        && active_default_popup();
    bool validated_multiplayer_reject_ai_technology_trade =
        command == "respond_to_diplomatic_offer"
        && field_string(request, "response") == "reject"
        && (technology_trade_label(active_label)
            || technology_demand_label(active_label)
            || relationship_offer_label(active_label))
        && multiplayer_contact_other >= 1
        && multiplayer_contact_other < MaxPlayerNum
        && !is_human(multiplayer_contact_other)
        && active_default_popup();
    bool validated_multiplayer_introduced_commlink_response =
        command == "respond_to_diplomatic_offer"
        && (field_string(request, "response") == "accept"
            || field_string(request, "response") == "reject")
        && introduced_commlink_offer_mode(active_label) >= 0
        && multiplayer_contact_other >= 1
        && multiplayer_contact_other < MaxPlayerNum
        && !is_human(multiplayer_contact_other)
        && active_default_popup();
    CSocialCategory multiplayer_social;
    multiplayer_social.models[SOCIAL_C_POLITICS] = field_int(request, "politics", -1);
    multiplayer_social.models[SOCIAL_C_ECONOMICS] = field_int(request, "economics", -1);
    multiplayer_social.models[SOCIAL_C_VALUES] = field_int(request, "values", -1);
    multiplayer_social.models[SOCIAL_C_FUTURE] = field_int(request, "future", -1);
    bool validated_multiplayer_social = command == "set_social_engineering"
        && !(*GameMoreRules & MRULES_NO_SOCIAL_ENGINEERING);
    if (validated_multiplayer_social) {
        for (int category = 0; category < MaxSocialCatNum; ++category) {
            int model = multiplayer_social.models[category];
            if (model < 0 || model >= MaxSocialModelNum
            || !society_avail(category, model, faction_id)) {
                validated_multiplayer_social = false;
                break;
            }
        }
    }
    if (validated_multiplayer_social) {
        int target_cost = social_upheaval(faction_id, &multiplayer_social);
        int cost_delta = target_cost - Factions[faction_id].SE_upheaval_cost_paid;
        validated_multiplayer_social = cost_delta <= Factions[faction_id].energy_credits;
    }
    int multiplayer_queue_base_id = field_int(request, "base_id", -1);
    int multiplayer_queue_item_id = field_int(request, "item_id", 99999);
    int multiplayer_queue_position = field_int(request, "queue_position", -1);
    bool multiplayer_queue_base_owned = multiplayer_queue_base_id >= 0
        && multiplayer_queue_base_id < *BaseCount
        && Bases[multiplayer_queue_base_id].faction_id == faction_id;
    bool validated_multiplayer_queue_append = false;
    bool validated_multiplayer_queue_remove = false;
    bool validated_multiplayer_queue_clear = false;
    int multiplayer_governor_active = field_int(request, "active", -1);
    int multiplayer_governor_citizens = field_int(request, "manage_citizens", -1);
    int multiplayer_governor_production = field_int(request, "manage_production", -1);
    const int multiplayer_governor_optional[] = {
        field_int(request, "new_units_automated", -1),
        field_int(request, "priority_explore", -1),
        field_int(request, "priority_discover", -1),
        field_int(request, "priority_build", -1),
        field_int(request, "priority_conquer", -1),
    };
    bool validated_multiplayer_governor = command == "set_base_governor"
        && multiplayer_queue_base_owned
        && (multiplayer_governor_active == 0 || multiplayer_governor_active == 1)
        && (multiplayer_governor_citizens == 0 || multiplayer_governor_citizens == 1)
        && (multiplayer_governor_production == 0 || multiplayer_governor_production == 1);
    if (validated_multiplayer_governor) {
        for (size_t index = 0; index < sizeof(multiplayer_governor_optional)
        / sizeof(multiplayer_governor_optional[0]); ++index) {
            if (multiplayer_governor_optional[index] < -1
            || multiplayer_governor_optional[index] > 1) {
                validated_multiplayer_governor = false;
                break;
            }
        }
    }
    std::string multiplayer_permission_key = field_string(
        request, "governor_permission");
    const GovernorPermissionSpec* multiplayer_permission = governor_permission(
        multiplayer_permission_key);
    int multiplayer_permission_active = field_int(request, "active", -1);
    bool validated_multiplayer_governor_permission =
        command == "set_governor_permission"
        && multiplayer_queue_base_owned && multiplayer_permission
        && (multiplayer_permission_active == 0 || multiplayer_permission_active == 1)
        && static_cast<bool>(Bases[multiplayer_queue_base_id].governor_flags
            & multiplayer_permission->mask) != (multiplayer_permission_active != 0);
    bool validated_multiplayer_citizen = false;
    if (multiplayer_queue_base_owned
    && (command == "convert_worker_to_specialist"
        || command == "assign_specialist_to_tile"
        || command == "set_specialist_type")) {
        set_base(multiplayer_queue_base_id);
        base_compute(1);
        BASE& citizen_base = Bases[multiplayer_queue_base_id];
        bool governor_manages =
            (citizen_base.governor_flags
                & (GOV_ACTIVE | GOV_MANAGE_CITIZENS))
            == (GOV_ACTIVE | GOV_MANAGE_CITIZENS);
        int specialist_index = field_int(request, "specialist_index", -1);
        int citizen_id = field_int(request, "citizen_id", -1);
        int tile_index = field_int(request, "tile_index", -1);
        if (!governor_manages && command == "convert_worker_to_specialist") {
            validated_multiplayer_citizen = tile_index >= 1 && tile_index < 21
                && (citizen_base.worked_tiles & (1 << tile_index))
                && citizen_base.specialist_total < MaxBaseSpecNum
                && specialist_available_at_base(citizen_base, citizen_id);
        } else if (!governor_manages
        && command == "assign_specialist_to_tile") {
            int x = 0;
            int y = 0;
            MAP* sq = tile_index >= 1 && tile_index < 21
                ? next_tile(citizen_base.x, citizen_base.y, tile_index, &x, &y)
                : NULL;
            int blocked_flags = tile_index >= 1 && tile_index < 21
                ? BaseTileFlags[tile_index]
                    & (BR_NOT_AVAILABLE | BR_NOT_VISIBLE | BR_BASE_IN_TILE
                        | BR_VEH_IN_TILE | BR_FOREIGN_TILE | BR_WORKER_ACTIVE)
                : BR_NOT_AVAILABLE;
            validated_multiplayer_citizen = specialist_index >= 0
                && specialist_index < citizen_base.specialist_total
                && sq && sq->is_visible(faction_id) && !blocked_flags
                && !(citizen_base.worked_tiles & (1 << tile_index));
        } else if (!governor_manages && command == "set_specialist_type") {
            validated_multiplayer_citizen = specialist_index >= 0
                && specialist_index < citizen_base.specialist_total
                && citizen_id != citizen_base.specialist_type(specialist_index)
                && specialist_available_at_base(citizen_base, citizen_id);
        }
    }
    if (multiplayer_queue_base_owned) {
        BASE& queue_base = Bases[multiplayer_queue_base_id];
        if (command == "queue_production" && queue_base.queue_size < 9) {
            set_base(multiplayer_queue_base_id);
            base_compute(1);
            validated_multiplayer_queue_append = production_item_buildable(
                faction_id, multiplayer_queue_base_id,
                multiplayer_queue_item_id, queue_base.queue_size + 1);
        }
        validated_multiplayer_queue_remove =
            command == "remove_queued_production"
            && multiplayer_queue_position >= 1
            && multiplayer_queue_position <= queue_base.queue_size
            && multiplayer_queue_position < 10;
        validated_multiplayer_queue_clear =
            command == "clear_production_queue" && queue_base.queue_size > 0;
    }
    // These labels have been audited as reports of outcomes already committed
    // by the engine.  Their sole native button dismisses local presentation;
    // it does not choose or mutate shared simulation state.
    bool validated_multiplayer_command = (command == "acknowledge_popup"
            && (reviewed_information_popup(active_label)
                || narrative_intro_popup(active_label)))
        || (command == "finish_human_diplomacy"
            && human_diplomacy_window_active())
        || (command == "advance_technology_presentation"
            && technology_presentation_active())
        || (command == "choose_research_priority"
            && active_label == "TECHRANDOM")
        || validated_multiplayer_move || validated_multiplayer_finish
        || validated_multiplayer_save
        || validated_multiplayer_end_turn || validated_multiplayer_production
        || validated_multiplayer_allocation
        || validated_multiplayer_research_priority
        || validated_multiplayer_open_diplomacy
        || validated_multiplayer_human_relationship
        || validated_multiplayer_human_technology
        || validated_multiplayer_human_energy
        || validated_multiplayer_human_joint_attack
        || validated_multiplayer_human_diplomacy_response
        || validated_multiplayer_contact_response
        || validated_multiplayer_ai_greeting
        || validated_multiplayer_reject_ai_technology_trade
        || validated_multiplayer_introduced_commlink_response
        || validated_multiplayer_finish_ai_diplomacy
        || validated_multiplayer_queue_append
        || validated_multiplayer_queue_remove
        || validated_multiplayer_queue_clear
        || validated_multiplayer_social
        || validated_multiplayer_governor
        || validated_multiplayer_governor_permission
        || validated_multiplayer_citizen;
    if (*MultiplayerActive && !validated_multiplayer_command) {
        return error_response("multiplayer_command_not_validated",
            "This multiplayer semantic command is not in the validated allowlist. It is blocked before mutation until its command family passes a two-client synchronization regression.");
    }
    if (end_turn_completion_pending()) {
        return error_response("end_turn_transition_pending",
            "The confirmed native end-turn request is still completing. Wait and observe; do not submit another command.");
    }
    if (interaction_kind(faction_id) != "turn"
    && !semantic_interaction_command(command)) {
        return semantic_not_actionable();
    }
    if (command == "propose_human_relationship") {
        std::string relationship = field_string(request, "relationship");
        bool treaty = relationship == "treaty"
            && human_treaty_proposal_legal(faction_id);
        bool pact = relationship == "pact"
            && human_pact_proposal_legal(faction_id);
        bool truce = relationship == "truce"
            && human_truce_proposal_legal(faction_id);
        if (!treaty && !pact && !truce) {
            return error_response("human_diplomacy_choice_changed",
                "The exact reviewed human relationship proposal is no longer legal in this native conversation.");
        }
        int native_action = treaty ? 10 : pact ? 11 : 9;
        int native_clause_type = treaty ? 3 : pact ? 2 : 4;
        int local_side = human_diplomacy_local_side(faction_id);
        int previous_count = human_diplomacy_clause_count(local_side);
        typedef void(__thiscall *DiploWindowAction)(void*, int);
        DiploWindowAction action = reinterpret_cast<DiploWindowAction>(0x4415C0);
        action(DiploWin, native_action);
        if (*MultiplayerActive) NetDaemon_await_exec(NetState, 1);
        int current_count = human_diplomacy_clause_count(local_side);
        if (current_count <= previous_count
        || !human_diplomacy_has_clause(local_side, native_clause_type)) {
            return error_response("native_human_diplomacy_rejected",
                "The native diplomacy window did not add the reviewed relationship clause.");
        }
        if (!human_diplomacy_window_active()) {
            return error_response("native_human_diplomacy_closed_before_commit",
                "The native diplomacy window closed before the reviewed relationship offer could be committed. Observe and open a fresh channel.");
        }
        // Stock paired diplomacy has no durable draft phase. Commit the exact
        // clause in this same game-thread request so the modal window cannot
        // disappear between an agent's compose and accept calls.
        action(DiploWin, 2);
        if (*MultiplayerActive) NetDaemon_await_exec(NetState, 1);
        bool active = human_diplomacy_window_active();
        if (active && human_diplomacy_acceptance(local_side) != 1) {
            return error_response("native_human_diplomacy_rejected",
                "The native diplomacy window did not commit the reviewed relationship offer.");
        }
        return std::string("{\"ok\":true,\"command\":\"propose_human_relationship\","
            "\"relationship\":") + json_string(relationship.c_str())
            + ",\"offering_faction_id\":"
            + std::to_string(faction_id)
            + ",\"native_clause_type\":" + std::to_string(native_clause_type)
            + ",\"clause_count\":"
            + std::to_string(current_count)
            + ",\"proposer_committed\":true}";
    }
    if (command == "propose_human_technology") {
        int tech_id = field_int(request, "tech_id", -1);
        if (!human_technology_proposal_legal(faction_id, tech_id)) {
            return error_response("human_diplomacy_technology_changed",
                "The exact reviewed player-owned technology is no longer transferable to this human counterpart.");
        }
        int local_side = human_diplomacy_local_side(faction_id);
        int previous_count = human_diplomacy_clause_count(local_side);
        typedef void(__thiscall *DiploWindowAddClause)(void*, int, int, int);
        DiploWindowAddClause add_clause =
            reinterpret_cast<DiploWindowAddClause>(0x441490);
        // Native action 5 opens a local technology list and then calls this
        // exact packet-aware clause routine with local side 0, type 0, and
        // the selected technology id. The semantic choice supplies that same
        // fair player-owned id without opening a visual selector.
        add_clause(DiploWin, 0, 0, tech_id);
        if (*MultiplayerActive) NetDaemon_await_exec(NetState, 1);
        int current_count = human_diplomacy_clause_count(local_side);
        if (current_count <= previous_count
        || !human_diplomacy_has_clause_value(local_side, 0, tech_id)) {
            return error_response("native_human_diplomacy_rejected",
                "The native diplomacy window did not add the reviewed technology clause.");
        }
        if (!human_diplomacy_window_active()) {
            return error_response("native_human_diplomacy_closed_before_commit",
                "The native diplomacy window closed before the reviewed technology offer could be committed. Observe and open a fresh channel.");
        }
        typedef void(__thiscall *DiploWindowAction)(void*, int);
        DiploWindowAction commit = reinterpret_cast<DiploWindowAction>(0x4415C0);
        commit(DiploWin, 2);
        if (*MultiplayerActive) NetDaemon_await_exec(NetState, 1);
        bool active = human_diplomacy_window_active();
        if (active && human_diplomacy_acceptance(local_side) != 1) {
            return error_response("native_human_diplomacy_rejected",
                "The native diplomacy window did not commit the reviewed technology offer.");
        }
        return std::string("{\"ok\":true,\"command\":\"propose_human_technology\","
            "\"offering_faction_id\":") + std::to_string(faction_id)
            + ",\"technology_id\":" + std::to_string(tech_id)
            + ",\"technology_name\":" + json_string(Tech[tech_id].name)
            + ",\"native_clause_type\":0,\"clause_count\":"
            + std::to_string(current_count)
            + ",\"proposer_committed\":true}";
    }
    if (command == "propose_human_energy") {
        int amount = field_int(request, "amount", -1);
        if (!human_energy_proposal_legal(faction_id, amount)) {
            return error_response("human_diplomacy_energy_changed",
                "The reviewed energy amount is no longer transferable to this human counterpart.");
        }
        int local_side = human_diplomacy_local_side(faction_id);
        int previous_count = human_diplomacy_clause_count(local_side);
        typedef void(__thiscall *DiploWindowAddClause)(void*, int, int, int);
        DiploWindowAddClause add_clause =
            reinterpret_cast<DiploWindowAddClause>(0x441490);
        // Native action 6 opens the local numeric selector and then calls
        // this packet-aware routine with local side 0, type 1, and the
        // selected amount. The semantic range is bounded by the caller's
        // current treasury, so no hidden state or visual selector is needed.
        add_clause(DiploWin, 0, 1, amount);
        if (*MultiplayerActive) NetDaemon_await_exec(NetState, 1);
        int current_count = human_diplomacy_clause_count(local_side);
        if (current_count <= previous_count
        || !human_diplomacy_has_clause_value(local_side, 1, amount)) {
            return error_response("native_human_diplomacy_rejected",
                "The native diplomacy window did not add the reviewed energy clause.");
        }
        if (!human_diplomacy_window_active()) {
            return error_response("native_human_diplomacy_closed_before_commit",
                "The native diplomacy window closed before the reviewed energy offer could be committed. Observe and open a fresh channel.");
        }
        typedef void(__thiscall *DiploWindowAction)(void*, int);
        DiploWindowAction commit = reinterpret_cast<DiploWindowAction>(0x4415C0);
        commit(DiploWin, 2);
        if (*MultiplayerActive) NetDaemon_await_exec(NetState, 1);
        bool active = human_diplomacy_window_active();
        if (active && human_diplomacy_acceptance(local_side) != 1) {
            return error_response("native_human_diplomacy_rejected",
                "The native diplomacy window did not commit the reviewed energy offer.");
        }
        return std::string("{\"ok\":true,\"command\":\"propose_human_energy\","
            "\"offering_faction_id\":") + std::to_string(faction_id)
            + ",\"energy_credits\":" + std::to_string(amount)
            + ",\"native_clause_type\":1,\"clause_count\":"
            + std::to_string(current_count)
            + ",\"proposer_committed\":true}";
    }
    if (command == "propose_human_joint_attack") {
        int target = field_int(request, "target_faction_id", -1);
        if (!human_joint_attack_proposal_legal(faction_id, target)) {
            return error_response("human_diplomacy_joint_attack_changed",
                "The reviewed contacted target is no longer a legal joint-attack proposal in this human conversation.");
        }
        int local_side = human_diplomacy_local_side(faction_id);
        int previous_count = human_diplomacy_clause_count(local_side);
        typedef void(__thiscall *DiploWindowAddClause)(void*, int, int, int);
        DiploWindowAddClause add_clause =
            reinterpret_cast<DiploWindowAddClause>(0x441490);
        // Native action 7 opens the faction selector and adds clause type 5.
        // The semantic choice supplies only a live, contacted third faction
        // already visible to the local player, without opening that selector.
        add_clause(DiploWin, 0, 5, target);
        if (*MultiplayerActive) NetDaemon_await_exec(NetState, 1);
        int current_count = human_diplomacy_clause_count(local_side);
        if (current_count <= previous_count
        || !human_diplomacy_has_clause_value(local_side, 5, target)) {
            return error_response("native_human_diplomacy_rejected",
                "The native diplomacy window did not add the reviewed joint-attack clause.");
        }
        if (!human_diplomacy_window_active()) {
            return error_response("native_human_diplomacy_closed_before_commit",
                "The native diplomacy window closed before the reviewed joint-attack offer could be committed. Observe and open a fresh channel.");
        }
        typedef void(__thiscall *DiploWindowAction)(void*, int);
        DiploWindowAction commit = reinterpret_cast<DiploWindowAction>(0x4415C0);
        commit(DiploWin, 2);
        if (*MultiplayerActive) NetDaemon_await_exec(NetState, 1);
        bool active = human_diplomacy_window_active();
        if (active && human_diplomacy_acceptance(local_side) != 1) {
            return error_response("native_human_diplomacy_rejected",
                "The native diplomacy window did not commit the reviewed joint-attack offer.");
        }
        return std::string("{\"ok\":true,\"command\":\"propose_human_joint_attack\","
            "\"offering_faction_id\":") + std::to_string(faction_id)
            + ",\"target_faction_id\":" + std::to_string(target)
            + ",\"target_faction_name\":"
            + json_string(MFactions[target].formal_name_faction)
            + ",\"native_clause_type\":5,\"clause_count\":"
            + std::to_string(current_count)
            + ",\"proposer_committed\":true}";
    }
    if (command == "respond_human_diplomacy") {
        int local_side = human_diplomacy_local_side(faction_id);
        int incoming_side = local_side < 0 ? -1 : 1 - local_side;
        std::string response = field_string(request, "response");
        if (incoming_side < 0
        || human_diplomacy_acceptance(local_side) != 0
        || (human_diplomacy_clause_count(local_side) <= 0
            && human_diplomacy_clause_count(incoming_side) <= 0)
        || (response != "accept" && response != "decline")) {
            return error_response("human_diplomacy_choice_changed",
                "The reviewed complete incoming human offer is no longer awaiting this response.");
        }
        int other = human_diplomacy_participant(incoming_side);
        typedef void(__thiscall *DiploWindowAction)(void*, int);
        DiploWindowAction action = reinterpret_cast<DiploWindowAction>(0x4415C0);
        action(DiploWin, response == "accept" ? 2 : 3);
        if (*MultiplayerActive) NetDaemon_await_exec(NetState, 1);
        bool active = human_diplomacy_window_active();
        int local_acceptance = active
            ? human_diplomacy_acceptance(local_side) : -1;
        bool accepted = response == "accept"
            && (has_treaty(faction_id, other, DIPLO_TREATY)
                || local_acceptance == 1 || !active);
        bool declined = response == "decline"
            && (local_acceptance == 2 || !active);
        if (!accepted && !declined) {
            return error_response("native_human_diplomacy_rejected",
                "The native human diplomacy window did not record the reviewed offer response.");
        }
        std::ostringstream result;
        result << "{\"ok\":true,\"command\":\"respond_human_diplomacy\","
            "\"response\":" << json_string(response.c_str())
            << ",\"counterpart_faction_id\":" << other
            << ",\"interaction_active\":" << (active ? "true" : "false")
            << ",\"relationship_status\":"
            << Factions[faction_id].diplo_status[other] << '}';
        return result.str();
    }
    if (command == "finish_human_diplomacy") {
        if (!human_diplomacy_window_active()) {
            return error_response("human_diplomacy_window_changed",
                "The reviewed native human diplomacy window is no longer active.");
        }
        int initiator = *reinterpret_cast<int*>(
            reinterpret_cast<char*>(DiploWin) + 0xAB4);
        int counterpart = *reinterpret_cast<int*>(
            reinterpret_cast<char*>(DiploWin) + 0xAB8);
        typedef void(__thiscall *DiploWindowAction)(void*, int);
        DiploWindowAction action = reinterpret_cast<DiploWindowAction>(0x4415C0);
        action(DiploWin, 4);
        return std::string("{\"ok\":true,\"command\":\"finish_human_diplomacy\","
            "\"initiator_faction_id\":") + std::to_string(initiator)
            + ",\"counterpart_faction_id\":" + std::to_string(counterpart)
            + ",\"native_packet_submitted\":true}";
    }
    if (command == "advance_technology_presentation") {
        int tech_id = technology_presentation_tech_id();
        if (tech_id < 0) {
            return error_response("technology_presentation_changed",
                "The exact passive native technology window is no longer active. Observe again.");
        }
        if (!pending_multiplayer_technology_presentations.empty()) {
            pending_multiplayer_technology_presentations.erase(
                pending_multiplayer_technology_presentations.begin());
            return std::string("{\"ok\":true,\"command\":\"advance_technology_presentation\",\"technology_id\":")
                + std::to_string(tech_id) + ",\"technology_name\":"
                + json_string(Tech[tech_id].name)
                + ",\"presentation\":\"multiplayer_semantic_queue\","
                "\"transition\":\"waiting_for_engine\"}";
        }
        // NetTechWindow's own vtable +0xE8 entry is Win_release_modal. Calling
        // the named native completion releases its synchronous exec loop with
        // no keyboard, mouse, pixels, or coordinates.
        Win_release_modal(reinterpret_cast<Win*>(NetTechWin));
        return std::string("{\"ok\":true,\"command\":\"advance_technology_presentation\",\"technology_id\":")
            + std::to_string(tech_id) + ",\"technology_name\":"
            + json_string(Tech[tech_id].name)
            + ",\"transition\":\"waiting_for_engine\"}";
    }
    if (command == "advance_endgame_presentation") {
        std::string phase = field_string(request, "phase");
        if (phase.empty() || phase != endgame_presentation_phase
        || pending_endgame_presentation_advance || active_default_popup()) {
            return error_response("endgame_presentation_changed",
                "Use the exact phase returned by the current passive endgame-presentation choice.");
        }
        if (!close_active_endgame_presentation()) {
            return error_response("endgame_window_unavailable",
                "The native passive endgame window could not be safely identified; report this capability gap and stop instead of using UI input.");
        }
        return std::string("{\"ok\":true,\"command\":\"advance_endgame_presentation\",\"phase\":")
            + json_string(phase.c_str())
            + ",\"transition\":\"waiting_for_engine\"}";
    }
    if (command == "save_game") {
        if (!human_turn_actionable(faction_id)) return semantic_not_actionable();
        if (*MultiplayerActive) {
            const int local_player_index = lan_local_player_index();
            const int host_player_index = lan_host_player_index();
            if (local_player_index < 1 || host_player_index < 1) {
                return error_response("multiplayer_host_identity_unavailable",
                    "The native DirectPlay host identity is not stable. Observe again before saving.");
            }
            if (local_player_index != host_player_index) {
                return error_response("multiplayer_save_host_only",
                    "Only the native DirectPlay host may save a multiplayer campaign.");
            }
        }
        std::string slot = field_string(request, "slot");
        if (!safe_path_component(slot, 32) || !ensure_agent_save_directory()) {
            return error_response("invalid_save_slot",
                "Save slots must contain 1 through 32 ASCII letters, digits, hyphens, or underscores.");
        }
        std::string path = agent_save_path(slot);
        bool replaced = GetFileAttributesA(path.c_str()) != INVALID_FILE_ATTRIBUTES;
        int status = mod_save_daemon(path.c_str());
        if (status != 0) {
            return std::string("{\"ok\":false,\"error\":{\"code\":\"save_failed\","
                "\"message\":\"The native save routine failed.\"},\"native_status\":")
                + std::to_string(status) + '}';
        }
        return std::string("{\"ok\":true,\"command\":\"save_game\",\"slot\":")
            + json_string(slot.c_str()) + ",\"relative_path\":" + json_string(path.c_str())
            + ",\"replaced\":" + (replaced ? "true" : "false")
            + ",\"multiplayer\":" + (*MultiplayerActive ? "true" : "false")
            + ",\"native_host\":" + (*MultiplayerActive ? "true" : "false")
            + ",\"turn\":" + std::to_string(*CurrentTurn)
            + ",\"year\":" + std::to_string(game_year(*CurrentTurn))
            + ",\"match_id\":" + json_string(agent_match_id.c_str()) + '}';
    }
    if (command == "skip_all_ready_units") {
        if (!human_turn_actionable(faction_id)) return semantic_not_actionable();
        if (field_int(request, "confirm_skip_all_ready", 0) != 1) {
            return error_response("skip_all_confirmation_required",
                "Copy confirm_skip_all_ready=1 only from the fresh game-management choice after deliberately deciding that every listed ready unit is finished for this turn.");
        }
        int actual_count = 0;
        for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
            if (Vehs[veh_id].faction_id == faction_id
            && semantic_unit_requires_decision(veh_id)) ++actual_count;
        }
        int requested_count = field_int(request, "ready_unit_count", -1);
        if (actual_count <= 0 || requested_count != actual_count) {
            return error_response("ready_unit_set_changed",
                "The exact current ready-unit count must match the fresh skip-all choice; observe and decide again.");
        }
        int source_turn = *CurrentTurn;
        std::ostringstream skipped_ids;
        skipped_ids << '[';
        bool comma = false;
        int skipped_count = 0;
        // Prevalidation above plus the revision guard fixes the complete set at
        // this decision boundary. veh_skip consumes no object and cannot open a
        // modal, so applying the already-reviewed set does not cross an
        // interruptible native event-loop boundary.
        for (int veh_id = 0; veh_id < *VehCount; ++veh_id) {
            if (Vehs[veh_id].faction_id != faction_id
            || !semantic_unit_requires_decision(veh_id)) continue;
            if (comma) skipped_ids << ',';
            comma = true;
            skipped_ids << veh_id;
            veh_skip(veh_id);
            ++skipped_count;
        }
        skipped_ids << ']';
        return std::string("{\"ok\":true,\"command\":\"skip_all_ready_units\","
            "\"skipped_unit_count\":") + std::to_string(skipped_count)
            + ",\"skipped_unit_ids\":" + skipped_ids.str()
            + ",\"source_turn\":" + std::to_string(source_turn)
            + ",\"native_turn_advanced\":"
            + (*CurrentTurn != source_turn ? "true" : "false")
            + ",\"native_auto_end_turn_possible\":true}";
    }
    if (command == "corner_global_energy_market") {
        if (!human_turn_actionable(faction_id)) return semantic_not_actionable();
        if (field_int(request, "confirm_corner_market", 0) != 1) {
            return error_response("corner_market_confirmation_required",
                "Copy confirm_corner_market=1 only from the exact fresh economic-victory choice after deciding to commit the quoted energy.");
        }
        Faction& faction = Factions[faction_id];
        int headquarters_base_id = owned_headquarters_base(faction_id);
        if (!(*GameRules & RULES_VICTORY_ECONOMIC)
        || !has_tech(Rules->tech_preq_economic_victory, faction_id)) {
            return error_response("economic_victory_unavailable",
                "Economic victory is disabled or its prerequisite technology is not owned.");
        }
        if (headquarters_base_id < 0) {
            return error_response("headquarters_required",
                "A currently owned Headquarters is required to corner the Global Energy Market.");
        }
        if (faction.corner_market_active() || faction.corner_market_cost > 0) {
            return error_response("corner_market_already_active",
                "The Global Energy Market plan is already active.");
        }
        int cost = corner_market(faction_id);
        if (faction.energy_credits < cost) {
            return error_response("insufficient_energy",
                "The faction no longer has the quoted energy required for the Global Energy Market plan.");
        }
        faction.corner_market_turn = *CurrentTurn + Rules->turns_corner_global_energy_market;
        faction.corner_market_cost = cost;
        faction.energy_credits -= cost;
        ParseNumTable[0] = game_year(faction.corner_market_turn);
        deferred_corner_market_notice = true;
        PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
        std::ostringstream out;
        out << "{\"ok\":true,\"command\":\"corner_global_energy_market\","
            "\"cost\":" << cost << ",\"energy_credits\":" << faction.energy_credits
            << ",\"headquarters_base_id\":" << headquarters_base_id
            << ",\"completion_turn\":" << faction.corner_market_turn
            << ",\"completion_year\":" << game_year(faction.corner_market_turn) << '}';
        return out.str();
    }
    if (command == "choose_probe_sabotage_target") {
        if (strcmp(agent_popup_label(), "VIRUS") || active_probe_unit_id < 0
        || active_probe_base_id < 0) {
            return error_response("not_probe_sabotage_target_menu",
                "The native post-entry sabotage target menu is not active.");
        }
        int target_id = field_int(request, "sabotage_target_id", -1);
        bool reviewed_id = target_id == 0 || target_id == 98 || target_id == 99
            || (target_id >= Fac_ID_First && target_id <= Fac_ID_Last);
        BasePop* active = active_default_popup();
        if (!reviewed_id || !active || !popup_has_choice_id(active, target_id)) {
            return error_response("probe_sabotage_target_unavailable",
                "Choose an exact sabotage target id returned by the current native menu.");
        }
        if (target_id == 99) active_probe_abort_requested = true;
        if (!submit_popup_choice_id(active, target_id)) {
            return error_response("probe_sabotage_target_unavailable",
                "The native target disappeared before selection; observe again.");
        }
        return std::string("{\"ok\":true,\"command\":\"choose_probe_sabotage_target\","
            "\"sabotage_target_id\":") + std::to_string(target_id)
            + ",\"target_base_id\":" + std::to_string(active_probe_base_id)
            + ",\"aborting\":" + (target_id == 99 ? "true" : "false") + '}';
    }
    if (command == "choose_captive_leader") {
        if (strcmp(agent_popup_label(), "FREEWHO") || active_probe_unit_id < 0
        || active_probe_base_id < 0) {
            return error_response("not_captive_leader_menu",
                "The native post-success captive-leader menu is not active.");
        }
        int captive_id = field_int(request, "captive_faction_id", -1);
        BasePop* active = active_default_popup();
        if (captive_id < 1 || captive_id >= MaxPlayerNum || !active
        || !popup_has_choice_id(active, captive_id)
        || !submit_popup_choice_id(active, captive_id)) {
            return error_response("captive_leader_unavailable",
                "Choose an exact captive faction id returned by the current native menu.");
        }
        return std::string("{\"ok\":true,\"command\":\"choose_captive_leader\","
            "\"captive_faction_id\":") + std::to_string(captive_id)
            + ",\"faction_name\":" + json_string(MFactions[captive_id].formal_name_faction)
            + ",\"target_base_id\":" + std::to_string(active_probe_base_id) + '}';
    }
    if (command == "respond_to_probe_sabotage_warning") {
        std::string label = agent_popup_label();
        std::string response = field_string(request, "response");
        if ((label != "MILVIRUS" && label != "HQVIRUS")
        || (response != "abort" && response != "proceed")) {
            return error_response("invalid_probe_sabotage_warning_response",
                "Use abort or proceed from the active targeted-sabotage warning.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable", "The sabotage warning popup is unavailable.");
        if (response == "abort") active_probe_abort_requested = true;
        submit_popup_choice(active, response == "proceed" ? 1 : 0);
        return std::string("{\"ok\":true,\"command\":\"respond_to_probe_sabotage_warning\","
            "\"response\":") + json_string(response.c_str())
            + ",\"warning\":" + json_string(label.c_str()) + '}';
    }
    if (command == "respond_to_end_turn_confirmation") {
        std::string response = field_string(request, "response");
        if (strcmp(agent_popup_label(), "REALLYOVER")
        || (response != "cancel" && response != "proceed")) {
            return error_response("invalid_end_turn_confirmation_response",
                "Use cancel or proceed only from the active native end-turn confirmation.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable",
            "The native end-turn confirmation is unavailable.");
        if (response == "proceed") {
            pending_end_turn_completion = true;
            pending_end_turn_source_turn = *CurrentTurn;
        } else {
            pending_end_turn_completion = false;
            pending_end_turn_source_turn = -1;
            if (deferred_end_turn_faction_id >= 0) {
                if (deferred_end_turn_timer_id) {
                    KillTimer(NULL, deferred_end_turn_timer_id);
                    deferred_end_turn_timer_id = 0;
                }
                deferred_end_turn_faction_id = -1;
                deferred_end_turn_source_turn = -1;
                deferred_action.status = "rejected";
                deferred_action.resolution = "cancelled_by_controller";
            }
        }
        submit_popup_choice(active, response == "proceed" ? 1 : 0);
        return std::string("{\"ok\":true,\"command\":\"respond_to_end_turn_confirmation\","
            "\"response\":") + json_string(response.c_str()) + '}';
    }
    if (command == "respond_to_base_obliteration") {
        std::string label = agent_popup_label();
        std::string response = field_string(request, "response");
        if ((label != "OBLIT" && label != "OBLITOK")
        || active_obliterate_base_id < 0 || active_obliterate_unit_id < 0
        || (response != "cancel" && response != "proceed")) {
            return error_response("invalid_base_obliteration_response",
                "Use cancel or proceed only from the active native base-obliteration confirmation.");
        }
        if (response == "proceed"
        && field_int(request, "confirm_obliteration", 0) != 1) {
            return error_response("obliteration_confirmation_required",
                "Base obliteration is irreversible. Copy confirm_obliteration=1 only from the exact active native confirmation.");
        }
        if (response == "proceed" && label == "OBLIT"
        && field_int(request, "confirm_atrocity", 0) != 1) {
            return error_response("atrocity_confirmation_required",
                "Base obliteration may be an atrocity. Copy confirm_atrocity=1 only after accepting the active confirmation's consequences.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable",
            "The native base-obliteration confirmation is unavailable.");
        active_obliterate_decision = response == "proceed" ? 1 : 0;
        submit_popup_choice(active, response == "proceed" ? 1 : 0);
        return std::string("{\"ok\":true,\"command\":\"respond_to_base_obliteration\","
            "\"response\":") + json_string(response.c_str())
            + ",\"base_id\":" + std::to_string(active_obliterate_base_id)
            + ",\"unit_id\":" + std::to_string(active_obliterate_unit_id) + '}';
    }
    if (command == "respond_to_nerve_gas") {
        std::string response = field_string(request, "response");
        if (strcmp(agent_popup_label(), "USENERVE")
        || (response != "conventional" && response != "commit")) {
            return error_response("invalid_nerve_gas_response",
                "Use conventional or commit from the active native Nerve Gas interaction.");
        }
        if (response == "commit" && field_int(request, "confirm_atrocity", 0) != 1) {
            return error_response("nerve_gas_atrocity_confirmation_required",
                "Copy confirm_atrocity=1 only from the exact fresh Nerve Gas choice after deciding to accept the atrocity and its native consequences.");
        }
        int attacker_id = deferred_action.command == "move_unit"
            && deferred_action.status == "pending" ? deferred_action.unit_id : -1;
        if (attacker_id < 0 || attacker_id >= *VehCount
        || Vehs[attacker_id].faction_id != faction_id
        || !has_abil(Vehs[attacker_id].unit_id, ABL_NERVE_GAS)) {
            return error_response("nerve_gas_context_changed",
                "The queued owned Nerve Gas attacker is no longer the unit represented by this popup; observe again and do not commit.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable",
            "The native Nerve Gas confirmation is unavailable.");
        submit_popup_choice(active, response == "commit" ? 1 : 0);
        return std::string("{\"ok\":true,\"command\":\"respond_to_nerve_gas\","
            "\"response\":") + json_string(response.c_str())
            + ",\"action_id\":" + std::to_string(deferred_action.id)
            + ",\"attacker_unit_id\":" + std::to_string(attacker_id)
            + ",\"chemical_weapons_armed\":"
            + (response == "commit" ? "true" : "false") + '}';
    }
    if (command == "respond_to_combat_confirmation") {
        std::string label = agent_popup_label();
        std::string response = field_string(request, "response");
        if (!combat_confirmation_label(label)
        || (response != "cancel" && response != "proceed")) {
            return error_response("invalid_combat_confirmation_response",
                "Use cancel or proceed from the active native combat-odds interaction.");
        }
        if (response == "proceed" && field_int(request, "confirm_attack", 0) != 1) {
            return error_response("combat_confirmation_required",
                "Copy confirm_attack=1 only from the exact fresh proceed choice after deciding to accept the displayed combat odds.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable",
            "The native combat-odds popup is unavailable.");
        bool hasty = label == "HASTY";
        submit_popup_choice(active, response == "proceed"
            ? (hasty ? 0 : 1) : (hasty ? 1 : 0));
        return std::string("{\"ok\":true,\"command\":\"respond_to_combat_confirmation\","
            "\"response\":") + json_string(response.c_str())
            + ",\"risk_assessment\":" + json_string(hasty ? "hasty_assault"
                : label == "BADIDEA" ? "strongly_against" : "confirmation_requested") + '}';
    }
    if (command == "respond_to_territorial_incident") {
        std::string label = agent_popup_label();
        std::string response = field_string(request, "response");
        int choice = -1;
        bool consequential = false;
        if (territorial_demand_label(label)) {
            choice = response == "withdraw" ? 0
                : response == "mutual_withdrawal" ? 1
                : response == "refuse" ? 2 : -1;
            consequential = response == "refuse";
        } else if (label == "THISLANDISMYLAND") {
            choice = response == "cancel" ? 0 : response == "proceed" ? 1 : -1;
            consequential = response == "proceed";
        } else if (hostility_confirmation_label(label)) {
            choice = response == "cancel" ? 0
                : response == "declare_vendetta" ? 1 : -1;
            consequential = response == "declare_vendetta";
        }
        if (choice < 0) {
            return error_response("invalid_territorial_incident_response",
                "Use only a response returned by the active territorial or hostility interaction.");
        }
        if (consequential && field_int(request, "confirm_hostility", 0) != 1) {
            return error_response("hostility_confirmation_required",
                "Copy confirm_hostility=1 only from the exact fresh consequential incident choice after deciding to accept its diplomatic consequences.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable",
            "The territorial or hostility popup is unavailable.");
        int counterpart = territorial_incident_counterpart(faction_id);
        submit_popup_choice(active, choice);
        return std::string("{\"ok\":true,\"command\":\"respond_to_territorial_incident\","
            "\"response\":") + json_string(response.c_str())
            + ",\"popup_label\":" + json_string(label.c_str())
            + ",\"counterpart_faction_id\":" + std::to_string(counterpart) + '}';
    }
    if (command == "respond_to_supreme_leader") {
        std::string label = agent_popup_label();
        std::string response = field_string(request, "response");
        if ((label != "ACCEDE" && label != "ACCEDECOOP")
        || (response != "accede" && response != "defy")) {
            return error_response("invalid_supreme_leader_response",
                "Use accede or defy from the active Supreme Leader interaction.");
        }
        if (response == "defy" && field_int(request, "confirm_defiance", 0) != 1) {
            return error_response("defiance_confirmation_required",
                "Copy confirm_defiance=1 only from the exact fresh defy choice after deciding to reject the Council result.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable",
            "The Supreme Leader popup is unavailable.");
        int other = *diplo_second_faction;
        submit_popup_choice(active, response == "accede" ? 0 : 1);
        return std::string("{\"ok\":true,\"command\":\"respond_to_supreme_leader\","
            "\"response\":") + json_string(response.c_str())
            + ",\"elected_faction_id\":" + std::to_string(other) + '}';
    }
    if (command == "respond_to_game_over") {
        std::string response = field_string(request, "response");
        if (strcmp(agent_popup_label(), "GAMEOVERMAN")
        || (response != "finish" && response != "continue")) {
            return error_response("invalid_game_over_response",
                "Use finish or continue from the active final-score interaction.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable",
            "The final-score popup is unavailable.");
        submit_popup_choice(active, response == "finish" ? 0 : 1);
        return std::string("{\"ok\":true,\"command\":\"respond_to_game_over\","
            "\"response\":") + json_string(response.c_str()) + '}';
    }
    if (command == "acknowledge_popup") {
        std::string label = semantic_popup_label();
        if (!reviewed_information_popup(label) && !popup_information_only()
        && !narrative_intro_popup(label)) {
            return error_response("unsupported_popup", "This popup has no reviewed semantic choice. Report a capability gap.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable", "The active popup object is unavailable.");
        begin_popup_transition(active);
        // Invoke the native OK handler.  Its completion virtual removes this
        // exact BasePop from the engine's modal stack, which is what releases
        // the synchronous exec loop.  Hiding the Win object directly is not
        // equivalent: it can erase the pixels while leaving GameHalted and
        // WinModalState latched forever.  0x6044D0 is the list double-click
        // adapter, not a generic popup completion routine, and intentionally
        // refuses the button-only mode used by PLANETFALL.
        BasePop_on_button_clicked(active, 0);
        return std::string("{\"ok\":true,\"command\":\"acknowledge_popup\",\"popup_label\":")
            + json_string(label.c_str()) + '}';
    }
    if (command == "respond_to_contact") {
        std::string label = agent_popup_label();
        std::string response = field_string(request, "response");
        if (label != "COMM" && label != "COMMDIPLO") {
            return error_response("not_incoming_contact", "No reviewed incoming-contact prompt is active.");
        }
        int button = -1;
        if (response == "accept") button = 0;
        else if (response == "decline") button = 1;
        else if (label == "COMMDIPLO" && response == "later") button = 2;
        else if (label == "COMMDIPLO" && response == "block") button = 3;
        if (button < 0) {
            return error_response("invalid_contact_response", "Use a response returned by the interaction choices.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable", "The incoming-contact popup object is unavailable.");
        int other = *diplo_second_faction;
        submit_popup_choice(active, button);
        return std::string("{\"ok\":true,\"command\":\"respond_to_contact\",\"response\":")
            + json_string(response.c_str()) + ",\"counterpart_faction_id\":" + std::to_string(other) + '}';
    }
    if (command == "continue_diplomacy") {
        std::string label = agent_popup_label();
        if (label.compare(0, 8, "INTRONEW") && label.compare(0, 5, "INTRO")) {
            return error_response("not_diplomacy_greeting", "No reviewed diplomacy greeting is active.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable", "The diplomacy popup object is unavailable.");
        begin_popup_transition(active);
        BasePop_on_button_clicked(active, 0);
        return std::string("{\"ok\":true,\"command\":\"continue_diplomacy\",\"popup_label\":")
            + json_string(label.c_str()) + '}';
    }
    if (command == "choose_diplomacy_option") {
        std::string label = agent_popup_label();
        std::string option_name = field_string(request, "option");
        if (label == "PROPOSAL" && option_name == "offer_units") {
            return error_response("proposal_unit_offer_unreachable",
                "The executable never inserts the Script.txt trained-unit row into the native PROPOSAL menu. Native id 12 belongs to the separate Council-vote request path. Transfer a unit only through a fresh, rules-checked give_unit choice in visible Pact territory.");
        }
        const NamedDiplomacyOption* option = diplomacy_option(label, option_name);
        if (!option) {
            return error_response("invalid_diplomacy_option",
                "Use an option returned by the active DIPLO, PROPOSAL, or gift interaction choices.");
        }
        if (label == "COUNTER1" && option->id == DiploCounterEnergyPayment) {
            return error_response("energy_gift_amount_required",
                "Use give_energy_gift with an amount from the current interaction choice; the native amount prompt is handled atomically.");
        }
        BasePop* active = active_default_popup();
        if (!active || !submit_popup_choice_id(active, option->id)) {
            return error_response("diplomacy_option_unavailable",
                "That semantic option is not present in the native menu. Observe fresh interaction choices.");
        }
        return std::string("{\"ok\":true,\"command\":\"choose_diplomacy_option\",\"menu\":")
            + json_string(label.c_str()) + ",\"option\":" + json_string(option_name.c_str())
            + ",\"native_option_id\":" + std::to_string(option->id) + '}';
    }
    if (command == "give_energy_gift") {
        std::string gift_label = agent_popup_label();
        bool selector_active = gift_label == "COUNTER1";
        if (!selector_active) {
            return error_response("not_energy_gift_menu",
                "The native gift-type menu is not active. Choose this action only from the current interaction choices.");
        }
        BasePop* active = active_default_popup();
        if (!active || (selector_active
        && !popup_has_choice_id(active, DiploCounterEnergyPayment))) {
            return error_response("energy_gift_unavailable",
                "The native energy-payment option is no longer present. Observe fresh interaction choices.");
        }
        int amount = field_int(request, "amount", -1);
        int available = Factions[faction_id].energy_credits;
        if (amount < 1 || amount > available) {
            return std::string("{\"ok\":false,\"error\":{\"code\":\"invalid_energy_gift_amount\","
                "\"message\":\"amount must be within the exact bounds returned by the current interaction choice.\"},"
                "\"amount_min\":1,\"amount_max\":") + std::to_string(max(0, available)) + '}';
        }
        int other = *diplo_second_faction;
        if (other < 1 || other >= MaxPlayerNum || other == faction_id || !is_alive(other)) {
            return error_response("invalid_energy_gift_counterpart",
                "The native diplomatic counterpart is no longer a valid living faction.");
        }
        int other_before = Factions[other].energy_credits;
        int withheld = available - amount;
        pending_energy_gift = true;
        pending_energy_gift_faction_id = faction_id;
        pending_energy_gift_other_id = other;
        pending_energy_gift_amount = amount;
        pending_energy_gift_timer_ticks = 0;
        pending_energy_gift_prompt_seen = false;
        pending_energy_gift_receipt_seen = false;
        // Close the selector through its native cancel result so its suspended
        // outer diplomacy caller cannot perform the gift a second time after
        // this MCP request returns. We invoke make_gift ourselves below with
        // the reviewed type while the entire transfer remains atomic.
        if (!popup_has_choice_id(active, 0) || !submit_popup_choice_id(active, 0)) {
            clear_pending_energy_gift();
            return error_response("energy_gift_selector_unavailable",
                "The native gift selector could not be safely dismissed; no gift was submitted.");
        }
        int previous_counter_proposal = *diplo_counter_proposal_id;
        *diplo_counter_proposal_id = DiploCounterEnergyPayment;
        energy_gift_timer_id = SetTimer(NULL, 0, 25, energy_gift_timer_proc);
        if (!energy_gift_timer_id) {
            *diplo_counter_proposal_id = previous_counter_proposal;
            clear_pending_energy_gift();
            return error_response("energy_gift_timer_unavailable",
                "The native modal driver could not be scheduled; no gift was submitted.");
        }
        Factions[faction_id].energy_credits = amount;
        make_gift(faction_id, other);
        *diplo_counter_proposal_id = previous_counter_proposal;
        bool prompt_seen = pending_energy_gift_prompt_seen;
        bool receipt_seen = pending_energy_gift_receipt_seen;
        clear_pending_energy_gift();
        if (faction_id >= 1 && faction_id < MaxPlayerNum) {
            Factions[faction_id].energy_credits += withheld;
        }
        Console_update_data(MapWin, 0);
        int own_after = Factions[faction_id].energy_credits;
        int other_after = Factions[other].energy_credits;
        if (!prompt_seen || other_after - other_before != amount
        || available - own_after != amount) {
            std::ostringstream failure;
            failure << "{\"ok\":false,\"error\":{\"code\":\"native_energy_gift_verification_failed\","
                "\"message\":\"The native gift chain did not produce the exact requested treasury transfer. Observe before taking another action.\"},"
                "\"requested_amount\":" << amount
                << ",\"native_amount_prompt_seen\":" << (prompt_seen ? "true" : "false")
                << ",\"native_receipt_seen\":" << (receipt_seen ? "true" : "false")
                << ",\"player_energy_before\":" << available
                << ",\"player_energy_after\":" << own_after
                << ",\"counterpart_energy_before\":" << other_before
                << ",\"counterpart_energy_after\":" << other_after << '}';
            return failure.str();
        }
        std::ostringstream result;
        result << "{\"ok\":true,\"command\":\"give_energy_gift\",\"amount\":" << amount
            << ",\"counterpart_faction_id\":" << other
            << ",\"player_energy_before\":" << available
            << ",\"player_energy_after\":" << own_after
            << ",\"counterpart_energy_before\":" << other_before
            << ",\"counterpart_energy_after\":" << other_after
            << ",\"native_amount_prompt_seen\":" << (prompt_seen ? "true" : "false")
            << ",\"native_receipt_seen\":" << (receipt_seen ? "true" : "false") << '}';
        return result.str();
    }
    if (command == "choose_diplomacy_target") {
        std::string label = agent_popup_label();
        std::string target_kind = field_string(request, "target_kind");
        bool commlink = label == "PROPOSECOMMLINK" && target_kind == "commlink";
        bool attack = label == "PROPOSEATTACK" && target_kind == "joint_attack";
        if (!commlink && !attack) {
            return error_response("not_diplomacy_target_menu",
                "Use the exact target kind returned by the active reviewed diplomacy selector.");
        }
        int target = field_int(request, "faction_id", -1);
        BasePop* active = active_default_popup();
        if (target < 1 || target >= MaxPlayerNum || !active
        || !submit_popup_choice_id(active, target)) {
            return error_response("diplomacy_target_unavailable",
                "Choose an exact faction id returned by the current native target selector.");
        }
        return std::string("{\"ok\":true,\"command\":\"choose_diplomacy_target\","
            "\"target_kind\":") + json_string(target_kind.c_str())
            + ",\"faction_id\":" + std::to_string(target)
            + ",\"faction_name\":" + json_string(MFactions[target].formal_name_faction) + '}';
    }
    if (command == "choose_diplomacy_base_target") {
        if (strcmp(agent_popup_label(), "PROPOSEBASE")) {
            return error_response("not_diplomacy_base_menu",
                "The reviewed native base-demand selector is not active.");
        }
        int base_id = field_int(request, "target_base_id", -1);
        BasePop* active = active_default_popup();
        if (base_id < 0 || base_id >= *BaseCount || !active
        || !submit_popup_choice_id(active, base_id)) {
            return error_response("diplomacy_base_unavailable",
                "Choose an exact base id returned by the current native base selector.");
        }
        return std::string("{\"ok\":true,\"command\":\"choose_diplomacy_base_target\","
            "\"target_base_id\":") + std::to_string(base_id)
            + ",\"base_name\":" + json_string(Bases[base_id].name) + '}';
    }
    if (command == "cancel_diplomacy_selection") {
        std::string label = agent_popup_label();
        if (label != "PROPOSECOMMLINK" && label != "PROPOSEATTACK"
        && label != "PROPOSEBASE") {
            return error_response("not_diplomacy_selection",
                "No reviewed cancellable diplomacy target selector is active.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable", "The diplomacy selector is unavailable.");
        begin_popup_transition(active);
        BasePop_on_button_clicked(active, 1);
        return std::string("{\"ok\":true,\"command\":\"cancel_diplomacy_selection\","
            "\"popup_label\":") + json_string(label.c_str()) + '}';
    }
    if (command == "choose_council_proposal") {
        if (strcmp(agent_popup_label(), "COUNCILISSUES")) {
            return error_response("not_council_proposal_menu",
                "The native Planetary Council proposal menu is not active.");
        }
        int proposal_id = field_int(request, "proposal_id", -1);
        const NamedCouncilProposal* proposal = council_proposal(proposal_id);
        BasePop* active = active_default_popup();
        int vote_value = 0;
        if (proposal_id == PROP_ELECT_PLANETARY_GOVERNOR
        || proposal_id == PROP_UNITE_SUPREME_LEADER) {
            int candidate = field_int(request, "candidate_faction_id", -1);
            if (candidate < 1 || candidate >= MaxPlayerNum || !is_alive(candidate)
            || is_alien(candidate) || !eligible(candidate)) {
                return error_response("invalid_council_candidate",
                    "Choose an eligible candidate returned with the current Council proposal.");
            }
            vote_value = candidate;
        } else {
            std::string response = field_string(request, "response");
            if (response != "yea" && response != "nay") {
                return error_response("missing_council_ballot",
                    "Choose yea or nay as returned with the current Council proposal.");
            }
            vote_value = response == "yea" ? -1 : -2;
        }
        if (!proposal || !active || !popup_has_choice_id(active, proposal_id)) {
            return error_response("council_proposal_unavailable",
                "Choose a proposal returned by the current interaction choices.");
        }
        clear_pending_council_vote();
        pending_council_proposal_id = proposal_id;
        pending_council_faction_id = faction_id;
        pending_council_vote_value = vote_value;
        pending_council_timer_stage = 1;
        council_vote_timer_id = SetTimer(NULL, 0, 25, council_vote_timer_proc);
        if (!council_vote_timer_id) {
            clear_pending_council_vote();
            return error_response("council_vote_schedule_failed",
                "The game could not schedule the semantic ballot on its UI thread.");
        }
        if (!submit_popup_choice_id(active, proposal_id)) {
            clear_pending_council_vote();
            return error_response("council_proposal_unavailable",
                "The native proposal disappeared before selection; observe again.");
        }
        return std::string("{\"ok\":true,\"command\":\"choose_council_proposal\",\"proposal_id\":")
            + std::to_string(proposal_id) + ",\"name\":" + json_string(proposal->name)
            + ",\"ballot_scheduled\":true}";
    }
    if (command == "respond_to_council_vote_bargain") {
        std::string label = agent_popup_label();
        int tech_count = council_vote_bargain_tech_count(label);
        std::string payment = field_string(request, "payment");
        if (tech_count < 0 || (payment != "none" && payment != "energy"
        && payment != "technologies") || (payment == "technologies" && tech_count == 0)) {
            return error_response("invalid_council_vote_bargain",
                "Use one exact payment method returned by the active Council vote bargain.");
        }
        int choice = payment == "none" ? 0 : payment == "energy" ? 1 : tech_count + 1;
        if (payment == "energy") {
            int price = agent_popup_parse_number(0);
            if (price < 0 || Factions[faction_id].energy_credits < price) {
                return error_response("council_vote_bargain_not_affordable",
                    "The quoted Council vote price is no longer affordable; inspect fresh choices.");
            }
        }
        if (payment == "technologies") {
            int tech_ids[] = {
                *diplo_entry_id, *diplo_tech_id2, *diplo_tech_id3, *diplo_tech_id4,
            };
            for (int index = 0; index < tech_count; ++index) {
                int tech_id = tech_ids[index];
                if (tech_id < 0 || tech_id >= MaxTechnologyNum
                || !(TechOwners[tech_id] & (1 << faction_id))) {
                    return error_response("council_vote_technology_changed",
                        "A technology in the requested vote-payment bundle is no longer owned; inspect fresh choices.");
                }
            }
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable", "The Council vote bargain is unavailable.");
        submit_popup_choice(active, choice);
        return std::string("{\"ok\":true,\"command\":\"respond_to_council_vote_bargain\","
            "\"payment\":") + json_string(payment.c_str())
            + ",\"requested_ballot\":"
            + json_string(council_vote_bargain_ballot(label)) + '}';
    }
    if (command == "respond_to_incoming_vote_offer") {
        std::string label = agent_popup_label();
        if (!incoming_council_vote_offer_label(label)) {
            return error_response("not_incoming_vote_offer",
                "No reviewed incoming Council vote-purchase offer is active.");
        }
        std::string response = field_string(request, "response");
        if (response != "reject" && response != "accept") {
            return error_response("invalid_incoming_vote_offer_response",
                "Choose accept or reject from the current incoming vote offer.");
        }
        int candidate = incoming_council_vote_candidate(faction_id);
        bool technology_offer = label == "VOTEFORMETECH";
        int tech1 = *diplo_tech_id1;
        int tech2 = *diplo_vote_offer_tech_id2;
        if (response == "accept") {
            if (candidate < 1 || field_int(request, "candidate_faction_id", -1) != candidate) {
                return error_response("incoming_vote_candidate_changed",
                    "Copy the exact candidate faction from this offer's fresh accept choice.");
            }
            if (field_int(request, "confirm_vote_commitment", 0) != 1) {
                return error_response("vote_commitment_confirmation_required",
                    "Set confirm_vote_commitment to 1 only after deliberately accepting the fresh Council vote-purchase offer.");
            }
            if (technology_offer && (tech1 < 0 || tech1 >= MaxTechnologyNum
            || tech2 < 0 || tech2 >= MaxTechnologyNum || tech1 == tech2)) {
                return error_response("incoming_vote_technology_offer_changed",
                    "The two native technologies in this offer are no longer valid; inspect fresh choices.");
            }
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable", "The incoming Council vote offer is unavailable.");
        submit_popup_choice(active, response == "accept" ? 1 : 0);
        std::ostringstream result;
        result << "{\"ok\":true,\"command\":\"respond_to_incoming_vote_offer\","
            "\"response\":" << json_string(response.c_str())
            << ",\"candidate_faction_id\":" << candidate
            << ",\"payment_type\":" << json_string(technology_offer ? "technologies" : "energy");
        if (technology_offer) {
            result << ",\"technology_ids\":[" << tech1 << ',' << tech2 << ']';
        } else {
            result << ",\"energy_credits_received\":"
                << max(0, agent_popup_parse_number(0));
        }
        result << ",\"vote_commitment_selected\":" << (response == "accept" ? "true" : "false")
            << ",\"observe_council_after_response\":true}";
        return result.str();
    }
    if (command == "cast_council_vote") {
        int council_window_proposal = active_council_window_proposal();
        if (council_window_proposal >= 0) {
            bool candidate_ballot = council_window_proposal == PROP_ELECT_PLANETARY_GOVERNOR
                || council_window_proposal == PROP_UNITE_SUPREME_LEADER;
            int vote_value = 0;
            if (candidate_ballot) {
                int candidate = field_int(request, "candidate_faction_id", -1);
                if (candidate < 1 || candidate >= MaxPlayerNum || !is_alive(candidate)
                || is_alien(candidate) || !eligible(candidate)) {
                    return error_response("invalid_council_candidate",
                        "Choose an eligible candidate returned by the active Council window ballot.");
                }
                vote_value = candidate;
            } else {
                std::string response = field_string(request, "response");
                if (response != "yea" && response != "nay") {
                    return error_response("invalid_council_vote",
                        "Choose yea or nay from the active Council window ballot.");
                }
                vote_value = response == "yea" ? -1 : -2;
            }
            clear_pending_council_vote();
            pending_council_proposal_id = council_window_proposal;
            pending_council_faction_id = faction_id;
            pending_council_vote_value = vote_value;
            pending_council_timer_stage = 1;
            council_vote_timer_id = SetTimer(NULL, 0, 25, council_vote_timer_proc);
            if (!council_vote_timer_id) {
                clear_pending_council_vote();
                return error_response("council_vote_schedule_failed",
                    "The game could not schedule the Council-window ballot on its UI thread.");
            }
            return std::string("{\"ok\":true,\"command\":\"cast_council_vote\","
                "\"proposal_id\":") + std::to_string(council_window_proposal)
                + ",\"ballot_scheduled\":true}";
        }
        std::string label = agent_popup_label();
        BasePop* active = active_default_popup();
        if (!active) return error_response("council_ballot_unavailable", "No native Council ballot is active.");
        if (label == "COUNCILVOTE") {
            std::string response = field_string(request, "response");
            int position = response == "yea" ? 0 : response == "nay" ? 1 : -1;
            if (position < 0) {
                return error_response("invalid_council_vote", "Choose yea or nay from the current ballot.");
            }
            submit_popup_choice(active, position);
            return std::string("{\"ok\":true,\"command\":\"cast_council_vote\",\"response\":")
                + json_string(response.c_str()) + '}';
        }
        if (label == "COUNCILVOTEGOV") {
            int candidate = field_int(request, "candidate_faction_id", -1);
            if (candidate < 1 || candidate >= MaxPlayerNum || !is_alive(candidate)
            || is_alien(candidate) || !submit_popup_choice_id(active, candidate)) {
                return error_response("invalid_council_candidate",
                    "Choose a candidate returned by the current Council ballot.");
            }
            return std::string("{\"ok\":true,\"command\":\"cast_council_vote\",\"candidate_faction_id\":")
                + std::to_string(candidate) + ",\"faction_name\":"
                + json_string(MFactions[candidate].formal_name_faction) + '}';
        }
        return error_response("not_council_ballot", "No reviewed Planetary Council ballot is active.");
    }
    if (command == "respond_to_diplomatic_offer") {
        std::string label = semantic_popup_label();
        std::string response = field_string(request, "response");
        bool counter = response == "counter";
        bool tech_trade = technology_trade_label(label);
        bool tech_demand = technology_demand_label(label);
        bool tech_demand_followup = technology_demand_followup_label(label);
        bool tech_demand_counter = tech_demand && technology_demand_counter_label(label)
            && counter && (field_string(request, "payment") == "energy"
                || field_string(request, "payment") == "technologies");
        bool relationship_offer = relationship_offer_label(label);
        bool attack_demand = attack_demand_label(label);
        bool joint_attack_energy_counteroffer = joint_attack_energy_counteroffer_label(label);
        int joint_attack_tech_count = joint_attack_counteroffer_tech_count(label);
        bool joint_attack_tech_counteroffer = joint_attack_tech_count > 0;
        bool bribe_demand = bribe_demand_label(label);
        bool bribe_ultimatum = label == "WEASELOUT";
        bool loan_offer = loan_offer_label(label);
        bool player_borrows = !label.compare(0, 10, "ENERGYLOAN");
        bool technology_purchase = technology_purchase_offer_label(label);
        bool prototype_purchase = prototype_purchase_offer_label(label);
        bool commlink_purchase = commlink_purchase_offer_label(label);
        bool base_purchase = base_purchase_offer_label(label);
        bool base_technology_exchange = base_technology_exchange_label(label);
        bool commlink_sale = commlink_sale_offer_label(label);
        bool commlink_technology_exchange = commlink_technology_exchange_label(label);
        int enemy_map_mode = enemy_map_offer_mode(label);
        bool enemy_map_offer = enemy_map_mode >= 0;
        bool friendly_map_exchange = friendly_map_exchange_label(label);
        int introduced_commlink_mode = introduced_commlink_offer_mode(label);
        bool introduced_commlink = introduced_commlink_mode >= 0;
        bool technology_sale = technology_sale_offer_label(label);
        if ((!tech_trade && !tech_demand && !tech_demand_followup
            && !relationship_offer && !attack_demand
            && !joint_attack_energy_counteroffer && !joint_attack_tech_counteroffer
            && !bribe_demand && !bribe_ultimatum && !loan_offer
            && !technology_purchase && !prototype_purchase && !commlink_purchase
            && !base_purchase && !base_technology_exchange && !commlink_sale
            && !commlink_technology_exchange && !enemy_map_offer && !friendly_map_exchange
            && !introduced_commlink && !technology_sale)
        || (response != "reject" && response != "accept" && !counter)
        || (counter && !tech_demand_counter && !bribe_demand
            && !(loan_offer && !player_borrows)
            && !(technology_sale && label == "ENERGYTECH2"))) {
            return error_response("invalid_diplomatic_offer_response",
                "Use an exact reject, accept, or typed counteroffer returned by the active reviewed diplomatic choices.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable", "The diplomatic offer popup is unavailable.");
        int principal = ParseNumTable[0];
        if ((joint_attack_energy_counteroffer || joint_attack_tech_counteroffer)
        && response == "accept") {
            int target = *diplo_trade_faction_id;
            if (target < 1 || target >= MaxPlayerNum || target == faction_id
            || target == *diplo_second_faction || !is_alive(target)) {
                return error_response("joint_attack_target_changed",
                    "The named joint-Vendetta target is no longer valid; inspect fresh choices.");
            }
            if (joint_attack_energy_counteroffer) {
                int price = agent_popup_parse_number(0);
                if (price < 0 || Factions[faction_id].energy_credits < price) {
                    return error_response("joint_attack_counteroffer_not_affordable",
                        "The quoted joint-Vendetta price is no longer affordable; inspect fresh choices.");
                }
            } else {
                int tech_ids[] = {
                    *diplo_entry_id, *diplo_tech_id2, *diplo_tech_id3, *diplo_tech_id4,
                };
                for (int index = 0; index < joint_attack_tech_count; ++index) {
                    int tech_id = tech_ids[index];
                    if (tech_id < 0 || tech_id >= MaxTechnologyNum
                    || !(TechOwners[tech_id] & (1 << faction_id))) {
                        return error_response("joint_attack_counteroffer_technology_changed",
                            "A technology in the complete joint-Vendetta payment bundle is no longer owned; inspect fresh choices.");
                    }
                }
            }
        }
        if ((tech_demand || tech_demand_followup) && response == "accept"
        && !demanded_technology_context_valid(label, faction_id)) {
            return error_response("technology_demand_changed",
                "The complete native technology-demand bundle is invalid or no longer owned; inspect fresh choices and do not concede.");
        }
        if (tech_demand_counter) {
            std::string payment = field_string(request, "payment");
            if (payment == "energy" && agent_popup_parse_number(0) < 0) {
                return error_response("technology_demand_counter_price_changed",
                    "The native energy counteroffer quote is no longer valid; inspect fresh choices.");
            }
            if (payment == "technologies") {
                int reciprocal = *diplo_tech_id2;
                int counterpart = *diplo_second_faction;
                if (reciprocal < 0 || reciprocal >= MaxTechnologyNum
                || counterpart < 1 || counterpart >= MaxPlayerNum
                || !(TechOwners[reciprocal] & (1 << counterpart))
                || (TechOwners[reciprocal] & (1 << faction_id))) {
                    return error_response("technology_demand_reciprocal_changed",
                        "The named reciprocal technology is no longer a valid counterpart-owned acquisition; inspect fresh choices.");
                }
            }
        }
        if (loan_offer && !player_borrows && response != "reject") {
            int cost = response == "counter" ? principal / 2 : principal;
            if (cost < 0 || Factions[faction_id].energy_credits < cost) {
                return error_response("loan_not_affordable",
                    "The selected lending principal is no longer affordable; inspect fresh choices.");
            }
        }
        if (technology_purchase && response == "accept") {
            int tech_id = *diplo_tech_id1;
            int price = ParseNumTable[0];
            if (tech_id < 0 || tech_id >= MaxTechnologyNum
            || (TechOwners[tech_id] & (1 << faction_id))) {
                return error_response("technology_purchase_changed",
                    "The offered technology is no longer a valid unowned acquisition; inspect fresh choices.");
            }
            if (price < 0 || Factions[faction_id].energy_credits < price) {
                return error_response("technology_purchase_not_affordable",
                    "The quoted technology purchase is no longer affordable; inspect fresh choices.");
            }
        }
        if (prototype_purchase && response == "accept") {
            int prototype_id = *diplo_tech_id1 - 97;
            int price = agent_popup_parse_number(0);
            if (prototype_id < 0 || prototype_id >= MaxProtoNum
            || !Units[prototype_id].name[0]) {
                return error_response("prototype_purchase_changed",
                    "The offered prototype is no longer a valid unit design; inspect fresh choices.");
            }
            if (price < 0 || Factions[faction_id].energy_credits < price) {
                return error_response("prototype_purchase_not_affordable",
                    "The quoted prototype purchase is no longer affordable; inspect fresh choices.");
            }
        }
        if (commlink_purchase && response == "accept") {
            int target = *diplo_tech_id1 - 89;
            int price = agent_popup_parse_number(0);
            if (target < 1 || target >= MaxPlayerNum || target == faction_id
            || !is_alive(target) || has_treaty(faction_id, target, DIPLO_COMMLINK)) {
                return error_response("commlink_purchase_changed",
                    "The offered commlink is no longer a valid unowned frequency; inspect fresh choices.");
            }
            if (price < 0 || Factions[faction_id].energy_credits < price) {
                return error_response("commlink_purchase_not_affordable",
                    "The quoted commlink purchase is no longer affordable; inspect fresh choices.");
            }
        }
        if ((base_purchase || base_technology_exchange) && response == "accept") {
            int base_id = *diplo_ask_base_swap_id;
            int counterpart = *diplo_second_faction;
            if (base_id < 0 || base_id >= *BaseCount
            || counterpart < 1 || counterpart >= MaxPlayerNum
            || Bases[base_id].faction_id != counterpart) {
                return error_response("base_purchase_changed",
                    "The offered base is no longer owned by the counterpart; inspect fresh choices.");
            }
            if (base_purchase) {
                int price = agent_popup_parse_number(0);
                if (price < 0 || Factions[faction_id].energy_credits < price) {
                    return error_response("base_purchase_not_affordable",
                        "The quoted base purchase is no longer affordable; inspect fresh choices.");
                }
            }
        }
        if (commlink_sale && response == "accept") {
            int target = *diplo_trade_faction_id;
            if (target < 1 || target >= MaxPlayerNum || target == faction_id
            || !has_treaty(faction_id, target, DIPLO_COMMLINK)
            || agent_popup_parse_number(0) < 0) {
                return error_response("commlink_sale_changed",
                    "The requested commlink or native payment is no longer valid; inspect fresh choices.");
            }
        }
        if (commlink_technology_exchange && response == "accept") {
            int target = *diplo_trade_faction_id;
            int tech_id = *diplo_entry_id;
            if (target < 1 || target >= MaxPlayerNum || target == faction_id
            || has_treaty(faction_id, target, DIPLO_COMMLINK)
            || tech_id < 0 || tech_id >= MaxTechnologyNum
            || !(TechOwners[tech_id] & (1 << faction_id))) {
                return error_response("commlink_technology_exchange_changed",
                    "The target commlink or named owned technology is no longer available; inspect fresh choices.");
            }
        }
        if (introduced_commlink && response == "accept") {
            int target = *diplo_tech_faction;
            if (target < 1 || target >= MaxPlayerNum || target == faction_id
            || !is_alive(target) || has_treaty(faction_id, target, DIPLO_COMMLINK)) {
                return error_response("introduced_commlink_changed",
                    "The introduced faction is no longer a valid uncontacted commlink target; inspect fresh choices.");
            }
            if (introduced_commlink_mode == 2) {
                int price = agent_popup_parse_number(0);
                if (price < 0 || Factions[faction_id].energy_credits < price) {
                    return error_response("introduced_commlink_not_affordable",
                        "The quoted commlink introduction is no longer affordable; inspect fresh choices.");
                }
            } else if (introduced_commlink_mode == 1) {
                int tech_id = *diplo_entry_id;
                if (tech_id < 0 || tech_id >= MaxTechnologyNum
                || !(TechOwners[tech_id] & (1 << faction_id))) {
                    return error_response("introduced_commlink_technology_changed",
                        "The requested technology is no longer owned; inspect fresh choices.");
                }
            }
        }
        if (enemy_map_offer && response == "accept") {
            int target = *diplo_intel_faction;
            if (target < 1 || target >= MaxPlayerNum || target == faction_id
            || !is_alive(target)) {
                return error_response("enemy_map_target_changed",
                    "The named intelligence-map target is no longer valid; inspect fresh choices.");
            }
            if (enemy_map_mode == 1) {
                int price = agent_popup_parse_number(0);
                if (price < 0 || Factions[faction_id].energy_credits < price) {
                    return error_response("enemy_map_not_affordable",
                        "The quoted intelligence-map price is no longer affordable; inspect fresh choices.");
                }
            } else {
                int tech_id = *diplo_entry_id;
                if (tech_id < 0 || tech_id >= MaxTechnologyNum
                || !(TechOwners[tech_id] & (1 << faction_id))) {
                    return error_response("enemy_map_technology_changed",
                        "The requested technology is no longer owned; inspect fresh choices.");
                }
            }
        }
        if (friendly_map_exchange && response == "accept") {
            int target = *diplo_intel_faction;
            int tech_id = *diplo_entry_id;
            if (target < 1 || target >= MaxPlayerNum || target == faction_id
            || !is_alive(target)) {
                return error_response("friendly_map_target_changed",
                    "The named territory-map target is no longer valid; inspect fresh choices.");
            }
            if (tech_id < 0 || tech_id >= MaxTechnologyNum
            || !(TechOwners[tech_id] & (1 << faction_id))) {
                return error_response("friendly_map_technology_changed",
                    "The requested technology is no longer owned; inspect fresh choices.");
            }
        }
        if (technology_sale && response != "reject") {
            int tech_id = response == "counter" ? *diplo_tech_id2 : *diplo_entry_id;
            if (tech_id < 0 || tech_id >= MaxTechnologyNum
            || !(TechOwners[tech_id] & (1 << faction_id))) {
                return error_response("technology_sale_changed",
                    "The selected owned technology is no longer available for this sale; inspect fresh choices.");
            }
        }
        int choice = relationship_offer ? (response == "accept" ? 0 : 1)
            : (technology_purchase || prototype_purchase || commlink_purchase
                || base_purchase || base_technology_exchange || commlink_sale
                || commlink_technology_exchange || enemy_map_offer
                || friendly_map_exchange || introduced_commlink)
                ? (response == "accept" ? 1 : 0)
            : technology_sale ? (response == "reject" ? 0 : response == "accept" ? 1 : 2)
            : loan_offer ? (response == "reject" ? 0 : response == "accept" ? 1 : 2)
            : tech_demand_counter ? (field_string(request, "payment") == "energy" ? 2 : 3)
            : counter ? 2 : (response == "accept" ? 1 : 0);
        submit_popup_choice(active, choice);
        const char* offer_type = relationship_offer
            ? (label == "SWEARAPACT" ? "pact"
                : label.find("TREATY") != std::string::npos ? "treaty" : "truce")
            : attack_demand ? "join_vendetta_request"
            : (joint_attack_energy_counteroffer || joint_attack_tech_counteroffer)
                ? "joint_attack_counteroffer"
            : (bribe_demand || bribe_ultimatum) ? "energy_demand"
            : prototype_purchase ? "prototype_purchase"
            : commlink_purchase ? "commlink_purchase"
            : base_purchase ? "base_purchase"
            : base_technology_exchange ? "base_for_all_technologies"
            : commlink_sale ? "commlink_sale"
            : commlink_technology_exchange ? "commlink_for_technology"
            : enemy_map_offer ? "enemy_installation_map"
            : friendly_map_exchange ? "territory_map_for_technology"
            : introduced_commlink ? "introduced_commlink"
            : technology_purchase ? "technology_purchase"
            : technology_sale ? "technology_sale"
            : loan_offer ? "loan_offer"
            : tech_demand_followup ? "technology_demand_followup"
            : tech_demand ? "technology_demand" : "technology_or_map_exchange";
        return std::string("{\"ok\":true,\"command\":\"respond_to_diplomatic_offer\",\"response\":")
            + json_string(response.c_str()) + ",\"offer_type\":"
            + json_string(offer_type) + '}';
    }
    if (command == "respond_to_design_offer") {
        std::string label = agent_popup_label();
        std::string response = field_string(request, "response");
        if (label != "ASKSEEDESIGN" || (response != "decline" && response != "open")) {
            return error_response("invalid_design_offer_response",
                "Choose decline or open from the active ASKSEEDESIGN interaction.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable", "The unit-design offer popup is unavailable.");
        // Always close the coordinate-oriented native offer.  An `open` response
        // hands control to the already complete semantic Unit Workshop instead.
        submit_popup_choice(active, 0);
        return std::string("{\"ok\":true,\"command\":\"respond_to_design_offer\",\"response\":")
            + json_string(response.c_str())
            + (response == "open"
                ? ",\"next_choice_kind\":\"unit_design\",\"semantic_workshop\":true}"
                : "}");
    }
    if (command == "respond_to_artifact") {
        std::string response = field_string(request, "response");
        int choice_id = response == "no_action" ? 0
            : response == "link_technology" ? 1
            : response == "accelerate_production" ? 2 : -1;
        BasePop* active = active_default_popup();
        int unit_id = artifact_interaction.unit_id;
        int base_id = artifact_interaction.base_id;
        bool valid = !strcmp(agent_popup_label(), "ARTIFACT")
            && artifact_interaction.valid && active
            && unit_id >= 0 && unit_id < *VehCount
            && base_id >= 0 && base_id < *BaseCount
            && Vehs[unit_id].faction_id == faction_id
            && Vehs[unit_id].plan() == PLAN_ARTIFACT
            && Bases[base_id].faction_id == faction_id
            && Bases[base_id].queue_items[0] == artifact_interaction.production_id;
        if (!valid || choice_id < 0 || !popup_has_choice_id(active, choice_id)) {
            return error_response("invalid_artifact_response",
                "Use only an exact response returned by the fresh active Artifact interaction.");
        }
        if (choice_id > 0 && field_int(request, "confirm_consume_artifact", 0) != 1) {
            return error_response("artifact_consumption_confirmation_required",
                "Copy confirm_consume_artifact=1 only after deliberately choosing a consuming Artifact action.");
        }
        int production_id = artifact_interaction.production_id;
        int action_id = deferred_action.status == "pending"
            ? static_cast<int>(deferred_action.id) : 0;
        resolved_artifact_unit_id = unit_id;
        resolved_artifact_consumed = choice_id > 0;
        if (!submit_popup_choice_id(active, choice_id)) {
            resolved_artifact_unit_id = -1;
            resolved_artifact_consumed = false;
            return error_response("artifact_choice_unavailable",
                "The native Artifact option is no longer available; observe fresh state.");
        }
        std::ostringstream out;
        out << "{\"ok\":true,\"command\":\"respond_to_artifact\",\"response\":"
            << json_string(response.c_str()) << ",\"unit_id\":" << unit_id
            << ",\"base_id\":" << base_id << ",\"production_id\":" << production_id
            << ",\"consumes_artifact\":" << (choice_id > 0 ? "true" : "false");
        if (action_id > 0) {
            out << ",\"queued\":true,\"action_id\":" << action_id;
        }
        out << '}';
        return out.str();
    }
    if (command == "respond_to_monolith") {
        std::string label = agent_popup_label();
        std::string response = field_string(request, "response");
        int choice = response == "leave" ? 0 : response == "investigate" ? 1
            : response == "always" ? 2 : -1;
        if (label != "MONOLITH" || choice < 0) {
            return error_response("invalid_monolith_response",
                "Choose leave, investigate, or always from the active MONOLITH interaction.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable", "The monolith popup is unavailable.");
        submit_popup_choice(active, choice);
        return std::string("{\"ok\":true,\"command\":\"respond_to_monolith\",\"response\":")
            + json_string(response.c_str()) + '}';
    }
    if (command == "respond_to_probe_incident") {
        std::string label = agent_popup_label();
        std::string response = field_string(request, "response");
        if (!probe_excuse_label(label)) {
            return error_response("not_probe_incident",
                "No reviewed probe-incident retaliation choice is active.");
        }
        bool pact = label == "PACTEXCUSE" || label == "PACTFRAMEEXCUSE";
        int choice = ((!pact && response == "forgive") || (pact && response == "tolerate")) ? 0
            : ((!pact && response == "declare_vendetta")
                || (pact && response == "renounce_pact")) ? 1 : -1;
        if (choice < 0) {
            return error_response("invalid_probe_incident_response",
                "Use the response returned by the active probe-incident choices.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable", "The probe-incident popup is unavailable.");
        int offender = probe_excuse_context.valid ? probe_excuse_context.offender_faction_id : -1;
        submit_popup_choice(active, choice);
        return std::string("{\"ok\":true,\"command\":\"respond_to_probe_incident\",\"response\":")
            + json_string(response.c_str()) + ",\"offender_faction_id\":"
            + std::to_string(offender) + '}';
    }
    if (command == "set_first_base_name") {
        if (!first_base_name_modal(faction_id)) {
            return error_response("not_first_base_name", "The first-base naming interaction is not active.");
        }
        std::string name = field_string(request, "name");
        if (name.empty() || name.size() >= sizeof(BASE::name)
        || name.find_first_of("\r\n\t") != std::string::npos) {
            return error_response("invalid_base_name", "Base names must contain 1 through 24 characters and no control whitespace.");
        }
        int base_id = first_owned_base(faction_id);
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable", "The base-name popup object is unavailable.");
        // Close through the dialog's native OK handler, then apply the
        // semantic value.  This preserves the normal dialog lifecycle while
        // avoiding simulated editing or keyboard input.
        begin_popup_transition(active);
        BasePop_on_button_clicked(active, 0);
        pending_base_name_id = base_id;
        pending_base_name = name;
        return std::string("{\"ok\":true,\"command\":\"set_first_base_name\",\"base_id\":")
            + std::to_string(base_id) + ",\"name\":" + json_string(name.c_str())
            + ",\"applies_after_modal_close\":true}";
    }
    if (command == "choose_research_priority") {
        int priority = field_int(request, "priority", -1);
        const bool initial_picker = !agent_popup_label()[0]
            && Factions[faction_id].tech_research_id < 0;
        const bool focus_picker = !strcmp(agent_popup_label(), "TECHRANDOM");
        if (!(*GameRules & RULES_BLIND_RESEARCH)
        || (!initial_picker && !focus_picker)
        || priority < TCAT_GROWTH || priority > TCAT_POWER) {
            return error_response("invalid_research_priority", "Choose a returned blind-research priority from 0 through 3.");
        }
        BasePop* active = active_default_popup();
        if (!active) return error_response("popup_unavailable", "The research-priority popup object is unavailable.");
        const char* names[] = {"Explore", "Discover", "Build", "Conquer"};
        if (focus_picker) {
            // TECHRANDOM is the engine's checkbox-backed research-focus
            // dialog. The caller reads DialogChoices after its native modal
            // loop returns, writes all four AI_* focus flags, then sends
            // synch_ai (NetDaemon command 8). Set one exact semantic bit and
            // complete through native OK; never synthesize list coordinates
            // or mutate the Faction fields ahead of the native caller.
            *DialogChoices = 1 << priority;
            pending_research_focus_faction_id = faction_id;
            pending_research_focus_priority = priority;
            begin_popup_transition(active);
            BasePop_on_button_clicked(active, 0);
            // BasePop's checkbox OK callback copies the visual control state
            // into DialogChoices. Restore the semantic one-area mask after
            // that callback but before this Windows-message handler returns;
            // only then can X_pop's suspended native caller resume and read
            // the final value before sending synch_ai.
            *DialogChoices = 1 << priority;
        } else {
            submit_popup_choice(active, priority);
        }
        agent_mp_last_research_faction = faction_id;
        agent_mp_last_research_priority = priority;
        return std::string("{\"ok\":true,\"command\":\"choose_research_priority\",\"priority\":")
            + std::to_string(priority) + ",\"name\":" + json_string(names[priority])
            + (focus_picker
                ? ",\"native_sync\":\"synch_ai_after_modal_return\"}"
                : "}");
    }
    if (!human_turn_actionable(faction_id)) return semantic_not_actionable();
    if (command == "create_unit_design") {
        if (*MultiplayerActive) {
            return error_response("multiplayer_unit_design_not_supported",
                "Semantic Unit Workshop mutations are not network-synchronized yet.");
        }
        int chassis_id = field_int(request, "chassis_id", -1);
        int weapon_id = field_int(request, "weapon_id", -1);
        int armor_id = field_int(request, "armor_id", -1);
        int reactor_id = field_int(request, "reactor_id", -1);
        int ability_id_1 = field_int(request, "ability_id_1", -1);
        int ability_id_2 = field_int(request, "ability_id_2", -1);
        uint32_t ability_flags = 0;
        std::string invalid_reason;
        if (!valid_design_components(faction_id, chassis_id, weapon_id, armor_id,
            reactor_id, ability_id_1, ability_id_2, ability_flags, invalid_reason)) {
            return error_response("invalid_unit_design", invalid_reason.c_str());
        }
        std::string requested_name = field_string(request, "name");
        if (requested_name.size() >= MaxProtoNameLen
        || requested_name.find_first_of("\r\n\t") != std::string::npos) {
            return error_response("invalid_unit_design_name",
                "A custom design name must contain at most 31 characters and no control whitespace; omit it for a native generated name.");
        }
        for (int unit_id = 0; unit_id < MaxProtoNum; ++unit_id) {
            UNIT& unit = Units[unit_id];
            if (!prototype_available_to_faction(faction_id, unit_id)) continue;
            if (unit.chassis_id == chassis_id && unit.weapon_id == weapon_id
            && unit.armor_id == armor_id && unit.reactor_id == reactor_id
            && unit.ability_flags == ability_flags) {
                return std::string("{\"ok\":false,\"error\":{\"code\":\"duplicate_unit_design\","
                    "\"message\":\"An identical currently available prototype already exists.\"},"
                    "\"prototype_id\":") + std::to_string(unit_id)
                    + ",\"name\":" + json_string(unit.name) + '}';
            }
        }
        char name[MaxProtoNameLen] = {};
        if (requested_name.empty()) {
            mod_name_proto(name, -1, faction_id,
                static_cast<VehChassis>(chassis_id), static_cast<VehWeapon>(weapon_id),
                static_cast<VehArmor>(armor_id), static_cast<VehAblFlag>(ability_flags),
                static_cast<VehReactor>(reactor_id));
        } else {
            strcpy_n(name, sizeof(name), requested_name.c_str());
        }
        int prototype_id = propose_proto(faction_id,
            chassis_id, weapon_id, armor_id, ability_flags, reactor_id,
            PLAN_AUTO_CALCULATE, name[0] ? name : NULL);
        if (!owned_custom_prototype(faction_id, prototype_id)
        || !Units[prototype_id].is_active()) {
            return error_response("native_unit_design_rejected",
                "The native prototype engine rejected this otherwise unlocked component combination.");
        }
        Console_update_data(MapWin, 0);
        std::ostringstream out;
        out << "{\"ok\":true,\"command\":\"create_unit_design\",\"prototype\":";
        append_prototype_summary(out, faction_id, prototype_id);
        out << '}';
        return out.str();
    }
    if (command == "retire_unit_design") {
        if (*MultiplayerActive) {
            return error_response("multiplayer_unit_design_not_supported",
                "Semantic Unit Workshop mutations are not network-synchronized yet.");
        }
        int prototype_id = field_int(request, "prototype_id", -1);
        if (!owned_custom_prototype(faction_id, prototype_id)
        || !Units[prototype_id].is_active()) {
            return error_response("invalid_owned_prototype",
                "prototype_id must identify an active custom design owned by the human faction.");
        }
        int active_units = veh_count(faction_id, prototype_id);
        int queue_references = prototype_queue_references(faction_id, prototype_id);
        if (active_units || queue_references) {
            return std::string("{\"ok\":false,\"error\":{\"code\":\"prototype_in_use\","
                "\"message\":\"A design can be retired semantically only when no owned units or production queues still reference it.\"},"
                "\"active_unit_count\":") + std::to_string(active_units)
                + ",\"production_queue_references\":" + std::to_string(queue_references) + '}';
        }
        if (field_int(request, "confirm_retire", 0) != 1) {
            return error_response("retire_confirmation_required",
                "Set confirm_retire to 1 after inspecting the fresh unit_design choice record.");
        }
        std::string name = Units[prototype_id].name;
        retire_proto(prototype_id, faction_id);
        Console_update_data(MapWin, 0);
        return std::string("{\"ok\":true,\"command\":\"retire_unit_design\",\"prototype_id\":")
            + std::to_string(prototype_id) + ",\"name\":" + json_string(name.c_str())
            + ",\"active\":" + (Units[prototype_id].is_active() ? "true" : "false") + '}';
    }
    if (command == "upgrade_prototype") {
        if (*MultiplayerActive) {
            return error_response("multiplayer_unit_upgrade_not_supported",
                "Semantic prototype upgrades are not network-synchronized yet.");
        }
        int source_id = field_int(request, "source_prototype_id", -1);
        int target_id = field_int(request, "target_prototype_id", -1);
        if (!prototype_available_to_faction(faction_id, source_id)) {
            return error_response("invalid_upgrade_source",
                "source_prototype_id must identify a currently available prototype owned or unlocked by the faction.");
        }
        if (!owned_custom_prototype(faction_id, target_id)
        || !Units[target_id].is_active() || source_id == target_id) {
            return error_response("invalid_upgrade_target",
                "target_prototype_id must identify a different active custom design owned by the human faction.");
        }
        UNIT& source = Units[source_id];
        UNIT& target = Units[target_id];
        uint32_t lost_abilities = source.ability_flags & ~target.ability_flags & ~ABL_SLOW;
        bool legal_path = source.chassis_id == target.chassis_id
            && source.plan <= PLAN_RECON && target.plan <= PLAN_RECON
            && source.offense_value() > 0 && source.defense_value() > 0
            && target.offense_value() >= source.offense_value()
            && target.defense_value() >= source.defense_value()
            && !lost_abilities
            && ((source.ability_flags & ABL_ARTILLERY)
                == (target.ability_flags & ABL_ARTILLERY));
        if (!legal_path) {
            return error_response("illegal_upgrade_path",
                "Semantic bulk upgrade is limited to same-chassis combat designs that do not reduce offense, defense, abilities, or artillery role.");
        }
        int active_units = veh_count(faction_id, source_id);
        int queue_references = prototype_queue_references(faction_id, source_id);
        if (!active_units && !queue_references) {
            return error_response("upgrade_source_unused",
                "No owned unit or production queue currently references the source prototype.");
        }
        int per_unit_cost = 10 * mod_upgrade_cost(faction_id, target_id, source_id);
        int total_cost = per_unit_cost * active_units;
        if (total_cost > Factions[faction_id].energy_credits) {
            return std::string("{\"ok\":false,\"error\":{\"code\":\"upgrade_unaffordable\","
                "\"message\":\"The faction lacks the energy credits for this all-units upgrade.\"},"
                "\"energy_cost\":") + std::to_string(total_cost)
                + ",\"energy_credits\":" + std::to_string(Factions[faction_id].energy_credits) + '}';
        }
        if (field_int(request, "confirm_upgrade", 0) != 1) {
            return std::string("{\"ok\":false,\"error\":{\"code\":\"upgrade_confirmation_required\","
                "\"message\":\"Set confirm_upgrade to 1 after inspecting this cost and fresh unit_design state.\"},"
                "\"active_unit_count\":") + std::to_string(active_units)
                + ",\"production_queue_references\":" + std::to_string(queue_references)
                + ",\"energy_cost_per_unit\":" + std::to_string(per_unit_cost)
                + ",\"energy_cost_total\":" + std::to_string(total_cost) + '}';
        }
        int energy_before = Factions[faction_id].energy_credits;
        std::string source_name = source.name;
        std::string target_name = target.name;
        full_upgrade(faction_id, target_id, source_id);
        return std::string("{\"ok\":true,\"command\":\"upgrade_prototype\",\"source_prototype_id\":")
            + std::to_string(source_id) + ",\"source_name\":" + json_string(source_name.c_str())
            + ",\"target_prototype_id\":" + std::to_string(target_id)
            + ",\"target_name\":" + json_string(target_name.c_str())
            + ",\"units_upgraded\":" + std::to_string(active_units)
            + ",\"queue_references_updated\":" + std::to_string(queue_references)
            + ",\"energy_spent\":" + std::to_string(energy_before - Factions[faction_id].energy_credits)
            + '}';
    }
    if (command == "convene_council") {
        if (*GameState & STATE_GAME_DONE || !can_call_council(faction_id, 0)) {
            return error_response("council_unavailable",
                "The native game reports that this faction cannot convene the Council now.");
        }
        if (deferred_native_action_pending() || deferred_end_turn_faction_id >= 0) {
            return error_response("blocking_action_already_queued",
                "A potentially blocking native action is already queued. Wait and observe.");
        }
        deferred_council_faction_id = faction_id;
        begin_deferred_action("convene_council");
        if (!PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0)) {
            deferred_council_faction_id = -1;
            deferred_action.status = "rejected";
            return error_response("council_queue_failed", "The game could not queue the native Council command.");
        }
        return std::string("{\"ok\":true,\"command\":\"convene_council\",\"queued\":true,\"action_id\":")
            + std::to_string(deferred_action.id) + '}';
    }
    if (command == "open_diplomacy") {
        int other = field_int(request, "faction_id", -1);
        if (other < 1 || other >= MaxPlayerNum || other == faction_id
        || !is_alive(other) || !has_treaty(faction_id, other, DIPLO_COMMLINK)) {
            return error_response("unavailable_diplomacy_target",
                "Choose a living contacted faction returned by diplomacy choices.");
        }
        if (deferred_native_action_pending() || deferred_end_turn_faction_id >= 0) {
            return error_response("diplomacy_already_queued",
                "A potentially blocking native action is already queued. Wait and observe.");
        }
        deferred_diplomacy_faction_id = other;
        begin_deferred_action("open_diplomacy");
        if (!PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0)) {
            deferred_diplomacy_faction_id = -1;
            deferred_action.status = "rejected";
            return error_response("diplomacy_queue_failed", "The game could not queue the native diplomatic channel.");
        }
        return std::string("{\"ok\":true,\"command\":\"open_diplomacy\",\"queued\":true,\"faction_id\":")
            + std::to_string(other) + ",\"faction_name\":"
            + json_string(MFactions[other].formal_name_faction)
            + ",\"action_id\":" + std::to_string(deferred_action.id) + '}';
    }
    if (command == "set_research_priority") {
        int priority = field_int(request, "priority", -1);
        if (!(*GameRules & RULES_BLIND_RESEARCH)
        || priority < TCAT_GROWTH || priority > TCAT_POWER) {
            return error_response("invalid_research_priority", "Choose a returned blind-research priority from 0 through 3.");
        }
        Faction& faction = Factions[faction_id];
        faction.AI_growth = priority == TCAT_GROWTH;
        faction.AI_tech = priority == TCAT_TECH;
        faction.AI_wealth = priority == TCAT_WEALTH;
        faction.AI_power = priority == TCAT_POWER;
        if (*MultiplayerActive) synch_ai(faction_id);
        const char* names[] = {"Explore", "Discover", "Build", "Conquer"};
        return std::string("{\"ok\":true,\"command\":\"set_research_priority\",\"priority\":")
            + std::to_string(priority) + ",\"name\":" + json_string(names[priority]) + '}';
    }
    if (command == "set_energy_allocation") {
        int economy = field_int(request, "economy", -1);
        int psych = field_int(request, "psych", -1);
        int labs = field_int(request, "labs", -1);
        if (economy < 0 || economy > 10 || psych < 0 || psych > 10
        || labs < 0 || labs > 10 || economy + psych + labs != 10) {
            return error_response("invalid_energy_allocation",
                "economy, psych, and labs must each be 0 through 10 and sum exactly to 10.");
        }
        Faction& faction = Factions[faction_id];
        faction.SE_alloc_psych = psych;
        faction.SE_alloc_labs = labs;
        // This is the same native allocation synchronization path used by the
        // game. Recompute only the player's bases so the structured snapshot
        // immediately reflects the consequences without advancing time.
        synch_alloc(faction_id);
        for (int base_id = 0; base_id < *BaseCount; ++base_id) {
            if (Bases[base_id].faction_id != faction_id) continue;
            set_base(base_id);
            base_compute(1);
        }
        return std::string("{\"ok\":true,\"command\":\"set_energy_allocation\",\"allocation\":{\"economy\":")
            + std::to_string(economy) + ",\"psych\":" + std::to_string(psych)
            + ",\"labs\":" + std::to_string(labs) + "}}";
    }
    if (command == "set_social_engineering") {
        if (*GameMoreRules & MRULES_NO_SOCIAL_ENGINEERING) {
            return error_response("social_engineering_disabled",
                "Social Engineering is disabled by this scenario's normal rules.");
        }
        CSocialCategory desired;
        desired.models[SOCIAL_C_POLITICS] = field_int(request, "politics", -1);
        desired.models[SOCIAL_C_ECONOMICS] = field_int(request, "economics", -1);
        desired.models[SOCIAL_C_VALUES] = field_int(request, "values", -1);
        desired.models[SOCIAL_C_FUTURE] = field_int(request, "future", -1);
        for (int category = 0; category < MaxSocialCatNum; ++category) {
            int model = desired.models[category];
            if (model < 0 || model >= MaxSocialModelNum
            || !society_avail(category, model, faction_id)) {
                return error_response("unavailable_social_model",
                    "Every policy model must be an available option returned by social_engineering choices.");
            }
        }
        Faction& faction = Factions[faction_id];
        int target_total_cost = social_upheaval(faction_id, &desired);
        int cost_delta = target_total_cost - faction.SE_upheaval_cost_paid;
        if (cost_delta > faction.energy_credits) {
            std::ostringstream out;
            out << "{\"ok\":false,\"error\":{\"code\":\"insufficient_energy\","
                << "\"message\":\"The selected policies cost more energy than the faction currently has.\"},"
                << "\"required_energy\":" << cost_delta << ",\"available_energy\":"
                << faction.energy_credits << '}';
            return out.str();
        }
        memcpy(&faction.SE_Politics_pending, &desired, sizeof(desired));
        faction.SE_upheaval_cost_paid += cost_delta;
        // Mirror the native Social Engineering dialog's commit path. These
        // synchronization calls apply the pending policy through normal game
        // rules (and propagate it in multiplayer); net_energy charges or
        // refunds only the legal upheaval delta.
        synch_alloc(faction_id);
        synch_soc(faction_id);
        if (cost_delta) net_energy(faction_id, -cost_delta, 0, 0, 0);
        CSocialEffect effective;
        social_calc(&desired, &effective, faction_id, false, false);
        std::ostringstream out;
        out << "{\"ok\":true,\"command\":\"set_social_engineering\",\"selected\":";
        append_social_models(out, desired);
        out << ",\"effective_ratings\":";
        append_social_effects(out, effective);
        out << ",\"target_total_cost\":" << target_total_cost
            << ",\"energy_delta\":" << -cost_delta
            << ",\"energy_credits\":" << faction.energy_credits << '}';
        return out.str();
    }
    if (command == "end_turn") {
        int ready_units = 0;
        for (int i = 0; i < *VehCount; ++i) {
            if (Vehs[i].faction_id == faction_id && semantic_unit_requires_decision(i)) ++ready_units;
        }
        if (ready_units) {
            return error_response("units_still_ready", "Skip, move, order, or otherwise resolve all ready units before ending the turn.");
        }
        if (deferred_native_action_pending() || deferred_end_turn_faction_id >= 0) {
            return error_response("action_already_queued",
                "A potentially blocking native action is already queued. Wait and observe.");
        }
        deferred_end_turn_faction_id = faction_id;
        deferred_end_turn_source_turn = *CurrentTurn;
        begin_deferred_action("end_turn");
        deferred_end_turn_timer_id = SetTimer(
            NULL, 0, 1, deferred_end_turn_timer_proc);
        if (!deferred_end_turn_timer_id) {
            deferred_end_turn_faction_id = -1;
            deferred_end_turn_source_turn = -1;
            deferred_action.status = "rejected";
            return error_response("end_turn_queue_failed",
                "The game could not queue the native turn transition.");
        }
        return std::string("{\"ok\":true,\"command\":\"end_turn\",\"queued\":true,"
            "\"source_turn\":") + std::to_string(deferred_end_turn_source_turn)
            + ",\"action_id\":" + std::to_string(deferred_action.id) + '}';
    }
    if (command == "choose_research") {
        int tech_id = field_int(request, "tech_id", -1);
        if (*GameRules & RULES_BLIND_RESEARCH) {
            return error_response("blind_research", "This game uses blind research; the player cannot choose an exact technology.");
        }
        if (tech_id < 0 || tech_id >= MaxTechnologyNum || !tech_avail(tech_id, faction_id)) {
            return error_response("invalid_research", "tech_id is not currently researchable.");
        }
        Faction& faction = Factions[faction_id];
        faction.tech_research_id = tech_id;
        faction.tech_cost = (conf.revised_tech_cost && !*MultiplayerActive)
            ? tech_alt_cost(tech_id, faction_id) : -1;
        return std::string("{\"ok\":true,\"command\":\"choose_research\",\"tech_id\":")
            + std::to_string(tech_id) + ",\"name\":" + json_string(Tech[tech_id].name) + '}';
    }
    if (command == "set_production") {
        int base_id = field_int(request, "base_id", -1);
        int item_id = field_int(request, "item_id", 99999);
        if (base_id < 0 || base_id >= *BaseCount || Bases[base_id].faction_id != faction_id) {
            return error_response("invalid_base", "base_id must identify a base owned by the human faction.");
        }
        set_base(base_id);
        base_compute(1);
        bool buildable = production_item_buildable(faction_id, base_id, item_id);
        if (!buildable) return error_response("invalid_production", "item_id is not currently buildable at this base.");
        mod_base_change(base_id, item_id);
        if (*MultiplayerActive) {
            // The base packet carries production and queue contents. The
            // leader packet carries the faction's derived units_queue counts;
            // stock full-state synchronization sends these in the same order.
            synch_base(base_id);
            synch_leader(faction_id);
        }
        set_base(base_id);
        base_compute(1);
        return std::string("{\"ok\":true,\"command\":\"set_production\",\"base_id\":")
            + std::to_string(base_id) + ",\"item_id\":" + std::to_string(item_id)
            + ",\"name\":" + json_string(production_name(item_id).c_str()) + '}';
    }
    if (command == "hurry_production") {
        int base_id = field_int(request, "base_id", -1);
        if (*MultiplayerActive) {
            return error_response("multiplayer_hurry_not_supported",
                "Production rushing is not semantic in multiplayer yet because it requires network synchronization.");
        }
        if (base_id < 0 || base_id >= *BaseCount || Bases[base_id].faction_id != faction_id) {
            return error_response("invalid_base", "base_id must identify a base owned by the human faction.");
        }
        set_base(base_id);
        base_compute(1);
        BASE& base = Bases[base_id];
        int item_cost = mineral_cost(base_id, base.queue_items[0]);
        int minerals = max(0, item_cost - base.minerals_accumulated);
        int cost = hurry_cost(base_id, base.queue_items[0], minerals);
        int available = max(0, Factions[faction_id].energy_credits
            - Factions[faction_id].hurry_cost_total);
        if (!base.can_hurry_item() || minerals <= 0 || cost <= 0) {
            return error_response("cannot_hurry_production",
                "Current production cannot be hurried under the native base rules.");
        }
        if (cost > available) {
            std::ostringstream out;
            out << "{\"ok\":false,\"error\":{\"code\":\"insufficient_energy\","
                << "\"message\":\"The faction cannot afford the current native hurry cost.\"},"
                << "\"required_energy\":" << cost << ",\"available_energy\":" << available << '}';
            return out.str();
        }
        Factions[faction_id].energy_credits -= cost;
        base.minerals_accumulated += minerals;
        base.state_flags |= BSTATE_HURRY_PRODUCTION;
        return std::string("{\"ok\":true,\"command\":\"hurry_production\",\"base_id\":")
            + std::to_string(base_id) + ",\"energy_cost\":" + std::to_string(cost)
            + ",\"minerals_added\":" + std::to_string(minerals)
            + ",\"energy_credits\":" + std::to_string(Factions[faction_id].energy_credits) + '}';
    }
    if (command == "nerve_staple") {
        int base_id = field_int(request, "base_id", -1);
        if (field_int(request, "confirm_atrocity", 0) != 1) {
            return error_response("atrocity_confirmation_required",
                "Nerve stapling is an atrocity. Copy confirm_atrocity=1 from the fresh base-management choice only after accepting its consequences.");
        }
        if (*MultiplayerActive) {
            return error_response("multiplayer_nerve_stapling_not_supported",
                "Semantic nerve stapling is not network-synchronized yet.");
        }
        if (base_id < 0 || base_id >= *BaseCount
        || Bases[base_id].faction_id != faction_id) {
            return error_response("invalid_base",
                "base_id must identify a base owned by the human faction.");
        }
        set_base(base_id);
        base_compute(1);
        if (!can_staple(base_id) || Bases[base_id].nerve_staple_turns_left) {
            return error_response("nerve_stapling_unavailable",
                "Use a fresh nerve_staple choice; Police must permit it and no prior stapling may still be active.");
        }
        if (deferred_native_action_pending()) {
            return error_response("action_already_queued",
                "A potentially blocking native action is already queued. Wait and observe.");
        }
        deferred_nerve_staple_base_id = base_id;
        begin_deferred_action("nerve_staple");
        if (!PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0)) {
            deferred_nerve_staple_base_id = -1;
            deferred_action.status = "rejected";
            return error_response("nerve_stapling_queue_failed",
                "The game could not queue the native nerve-stapling action.");
        }
        return std::string("{\"ok\":true,\"command\":\"nerve_staple\",\"queued\":true,\"base_id\":")
            + std::to_string(base_id) + ",\"action_id\":"
            + std::to_string(deferred_action.id) + '}';
    }
    if (command == "obliterate_base") {
        int base_id = field_int(request, "base_id", -1);
        int unit_id = field_int(request, "unit_id", -1);
        if (*MultiplayerActive) {
            return error_response("multiplayer_base_obliteration_not_supported",
                "Semantic base obliteration is not network-synchronized yet.");
        }
        if (base_id < 0 || base_id >= *BaseCount
        || Bases[base_id].faction_id != faction_id) {
            return error_response("invalid_base",
                "base_id must identify a base currently owned by the human faction.");
        }
        if (is_objective(base_id)) {
            return error_response("objective_base_cannot_be_obliterated",
                "The native scenario rules forbid obliterating an objective base.");
        }
        BASE& target = Bases[base_id];
        if (unit_id < 0 || unit_id >= *VehCount
        || Vehs[unit_id].faction_id != faction_id
        || Vehs[unit_id].x != target.x || Vehs[unit_id].y != target.y) {
            return error_response("obliteration_unit_unavailable",
                "Use the exact owned unit standing inside the base from a fresh base-management choice.");
        }
        if (deferred_native_action_pending()) {
            return error_response("action_already_queued",
                "A potentially blocking native action is already queued. Wait and observe.");
        }
        deferred_obliterate_base_id = base_id;
        deferred_obliterate_unit_id = unit_id;
        begin_deferred_action("obliterate_base", unit_id,
            target.x, target.y, target.x, target.y);
        if (!PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0)) {
            deferred_obliterate_base_id = -1;
            deferred_obliterate_unit_id = -1;
            deferred_action.status = "rejected";
            return error_response("base_obliteration_queue_failed",
                "The game could not queue the native base-obliteration action.");
        }
        return std::string("{\"ok\":true,\"command\":\"obliterate_base\",\"queued\":true,\"base_id\":")
            + std::to_string(base_id) + ",\"unit_id\":" + std::to_string(unit_id)
            + ",\"confirmation_follows\":true,\"action_id\":"
            + std::to_string(deferred_action.id) + '}';
    }
    if (command == "recycle_facility") {
        int base_id = field_int(request, "base_id", -1);
        int facility_id = field_int(request, "facility_id", -1);
        if (*MultiplayerActive) {
            return error_response("multiplayer_facility_recycling_not_supported",
                "Semantic facility recycling is not network-synchronized yet.");
        }
        if (base_id < 0 || base_id >= *BaseCount
        || Bases[base_id].faction_id != faction_id) {
            return error_response("invalid_base",
                "base_id must identify a base owned by the human faction.");
        }
        set_base(base_id);
        base_compute(1);
        if (!facility_recyclable_at_base(faction_id, base_id, facility_id)) {
            return error_response("facility_recycling_unavailable",
                "Use an exact recycle_facility tuple from fresh base-management choices; Headquarters, sea Pressure Domes, faction-granted facilities, absent facilities, and a second recycle this turn are forbidden.");
        }
        int refund = facility_recycle_refund(Bases[base_id], facility_id);
        if (field_int(request, "confirm_recycle", 0) != 1) {
            std::ostringstream out;
            out << "{\"ok\":false,\"error\":{\"code\":\"recycle_confirmation_required\"," 
                << "\"message\":\"Set confirm_recycle=1 only after inspecting this destructive fresh choice.\"},"
                << "\"base_id\":" << base_id << ",\"facility_id\":" << facility_id
                << ",\"facility_name\":" << json_string(Facility[facility_id].name)
                << ",\"energy_refund\":" << refund << '}';
            return out.str();
        }
        std::string facility_name = Facility[facility_id].name;
        Bases[base_id].state_flags |= BSTATE_FACILITY_SCRAPPED;
        set_fac(static_cast<FacilityId>(facility_id), base_id, false);
        Factions[faction_id].energy_credits += refund;
        set_base(base_id);
        base_compute(1);
        draw_tile(Bases[base_id].x, Bases[base_id].y, 2);
        return std::string("{\"ok\":true,\"command\":\"recycle_facility\",\"base_id\":")
            + std::to_string(base_id) + ",\"facility_id\":"
            + std::to_string(facility_id) + ",\"facility_name\":"
            + json_string(facility_name.c_str()) + ",\"energy_refund\":"
            + std::to_string(refund) + ",\"energy_credits\":"
            + std::to_string(Factions[faction_id].energy_credits)
            + ",\"facility_recycled_this_turn\":true}";
    }
    if (command == "rename_base") {
        int base_id = field_int(request, "base_id", -1);
        std::string name = field_string(request, "name");
        if (*MultiplayerActive) {
            return error_response("multiplayer_base_rename_not_supported",
                "Semantic base renaming is not network-synchronized yet.");
        }
        if (base_id < 0 || base_id >= *BaseCount || Bases[base_id].faction_id != faction_id) {
            return error_response("invalid_base", "base_id must identify a base owned by the human faction.");
        }
        if (name.empty() || name.size() >= sizeof(BASE::name)
        || name.find_first_of("\r\n\t") != std::string::npos) {
            return error_response("invalid_base_name",
                "Base names must contain 1 through 24 characters and no control whitespace.");
        }
        strcpy_n(Bases[base_id].name, sizeof(BASE::name), name.c_str());
        draw_tile(Bases[base_id].x, Bases[base_id].y, 2);
        return std::string("{\"ok\":true,\"command\":\"rename_base\",\"base_id\":")
            + std::to_string(base_id) + ",\"name\":" + json_string(name.c_str()) + '}';
    }
    if (command == "set_base_governor") {
        int base_id = field_int(request, "base_id", -1);
        int active = field_int(request, "active", -1);
        int manage_citizens = field_int(request, "manage_citizens", -1);
        int manage_production = field_int(request, "manage_production", -1);
        int new_units_automated = field_int(request, "new_units_automated", -1);
        int priority_explore = field_int(request, "priority_explore", -1);
        int priority_discover = field_int(request, "priority_discover", -1);
        int priority_build = field_int(request, "priority_build", -1);
        int priority_conquer = field_int(request, "priority_conquer", -1);
        if (base_id < 0 || base_id >= *BaseCount || Bases[base_id].faction_id != faction_id) {
            return error_response("invalid_base", "base_id must identify a base owned by the human faction.");
        }
        if ((active != 0 && active != 1) || (manage_citizens != 0 && manage_citizens != 1)
        || (manage_production != 0 && manage_production != 1)) {
            return error_response("invalid_governor_settings",
                "active, manage_citizens, and manage_production must each be 0 or 1.");
        }
        const int optional_values[] = {new_units_automated, priority_explore,
            priority_discover, priority_build, priority_conquer};
        for (size_t index = 0; index < sizeof(optional_values) / sizeof(optional_values[0]); ++index) {
            if (optional_values[index] < -1 || optional_values[index] > 1) {
                return error_response("invalid_governor_settings",
                    "Optional governor controls must be omitted/-1, 0, or 1.");
            }
        }
        BASE& base = Bases[base_id];
        uint32_t old_flags = base.governor_flags;
        const uint32_t primary = GOV_ACTIVE | GOV_MANAGE_CITIZENS | GOV_MANAGE_PRODUCTION;
        uint32_t new_flags = old_flags & ~primary;
        if (active) new_flags |= GOV_ACTIVE;
        if (manage_citizens) new_flags |= GOV_MANAGE_CITIZENS;
        if (manage_production) new_flags |= GOV_MANAGE_PRODUCTION;
        const uint32_t optional_masks[] = {GOV_NEW_VEH_FULLY_AUTO, GOV_PRIORITY_EXPLORE,
            GOV_PRIORITY_DISCOVER, GOV_PRIORITY_BUILD, GOV_PRIORITY_CONQUER};
        for (size_t index = 0; index < sizeof(optional_values) / sizeof(optional_values[0]); ++index) {
            if (optional_values[index] < 0) continue;
            new_flags &= ~optional_masks[index];
            if (optional_values[index]) new_flags |= optional_masks[index];
        }
        base.governor_flags = new_flags;
        Factions[faction_id].base_governor_adv = new_flags;
        bool production_newly_managed = (new_flags & (GOV_ACTIVE | GOV_MANAGE_PRODUCTION))
            == (GOV_ACTIVE | GOV_MANAGE_PRODUCTION)
            && (old_flags & (GOV_ACTIVE | GOV_MANAGE_PRODUCTION))
                != (GOV_ACTIVE | GOV_MANAGE_PRODUCTION);
        bool citizens_newly_managed = (new_flags & (GOV_ACTIVE | GOV_MANAGE_CITIZENS))
            == (GOV_ACTIVE | GOV_MANAGE_CITIZENS)
            && (old_flags & (GOV_ACTIVE | GOV_MANAGE_CITIZENS))
                != (GOV_ACTIVE | GOV_MANAGE_CITIZENS);
        if (production_newly_managed) {
            base.state_flags &= ~BSTATE_UNK_80000000;
            for (int position = 1; position <= base.queue_size && position < 10; ++position) {
                int queued_item = base.queue_items[position];
                if (queued_item >= 0 && queued_item < MaxProtoNum) {
                    Factions[faction_id].units_queue[queued_item]--;
                }
            }
            base.queue_size = 0;
            mod_base_reset(base_id, 1);
        }
        if (citizens_newly_managed) {
            base.worked_tiles = 0;
            base.specialist_total = 0;
            base.specialist_adjust = 0;
        }
        set_base(base_id);
        base_compute(1);
        if (*MultiplayerActive) {
            synch_base(base_id);
            synch_leader(faction_id);
        }
        return std::string("{\"ok\":true,\"command\":\"set_base_governor\",\"base_id\":")
            + std::to_string(base_id) + ",\"changed\":" + (old_flags != new_flags ? "true" : "false")
            + ",\"governor\":{\"active\":" + (active ? "true" : "false")
            + ",\"manage_citizens\":" + (manage_citizens ? "true" : "false")
            + ",\"manage_production\":" + (manage_production ? "true" : "false") + "}}";
    }
    if (command == "set_governor_permission") {
        int base_id = field_int(request, "base_id", -1);
        int active = field_int(request, "active", -1);
        std::string key = field_string(request, "governor_permission");
        const GovernorPermissionSpec* permission = governor_permission(key);
        if (base_id < 0 || base_id >= *BaseCount
        || Bases[base_id].faction_id != faction_id) {
            return error_response("invalid_base",
                "base_id must identify a base owned by the human faction.");
        }
        if (!permission || (active != 0 && active != 1)) {
            return error_response("invalid_governor_permission",
                "Use an exact named governor permission and opposite 0/1 state from fresh base-management choices.");
        }
        BASE& base = Bases[base_id];
        bool old_active = base.governor_flags & permission->mask;
        if (old_active == (active != 0)) {
            return error_response("governor_permission_unchanged",
                "The requested permission already has that state; use the opposite fresh toggle.");
        }
        if (active) base.governor_flags |= permission->mask;
        else base.governor_flags &= ~permission->mask;
        Factions[faction_id].base_governor_adv = base.governor_flags;
        bool recalculate = (base.governor_flags & (GOV_ACTIVE | GOV_MANAGE_PRODUCTION))
            == (GOV_ACTIVE | GOV_MANAGE_PRODUCTION);
        if (recalculate) {
            base.state_flags &= ~BSTATE_UNK_80000000;
            for (int position = 1; position <= base.queue_size && position < 10; ++position) {
                int queued_item = base.queue_items[position];
                if (queued_item >= 0 && queued_item < MaxProtoNum) {
                    Factions[faction_id].units_queue[queued_item]--;
                }
            }
            base.queue_size = 0;
            mod_base_reset(base_id, 1);
        }
        set_base(base_id);
        base_compute(1);
        if (*MultiplayerActive) {
            synch_base(base_id);
            synch_leader(faction_id);
        }
        return std::string("{\"ok\":true,\"command\":\"set_governor_permission\",\"base_id\":")
            + std::to_string(base_id) + ",\"governor_permission\":"
            + json_string(key.c_str()) + ",\"active\":" + (active ? "true" : "false")
            + ",\"production_recalculated\":" + (recalculate ? "true" : "false")
            + ",\"affects_future_new_bases\":true}";
    }
    if (command == "queue_production") {
        int base_id = field_int(request, "base_id", -1);
        int item_id = field_int(request, "item_id", 99999);
        if (base_id < 0 || base_id >= *BaseCount || Bases[base_id].faction_id != faction_id) {
            return error_response("invalid_base", "base_id must identify a base owned by the human faction.");
        }
        BASE& base = Bases[base_id];
        if (base.queue_size >= 9) {
            return error_response("production_queue_full", "The native production queue already contains ten entries.");
        }
        set_base(base_id);
        base_compute(1);
        if (!production_item_buildable(faction_id, base_id, item_id, base.queue_size + 1)) {
            return error_response("invalid_queued_production",
                "item_id is not currently legal to append to this base's production queue.");
        }
        ++base.queue_size;
        base.queue_items[base.queue_size] = item_id;
        if (item_id >= 0 && item_id < MaxProtoNum) Factions[faction_id].units_queue[item_id]++;
        if (*MultiplayerActive) {
            synch_base(base_id);
            synch_leader(faction_id);
        }
        return std::string("{\"ok\":true,\"command\":\"queue_production\",\"base_id\":")
            + std::to_string(base_id) + ",\"queue_position\":" + std::to_string(base.queue_size)
            + ",\"item_id\":" + std::to_string(item_id) + ",\"name\":"
            + json_string(production_name(item_id).c_str()) + '}';
    }
    if (command == "remove_queued_production") {
        int base_id = field_int(request, "base_id", -1);
        int position = field_int(request, "queue_position", -1);
        if (base_id < 0 || base_id >= *BaseCount || Bases[base_id].faction_id != faction_id) {
            return error_response("invalid_base", "base_id must identify a base owned by the human faction.");
        }
        BASE& base = Bases[base_id];
        if (position < 1 || position > base.queue_size || position >= 10) {
            return error_response("invalid_queue_position",
                "queue_position must identify a future entry returned by base_management choices; current production is position 0.");
        }
        int removed = base.queue_items[position];
        if (removed >= 0 && removed < MaxProtoNum) Factions[faction_id].units_queue[removed]--;
        for (int index = position; index < base.queue_size; ++index) {
            base.queue_items[index] = base.queue_items[index + 1];
        }
        base.queue_items[base.queue_size] = 0;
        --base.queue_size;
        if (*MultiplayerActive) {
            synch_base(base_id);
            synch_leader(faction_id);
        }
        return std::string("{\"ok\":true,\"command\":\"remove_queued_production\",\"base_id\":")
            + std::to_string(base_id) + ",\"removed_position\":" + std::to_string(position)
            + ",\"removed_item_id\":" + std::to_string(removed) + '}';
    }
    if (command == "clear_production_queue") {
        int base_id = field_int(request, "base_id", -1);
        if (base_id < 0 || base_id >= *BaseCount || Bases[base_id].faction_id != faction_id) {
            return error_response("invalid_base", "base_id must identify a base owned by the human faction.");
        }
        BASE& base = Bases[base_id];
        int removed_count = base.queue_size;
        for (int position = 1; position <= base.queue_size && position < 10; ++position) {
            int removed = base.queue_items[position];
            if (removed >= 0 && removed < MaxProtoNum) Factions[faction_id].units_queue[removed]--;
            base.queue_items[position] = 0;
        }
        base.queue_size = 0;
        if (*MultiplayerActive) {
            synch_base(base_id);
            synch_leader(faction_id);
        }
        return std::string("{\"ok\":true,\"command\":\"clear_production_queue\",\"base_id\":")
            + std::to_string(base_id) + ",\"removed_count\":" + std::to_string(removed_count) + '}';
    }
    if (command == "convert_worker_to_specialist"
    || command == "assign_specialist_to_tile" || command == "set_specialist_type") {
        int base_id = field_int(request, "base_id", -1);
        if (base_id < 0 || base_id >= *BaseCount || Bases[base_id].faction_id != faction_id) {
            return error_response("invalid_base", "base_id must identify a base owned by the human faction.");
        }
        set_base(base_id);
        base_compute(1);
        BASE& base = Bases[base_id];
        if ((base.governor_flags & (GOV_ACTIVE | GOV_MANAGE_CITIZENS))
        == (GOV_ACTIVE | GOV_MANAGE_CITIZENS)) {
            return error_response("citizens_managed_by_governor",
                "Disable citizen management with set_base_governor before assigning workers or specialists manually.");
        }
        int specialist_index = field_int(request, "specialist_index", -1);
        int citizen_id = field_int(request, "citizen_id", -1);
        int tile_index = field_int(request, "tile_index", -1);
        if (command == "convert_worker_to_specialist") {
            if (tile_index < 1 || tile_index >= 21
            || !(base.worked_tiles & (1 << tile_index))) {
                return error_response("invalid_worked_tile",
                    "tile_index must identify a currently worked non-base tile from base_citizens choices.");
            }
            if (base.specialist_total >= MaxBaseSpecNum
            || !specialist_available_at_base(base, citizen_id)) {
                return error_response("invalid_specialist_type",
                    "citizen_id must be an available specialist type and specialist capacity must remain available.");
            }
            base.worked_tiles &= ~(1 << tile_index);
            base.set_specialist_type(base.specialist_total, citizen_id);
            ++base.specialist_total;
            base.specialist_adjust = 0;
        } else if (command == "assign_specialist_to_tile") {
            if (specialist_index < 0 || specialist_index >= base.specialist_total) {
                return error_response("invalid_specialist_index",
                    "specialist_index must identify an existing specialist from base_citizens choices.");
            }
            int x = 0;
            int y = 0;
            MAP* sq = tile_index >= 1 && tile_index < 21
                ? next_tile(base.x, base.y, tile_index, &x, &y) : NULL;
            int blocked_flags = tile_index >= 1 && tile_index < 21
                ? BaseTileFlags[tile_index] & (BR_NOT_AVAILABLE | BR_NOT_VISIBLE
                    | BR_BASE_IN_TILE | BR_VEH_IN_TILE | BR_FOREIGN_TILE | BR_WORKER_ACTIVE)
                : BR_NOT_AVAILABLE;
            if (!sq || !sq->is_visible(faction_id) || blocked_flags
            || (base.worked_tiles & (1 << tile_index))) {
                return error_response("unavailable_worker_tile",
                    "tile_index must identify a fresh, assignable, unworked tile from base_citizens choices.");
            }
            for (int index = specialist_index; index + 1 < base.specialist_total; ++index) {
                base.set_specialist_type(index, base.specialist_type(index + 1));
            }
            --base.specialist_total;
            base.set_specialist_type(base.specialist_total, 0);
            base.specialist_adjust = 0;
            base.worked_tiles |= 1 << tile_index;
        } else {
            if (specialist_index < 0 || specialist_index >= base.specialist_total) {
                return error_response("invalid_specialist_index",
                    "specialist_index must identify an existing specialist from base_citizens choices.");
            }
            if (!specialist_available_at_base(base, citizen_id)) {
                return error_response("invalid_specialist_type",
                    "citizen_id must be an available specialist type from base_citizens choices.");
            }
            base.set_specialist_type(specialist_index, citizen_id);
        }
        set_base(base_id);
        base_compute(1);
        if (*MultiplayerActive) {
            synch_base(base_id);
            synch_leader(faction_id);
        }
        return std::string("{\"ok\":true,\"command\":") + json_string(command.c_str())
            + ",\"base_id\":" + std::to_string(base_id)
            + ",\"specialist_total\":" + std::to_string(base.specialist_total)
            + ",\"worked_tiles_mask\":" + std::to_string(base.worked_tiles) + '}';
    }
    int veh_id = field_int(request, "unit_id", -1);
    if (veh_id < 0 || veh_id >= *VehCount || Vehs[veh_id].faction_id != faction_id) {
        return error_response("invalid_unit", "unit_id must identify a unit owned by the human faction.");
    }
    VEH& veh = Vehs[veh_id];
    if (semantic_carrier_capacity(veh_id) > 0
    && semantic_carrier_dependency_count(veh_id) > 0) {
        return error_response("carrier_recovery_locked",
            "This carrier cannot accept commands while an owned fuel-limited aircraft is inbound or co-located but not boarded. Board, redirect, or activate that aircraft first.");
    }
    if (command == "set_bombing_run") {
        int target_tile_id = -1;
        int target_x = -1;
        int target_y = -1;
        if (!semantic_request_tile(request, &target_tile_id, &target_x, &target_y)) {
            return error_response("invalid_bombing_target",
                "target_tile_id must be copied from this aircraft's fresh bombing-run choice.");
        }
        std::string reason;
        if (!semantic_bombing_run_target_eligible(
            faction_id, veh_id, target_x, target_y, &reason)) {
            return std::string("{\"ok\":false,\"error\":{\"code\":\"invalid_bombing_target\","
                "\"message\":\"Use an exact currently visible Vendetta base from this ready aircraft's fresh bombing-run choices.\"},\"reason\":")
                + json_string(reason.c_str()) + '}';
        }
        int target_base_id = base_at(target_x, target_y);
        VEH previous = veh;
        Console_set_bombing_run(MapWin, veh_id, target_x, target_y);
        // The stock handler establishes the native automation flags, writes the
        // destination, and then calls synch_veh. In this host's patched
        // single-player executable that synchronization round-trip clears the
        // two destination words back to -1 while retaining the accepted native
        // policy. Restore exactly the already-validated target the stock
        // handler wrote. LAN remains withheld because its packet behavior has
        // not been validated.
        if (!*MultiplayerActive && (veh.state & VSTATE_ON_ALERT)
        && veh.order_auto_type == ORDERA_BOMBING_RUN
        && veh.waypoint_x[0] < 0 && veh.waypoint_y[0] < 0) {
            veh.waypoint_x[0] = target_x;
            veh.waypoint_y[0] = target_y;
        }
        bool accepted = (veh.state & VSTATE_ON_ALERT)
            && veh.order_auto_type == ORDERA_BOMBING_RUN
            && veh.waypoint_x[0] == target_x && veh.waypoint_y[0] == target_y;
        if (!accepted) {
            veh = previous;
            synch_veh(veh_id);
            return error_response("native_bombing_run_rejected",
                "The native bombing-run routine did not retain the exact guarded target; the aircraft was restored.");
        }
        return std::string("{\"ok\":true,\"command\":\"set_bombing_run\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"target_tile_id\":"
            + std::to_string(target_tile_id) + ",\"target_base_name\":"
            + json_string(Bases[target_base_id].name) + ",\"target_faction_id\":"
            + std::to_string(Bases[target_base_id].faction_id)
            + ",\"automation\":\"bombing_run\",\"persistent\":true,"
            "\"fuel_policy\":\"non_sacrificial_round_trip\",\"ready\":false}";
    }
    if (command == "destroy_terrain_improvement") {
        int former_id = field_int(request, "former_id", -1);
        if (*MultiplayerActive) {
            return error_response("multiplayer_terrain_destruction_not_supported",
                "The native LAN selection and diplomacy packet path for terrain destruction has not been validated yet.");
        }
        if (!terrain_destruction_unit_eligible(veh_id)) {
            return error_response("terrain_destruction_unit_unavailable",
                "Use a fresh destroy_terrain_improvement choice for a ready combat unit or former on a compatible non-base tile.");
        }
        MAP* sq = mapsq(veh.x, veh.y);
        if (!sq || !terrain_destruction_item_available(sq->items, former_id)) {
            return error_response("terrain_improvement_unavailable",
                "former_id must identify an exact currently destructible improvement returned for this unit's visible tile.");
        }
        if (field_int(request, "confirm_destruction", 0) != 1) {
            return error_response("terrain_destruction_confirmation_required",
                "Copy confirm_destruction=1 only from the exact fresh destructive choice after deciding to remove that improvement.");
        }
        int owner = whose_territory(faction_id, veh.x, veh.y, 0, 0);
        bool foreign = owner >= 1 && owner != faction_id;
        if (foreign && has_treaty(faction_id, owner, DIPLO_PACT)) {
            return error_response("pact_forbids_terrain_destruction",
                "The native game forbids this hostile action in Pact territory; renegotiate the Pact first.");
        }
        bool hostility_required = foreign
            && !has_treaty(faction_id, owner, DIPLO_VENDETTA);
        if (hostility_required && field_int(request, "confirm_hostility", 0) != 1) {
            return error_response("hostility_confirmation_required",
                "Copy confirm_hostility=1 only from this exact fresh foreign-territory choice after accepting the diplomatic break.");
        }
        if (deferred_native_action_pending()) {
            return error_response("action_already_queued",
                "A potentially blocking native action is already queued. Wait and observe.");
        }
        deferred_destroy_unit_id = veh_id;
        deferred_destroy_former_id = former_id;
        deferred_destroy_owner_id = owner;
        deferred_destroy_hostility_confirmed = hostility_required;
        begin_deferred_action("destroy_terrain_improvement", veh_id,
            veh.x, veh.y, veh.x, veh.y);
        if (!PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0)) {
            deferred_destroy_unit_id = -1;
            deferred_destroy_former_id = -1;
            deferred_destroy_owner_id = -1;
            deferred_destroy_hostility_confirmed = false;
            deferred_action.status = "rejected";
            return error_response("terrain_destruction_queue_failed",
                "The game could not queue the native terrain-destruction action.");
        }
        return std::string("{\"ok\":true,\"command\":\"destroy_terrain_improvement\","
            "\"queued\":true,\"unit_id\":") + std::to_string(veh_id)
            + ",\"former_id\":" + std::to_string(former_id)
            + ",\"improvement_name\":" + json_string(
                is_ocean(sq) ? Terraform[former_id].name_sea : Terraform[former_id].name)
            + ",\"territory_owner_faction_id\":" + std::to_string(owner)
            + ",\"hostility_follows\":" + (hostility_required ? "true" : "false")
            + ",\"action_id\":" + std::to_string(deferred_action.id) + '}';
    }
    if (command == "recover_to_carrier") {
        int carrier_id = field_int(request, "target_unit_id", -1);
        std::string reason;
        if (!semantic_carrier_recovery_eligible(
            faction_id, veh_id, carrier_id, &reason)) {
            return std::string("{\"ok\":false,\"error\":{\"code\":\"invalid_carrier_recovery\","
                "\"message\":\"Use an exact fresh recover_to_carrier choice.\"},\"reason\":")
                + json_string(reason.c_str()) + '}';
        }
        VEH previous_aircraft = veh;
        VEH previous_carrier = Vehs[carrier_id];
        VEH& carrier = Vehs[carrier_id];
        int safe_range = semantic_air_safe_range(veh_id);
        int distance = map_range(veh.x, veh.y, carrier.x, carrier.y);
        carrier.order = ORDER_HOLD;
        carrier.waypoint_x[0] = -1;
        carrier.waypoint_y[0] = 0;
        carrier.status_icon = 'H';
        veh_skip(carrier_id);
        int result = set_move_to(veh_id, carrier.x, carrier.y);
        bool accepted = veh.order == ORDER_MOVE_TO
            && veh.waypoint_x[0] == carrier.x
            && veh.waypoint_y[0] == carrier.y;
        if (!accepted) {
            veh = previous_aircraft;
            carrier = previous_carrier;
            synch_veh(veh_id);
            synch_veh(carrier_id);
            return error_response("native_carrier_recovery_rejected",
                "The native waypoint engine did not accept this reserved carrier route; both units were restored.");
        }
        std::ostringstream out;
        out << "{\"ok\":true,\"command\":\"recover_to_carrier\",\"unit_id\":"
            << veh_id << ",\"target_unit_id\":" << carrier_id
            << ",\"target_tile_id\":" << semantic_tile_id(carrier.x, carrier.y)
            << ",\"distance\":" << distance << ",\"native_result\":" << result
            << ",\"safe_range_at_assignment\":" << safe_range
            << ",\"capacity\":" << semantic_carrier_capacity(carrier_id)
            << ",\"loaded\":" << veh_cargo_loaded(carrier_id)
            << ",\"inbound_reserved\":" << semantic_carrier_inbound_count(carrier_id)
            << ",\"fuel_safe\":true,\"persistent\":true,\"ready\":false,"
            << "\"carrier_held\":true,\"route_kind\":\"carrier_recovery\"}";
        return out.str();
    }
    if (command == "board_carrier") {
        int carrier_id = field_int(request, "target_unit_id", -1);
        bool valid_carrier = !*MultiplayerActive
            && carrier_id >= 0 && carrier_id < *VehCount && carrier_id != veh_id
            && Vehs[carrier_id].faction_id == faction_id
            && semantic_carrier_capacity(carrier_id) > 0
            && Vehs[carrier_id].x == veh.x && Vehs[carrier_id].y == veh.y;
        bool valid_aircraft = veh.triad() == TRIAD_AIR && !veh.is_missile()
            && veh.range() && veh.order == ORDER_NONE;
        bool capacity_available = valid_carrier
            && veh_cargo_loaded(carrier_id)
                + semantic_carrier_inbound_count(carrier_id, veh_id)
                < semantic_carrier_capacity(carrier_id);
        if (!valid_carrier || !valid_aircraft || !capacity_available) {
            return error_response("invalid_carrier_boarding",
                "Use board_carrier only from this ready aircraft's exact fresh co-located carrier choice with unreserved deck capacity.");
        }
        VEH previous_aircraft = veh;
        sleep(veh_id);
        veh.waypoint_x[0] = carrier_id;
        veh.waypoint_y[0] = 0;
        veh.status_icon = 'L';
        stack_veh(carrier_id, 0);
        if (!semantic_aircraft_boarded_on(veh_id, carrier_id)) {
            veh = previous_aircraft;
            stack_veh(carrier_id, 0);
            synch_veh(veh_id);
            synch_veh(carrier_id);
            return error_response("native_carrier_boarding_rejected",
                "The native carrier stack did not accept this aircraft; its prior state was restored.");
        }
        veh.apply_refuel();
        veh_skip(veh_id);
        return std::string("{\"ok\":true,\"command\":\"board_carrier\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"target_unit_id\":"
            + std::to_string(carrier_id) + ",\"boarded\":true,\"refueled\":true,"
            "\"capacity\":" + std::to_string(semantic_carrier_capacity(carrier_id))
            + ",\"loaded\":" + std::to_string(veh_cargo_loaded(carrier_id)) + '}';
    }
    if (command == "activate_unit") {
        int boarded_carrier_id = -1;
        if (veh.order == ORDER_SENTRY_BOARD && veh.waypoint_x[0] >= 0
        && veh.waypoint_x[0] < *VehCount
        && semantic_aircraft_boarded_on(veh_id, veh.waypoint_x[0])) {
            boarded_carrier_id = veh.waypoint_x[0];
        }
        bool was_exploring = veh.state & VSTATE_EXPLORE;
        bool was_native_automation = semantic_native_automation_active(veh);
        std::string old_automation = was_exploring ? "auto_explore"
            : was_native_automation ? semantic_unit_order_name(veh) : "none";
        if (veh.order == ORDER_NONE && !was_exploring && !was_native_automation) {
            return error_response("unit_not_under_orders",
                "activate_unit is available only for a unit with a persistent order.");
        }
        if (veh_jail(veh_id) && boarded_carrier_id < 0) {
            return error_response("cannot_disembark_here",
                "This boarded unit cannot be activated until its transport reaches a base or airbase.");
        }
        int old_order = veh.order;
        veh_wake(veh_id);
        if (boarded_carrier_id >= 0) {
            veh.state &= ~VSTATE_IN_TRANSPORT;
            stack_veh(boarded_carrier_id, 0);
            synch_veh(veh_id);
            synch_veh(boarded_carrier_id);
        }
        return std::string("{\"ok\":true,\"command\":\"activate_unit\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"old_order\":" + std::to_string(old_order)
            + ",\"old_automation\":" + json_string(old_automation.c_str())
            + ",\"left_carrier\":" + (boarded_carrier_id >= 0 ? "true" : "false")
            + ",\"ready\":" + (veh_unmoved(veh_id) ? "true" : "false") + '}';
    }
    if (command == "remain_boarded") {
        int transport_id = field_int(request, "transport_unit_id", -1);
        if (veh.order != ORDER_SENTRY_BOARD || veh.waypoint_x[0] != transport_id
        || transport_id < 0 || transport_id >= *VehCount || transport_id == veh_id
        || Vehs[transport_id].faction_id != faction_id
        || Vehs[transport_id].x != veh.x || Vehs[transport_id].y != veh.y
        || (veh_cargo(transport_id) <= 0
            && !semantic_aircraft_boarded_on(veh_id, transport_id))) {
            return error_response("invalid_boarded_passenger",
                "Use remain_boarded only from this passenger's fresh boarded unit_actions choices.");
        }
        veh_skip(veh_id);
        return std::string("{\"ok\":true,\"command\":\"remain_boarded\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"transport_unit_id\":"
            + std::to_string(transport_id) + ",\"boarded\":true}";
    }
    if (command == "disembark_unit") {
        int transport_id = field_int(request, "transport_unit_id", -1);
        int target_tile_id = -1;
        int x = -1;
        int y = -1;
        bool target_tile_valid = semantic_request_tile(
            request, &target_tile_id, &x, &y);
        int direction = -1;
        for (int dir = 0; dir < 8; ++dir) {
            if (wrap(veh.x + BaseOffsetX[dir]) == x
            && veh.y + BaseOffsetY[dir] == y) direction = dir;
        }
        bool valid_transport = veh.order == ORDER_SENTRY_BOARD
            && veh.waypoint_x[0] == transport_id && transport_id >= 0
            && transport_id < *VehCount && transport_id != veh_id
            && Vehs[transport_id].faction_id == faction_id
            && Vehs[transport_id].x == veh.x && Vehs[transport_id].y == veh.y
            && Vehs[transport_id].triad() == TRIAD_SEA && veh_cargo(transport_id) > 0;
        MAP* target = mapsq(x, y);
        if (!target_tile_valid || !valid_transport || direction < 0 || !target
        || !is_known(x, y, faction_id)
        || (is_ocean(target) && target->base_who() < 0)
        || veh_speed(veh_id, 0) - veh.moves_spent <= 0) {
            return error_response("invalid_disembark",
                "Use a fresh disembark_unit choice for this boarded passenger and adjacent known land tile.");
        }
        if (target->is_visible(faction_id)) {
            int owner = target->veh_who();
            if (owner >= 0 && owner != faction_id && !has_pact(faction_id, owner)
            && !veh.is_combat_unit() && !veh.is_probe()) {
                return error_response("blocked_disembark_target",
                    "A noncombat passenger cannot disembark onto the visible non-pact stack.");
            }
        }
        if (deferred_native_action_pending()) {
            return error_response("action_already_queued",
                "A potentially blocking native action is already queued.");
        }
        int old_order = veh.order;
        int old_waypoint_x = veh.waypoint_x[0];
        int old_waypoint_y = veh.waypoint_y[0];
        char old_icon = veh.status_icon;
        deferred_move_unit_id = veh_id;
        deferred_move_direction = direction;
        deferred_move_x = x;
        deferred_move_y = y;
        begin_deferred_action("disembark_unit", veh_id, veh.x, veh.y, x, y);
        veh_wake(veh_id);
        if (!PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0)) {
            deferred_move_unit_id = -1;
            deferred_move_direction = -1;
            deferred_move_x = -1;
            deferred_move_y = -1;
            veh.order = old_order;
            veh.waypoint_x[0] = old_waypoint_x;
            veh.waypoint_y[0] = old_waypoint_y;
            veh.status_icon = old_icon;
            deferred_action.status = "rejected";
            return error_response("disembark_queue_failed",
                "The game could not queue the native disembark movement action.");
        }
        return std::string("{\"ok\":true,\"command\":\"disembark_unit\",\"queued\":true,\"unit_id\":")
            + std::to_string(veh_id) + ",\"transport_unit_id\":" + std::to_string(transport_id)
            + ",\"target_tile_id\":" + std::to_string(target_tile_id)
            + ",\"action_id\":" + std::to_string(deferred_action.id) + '}';
    }
    if (!veh_unmoved(veh_id)) {
        return error_response("unit_not_ready", "This unit has no unresolved action available this turn.");
    }
    if (command == "go_to_base") {
        int base_id = field_int(request, "base_id", -1);
        std::string reason;
        if (!semantic_go_to_base_eligible(faction_id, veh_id, base_id, &reason)) {
            std::string message =
                std::string("Use an exact guarded go_to_base choice from fresh unit_actions: ")
                + reason + '.';
            return error_response("invalid_go_to_base", message.c_str());
        }
        BASE& base = Bases[base_id];
        int distance = map_range(veh.x, veh.y, base.x, base.y);
        int result = set_move_to(veh_id, base.x, base.y);
        bool accepted = veh.order == ORDER_MOVE_TO
            && veh.waypoint_x[0] == base.x && veh.waypoint_y[0] == base.y;
        if (!accepted) {
            return error_response("native_go_to_base_rejected",
                "The native waypoint engine did not accept this guarded base route; observe fresh state.");
        }
        std::ostringstream out;
        out << "{\"ok\":true,\"command\":\"go_to_base\",\"unit_id\":" << veh_id
            << ",\"destination_base_id\":" << base_id
            << ",\"destination_base_name\":" << json_string(base.name)
            << ",\"distance\":" << distance;
        if (veh.triad() == TRIAD_AIR) {
            out << ",\"fuel_safe\":true,\"fuel_limited\":"
                << (veh.range() ? "true" : "false");
            if (veh.range()) out << ",\"safe_range_at_assignment\":"
                << semantic_air_safe_range(veh_id);
        }
        out << ",\"native_result\":" << result
            << ",\"order\":\"go_to\",\"persistent\":true,\"ready\":false}";
        return out.str();
    }
    if (command == "return_to_base") {
        if (*MultiplayerActive) {
            return error_response("multiplayer_return_to_base_not_supported",
                "Semantic return-to-base orders are withheld in LAN games until their native synchronization is validated end to end.");
        }
        int requested_base_id = field_int(request, "base_id", -1);
        int candidate_base_id = semantic_return_base_candidate(faction_id, veh_id);
        if (candidate_base_id < 0 || requested_base_id != candidate_base_id) {
            return error_response("invalid_return_base",
                "Use the exact native-selected known base returned by this ready land or sea unit's fresh unit_actions choices.");
        }
        VEH previous = veh;
        Console_go_home(MapWin, veh_id);
        bool accepted = veh.order == ORDER_MOVE_TO
            && veh.waypoint_x[0] == Bases[candidate_base_id].x
            && veh.waypoint_y[0] == Bases[candidate_base_id].y
            && is_known(veh.waypoint_x[0], veh.waypoint_y[0], faction_id);
        if (!accepted) {
            veh = previous;
            synch_veh(veh_id);
            return error_response("native_return_to_base_rejected",
                "The native route selector did not create the exact fair-play return order; observe fresh state.");
        }
        return std::string("{\"ok\":true,\"command\":\"return_to_base\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"destination_base_id\":"
            + std::to_string(candidate_base_id) + ",\"destination_base_name\":"
            + json_string(Bases[candidate_base_id].name)
            + ",\"order\":\"go_to\",\"persistent\":true,\"ready\":false}";
    }
    if (command == "set_unit_on_alert") {
        if (*MultiplayerActive) {
            return error_response("multiplayer_on_alert_not_supported",
                "Semantic On Alert automation is withheld in LAN games until its native synchronization is validated end to end.");
        }
        if (!veh.is_combat_unit()) {
            return error_response("on_alert_unavailable",
                "Use set_unit_on_alert only from a ready combat unit's fresh unit_actions choices.");
        }
        Console_automate(MapWin, veh_id, ORDERA_ON_ALERT);
        if (!(veh.state & VSTATE_ON_ALERT) || veh.order_auto_type != ORDERA_ON_ALERT) {
            return error_response("native_on_alert_rejected",
                "The native On Alert command did not accept this unit; observe fresh state.");
        }
        return std::string("{\"ok\":true,\"command\":\"set_unit_on_alert\",\"unit_id\":")
            + std::to_string(veh_id)
            + ",\"automation\":\"on_alert\",\"persistent\":true,\"ready\":false}";
    }
    if (command == "automate_air_defense") {
        if (*MultiplayerActive) {
            return error_response("multiplayer_air_defense_not_supported",
                "Semantic air-defense automation is withheld in LAN games until its native synchronization is validated end to end.");
        }
        if (!semantic_air_defense_eligible(veh)) {
            return error_response("air_defense_unavailable",
                "Use automate_air_defense only from a ready Air Superiority aircraft's fresh unit_actions choices.");
        }
        Console_automate(MapWin, veh_id, ORDERA_AUTOMATE_AIR_DEFENSE);
        if (!(veh.state & VSTATE_ON_ALERT)
        || veh.order_auto_type != ORDERA_AUTOMATE_AIR_DEFENSE) {
            return error_response("native_air_defense_rejected",
                "The native Automate Air Defense command did not accept this aircraft; observe fresh state.");
        }
        return std::string("{\"ok\":true,\"command\":\"automate_air_defense\",\"unit_id\":")
            + std::to_string(veh_id)
            + ",\"automation\":\"auto_air_defense\",\"persistent\":true,\"ready\":false}";
    }
    if (command == "automate_former") {
        if (*MultiplayerActive) {
            return error_response("multiplayer_former_automation_not_supported",
                "Semantic former automation is withheld in LAN games until its native synchronization is validated end to end.");
        }
        std::string mode = field_string(request, "automation_mode");
        int mode_id = former_automation_mode_id(mode);
        if (!former_automation_mode_available(faction_id, veh, mode_id)) {
            return error_response("former_automation_unavailable",
                "Use an exact currently eligible automation_mode returned by this ready former's fresh unit_actions choices.");
        }
        Console_automate(MapWin, veh_id, mode_id);
        if (!(veh.state & VSTATE_ON_ALERT) || veh.order_auto_type != mode_id) {
            return error_response("native_former_automation_rejected",
                "The native former automation command did not accept this mode; observe fresh state.");
        }
        return std::string("{\"ok\":true,\"command\":\"automate_former\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"automation_mode\":" + json_string(mode.c_str())
            + ",\"native_mode_id\":" + std::to_string(mode_id)
            + ",\"persistent\":true,\"ready\":false}";
    }
    if (command == "auto_explore_unit") {
        if (!veh.is_combat_unit() || veh.state & VSTATE_EXPLORE) {
            return error_response("auto_explore_unavailable",
                "Use auto_explore_unit only from a ready combat unit's fresh unit_actions choices.");
        }
        Console_explore(MapWin, veh_id);
        if (!(veh.state & VSTATE_EXPLORE)) {
            return error_response("native_auto_explore_rejected",
                "The native Explore command did not accept this unit; observe fresh state.");
        }
        return std::string("{\"ok\":true,\"command\":\"auto_explore_unit\",\"unit_id\":")
            + std::to_string(veh_id)
            + ",\"persistent\":true,\"native_automation\":true,\"ready\":false}";
    }
    if (command == "set_designated_defender") {
        if (*MultiplayerActive) {
            return error_response("multiplayer_designated_defender_not_supported",
                "Defender designation is withheld in LAN games until its native synchronization is validated end to end.");
        }
        bool valid_active = false;
        bool desired = field_bool(request, "active", false, &valid_active);
        if (!veh.is_combat_unit() || !valid_active) {
            return error_response("designated_defender_unavailable",
                "Use the exact active value returned by a ready combat unit's fresh unit_actions choices.");
        }
        bool current = veh.state & VSTATE_DESIGNATE_DEFENDER;
        if (desired == current) {
            return error_response("designation_already_set",
                "The requested designated-defender state is already active; observe fresh choices.");
        }
        Console_designate(MapWin, veh_id);
        bool applied = veh.state & VSTATE_DESIGNATE_DEFENDER;
        if (applied != desired) {
            return error_response("native_designation_rejected",
                "The native designated-defender command did not accept this unit; observe fresh state.");
        }
        return std::string("{\"ok\":true,\"command\":\"set_designated_defender\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"active\":" + (applied ? "true" : "false")
            + ",\"consumes_turn\":false,\"ready\":"
            + (veh_unmoved(veh_id) ? "true" : "false") + '}';
    }
    if (command == "upgrade_unit") {
        if (*MultiplayerActive) {
            return error_response("multiplayer_single_unit_upgrade_not_supported",
                "Single-unit upgrades are withheld in LAN games until their native network packets are validated end to end.");
        }
        int source_id = veh.unit_id;
        int target_id = field_int(request, "target_prototype_id", -1);
        if (!single_unit_upgrade_path_legal(faction_id, source_id, target_id)) {
            return error_response("illegal_single_unit_upgrade",
                "Use a target_prototype_id returned by this ready unit's fresh unit_actions choices.");
        }
        int energy_cost = 10 * mod_upgrade_cost(faction_id, target_id, source_id);
        if (energy_cost > Factions[faction_id].energy_credits) {
            return std::string("{\"ok\":false,\"error\":{\"code\":\"single_unit_upgrade_unaffordable\","
                "\"message\":\"The faction lacks the energy credits for this one-unit upgrade.\"},"
                "\"energy_cost\":") + std::to_string(energy_cost)
                + ",\"energy_credits\":" + std::to_string(Factions[faction_id].energy_credits) + '}';
        }
        if (field_int(request, "confirm_upgrade", 0) != 1) {
            return std::string("{\"ok\":false,\"error\":{\"code\":\"single_unit_upgrade_confirmation_required\","
                "\"message\":\"Set confirm_upgrade to 1 after inspecting this exact unit choice and energy cost.\"},"
                "\"unit_id\":") + std::to_string(veh_id)
                + ",\"source_prototype_id\":" + std::to_string(source_id)
                + ",\"target_prototype_id\":" + std::to_string(target_id)
                + ",\"energy_cost\":" + std::to_string(energy_cost) + '}';
        }
        std::string source_name = Units[source_id].name;
        std::string target_name = Units[target_id].name;
        int charged = single_unit_upgrade(veh_id, target_id);
        if (charged < 0) {
            return error_response("single_unit_upgrade_failed",
                "The native single-unit upgrade could not be applied; observe fresh state before retrying.");
        }
        return std::string("{\"ok\":true,\"command\":\"upgrade_unit\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"source_prototype_id\":" + std::to_string(source_id)
            + ",\"source_name\":" + json_string(source_name.c_str())
            + ",\"target_prototype_id\":" + std::to_string(target_id)
            + ",\"target_name\":" + json_string(target_name.c_str())
            + ",\"energy_spent\":" + std::to_string(charged)
            + ",\"turn_consumed\":true}";
    }
    if (command == "use_psi_gate") {
        int source_base_id = field_int(request, "source_base_id", -1);
        int destination_base_id = field_int(request, "destination_base_id", -1);
        if (source_base_id < 0 || source_base_id >= *BaseCount
        || source_base_id != base_at(veh.x, veh.y)
        || Bases[source_base_id].faction_id != faction_id
        || !can_use_teleport(source_base_id) || veh.moves_spent) {
            return error_response("invalid_psi_gate_source",
                "Use a fresh Psi Gate choice for a ready unit at an owned, unused source gate.");
        }
        if (destination_base_id < 0 || destination_base_id >= *BaseCount
        || destination_base_id == source_base_id
        || Bases[destination_base_id].faction_id != faction_id
        || !has_fac_built(FAC_PSI_GATE, destination_base_id)) {
            return error_response("invalid_psi_gate_destination",
                "destination_base_id must be an owned Psi Gate returned by this unit's fresh choices.");
        }
        BASE& destination = Bases[destination_base_id];
        bool compatible = veh.triad() == TRIAD_AIR
            || (veh.triad() == TRIAD_LAND && !is_ocean(&destination))
            || (veh.triad() == TRIAD_SEA && coast_tiles(destination.x, destination.y));
        if (!compatible) {
            return error_response("incompatible_psi_gate_destination",
                "The destination cannot receive this unit's triad.");
        }
        int old_x = veh.x;
        int old_y = veh.y;
        int result = net_action_gate(veh_id, destination_base_id);
        return std::string("{\"ok\":true,\"command\":\"use_psi_gate\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"source_base_id\":" + std::to_string(source_base_id)
            + ",\"destination_base_id\":" + std::to_string(destination_base_id)
            + ",\"native_result\":" + std::to_string(result)
            + ",\"origin_tile_id\":" + std::to_string(semantic_tile_id(old_x, old_y))
            + ",\"observed_tile_id\":"
            + std::to_string(semantic_tile_id(Vehs[veh_id].x, Vehs[veh_id].y))
            + ",\"source_gate_used\":"
            + ((Bases[source_base_id].state_flags & BSTATE_PSI_GATE_USED) ? "true" : "false")
            + ",\"destination_gate_used\":"
            + ((Bases[destination_base_id].state_flags & BSTATE_PSI_GATE_USED) ? "true" : "false") + '}';
    }
    if (command == "execute_probe_subversion") {
        int target_id = field_int(request, "target_unit_id", -1);
        int enhanced = field_int(request, "enhanced", 0);
        int x = target_id >= 0 && target_id < *VehCount ? Vehs[target_id].x : -1;
        int y = target_id >= 0 && target_id < *VehCount ? Vehs[target_id].y : -1;
        if (!veh.is_probe() || field_int(request, "confirm_probe_incident", 0) != 1) {
            return error_response("probe_confirmation_required",
                "Use a returned probe-subversion tuple and set confirm_probe_incident to 1.");
        }
        bool adjacent = false;
        for (int dir = 0; dir < 8; ++dir) {
            adjacent |= wrap(veh.x + BaseOffsetX[dir]) == x
                && veh.y + BaseOffsetY[dir] == y;
        }
        if (target_id < 0 || target_id >= *VehCount || !adjacent
        || Vehs[target_id].x != x || Vehs[target_id].y != y
        || Vehs[target_id].faction_id == faction_id
        || has_pact(faction_id, Vehs[target_id].faction_id)
        || !(Vehs[target_id].visibility & (1 << faction_id))) {
            return error_response("invalid_probe_subversion_target",
                "Use the exact visible adjacent isolated target returned by fresh unit choices.");
        }
        int cost = probe_unit_mind_control_cost(veh_id, target_id);
        if (cost < 0 || Factions[faction_id].energy_credits < cost) {
            return error_response("probe_subversion_unaffordable",
                "The native subversion cost is unavailable or exceeds current energy reserves.");
        }
        if (deferred_native_action_pending()) {
            return error_response("action_already_queued", "A potentially blocking native action is already queued.");
        }
        deferred_probe_unit_id = veh_id;
        deferred_probe_base_id = -1;
        deferred_probe_target_unit_id = target_id;
        deferred_probe_action_id = PRB_MIND_CONTROL_UNIT;
        deferred_probe_enhanced = enhanced != 0;
        deferred_probe_frame_faction_id = 0;
        begin_deferred_action("execute_probe_subversion", veh_id, veh.x, veh.y, x, y);
        if (!PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0)) {
            deferred_probe_unit_id = -1;
            deferred_probe_base_id = -1;
            deferred_probe_target_unit_id = -1;
            deferred_probe_action_id = -1;
            deferred_probe_frame_faction_id = 0;
            deferred_probe_enhanced = false;
            active_probe_unit_id = -1;
            active_probe_base_id = -1;
            active_probe_abort_requested = false;
            deferred_action.status = "rejected";
            return error_response("probe_queue_failed", "The game could not queue native unit subversion.");
        }
        return std::string("{\"ok\":true,\"command\":\"execute_probe_subversion\",\"queued\":true,\"unit_id\":")
            + std::to_string(veh_id) + ",\"target_unit_id\":" + std::to_string(target_id)
            + ",\"energy_cost\":" + std::to_string(cost)
            + ",\"action_id\":" + std::to_string(deferred_action.id) + '}';
    }
    if (command == "execute_probe_mission") {
        int target_base_id = field_int(request, "target_base_id", -1);
        int target_x = target_base_id >= 0 && target_base_id < *BaseCount
            ? Bases[target_base_id].x : -1;
        int target_y = target_base_id >= 0 && target_base_id < *BaseCount
            ? Bases[target_base_id].y : -1;
        int action_id = field_int(request, "action_id", -99);
        int enhanced = field_int(request, "enhanced", 0);
        int frame_faction_id = field_int(request, "frame_faction_id", 0);
        if (!veh.is_probe()) {
            return error_response("not_probe_team", "Only a probe team can execute a probe mission.");
        }
        if (field_int(request, "confirm_probe_incident", 0) != 1) {
            return error_response("probe_confirmation_required",
                "Set confirm_probe_incident to 1 after selecting the exact visible target and mission.");
        }
        if (target_base_id < 0 || target_base_id >= *BaseCount
        || base_at(target_x, target_y) != target_base_id) {
            return error_response("invalid_probe_target",
                "Use a visible adjacent non-pact target base returned by this probe team's fresh choices.");
        }
        if (Bases[target_base_id].faction_id == faction_id
        || has_pact(faction_id, Bases[target_base_id].faction_id)) {
            return error_response("invalid_probe_relation",
                "Probe missions cannot target an owned or pact base through this semantic action.");
        }
        if (is_ocean(mapsq(veh.x, veh.y)) != is_ocean(&Bases[target_base_id])
        && !has_abil(veh.unit_id, ABL_AMPHIBIOUS)) {
            return error_response("probe_target_requires_amphibious",
                "This probe design cannot cross the land/sea boundary to reach the target base.");
        }
        bool probe_target_adjacent = false;
        for (int dir = 0; dir < 8; ++dir) {
            if (wrap(veh.x + BaseOffsetX[dir]) == target_x
            && veh.y + BaseOffsetY[dir] == target_y) {
                probe_target_adjacent = true;
                break;
            }
        }
        if (!probe_target_adjacent) {
            return error_response("probe_target_not_adjacent",
                "The selected target base is no longer adjacent to this probe team.");
        }
        MAP* probe_target_sq = mapsq(target_x, target_y);
        if (!probe_target_sq || !probe_target_sq->is_visible(faction_id)) {
            return error_response("probe_target_not_visible",
                "The selected target base is no longer currently visible to this faction.");
        }
        int target_faction_id = Bases[target_base_id].faction_id;
        if (action_id == PRB_INTRODUCE_GENETIC_PLAGUE
        && field_int(request, "confirm_atrocity", 0) != 1) {
            return error_response("atrocity_confirmation_required",
                "Genetic plague is an atrocity. Copy confirm_atrocity=1 from the returned mission only after choosing to accept its consequences.");
        }
        bool target_hq = has_fac_built(FAC_HEADQUARTERS, target_base_id);
        bool valid_action = action_id >= PRB_INFILTRATE_DATALINKS
            && action_id <= PRB_FREE_CAPTURED_FACTION_LEADER;
        if (action_id == PRB_INFILTRATE_DATALINKS
        && (conf.counter_espionage ? probe_has_renew(faction_id, target_faction_id)
            : has_treaty(faction_id, target_faction_id, DIPLO_HAVE_INFILTRATOR))) {
            valid_action = false;
        }
        if (action_id == PRB_PROCURE_RESEARCH_DATA && !Rules->tgl_probe_steal_tech) {
            valid_action = false;
        }
        if (action_id == PRB_ASSASSINATE_PROMINENT_RESEARCHERS && !target_hq) {
            valid_action = false;
        }
        if (action_id == PRB_INTRODUCE_GENETIC_PLAGUE
        && !faction_has_gene_warfare(faction_id)) {
            valid_action = false;
        }
        if (action_id == PRB_FREE_CAPTURED_FACTION_LEADER
        && (!*ExpansionEnabled || *MultiplayerActive || !target_hq
            || captured_leaders(target_faction_id).empty())) {
            valid_action = false;
        }
        if (action_id == PRB_MIND_CONTROL_CITY) {
            int cost = mod_mind_control(target_base_id, faction_id, 0);
            if (target_hq || cost < 0 || Factions[faction_id].energy_credits < cost) {
                valid_action = false;
            }
        } else if (action_id == PRB_ACTIVATE_SABOTAGE_VIRUS && enhanced) {
            valid_action = true;
        } else if (enhanced) {
            valid_action = false;
        }
        if (frame_faction_id != 0) {
            valid_action = action_id >= PRB_PROCURE_RESEARCH_DATA
                && action_id <= PRB_ASSASSINATE_PROMINENT_RESEARCHERS
                && frame_faction_id > 0 && frame_faction_id < MaxPlayerNum
                && frame_faction_id != faction_id && frame_faction_id != target_faction_id
                && is_alive(frame_faction_id)
                && has_treaty(faction_id, frame_faction_id, DIPLO_COMMLINK);
        }
        if (!valid_action) {
            return error_response("invalid_probe_mission",
                "Use an exact mission tuple returned by this probe team's fresh unit_actions choices.");
        }
        if (deferred_native_action_pending()) {
            return error_response("action_already_queued",
                "A potentially blocking native action is already queued.");
        }
        deferred_probe_unit_id = veh_id;
        deferred_probe_base_id = target_base_id;
        deferred_probe_target_unit_id = -1;
        deferred_probe_action_id = action_id;
        deferred_probe_enhanced = enhanced != 0;
        deferred_probe_frame_faction_id = frame_faction_id;
        begin_deferred_action("execute_probe_mission", veh_id, veh.x, veh.y,
            target_x, target_y);
        if (!PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0)) {
            deferred_probe_unit_id = -1;
            deferred_probe_base_id = -1;
            deferred_probe_action_id = -1;
            deferred_probe_enhanced = false;
            deferred_probe_frame_faction_id = 0;
            deferred_action.status = "rejected";
            return error_response("probe_queue_failed",
                "The game could not queue the native probe mission.");
        }
        return std::string("{\"ok\":true,\"command\":\"execute_probe_mission\",\"queued\":true,\"unit_id\":")
            + std::to_string(veh_id) + ",\"target_base_id\":" + std::to_string(target_base_id)
            + ",\"probe_action_id\":" + std::to_string(action_id)
            + ",\"mission\":" + json_string(probe_action_name(action_id))
            + ",\"enhanced\":" + (enhanced ? "true" : "false")
            + ",\"action_id\":" + std::to_string(deferred_action.id)
            + '}';
    }
    if (command == "launch_missile") {
        int target_tile_id = -1;
        int x = -1;
        int y = -1;
        bool target_tile_valid = semantic_request_tile(
            request, &target_tile_id, &x, &y);
        std::string reason;
        if (!target_tile_valid || !missile_target_legal(faction_id, veh_id, x, y, &reason)) {
            return std::string("{\"ok\":false,\"error\":{\"code\":\"invalid_missile_target\","
                "\"message\":\"The queried missile target is no longer legal; inspect fresh fair-play state.\"},"
                "\"reason\":") + json_string(reason.c_str()) + '}';
        }
        if (veh.is_planet_buster() && field_int(request, "confirm_atrocity", 0) != 1) {
            return error_response("atrocity_confirmation_required",
                "A Planet Buster is an atrocity. Copy confirm_atrocity=1 only from the exact fresh launch tuple after choosing to accept its consequences.");
        }
        if (*MultiplayerActive) {
            return error_response("multiplayer_missile_launch_not_supported",
                "Semantic missile launches are not network-synchronized yet.");
        }
        if (deferred_native_action_pending()) {
            return error_response("action_already_queued",
                "A potentially blocking native action is already queued.");
        }
        std::string kind = missile_kind(veh);
        deferred_missile_unit_id = veh_id;
        deferred_missile_x = x;
        deferred_missile_y = y;
        begin_deferred_action("launch_missile", veh_id, veh.x, veh.y, x, y);
        if (!PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0)) {
            deferred_missile_unit_id = -1;
            deferred_missile_x = -1;
            deferred_missile_y = -1;
            deferred_action.status = "rejected";
            return error_response("missile_queue_failed",
                "The game could not queue the native missile launch.");
        }
        return std::string("{\"ok\":true,\"command\":\"launch_missile\",\"queued\":true,\"unit_id\":")
            + std::to_string(veh_id) + ",\"missile_kind\":" + json_string(kind.c_str())
            + ",\"target_tile_id\":" + std::to_string(target_tile_id)
            + ",\"action_id\":"
            + std::to_string(deferred_action.id) + '}';
    }
    if (command == "board_transport") {
        int transport_id = field_int(request, "transport_unit_id", -1);
        if (veh.triad() != TRIAD_LAND || transport_id < 0 || transport_id >= *VehCount
        || transport_id == veh_id || Vehs[transport_id].faction_id != faction_id
        || Vehs[transport_id].x != veh.x || Vehs[transport_id].y != veh.y
        || veh_cargo(transport_id) <= 0
        || veh_cargo_loaded(transport_id) >= veh_cargo(transport_id)) {
            return error_response("invalid_transport_boarding",
                "transport_unit_id must be a co-located owned transport with capacity, as returned by fresh unit_actions choices.");
        }
        int result = set_board_to(veh_id, transport_id);
        veh_skip(veh_id);
        return std::string("{\"ok\":true,\"command\":\"board_transport\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"transport_unit_id\":" + std::to_string(transport_id)
            + ",\"result\":" + std::to_string(result) + ",\"boarded\":true}";
    }
    if (command == "airdrop_unit") {
        int target_tile_id = -1;
        int x = -1;
        int y = -1;
        bool target_tile_valid = semantic_request_tile(
            request, &target_tile_id, &x, &y);
        MAP* origin = mapsq(veh.x, veh.y);
        MAP* target = mapsq(x, y);
        if (!target_tile_valid || !can_airdrop(veh_id, origin)
        || !target || !target->is_visible(faction_id)
        || is_ocean(target) || map_range(veh.x, veh.y, x, y) > drop_range(faction_id)
        || !allow_airdrop(x, y, faction_id, veh.is_combat_unit(), target)) {
            return error_response("invalid_airdrop_target",
                "Use a currently returned visible, rule-validated target from this unit's fresh airdrop choice.");
        }
        int unit_owner = target->veh_who();
        int base_owner = target->base_who();
        bool unsafe_relation = (unit_owner >= 0 && unit_owner != faction_id
                && !has_pact(faction_id, unit_owner) && !at_war(faction_id, unit_owner))
            || (base_owner >= 0 && base_owner != faction_id
                && !has_pact(faction_id, base_owner) && !at_war(faction_id, base_owner));
        if (unsafe_relation) {
            return error_response("airdrop_requires_diplomatic_decision",
                "A semantic airdrop will not implicitly break a treaty or truce. Resolve diplomacy first.");
        }
        int old_x = veh.x;
        int old_y = veh.y;
        int result = action_airdrop(veh_id, x, y, 3);
        bool same_unit = veh_id >= 0 && veh_id < *VehCount
            && Vehs[veh_id].faction_id == faction_id;
        return std::string("{\"ok\":true,\"command\":\"airdrop_unit\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"native_result\":" + std::to_string(result)
            + ",\"accepted\":" + (result ? "true" : "false")
            + ",\"origin_tile_id\":" + std::to_string(semantic_tile_id(old_x, old_y))
            + ",\"target_tile_id\":" + std::to_string(target_tile_id)
            + ",\"observed_tile_id\":" + (same_unit
                ? std::to_string(semantic_tile_id(Vehs[veh_id].x, Vehs[veh_id].y))
                : "null") + '}';
    }
    if (command == "artillery_attack") {
        int target_tile_id = -1;
        int x = -1;
        int y = -1;
        bool target_tile_valid = semantic_request_tile(
            request, &target_tile_id, &x, &y);
        int target_id = veh_at(x, y);
        bool visible_target = false;
        for (int current = target_id; current >= 0;
        current = Vehs[current].next_veh_id_stack) {
            if (Vehs[current].faction_id != faction_id
            && !has_pact(faction_id, Vehs[current].faction_id)
            && (Vehs[current].visibility & (1 << faction_id))) {
                visible_target = true;
                break;
            }
        }
        if (!target_tile_valid || !can_arty(veh.unit_id, true) || !visible_target
        || map_range(veh.x, veh.y, x, y) > arty_range(veh.unit_id)) {
            return error_response("invalid_artillery_target",
                "Use a visible non-pact stack returned by this artillery unit's fresh unit_actions choices.");
        }
        std::string attacker_name = veh.name();
        int old_x = veh.x;
        int old_y = veh.y;
        action_arty(veh_id, x, y);
        return std::string("{\"ok\":true,\"command\":\"artillery_attack\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"unit_name\":" + json_string(attacker_name.c_str())
            + ",\"origin_tile_id\":" + std::to_string(semantic_tile_id(old_x, old_y))
            + ",\"target_tile_id\":" + std::to_string(target_tile_id)
            + ",\"accepted\":true,\"observe_after_combat\":true,\"unit_ids_may_have_shifted\":true}";
    }
    if (command == "rehome_unit") {
        if (*MultiplayerActive) {
            return error_response("multiplayer_rehome_not_supported",
                "Semantic support-home changes are not network-synchronized yet.");
        }
        int base_id = field_int(request, "base_id", -1);
        int local_base_id = base_at(veh.x, veh.y);
        if (base_id < 0 || base_id != local_base_id || base_id >= *BaseCount
        || Bases[base_id].faction_id != faction_id) {
            return error_response("invalid_rehome_base",
                "The unit can be rehomed only to the owned base on its current tile.");
        }
        int old_home_base_id = veh.home_base_id;
        veh.home_base_id = base_id;
        set_base(base_id);
        base_compute(1);
        if (old_home_base_id >= 0 && old_home_base_id < *BaseCount
        && old_home_base_id != base_id && Bases[old_home_base_id].faction_id == faction_id) {
            set_base(old_home_base_id);
            base_compute(1);
        }
        return std::string("{\"ok\":true,\"command\":\"rehome_unit\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"old_home_base_id\":"
            + std::to_string(old_home_base_id) + ",\"home_base_id\":"
            + std::to_string(base_id) + '}';
    }
    if (command == "give_unit") {
        if (*MultiplayerActive) {
            return error_response("multiplayer_unit_transfer_not_supported",
                "Semantic unit transfers are not network-synchronized yet.");
        }
        MAP* sq = mapsq(veh.x, veh.y);
        int target = sq ? sq->owner : -1;
        int requested_target = field_int(request, "faction_id", -1);
        bool air_compatible = veh.triad() != TRIAD_AIR || (sq && sq->is_airbase());
        bool cargo_safe = veh.order != ORDER_SENTRY_BOARD
            && (!veh.is_transport() || veh_cargo_loaded(veh_id) == 0);
        if (!sq || !sq->is_visible(faction_id)
        || target < 1 || target >= MaxPlayerNum || target == faction_id
        || target != requested_target || !is_alive(target)
        || !has_pact(faction_id, target) || !air_compatible || !cargo_safe) {
            return error_response("unit_transfer_unavailable",
                "Use the exact target faction returned by this unit's fresh unit_actions choice. The unit must be unboarded, carry no cargo, and stand in that living Pact faction's territory; air units also require a base or airbase.");
        }
        if (field_int(request, "confirm_transfer", 0) != 1) {
            return error_response("unit_transfer_confirmation_required",
                "Set confirm_transfer to 1 only after selecting the consequential fresh unit-transfer choice.");
        }
        std::string unit_name = veh.name();
        int old_prototype_id = veh.unit_id;
        int x = veh.x;
        int y = veh.y;
        action_give(veh_id, target);
        Console_update_data(MapWin, 0);
        if (veh_id < 0 || veh_id >= *VehCount || Vehs[veh_id].faction_id != target) {
            return error_response("native_unit_transfer_verification_failed",
                "The native ownership-change routine did not leave the selected unit under the requested faction's control. Observe before acting again.");
        }
        return std::string("{\"ok\":true,\"command\":\"give_unit\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"unit_name\":" + json_string(unit_name.c_str())
            + ",\"old_prototype_id\":" + std::to_string(old_prototype_id)
            + ",\"new_prototype_id\":" + std::to_string(Vehs[veh_id].unit_id)
            + ",\"from_faction_id\":" + std::to_string(faction_id)
            + ",\"to_faction_id\":" + std::to_string(target)
            + ",\"tile_id\":" + std::to_string(semantic_tile_id(x, y))
            + ",\"native_owner_change_verified\":true}";
    }
    if (command == "convoy_resource") {
        if (!veh.is_supply()) {
            return error_response("not_supply_unit", "Only a supply unit can convoy resources.");
        }
        std::string resource_name = field_string(request, "resource");
        ResType resource = resource_name == "nutrients" ? RES_NUTRIENT
            : resource_name == "minerals" ? RES_MINERAL
            : resource_name == "energy" ? RES_ENERGY : RES_NONE;
        if (resource == RES_NONE || !can_convoy(veh_id, resource)) {
            return error_response("cannot_convoy_resource",
                "Use a resource returned by this unit's fresh unit_actions choices.");
        }
        int result = set_convoy(veh_id, resource);
        return std::string("{\"ok\":true,\"command\":\"convoy_resource\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"resource\":" + json_string(resource_name.c_str())
            + ",\"result\":" + std::to_string(result) + ",\"persistent\":true}";
    }
    if (command == "self_destruct_unit") {
        if (*MultiplayerActive) {
            return error_response("multiplayer_self_destruct_not_supported",
                "Semantic self-destruct is withheld until its native LAN synchronization packet is validated.");
        }
        if (!self_destruct_unit_eligible(faction_id, veh_id)) {
            return error_response("self_destruct_unavailable",
                "Use a fresh self_destruct_unit choice for an actionable non-objective unit outside every base.");
        }
        if (field_int(request, "confirm_self_destruct", 0) != 1) {
            return error_response("self_destruct_confirmation_required",
                "Copy confirm_self_destruct=1 only from the exact fresh overload choice after reviewing its known blast casualties.");
        }
        std::string unit_name = veh.name();
        int prototype_id = veh.unit_id;
        int source_tile_id = semantic_tile_id(veh.x, veh.y);
        int blast_damage = clamp(weap_val(prototype_id, faction_id), 1, 20)
            * Units[prototype_id].reactor_id / 2;
        action_destruct(veh_id);
        return std::string("{\"ok\":true,\"command\":\"self_destruct_unit\","
            "\"former_unit_id\":") + std::to_string(veh_id)
            + ",\"prototype_id\":" + std::to_string(prototype_id)
            + ",\"unit_name\":" + json_string(unit_name.c_str())
            + ",\"source_tile_id\":" + std::to_string(source_tile_id)
            + ",\"blast_damage\":" + std::to_string(blast_damage)
            + ",\"ids_may_have_shifted\":true}";
    }
    if (command == "disband_unit") {
        if (field_int(request, "confirm_disband", 0) != 1) {
            return error_response("disband_confirmation_required",
                "Set confirm_disband to 1 after selecting the destructive choice.");
        }
        std::string unit_name = veh.name();
        int unit_id = veh.unit_id;
        veh_kill(veh_id);
        return std::string("{\"ok\":true,\"command\":\"disband_unit\",\"deleted_unit_id\":")
            + std::to_string(veh_id) + ",\"prototype_id\":" + std::to_string(unit_id)
            + ",\"unit_name\":" + json_string(unit_name.c_str())
            + ",\"ids_may_have_shifted\":true}";
    }
    if (command == "move_unit") {
        int target_tile_id = -1;
        int x = -1;
        int y = -1;
        bool target_tile_valid = semantic_request_tile(
            request, &target_tile_id, &x, &y);
        int offset = -1;
        for (int dir = 0; dir < 8; ++dir) {
            if (wrap(veh.x + BaseOffsetX[dir]) == x && veh.y + BaseOffsetY[dir] == y) offset = dir;
        }
        if (!target_tile_valid || offset < 0) return error_response(
            "invalid_move", "target_tile_id must identify an adjacent target returned by unit_actions choices.");
        if (deferred_native_action_pending()) {
            return error_response("action_already_queued", "A potentially blocking native action is already queued.");
        }
        deferred_move_unit_id = veh_id;
        deferred_move_direction = offset;
        deferred_move_x = x;
        deferred_move_y = y;
        begin_deferred_action("move_unit", veh_id, veh.x, veh.y, x, y);
        if (!PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0)) {
            deferred_move_unit_id = -1;
            deferred_move_direction = -1;
            deferred_move_x = -1;
            deferred_move_y = -1;
            deferred_action.status = "rejected";
            return error_response("move_queue_failed", "The game could not queue the native movement action.");
        }
        return std::string("{\"ok\":true,\"command\":\"move_unit\",\"queued\":true,\"unit_id\":")
            + std::to_string(veh_id) + ",\"target_tile_id\":"
            + std::to_string(target_tile_id)
            + ",\"action_id\":" + std::to_string(deferred_action.id) + '}';
    }
    if (command == "skip_unit") {
        veh_skip(veh_id);
        if (*MultiplayerActive) synch_veh(veh_id);
        return std::string("{\"ok\":true,\"command\":\"skip_unit\",\"unit_id\":") + std::to_string(veh_id) + '}';
    }
    if (command == "hold_unit") {
        veh.order = ORDER_HOLD;
        veh.waypoint_x[0] = -1;
        veh.waypoint_y[0] = 0;
        veh.status_icon = 'H';
        veh_skip(veh_id);
        if (*MultiplayerActive) synch_veh(veh_id);
        return std::string("{\"ok\":true,\"command\":\"hold_unit\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"persistent\":true}";
    }
    if (command == "sentry_unit") {
        sleep(veh_id);
        veh.status_icon = 'S';
        veh_skip(veh_id);
        if (*MultiplayerActive) synch_veh(veh_id);
        return std::string("{\"ok\":true,\"command\":\"sentry_unit\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"persistent\":true}";
    }
    if (command == "patrol_unit") {
        int target_tile_id = -1;
        int x = -1;
        int y = -1;
        bool target_tile_valid = semantic_request_tile(
            request, &target_tile_id, &x, &y);
        if (!target_tile_valid || !is_known(x, y, faction_id)) {
            return error_response("unknown_patrol_waypoint",
                "The patrol waypoint must be an on-map tile already known to the human faction.");
        }
        if (!valid_patrol(veh_id, x, y)) {
            return error_response("invalid_patrol_waypoint",
                "The native patrol rules reject this waypoint for the unit's triad, region, range, or airbase state.");
        }
        int result = action_patrol(veh_id, x, y);
        return std::string("{\"ok\":true,\"command\":\"patrol_unit\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"target_tile_id\":"
            + std::to_string(target_tile_id) + ",\"native_result\":"
            + std::to_string(result) + ",\"order\":"
            + json_string(unit_order_name(Vehs[veh_id].order))
            + ",\"waypoint_count\":" + std::to_string(Vehs[veh_id].waypoint_count)
            + ",\"persistent\":true}";
    }
    if (command == "build_road_to") {
        int target_tile_id = -1;
        int x = -1;
        int y = -1;
        bool target_tile_valid = semantic_request_tile(
            request, &target_tile_id, &x, &y);
        std::string infrastructure = field_string(request, "infrastructure");
        MAP* current_sq = mapsq(veh.x, veh.y);
        MAP* target_sq = mapsq(x, y);
        bool magtube = infrastructure == "magtube";
        if (!veh.is_former() || veh.triad() != TRIAD_LAND || !current_sq
        || is_ocean(current_sq)) {
            return error_response("road_to_requires_land_former",
                "Road To and Mag Tube To require a ready land former currently on land.");
        }
        if (infrastructure != "road" && !magtube) {
            return error_response("invalid_road_to_infrastructure",
                "Use the road or magtube mode returned by this former's fresh choices.");
        }
        if (!terrain_avail(magtube ? FORMER_MAGTUBE : FORMER_ROAD, 0, faction_id)) {
            return error_response("road_to_technology_unavailable",
                "The selected infrastructure is not unlocked for this faction.");
        }
        if (!target_tile_valid || !target_sq || !is_known(x, y, faction_id) || is_ocean(target_sq)
        || (x == veh.x && y == veh.y)) {
            return error_response("invalid_road_to_destination",
                "Choose a different known land destination; the native engine will resolve the route turn by turn.");
        }
        int result = set_road_to(veh_id, x, y);
        if (magtube) {
            Vehs[veh_id].order = ORDER_MAGTUBE_TO;
            Vehs[veh_id].status_icon = 'T';
        }
        return std::string("{\"ok\":true,\"command\":\"build_road_to\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"infrastructure\":"
            + json_string(infrastructure.c_str()) + ",\"target_tile_id\":"
            + std::to_string(target_tile_id) + ",\"native_result\":" + std::to_string(result)
            + ",\"order\":" + json_string(unit_order_name(Vehs[veh_id].order))
            + ",\"persistent\":true}";
    }
    if (command == "go_to") {
        int target_tile_id = -1;
        int x = -1;
        int y = -1;
        bool target_tile_valid = semantic_request_tile(
            request, &target_tile_id, &x, &y);
        std::string reason;
        if (!target_tile_valid || !semantic_go_to_tile_eligible(
            faction_id, veh_id, x, y, &reason)) {
            std::string message = std::string(
                "Use an exact guarded go_to choice from fresh unit_actions: ")
                + (target_tile_valid ? reason : "invalid tile_id") + '.';
            return error_response("invalid_go_to_destination", message.c_str());
        }
        int distance = map_range(veh.x, veh.y, x, y);
        bool air_fuel_limited = veh.triad() == TRIAD_AIR
            && !veh.is_missile() && veh.range();
        bool destination_refuels = air_fuel_limited
            && semantic_friendly_air_refuel_tile(faction_id, x, y);
        int result = set_move_to(veh_id, x, y);
        bool accepted = veh.order == ORDER_MOVE_TO
            && veh.waypoint_x[0] == x && veh.waypoint_y[0] == y;
        if (!accepted) return error_response("native_go_to_rejected",
            "The native waypoint engine did not accept this guarded tile route; observe fresh state.");
        std::ostringstream out;
        out << "{\"ok\":true,\"command\":\"go_to\",\"unit_id\":" << veh_id
            << ",\"target_tile_id\":" << target_tile_id
            << ",\"distance\":" << distance << ",\"result\":" << result
            << ",\"persistent\":true,\"ready\":false";
        if (air_fuel_limited) {
            out << ",\"fuel_safe\":true,\"destination_refuels\":"
                << (destination_refuels ? "true" : "false")
                << ",\"safe_range_at_assignment\":" << semantic_air_safe_range(veh_id)
                << ",\"route_kind\":" << json_string(
                    destination_refuels ? "air_recovery" : "air_round_trip");
        }
        out << '}';
        return out.str();
    }
    if (command == "found_base") {
        if (!veh.is_colony() || !can_build_base(veh.x, veh.y, faction_id, veh.triad())) {
            return error_response("cannot_found_base", "This unit cannot found a base on its current tile.");
        }
        std::string name = field_string(request, "name");
        if (name.size() >= sizeof(BASE::name)
        || name.find_first_of("\r\n\t") != std::string::npos) {
            return error_response("invalid_base_name",
                "Base names must contain at most 24 characters and no control whitespace.");
        }
        int result = net_action_build(veh_id, name.empty() ? NULL : const_cast<char*>(name.c_str()));
        return std::string("{\"ok\":true,\"command\":\"found_base\",\"result\":") + std::to_string(result) + '}';
    }
    if (command == "terraform") {
        int former_id = field_int(request, "former_id", -1);
        MAP* sq = mapsq(veh.x, veh.y);
        int ocean = sq && is_ocean(sq);
        if (!veh.is_former() || former_id < FORMER_FARM || former_id > FORMER_MONOLITH
            || !sq || sq->base_who() >= 0
            || !terrain_avail(static_cast<FormerItem>(former_id), ocean, faction_id)) {
            return error_response("cannot_terraform", "former_id is not a legal terraform order for this unit and tile.");
        }
        int result = action_terraform(veh_id, former_id, 1);
        bool in_progress = veh_id < *VehCount
            && Vehs[veh_id].order == former_id + VehOrderFormerFirst;
        return std::string("{\"ok\":true,\"command\":\"terraform\",\"unit_id\":")
            + std::to_string(veh_id) + ",\"former_id\":" + std::to_string(former_id)
            + ",\"result\":" + std::to_string(result)
            + ",\"accepted\":" + ((result || in_progress) ? "true" : "false")
            + ",\"in_progress\":" + (in_progress ? "true" : "false") + '}';
    }
    return error_response("unsupported_semantic_command",
        "The requested semantic command is not implemented. Enumerate a fresh choice family or report a capability gap.");
}

#if 0
// Retired pre-semantic UI prototype. Kept outside the binary temporarily for
// native menu reverse-engineering reference; execute_request rejects `act`.
HWND active_input_target(HWND hwnd) {
    HWND target = GetLastActivePopup(hwnd);
    return target && IsWindow(target) ? target : hwnd;
}

void center_pointer(HWND hwnd) {
    RECT rect = {};
    GetClientRect(hwnd, &rect);
    POINT point = {(rect.right - rect.left) / 2, (rect.bottom - rect.top) / 2};
    ClientToScreen(hwnd, &point);
    SetCursorPos(point.x, point.y);
}

void post_key(HWND hwnd, UINT vk, bool ctrl, bool shift, bool alt) {
    // SMACX checks modifier state with GetAsyncKeyState, so synthetic window
    // messages are insufficient for Ctrl/Shift/Alt shortcuts. SendInput makes
    // the shortcut indistinguishable from normal local keyboard input.
    SetForegroundWindow(active_input_target(hwnd));
    INPUT inputs[8] = {};
    int count = 0;
    const UINT modifiers[] = {VK_CONTROL, VK_SHIFT, VK_MENU};
    const bool enabled[] = {ctrl, shift, alt};
    for (int i = 0; i < 3; ++i) {
        if (enabled[i]) {
            inputs[count].type = INPUT_KEYBOARD;
            inputs[count++].ki.wVk = modifiers[i];
        }
    }
    inputs[count].type = INPUT_KEYBOARD;
    inputs[count++].ki.wVk = static_cast<WORD>(vk);
    inputs[count].type = INPUT_KEYBOARD;
    inputs[count].ki.wVk = static_cast<WORD>(vk);
    inputs[count++].ki.dwFlags = KEYEVENTF_KEYUP;
    for (int i = 2; i >= 0; --i) {
        if (enabled[i]) {
            inputs[count].type = INPUT_KEYBOARD;
            inputs[count].ki.wVk = modifiers[i];
            inputs[count++].ki.dwFlags = KEYEVENTF_KEYUP;
        }
    }
    SendInput(count, inputs, sizeof(INPUT));
}

UINT named_key(const std::string& name) {
    if (name.size() == 1) return static_cast<UINT>(VkKeyScanA(name[0]) & 0xFF);
    if (name == "ENTER") return VK_RETURN;
    if (name == "ESC" || name == "ESCAPE") return VK_ESCAPE;
    if (name == "SPACE") return VK_SPACE;
    if (name == "TAB") return VK_TAB;
    if (name == "BACKSPACE") return VK_BACK;
    if (name == "DELETE") return VK_DELETE;
    if (name == "HOME") return VK_HOME;
    if (name == "END") return VK_END;
    if (name == "UP") return VK_UP;
    if (name == "DOWN") return VK_DOWN;
    if (name == "LEFT") return VK_LEFT;
    if (name == "RIGHT") return VK_RIGHT;
    if (name.size() >= 2 && name[0] == 'F') {
        int f = atoi(name.c_str() + 1);
        if (f >= 1 && f <= 12) return VK_F1 + f - 1;
    }
    return 0;
}

std::string act_response(HWND hwnd, const std::string& request) {
    std::string action = field_string(request, "action");
    if (action == "select_unit") {
        if (!game_active()) return error_response("not_in_game", "Start or load a game first.");
        int faction_id = *CurrentPlayerFaction;
        int veh_id = field_int(request, "unit_id", -1);
        if (veh_id < 0 || veh_id >= *VehCount || Vehs[veh_id].faction_id != faction_id) {
            return error_response("invalid_unit", "unit_id must identify a unit owned by the current human faction.");
        }
        if (*CurrentFaction != faction_id || *WinModalState || *PopupDialogState) {
            return error_response("not_actionable", "Wait for the human faction's non-modal turn before selecting a unit.");
        }
        MapWin->iUnit = veh_id;
        *CurrentVehID = veh_id;
        Console_focus(MapWin, Vehs[veh_id].x, Vehs[veh_id].y, faction_id);
        Console_update_data(MapWin, 0);
        return std::string("{\"ok\":true,\"selected_unit_id\":") + std::to_string(veh_id) + '}';
    }
    if (action == "open_base") {
        if (!game_active()) return error_response("not_in_game", "Start or load a game first.");
        int faction_id = *CurrentPlayerFaction;
        int base_id = field_int(request, "base_id", -1);
        if (base_id < 0 || base_id >= *BaseCount || Bases[base_id].faction_id != faction_id) {
            return error_response("invalid_base", "base_id must identify a base owned by the current human faction.");
        }
        if (*CurrentFaction != faction_id || *WinModalState || *PopupDialogState) {
            return error_response("not_actionable", "Wait for the human faction's non-modal turn before opening a base.");
        }
        BaseWin_zoom(BaseWin, base_id, 0);
        return std::string("{\"ok\":true,\"opened_base_id\":") + std::to_string(base_id) + '}';
    }
    if (action == "click") {
        int x1000 = field_int(request, "x1000", -1);
        int y1000 = field_int(request, "y1000", -1);
        if (x1000 < 0 || x1000 > 1000 || y1000 < 0 || y1000 > 1000) {
            return error_response("bad_coordinates", "x1000 and y1000 must be normalized integers from 0 to 1000.");
        }
        HWND target = active_input_target(hwnd);
        RECT rect = {};
        GetClientRect(target, &rect);
        int x = (rect.right - rect.left) * x1000 / 1000;
        int y = (rect.bottom - rect.top) * y1000 / 1000;
        if (target == hwnd) {
            LPARAM point = MAKELPARAM(x, y);
            PostMessage(target, WM_MOUSEMOVE, 0, point);
            PostMessage(target, WM_LBUTTONDOWN, MK_LBUTTON, point);
            PostMessage(target, WM_LBUTTONUP, 0, point);
            center_pointer(hwnd);
        } else {
            POINT point = {x, y};
            ClientToScreen(target, &point);
            SetForegroundWindow(target);
            INPUT inputs[3] = {};
            for (int i = 0; i < 3; ++i) inputs[i].type = INPUT_MOUSE;
            inputs[0].mi.dx = MulDiv(point.x, 65535, GetSystemMetrics(SM_CXSCREEN) - 1);
            inputs[0].mi.dy = MulDiv(point.y, 65535, GetSystemMetrics(SM_CYSCREEN) - 1);
            inputs[0].mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE;
            inputs[1].mi.dwFlags = MOUSEEVENTF_LEFTDOWN;
            inputs[2].mi.dwFlags = MOUSEEVENTF_LEFTUP;
            SendInput(3, inputs, sizeof(INPUT));
            center_pointer(hwnd);
        }
        return "{\"ok\":true,\"queued\":true,\"action\":\"click\"}";
    }
    if (action == "text" || action == "chat") {
        std::string value = field_string(request, "text");
        if (value.empty() || value.size() > 512) {
            return error_response("bad_text", "text must contain 1 to 512 characters.");
        }
        if (action == "chat") post_key(hwnd, 'C', true, false, false);
        for (size_t i = 0; i < value.size(); ++i) {
            SHORT translated = VkKeyScanA(value[i]);
            if (translated == -1) continue;
            UINT character_key = static_cast<UINT>(translated & 0xFF);
            BYTE modifiers = static_cast<BYTE>((translated >> 8) & 0xFF);
            post_key(hwnd, character_key, modifiers & 2, modifiers & 1, modifiers & 4);
        }
        if (field_int(request, "submit", action == "chat" ? 1 : 0)) {
            post_key(hwnd, VK_RETURN, false, false, false);
        }
        return std::string("{\"ok\":true,\"queued\":true,\"action\":")
            + json_string(action.c_str()) + ",\"characters\":" + std::to_string(value.size()) + '}';
    }
    if (action == "menu") {
        std::string choice = field_string(request, "choice");
        const char* names[] = {"start_game", "quick_start", "scenario", "load_game", "multiplayer",
            "options", "credits", "exit"};
        const int y1000[] = {265, 365, 465, 560, 655, 750, 845, 940};
        int pick = -1;
        for (int i = 0; i < 8; ++i) if (choice == names[i]) pick = i;
        if (pick < 0) return error_response("bad_choice", "Unknown main-menu choice.");
        RECT rect = {};
        GetClientRect(hwnd, &rect);
        int x = (rect.right - rect.left) * 855 / 1000;
        int y = (rect.bottom - rect.top) * y1000[pick] / 1000;
        LPARAM point = MAKELPARAM(x, y);
        PostMessage(hwnd, WM_MOUSEMOVE, 0, point);
        PostMessage(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, point);
        PostMessage(hwnd, WM_LBUTTONUP, 0, point);
        center_pointer(hwnd);
        return std::string("{\"ok\":true,\"queued\":true,\"action\":\"menu\",\"choice\":")
            + json_string(choice.c_str()) + '}';
    }
    bool ctrl = field_int(request, "ctrl", 0) != 0;
    bool shift = field_int(request, "shift", 0) != 0;
    bool alt = field_int(request, "alt", 0) != 0;
    UINT vk = 0;
    if (action == "key") {
        std::string key = field_string(request, "key");
        std::transform(key.begin(), key.end(), key.begin(), ::toupper);
        vk = named_key(key);
    } else if (action == "end_turn") {
        vk = VK_RETURN;
        ctrl = true;
    } else if (action == "skip_unit") {
        vk = VK_SPACE;
    } else if (action == "hold_unit") {
        vk = 'H';
    } else if (action == "sentry_unit") {
        vk = 'L';
    } else if (action == "move") {
        std::string direction = field_string(request, "direction");
        if (direction == "N") vk = VK_NUMPAD8;
        else if (direction == "NE") vk = VK_NUMPAD9;
        else if (direction == "E") vk = VK_NUMPAD6;
        else if (direction == "SE") vk = VK_NUMPAD3;
        else if (direction == "S") vk = VK_NUMPAD2;
        else if (direction == "SW") vk = VK_NUMPAD1;
        else if (direction == "W") vk = VK_NUMPAD4;
        else if (direction == "NW") vk = VK_NUMPAD7;
    }
    if (!vk) return error_response("bad_action", "Unsupported action or missing action argument.");
    if ((action == "move" || action == "end_turn" || action == "skip_unit"
        || action == "hold_unit" || action == "sentry_unit") && game_active()) {
        int faction_id = *CurrentPlayerFaction;
        if (*CurrentFaction != faction_id || *WinModalState || *PopupDialogState) {
            return error_response("not_actionable", "Wait until observation.ui.can_act is true.");
        }
    }
    post_key(hwnd, vk, ctrl, shift, alt);
    return std::string("{\"ok\":true,\"queued\":true,\"action\":") + json_string(action.c_str()) + '}';
}

#endif

std::string execute_request(const std::string& request) {
    if (field_string(request, "token") != auth_token) return error_response("unauthorized", "Invalid bridge token.");
    apply_deferred_semantics();
    std::string op = field_string(request, "op");
    if (op == "ping" || op == "status") return status_response();
    if (op == "human_ui_state") return human_ui_state_response();
    if (op == "human_ui_control") return human_ui_control_response(request);
    if (op == "observe") return observe_response();
    if (op == "observation_feed") return observation_feed_response(request);
    if (op == "semantic_identity_state") return semantic_identity_state_response(request);
    if (op == "list_bases") return bases_response();
    if (op == "list_units") return units_response();
    if (op == "list_factions") return factions_response();
    if (op == "list_technologies") return technologies_response();
    if (op == "test_technology_demand_status") {
        return test_technology_demand_status_response();
    }
    if (op == "test_nerve_gas_status") return test_nerve_gas_status_response();
    if (op == "test_self_destruct_status") return test_self_destruct_status_response();
    if (op == "test_full_endgame_status") return test_full_endgame_status_response();
    if (op == "test_network_sync_status") {
        return test_network_sync_status_response();
    }
    if (op == "test_social_engineering_fixture") {
        return test_social_engineering_fixture_response(request);
    }
    if (op == "test_lan_combat_fixture") {
        return test_lan_combat_fixture_response(request);
    }
    if (op == "test_lan_diplomacy_fixture") {
        return test_lan_diplomacy_fixture_response(request);
    }
    if (op == "test_lan_ai_contact_fixture") {
        return test_lan_ai_contact_fixture_response(request);
    }
    if (op == "test_airdrop_legality_fixture") {
        return test_airdrop_legality_fixture_response();
    }
    if (op == "test_identity_compaction_fixture") {
        return test_identity_compaction_fixture_response();
    }
    if (op == "list_tiles") return tiles_response(request);
    if (op == "perspective_world_page") return perspective_world_page_response(request);
    if (op == "semantic_snapshot") return semantic_snapshot_response();
    if (op == "semantic_chat") return semantic_chat_response(request);
    if (op == "semantic_lan") {
        return semantic_lan_host_response(request, false);
    }
    if (op == "test_lan_host_fixture") {
        return semantic_lan_host_response(request, true);
    }
    if (op == "test_chat_fixture") {
        char test_mode[8] = {};
        char test_chat[8] = {};
        if (!GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode,
            sizeof(test_mode)) || strcmp(test_mode, "1")
        || !GetEnvironmentVariableA("SMACX_AGENT_TEST_CHAT", test_chat,
            sizeof(test_chat)) || strcmp(test_chat, "1")) {
            return error_response("test_mode_disabled",
                "The contained chat fixture is disabled.");
        }
        std::string text = field_string(request, "text");
        int sender = field_int(request, "sender_faction_id", -1);
        if (!valid_chat_text(text) || sender < 1 || sender >= MaxPlayerNum) {
            return error_response("invalid_chat_fixture",
                "The fixture requires printable chat text and a player faction id.");
        }
        agent_chat_display(reinterpret_cast<void*>(0x7AE820), text.c_str(), sender);
        return semantic_chat_response("{\"action\":\"list\"}");
    }
    if (op == "action_status") {
        refresh_deferred_end_turn_state();
        return deferred_action_response(static_cast<uint32_t>(field_int(request, "action_id", 0)));
    }
    if (op == "semantic_choices") return semantic_choices_response(request);
    if (op == "semantic_command") {
        std::string response = semantic_command_response(request);
        if (response.compare(0, 10, "{\"ok\":true") == 0) {
            ++semantic_mutation_generation;
        }
        return response;
    }
    if (op == "act") {
        return error_response("raw_ui_disabled",
            "Mouse, keyboard, text-entry, and coordinate menu operations are disabled. Use semantic snapshot, choices, and commands only.");
    }
    return error_response("bad_operation", "Unknown operation.");
}

bool send_all(SOCKET socket, const std::string& value) {
    size_t sent = 0;
    while (sent < value.size()) {
        int count = send(socket, value.data() + sent, static_cast<int>(value.size() - sent), 0);
        if (count <= 0) return false;
        sent += count;
    }
    return true;
}

DWORD WINAPI server_worker(void*) {
    WSADATA data = {};
    if (WSAStartup(MAKEWORD(2, 2), &data) != 0) return 1;
    listen_socket = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listen_socket == INVALID_SOCKET) { WSACleanup(); return 2; }
    sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    char port_text[16] = {};
    DWORD port_len = GetEnvironmentVariableA("SMACX_AGENT_PORT", port_text, sizeof(port_text));
    int port = port_len ? atoi(port_text) : DefaultPort;
    address.sin_port = htons(static_cast<u_short>(port > 0 && port < 65536 ? port : DefaultPort));
    int exclusive = 1;
    setsockopt(listen_socket, SOL_SOCKET, SO_EXCLUSIVEADDRUSE,
        reinterpret_cast<const char*>(&exclusive), sizeof(exclusive));
    if (bind(listen_socket, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR
        || listen(listen_socket, 1) == SOCKET_ERROR) {
        closesocket(listen_socket);
        listen_socket = INVALID_SOCKET;
        WSACleanup();
        return 3;
    }
    while (!stopping) {
        SOCKET client = accept(listen_socket, NULL, NULL);
        if (client == INVALID_SOCKET) break;
        std::string buffer;
        char chunk[2048];
        while (!stopping) {
            int count = recv(client, chunk, sizeof(chunk), 0);
            if (count <= 0) break;
            buffer.append(chunk, chunk + count);
            if (buffer.size() > MaxRequestBytes) {
                send_all(client, error_response("request_too_large", "Maximum request size is 16384 bytes.") + "\n");
                break;
            }
            size_t newline = 0;
            while ((newline = buffer.find('\n')) != std::string::npos) {
                std::string request = buffer.substr(0, newline);
                buffer.erase(0, newline + 1);
                ResetEvent(response_event);
                uint64_t sequence = 0;
                EnterCriticalSection(&request_lock);
                sequence = ++pending_sequence;
                pending_request = request;
                pending_response.clear();
                request_pending = true;
                LeaveCriticalSection(&request_lock);
                InterlockedExchange(&request_getmessage_hits, 0);
                InterlockedExchange(&request_network_wait_hits, 0);
                InterlockedExchange(&request_modal_wait_hits, 0);
                InterlockedExchange(&request_handler_hits, 0);
                DWORD response_timeout = 5000;
                char test_mode[8] = {};
                bool test_lan_request = request.find(
                    "\"op\":\"test_lan_host_fixture\"") != std::string::npos;
                bool semantic_lan_request = request.find(
                    "\"op\":\"semantic_lan\"") != std::string::npos;
                if ((semantic_lan_request || (test_lan_request
                    && GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE", test_mode,
                        sizeof(test_mode)) && !strcmp(test_mode, "1")))) {
                    // DirectPlay's synchronous TCP/IP session enumeration may
                    // wait through multiple provider retry intervals even on
                    // localhost. Keep the bridge connection alive long enough
                    // to receive the authoritative result rather than turning
                    // a slow discovery into an uncertain operation.
                    response_timeout = 30000;
                }
                if (!PostMessage(game_window, WM_SMACX_AGENT, 0, 0)
                    || WaitForSingleObject(response_event, response_timeout)
                        != WAIT_OBJECT_0) {
                    std::string timeout_message = std::string(
                        "The game thread did not answer within the ")
                        + std::to_string(response_timeout / 1000)
                        + " second operation deadline. LAN diagnostic stage: "
                        + std::to_string(lan_test_completed_stage)
                        + "; dispatch hits getmessage="
                        + std::to_string(request_getmessage_hits)
                        + ", network_wait="
                        + std::to_string(request_network_wait_hits)
                        + ", modal_wait="
                        + std::to_string(request_modal_wait_hits)
                        + ", handler="
                        + std::to_string(request_handler_hits) + '.';
                    send_all(client, error_response(
                        "game_timeout", timeout_message.c_str()) + "\n");
                    continue;
                }
                EnterCriticalSection(&request_lock);
                bool response_matches = response_sequence == sequence;
                std::string response = response_matches ? pending_response : "";
                LeaveCriticalSection(&request_lock);
                if (!response_matches) {
                    send_all(client, error_response("superseded_request",
                        "This request completed after a newer re-entrant game request. Observe current state.") + "\n");
                    continue;
                }
                if (!send_all(client, response + "\n")) break;
            }
        }
        closesocket(client);
    }
    if (listen_socket != INVALID_SOCKET) closesocket(listen_socket);
    listen_socket = INVALID_SOCKET;
    WSACleanup();
    return 0;
}

} // namespace

void agent_observe_unit_destroyed(int veh_id) {
    if (!lock_initialized || !game_active() || veh_id < 0
    || veh_id >= *VehCount) return;
    const int perspective = *CurrentPlayerFaction;
    VEH& veh = Vehs[veh_id];
    // The VEH array is compacted after this hook for every destruction,
    // including units outside the current perspective.  Keep our private
    // identity/shadow arrays in exact lockstep even when fair-play rules mean
    // no observation event may be emitted for the destroyed row.
    ensure_semantic_vehicle_handles();
    if (perspective >= 1 && perspective < MaxPlayerNum
    && (veh.faction_id == perspective
        || (veh.visibility & (1 << perspective)))) {
        const int stable_handle = semantic_vehicle_handle(veh_id);
        const int tile_id = semantic_tile_id(veh.x, veh.y);
        append_observation_event("visible_unit_destroyed", *CurrentTurn,
            stable_handle, veh.faction_id, tile_id, tile_id,
            veh.cur_hitpoints(), 0, true);
    }
    // Mirror the native memmove that immediately follows this hook. Surviving
    // semantic handles and observation shadows retain their exact identity.
    if (veh_id < static_cast<int>(semantic_vehicle_handles.size()))
        semantic_vehicle_handles.erase(semantic_vehicle_handles.begin() + veh_id);
    if (veh_id < static_cast<int>(observed_vehicles.size()))
        observed_vehicles.erase(observed_vehicles.begin() + veh_id);
    if (veh_id < static_cast<int>(sampled_vehicles.size()))
        sampled_vehicles.erase(sampled_vehicles.begin() + veh_id);
}

void agent_observe_base_founded(int base_id) {
    if (!lock_initialized || !game_active() || base_id < 0
    || base_id >= *BaseCount) return;
    const int perspective = *CurrentPlayerFaction;
    const BASE& base = Bases[base_id];
    MAP* square = mapsq(base.x, base.y);
    if (perspective < 1 || perspective >= MaxPlayerNum
    || (base.faction_id != perspective
        && !(square && square->is_visible(perspective)))) return;
    const int tile_id = semantic_tile_id(base.x, base.y);
    append_observation_event("visible_base_founded", *CurrentTurn,
        base_id, base.faction_id, -1, tile_id, -1, base.pop_size, true);
}

void agent_observe_base_destroyed(int base_id) {
    if (!lock_initialized || !game_active() || base_id < 0
    || base_id >= *BaseCount) return;
    const int perspective = *CurrentPlayerFaction;
    const BASE& base = Bases[base_id];
    MAP* square = mapsq(base.x, base.y);
    if (perspective < 1 || perspective >= MaxPlayerNum
    || (base.faction_id != perspective
        && !(square && square->is_visible(perspective)))) return;
    const int tile_id = semantic_tile_id(base.x, base.y);
    append_observation_event("visible_base_destroyed", *CurrentTurn,
        base_id, base.faction_id, tile_id, tile_id, base.pop_size, 0, true);
    observed_bases.clear();
    semantic_observation_shadow_ready = false;
}

void agent_observe_base_captured(int base_id, int old_faction_id,
int new_faction_id) {
    if (!lock_initialized || !game_active() || base_id < 0
    || base_id >= *BaseCount) return;
    const int perspective = *CurrentPlayerFaction;
    const BASE& base = Bases[base_id];
    MAP* square = mapsq(base.x, base.y);
    if (perspective < 1 || perspective >= MaxPlayerNum
    || (old_faction_id != perspective && new_faction_id != perspective
        && !(square && square->is_visible(perspective)))) return;
    const int tile_id = semantic_tile_id(base.x, base.y);
    append_observation_event("visible_base_captured", *CurrentTurn,
        base_id, new_faction_id, tile_id, tile_id,
        old_faction_id, new_faction_id, true);
    observed_bases.clear();
    semantic_observation_shadow_ready = false;
}

void agent_set_probe_excuse_context(int offender_faction_id, int target_faction_id,
int action_id, bool framed, bool pact) {
    probe_excuse_context.valid = true;
    probe_excuse_context.offender_faction_id = offender_faction_id;
    probe_excuse_context.target_faction_id = target_faction_id;
    probe_excuse_context.action_id = action_id;
    probe_excuse_context.framed = framed;
    probe_excuse_context.pact = pact;
}

void agent_clear_probe_excuse_context() {
    probe_excuse_context = ProbeExcuseContext();
}

void agent_set_artifact_context(int unit_id, int base_id, int production_id,
int production_cost) {
    artifact_interaction.valid = true;
    artifact_interaction.unit_id = unit_id;
    artifact_interaction.base_id = base_id;
    artifact_interaction.production_id = production_id;
    artifact_interaction.production_cost = production_cost;
}

void agent_clear_artifact_context() {
    artifact_interaction = ArtifactInteractionContext();
}

void agent_begin_endgame_presentation(const char* phase) {
    endgame_presentation_phase = phase ? phase : "";
    pending_endgame_presentation_advance = false;
    ++endgame_presentation_generation;
}

void agent_end_endgame_presentation(const char* phase) {
    if (!phase || endgame_presentation_phase == phase) {
        endgame_presentation_phase.clear();
    }
    pending_endgame_presentation_advance = false;
    ++endgame_presentation_generation;
}

void __thiscall agent_chat_display(void* This, const char* text,
int sender_faction_id) {
    if (text && text[0] && sender_faction_id >= 1
    && sender_faction_id < MaxPlayerNum) {
        // The native display routine receives "<speaker>: <payload>" after
        // NetDaemon has formatted the faction/player name.  The semantic API
        // returns the authored payload; sender identity is already a separate
        // typed field and should not be duplicated into message text.
        const char* payload = strstr(text, ": ");
        append_chat_event(false, false, sender_faction_id, -1,
            payload ? payload + 2 : text);
    }
    typedef void(__thiscall *FNativeChatDisplay)(void*, const char*, int);
    reinterpret_cast<FNativeChatDisplay>(0x45C0B0)(
        This, text ? text : "", sender_faction_id);
}

int __thiscall agent_network_initialize(void* This, int mode) {
    // A semantic host/join has already completed the stock DirectPlay setup,
    // service selection, session open, and local-player join.  Consume that
    // handoff exactly once when multiplayer_init reaches its normal network
    // initialization call.  Clearing it here (rather than in the window
    // message which leaves TOPMENU) proves that the native startup path has
    // actually accepted the handoff.
    if (InterlockedCompareExchange(&lan_test_preconnected, 0, 1) == 1) {
        return 0;
    }
    typedef int(__thiscall *FNativeNetworkInitialize)(void*, int);
    return reinterpret_cast<FNativeNetworkInitialize>(0x52DF30)(This, mode);
}

int __thiscall agent_multiplayer_game_type_choice(
void* This, int left, int top, void* callback) {
    if (InterlockedCompareExchange(&lan_pending_load_active, 1, 1) == 1) {
        InterlockedExchange(&lan_pending_load_choice_seen, 1);
        // The stock list is New Game, Scenario, Multiplayer Scenario, Load.
        // Returning the requested semantic index preserves the surrounding native
        // NetWindow logic while bypassing only the modal selector.
        return lan_pending_load_game_type;
    }
    typedef int(__thiscall *FNativeGameTypeChoice)(
        void*, int, int, void*);
    return reinterpret_cast<FNativeGameTypeChoice>(0x59D250)(
        This, left, top, callback);
}

int __cdecl agent_multiplayer_load_game(int save_mode, int flags) {
    if (InterlockedCompareExchange(&lan_pending_load_active, 1, 1) == 1) {
        int status = lan_pending_load_path.empty()
            ? SAVE_LOAD_NONE
            : mod_load_daemon(lan_pending_load_path.c_str(), flags);
        if (status == SAVE_LOAD_VALID) {
            // The modal selector normally updates both its NetWindow backing
            // field and the shared type value before returning. Our semantic
            // selection bypasses that presentation, so preserve precisely the
            // same bookkeeping before the stock handler publishes packet
            // 0x2F03 and rebuilds the loaded-faction lobby.
            *reinterpret_cast<int*>(reinterpret_cast<char*>(NetWin) + 0x7728)
                = lan_pending_load_game_type;
            *reinterpret_cast<int*>(0x90E778) = lan_pending_load_game_type;
        }
        InterlockedExchange(&lan_pending_load_native_status, status);
        return status;
    }
    return load_game(save_mode, flags);
}

int __thiscall agent_multiplayer_faction_choice_result(
void* This, int left, int top, void* callback) {
    if (InterlockedCompareExchange(&lan_pending_faction_choice_active, 1, 1) == 1) {
        // NetWindow::choose_faction builds this source list in ascending
        // record order, omitting disabled records and factions already held
        // by a lobby participant. Return that list's zero-based row directly
        // at its one call site. This avoids entering any presentation/modal
        // lifecycle while leaving the stock record mapping, validation,
        // setup mutation, and 0x2F04 publication untouched.
        int required = static_cast<int>(lan_pending_faction_choice_id);
        if (!lan_faction_choice_selectable(required)) {
            InterlockedExchange(&lan_pending_faction_selector_result, -1);
            return -1;
        }
        // Any in-range row reaches the immediately adjacent guarded mapping
        // hook. That hook supplies the authoritative record directly because
        // this presentation-free path intentionally never builds the modal's
        // private result table.
        InterlockedExchange(&lan_pending_faction_choice_seen, 1);
        InterlockedExchange(&lan_pending_faction_selector_result, 0);
        return 0;
    }
    typedef int(__thiscall *FNativeFactionChoiceResult)(
        void*, int, int, void*);
    return reinterpret_cast<FNativeFactionChoiceResult>(0x59D250)(
        This, left, top, callback);
}

int __cdecl agent_multiplayer_faction_map_result(
int selector_result, const unsigned char* native_frame) {
    if (InterlockedCompareExchange(&lan_pending_faction_choice_active, 1, 1) == 1) {
        // The stock mapping produces a one-based record id, then the caller
        // decrements it before validation. Supply exactly that representation.
        int required = static_cast<int>(lan_pending_faction_choice_id);
        return lan_faction_choice_selectable(required) ? required + 1 : -1;
    }
    if (!native_frame || selector_result < 0 || selector_result >= 64) {
        return -1;
    }
    // Original instruction at 0x47C721:
    // mov ebx,[ebp + selector_result*20 - 0x848]
    return *reinterpret_cast<const int*>(
        native_frame + selector_result * 20 - 0x848);
}

int __thiscall agent_multiplayer_faction_validation(void* This, int flags) {
    if (InterlockedCompareExchange(&lan_pending_faction_choice_active, 1, 1) == 1) {
        int local_index = lan_local_player_index();
        int observed = local_index >= 1
            ? static_cast<signed char>(lan_setup_record(local_index)[3]) : -1;
        InterlockedExchange(&lan_pending_faction_choice_before_validation, observed);
        // A wrong selector row must never reach native validation: returning
        // false makes NetWindow::choose_faction restore the prior setup record.
        if (observed != static_cast<int>(lan_pending_faction_choice_id)) {
            InterlockedExchange(&lan_pending_faction_validation_result, 0);
            return 0;
        }
    }
    typedef int(__thiscall *FNativeFactionValidation)(void*, int);
    int result = reinterpret_cast<FNativeFactionValidation>(0x47C970)(This, flags);
    if (InterlockedCompareExchange(&lan_pending_faction_choice_active, 1, 1) == 1) {
        InterlockedExchange(&lan_pending_faction_validation_result, result);
    }
    return result;
}

BOOL __stdcall agent_multiplayer_manifest_find_next(
HANDLE search_handle, WIN32_FIND_DATAA* find_data,
const unsigned char* native_frame) {
    // NetDaemon_send_files stores the number of completed 0x0F06 records at
    // EBP-0x24. Each record is 0x11c bytes, its index is serialized as one
    // byte, and the stock allocation is exactly 0x11c00 bytes. Steam's SMACX
    // distribution has more than 256 root files, so the unmodified loop asks
    // for record 257 and writes beyond that allocation before sending it.
    // Ending enumeration here preserves the native protocol and its cleanup
    // path instead of inventing wrapped record identifiers.
    if (!native_frame || !find_data || search_handle == INVALID_HANDLE_VALUE) {
        return FALSE;
    }
    int completed_records = *reinterpret_cast<const int*>(native_frame - 0x24);
    if (completed_records >= 256) {
        debug("NetDaemon_send_files: capped loaded-game manifest at 256 records\n");
        return FALSE;
    }
    return FindNextFileA(search_handle, find_data);
}

void __thiscall agent_new_technology_presentation(void* This,
int technology_id, int source_faction_id) {
    if (*MultiplayerActive && lock_initialized
    && technology_id >= 0 && technology_id < MaxTechnologyNum
    && Tech[technology_id].name[0]) {
        // Stock NetTechWindow can enter a private loop that excludes the
        // bridge's main-window message. The technology has already been
        // awarded before this presentation call. Queue only its public local
        // identity and let the semantic client acknowledge it in order;
        // never touch the transfer, ownership bits, or DirectPlay packet.
        if (pending_multiplayer_technology_presentations.size() < 16) {
            pending_multiplayer_technology_presentations.push_back(
                technology_id);
            ++semantic_mutation_generation;
        }
        return;
    }
    typedef void(__thiscall *FNativeNewTechnologyPresentation)(
        void*, int, int);
    reinterpret_cast<FNativeNewTechnologyPresentation>(0x483F90)(
        This, technology_id, source_faction_id);
}

void agent_bridge_start_once(HWND hwnd) {
    game_window = hwnd;
    if (InterlockedCompareExchange(&started, 1, 0) != 0) return;
    char enabled[8] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_ENABLE", enabled, sizeof(enabled)) || strcmp(enabled, "1")) return;
    char token[256] = {};
    if (!GetEnvironmentVariableA("SMACX_AGENT_TOKEN", token, sizeof(token)) || strlen(token) < 16) return;
    auth_token = token;
    char session_id[128] = {};
    char match_id[128] = {};
    GetEnvironmentVariableA("SMACX_AGENT_SESSION_ID", session_id, sizeof(session_id));
    GetEnvironmentVariableA("SMACX_AGENT_MATCH_ID", match_id, sizeof(match_id));
    char controller_kind[16] = {};
    GetEnvironmentVariableA("SMACX_CONTROLLER_KIND", controller_kind,
        sizeof(controller_kind));
    managed_human_controller = !strcmp(controller_kind, "human");
    agent_session_id = session_id[0] ? session_id : "unmanaged-session";
    agent_match_id = match_id[0] ? match_id : "unmanaged-match";
    InitializeCriticalSection(&request_lock);
    lock_initialized = true;
    response_event = CreateEventA(NULL, TRUE, FALSE, NULL);
    if (!response_event) return;
    request_getmessage_hook = SetWindowsHookExA(
        WH_GETMESSAGE, agent_request_getmessage_hook, NULL,
        GetCurrentThreadId());
    semantic_observation_timer_id = SetTimer(
        NULL, 0, 50, semantic_observation_timer_proc);
    worker_thread = CreateThread(NULL, 0, server_worker, NULL, 0, NULL);
}

bool agent_bridge_handle_message(HWND hwnd, UINT msg) {
    if (msg == WM_SMACX_AGENT_DEFERRED) {
        if (lan_test_lobby_pending) {
            // semantic_lan(host/join) is requested while TOPMENU's SetupWin
            // owns the native modal loop.  Calling multiplayer_init directly
            // from this message would be re-entrant: the game could start,
            // but TOPMENU would remain the modal owner underneath it and keep
            // GameHalted/WinModalState latched.  A real user instead selects
            // TOPMENU item 4; SetupWin releases its modal loop, top_menu sees
            // that semantic item, and only then calls multiplayer_init.
            //
            // Reproduce that native control-flow transition without pixels,
            // coordinates, keys, or synthetic input.  Fail closed unless the
            // exact TOPMENU SetupWin and its source Popup are both present.
            SetupWin* setup = reinterpret_cast<SetupWin*>(*ModalStackCurrent);
            BasePop* source_popup = agent_popup_object();
            BasePop* setup_source_popup = setup
                ? *reinterpret_cast<BasePop**>(
                    reinterpret_cast<char*>(setup) + 0x1014)
                : NULL;
            int* setup_item_count = setup
                ? reinterpret_cast<int*>(
                    reinterpret_cast<char*>(setup) + 0xFCC)
                : NULL;
            int* setup_selection = setup
                ? reinterpret_cast<int*>(
                    reinterpret_cast<char*>(setup) + 0xFD0)
                : NULL;
            const bool exact_top_menu = setup
                && setup->vtable == reinterpret_cast<void*>(0x66D8E8)
                && !strcmp(agent_popup_last_started_label(), "TOPMENU")
                && setup_source_popup == source_popup
                && *setup_item_count > 4;
            if (exact_top_menu) {
                InterlockedExchange(&lan_test_lobby_pending, 0);
                *setup_selection = 4;
                Win_release_modal(reinterpret_cast<Win*>(setup));
            }
            return true;
        }
        if (deferred_corner_market_notice) {
            deferred_corner_market_notice = false;
            if (game_active()) {
                popp(ScriptFile, "CORNERING", 0, "econwin_sm.pcx", 0);
            }
            return true;
        }
        if (test_full_endgame_fixture_pending) {
            test_full_endgame_fixture_pending = false;
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            if (human_turn_actionable(faction_id)) {
                // Exercise either the passive score stack directly or its
                // optional economic-victory narrative prefix.
                if (test_full_endgame_narrative) {
                    *GamePreferences &= ~PREF_AV_INTERLUDES_DISABLED;
                    *GameVictoryType = VIC_ECONOMIC_SOLO;
                    Factions[faction_id].player_flags |= PFLAG_UNK_40000;
                    *GameState |= STATE_VICTORY_ECONOMIC;
                } else {
                    *GamePreferences |= PREF_AV_INTERLUDES_DISABLED;
                    *GameVictoryType = VIC_TRANSCEND_PLR;
                }
                *GameState |= STATE_GAME_DONE;
                *GameState &= ~STATE_FINAL_SCORE_DONE;
                end_of_game(0);
                // The native main loop clears these globals almost
                // immediately after the finish response. Capture them at the
                // production call boundary so the contained regression does
                // not depend on a polling race with that cleanup.
                test_full_endgame_final_score_done =
                    (*GameState & STATE_FINAL_SCORE_DONE) != 0;
                test_full_endgame_control_turn_a = *ControlTurnA;
                test_full_endgame_control_turn_b = *ControlTurnB;
                test_full_endgame_result_captured = true;
            }
            return true;
        }
        if (test_endgame_fixture_stage >= 0) {
            int stage = test_endgame_fixture_stage;
            test_endgame_fixture_stage = -1;
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            if (human_turn_actionable(faction_id)) {
                if (stage == 0) {
                    popp(ScriptFile, "ACCEDE", 0, "dipvic_sm.pcx", 0);
                    test_endgame_fixture_stage = 1;
                    PostMessage(game_window, WM_SMACX_AGENT_DEFERRED, 0, 0);
                } else {
                    popp(ScriptFile, "GAMEOVERMAN", 0, "stars_sm.pcx", 0);
                }
            }
            return true;
        }
        if (test_energy_gift_fixture_stage == 0) {
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            test_energy_gift_fixture_stage = 1;
            if (faction_id >= 1 && test_energy_gift_other_id >= 1
            && human_turn_actionable(faction_id)) {
                *diplo_second_faction = test_energy_gift_other_id;
                *diplo_current_proposal_id = DiploProposalMakeGift;
                test_energy_gift_menu_timer_ticks = 0;
                test_energy_gift_menu_timer_id = SetTimer(
                    NULL, 0, 25, test_energy_gift_menu_timer_proc);
                X_dialog("COUNTER1", test_energy_gift_other_id);
            }
            return true;
        }
        if (test_proposal_guard_fixture_stage == 0) {
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            test_proposal_guard_fixture_stage = 1;
            if (faction_id >= 1 && test_proposal_guard_other_id >= 1
            && human_turn_actionable(faction_id)) {
                *diplo_second_faction = test_proposal_guard_other_id;
                test_proposal_guard_menu_timer_ticks = 0;
                test_proposal_guard_menu_timer_id = SetTimer(
                    NULL, 0, 25, test_proposal_guard_menu_timer_proc);
                X_dialog("PROPOSAL", test_proposal_guard_other_id);
            }
            return true;
        }
        if (test_incoming_vote_offer_fixture_stage == 0) {
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            test_incoming_vote_offer_fixture_stage = 1;
            if (faction_id >= 1 && test_incoming_vote_offer_other_id >= 1
            && human_turn_actionable(faction_id)) {
                parse_gen_name(faction_id, 0, 1);
                parse_gen_name(test_incoming_vote_offer_other_id, 2, 3);
                int accepted = 0;
                if (test_incoming_vote_offer_technologies) {
                    parse_says(4, Tech[*diplo_tech_id1].name, -1, -1);
                    parse_says(5, Tech[*diplo_vote_offer_tech_id2].name, -1, -1);
                    accepted = X_dialog("VOTEFORMETECH", test_incoming_vote_offer_other_id);
                } else {
                    parse_num(0, 125);
                    accepted = X_dialog("VOTEFORME", test_incoming_vote_offer_other_id);
                }
                // The production dialog is owned by the native Council method at
                // 0x427D63..0x4282E2, which performs this continuation after
                // X_dialog returns. This contained fixture opened only the real
                // dialog, so reproduce the disassembled caller's exact payment
                // continuation with native tech_achieved/treasury state. Never
                // compile or activate this path outside explicit test mode.
                if (accepted == 1 && test_incoming_vote_offer_technologies) {
                    tech_achieved(faction_id, *diplo_tech_id1,
                        test_incoming_vote_offer_other_id, 0);
                    tech_achieved(faction_id, *diplo_vote_offer_tech_id2,
                        test_incoming_vote_offer_other_id, 0);
                } else if (accepted == 1) {
                    Factions[test_incoming_vote_offer_other_id].energy_credits -= 125;
                    Factions[faction_id].energy_credits += 125;
                    draw_map(1);
                }
            }
            return true;
        }
        if (test_joint_attack_counteroffer_fixture_stage == 0) {
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            test_joint_attack_counteroffer_fixture_stage = 1;
            if (faction_id >= 1 && test_joint_attack_counteroffer_other_id >= 1
            && test_joint_attack_counteroffer_target_id >= 1
            && human_turn_actionable(faction_id)) {
                *diplo_second_faction = test_joint_attack_counteroffer_other_id;
                *diplo_trade_faction_id = test_joint_attack_counteroffer_target_id;
                *diplo_counter_proposal_id = 2;
                *diplo_entry_id = -1;
                *diplo_tech_id2 = -1;
                *diplo_tech_id3 = -1;
                *diplo_tech_id4 = -1;
                *DiploFriction = 0;
                propose_attack(faction_id, test_joint_attack_counteroffer_other_id);
            }
            return true;
        }
        if (test_technology_demand_fixture_stage == 0) {
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            test_technology_demand_fixture_stage = 1;
            if (faction_id >= 1 && test_technology_demand_other_id >= 1
            && human_turn_actionable(faction_id)) {
                *diplo_second_faction = test_technology_demand_other_id;
                *diplo_entry_id = test_technology_demand_ids[0];
                *diplo_tech_id2 = test_technology_demand_ids[1];
                *diplo_tech_id3 = test_technology_demand_ids[2];
                *diplo_tech_id4 = test_technology_demand_ids[3];
                *diplo_tech_id1 = test_technology_demand_distractor_id;
                parse_gen_name(faction_id, 0, 1);
                parse_gen_name(test_technology_demand_other_id, 2, 3);
                parse_says(0, Tech[test_technology_demand_ids[0]].name, -1, -1);
                parse_says(6, Tech[test_technology_demand_ids[1]].name, -1, -1);
                parse_says(8, Tech[test_technology_demand_ids[2]].name, -1, -1);
                parse_says(9, Tech[test_technology_demand_ids[3]].name, -1, -1);
                parse_num(0, 125);
                const char* initial_label = test_technology_demand_fixture_mode
                    ? "DEMANDTECH9A" : "DEMANDTECH15";
                int accepted = X_dialog(initial_label, test_technology_demand_other_id);
                test_technology_demand_initial_result = accepted;
                if (test_technology_demand_fixture_mode == 1 && accepted == 2) {
                    accepted = X_dialog("DEMANDTECHAGAIN1",
                        test_technology_demand_other_id);
                    test_technology_demand_followup_result = accepted;
                } else if (test_technology_demand_fixture_mode == 2 && accepted == 3) {
                    accepted = X_dialog("DEMANDTECHAGAIN2",
                        test_technology_demand_other_id);
                    test_technology_demand_followup_result = accepted;
                }
                if (accepted == 1) {
                    // The production dialog is owned by the native diplomacy
                    // method around 0x5543E2, which transfers every Script-
                    // named bundle member after X_dialog returns. This test-
                    // only caller reproduces that exact continuation.
                    int count = test_technology_demand_fixture_mode ? 1 : 4;
                    for (int index = 0; index < count; ++index) {
                        tech_achieved(test_technology_demand_other_id,
                            test_technology_demand_ids[index], faction_id, 0);
                    }
                }
                test_technology_demand_fixture_stage = 2;
            }
            return true;
        }
        if (test_diplomatic_purchase_fixture_stage == 1
        || test_diplomatic_purchase_fixture_stage == 3) {
            int stage = test_diplomatic_purchase_fixture_stage;
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            if (faction_id >= 1 && human_turn_actionable(faction_id)
            && test_diplomatic_purchase_seller_id >= 1) {
                *diplo_second_faction = test_diplomatic_purchase_seller_id;
                *DiploFriction = 0;
                if (stage == 1 && test_diplomatic_purchase_prototype_id >= 0) {
                    *diplo_prototype_id = test_diplomatic_purchase_prototype_id;
                    buy_tech(faction_id, test_diplomatic_purchase_seller_id,
                        DiploCounterEnergyPayment, 0, DiploProposalBuyTech);
                    test_diplomatic_purchase_fixture_stage = 2;
                } else if (stage == 3 && test_diplomatic_purchase_target_id >= 1) {
                    *diplo_tech_id1 = test_diplomatic_purchase_target_id + 89;
                    *diplo_trade_faction_id = test_diplomatic_purchase_target_id;
                    buy_tech(faction_id, test_diplomatic_purchase_seller_id,
                        DiploCounterEnergyPayment, 0, DiploProposalTradeCommlink);
                    test_diplomatic_purchase_fixture_stage = 4;
                }
            }
            return true;
        }
        if (test_base_purchase_fixture_stage == 0) {
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            test_base_purchase_fixture_stage = 1;
            if (faction_id >= 1 && test_base_purchase_seller_id >= 1
            && test_base_purchase_base_id >= 0 && human_turn_actionable(faction_id)) {
                *diplo_second_faction = test_base_purchase_seller_id;
                *diplo_ask_base_swap_id = test_base_purchase_base_id;
                *diplo_current_proposal_id = DiploProposalBaseSwap;
                *diplo_counter_proposal_id = DiploCounterEnergyPayment;
                *DiploFriction = 0;
                mod_base_swap(faction_id, test_base_purchase_seller_id);
            }
            return true;
        }
        if (test_council_bargain_fixture_stage == 0) {
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            test_council_bargain_fixture_stage = 1;
            if (faction_id >= 1 && test_council_bargain_other_id >= 1
            && human_turn_actionable(faction_id)) {
                *diplo_second_faction = test_council_bargain_other_id;
                *DiploFriction = 0;
                buy_council_vote(faction_id, test_council_bargain_other_id,
                    PROP_GLOBAL_TRADE_PACT, -1);
            }
            return true;
        }
        if (test_base_status_fixture_base_id >= 0) {
            int base_id = test_base_status_fixture_base_id;
            test_base_status_fixture_base_id = -1;
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            if (base_id >= 0 && base_id < *BaseCount
            && Bases[base_id].faction_id == faction_id
            && human_turn_actionable(faction_id)) {
                set_base(base_id);
                popb("STARVE", WARN_STOP_STARVATION, 14, "starv_sm.pcx", 0);
            }
            return true;
        }
        if (test_commerce_fixture_other_faction_id >= 0) {
            int other = test_commerce_fixture_other_faction_id;
            test_commerce_fixture_other_faction_id = -1;
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            if (faction_id >= 1 && other >= 1 && other < MaxPlayerNum
            && other != faction_id && human_turn_actionable(faction_id)) {
                *diplo_second_faction = other;
                *DiploFriction = 0;
                mod_buy_tech(faction_id, other, DiploCounterEnergyPayment,
                    0, DiploProposalTechTrade);
            }
            return true;
        }
        if (test_loan_fixture_other_faction_id >= 0) {
            int other = test_loan_fixture_other_faction_id;
            test_loan_fixture_other_faction_id = -1;
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            if (faction_id >= 1 && other >= 1 && other < MaxPlayerNum
            && other != faction_id && human_turn_actionable(faction_id)) {
                *diplo_second_faction = other;
                *diplo_current_proposal_id = DiploProposalNeedEnergy;
                *diplo_counter_proposal_id = DiploCounterLoanPayment;
                *DiploFriction = 0;
                mod_energy_trade(faction_id, other);
            }
            return true;
        }
        if (deferred_destroy_unit_id >= 0) {
            int veh_id = deferred_destroy_unit_id;
            int former_id = deferred_destroy_former_id;
            int expected_owner = deferred_destroy_owner_id;
            bool hostility_confirmed = deferred_destroy_hostility_confirmed;
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            bool attempted = false;
            bool removed = false;
            uint32_t improvement_bit = 0;
            if (!*MultiplayerActive && faction_id >= 1
            && veh_id >= 0 && veh_id < *VehCount
            && Vehs[veh_id].faction_id == faction_id
            && terrain_destruction_unit_eligible(veh_id)
            && human_turn_actionable(faction_id)) {
                VEH& veh = Vehs[veh_id];
                MAP* sq = mapsq(veh.x, veh.y);
                int owner = whose_territory(faction_id, veh.x, veh.y, 0, 0);
                bool foreign = owner >= 1 && owner != faction_id;
                bool pact = foreign && has_treaty(faction_id, owner, DIPLO_PACT);
                bool hostility_required = foreign
                    && !has_treaty(faction_id, owner, DIPLO_VENDETTA);
                if (sq && owner == expected_owner && !pact
                && (!hostility_required || hostility_confirmed)
                && terrain_destruction_item_available(sq->items, former_id)) {
                    attempted = true;
                    improvement_bit = Terraform[former_id].bit;
                    if (hostility_required) double_cross(faction_id, owner, -1);
                    action_destroy(veh_id, improvement_bit, -1, -1);
                    MAP* observed_sq = veh_id < *VehCount ? mapsq(
                        Vehs[veh_id].x, Vehs[veh_id].y) : NULL;
                    removed = observed_sq && !(observed_sq->items & improvement_bit);
                    if (veh_id < *VehCount) {
                        deferred_action.observed_x = Vehs[veh_id].x;
                        deferred_action.observed_y = Vehs[veh_id].y;
                    }
                }
            }
            deferred_destroy_unit_id = -1;
            deferred_destroy_former_id = -1;
            deferred_destroy_owner_id = -1;
            deferred_destroy_hostility_confirmed = false;
            deferred_action.native_result = removed ? 1 : 0;
            deferred_action.status = removed ? "completed" : "rejected";
            deferred_action.resolution = removed
                ? "native_terrain_improvement_destroyed"
                : (attempted ? "native_failure" : "state_changed_before_execution");
            return true;
        }
        if (deferred_obliterate_base_id >= 0) {
            int base_id = deferred_obliterate_base_id;
            int unit_id = deferred_obliterate_unit_id;
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            bool attempted = false;
            int base_count_before = *BaseCount;
            int base_x = -1;
            int base_y = -1;
            if (!*MultiplayerActive && faction_id >= 1
            && base_id >= 0 && base_id < *BaseCount
            && Bases[base_id].faction_id == faction_id && !is_objective(base_id)
            && unit_id >= 0 && unit_id < *VehCount
            && Vehs[unit_id].faction_id == faction_id
            && Vehs[unit_id].x == Bases[base_id].x
            && Vehs[unit_id].y == Bases[base_id].y
            && human_turn_actionable(faction_id)) {
                attempted = true;
                base_x = Bases[base_id].x;
                base_y = Bases[base_id].y;
                for (int safe_base_id = 0; safe_base_id < *BaseCount; ++safe_base_id) {
                    if (safe_base_id != base_id
                    && Bases[safe_base_id].faction_id == faction_id) {
                        set_base(safe_base_id);
                        base_compute(1);
                        break;
                    }
                }
                MapWin->iUnit = unit_id;
                *CurrentVehID = unit_id;
                active_obliterate_base_id = base_id;
                active_obliterate_unit_id = unit_id;
                active_obliterate_decision = -1;
                Console_oblit(MapWin, unit_id);
            }
            bool removed = attempted && *BaseCount == base_count_before - 1
                && base_at(base_x, base_y) < 0;
            bool cancelled = attempted && active_obliterate_decision == 0 && !removed;
            deferred_obliterate_base_id = -1;
            deferred_obliterate_unit_id = -1;
            active_obliterate_base_id = -1;
            active_obliterate_unit_id = -1;
            active_obliterate_decision = -1;
            deferred_action.native_result = removed ? 1 : 0;
            deferred_action.status = removed || cancelled ? "completed" : "rejected";
            deferred_action.resolution = removed ? "native_base_obliterated"
                : cancelled ? "cancelled_by_player" : (attempted ? "native_failure" : "");
            return true;
        }
        if (deferred_nerve_staple_base_id >= 0) {
            int base_id = deferred_nerve_staple_base_id;
            deferred_nerve_staple_base_id = -1;
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            bool attempted = false;
            int turns_before = 0;
            if (faction_id >= 1 && base_id >= 0 && base_id < *BaseCount
            && Bases[base_id].faction_id == faction_id
            && !Bases[base_id].nerve_staple_turns_left
            && can_staple(base_id) && human_turn_actionable(faction_id)) {
                attempted = true;
                turns_before = Bases[base_id].nerve_staple_turns_left;
                set_base(base_id);
                action_staple(base_id);
                set_base(base_id);
                base_compute(1);
            }
            bool effective = attempted
                && Bases[base_id].nerve_staple_turns_left > turns_before;
            deferred_action.native_result = effective ? 1 : 0;
            deferred_action.status = attempted ? "completed" : "rejected";
            deferred_action.resolution = effective ? "stapled"
                : (attempted ? "native_failure" : "");
            return true;
        }
        if (deferred_missile_unit_id >= 0) {
            int veh_id = deferred_missile_unit_id;
            int target_x = deferred_missile_x;
            int target_y = deferred_missile_y;
            deferred_missile_unit_id = -1;
            deferred_missile_x = -1;
            deferred_missile_y = -1;
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            bool attempted = false;
            int native_result = 0;
            std::string kind;
            std::string reason;
            if (!*MultiplayerActive && faction_id >= 1
            && missile_target_legal(faction_id, veh_id, target_x, target_y, &reason)
            && human_turn_actionable(faction_id)) {
                attempted = true;
                kind = missile_kind(Vehs[veh_id]);
                MapWin->iUnit = veh_id;
                *CurrentVehID = veh_id;
                if (kind == "planet_buster") {
                    planet_busting(veh_id, target_x, target_y);
                    native_result = 1;
                } else if (kind == "tectonic") {
                    action_tectonic(veh_id, target_x, target_y);
                    native_result = 1;
                } else if (kind == "fungal") {
                    action_fungal(veh_id, target_x, target_y);
                    native_result = 1;
                } else {
                    *VehAttackFlags = 2;
                    native_result = mod_battle_fight_2(
                        veh_id, 0, target_x, target_y, 0, 1, NULL);
                }
            }
            deferred_action.native_result = native_result;
            deferred_action.observed_x = -1;
            deferred_action.observed_y = -1;
            deferred_action.status = attempted ? "completed" : "rejected";
            deferred_action.resolution = attempted
                ? std::string("native_") + kind + "_resolved"
                : reason;
            return true;
        }
        if (deferred_probe_unit_id >= 0) {
            int veh_id = deferred_probe_unit_id;
            int target_base_id = deferred_probe_base_id;
            int target_unit_id = deferred_probe_target_unit_id;
            int target_x = deferred_action.target_x;
            int target_y = deferred_action.target_y;
            int action_id = deferred_probe_action_id;
            int frame_faction_id = deferred_probe_frame_faction_id;
            bool enhanced = deferred_probe_enhanced;
            deferred_probe_unit_id = -1;
            deferred_probe_base_id = -1;
            deferred_probe_target_unit_id = -1;
            deferred_probe_action_id = -1;
            deferred_probe_frame_faction_id = 0;
            deferred_probe_enhanced = false;
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            bool attempted = false;
            int native_result = 0;
            active_probe_unit_id = -1;
            active_probe_base_id = -1;
            active_probe_abort_requested = false;
            if (faction_id >= 1 && veh_id >= 0 && veh_id < *VehCount
            && target_unit_id >= 0 && target_unit_id < *VehCount
            && Vehs[veh_id].faction_id == faction_id && Vehs[veh_id].is_probe()
            && veh_unmoved(veh_id) && Vehs[target_unit_id].x == target_x
            && Vehs[target_unit_id].y == target_y
            && Vehs[target_unit_id].faction_id != faction_id
            && !has_pact(faction_id, Vehs[target_unit_id].faction_id)
            && (Vehs[target_unit_id].visibility & (1 << faction_id))
            && probe_unit_mind_control_cost(veh_id, target_unit_id) >= 0
            && Factions[faction_id].energy_credits
                >= probe_unit_mind_control_cost(veh_id, target_unit_id)
            && human_turn_actionable(faction_id)) {
                bool adjacent = false;
                for (int dir = 0; dir < 8; ++dir) {
                    adjacent |= wrap(Vehs[veh_id].x + BaseOffsetX[dir]) == target_x
                        && Vehs[veh_id].y + BaseOffsetY[dir] == target_y;
                }
                if (adjacent) {
                    attempted = true;
                    Vehs[veh_id].probe_action = static_cast<uint8_t>(enhanced ? 8 : 0);
                    *VehAttackFlags = 2;
                    native_result = probe(veh_id, -1, target_unit_id, 1);
                }
            }
            if (target_unit_id >= 0) {
                deferred_action.native_result = native_result;
                deferred_action.observed_x = -1;
                deferred_action.observed_y = -1;
                deferred_action.status = attempted && native_result ? "completed" : "rejected";
                return true;
            }
            if (faction_id >= 1 && veh_id >= 0 && veh_id < *VehCount
            && Vehs[veh_id].faction_id == faction_id && Vehs[veh_id].is_probe()
            && veh_unmoved(veh_id) && target_base_id >= 0 && target_base_id < *BaseCount
            && base_at(target_x, target_y) == target_base_id
            && Bases[target_base_id].faction_id != faction_id
            && !has_pact(faction_id, Bases[target_base_id].faction_id)
            && (is_ocean(mapsq(Vehs[veh_id].x, Vehs[veh_id].y))
                == is_ocean(&Bases[target_base_id])
                || has_abil(Vehs[veh_id].unit_id, ABL_AMPHIBIOUS))
            && mapsq(target_x, target_y)->is_visible(faction_id)
            && action_id >= PRB_INFILTRATE_DATALINKS
            && action_id <= PRB_FREE_CAPTURED_FACTION_LEADER
            && human_turn_actionable(faction_id)) {
                bool adjacent = false;
                for (int dir = 0; dir < 8; ++dir) {
                    if (wrap(Vehs[veh_id].x + BaseOffsetX[dir]) == target_x
                    && Vehs[veh_id].y + BaseOffsetY[dir] == target_y) {
                        adjacent = true;
                        break;
                    }
                }
                if (!adjacent) {
                    deferred_action.native_result = 0;
                    deferred_action.status = "rejected";
                    return true;
                }
                attempted = true;
                bool staged_targeted_sabotage = enhanced
                    && action_id == PRB_ACTIVATE_SABOTAGE_VIRUS;
                bool staged_leader_rescue = action_id == PRB_FREE_CAPTURED_FACTION_LEADER;
                Vehs[veh_id].probe_action = static_cast<uint8_t>(
                    (staged_leader_rescue ? 8 : (action_id & 7))
                    | (enhanced ? 8 : 0) | (frame_faction_id * 32));
                if (staged_targeted_sabotage || staged_leader_rescue) {
                    active_probe_unit_id = veh_id;
                    active_probe_base_id = target_base_id;
                }
                *VehAttackFlags = (staged_targeted_sabotage || staged_leader_rescue) ? 6 : 2;
                native_result = probe(veh_id, target_base_id, -1, 1);
            }
            bool aborted_by_agent = active_probe_abort_requested;
            active_probe_unit_id = -1;
            active_probe_base_id = -1;
            active_probe_abort_requested = false;
            deferred_action.native_result = native_result;
            deferred_action.observed_x = -1;
            deferred_action.observed_y = -1;
            deferred_action.status = attempted && (native_result || aborted_by_agent)
                ? "completed" : "rejected";
            deferred_action.resolution = aborted_by_agent
                ? "aborted_by_agent" : (native_result ? "resolved_by_native_game" : "");
            return true;
        }
        if (deferred_move_unit_id >= 0) {
            int veh_id = deferred_move_unit_id;
            int direction = deferred_move_direction;
            int target_x = deferred_move_x;
            int target_y = deferred_move_y;
            deferred_move_unit_id = -1;
            deferred_move_direction = -1;
            deferred_move_x = -1;
            deferred_move_y = -1;
            int faction_id = game_active() ? *CurrentPlayerFaction : -1;
            bool attempted = false;
            int native_result = 0;
            int original_unit_id = -1;
            int original_moves_spent = -1;
            int original_damage_taken = -1;
            bool visible_combat_target = false;
            resolved_artifact_unit_id = -1;
            resolved_artifact_consumed = false;
            uint64_t target_fingerprint_before = 1469598103934665603ULL;
            auto visible_target_fingerprint = [&](int x, int y) {
                uint64_t hash = 1469598103934665603ULL;
                for (int target_id = veh_at(x, y); target_id >= 0;
                target_id = Vehs[target_id].next_veh_id_stack) {
                    VEH& target = Vehs[target_id];
                    if (target.faction_id == faction_id
                    || !(target.visibility & (1 << faction_id))) continue;
                    hash ^= static_cast<uint32_t>(target.faction_id);
                    hash *= 1099511628211ULL;
                    hash ^= static_cast<uint32_t>(target.unit_id + 1);
                    hash *= 1099511628211ULL;
                    hash ^= static_cast<uint32_t>(target.damage_taken + 1);
                    hash *= 1099511628211ULL;
                }
                return hash;
            };
            if (faction_id >= 1 && veh_id >= 0 && veh_id < *VehCount
            && Vehs[veh_id].faction_id == faction_id && veh_unmoved(veh_id)
            && direction >= 0 && direction < 8
            && wrap(Vehs[veh_id].x + BaseOffsetX[direction]) == target_x
            && Vehs[veh_id].y + BaseOffsetY[direction] == target_y
            && human_turn_actionable(faction_id)) {
                attempted = true;
                original_unit_id = Vehs[veh_id].unit_id;
                original_moves_spent = Vehs[veh_id].moves_spent;
                original_damage_taken = Vehs[veh_id].damage_taken;
                visible_combat_target = visible_hostile_at(
                    faction_id, target_x, target_y);
                int target_base_id = base_at(target_x, target_y);
                visible_combat_target |= target_base_id >= 0
                    && Bases[target_base_id].faction_id != faction_id
                    && !has_pact(faction_id, Bases[target_base_id].faction_id)
                    && mapsq(target_x, target_y)->is_visible(faction_id);
                target_fingerprint_before = visible_target_fingerprint(target_x, target_y);
                if (*MultiplayerActive && !*ControlTurnC) {
                    // Match the stock human-turn path: send the semantic move
                    // through NetDaemon so the session authority executes it
                    // and every peer receives the same vehicle update. Calling
                    // order_veh directly here would mutate only this process.
                    net_int_t network_veh_id = veh_id;
                    *dword_93E908 = &network_veh_id;
                    native_result = NetDaemon_order_veh(
                        NetState, veh_id, direction, 1);
                    if (native_result) {
                        NetDaemon_await_exec(NetState, 1);
                        // The order command normally carries the resulting
                        // vehicle update, but DirectPlay can complete the
                        // local authority execution before a passive peer has
                        // received that payload. Follow with the stock full
                        // vehicle synchronization command so the final unit
                        // record is eventually identical on every client.
                        synch_veh(veh_id);
                        // Do not report deferred completion while that final
                        // reliable synchronization is still queued locally.
                        NetDaemon_await_exec(NetState, 1);
                    }
                    if (*dword_93E908 == &network_veh_id) *dword_93E908 = NULL;
                } else {
                    native_result = order_veh(veh_id, direction, 3);
                }
            }
            bool artifact_consumed = attempted
                && resolved_artifact_consumed
                && resolved_artifact_unit_id == veh_id
                && original_unit_id == BSC_ALIEN_ARTIFACT;
            resolved_artifact_unit_id = -1;
            resolved_artifact_consumed = false;
            deferred_action.native_result = native_result;
            bool same_unit = veh_id >= 0 && veh_id < *VehCount
                && Vehs[veh_id].faction_id == faction_id
                && Vehs[veh_id].unit_id == original_unit_id;
            if (same_unit) {
                deferred_action.observed_x = Vehs[veh_id].x;
                deferred_action.observed_y = Vehs[veh_id].y;
            }
            bool changed_position = same_unit
                && (Vehs[veh_id].x != deferred_action.origin_x
                    || Vehs[veh_id].y != deferred_action.origin_y);
            bool attacker_state_changed = same_unit
                && (Vehs[veh_id].moves_spent != original_moves_spent
                    || Vehs[veh_id].damage_taken != original_damage_taken);
            bool target_state_changed = attempted
                && visible_target_fingerprint(target_x, target_y)
                    != target_fingerprint_before;
            bool combat_resolved = attempted && visible_combat_target
                && (!same_unit || attacker_state_changed || target_state_changed);
            deferred_action.status = attempted
                && (native_result || changed_position || combat_resolved || artifact_consumed)
                ? "completed" : "rejected";
            deferred_action.resolution = artifact_consumed
                ? "native_artifact_consumed"
                : combat_resolved
                ? "native_combat_resolved"
                : (changed_position || native_result ? "native_move_resolved" : "");
            return true;
        }
        if (deferred_council_faction_id >= 0) {
            int faction_id = deferred_council_faction_id;
            deferred_council_faction_id = -1;
            if (game_active() && faction_id == *CurrentPlayerFaction
            && human_turn_actionable(faction_id)
            && !(*GameState & STATE_GAME_DONE) && can_call_council(faction_id, 0)) {
                call_council(faction_id);
                deferred_action.native_result = 1;
                deferred_action.status = "completed";
            } else {
                deferred_action.native_result = 0;
                deferred_action.status = "rejected";
            }
            return true;
        }
        int other = deferred_diplomacy_faction_id;
        deferred_diplomacy_faction_id = -1;
        int faction_id = game_active() ? *CurrentPlayerFaction : -1;
        if (faction_id >= 1 && other >= 1 && other < MaxPlayerNum
        && other != faction_id && is_alive(other)
        && has_treaty(faction_id, other, DIPLO_COMMLINK)
        && human_turn_actionable(faction_id)) {
            if (is_human(other)) {
                // Match the stock multiplayer commlink path: 0x1502 asks the
                // remote human to enter the paired DiploWindow before the
                // local modal starts.  Calling diplo() alone creates only a
                // local window, so subsequent clause packets have no live
                // recipient and are discarded.
                if (*MultiplayerActive) {
                    message_data(0x1502, 0, other, 0, 0, 0);
                    *dword_7492CC = 1;
                }
                diplo(faction_id, other);
                // Human-to-human diplomacy submits its final transmission
                // asynchronously.  Do not report the deferred action complete
                // (or allow the next semantic mutation) until DirectPlay has
                // drained that packet, just as the validated move path does.
                if (*MultiplayerActive) NetDaemon_await_exec(NetState, 1);
            } else communicate(faction_id, other, 0);
            deferred_action.native_result = 1;
            deferred_action.status = "completed";
        } else {
            deferred_action.native_result = 0;
            deferred_action.status = "rejected";
        }
        return true;
    }
    if (msg != WM_SMACX_AGENT) return false;
    InterlockedIncrement(&request_handler_hits);
    std::string request;
    uint64_t sequence = 0;
    EnterCriticalSection(&request_lock);
    if (request_pending && !request_in_progress) {
        request = pending_request;
        sequence = pending_sequence;
        request_in_progress = true;
    }
    LeaveCriticalSection(&request_lock);
    if (request.empty()) return true;
    std::string response = execute_request(request);
    EnterCriticalSection(&request_lock);
    bool current = sequence == pending_sequence;
    if (current) {
        pending_response = response;
        response_sequence = sequence;
        request_pending = false;
    }
    request_in_progress = false;
    LeaveCriticalSection(&request_lock);
    if (current) SetEvent(response_event);
    return true;
}

void agent_bridge_stop() {
    if (!lock_initialized) return;
    if (deferred_end_turn_timer_id) {
        KillTimer(NULL, deferred_end_turn_timer_id);
        deferred_end_turn_timer_id = 0;
    }
    if (semantic_observation_timer_id) {
        KillTimer(NULL, semantic_observation_timer_id);
        semantic_observation_timer_id = 0;
    }
    reset_semantic_observation_shadow();
    clear_pending_council_vote();
    InterlockedExchange(&stopping, 1);
    if (listen_socket != INVALID_SOCKET) closesocket(listen_socket);
    if (worker_thread) {
        WaitForSingleObject(worker_thread, 2000);
        CloseHandle(worker_thread);
        worker_thread = NULL;
    }
    if (response_event) {
        CloseHandle(response_event);
        response_event = NULL;
    }
    if (request_getmessage_hook) {
        UnhookWindowsHookEx(request_getmessage_hook);
        request_getmessage_hook = NULL;
    }
    DeleteCriticalSection(&request_lock);
    lock_initialized = false;
}

int __cdecl agent_network_wait_task() {
    // NetDaemon::await_exec and ::await_diplo cooperatively process native
    // packets but can consume the posted main-window bridge request. Service
    // it at their existing wait_task boundary, then run the original pump.
    // request_in_progress prevents an action that entered this loop from
    // re-entering itself; agent_modal_service_depth prevents a read from
    // recursively pumping DirectPlay before this loop's own packet processor.
    if (lock_initialized) {
        InterlockedIncrement(&request_network_wait_hits);
        ++agent_modal_service_depth;
        agent_bridge_handle_message(game_window, WM_SMACX_AGENT);
        --agent_modal_service_depth;
    }
    return wait_task();
}

bool bridge_request_waiting() {
    if (!lock_initialized) return false;
    EnterCriticalSection(&request_lock);
    bool waiting = request_pending && !request_in_progress;
    LeaveCriticalSection(&request_lock);
    return waiting;
}

int __cdecl agent_modal_wait_task() {
    // Do not poll or lock the bridge during ordinary animation frames. Only a
    // real serialized request activates this fallback, which avoids changing
    // the timing of paired native diplomacy while still preventing its modal
    // loop from starving semantic control.
    if (bridge_request_waiting()) {
        InterlockedIncrement(&request_modal_wait_hits);
        ++agent_modal_service_depth;
        agent_bridge_handle_message(game_window, WM_SMACX_AGENT);
        --agent_modal_service_depth;
    }
    return wait_task();
}
