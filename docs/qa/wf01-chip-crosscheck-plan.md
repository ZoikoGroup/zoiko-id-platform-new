# Independent chip cross-check: plan (WF01 and WF03 E03; CR-123)

Prepared 18 Nov 2026 by engineering. It sequences a test and sets how its result is read. It approves nothing and records no result.

## Why

The wireframes (01 section on the chip reader; 03 decision and row E03) allow a self-run chip claim only "if independent self-run assurance gates pass", with JMRTD and NFCPassportReader as sandboxed references and a licensed provider as the primary. We wrote our own reader (CR-108, CR-111) and proved it only against the ICAO standard's worked examples and a simulated chip. This plan is the independent evidence: the same documents on the same phones through our reader and through an independent one, compared.

## The setup

| Item | Choice |
|---|---|
| Reference readers | JMRTD on Android, NFCPassportReader on iOS. Each is a separate lab tool, never linked into the product (licences: `sdks/native-bridge/crosscheck/README.md`) |
| Pairing | Same physical document, same phone model, same access method, one run per reader |
| Documents | Real passports and ID cards of the launch countries, from consenting staff or an issuing authority's test set, plus a lab-supplied clone or tampered test chip if one exists |
| Phones | At least two Android models and two iPhone models (the owners set the numbers) |
| Access methods | BAC, PACE with the generic mapping, PACE with the chip authentication mapping, wherever a document supports them |
| Record | One JSON file per run, per `sdks/native-bridge/crosscheck/run.schema.json`: the READ report (hashes of the signed object and each data group) kept apart from the CRYPTOGRAPHIC report (the verdict of each check). No document number, name, photo or data-group content |

## How a result is read

Run `python tools/scripts/chip_crosscheck.py RUNS_DIR`. It pairs the runs, compares them and applies the gate in `backend/config/self-run-verification/chip-crosscheck-gate.yaml`.

- **The gate ships with no policy**, so it reports `NOT_CONFIGURED` and never passes. The security and QA owners record how many documents, phones and access methods are enough, and which references must have been used. Those numbers are a risk decision and are not proposed here.
- **A pass where the reference failed is critical**, and one such pair fails the gate however much else agrees: it is the direction that would let a bad passport through. Ours failing what the reference passed is a finding to explain (often the trust list), not a critical one.
- **Different documents, different signed objects or different phones are not comparisons** and add nothing, so padding the evidence does not help. A check one side did not run is "not comparable", never agreement.
- **Read and cryptographic reports must both agree.** A byte difference in a data group, or a group read by only one reader, fails the read side.

## What a pass would mean, and what it would not

A pass means: on the documents, phones and methods tested, our reader read the same bytes and reached the same verdicts as an independent reader, and never passed what it failed. It does **not** mean: certified, safe for any other document or country, resistant to a clone it was not tested against, or approved. The remaining gates in the wireframes still stand: trust material, DPIA, region controls, lawful basis, signed policies, and lab evidence for device security. A self-run claim is a decision for the owners with this as one input.

## Who does what

| Step | Owner |
|---|---|
| Build the two harness apps against the libraries' current documentation | Mobile lead |
| Provide documents and consent, and record the device matrix | QA |
| Read each library's licence and sign the register entry | Legal |
| Record the gate policy | Security owner and QA lead |
| Run the command and attach the report to the UAT register | QA |
