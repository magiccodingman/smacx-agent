"""Allowlisted adapter for an authenticated UI-thread doctrine receipt.

This is control-plane code. Model callers cannot provide receipts or approve compatibility.
"""
from copy import deepcopy
from smacx_doctrine import *

# Public alphax.txt bytes reviewed for the shipped rules family. No fallback by filename/name.
VERIFIED_RULE_FILES = frozenset({
'bb01e3ac23793da0a46713f4851d65e63b599c24aef8300ffe45e7e57b332c42'
})

def public_faction(raw):
    f={'name':label(require(raw,'name',str)),'progenitor':require(raw,'progenitor',bool),'mechanics':[]}
    if raw.get('leader'):f['leader']=label(raw['leader'])
    facts=f['mechanics']
    def add(kind,amount=None,name=None):
        item={'kind':kind}
        if amount is not None:item['amount']=amount
        if name is not None:item['name']=name
        facts.append(item)
    modifiers=require(raw,'modifiers',list)
    if len(modifiers)!=12 or any(type(x) is not int for x in modifiers):raise DoctrineError('doctrine_invalid_faction_modifiers')
    morale,research,drone,talent,energy,interest,pop,hurry,techcost,psi,share,commerce=modifiers
    if research:raise DoctrineError('doctrine_unmapped_faction_mechanic:legacy_RESEARCH')
    for kind,value in [('morale_bonus',morale),('drone',drone),('talent',talent),('starting_energy',energy),
                        ('population_limit',-pop),('psi',psi),('commerce',commerce)]:
        if value:add(kind,value)
    if interest:raise DoctrineError('doctrine_unmapped_faction_mechanic:interest')
    if hurry!=100:add('hurry_cost',hurry)
    if techcost!=100:add('research_cost',techcost)
    selected=require(raw,'selected_technologies',int)
    if selected:add('selected_technologies',selected)
    flags=require(raw,'flags',int)
    if flags & ~0xfff0:raise DoctrineError('doctrine_unknown_faction_flags')
    for bit,kind in {0x10:'technology_on_capture',0x40:'worm_police',0x100:'aquatic',0x200:'free_prototypes',
                     0x800:'no_mind_control',0x1000:'starting_commlink',0x2000:'cheaper_elevation',0x8000:'morale_immunity'}.items():
        if flags & bit:add(kind)
    if flags & 0x400:add('attack',125)
    if share:add('infiltration_share' if flags&0x20 else 'technology_share',share)
    for bonus in require(raw,'bonuses',list):
        rule=require(bonus,'rule',int);a=require(bonus,'a',int);b=require(bonus,'b',int)
        if rule in (0,1,2,12,15):add({0:'starting_technology',1:'starting_unit',2:'free_facility',12:'free_facility_after_technology',15:'free_ability_after_technology'}[rule],name=label(bonus.get('name')))
        elif rule in (3,4,10):
            if not 0<=a<len(RATINGS):raise DoctrineError('doctrine_invalid_rating_index')
            add({3:'social',4:'rating_immunity',10:'rating_robust'}[rule],b,RATINGS[a])
        elif rule in (5,6):add('model_impunity' if rule==5 else 'model_penalty',name=label(bonus.get('name')))
        elif rule in (7,8,9,11,13,14,16,17,18):add({7:'fungus_nutrients',8:'fungus_minerals',9:'fungus_energy',11:'votes',13:'revolt',14:'fewer_drones',16:'probe_cost',17:'defense',18:'attack'}[rule],a*100 if rule==11 else a)
        else:raise DoctrineError('doctrine_unmapped_faction_bonus:'+str(rule))
    if raw.get('prohibited_model'):add('prohibited_model',name=label(raw['prohibited_model']))
    if f['progenitor']:add('progenitor_grid')
    if not facts:add('no_special_modifiers')
    return f


def confirmed_context(receipt, *, match_id, session_id, faction_id, previous=None):
    if receipt.get('ok') is not True or receipt.get('schema')!='smacx.native-doctrine.v1':
        raise DoctrineError('doctrine_native_receipt_required')
    if (receipt.get('match_id'),receipt.get('session_id'),receipt.get('faction_id'))!=(match_id,session_id,faction_id):
        raise DoctrineError('doctrine_native_scope_mismatch')
    if receipt.get('engine_contract')!=COMPATIBILITY or receipt.get('rules_file_sha256') not in VERIFIED_RULE_FILES:
        raise DoctrineError('doctrine_unverified_loaded_ruleset')
    approved=json.loads((TEMPLATE.parent/'engine-compatibility.json').read_text())
    if receipt.get('engine_source_sha256')!=approved['engine_source_sha256']:
        raise DoctrineError('doctrine_unreviewed_engine_build')
    if receipt.get('config_supported') is not True or receipt.get('scenario_supported') is not True:
        raise DoctrineError('doctrine_unmapped_material_rules_override')
    f=public_faction(require(receipt,'self_faction',dict))
    raw_rules=require(receipt,'rules',dict)
    r={key:require(raw_rules,key,bool) for key in RULES}
    # Eligibility is potential under fixed rules, never current unlock/readiness.
    eligible=[key for key in ('conquest','economic','diplomatic','transcendence') if r['victory_'+key] and not(f['progenitor'] and key=='diplomatic')]
    if f['progenitor']:eligible.append('progenitor')
    c={'schema':CONTEXT_SCHEMA,'self_faction':f,'rules':r,'research_mode':'blind' if r['blind_research'] else 'directed',
       'difficulty':require(receipt,'difficulty',dict),
       'victory':{'eligible':eligible,'ending_year':require(receipt,'ending_year',int),'supreme_fraction':75,
           'progenitor':{'generators':require(receipt,'generators',int),'population':require(receipt,'generator_population',int),'cooperative':True}},
       'opening':{'kind':'time_warp' if receipt.get('planetfall') is True and r['time_warp'] else 'planetfall' if receipt.get('planetfall') is True else 'imported',
                  'initial_pod_placement':require(receipt,'initial_pod_placement',bool)},
       'world':deepcopy(require(receipt,'world',dict))}
    # Freeze starting known participant roster. Contact/elimination never regenerates doctrine.
    # Changes to already included public faction mechanics are checked where still exposed.
    public=[public_faction(row) for row in require(receipt,'participants',list)]
    if previous is not None:
        c['opening']=deepcopy(previous['opening'])
        if 'participants' in previous:
            by_name={row['name']:row for row in public}
            c['participants']=[by_name.get(row['name'],row) for row in previous['participants']]
    elif receipt.get('roster_complete') is True:
        c['participants']=public
    # Fingerprint only stable public mechanics plus compatibility evidence, never current world facts.
    c['compatibility']={'profile':COMPATIBILITY,'invariant_overrides':[],
        'loaded_fingerprint':fingerprint({'rules_file':receipt['rules_file_sha256'],'engine':receipt['engine_source_sha256'],
            'faction':f,'difficulty':c['difficulty'],'victory':c['victory']})}
    compile_doctrine(c)  # Validate before persistence; nested unknowns never become defaults.
    return c
