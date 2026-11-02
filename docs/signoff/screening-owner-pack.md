# Owner pack: ZID-ED-04 director, owner and sanctions screening

A checklist for the owners. It approves nothing, and every reference below is blank on purpose. Source: ZID-ED-04 sections 03, 05, 07 and 08 (proposed, awaiting founder approval). Slice: [L-4](../phases/phase-l-second-wireframe-decisions.md); what engineering built: [CR-043](../specs/change-requests/CR-043-phase-l-company-verification.md).

Until the four lists below are loaded and current, every screening answers UNAVAILABLE: that is the truthful state, not a fault.

## 1. Sanctions list sources (ED-05 section 05)

Each list needs a verified download address, a licence check, and a schedule that keeps it inside `list_max_age_hours` (48 in `backend/config/screening/screening-policy.yaml`). Put the address in `sources.<LIST>.url` and run the ingest.

| List | Format the parser expects | Confirmed against | Address on record | Owner action |
|---|---|---|---|---|
| UN Security Council consolidated | XML, `CONSOLIDATED_LIST` with `INDIVIDUALS` and `ENTITIES` | The live file (individuals); entities and documents are not confirmed | `scsanctions.un.org/resources/xml/en/consolidated.xml`, which redirects to a short-lived signed blob address | Confirm the entity and document element names against the UN schema; schedule the fetch |
| OFAC SDN | `SDN.CSV`, `ALT.CSV`, `ADD.CSV` (fixed column order, `-0-` for empty) | The published data specification | none | Confirm the Sanctions List Service download address; schedule the three files together; confirm the non-SDN lists ED-04 names (the parser covers the SDN files only) |
| UK Sanctions List | CSV read by its published field headings | The format guide's headings | none | Confirm the CSV address on the UK Sanctions List page; the file type inference (person, entity, ship) is a heuristic to check on a real file |
| EU consolidated | XML, `sanctionEntity` with `nameAlias`, `birthdate`, `citizenship`, `identification` | Nothing: written from the published description | none | Register an EU Financial Sanctions Files account and token, then confirm the element names against a real file |

The OFAC consolidated (non-SDN) lists load as `OFAC_NON_SDN` from `CONS_PRIM.CSV`, `CONS_ALT.CSV` and `CONS_ADD.CSV` (the layout is the SDN layout; confirmed in OFAC's consolidated data specification). The OFAC download page listed an address on what looked like a development host, so no OFAC address is recorded: an owner confirms the production one.

A country's own list loads as `LOCAL`. The owner converts it to this CSV (first row exactly): `list_key,source_ref,kind,primary_name,aliases,birth,countries,identifiers,programs`; `kind` is PERSON, ENTITY or OTHER, several values in one cell are separated by `|`, and `list_key` names the source list (for example `AE-LTL`). A pack asks for it with `local_list_required: true`.

## 2. Country packs (ED-04 section 03)

Each pack is Legal's decision per country. Only `GB` (the UK people-with-significant-control test at 25 percent, plus the OFSI and OFAC ownership tests) and `US` (the two ownership tests, no beneficial-owner rule) are proposed. Every other country has no pack, so its decisions go to review (`COUNTRY_PACK_MISSING`); no universal 25 percent is ever applied.

| Country | Beneficial-owner rule | Sanctions ownership tests | Legal approval reference |
|---|---|---|---|
| GB | uk_psc, over 25 percent or control | ofsi, ofac_50 | ______ |
| US | none proposed | ofac_50, ofsi | ______ |
| IE, DE, FR and every other country | needs a pack | needs a pack | ______ |

Both ownership tests are applied to every pack as the conservative choice, because each only ever adds a review. Legal decides whether that stays.

## 3. Policy numbers only the owners can set

| Setting | Proposed | Owner |
|---|---|---|
| Name similarity that raises an alert (`match_threshold`) | 0.88, strong at 0.97 | Compliance |
| Longest age of a list at screening time | 48 hours | Compliance, SRE |
| Clear outcome valid for | 365 days (90 days for elevated risk), from ED-04 | Compliance |
| Which countries and risk tiers need beneficial-owner collection | not decided | Legal, Compliance |

## 4. People and operating model

- **Designated compliance reviewers:** named people who may see restricted evidence and rule on a case. The service refuses a ruling by the requester or a screened party, and a ruling needs a written rationale; who holds the role is yours.
- **Fair-review procedure:** the correction, appeal and deletion routes ED-04 asks for, and the product disclosure.
- **Regulatory scope:** whether and where Zoiko iD is an obliged entity (ED-04 section 07).
- **Residency:** screening evidence and party records are kept in the tenant's home cell; Security and Privacy confirm the list suppliers' and any enrichment vendors' processing regions.

## 5. What engineering has proved

| Acceptance test | Evidence |
|---|---|
| EDR04-01 a registered company without an authorized applicant never grants verified-owner authority | `backend/app/tests/unit/test_screening.py` |
| EDR04-02 UK PSC, indirect control and the UK and U.S. ownership rules give different answers | `test_screening.py` |
| EDR04-03 namesakes, transliteration and incomplete records go to review, never automatic rejection | `test_screening.py` |
| EDR04-04 list updates, outages, stale data and delistings are traceable and replayable | `backend/app/tests/component/test_screening_register_pg.py` (a delisting is screened again and every decision is replayed from the lists it named; the earlier outage and stale cases are in `test_screening_pg.py`) |
| EDR04-05 a missing intermediary or unresolved owner cannot silently pass | `test_screening.py` |
| EDR04-06 restricted person records stay out of events, logs, audit and the customer view, and in the organization's region | `backend/app/tests/component/test_screening_register_api_pg.py` (residency is checked before a name is stored; nothing from the register, the declaration or an appeal reaches an event, an audit record or signed evidence), `test_screening.py`, `test_screening_pg.py` (analytics and third-party processors are not covered: none exists) |
| Directors and persons with significant control from the register, reconciled with the declaration, screened and explained as tasks | `backend/app/tests/unit/test_screening_register.py`, `test_kyb_register.py`, `component/test_screening_register_pg.py` |
| Appeal and correction, decided by a different reviewer, the statement kept private | `test_screening_register_pg.py`, `test_screening_register_api_pg.py` |
| Monitoring triggers: a changed register, a privileged role, an owner added, an activation | `test_screening_register_pg.py`, `test_screening_register_api_pg.py` |
| No plan or entitlement can switch screening off | `backend/app/tests/unit/test_kyb_register.py` |
| EDR04-07 legal, privacy and security sign-off of the packs, licences and procedure | the owners |

## 6. Added: decisions about the register, appeals and monitoring

| Decision | Owner | Recorded |
|---|---|---|
| The fair-review procedure for an appeal: who may decide, the time to answer, what the customer is told | Legal / Compliance | ______ |
| How long an appeal statement and the register snapshots are kept | Privacy / Legal | ______ |
| Which registers other than Companies House are read for directors and persons with significant control, and on what lawful basis | Legal / Registry owners | ______ |
| How a register's protected entries are handled (a person reviews them today) | Compliance | ______ |
| Which actions count as high-risk for a rescreen beyond a privileged role, a new owner and an activation | Security / Compliance | ______ |
