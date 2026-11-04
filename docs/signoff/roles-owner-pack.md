# Owner pack: ZID-ENG-DEC-007 role by feature permissions

A checklist for the owners. It approves nothing, and every reference below is blank on purpose. Slice: [L-7](../phases/phase-l-second-wireframe-decisions.md); what engineering built: [CR-044](../specs/change-requests/CR-044-role-feature-matrix.md).

| # | Ruling needed | Why engineering cannot decide | Reference |
|---|---|---|---|
| 1 | Founder approval of ZID-ENG-DEC-007 and its six matrices (the matrix file is generated from them) | The record is proposed, not approved | |
| 2 | The Team page invites ADMIN directly; the record says an Admin grant is a request an independent checker approves. The request route exists (`POST /v1/tenant/role-grants`). Ruling: set `direct_admin_invitation` in `backend/config/roles/role-policy.yaml` to REFUSED, or keep ALLOWED | Changes a shipped behaviour | |
| 3 | Whether MEMBER is retired, mapped to Viewer, or kept for provisioning only | Affects existing members and the SCIM feed | |
| 4 | Who may write a person link (`platform.principal_person_link`): identity proofing, a platform review or an enterprise directory, and the evidence each needs. The table is append-only, so a wrong link is corrected by the platform | Needs an identity-linking rule and a privacy review | |
| 5 | Which routes, workers, exports and webhooks move onto the matrix first (EDR07-09) | Sequencing and risk | |
| 6 | The "assigned team", "assigned case" and "assigned template" scopes that limit an Operator | The record names the limit but not how an assignment is made | |
| 7 | Admin access to billing "limited to expressly assigned billing scope" (matrix F) and its link to the billing roles in L-14 | Depends on the commercial closures | |
| 8 | Confirm or narrow the five actions the pending bootstrap Owner may take before the organization gate passes (`BOOTSTRAP_ACTIONS` in `tenant/domain/role_matrix.py`) | An engineering default so company verification can run; the record does not say | |
| 9 | The onboarding plan approval: an Admin approves plans today, matrix A lets only an Owner or Approver approve a privileged request. Keep the Admin approval, or move it to Owner or Approver | Changes a shipped onboarding flow | |
| 10 | Which remaining module routes (privacy, evidence, commercial, communication, search, signals, operator) are mapped to matrix actions next | Each needs an action chosen from the matrix | |
| 11 | **Switch the route map on.** `route_matrix_enforcement` in `backend/config/roles/role-policy.yaml` is REPORT_ONLY: every mapped route records what the matrix would refuse and lets the request through. Read the recorded refusals (`tenant.access.decision`, object `route:...`), then set ENFORCED | Changes who can reach tenant administration routes today | `backend/config/roles/route-map.yaml` |
| 12 | **The 130 or so mappings in `route-map.yaml`** are engineering choices of a matrix row and a mode for each route (for example publishing a policy release is mapped to `d.change-role-taxonomy-permission-catalog`, activating an agent to `e.approve-agent-authority-sensitive-tool-grant`). Confirm or change each | The matrices do not name routes | |
| 13 | **Rows the matrices do not have.** Data-protection administration (`/v1/privacy/admin/*`) has no row and is exempt (NO_MATRIX_ACTION); so are search, onboarding steps and the owner steps suspend, resume and offboard. Add rows, or rule that the protected action alone is enough | Needs a record change | |
| 14 | **Legal-hold release** is mapped to `f.release-legal-hold-override-retention`, which is reserved to Zoiko iD: once ENFORCED, no customer role can release a hold through the API. Confirm that is intended | The record reserves it | |
| 15 | **Exemptions by kind** (own account, operator plane, protocol, approval engine, billing layer, inline evaluation): confirm that each family belongs in its kind, in particular `/v1/reviews/*`, `/v1/containment-actions` and `/v1/tenants*` as operator plane | A wrong kind leaves a route outside the matrix | |
