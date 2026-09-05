# Geographic semantics and hierarchical LOD acceptance

These content-free deterministic measurements were captured on 2026-09-04
after the physical-geography and geopolitical-semantics correction pass. They
contain no prompts, game assets, saves, credentials, or private endpoints.

| Fixture | Result |
| --- | ---: |
| Small quiet 64K anchor | 962 estimated tokens |
| Huge quiet 64K anchor | 965 estimated tokens |
| Huge quiet growth for 100× as many known tiles | 0.312% |
| Huge quiet 256K anchor | 966 estimated tokens |
| Huge fragmented 64K anchor | 4,751 estimated tokens; 3,176 regions explicitly omitted |
| Huge chaotic 64K anchor | 5,895 estimated tokens |
| Huge chaotic 256K anchor | 15,902 estimated tokens |

`scripts/geographic_semantics_contract_test.py` additionally proves:

- physical landmass/ocean-mass identity is independent from ownership,
  diplomacy, units, and hostile zones of control, while known terrain mutation
  may split or merge it;
- sea-mobility regions may include coastal bases without changing physical
  ocean identity;
- native-shaped current, stale, and unknown territorial evidence remains
  distinct and ownership interfaces never become political-border claims;
- known resource, improvement, coastline, naval, and named-landmark evidence is
  aggregated with bounded representatives and field-level freshness;
- an unknown frontier can qualify a possible connection between known
  components without asserting hidden terrain, and scout arrival is computed
  only when that frontier is queried;
- mechanically related theaters can cross geographic and mobility regions,
  include allied participation, and remain promoted by active plans or recent
  journaled material events while unrelated quiet geography demotes;
- queried expansion candidates differ by guarded native buildability, resources,
  overlap, terrain, danger, connectivity, and logistics without receiving a
  deterministic strategic rank; and
- repair and staging rows are subject-relative, rules-aware, and preserve
  unknown foreign access.

The token figures come from `scripts/world_context_benchmark.py`. The 64K and
256K outputs use the same perspective facts and differ only in bounded detail.
The quiet-map invariant demonstrates that provider-facing size scales mainly
with strategic complexity rather than raw tile count.

