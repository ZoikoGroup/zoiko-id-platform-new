# Approvals, dual control and single-person organizations: what only the owners can close (ZID-ENG-DEC-010, 011, 012; slice L-10, CR-047)

Engineering built the service and its configuration. These items need a named owner's decision, signature or an operational fact. None is marked done by engineering.

## Approvals the records ask for
| # | Owner | Decision | Where it lives | State |
|---|---|---|---|---|
| 1 | Founder | Approve the A0/A1/A2 framework (Decision 10 section 10) | the three records | Proposed |
| 2 | Security and Product | Lock the action-to-tier registry under version control (Decision 10 section 10) | `backend/config/approvals/action-tiers.yaml` (any edit changes the policy version of new cases) | Draft registry exists |
| 3 | Founder | Approve the A2 action list and the per-action approver qualifications (Decision 11 section 12) | the registry rows `approvers` and `gate` | Proposed |
| 4 | Founder | Approve Solo Organization Governance Mode (Decision 12) | the records | Recommended |
| 5 | Security | Threat model for collusion, owner impersonation, delegated-role loops, stale votes, cross-region approval and races (Decision 10 section 10) | none yet | Open |
| 6 | Operations | Request expiry defaults, review SLAs, revocation and incident evidence duties (Decision 10 section 10) | `approval-policy.yaml`: 3 days default, 14 days maximum are engineering proposals | Open |

## Switches the owners set
| Switch | Default | Effect |
|---|---|---|
| `governance_enforcement` | `SHADOW` | `SHADOW` leaves the earlier two-person approval valid for owner changes, privileged role grants and resumption and only logs that an A2 case would be required. `ENFORCE` refuses that path for every A2 action. Flip it after the console screens exist (L-13). |
| `assured_review` | `INACTIVE` | The staffed trust-operations service of Decision 12 does not exist. It must stay `INACTIVE` until staffing, role separation, regional controls and an audited case process are approved. |
| `reviewer_validity_days` | 365 | A proposal. |

## Rulings engineering needs
1. **Endpoint names.** The three records name three different sets; the new service uses `/v1/approval-cases` and `/votes` because `/v1/approval-requests` already exists. Confirm or rename.
2. **Who verifies the first reviewer of a solo organization.** A solo owner cannot approve their own appointee. Two routes exist: an A1 approval by an independent person (impossible if there is none), or a platform verification an operator records (`python -m zid_platform.entrypoints.verify_reviewer`). Name the Zoiko iD role that may run it, what evidence it must hold, and where that evidence is kept.
3. **Reviewer qualifications.** The records say "verified external professional, co-director, trustee or authorized delegate" with documented authority. Decide which documents prove each, who checks them, and the conflict declarations that disqualify.
4. **Platform A2 actions.** The flow exists: the eight actions are approved in the operator tenant and each operator tool must call `consume_authorization`. Decide who the platform Owners are (two independent people, how each authenticates), then require each operation (key replacement, rollback, restore, retention, home-region change, impersonation, disabling gates) to call the guard when its tool is written. Today only `bootstrap_operator` does, and the **first** operator is a bootstrap exception: confirm it.
5. **The four actions that have no operation** (weakening authentication, recovery or approval policy; credential issuer or authority root change; organization-wide export or impersonation; delegated authority) say why in the registry. Confirm each reason, or decide to create the capability (each would be a new product decision, not a wiring task).
6. **Additional gates.** Three are machine-checked (the incoming Owner has a passkey, a role grant lists its permissions, an agent is still pending). The rest (successor verification, privacy and residency clearance, key ceremony evidence, re-proofing evidence content) are shown and recorded only. Decide which must become automated checks and who supplies each source.
7. **Re-proofing evidence for recovery.** The recovery case requires a named re-proofing reference. Decide what that reference points to (an L-5 identity case, an operator record) and that it is checked before the case opens.
8. **Legal form of a one-member organization** is never inferred from the member count. Decide the per-country legal-form and representation-authority table (Decision 12 section 01), owned by Legal.
9. **Expiry.** A case that nobody touches stays open until the next read. Decide whether operations want a sweeper and an alert before expiry.
10. **Notifications.** The events exist; who sends mail, to whom (the Owner, the affected person, the recovery contact) and in what words is not decided (Decision 11 section 07 requires notification).

## Operational facts only operations can supply
- Whether any staffed Assured Review service will exist, when, and under what contract and regional controls.
- The incident-commander role for containment (who may call it, and how the post-incident review is tracked: today it is an audit marker, not a work item).
- Break-glass and support-tooling procedures that must route through the approval service rather than around it (Decision 11 T09).

## What this slice does not claim
No certification and no staffed review service. Platform dual control exists as a guard and one consumer; the platform Owners and the other operator tools are open. The acceptance rows EDR10, EDR11 and EDR12 are linked as in progress, never verified.
