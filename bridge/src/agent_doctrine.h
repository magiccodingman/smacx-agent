// Included inside the bridge namespace: read-only, UI-thread public rules receipt.
// No opponents' runtime data, controller kinds or AI agenda fields are read.
std::string doctrine_loaded_alpha, doctrine_loading_alpha;
bool doctrine_loaded_supported = false;
CRules doctrine_loaded_rules = {};
std::string doctrine_file_sha256(const char* path) {
    HCRYPTPROV provider = 0; HCRYPTHASH hash = 0;
    if (!CryptAcquireContextA(&provider, NULL, NULL, PROV_RSA_AES, CRYPT_VERIFYCONTEXT)) return "";
    if (!CryptCreateHash(provider, CALG_SHA_256, 0, 0, &hash)) { CryptReleaseContext(provider, 0); return ""; }
    std::ifstream input(path, std::ios::binary);
    char bytes[4096]; size_t total = 0; bool ok = input.good();
    while (ok && input.read(bytes, sizeof(bytes)).gcount() > 0) {
        size_t n = input.gcount(); total += n;
        ok = total <= 1024*1024 && CryptHashData(hash, reinterpret_cast<BYTE*>(bytes), DWORD(n), 0);
    }
    BYTE digest[32]; DWORD size = sizeof(digest); std::ostringstream out;
    if (ok && !input.bad() && CryptGetHashParam(hash, HP_HASHVAL, digest, &size, 0)) {
        const char* hex = "0123456789abcdef";
        for (BYTE byte : digest) out << hex[byte >> 4] << hex[byte & 15];
    }
    CryptDestroyHash(hash); CryptReleaseContext(provider, 0); return out.str();
}

bool doctrine_config_supported() {
    std::ifstream input("thinker.ini"); if (!input.good()) return false;
    std::string line;
    while (std::getline(input, line)) {
        size_t start = line.find_first_not_of(" \t\r");
        if (start == std::string::npos || line[start] == '#' || line[start] == ';' || line[start] == '[') continue;
        size_t equal = line.find('=', start); if (equal == std::string::npos) return false;
        std::string key = line.substr(start, equal-start);
        while (!key.empty() && (key.back() == ' ' || key.back() == '\t')) key.pop_back();
        if (key != "video_mode" && key != "DisableOpeningMovie" && key != "window_width" && key != "window_height") return false;
    }
    return true;
}

void append_doctrine_faction(std::ostringstream& out, int id) {
    MFaction& f = MFactions[id];
    out << "{\"name\":" << json_string(f.formal_name_faction)
        << ",\"leader\":" << json_string(f.name_leader)
        << ",\"progenitor\":" << (f.is_alien() ? "true" : "false")
        << ",\"flags\":" << f.rule_flags << ",\"selected_technologies\":" << f.rule_tech_selected
        << ",\"modifiers\":[" << f.rule_morale << ',' << f.rule_research << ',' << f.rule_drone
        << ',' << f.rule_talent << ',' << f.rule_energy << ',' << f.rule_interest << ',' << f.rule_population
        << ',' << f.rule_hurry << ',' << f.rule_techcost << ',' << f.rule_psi << ',' << f.rule_sharetech
        << ',' << f.rule_commerce << "],\"bonuses\":[";
    for (int i = 0; i < std::min(f.faction_bonus_count, 8); ++i) {
        if (i) out << ',';
        int rule = f.faction_bonus_id[i], a = f.faction_bonus_val1[i], b = f.faction_bonus_val2[i];
        const char* name = "";
        if (rule == RULE_TECH && a >= 0 && a < MaxTechnologyNum) name = Tech[a].name;
        if ((rule == RULE_FACILITY || rule == RULE_FREEFAC) && a >= 0 && a <= SP_ID_Last) name = Facility[a].name;
        if (rule == RULE_UNIT && a >= 0 && a < MaxProtoFactionNum) name = Units[a].name;
        if (rule == RULE_FREEABIL && a >= 0 && a < MaxAbilityNum) name = Ability[a].name;
        if ((rule == RULE_IMPUNITY || rule == RULE_PENALTY) && a >= 0 && a < MaxSocialCatNum && b >= 0 && b < MaxSocialModelNum) name = SocialField[a].soc_name[b];
        out << "{\"rule\":" << rule << ",\"a\":" << a << ",\"b\":" << b << ",\"name\":" << json_string(name) << '}';
    }
    out << "],\"prohibited_model\":";
    int category=f.soc_opposition_category, model=f.soc_opposition_model;
    if (category>=0 && category<MaxSocialCatNum && model>=0 && model<MaxSocialModelNum) out << json_string(SocialField[category].soc_name[model]);
    else out << "null";
    out << '}';
}

