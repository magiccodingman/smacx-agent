"""Deterministic gameplay literacy compiled from a confirmed public native receipt.

No strategy selection, runtime world access, or unverified stock fallback lives here.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

DOCTRINE_VERSION = 'smacx.sovereign-doctrine.v1'
COMPILER_VERSION = 'smacx.doctrine-compiler.v1'
CONTEXT_SCHEMA = 'smacx.confirmed-gameplay-context.v1'
COMPATIBILITY = 'thinker-smacx-doctrine.v1'
TEMPLATE = Path(__file__).with_name('doctrine') / 'sovereign-v1.md'
BLOCKS = (
    'SELF_FACTION_CONTEXT','OPPONENT_FACTION_CONTEXT','DIFFICULTY_BASE_CONTEXT','WORLD_CONTEXT',
    'EXPLORATION_RULE_CONTEXT','OPENING_RULE_CONTEXT','PERSISTENCE_RULE_CONTEXT','DIFFICULTY_ECOLOGY_CONTEXT',
    'RESEARCH_RULE_CONTEXT','DIFFICULTY_SOCIAL_CONTEXT','SPECIAL_DIPLOMACY_CONTEXT',
    'DIPLOMATIC_VICTORY_CONTEXT','ENABLED_VICTORY_CONTEXT','COOPERATIVE_VICTORY_CONTEXT',
    'PROGENITOR_VICTORY_CONTEXT','ELIMINATION_RULE_CONTEXT',
)
RULES = ('victory_conquest','victory_economic','victory_diplomatic','victory_transcendence',
    'victory_cooperative','do_or_die','look_first','time_warp','ironman','blind_research',
    'tech_stagnation','spoils_of_war','unity_survey','unity_scattering','random_events')
RATINGS = ('ECONOMY','EFFICIENCY','SUPPORT','MORALE','POLICE','GROWTH','PLANET','PROBE','INDUSTRY','RESEARCH')
# Native/public inputs only. Optional means no stock inference when absent.
INPUT_MANIFEST = {
    'schema': 'required', 'compatibility': 'required', 'self_faction': 'required',
    'rules': 'required', 'research_mode': 'required', 'difficulty': 'required', 'victory': 'required', 'opening': 'required',
    'participants': 'optional', 'world': 'optional',
}
BLOCK_INPUTS = {
 'SELF_FACTION_CONTEXT':['self_faction','victory.eligible'],
 'OPPONENT_FACTION_CONTEXT':['participants'],
 'DIFFICULTY_BASE_CONTEXT':['difficulty.name','difficulty.natural_content'],
 'WORLD_CONTEXT':['world','participants'],
 'EXPLORATION_RULE_CONTEXT':['rules.unity_survey','rules.unity_scattering','rules.random_events','difficulty.event_first_turn'],
 'OPENING_RULE_CONTEXT':['opening','rules.look_first','rules.time_warp'],
 'PERSISTENCE_RULE_CONTEXT':['rules.ironman'],
 'DIFFICULTY_ECOLOGY_CONTEXT':['difficulty.ecology'],
 'RESEARCH_RULE_CONTEXT':['research_mode','rules.blind_research','self_faction.progenitor','rules.tech_stagnation','rules.spoils_of_war','difficulty.research'],
 'DIFFICULTY_SOCIAL_CONTEXT':['difficulty.se_cost'],
 'SPECIAL_DIPLOMACY_CONTEXT':['self_faction.progenitor','participants','victory.progenitor'],
 'DIPLOMATIC_VICTORY_CONTEXT':['rules.victory_diplomatic','victory.eligible','victory.supreme_fraction'],
 'ENABLED_VICTORY_CONTEXT':['rules','victory.eligible','victory.ending_year'],
 'COOPERATIVE_VICTORY_CONTEXT':['rules.victory_cooperative'],
 'PROGENITOR_VICTORY_CONTEXT':['self_faction.progenitor','participants','victory.progenitor'],
 'ELIMINATION_RULE_CONTEXT':['rules.do_or_die'],
}

class DoctrineError(ValueError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def require(value: Mapping, key: str, kind: type):
    result=value.get(key)
    if type(result) is not kind:
        raise DoctrineError('doctrine_required_input:'+key)
    return result


def label(value: Any) -> str:
    if not isinstance(value,str) or not value.strip() or len(value)>160 or any(c in value for c in '\n\r{}<>'):
        raise DoctrineError('doctrine_invalid_public_label')
    # Names remain data, including custom names, never template syntax.
    return value.replace('`','').replace('*','').strip()


def faction_text(f: dict) -> str:
    name=label(require(f,'name',str));require(f,'progenitor',bool)
    facts=require(f,'mechanics',list)
    if not facts:raise DoctrineError('doctrine_required_input:self_faction.mechanics')
    rendered=[]
    for fact in facts:
        kind=require(fact,'kind',str)
        amount=fact.get('amount')
        if amount is not None and (type(amount) is not int or abs(amount)>10000):raise DoctrineError('doctrine_invalid_modifier')
        if kind=='social':
            rating=require(fact,'name',str)
            if rating not in RATINGS:raise DoctrineError('doctrine_unknown_social_rating')
            rendered.append(f'{amount:+d} {rating}')
        elif kind in ('starting_technology','starting_unit','free_facility','prohibited_model'):
            rendered.append({'starting_technology':'Starting technology: ','starting_unit':'Starting unit: ',
                'free_facility':'Free facility: ','prohibited_model':'Cannot adopt '}[kind]+label(fact.get('name')))
        elif kind in ('attack','defense','psi','research_cost','hurry_cost','votes'):
            rendered.append({'attack':'Conventional attack modifier','defense':'Conventional defense modifier','psi':'Psi modifier',
                'research_cost':'Research cost factor','hurry_cost':'Hurry cost factor','votes':'Council voting factor'}[kind]+f': {amount}%')
        elif kind in ('fungus_nutrients','fungus_minerals','fungus_energy','population_limit','commerce','starting_energy','drone','talent'):
            rendered.append(kind.replace('_',' ')+f': {amount:+d}')
        elif kind in ('free_prototypes','technology_on_capture','worm_police','aquatic','efficiency_immunity','infiltration_technology','no_mind_control'):
            rendered.append({'free_prototypes':'No ordinary prototype surcharge','technology_on_capture':'Technology acquisition through base capture',
             'worm_police':'Native-life policing advantage','aquatic':'Aquatic faction with sea-start mechanics',
             'efficiency_immunity':'Immune to negative EFFICIENCY effects','infiltration_technology':'Technology sharing depends on infiltration',
             'no_mind_control':'Immune to ordinary mind control'}[kind])
        else:raise DoctrineError('doctrine_unmapped_faction_mechanic:'+kind)
    return f'**{name}**: '+ '; '.join(rendered)+'.'


def compile_doctrine(context: Mapping[str, Any]) -> dict:
    if not isinstance(context,Mapping):raise DoctrineError('doctrine_confirmed_context_required')
    # Strict allowlist makes accidental runtime/controller ingestion visible.
    if set(context)-set(INPUT_MANIFEST):raise DoctrineError('doctrine_unexpected_context_field')
    if context.get('schema')!=CONTEXT_SCHEMA:raise DoctrineError('doctrine_unconfirmed_context')
    c=dict(context); compatibility=require(c,'compatibility',dict)
    if compatibility.get('profile')!=COMPATIBILITY or compatibility.get('invariant_overrides')!=[]:
        raise DoctrineError('doctrine_incompatible_loaded_ruleset')
    if not re.fullmatch('[0-9a-f]{64}',str(compatibility.get('loaded_fingerprint',''))):
        raise DoctrineError('doctrine_loaded_ruleset_fingerprint_required')
    f=require(c,'self_faction',dict);r=require(c,'rules',dict)
    for key in RULES:require(r,key,bool)
    if set(r)!=set(RULES):raise DoctrineError('doctrine_unmapped_fixed_rule')
    d=require(c,'difficulty',dict);v=require(c,'victory',dict);opening=require(c,'opening',dict)
    eligible=require(v,'eligible',list)
    if any(x not in ('conquest','economic','diplomatic','transcendence','progenitor') for x in eligible):raise DoctrineError('doctrine_unknown_victory')
    if len(set(eligible))!=len(eligible):raise DoctrineError('doctrine_duplicate_victory')
    year=require(v,'ending_year',int)
    if not 0<year<10000:raise DoctrineError('doctrine_invalid_ending_year')
    if opening.get('kind') not in ('planetfall','time_warp','imported'):raise DoctrineError('doctrine_unknown_opening')
    require(opening,'initial_pod_placement',bool)
    b={key:'' for key in BLOCKS}
    b['SELF_FACTION_CONTEXT']='You govern '+faction_text(f)+' These mechanics create opportunities and constraints; choose your strategy from the actual position, not a stock leader agenda.'
    others=c.get('participants')
    if others is not None and type(others) is not list:raise DoctrineError('doctrine_invalid_participants')
    b['OPPONENT_FACTION_CONTEXT']='\n'.join('- '+faction_text(other) for other in others) if others else 'No additional participant mechanics are confirmed in this seat’s public setup context.'
    aliens=f['progenitor'] or any(other['progenitor'] for other in (others or []))
    if aliens:
        pv=require(v,'progenitor',dict)
        count=require(pv,'generators',int);size=require(pv,'population',int);require(pv,'cooperative',bool)
        if count<1 or size<1:raise DoctrineError('doctrine_invalid_progenitor_requirements')
        b['SPECIAL_DIPLOMACY_CONTEXT']='Progenitor communication has cross-species technology restrictions. The two Progenitor factions cannot make peace with one another. Use current legal diplomacy choices for human–Progenitor agreements.'
        b['PROGENITOR_VICTORY_CONTEXT']=f'**Progenitor Victory:** complete {count} Subspace Generators in bases of at least size {size}. '+('This is your faction’s special route.' if f['progenitor'] else 'This is an opponent victory threat.')+' '+('The loaded rules permit a cooperative Pact outcome when Cooperative Victory applies.' if pv['cooperative'] else 'The loaded rules do not permit sharing this special victory.')
    if f['progenitor'] and 'progenitor' not in eligible:raise DoctrineError('doctrine_missing_progenitor_eligibility')
    if not f['progenitor'] and 'progenitor' in eligible:raise DoctrineError('doctrine_conflicting_victory_eligibility')
    name=label(require(d,'name',str));content=require(d,'natural_content',int)
    if content<0 or content>100:raise DoctrineError('doctrine_invalid_contentment')
    b['DIFFICULTY_BASE_CONTEXT']=f'At {name}, the baseline naturally content population is {content} per base before other modifiers. Faction rules, bureaucracy, conquest, facilities, Projects and Social Engineering can change actual unrest.'
    ecology=require(d,'ecology',str)
    if ecology not in ('standard','harsher'):raise DoctrineError('doctrine_unknown_ecology')
    if ecology=='harsher':b['DIFFICULTY_ECOLOGY_CONTEXT']='The loaded difficulty uses harsher eco-damage rules; monitor intensive industry and disruptive terraforming.'
    se=require(d,'se_cost',str)
    if se not in ('free','paid'):raise DoctrineError('doctrine_unknown_se_cost')
    b['DIFFICULTY_SOCIAL_CONTEXT']='Social Engineering changes have no ordinary Energy cost at this difficulty.' if se=='free' else 'Social Engineering changes can cost Energy. Inspect the exact current cost, including any same-turn credit, before switching.'
    research=require(d,'research',str)
    if research not in ('loaded','forgiving','moderate','expensive'):raise DoctrineError('doctrine_unknown_research_burden')
    mode=require(c,'research_mode',str)
    if mode not in ('blind','directed'):raise DoctrineError('doctrine_unknown_effective_research_mode')
    b['RESEARCH_RULE_CONTEXT']=('Blind Research applies to this seat: choose broad Explore, Discover, Build and Conquer priorities rather than an exact technology.' if mode=='blind' else 'Directed Research applies to this seat: select a currently available technology through the legal choices.')
    if mode=='directed' and r['blind_research']:b['RESEARCH_RULE_CONTEXT']+=' A verified seat-specific override takes precedence over the global Blind Research flag.'
    if r['tech_stagnation']:b['RESEARCH_RULE_CONTEXT']+=' Tech Stagnation is enabled: discoveries take longer, extending the relevance of existing technology generations.'
    if r['spoils_of_war']:b['RESEARCH_RULE_CONTEXT']+=' Spoils of War is enabled: capturing an enemy base can grant an otherwise unknown technology.'
    b['RESEARCH_RULE_CONTEXT']+=' Research investment follows the loaded rules; use current research costs rather than an assumed difficulty multiplier.' if research=='loaded' else f' Research investment at this difficulty is {research}.'
    b['EXPLORATION_RULE_CONTEXT']=('Unity Survey makes broad starting geography available; ordinary fog still limits current detail and unit knowledge.' if r['unity_survey'] else 'Unity Survey is unavailable: unexplored geography must be discovered or legitimately learned.')+' '+('Unity Pods are scattered broadly across Planet.' if r['unity_scattering'] else 'Unity Pods are concentrated around landing regions rather than scattered broadly.')
    if r['random_events']:
        first=require(d,'event_first_turn',int)
        if first<0:raise DoctrineError('doctrine_invalid_event_turn')
        b['EXPLORATION_RULE_CONTEXT']+=f' Random Events are enabled; ordinary qualifying events become eligible from turn {first}. Eligibility does not guarantee an event.'
    else:b['EXPLORATION_RULE_CONTEXT']+=' Ordinary Random Events are suppressed.'
    if opening['kind']!='imported':
        if r['look_first'] and opening['initial_pod_placement']:b['OPENING_RULE_CONTEXT']='Campaign-start context: Look First allows evaluating the initial Colony Pod site before founding.'
        if r['time_warp']:b['OPENING_RULE_CONTEXT']+=' Campaign-start context: Time Warp begins from an accelerated developed position; assess that position rather than assume an ordinary Planetfall opening.'
    if r['ironman']:b['PERSISTENCE_RULE_CONTEXT']='Iron Man restricts ordinary game save-and-restore play and modifies scoring. Platform crash recovery, journal replay and infrastructure restoration remain controlled by the harness, not the sovereign.'
    enabled=[key for key in ('conquest','economic','diplomatic','transcendence') if r['victory_'+key]]
    if any(key not in enabled for key in eligible if key!='progenitor'):raise DoctrineError('doctrine_disabled_eligible_victory')
    b['ENABLED_VICTORY_CONTEXT']='Enabled standard victory paths: '+(', '.join(x.title() for x in enabled) or 'none')+'.'
    definitions={
        'conquest':'Conquest resolves through defeating or subjugating the independent opposition under the loaded rules. Preparation, peace and alliances can serve that objective; constant war is not required.',
        'economic':'Economic victory requires the enabling technology and enough Energy to corner the Global Energy Market. Protect the Headquarters and survive the announced countdown; inspect the actual required cost and duration.',
        'transcendence':'An eligible faction follows the late scientific and Planetary sequence through The Voice of Planet and then completes The Ascent to Transcendence.',
    }
    for key in enabled:
        if key in definitions:b['ENABLED_VICTORY_CONTEXT']+='\n'+definitions[key]
        if key not in eligible:b['SELF_FACTION_CONTEXT']+=f' The globally enabled {key.title()} route is not available to your faction; treat it as an opponent threat.'
    b['ENABLED_VICTORY_CONTEXT']+=f'\nThe match also has a time-limit ending in mission year {year}; account for the native end-of-game scoring conditions.'
    if r['victory_diplomatic']:
        fraction=require(v,'supreme_fraction',int)
        if not 1<=fraction<=100:raise DoctrineError('doctrine_invalid_vote_threshold')
        b['DIPLOMATIC_VICTORY_CONTEXT']=f'Diplomatic Victory is enabled: the Supreme Leader election requires {fraction}% of Council votes after its enabling technology. A defied result may require defeating or subjugating holdouts; inspect candidate eligibility and current native requirements.'+(' This is an opponent route, not your own victory path.' if 'diplomatic' not in eligible else '')
    if aliens and r['victory_diplomatic']:b['DIPLOMATIC_VICTORY_CONTEXT']+=' Unconquered Progenitor factions prevent ordinary Diplomatic Victory.'
    b['COOPERATIVE_VICTORY_CONTEXT']=('Cooperative Victory is enabled: qualifying Pact partners can share applicable victories under the loaded eligibility rules.' if r['victory_cooperative'] else 'Cooperative Victory is disabled: friendship, Treaty, Pact or private promises do not make factions joint mechanical winners.')
    b['ELIMINATION_RULE_CONTEXT']=('Do or Die is enabled: eliminated factions do not receive the ordinary early-game escape and restart opportunity.' if r['do_or_die'] else 'Do or Die is disabled: an eliminated faction may escape and restart when native eligibility conditions permit; do not assume its first defeat is permanent.')
    world=c.get('world')
    if world:
        lines=[];width=world.get('width');height=world.get('height')
        if type(width) is int and type(height) is int and width>0 and height>0:
            line=f'World dimensions: {width} × {height} in native map coordinates.'
            if others is not None:
                # Raw rectangular coordinate area; parity factor cancels against baseline.
                ratio=(width*height/(len(others)+1))/(80*40/7)
                density='very high' if ratio<.60 else 'high' if ratio<.85 else 'moderate' if ratio<1.25 else 'low' if ratio<1.75 else 'very low'
                line+=f' Nominal faction density is {density}; actual space, contact and competition depend on geography and starting placement.'
            lines.append(line)
        descriptions={
          'ocean_coverage':('Low ocean target favors more land.','Medium ocean target makes maritime access potentially significant.','High ocean target favors scarce land and greater maritime importance.'),
          'erosive_forces':('Strong erosion favors flatter terrain.','Average erosion gives no strong ruggedness prior.','Weak erosion favors rugged terrain.'),
          'cloud_cover':('Sparse cloud cover favors drier terrain.','Average cloud cover gives no strong rainfall prior.','Dense cloud cover favors wetter terrain.'),
          'native_life':('Rare native life lowers its generation frequency.','Average native life gives no extreme frequency prior.','Abundant native life increases fungus and native encounter opportunities; psi preparation may matter earlier.'),
        }
        for key,values in descriptions.items():
            value=world.get(key)
            if value is None:continue
            if type(value) is not int or value not in (0,1,2):raise DoctrineError('doctrine_invalid_world_prior:'+key)
            lines.append(values[value])
        b['WORLD_CONTEXT']='\n'.join('- '+line for line in lines)
    template=TEMPLATE.read_text(encoding='utf-8')
    if sorted(re.findall(r'\{\{([A-Z_]+)\}\}',template))!=sorted(BLOCKS):raise DoctrineError('doctrine_placeholder_manifest_mismatch')
    text=re.sub(r'\{\{([A-Z_]+)\}\}',lambda m:b[m[1]].strip(),template).strip()
    if '{{' in text or '}}' in text:raise DoctrineError('doctrine_unresolved_placeholder')
    return {'text':text,'blocks':b,'metadata':{
        'doctrine_version':DOCTRINE_VERSION,'compiler_version':COMPILER_VERSION,
        'doctrine_sha256':hashlib.sha256(template.encode()).hexdigest(),
        'compatibility':compatibility,'fixed_configuration_sha256':fingerprint(c),
        'gameplay_sha256':hashlib.sha256(text.encode()).hexdigest(),
    }}
