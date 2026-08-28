#pragma once

#include "main.h"

enum PopDialog {
    PopDialogCheckbox = 0x1, // Multiple choices
    PopDialogListChoice = 0x2, // Only one choice
    PopDialogTextInput = 0x4,
    PopDialogBtnCancel = 0x40,
    PopDialogLargeWindow = 0x80, // Large square window placed on top left
    PopDialogSmallWindow = 0x400, // Narrow window
    PopDialogUnk1000 = 0x1000,
    PopDialogUnk10000 = 0x10000, // Removes portrait and ok buttons?
    PopDialogUnk40000 = 0x40000, // Removes ok buttons?
    PopDialogUnk100000 = 0x100000,
};

enum DiploProposal {
    DiploProposalMakeGift = 1,
    DiploProposalMakePact = 2,
    DiploProposalMakeTreaty = 3,
    DiploProposalTechTrade = 4,
    DiploProposalBuyTech = 5, // mention related prototype?
    DiploProposalNeedEnergy = 6,
    DiploProposalTradeMaps = 7,
    DiploProposalJointAttack = 8,
    DiploProposalBaseSwap = 9,
    DiploProposalCloseDialog = 10,
    DiploProposalRepayLoan = 11,
    DiploProposalOfferUnits = 12,
    DiploProposalTradeCommlink = 13,
};

enum DiploCounter {
    DiploCounterFriendship = 1, // goodwill and friendship
    DiploCounterNameAPrice = 2, // need but name your price
    DiploCounterThreaten = 3, // threaten with attack or cancel pact
    DiploCounterResearchData = 4, // valuable research data
    DiploCounterEnergyPayment = 5, // modest sum of energy credits
    DiploCounterLoanPayment = 6, // schedule of loan payments
    DiploCounterGiveBase = 8, // turn over one of my bases
};

// Semantic bridge metadata for the popup currently blocking the UI thread.
// The label is an engine script identifier, not text recovered from pixels.
const char* agent_popup_label();
const char* agent_popup_last_started_label();
BasePop* agent_popup_object();
uint64_t agent_popup_generation();
void agent_popup_started(const char* label, BasePop* popup);
const char* agent_popup_parse_string(int index);
int agent_popup_parse_number(int index);
int agent_popp(const char* filename, const char* label, int flags,
    const char* imagefile, fp_none fn);

void parse_gen_name(int faction_id, size_t title_value, size_t name_value);
void parse_noun_name(int faction_id, size_t title_value, size_t name_value);
int __cdecl X_pop(const char* label, fp_none fn);
int __cdecl X_pop_2(const char* filename, const char* label, fp_none fn);
int __cdecl X_pop_6(const char* label, int a2, fp_none fn);
int __cdecl X_pops(const char* label, Sprite* a2, fp_none fn);
int __cdecl X_pops_11(const char* label, int a2, Sprite* a3, fp_none fn);
int __cdecl X_dialog(const char* label, int faction2);
int __cdecl X_dialog(const char* filename, const char* label, int faction2);
int __cdecl DiploPop_spying(int faction_id);
int __cdecl mod_threaten(int faction1, int faction2);
int __cdecl mod_base_swap(int faction1, int faction2);
int __cdecl mod_energy_trade(int faction1, int faction2);
int __cdecl mod_buy_tech(int faction1, int faction2, int counter_id, int high_price, int proposal_id);
