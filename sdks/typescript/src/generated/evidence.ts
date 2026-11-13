// GENERATED from contracts/openapi/public/evidence.json by frontend/packages/api-client/openapi/codegen.mjs. Do not edit.
export interface paths {
    "/v1/evidence/artifacts/{artifact_type}/{artifact_ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Artifact */
        get: operations["get_artifact_v1_evidence_artifacts__artifact_type___artifact_ref__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/audit-events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Audit Events */
        get: operations["audit_events_v1_evidence_audit_events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/audit-events/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Export Audit Events
         * @description A company's audit records as a feed a SIEM can follow: oldest first, `limit` at a time, with a cursor to continue from.
         *
         *     Pass the `next_cursor` of one answer as `after` of the next; with nothing new the cursor stays the same, so polling is safe to repeat. Every record
         *     carries the same fields as the audit list plus the schema name, so a collector maps it once. `format=ndjson` gives one JSON object per line with
         *     the cursor in the `X-Next-Cursor` header. Plan: custom event streams / SIEM integrations.
         */
        get: operations["export_audit_events_v1_evidence_audit_events_export_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/bulk-exports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Bulk Exports */
        get: operations["bulk_exports_v1_evidence_bulk_exports_get"];
        put?: never;
        /**
         * Bulk Export
         * @description Several receipts in one signed package that stops being downloadable at `expires_at` (API-13). The same R3
         *     `evidence.export` gate as a single export; every receipt also leaves its own custody record.
         */
        post: operations["bulk_export_v1_evidence_bulk_exports_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/bulk-exports/{export_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Bulk Export Download
         * @description The package while it is available. Past its expiry it is erased and this answers 410.
         */
        get: operations["bulk_export_download_v1_evidence_bulk_exports__export_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/cases": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Cases */
        get: operations["list_cases_v1_evidence_cases_get"];
        put?: never;
        /** Open Case */
        post: operations["open_case_v1_evidence_cases_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/cases/{case_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Case */
        get: operations["get_case_v1_evidence_cases__case_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/cases/{case_id}/items": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Add Case Item */
        post: operations["add_case_item_v1_evidence_cases__case_id__items_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/cases/{case_id}:close": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Close Case */
        post: operations["close_case_v1_evidence_cases__case_id__close_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/handles:resolve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Resolve
         * @description Re-identification (R3/E3): case-bound, recorded in chain of custody (EVIDENCE §13, §18).
         */
        post: operations["resolve_v1_evidence_handles_resolve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/keys": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Keys
         * @description Every public key that verifies Zoiko evidence, historic ones included (DEC-F2 §5).
         */
        get: operations["keys_v1_evidence_keys_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/legal-holds": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Holds */
        get: operations["list_holds_v1_evidence_legal_holds_get"];
        put?: never;
        /**
         * Create Hold
         * @description A hold needs a second administrator's approval of exactly this scope (two-person rule).
         */
        post: operations["create_hold_v1_evidence_legal_holds_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/legal-holds/{hold_id}:release": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Release Hold */
        post: operations["release_hold_v1_evidence_legal_holds__hold_id__release_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/packages": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Export */
        post: operations["export_v1_evidence_packages_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/partition:verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Verify Partition
         * @description Recompute the tenant's whole hash chain and every checkpoint; a failure is an incident.
         */
        post: operations["verify_partition_v1_evidence_partition_verify_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/provenance/{entity_type}/{entity_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Provenance */
        get: operations["get_provenance_v1_evidence_provenance__entity_type___entity_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/receipts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Receipts */
        get: operations["list_receipts_v1_evidence_receipts_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/receipts/{receipt_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Receipt */
        get: operations["get_receipt_v1_evidence_receipts__receipt_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/receipts/{receipt_id}/proof": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Proof */
        get: operations["proof_v1_evidence_receipts__receipt_id__proof_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/receipts/{receipt_id}:annotate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Annotate */
        post: operations["annotate_v1_evidence_receipts__receipt_id__annotate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/receipts/{receipt_id}:replay": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Run Replay */
        post: operations["run_replay_v1_evidence_receipts__receipt_id__replay_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/evidence/receipts/{receipt_id}:verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Verify */
        post: operations["verify_v1_evidence_receipts__receipt_id__verify_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/baselines": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Import Baseline */
        post: operations["import_baseline_v2_assurance_baselines_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/baselines/risk-confirmations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Confirm Risks */
        post: operations["confirm_risks_v2_assurance_baselines_risk_confirmations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/defects": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Open Defect */
        post: operations["open_defect_v2_assurance_defects_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/evidence:verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Verify Evidence */
        post: operations["verify_evidence_v2_assurance_evidence_verify_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/fixtures": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Fixtures */
        get: operations["list_fixtures_v2_assurance_fixtures_get"];
        put?: never;
        /** Create Fixture */
        post: operations["create_fixture_v2_assurance_fixtures_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/fixtures/{fixture_id}/review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Review Fixture */
        post: operations["review_fixture_v2_assurance_fixtures__fixture_id__review_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/fixtures/{fixture_id}/revisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Add Revision */
        post: operations["add_revision_v2_assurance_fixtures__fixture_id__revisions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/policies": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Propose Policy */
        post: operations["propose_policy_v2_assurance_policies_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/policies/{policy_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Policy */
        get: operations["get_policy_v2_assurance_policies__policy_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/policies/{policy_id}/signatures": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Sign Policy */
        post: operations["sign_policy_v2_assurance_policies__policy_id__signatures_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/policies:from-draft": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Propose From Draft
         * @description An unsigned proposal from the numbers a controlled document already proposes. Nobody's signature is implied.
         */
        post: operations["propose_from_draft_v2_assurance_policies_from_draft_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/provider-lab": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Provider Lab Report
         * @description Every documented fault injected into the platform's own guards, with what each guard did. It talks to no provider.
         */
        get: operations["provider_lab_report_v2_assurance_provider_lab_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/provider-lab/parity": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Provider Parity */
        post: operations["provider_parity_v2_assurance_provider_lab_parity_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/registry-sources/{name}:run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Run Registry Source
         * @description Fetch one enabled official register, compare it with the last snapshot and close what drifted. A disabled source, or no registered fetcher, fetches nothing.
         */
        post: operations["run_registry_source_v2_assurance_registry_sources__name__run_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/registry-watch": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Registry Watch
         * @description Two snapshots of an official register in, drift out, and the exact provider routes that drifted closed. Nothing is fetched.
         */
        post: operations["registry_watch_v2_assurance_registry_watch_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/release-candidates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Candidate */
        post: operations["create_candidate_v2_assurance_release_candidates_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/release-candidates/{release_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Preview */
        get: operations["preview_v2_assurance_release_candidates__release_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/releases/{release_id}/decisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Decide */
        post: operations["decide_v2_assurance_releases__release_id__decisions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/releases/{release_id}/rollback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Rollback */
        post: operations["rollback_v2_assurance_releases__release_id__rollback_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/requirements": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Requirements */
        get: operations["requirements_v2_assurance_requirements_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/role-assignments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Assign Role */
        post: operations["assign_role_v2_assurance_role_assignments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/test-runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Runs */
        get: operations["list_runs_v2_assurance_test_runs_get"];
        put?: never;
        /** Create Run */
        post: operations["create_run_v2_assurance_test_runs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/test-runs/{run_id}/cases/{case_id}/manifest": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Sign Manifest */
        post: operations["sign_manifest_v2_assurance_test_runs__run_id__cases__case_id__manifest_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/test-runs/{run_id}/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Append Event */
        post: operations["append_event_v2_assurance_test_runs__run_id__events_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/assurance/test-runs/{run_id}/evidence": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Evidence */
        get: operations["evidence_v2_assurance_test_runs__run_id__evidence_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/documents/extract": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Extract */
        post: operations["extract_v2_documents_extract_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/documents/validate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Validate Document */
        post: operations["validate_document_v2_documents_validate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/documents/{validation_ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Validation */
        get: operations["read_validation_v2_documents__validation_ref__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/evidence/uploads": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Uploads
         * @description The tenant's uploads as metadata (state, scan result, retention), newest first. Never the bytes.
         */
        get: operations["list_uploads_v2_evidence_uploads_get"];
        put?: never;
        /** Announce Upload */
        post: operations["announce_upload_v2_evidence_uploads_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/evidence/uploads/{upload_id}/content": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Send Content */
        put: operations["send_content_v2_evidence_uploads__upload_id__content_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/evidence/{evidence_ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Evidence */
        delete: operations["delete_evidence_v2_evidence__evidence_ref__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/evidence/{manifest_ref}/manifest": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Manifest */
        get: operations["manifest_v2_evidence__manifest_ref__manifest_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AnnotateBody */
        AnnotateBody: {
            /** Kind */
            kind: string;
            /** Note Commitment */
            note_commitment?: string | null;
            /** Reason Code */
            reason_code: string;
            /** Superseded By */
            superseded_by?: string | null;
        };
        /** BulkBody */
        BulkBody: {
            /** Case Ref */
            case_ref: string;
            /** Mode */
            mode: string;
            /**
             * Purpose
             * @default
             */
            purpose: string;
            /** Receipt Ids */
            receipt_ids: string[];
            /** Ttl Minutes */
            ttl_minutes?: number | null;
        };
        /** CaseBody */
        CaseBody: {
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            /** Kind */
            kind: string;
            /** Legal Basis */
            legal_basis?: string | null;
            /** Purpose */
            purpose: string;
            /** Reference */
            reference: string;
        };
        /** CaseItemBody */
        CaseItemBody: {
            /** Evidence Item Id */
            evidence_item_id: string;
            /** Purpose */
            purpose: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** HoldBody */
        HoldBody: {
            /**
             * Approval Request Id
             * Format: uuid
             */
            approval_request_id: string;
            /** Basis */
            basis: string;
            /** Expires At */
            expires_at?: string | null;
            /** Object Types */
            object_types?: string[];
            /** Reference */
            reference: string;
            /**
             * Review At
             * Format: date-time
             */
            review_at: string;
            /** Subject Principal Ids */
            subject_principal_ids?: string[];
        };
        /** IssuerClaim */
        IssuerClaim: {
            /** Country */
            country?: string | null;
            /** Name */
            name: string;
        };
        /** PackageBody */
        PackageBody: {
            /** Case Id */
            case_id?: string | null;
            /** Case Ref */
            case_ref: string;
            /** Mode */
            mode: string;
            /**
             * Purpose
             * @default
             */
            purpose: string;
            /**
             * Receipt Id
             * Format: uuid
             */
            receipt_id: string;
            /** Redact */
            redact?: string[];
        };
        /** UatBaselineIn */
        UatBaselineIn: {
            /** Cases */
            cases: components["schemas"]["UatCaseIn"][];
            /** Version */
            version: string;
        };
        /** UatCandidateIn */
        UatCandidateIn: {
            /** Build Sha256 */
            build_sha256: string;
            /** Commit Authors */
            commit_authors?: string[];
            /** Current Inputs */
            current_inputs?: {
                [key: string]: string;
            };
            /** Engineering Owner */
            engineering_owner?: string | null;
            /** Facts */
            facts?: {
                [key: string]: unknown;
            };
            /** Policy Pack */
            policy_pack: string;
            privacy_gate?: components["schemas"]["UatPrivacyGate"] | null;
            /** Release Unit */
            release_unit: {
                [key: string]: unknown;
            };
        };
        /** UatCaseIn */
        UatCaseIn: {
            /** Assertion */
            assertion: string;
            /** Case Id */
            case_id: string;
            /**
             * Dependency
             * @enum {string}
             */
            dependency: "OWN" | "THIRD_PARTY" | "PEOPLE";
            /**
             * Oracle Version
             * @default v1
             */
            oracle_version: string;
            /**
             * Risk
             * @enum {string}
             */
            risk: "PROPOSED_P0" | "PROPOSED_P1" | "P0" | "P1" | "P2";
            /** Suite */
            suite: string;
        };
        /** UatDecisionIn */
        UatDecisionIn: {
            /**
             * Decision
             * @enum {string}
             */
            decision: "APPROVE" | "REJECT";
            /** Rationale */
            rationale: string;
            /**
             * Role
             * @enum {string}
             */
            role: "QA_LEAD" | "CTO" | "SECURITY_GRC" | "PRIVACY" | "COUNTRY_LEGAL";
        };
        /** UatDefectIn */
        UatDefectIn: {
            /** Case Id */
            case_id: string;
            /** Owner */
            owner: string;
            /**
             * Severity
             * @enum {string}
             */
            severity: "BLOCKER" | "CRITICAL" | "MAJOR" | "MINOR";
        };
        /** UatDraftIn */
        UatDraftIn: {
            /** Release Unit */
            release_unit: {
                [key: string]: unknown;
            };
        };
        /** UatEventIn */
        UatEventIn: {
            /** Case Id */
            case_id?: string | null;
            /** Idempotency Key */
            idempotency_key: string;
            /** Kind */
            kind: string;
            /** Payload */
            payload?: {
                [key: string]: unknown;
            };
        };
        /** UatFixtureIn */
        UatFixtureIn: {
            /** Content Sha256 */
            content_sha256: string;
            /** Name */
            name: string;
        };
        /** UatFixtureReviewIn */
        UatFixtureReviewIn: {
            /** Approve */
            approve: boolean;
            /** Rationale */
            rationale: string;
            /** Revision */
            revision: number;
        };
        /** UatManifestIn */
        UatManifestIn: {
            /** Defect Id */
            defect_id?: string | null;
            /** Human Approval Refs */
            human_approval_refs?: string[];
            /** Measurements */
            measurements?: {
                [key: string]: number;
            };
            /**
             * Oracle Version
             * @default v1
             */
            oracle_version: string;
            /** Provider Receipt Refs */
            provider_receipt_refs?: string[];
            /** Requirement Ids */
            requirement_ids?: string[];
            /** Result */
            result: string;
            /** Source Snapshot Refs */
            source_snapshot_refs?: string[];
            /** Trace Refs */
            trace_refs?: string[];
        };
        /** UatParityIn */
        UatParityIn: {
            /** Candidate */
            candidate: {
                [key: string]: unknown;
            };
            /** Keys */
            keys: string[];
            /** Reference */
            reference: {
                [key: string]: unknown;
            };
        };
        /** UatPolicyIn */
        UatPolicyIn: {
            /** Release Unit */
            release_unit: {
                [key: string]: unknown;
            };
            /** Thresholds */
            thresholds: {
                [key: string]: number;
            };
        };
        /** UatPolicySignIn */
        UatPolicySignIn: {
            /**
             * Decision
             * @enum {string}
             */
            decision: "APPROVE" | "REJECT";
            /** Rationale */
            rationale: string;
            /**
             * Role
             * @enum {string}
             */
            role: "QA_LEAD" | "CTO" | "SECURITY_GRC";
        };
        /** UatPrivacyGate */
        UatPrivacyGate: {
            /** Market */
            market: string;
            /** Product */
            product: string;
            /** Version */
            version: string;
        };
        /** UatRevisionIn */
        UatRevisionIn: {
            /** Content Sha256 */
            content_sha256: string;
        };
        /** UatRiskIn */
        UatRiskIn: {
            /** Risks */
            risks: {
                [key: string]: "P0" | "P1" | "P2";
            };
        };
        /** UatRoleIn */
        UatRoleIn: {
            /** Principal */
            principal: string;
            /**
             * Role
             * @enum {string}
             */
            role: "QA_LEAD" | "CTO" | "SECURITY_GRC" | "PRIVACY" | "COUNTRY_LEGAL" | "TESTER" | "FIXTURE_REVIEWER";
        };
        /** UatRollbackIn */
        UatRollbackIn: {
            /** Reason */
            reason: string;
            /** Scope */
            scope: {
                [key: string]: unknown;
            };
        };
        /** UatRunIn */
        UatRunIn: {
            /** Build Sha256 */
            build_sha256: string;
            /** Case Ids */
            case_ids: string[];
            /** Commit Authors */
            commit_authors?: string[];
            /** Config Digest */
            config_digest: string;
            /** Environment Id */
            environment_id: string;
            /** Fixture Hashes */
            fixture_hashes?: string[];
            /**
             * Kind
             * @default INDEPENDENT
             * @enum {string}
             */
            kind: "INDEPENDENT" | "DEVELOPER";
            /** Pins */
            pins?: {
                [key: string]: string;
            };
            /** Release Unit */
            release_unit: {
                [key: string]: unknown;
            };
        };
        /** UatServiceIn */
        UatServiceIn: {
            /** Provider Id */
            provider_id: string;
            /** Scheme Version */
            scheme_version?: string | null;
            /** Service Code */
            service_code: string;
            /**
             * Status
             * @enum {string}
             */
            status: "REGISTERED" | "SUSPENDED" | "WITHDRAWN";
            /** Valid Until */
            valid_until?: string | null;
        };
        /** UatVerifyIn */
        UatVerifyIn: {
            /** Events */
            events?: {
                [key: string]: unknown;
            }[] | null;
            /** Jws */
            jws?: string | null;
            /** Manifest */
            manifest?: {
                [key: string]: unknown;
            } | null;
            /** Manifest Id */
            manifest_id?: string | null;
        };
        /** UatWatchIn */
        UatWatchIn: {
            /** Current */
            current: components["schemas"]["UatServiceIn"][];
            /** Previous */
            previous: components["schemas"]["UatServiceIn"][];
        };
        /** V2DocumentIn */
        V2DocumentIn: {
            /** Checks */
            checks: string[];
            /** Content Base64 */
            content_base64?: string | null;
            /** Declared Type */
            declared_type: string;
            /** Evidence Ref */
            evidence_ref?: string | null;
            /** Expected Sha256 */
            expected_sha256?: string | null;
            issuer_claim?: components["schemas"]["IssuerClaim"] | null;
            /** Jurisdiction */
            jurisdiction?: {
                [key: string]: string;
            } | null;
            /** Purpose */
            purpose?: string | null;
            /** Signature Base64 */
            signature_base64?: string | null;
        };
        /** V2ExtractIn */
        V2ExtractIn: {
            /** Barcode Text */
            barcode_text?: string | null;
            /** Image Base64 */
            image_base64?: string | null;
            /** Mrz Lines */
            mrz_lines?: string[] | null;
            /** Pdf Base64 */
            pdf_base64?: string | null;
            /** Template Id */
            template_id?: string | null;
        };
        /** V2UploadIn */
        V2UploadIn: {
            /** Declared Type */
            declared_type: string;
            /** Sha256 */
            sha256: string;
            /** Size Bytes */
            size_bytes: number;
            /** Subject Ref */
            subject_ref?: string | null;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** ReleaseBody */
        zid_platform__modules__evidence__api__evidence__ReleaseBody: {
            /** Reason */
            reason: string;
        };
        /** ResolveBody */
        zid_platform__modules__evidence__api__evidence__ResolveBody: {
            /** Case Ref */
            case_ref: string;
            /** Handle */
            handle: string;
            /** Purpose */
            purpose: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    get_artifact_v1_evidence_artifacts__artifact_type___artifact_ref__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                artifact_type: string;
                artifact_ref: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    audit_events_v1_evidence_audit_events_get: {
        parameters: {
            query?: {
                event_type?: string | null;
                before?: string | null;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    export_audit_events_v1_evidence_audit_events_export_get: {
        parameters: {
            query?: {
                after?: string | null;
                event_type?: string | null;
                limit?: number;
                format?: "json" | "ndjson";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    bulk_exports_v1_evidence_bulk_exports_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    bulk_export_v1_evidence_bulk_exports_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BulkBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    bulk_export_download_v1_evidence_bulk_exports__export_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                export_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_cases_v1_evidence_cases_get: {
        parameters: {
            query?: {
                status?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    open_case_v1_evidence_cases_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CaseBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_case_v1_evidence_cases__case_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                case_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_case_item_v1_evidence_cases__case_id__items_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                case_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CaseItemBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    close_case_v1_evidence_cases__case_id__close_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                case_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resolve_v1_evidence_handles_resolve_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__evidence__api__evidence__ResolveBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    keys_v1_evidence_keys_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    list_holds_v1_evidence_legal_holds_get: {
        parameters: {
            query?: {
                status?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_hold_v1_evidence_legal_holds_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["HoldBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    release_hold_v1_evidence_legal_holds__hold_id__release_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                hold_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__evidence__api__evidence__ReleaseBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    export_v1_evidence_packages_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PackageBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    verify_partition_v1_evidence_partition_verify_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    get_provenance_v1_evidence_provenance__entity_type___entity_id__get: {
        parameters: {
            query?: {
                depth?: number;
            };
            header?: never;
            path: {
                entity_type: string;
                entity_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_receipts_v1_evidence_receipts_get: {
        parameters: {
            query?: {
                action?: string | null;
                outcome?: string | null;
                evidence_class?: string | null;
                actor_handle?: string | null;
                before?: string | null;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_receipt_v1_evidence_receipts__receipt_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                receipt_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    proof_v1_evidence_receipts__receipt_id__proof_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                receipt_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    annotate_v1_evidence_receipts__receipt_id__annotate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                receipt_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AnnotateBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    run_replay_v1_evidence_receipts__receipt_id__replay_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                receipt_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    verify_v1_evidence_receipts__receipt_id__verify_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                receipt_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    import_baseline_v2_assurance_baselines_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatBaselineIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    confirm_risks_v2_assurance_baselines_risk_confirmations_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatRiskIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    open_defect_v2_assurance_defects_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatDefectIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    verify_evidence_v2_assurance_evidence_verify_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatVerifyIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_fixtures_v2_assurance_fixtures_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    create_fixture_v2_assurance_fixtures_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatFixtureIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    review_fixture_v2_assurance_fixtures__fixture_id__review_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                fixture_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatFixtureReviewIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_revision_v2_assurance_fixtures__fixture_id__revisions_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                fixture_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatRevisionIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    propose_policy_v2_assurance_policies_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatPolicyIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_policy_v2_assurance_policies__policy_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                policy_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    sign_policy_v2_assurance_policies__policy_id__signatures_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                policy_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatPolicySignIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    propose_from_draft_v2_assurance_policies_from_draft_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatDraftIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    provider_lab_report_v2_assurance_provider_lab_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    provider_parity_v2_assurance_provider_lab_parity_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatParityIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    run_registry_source_v2_assurance_registry_sources__name__run_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                name: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    registry_watch_v2_assurance_registry_watch_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatWatchIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_candidate_v2_assurance_release_candidates_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatCandidateIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preview_v2_assurance_release_candidates__release_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                release_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    decide_v2_assurance_releases__release_id__decisions_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                release_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatDecisionIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    rollback_v2_assurance_releases__release_id__rollback_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                release_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatRollbackIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    requirements_v2_assurance_requirements_get: {
        parameters: {
            query?: {
                suite?: string | null;
                status?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    assign_role_v2_assurance_role_assignments_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatRoleIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_runs_v2_assurance_test_runs_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    create_run_v2_assurance_test_runs_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatRunIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    sign_manifest_v2_assurance_test_runs__run_id__cases__case_id__manifest_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
                case_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatManifestIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    append_event_v2_assurance_test_runs__run_id__events_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UatEventIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    evidence_v2_assurance_test_runs__run_id__evidence_get: {
        parameters: {
            query?: {
                audience?: string;
            };
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    extract_v2_documents_extract_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["V2ExtractIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    validate_document_v2_documents_validate_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["V2DocumentIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    read_validation_v2_documents__validation_ref__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                validation_ref: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_uploads_v2_evidence_uploads_get: {
        parameters: {
            query?: {
                subject_ref?: string | null;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    announce_upload_v2_evidence_uploads_post: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["V2UploadIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    send_content_v2_evidence_uploads__upload_id__content_put: {
        parameters: {
            query?: never;
            header: {
                "X-Upload-Token": string;
            };
            path: {
                upload_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_evidence_v2_evidence__evidence_ref__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                evidence_ref: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    manifest_v2_evidence__manifest_ref__manifest_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                manifest_ref: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}
