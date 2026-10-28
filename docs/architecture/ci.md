# CI and release pipeline

What runs, what is required to merge, and what is known to be missing. The workflows are in `.github/workflows/`; the branch rule is `.github/rulesets/default-branch.json` (applied in GitHub settings, not by the repository). Policy tests for all of this are in `backend/app/tests/unit/test_devsecops_controls.py`.

## Rules every workflow follows (tested)

- The token is read-only at the top of every workflow; a job that needs more says so itself.
- Every action is pinned by commit SHA, tool images by digest, Python and Node packages by lockfile.
- Nothing a pull request controls can reach a secret, a registry login or an OIDC identity. No `pull_request_target`. Release and signing run only from `main` and only in the repository itself.
- No static cloud credential anywhere; deployment is pulled by Argo CD, so CI holds no cloud access.
- Checkouts do not keep the token in `.git/config` (`persist-credentials: false`), because tests run repository code.

## On every pull request

| Workflow | Job (gate in bold) | What it proves |
|---|---|---|
| backend | static | repo structure, contract compatibility with the base branch, `uv.lock` matches `pyproject`, ruff, ruff format, mypy (strict) |
| | unit | unit, property, contract and library tests, no services |
| | component | the PostgreSQL component suite, with SpiceDB and Kafka |
| | python-audit | the locked Python dependencies against the advisory database |
| | sast | bandit, medium or worse with high confidence, on our source (tests excluded) |
| | **checks** | all of the above passed. This is the one backend gate the branch rule requires |
| frontend | verify | lint, types, tests, generated API client matches the contracts, production build of both apps |
| | audit | `npm audit` on production dependencies, high and critical |
| | **frontend-checks** | both passed |
| image-check | lint | hadolint on both Dockerfiles, actionlint on every workflow |
| | backend-image, frontend-image | the images build as the release builds them, the backend image runs as non-root, and no critical vulnerability with a fix is present |
| security | **secret-scan**, **devsecops-controls** | no credential-shaped string in tracked files (a hit opens a rotation incident); the static pipeline, chart, policy and release-gate tests |

There is no `paths` filter on these, so a required check is always reported. (A filtered workflow that does not start leaves a pull request waiting on a check that never arrives.)

## After merge and on a schedule

- **release** (push to `main`): builds the backend and both frontend images by digest in the hardened reusable builders, scans, writes a CycloneDX SBOM, attests provenance and SBOM, and signs by digest. **Promotion** is a manual run that verifies the same digest (signature, provenance, SBOM), runs the release gate with the CISA known-exploited feed, and waits for an environment approval. It never rebuilds.
- **scheduled** (nightly): the Python and Node advisory audits again, against advisories published since the last change. A hit opens an issue.
- **dependabot**: weekly grouped update pull requests for actions, Python, Node, images and OpenTofu.

## Branch rule: required checks

Required now: `checks`, `secret-scan`, `devsecops-controls`.

Add `frontend-checks`, and the `lint`, `backend-image` and `frontend-image` jobs of image-check, once each has passed on a real pull request. They were validated locally (every command, actionlint, hadolint) but not yet run on GitHub, and a required check that fails for an environment reason blocks every merge.

## Known gaps

1. **The release workflow cannot succeed on this repository today.** Its attestation steps fail with "Feature not available for user-owned private repositories". GitHub artifact attestations (provenance and SBOM attestation) need a public repository, or an organization-owned repository on a plan that supports them. Until then no image is signed and `promote` cannot pass, which is the correct fail-closed result. The options are to move the repository to an organization on a supporting plan, or to replace the GitHub attestations with cosign attestations stored with the image (the release gate, the Kyverno policy and the policy tests all assume GitHub attestations today, so that is a design change, not a one-line edit).
2. The frontend images are built, scanned and signed by the release but `promote` only verifies the backend image. The gate and its candidate builder need the web and operator images added.
3. Release builds on push to `main` without waiting for the test jobs. This is safe only because the branch rule forces every change through the checks before it reaches `main`.
4. No browser end-to-end tests run in CI. The Playwright suite in `frontend/tests/e2e` needs the whole stack (cell, global role, PostgreSQL, Valkey) and is not wired.
5. No coverage measurement, no CodeQL or dependency review (both need GitHub Advanced Security on private repositories), no load or chaos stage.
6. `CODEOWNERS` names `@zoiko/...` teams. The branch rule requires code-owner review, so those teams must exist with write access or no pull request can be approved.
