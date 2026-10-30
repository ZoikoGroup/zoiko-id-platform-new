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
