# Spec registry generator

Regenerates the doc-derived registries from `docs/specs/*.docx` so they never drift from the specs.

```
python -I tools/codegen/spec-registries/ext.py docs/specs  build/spec-text        # .docx -> text
python -I tools/codegen/spec-registries/extract.py build/spec-text build/spec-json  # text -> json
python -I tools/codegen/spec-registries/generate.py build/spec-json .               # json -> repo files
```

Outputs: `contracts/error-codes/error-codes.yaml`, `contracts/asyncapi/event-catalogue.yaml`, `docs/traceability/requirements.csv`, `docs/traceability/acceptance-tests.csv`.

Source precedence follows CR-005 (ZID-SEC-001 §33): canonical spec > supplementary (pending reconciliation) > superseded. Each generated entry records `source` and `source_status`. Run in CI whenever a spec file changes; a diff in generated files must be reviewed with the spec change.
