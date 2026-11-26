# SDK release (sign and publish)

**Source:** ZID-CP-WF10 v2.0, WF-10.06. **Built:** CR-173. **Status:** the pipeline, the signing tool and the checks are built and tested. No SDK is signed or published until the company completes the one-time setup below. Until then the SDK register says every package is EXPERIMENTAL and unpublished, and the public claims page says no SDK is available.

This is a guide, not a numbered runbook: it is not in the service catalogue and no alert rule pages from it.

## What a release is

A release is the built package (wheel, npm tarball, NuGet package), a manifest of the SHA-256 digest of every file in it, and an Ed25519 signature over that manifest made with the company release key. Anyone can check one with the public key committed in this repository:

    python tools/scripts/sdk_release.py verify release.json <folder-with-the-package-files> --key backend/config/developer/sdk-signing-public.pem

A changed, added or missing file, an altered manifest and a signature from another key are each refused by name. The manifest lists files and declared dependencies. It is not a vulnerability scan and not a statement that the code is certified.

## One-time setup (company owners; about half a day, most of it waiting for registries)

### 1. The release key

Do this once, on a machine the company controls, by the person accountable for releases.

    python tools/scripts/sdk_release.py keygen --private release-key.pem --public sdk-signing-public.pem

1. Store `release-key.pem` in the company secret store (password manager vault with restricted access, or a key vault), then delete the local copy. It is the only secret. Never put it in the repository, a chat, a ticket or an email.
2. Commit `sdk-signing-public.pem` as `backend/config/developer/sdk-signing-public.pem` in a reviewed change. It is public by design.
3. Write down the key id the command printed. It is how a verifier knows which key signed a release.
4. Name a second person who can recover the key (two people, not one).

The tool refuses to overwrite an existing key pair. Replacing the release key makes every earlier signature unverifiable, so do it only for a compromise, and say so publicly (see Key compromise).

### 2. GitHub setup

1. Repository variable `SDK_RELEASE_ENABLED` = `true` (Settings, Secrets and variables, Actions, Variables). Off means the pipeline does nothing.
2. Create an environment named `sdk-release` (Settings, Environments). Turn on **Required reviewers** and choose at least two people who are not the person who usually starts releases. Restrict it to the `main` branch.
3. In that environment add the secrets:
   - `SDK_SIGNING_KEY`: the full text of `release-key.pem`.
   - `NPM_TOKEN`: an npm automation token for the `@zoiko` scope (npm only).
   - `NUGET_API_KEY`: a NuGet key limited to the `Zoiko.Id` package id (NuGet only).
   There is no PyPI secret: PyPI uses trusted publishing.

### 3. Registry accounts (a company account, never one person's)

| SDK | Registry | What to create |
|---|---|---|
| `verify-api-python` (`zoiko-verify`) | PyPI | A company organisation. Add a **trusted publisher**: repository `ZoikoGroup/zoiko-id-platform-new`, workflow `sdk-release.yml`, environment `sdk-release`. No token is needed. |
| `typescript-credentials` (`@zoiko/credentials`) | npm | The `@zoiko` organisation, with two owners and two-factor sign-in required. Create the automation token. |
| `dotnet` (`Zoiko.Id`) | NuGet | A company account; reserve the package id `Zoiko.Id`. Create the scoped API key. |
| Java (`com.zoiko.id`) | Maven Central | Verify the namespace, create a GPG key for Maven signing. **Not automated yet**: the pipeline does not publish Java. Release it by hand from a clean checkout after `mvn test`. |
| Go (`github.com/zoiko/zoiko-id-go`) | git tags | A Go module is published by a version tag, and its module path must match where it lives. The path does not match this repository, so decide the public repository first. **Not automated.** |

## Releasing

1. Raise the version in the package itself (`pyproject.toml`, `package.json` or `Zoiko.Id.csproj`) in a reviewed change. The pipeline refuses a run whose version differs from the package.
2. Run the **sdk-release** workflow (Actions, Run workflow). Choose the SDK, type the version, and leave **publish** off first. This is a dry run: it tests, builds, signs (a reviewer approves) and checks the signature, then stops.
3. Download the `release-...` artifact from the run. Verify it yourself with the public key (command above).
4. Run it again with **publish** on. A reviewer approves again. The publish job re-verifies the files against the signature immediately before they leave.
5. In a reviewed change update `backend/config/developer/sdk-releases.yaml` for that package: `published: true`, `last_test_run` (the date) and `last_test_evidence` (the run URL), `provenance: {signed: true, sbom: false}`. The register's rules refuse a GA state that the evidence does not back, and the public claims page then updates by itself. `sbom` stays false: the manifest lists files and declared dependencies only.
6. Raise the state from EXPERIMENTAL to BETA first. GA needs the compatibility matrix, each platform backed by an executed run.

## Key compromise

If the private key may have leaked: remove `SDK_SIGNING_KEY` from the environment now, stop releases, generate a new pair, commit the new public key in a reviewed change, publish a notice (what was exposed, which releases are affected, the new key id), and re-sign and republish current versions. Earlier signatures stay valid only for people who still hold the old public key, so say clearly which releases to distrust.

## What this pipeline does not do

- It does not create accounts, hold the key outside the approved environment, or approve a release by itself.
- It does not scan for vulnerabilities and it does not certify anything.
- It does not publish Java or Go (needs a GPG key and a decision on the repository path).
