#!/usr/bin/env python3
"""Golden content coverage; fixture receipts are native-shaped, not live certification."""
import copy
import json
from pathlib import Path
import sys
from smacx_doctrine import *
ROOT=Path(__file__).resolve().parents[1]


def fixtures():
    base={'schema':CONTEXT_SCHEMA,'compatibility':{'profile':COMPATIBILITY,'loaded_fingerprint':'a'*64,'invariant_overrides':[]},
      'self_faction':{'name':"Gaia's Stepdaughters",'progenitor':False,'mechanics':[
        {'kind':'social','name':'EFFICIENCY','amount':2},{'kind':'social','name':'PLANET','amount':1},
        {'kind':'social','name':'MORALE','amount':-1},{'kind':'social','name':'POLICE','amount':-1},
        {'kind':'starting_technology','name':'Centauri Ecology'},{'kind':'fungus_nutrients','amount':1},
        {'kind':'prohibited_model','name':'Free Market'}]},
      'rules':{key:key in ('victory_conquest','victory_economic','victory_diplomatic','victory_transcendence','blind_research','unity_scattering','random_events') for key in RULES},
      'research_mode':'blind','difficulty':{'name':'Librarian','natural_content':3,'ecology':'standard','research':'loaded','se_cost':'paid','event_first_turn':45},
      'victory':{'eligible':['conquest','economic','diplomatic','transcendence'],'ending_year':2500,'supreme_fraction':75,
          'progenitor':{'generators':6,'population':10,'cooperative':True}},
      'opening':{'kind':'planetfall','initial_pod_placement':True},
      'world':{'width':80,'height':80,'ocean_coverage':1,'erosive_forces':1,'cloud_cover':1,'native_life':1},'participants':[]}
    morgan={'name':'Morgan Industries','progenitor':False,'mechanics':[{'kind':'social','name':'ECONOMY','amount':1},
      {'kind':'social','name':'SUPPORT','amount':-1},{'kind':'population_limit','amount':-3},
      {'kind':'commerce','amount':1},{'kind':'starting_energy','amount':100},{'kind':'prohibited_model','name':'Planned'}]}
    university={'name':'University of Planet','progenitor':False,'mechanics':[{'kind':'social','name':'RESEARCH','amount':2},
      {'kind':'social','name':'PROBE','amount':-2},{'kind':'free_facility','name':'Network Node'},{'kind':'drone','amount':4}]}
    alien={'name':'Manifold Caretakers','progenitor':True,'mechanics':[{'kind':'social','name':'PLANET','amount':1},
        {'kind':'defense','amount':125},{'kind':'free_facility','name':'Recycling Tanks'}]}
    result={'stock-blind':base}
    def variant(name,change):
        value=copy.deepcopy(base);change(value);result[name]=value
    variant('morgan',lambda c:c.update(self_faction=morgan))
    variant('university',lambda c:c.update(self_faction=university))
    variant('directed',lambda c:(c['rules'].update(blind_research=False),c.update(research_mode='directed')))
    variant('progenitor',lambda c:(c.update(self_faction=alien,research_mode='directed'),c['rules'].update(blind_research=False),c['victory'].update(eligible=['conquest','economic','transcendence','progenitor'])))
    variant('human-progenitor-opponent',lambda c:c.update(participants=[alien]))
    variant('cooperative',lambda c:c['rules'].update(victory_cooperative=True))
    variant('conquest-only',lambda c:(c['rules'].update(victory_economic=False,victory_diplomatic=False,victory_transcendence=False),c['victory'].update(eligible=['conquest'])))
    variant('tiny-crowded',lambda c:(c['world'].update(width=48,height=48),c.update(participants=[{**morgan,'name':f'Public faction {i}'} for i in range(6)])))
    variant('huge-sparse',lambda c:c['world'].update(width=128,height=128))
    variant('custom-map',lambda c:c['world'].update(width=192,height=32))
    variant('time-warp',lambda c:(c['rules'].update(time_warp=True),c['opening'].update(kind='time_warp',initial_pod_placement=False)))
    variant('look-first',lambda c:c['rules'].update(look_first=True))
    variant('iron-man',lambda c:c['rules'].update(ironman=True))
    variant('resolved-random-morgan',lambda c:c.update(self_faction=morgan))
    variant('imported-midgame',lambda c:(c['rules'].update(look_first=True,time_warp=True),c['opening'].update(kind='imported',initial_pod_placement=False)))
    variant('custom-faction-compatible',lambda c:c.update(self_faction={'name':'Public custom faction','progenitor':False,'mechanics':[{'kind':'social','name':'INDUSTRY','amount':3}]}))
    variant('optional-unknown',lambda c:(c.pop('world'),c.pop('participants')))
    variant('missing-required',lambda c:c.pop('victory'))
    variant('unknown-ruleset',lambda c:c['compatibility'].update(profile='unknown-mod'))
    variant('material-mod-conflict',lambda c:c['compatibility'].update(invariant_overrides=['changed_psi_model']))
    return result


def main():
    write='--write-goldens' in sys.argv
    inventory=json.loads((ROOT/'docs/doctrine/claim-inventory.json').read_text())
    paragraphs=[p.strip() for p in TEMPLATE.read_text().split('\n\n') if p.strip() and not p.startswith('#')]
    assert [row['text'] for row in inventory]==paragraphs, 'doctrine inventory drift'
    assert all(row['text_sha256']==hashlib.sha256(row['text'].encode()).hexdigest() for row in inventory)
    results=[]
    for name,c in fixtures().items():
        try:rendered=compile_doctrine(c);out={'metadata':rendered['metadata'],'blocks':rendered['blocks']}
        except DoctrineError as e:out={'error':str(e)}
        if name in ('missing-required','unknown-ruleset','material-mod-conflict'):assert 'error' in out
        else:
            assert 'error' not in out,(name,out)
            assert '{{' not in rendered['text']
            assert 'control_policy' not in rendered['text']
            assert compile_doctrine(json.loads(canonical(c)))==rendered
            if name=='imported-midgame':assert not out['blocks']['OPENING_RULE_CONTEXT']
            if name=='optional-unknown':assert not out['blocks']['WORLD_CONTEXT']
            if name=='conquest-only':assert not out['blocks']['DIPLOMATIC_VICTORY_CONTEXT']
            if name=='custom-faction-compatible':assert "Gaia" not in rendered['text']
            if name=='progenitor':assert 'ineligible for Planetary Governor' in out['blocks']['SPECIAL_DIPLOMACY_CONTEXT']
        target=ROOT/'docs/doctrine/fixtures'/f'{name}.json'
        golden={'input':c,'expected':out}
        if write:
            target.write_text(json.dumps(golden,indent=2,ensure_ascii=False)+'\n')
            if 'error' not in out:(target.with_suffix('.md')).write_text(rendered['text']+'\n')
        else:assert json.loads(target.read_text())==golden,name
        results.append({'case':name,'passed':True,'expected_error':out.get('error')})
    for field in ('controller_type','current_turn','hidden_agenda','units'):
        c=copy.deepcopy(fixtures()['stock-blind']);c[field]='LEAK_SENTINEL'
        try:compile_doctrine(c)
        except DoctrineError:pass
        else:raise AssertionError(field)
    print(json.dumps({'passed':True,'classification':'deterministic native-shaped content fixtures','cases':results}))

if __name__=='__main__':main()
