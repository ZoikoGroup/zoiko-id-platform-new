# CR-156: Verify API, slice 8: workflow entitlement and hosted-page settings

Status: slice 8 built 24 Nov 2026 (uncommitted). Source: `ZID-EXT-IDV-001` epics E14 (workflow entitlement) and E19 (hosted white-label configuration: approved themes, origin allowlists, localization).

**Why.** Every application could start every workflow, and the verification page a customer sees always read the same. A company needs to limit what an application may start, and to show its own name and an approved look to its customers, without being able to change what the page is.

**Workflow entitlement (E14).** Per application: a list of allowed workflows (empty means every approved workflow) and a default used when a request names none.
- A request for a workflow the application is not allowed answers `WORKFLOW_NOT_ENTITLED` (403). This applies to sandbox test fixtures and to rechecks too (a recheck of a session whose workflow is no longer allowed is refused), so there is no side door.
- `GET /v1/verify-api/workflows` now says, for the calling application, which workflows it may start and which is its default.
- The default must be one of the allowed ones; a default with an empty list may be any approved workflow.

**Hosted page (E19).** Per application, only four settings, all validated: a display name (2 to 60 characters, no markup or control characters), **one of a fixed set of themes** (blue, green, violet, slate), an https help link (no login, no fragment) and a language. There is no free-form colour, CSS, logo or markup, so a partner can change the name and accent but not what the page is or does. The page maps a theme to fixed styles and
ignores anything unknown. The customer sees the display name in place of the application name, never the internal name or their own reference.
- **Return addresses** are now editable (`redirect_origins`), with the creation rule (exact https origins). The "back to the company" link is offered only while that address is **still registered**: removing an address stops sessions already made with it from sending customers there, and a new session naming it is refused.

**Changing settings.** `PUT /v1/verify-api/applications/{id}/settings` (`verify_api.application.manage`): only the fields sent change, each is checked as at creation, unknown settings are refused, a retired application cannot be changed. Console: "Workflows and customer page" section inside an application on the Developers page.
Data: migration `verify_api_0005` adds `allowed_workflows`, `default_workflow_id`, `branding` to `verify.api_application`.

**Not done.** Logos and custom domains (they need an approved asset and domain-ownership process); mobile app ids and SDK settings (no SDK yet); per-market workflow versions; a publish workflow for new workflow versions (the platform's approved list is fixed in code).

**Tests.** Unit (6): branding and workflow rules, including markup, unsafe links, unknown keys and themes. Component (3, real HTTP and database): limiting workflows, default and refusals across create, fixtures and rechecks; bad settings refused and partial changes keeping the rest; the hosted page showing the chosen name, theme, help link and language, and the return address stopping after removal. Earlier Verify API suites (32) still pass. Web: tsc 0 errors, eslint clean, 36 tests; a
test caught that a theme named `constructor` resolved to a built-in object, fixed with an own-property check.
