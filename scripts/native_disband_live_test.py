#!/usr/bin/env python3
"""Isolated native disband: cancellation, spent units, and mineral recycling.

Requires SMACX_AGENT_TEST_MODE=1 and SMACX_ACCEPTANCE_MANAGED_ACTIONS=1.
Never run against an acceptance campaign: this creates fixture units.
"""
import json,time
from smacx_controller import bridge_request

def call(op,**kw):
    r=bridge_request(op,**kw)
    assert r.get('ok'),r
    return r

def command(**kw):
    s=call('semantic_snapshot')['snapshot']
    return call('semantic_command',match_id=s['match_id'],session_id=s['session_id'],
                expected_revision=s['revision'],**kw)

def wait_popup():
    for _ in range(40):
        s=call('semantic_snapshot')['snapshot']
        if s.get('interaction',{}).get('popup_label') in ('DISBAND','DISBAND2'):
            return call('semantic_choices',kind='interaction')
        time.sleep(.1)
    raise AssertionError('Native disband popup did not appear')

def units():return call('list_units',scope='own',limit=100)['items']
def minerals(base):
    return next(b for b in call('list_bases')['items'] if b['id']==base)['minerals']['accumulated']

def main():
    results=[]
    for spent in (0,1):
        fixture=call('test_managed_action_fixture',phase='diagnostics_disband',spent=spent)
        uid,bid=fixture['unit_id'],fixture['base_id']
        catalog=call('semantic_choices',kind='unit_actions',unit_id=uid)
        assert any(c.get('command')=='disband_unit' for c in catalog['choices']),catalog
        call('semantic_choices',kind='production',base_id=bid)
        stored=minerals(bid);before=units()
        queued=command(command='disband_unit',unit_id=uid,confirm_disband=1)
        assert queued.get('queued') and len(units())==len(before),queued
        choices=wait_popup();assert {c.get('response') for c in choices['choices']}=={'cancel','proceed'}
        command(command='respond_to_unit_disband',response='cancel')
        time.sleep(.3)
        after_cancel=units()
        stable=lambda rows: [{k:v for k,v in row.items() if k!='selected'} for row in rows]
        assert stable(after_cancel)==stable(before) and minerals(bid)==stored, {'before':before,'after':after_cancel,'minerals_before':stored,'minerals_after':minerals(bid)}
        command(command='disband_unit',unit_id=uid,confirm_disband=1);wait_popup()
        command(command='respond_to_unit_disband',response='proceed',confirm_disband=1)
        for _ in range(40):
            if len(units())==len(before)-1:break
            time.sleep(.1)
        assert len(units())==len(before)-1
        after=minerals(bid)
        assert after-stored==5,{'before':stored,'after':after,'spent':spent}
        results.append({'spent':bool(spent),'cancel_preserved_unit_and_minerals':True,
                        'removed_units':1,'minerals_before':stored,'minerals_after':after})
    print(json.dumps({'passed':True,'native_disband_cases':results}))
if __name__=='__main__':main()
