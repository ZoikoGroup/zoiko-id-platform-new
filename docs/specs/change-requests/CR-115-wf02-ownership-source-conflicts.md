# CR-115: ownership statements from several sources, and what happens when they disagree

Source: Wireframe 02 (ZID-DTV-WF-02) section 14, acceptance T20. Status: engineering built 18 Nov 2026. Not wired to a screen or to the stored company grade.

## What was built

- `organization/domain/ownership_sources.py` (pure): each statement names its source kind (register, signed paper, declaration) and reference. `reconcile` returns link items for `ownership.build` and the conflicts found.
- A holding is **disputed** when sources give different shares (beyond a 0.01 point rounding tolerance) or when a source declared to cover the company completely is silent about a holder another source names. A disputed link raises `DISPUTED_LINK` in the ownership analysis, so the candidate needs evidence and the structure is not complete.
- The platform never picks a winner. The strongest kind of source supplies the arithmetic default only; every source's statement stays on the link, named, for a reviewer. Any conflict sends the company to a person.
- Tests: `tests/unit/test_ownership_sources.py` (11), including a run through the real ownership analysis.

## Not built

- Nothing feeds it yet: no route reads uploaded shareholder papers into statements, and the case screen does not show the named statements.
- Which source covers which company is supplied by the caller; no register adapter declares coverage yet.
