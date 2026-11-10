# CR-066: A build pipeline can register a build with its key (ZID-MACHINE-001 §3, §9; ZID-AUTH-001 §2, §15)

Status: open (built and tested; the Security Lead confirms the trust decision below before this is relied on in production).

## Why
CR-065 gave a build pipeline one authority, `machine.build.register`, so it can propose software and never approve, revoke or run it. It was never possible
in practice. A pipeline signs in with a private key (OAuth client credentials) and has no session, so it has no passkey to give, and registering a build was
refused twice: by the central step-up list (`POST /v1/machine/builds`, `STEP_UP_REQUIRED`), and, even with that off, by the assurance rules for an R2 action
(`ASSURANCE_INSUFFICIENT`: a caller with no session is AAL-Z0). A test that signs in for real (`test_pipeline_signin_pg.py`) found it. The recommended
production path, where nobody types a digest, could not work.

## What changes
- **A signed key counts as AAL-Z2, for one action.** The action catalogue gets an explicit per-action flag, `machine_callable: true`, set on
  `machine.build.register` only. For an action with that flag, and only when the caller is a *signed-key service caller*, `protected_action` evaluates
  the decision at AAL-Z2 (the existing taxonomy already treats proof of possession of a key as phishing-resistant: `pop`, `swk`). Every other action sees the
  caller's real level, so every other action still refuses a machine.
- **A signed-key service caller is read from the client's own record**, never from anything the caller sent (`signed_key_service_caller`): the client is
  ACTIVE, has `client_credentials` as its only grant, authenticates with `private_key_jwt`, holds public keys, has no sign-in redirect, **holds the build-pipeline
  scope `machine.build:register`**, and the token's subject is that client's service principal. A person's token, a token that merely names a service client, and a
  machine client made for something else (access decisions, a SCIM source) do not qualify, whatever grants their identity holds.
- **The step-up list gets an explicit opt-in per rule**, `machine: exempt`, set on `POST /v1/machine/builds` only. It skips the *human* proof for a
  signed-key service caller on that route. People are never exempt; no other rule is.
- **Two opt-ins must both be true**: the route says machines may skip the proof, and the action says machines may call it. A unit test fixes both lists
  at exactly one entry, so adding another is a visible, reviewed change.
- **What the pipeline still cannot do**, tested with every other machine grant given to it: approve or revoke a build, register a tool, draft a release.
  It still needs the `machine.build.register` grant (without it, refused), and a registered build is only a draft until a second person approves it.
- **A client type of its own.** `POST /v1/integration/oauth-clients` accepts `purpose: BUILD_PIPELINE`: the same shape as a decisions client (confidential, a signed key,
  no redirect, no secret) with its own scope, so a client made for decisions cannot register builds and a pipeline client is not a decision caller. The record now returns
  `service_principal_id`, the identity a grant is issued to. The console's Builds page makes these credentials in one press (the key pair is made in the browser; the private
  key is shown once and never sent).
- `register_build.py` asks for the pipeline scope by default.

## Why this is safe enough
A pipeline that is compromised can *propose* a build. Nothing can run it: a second person must approve it (a separate gate, R3/E3, with a passkey), a release
and a workload binding need step-up of their own, and the attestor compares what runs to what was approved. The control that matters, the two-person approval,
is untouched.

## Owner decisions (proposed values, none ratified)
1. **Is a signed key enough for AAL-Z2 here?** Proposed: yes, for this one action, because the result is only a proposal. Alternative: a distinct machine
   assurance level, with its own mapping and review.
2. **Should people registering by hand keep the passkey proof?** Proposed: yes (unchanged); only signed-key service callers skip it.
3. ~~A dedicated "build pipeline" application type.~~ Done: `purpose: BUILD_PIPELINE`, with its own scope (see above).