std::string doctrine_context_response() {
    int id=*CurrentPlayerFaction;
    if (!game_active() || id <= 0 || id >= MaxPlayerNum || !is_human(id))
        return error_response("doctrine_seat_unavailable", "An active human-equivalent gameplay seat is required.");
    int difficulty=Factions[id].diff_level;
    if (difficulty<0 || difficulty>=MaxDiffNum) return error_response("doctrine_difficulty_unavailable", "Loaded seat difficulty is invalid.");
    // Scenario mechanics without reviewed overrides must not inherit normal doctrine.
    bool scenario_compatible = (*ObjectiveReqVictory == 0 || *ObjectiveReqVictory == 9999)
        && (*ObjectivesSuddenDeathVictory == 0 || *ObjectivesSuddenDeathVictory == 9999)
        && !(*GameRules & (RULES_SCN_NO_TECH_TRADING|RULES_SCN_NO_TECH_ADVANCES|RULES_SCN_NO_COLONY_PODS|RULES_SCN_NO_TERRAFORMING|RULES_SCN_NO_NATIVE_LIFE|RULES_SCN_NO_BUILDING_SP))
        && !(*GameMoreRules & (MRULES_NO_PLANETARY_COUNCIL|MRULES_NO_SOCIAL_ENGINEERING));
    std::ostringstream out;
    out << "{\"ok\":true,\"schema\":\"smacx.native-doctrine.v1\",\"match_id\":" << json_string(agent_match_id.c_str())
        << ",\"session_id\":" << json_string(agent_session_id.c_str()) << ",\"faction_id\":" << id
        << ",\"engine_contract\":\"thinker-smacx-doctrine.v1\",\"rules_file_sha256\":" << json_string(doctrine_loaded_alpha.c_str())
        << ",\"engine_source_sha256\":" << json_string(DOCTRINE_ENGINE_SHA256)
        << ",\"config_supported\":" << (doctrine_loaded_supported && !memcmp(&doctrine_loaded_rules, Rules, sizeof(CRules)) ? "true" : "false")
        << ",\"scenario_supported\":" << (scenario_compatible ? "true" : "false")
        << ",\"self_faction\":";
    append_doctrine_faction(out,id);
    out << ",\"difficulty\":{\"name\":" << json_string(lan_difficulty_name(difficulty))
        << ",\"natural_content\":" << conf.content_pop_player[difficulty]
        << ",\"ecology\":" << json_string(difficulty>=DIFF_THINKER ? "harsher" : "standard")
        << ",\"se_cost\":" << json_string(difficulty==0 ? "free" : "paid")
        << ",\"research\":\"loaded\",\"event_first_turn\":" << (75-*DiffLevel*10) << '}'
        << ",\"rules\":";
    append_named_game_rules(out,*GameRules,*GameState);
    out << ",\"ending_year\":" << *EndingMissionYear
        << ",\"generators\":" << Rules->subspace_gen_req << ",\"generator_population\":" << Rules->base_size_subspace_gen
        << ",\"world\":{\"width\":" << *MapAreaX << ",\"height\":" << *MapAreaY
        << ",\"ocean_coverage\":" << *MapOceanCoverage << ",\"erosive_forces\":" << *MapErosiveForces
        << ",\"cloud_cover\":" << *MapCloudCover << ",\"native_life\":" << *MapNativeLifeForms << "}"
        << ",\"planetfall\":" << (*CurrentTurn==0 ? "true" : "false")
        << ",\"initial_pod_placement\":" << (*CurrentTurn==0 && Factions[id].base_count==0 ? "true" : "false")
        << ",\"participants\":[";
    bool comma=false;
    for (int other=1;other<MaxPlayerNum;++other) {
        if (other==id || !is_alive(other) || !has_treaty(id,other,DIPLO_COMMLINK)) continue;
        if (comma) out << ','; comma=true;
        append_doctrine_faction(out,other);
    }
    out << "],\"roster_complete\":";
    bool complete=true;
    for (int other=1;other<MaxPlayerNum;++other) if (other!=id && is_alive(other) && !has_treaty(id,other,DIPLO_COMMLINK)) complete=false;
    out << (complete ? "true" : "false") << "}";
    return out.str();
}
