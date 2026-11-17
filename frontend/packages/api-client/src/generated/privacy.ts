// GENERATED from contracts/openapi/public/privacy.json by packages/api-client/openapi/codegen.mjs. Do not edit.
export interface paths {
    "/v1/privacy/admin/automated-decision-profiles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Adm */
        get: operations["list_adm_v1_privacy_admin_automated_decision_profiles_get"];
        put?: never;
        /** Create Adm */
        post: operations["create_adm_v1_privacy_admin_automated_decision_profiles_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/data-map/{subject_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Data Map */
        get: operations["data_map_v1_privacy_admin_data_map__subject_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/notices": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Notices */
        get: operations["list_notices_v1_privacy_admin_notices_get"];
        put?: never;
        /** Publish Notice */
        post: operations["publish_notice_v1_privacy_admin_notices_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/processing-activities": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Activities */
        get: operations["list_activities_v1_privacy_admin_processing_activities_get"];
        put?: never;
        /** Create Activity */
        post: operations["create_activity_v1_privacy_admin_processing_activities_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/processing-activities/{activity_id}/role-assignments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Assign Role */
        post: operations["assign_role_v1_privacy_admin_processing_activities__activity_id__role_assignments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/processing-activities/{activity_id}:activate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Activate Activity */
        post: operations["activate_activity_v1_privacy_admin_processing_activities__activity_id__activate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/processors": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Processors */
        get: operations["list_processors_v1_privacy_admin_processors_get"];
        put?: never;
        /** Register Processor */
        post: operations["register_processor_v1_privacy_admin_processors_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/processors/{processor_id}:approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Approve Processor */
        post: operations["approve_processor_v1_privacy_admin_processors__processor_id__approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/processors/{processor_id}:suspend": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Suspend Processor */
        post: operations["suspend_processor_v1_privacy_admin_processors__processor_id__suspend_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/purposes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Purposes */
        get: operations["list_purposes_v1_privacy_admin_purposes_get"];
        put?: never;
        /** Create Purpose */
        post: operations["create_purpose_v1_privacy_admin_purposes_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/purposes/{purpose_id}:activate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Activate Purpose
         * @description The activating administrator reviews the draft and must not be its author (DB check).
         */
        post: operations["activate_purpose_v1_privacy_admin_purposes__purpose_id__activate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/release-check": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Release Check
         * @description DEC-F3: launch flows without an approved role assignment block G4.
         */
        get: operations["release_check_v1_privacy_admin_release_check_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/restrictions/{restriction_id}:lift": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Lift */
        post: operations["lift_v1_privacy_admin_restrictions__restriction_id__lift_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/retention-policies": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Retention */
        get: operations["list_retention_v1_privacy_admin_retention_policies_get"];
        put?: never;
        /** Create Retention */
        post: operations["create_retention_v1_privacy_admin_retention_policies_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/rights-requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Requests */
        get: operations["list_requests_v1_privacy_admin_rights_requests_get"];
        put?: never;
        /** Open On Behalf */
        post: operations["open_on_behalf_v1_privacy_admin_rights_requests_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/rights-requests/{request_id}/items/{item_id}:acknowledge": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Acknowledge */
        post: operations["acknowledge_v1_privacy_admin_rights_requests__request_id__items__item_id__acknowledge_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/rights-requests/{request_id}:close": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Close */
        post: operations["close_v1_privacy_admin_rights_requests__request_id__close_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/rights-requests/{request_id}:fulfill": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Fulfill */
        post: operations["fulfill_v1_privacy_admin_rights_requests__request_id__fulfill_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/rights-requests/{request_id}:verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Verify */
        post: operations["verify_v1_privacy_admin_rights_requests__request_id__verify_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/admin/role-map": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Role Map */
        get: operations["role_map_v1_privacy_admin_role_map_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/consent-options": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Consent Options
         * @description What the signed-in person can consent to, each with the exact notice to cite when giving consent.
         */
        get: operations["consent_options_v1_privacy_consent_options_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/consents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** My Consents */
        get: operations["my_consents_v1_privacy_consents_get"];
        put?: never;
        /** Give Consent */
        post: operations["give_consent_v1_privacy_consents_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/consents/{consent_id}:withdraw": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Withdraw */
        post: operations["withdraw_v1_privacy_consents__consent_id__withdraw_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/jurisdiction-packs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Jurisdiction Packs */
        get: operations["jurisdiction_packs_v1_privacy_jurisdiction_packs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/preferences": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** My Preferences */
        get: operations["my_preferences_v1_privacy_preferences_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/preferences/{preference_type}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Put Preference */
        put: operations["put_preference_v1_privacy_preferences__preference_type__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/preferences:signal": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Preference Signal
         * @description Global Privacy Control: a valid `Sec-GPC: 1` opts out of sale/sharing (PRIV-21).
         */
        post: operations["preference_signal_v1_privacy_preferences_signal_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/purposes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Purposes */
        get: operations["purposes_v1_privacy_purposes_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/rights-requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Open Request */
        post: operations["open_request_v1_privacy_rights_requests_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/rights-requests/mine": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** My Requests */
        get: operations["my_requests_v1_privacy_rights_requests_mine_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/rights-requests/{request_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Request */
        get: operations["get_request_v1_privacy_rights_requests__request_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/privacy/rights-requests/{request_id}/package": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Package
         * @description Delivery needs a fresh strong authentication (§15 "step-up authenticated delivery").
         */
        get: operations["package_v1_privacy_rights_requests__request_id__package_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/decisions/{decision_id}/appeals": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Appeal */
        post: operations["appeal_v2_decisions__decision_id__appeals_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/decisions/{decision_id}/human-reviews": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Human Review */
        post: operations["human_review_v2_decisions__decision_id__human_reviews_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/adm-evaluations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Adm Evaluation */
        post: operations["adm_evaluation_v2_privacy_adm_evaluations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/authorization-evaluations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**  Purpose */
        post: operations["_purpose_v2_privacy_authorization_evaluations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/breaches": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Breaches
         * @description The tenant's breach cases with clocks and capture state (CR-108).
         */
        get: operations["list_breaches_v2_privacy_breaches_get"];
        put?: never;
        /** Open Breach */
        post: operations["open_breach_v2_privacy_breaches_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/breaches/{breach_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Breach */
        get: operations["get_breach_v2_privacy_breaches__breach_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/breaches/{breach_id}/biometric-capture:pause": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Pause Capture
         * @description Containment (WF-04 B09, CR-108): no biometric method runs for this tenant in the case's jurisdictions until it is released or closed.
         */
        post: operations["pause_capture_v2_privacy_breaches__breach_id__biometric_capture_pause_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/breaches/{breach_id}/biometric-capture:release": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Release Capture
         * @description Lift the containment: by someone other than whoever paused it, with a reason (CR-108).
         */
        post: operations["release_capture_v2_privacy_breaches__breach_id__biometric_capture_release_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/breaches/{breach_id}/close": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Close Breach */
        post: operations["close_breach_v2_privacy_breaches__breach_id__close_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/breaches/{breach_id}/notifications": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Notified */
        post: operations["notified_v2_privacy_breaches__breach_id__notifications_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/consent-records": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Give Consent */
        post: operations["give_consent_v2_privacy_consent_records_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/consent-records/{consent_id}:withdraw": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Withdraw Consent */
        post: operations["withdraw_consent_v2_privacy_consent_records__consent_id__withdraw_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/dpia-packages": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Packages
         * @description The tenant's DPIA packages with their release state (the workbench list; CR-108).
         */
        get: operations["list_packages_v2_privacy_dpia_packages_get"];
        put?: never;
        /** Create Package */
        post: operations["create_package_v2_privacy_dpia_packages_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/dpia-packages/{package_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Package */
        get: operations["get_package_v2_privacy_dpia_packages__package_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/dpia-packages/{package_id}/decisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Decide Package */
        post: operations["decide_package_v2_privacy_dpia_packages__package_id__decisions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/dpia-packages/{package_id}/gate-signoffs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Sign Gate */
        post: operations["sign_gate_v2_privacy_dpia_packages__package_id__gate_signoffs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/dpia-packages/{package_id}/risks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Risks */
        put: operations["set_risks_v2_privacy_dpia_packages__package_id__risks_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/erasure-jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Jobs
         * @description The tenant's erasure jobs, newest first, with their per-class state (CR-108).
         */
        get: operations["list_jobs_v2_privacy_erasure_jobs_get"];
        put?: never;
        /** Open Job */
        post: operations["open_job_v2_privacy_erasure_jobs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/erasure-jobs/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Job */
        get: operations["get_job_v2_privacy_erasure_jobs__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/erasure-jobs/{job_id}/proof": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Proof */
        get: operations["get_proof_v2_privacy_erasure_jobs__job_id__proof_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/erasure-jobs/{job_id}/tasks/{task_id}:attest": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Attest */
        post: operations["attest_v2_privacy_erasure_jobs__job_id__tasks__task_id__attest_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/erasure-jobs:preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Preview Job
         * @description B05 dry run (CR-108): what an erasure would dispose, hold or send to providers, in deletion order. Changes nothing.
         */
        post: operations["preview_job_v2_privacy_erasure_jobs_preview_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/exports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Create Export
         * @description A signed, expiring, recipient-bound export of the named claims only (AT-047). The caller supplies the source claims; nothing is stored.
         */
        post: operations["create_export_v2_privacy_exports_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/exports:verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Verify Export */
        post: operations["verify_export_v2_privacy_exports_verify_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/holds": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Hold */
        post: operations["create_hold_v2_privacy_holds_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/holds/{hold_id}/release": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Release Hold */
        post: operations["release_hold_v2_privacy_holds__hold_id__release_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/kpis": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Kpis */
        get: operations["kpis_v2_privacy_kpis_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/model-evaluations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Record Model Evaluation */
        post: operations["record_model_evaluation_v2_privacy_model_evaluations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/models": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Register Model */
        post: operations["register_model_v2_privacy_models_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/models/{model_ref}/versions/{version}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Model */
        get: operations["get_model_v2_privacy_models__model_ref__versions__version__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/models/{model_ref}/versions/{version}:promote": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Promote Model */
        post: operations["promote_model_v2_privacy_models__model_ref__versions__version__promote_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/models/{model_ref}/versions/{version}:rollback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Rollback Model */
        post: operations["rollback_model_v2_privacy_models__model_ref__versions__version__rollback_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/models/{model_ref}:observe": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Observe Model */
        post: operations["observe_model_v2_privacy_models__model_ref__observe_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/outcome-routing": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Outcome Route */
        post: operations["outcome_route_v2_privacy_outcome_routing_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/processors/{processor_id}/receipt-key": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Receipt Key */
        post: operations["receipt_key_v2_privacy_processors__processor_id__receipt_key_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/provider-controls/{service_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Provider Controls */
        get: operations["provider_controls_v2_privacy_provider_controls__service_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/purpose-decisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**  Purpose */
        post: operations["_purpose_v2_privacy_purpose_decisions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/release-gates/{market}/{product}/{version}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Release Gate */
        get: operations["release_gate_v2_privacy_release_gates__market___product___version__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Open Request */
        post: operations["open_request_v2_privacy_requests_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/requests/{request_id}/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Export
         * @description Only the person the data is about, with fresh strong authentication; never an administrator, never another person's data.
         */
        get: operations["export_v2_privacy_requests__request_id__export_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/requests/{request_id}/processor-tasks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Processor Task */
        post: operations["processor_task_v2_privacy_requests__request_id__processor_tasks_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/requests/{request_id}/proof-of-authority": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Proof Of Authority */
        post: operations["proof_of_authority_v2_privacy_requests__request_id__proof_of_authority_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/requests/{request_id}/review-decisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Review Decision */
        post: operations["review_decision_v2_privacy_requests__request_id__review_decisions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/requests/{request_id}/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Status */
        get: operations["status_v2_privacy_requests__request_id__status_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/retention-evaluations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Evaluate Retention */
        post: operations["evaluate_retention_v2_privacy_retention_evaluations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/retention-policies/{policy_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Policy */
        get: operations["get_policy_v2_privacy_retention_policies__policy_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/reviews/{review_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Review */
        get: operations["get_review_v2_privacy_reviews__review_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/reviews/{review_id}/decisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Decide Review */
        post: operations["decide_review_v2_privacy_reviews__review_id__decisions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/subprocessor-changes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Notify Change */
        post: operations["notify_change_v2_privacy_subprocessor_changes_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/subprocessor-changes/{change_id}:decide": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Decide Change */
        post: operations["decide_change_v2_privacy_subprocessor_changes__change_id__decide_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/privacy/transfer-decisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Transfer */
        post: operations["transfer_v2_privacy_transfer_decisions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/providers/entitlements": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Entitlements */
        get: operations["list_entitlements_v2_providers_entitlements_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/providers/route": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Dry Run Route */
        post: operations["dry_run_route_v2_providers_route_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/providers/{provider_id}/deletion-receipts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Deletion Receipt */
        post: operations["deletion_receipt_v2_providers__provider_id__deletion_receipts_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/providers/{provider_id}/entitlements": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Record Entitlement */
        post: operations["record_entitlement_v2_providers__provider_id__entitlements_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/providers/{provider_id}/entitlements/{service}/impact": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Withdrawal Impact
         * @description Impact analysis before a kill switch is pulled (Doc 03 RCG-G08). Changes nothing.
         */
        get: operations["withdrawal_impact_v2_providers__provider_id__entitlements__service__impact_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/providers/{provider_id}/entitlements/{service}/revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Revoke Entitlement */
        post: operations["revoke_entitlement_v2_providers__provider_id__entitlements__service__revoke_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/providers/{provider_id}/entitlements/{service}/suspend": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Suspend Entitlement */
        post: operations["suspend_entitlement_v2_providers__provider_id__entitlements__service__suspend_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/verification-sessions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Session */
        post: operations["create_session_v2_verification_sessions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/verification-sessions/{session_ref}/alternatives": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Alternatives */
        get: operations["alternatives_v2_verification_sessions__session_ref__alternatives_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/verification-sessions/{session_ref}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel Session */
        post: operations["cancel_session_v2_verification_sessions__session_ref__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/verification-sessions/{session_ref}/consent-records": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Session Consent */
        post: operations["session_consent_v2_verification_sessions__session_ref__consent_records_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/verification-sessions/{session_ref}/notices/acknowledgments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Acknowledge */
        post: operations["acknowledge_v2_verification_sessions__session_ref__notices_acknowledgments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/verification-sessions/{session_ref}/route": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Choose Route */
        post: operations["choose_route_v2_verification_sessions__session_ref__route_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/verification-sessions/{session_ref}/subject-status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Subject Status */
        get: operations["subject_status_v2_verification_sessions__session_ref__subject_status_get"];
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
        /** AckBody */
        AckBody: {
            /** Ack Ref */
            ack_ref: string;
        };
        /** ActivityBody */
        ActivityBody: {
            /** Code */
            code: string;
            /** Data Categories */
            data_categories: string[];
            /** Data Subject Classes */
            data_subject_classes?: string[];
            /** Dpia Ref */
            dpia_ref?: string | null;
            /** Jurisdiction Pack Id */
            jurisdiction_pack_id: string;
            /**
             * Launch Flow
             * @default false
             */
            launch_flow: boolean;
            /** Lawful Basis */
            lawful_basis: string;
            /** Name */
            name: string;
            /** Purpose Codes */
            purpose_codes: string[];
            /** Recipient Classes */
            recipient_classes?: string[];
            /** Residency Profile */
            residency_profile?: string | null;
            /** Retention Policy Code */
            retention_policy_code?: string | null;
            /** Source Classes */
            source_classes?: string[];
            /** Special Category Condition */
            special_category_condition?: string | null;
        };
        /** AdmBody */
        AdmBody: {
            /** Decision Class */
            decision_class: string;
            /** Explanation Profile */
            explanation_profile: string;
            /** Human Review Route */
            human_review_route: string;
            /** Input Data Categories */
            input_data_categories: string[];
            /** Jurisdiction Rules */
            jurisdiction_rules?: {
                [key: string]: string;
            };
            /** Model Or Rules Ref */
            model_or_rules_ref: string;
            /** Purpose Code */
            purpose_code: string;
            /** Risk Assessment Ref */
            risk_assessment_ref?: string | null;
            /** Significance Class */
            significance_class: string;
            /** Solely Automated */
            solely_automated: boolean;
            /**
             * Special Category Use
             * @default false
             */
            special_category_use: boolean;
        };
        /** AdmIn */
        AdmIn: {
            /** Jurisdiction Pack Id */
            jurisdiction_pack_id: string;
            /** Safeguards Offered */
            safeguards_offered?: string[];
            /** Significant */
            significant: boolean;
            /** Solely Automated */
            solely_automated: boolean;
            /**
             * Special Category
             * @default false
             */
            special_category: boolean;
            /** Special Condition Ref */
            special_condition_ref?: string | null;
        };
        /** ConsentBody */
        ConsentBody: {
            /** Activity Code */
            activity_code: string;
            /** Affirmative Action */
            affirmative_action: string;
            /** Capture Method */
            capture_method: string;
            /** Expires At */
            expires_at?: string | null;
            /** Notice Version */
            notice_version: string;
            /** Purpose Code */
            purpose_code: string;
        };
        /** ConsentIn */
        ConsentIn: {
            /** Activity Code */
            activity_code: string;
            /** Affirmative Action */
            affirmative_action: string;
            /** Capture Method */
            capture_method: string;
            /** Expires At */
            expires_at?: string | null;
            /** Notice Version */
            notice_version: string;
            /** Purpose Code */
            purpose_code: string;
        };
        /** EntitlementIn */
        EntitlementIn: {
            /** Assurance */
            assurance: number;
            /**
             * Cost Rank
             * @default 0
             */
            cost_rank: number;
            /** Countries */
            countries: string[];
            /** Processing Regions */
            processing_regions: string[];
            /** Purposes */
            purposes: string[];
            /** Service Code */
            service_code: string;
            /**
             * Valid From
             * Format: date
             */
            valid_from: string;
            /** Valid Until */
            valid_until?: string | null;
        };
        /** EvaluationIn */
        EvaluationIn: {
            /** Legal Hold Refs */
            legal_hold_refs?: string[];
            /** Object Ref */
            object_ref: string;
            /** Purpose Id */
            purpose_id: string;
        };
        /** ExportIn */
        ExportIn: {
            /** Claims */
            claims: {
                [key: string]: unknown;
            };
            /** Include */
            include: string[];
            /** Purpose */
            purpose: string;
            /** Recipient */
            recipient: string;
            /**
             * Ttl Seconds
             * @default 3600
             */
            ttl_seconds: number;
        };
        /** ExportVerifyIn */
        ExportVerifyIn: {
            /** Expected Recipient */
            expected_recipient?: string | null;
            /** Token */
            token: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** HoldIn */
        HoldIn: {
            /** Approvers */
            approvers?: string[];
            /**
             * Authorization
             * Format: uuid
             */
            authorization: string;
            /** Basis */
            basis: string;
            /** Expires At */
            expires_at?: string | null;
            /** Reference */
            reference: string;
            /**
             * Review At
             * Format: date-time
             */
            review_at: string;
            scope: components["schemas"]["Scope"];
        };
        /** OnBehalfBody */
        OnBehalfBody: {
            /** Authorized Agent Ref */
            authorized_agent_ref?: string | null;
            /** Jurisdiction Pack Id */
            jurisdiction_pack_id: string;
            /** Request Type */
            request_type: string;
            /** Scope */
            scope?: {
                [key: string]: string;
            };
            /**
             * Subject Principal Id
             * Format: uuid
             */
            subject_principal_id: string;
        };
        /** PreferenceBody */
        PreferenceBody: {
            /** Value */
            value: boolean;
        };
        /** PrivV2AckIn */
        PrivV2AckIn: {
            /** Language */
            language: string;
            /** Notice Version */
            notice_version: string;
            /** Subject Ref */
            subject_ref?: string | null;
        };
        /** PrivV2AttestIn */
        PrivV2AttestIn: {
            /** Statement */
            statement: string;
        };
        /** PrivV2BreachIn */
        PrivV2BreachIn: {
            /**
             * Detected At
             * Format: date-time
             */
            detected_at: string;
            /** Jurisdictions */
            jurisdictions: string[];
            /** Pause Biometric Capture */
            pause_biometric_capture?: boolean | null;
            /**
             * Severity
             * @enum {string}
             */
            severity: "LOW" | "MODERATE" | "HIGH" | "CRITICAL";
            /** Subjects Estimate */
            subjects_estimate?: number | null;
            /** Summary */
            summary: string;
        };
        /** PrivV2CaptureReleaseIn */
        PrivV2CaptureReleaseIn: {
            /** Reason */
            reason: string;
        };
        /** PrivV2ChangeIn */
        PrivV2ChangeIn: {
            /** Detail */
            detail: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "SUBPROCESSOR_ADDED" | "SUBPROCESSOR_REMOVED" | "LOCATION_ADDED" | "LOCATION_REMOVED";
            /**
             * Notified At
             * Format: date-time
             */
            notified_at: string;
            /** Processor Id */
            processor_id: string;
        };
        /** PrivV2DecideIn */
        PrivV2DecideIn: {
            /** Approve */
            approve: boolean;
        };
        /** PrivV2ErasureIn */
        PrivV2ErasureIn: {
            /** Legal Basis */
            legal_basis: string;
            /** Reason */
            reason: string;
            /**
             * Scope
             * @default ALL
             * @enum {string}
             */
            scope: "ALL" | "PROVIDERS_ONLY" | "FIRST_PARTY_ONLY";
            /** Subject Ref */
            subject_ref: string;
        };
        /** PrivV2ErasurePreviewIn */
        PrivV2ErasurePreviewIn: {
            /**
             * Scope
             * @default ALL
             * @enum {string}
             */
            scope: "ALL" | "PROVIDERS_ONLY" | "FIRST_PARTY_ONLY";
            /** Subject Ref */
            subject_ref: string;
        };
        /** PrivV2EvaluationIn */
        PrivV2EvaluationIn: {
            /**
             * Accessible Alternative Confirmed
             * @default false
             */
            accessible_alternative_confirmed: boolean;
            /** Audit Attributes */
            audit_attributes?: string[];
            /** Cohorts */
            cohorts: number;
            /** Evaluator Ref */
            evaluator_ref: string;
            /** Independent */
            independent: boolean;
            /** Metrics */
            metrics?: {
                [key: string]: unknown;
            };
            /** Model Ref */
            model_ref: string;
            /** Report Ref */
            report_ref?: string | null;
            /** Version */
            version: string;
        };
        /** PrivV2GateIn */
        PrivV2GateIn: {
            /** Evidence Uri */
            evidence_uri: string;
            /**
             * Gate
             * @enum {string}
             */
            gate: "G0" | "G1" | "G2" | "G3" | "G4" | "G5" | "G6" | "G7";
            /** Role */
            role: string;
        };
        /** PrivV2KeyIn */
        PrivV2KeyIn: {
            /** Public Key */
            public_key: string;
        };
        /** PrivV2ModelIn */
        PrivV2ModelIn: {
            /** Artifact Sha256 */
            artifact_sha256?: string | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "face_match" | "pad" | "fraud" | "document";
            /** Live Features */
            live_features?: string[];
            /** Model Ref */
            model_ref: string;
            /** Provider Service Ref */
            provider_service_ref?: string | null;
            /** Version */
            version: string;
        };
        /** PrivV2NotifiedIn */
        PrivV2NotifiedIn: {
            /** Jurisdiction */
            jurisdiction: string;
            /**
             * Notified At
             * Format: date-time
             */
            notified_at: string;
            /** Reference */
            reference: string;
        };
        /** PrivV2ObserveIn */
        PrivV2ObserveIn: {
            /** Seen Version */
            seen_version: string;
        };
        /** PrivV2PackageDecisionIn */
        PrivV2PackageDecisionIn: {
            /** Approve */
            approve: boolean;
            /** Rationale */
            rationale: string;
            /**
             * Valid Days
             * @default 365
             */
            valid_days: number;
        };
        /** PrivV2PackageIn */
        PrivV2PackageIn: {
            /** Controller */
            controller: string;
            /** Market */
            market: string;
            /** Model Version */
            model_version?: string | null;
            /** Product */
            product: string;
            /** Provider Service Ref */
            provider_service_ref?: string | null;
            /** Purpose Code */
            purpose_code: string;
            /** Version */
            version: string;
        };
        /** PrivV2PurposeIn */
        PrivV2PurposeIn: {
            /** Activity Code */
            activity_code: string;
            /** Country */
            country?: string | null;
            /**
             * Data Category
             * @enum {string}
             */
            data_category: "D0" | "D1" | "D2" | "D3" | "D4";
            /** Jurisdiction Pack Id */
            jurisdiction_pack_id?: string | null;
            /** Necessity Ref */
            necessity_ref?: string | null;
            /** Notice Version */
            notice_version?: string | null;
            /** Provider Service Ref */
            provider_service_ref?: string | null;
            /** Purpose Code */
            purpose_code: string;
            /** Release Ref */
            release_ref?: string | null;
            /** Retention Policy Ref */
            retention_policy_ref?: string | null;
            /** Subject Ref */
            subject_ref?: string | null;
            /** Subregion */
            subregion?: string | null;
        };
        /** PrivV2ReceiptIn */
        PrivV2ReceiptIn: {
            /** Deleted At */
            deleted_at: string;
            /** Job Id */
            job_id: string;
            /** Object Count */
            object_count: number;
            /** Signature */
            signature: string;
            /** Task Id */
            task_id: string;
        };
        /** PrivV2RequestReviewIn */
        PrivV2RequestReviewIn: {
            /**
             * Action
             * @enum {string}
             */
            action: "FULFIL" | "DENY" | "PARTIAL" | "WITHDRAW";
            /** Evidence */
            evidence?: string[];
            /** Rule */
            rule: string;
        };
        /** PrivV2ReviewDecisionIn */
        PrivV2ReviewDecisionIn: {
            /** Correction Ref */
            correction_ref?: string | null;
            /**
             * Outcome
             * @enum {string}
             */
            outcome: "UPHELD" | "OVERTURNED" | "CORRECTED";
            /** Rationale */
            rationale: string;
        };
        /** PrivV2ReviewIn */
        PrivV2ReviewIn: {
            /** Statement */
            statement: string;
        };
        /** PrivV2RisksIn */
        PrivV2RisksIn: {
            /** Consultation Ref */
            consultation_ref?: string | null;
            /** Risks */
            risks: {
                [key: string]: unknown;
            }[];
        };
        /** PrivV2TransferIn */
        PrivV2TransferIn: {
            /**
             * Data Category
             * @enum {string}
             */
            data_category: "D0" | "D1" | "D2" | "D3" | "D4";
            /** Destination Country */
            destination_country: string;
            /** Jurisdiction Pack Id */
            jurisdiction_pack_id?: string | null;
            /** Origin Country */
            origin_country: string;
            /** Processor Id */
            processor_id: string;
            /** Purpose Code */
            purpose_code: string;
            /** Remote Access Countries */
            remote_access_countries?: string[];
        };
        /** ProcessorBody */
        ProcessorBody: {
            /** Contract Ref */
            contract_ref?: string | null;
            /** Data Categories */
            data_categories: string[];
            /** Deletion Sla Days */
            deletion_sla_days?: number | null;
            /** Locations */
            locations: string[];
            /** Name */
            name: string;
            /** Processing Activity Codes */
            processing_activity_codes?: string[];
            /** Processor Id */
            processor_id: string;
            /** Purposes */
            purposes?: string[];
            /** Role */
            role: string;
            /** Security Assurance */
            security_assurance?: string | null;
            /** Transfer Mechanism */
            transfer_mechanism?: string | null;
        };
        /** ProofIn */
        ProofIn: {
            /** Level */
            level: number;
            /** Method */
            method: string;
            /** Verified */
            verified: boolean;
        };
        /** PurposeBody */
        PurposeBody: {
            /** Code */
            code: string;
            /** Compatible Secondary Purposes */
            compatible_secondary_purposes?: string[];
            /** Data Categories */
            data_categories: string[];
            /** Description */
            description: string;
            /** Prohibited Uses */
            prohibited_uses?: string[];
            /** Recipient Classes */
            recipient_classes?: string[];
            /** Retention Policy Code */
            retention_policy_code?: string | null;
        };
        /** ReasonIn */
        ReasonIn: {
            /** Reason */
            reason: string;
        };
        /** RequestIn */
        RequestIn: {
            /** Jurisdiction */
            jurisdiction: string;
            /** Represented By */
            represented_by?: string | null;
            /**
             * Right
             * @enum {string}
             */
            right: "ACCESS" | "RECTIFICATION" | "ERASURE" | "RESTRICTION" | "OBJECTION" | "PORTABILITY" | "OPT_OUT_SALE_SHARING" | "LIMIT_SENSITIVE_PI" | "ADMT_ACCESS" | "ADMT_OPT_OUT" | "COMPLAINT" | "HUMAN_REVIEW";
            /** Scope */
            scope?: {
                [key: string]: string;
            };
            /** Subject Ref */
            subject_ref: string;
        };
        /** RetentionBody */
        RetentionBody: {
            /** Action */
            action: string;
            /**
             * Approval Request Id
             * Format: uuid
             */
            approval_request_id: string;
            /** Backup Erasure Window Days */
            backup_erasure_window_days?: number | null;
            /** Code */
            code: string;
            /** Jurisdiction Pack Id */
            jurisdiction_pack_id: string;
            /**
             * Legal Hold Suspends
             * @default true
             */
            legal_hold_suspends: boolean;
            /**
             * Legally Required
             * @default false
             */
            legally_required: boolean;
            /** Object Type */
            object_type: string;
            /** Purpose Code */
            purpose_code?: string | null;
            /** Retention Days */
            retention_days: number;
            /** Trigger */
            trigger: string;
        };
        /** RightsBody */
        RightsBody: {
            /** Jurisdiction Pack Id */
            jurisdiction_pack_id: string;
            /** Request Type */
            request_type: string;
            /** Scope */
            scope?: {
                [key: string]: string;
            };
        };
        /** RouteIn */
        RouteIn: {
            /** Country */
            country: string;
            /** Downgrade Approval Ref */
            downgrade_approval_ref?: string | null;
            /** Failing Over From */
            failing_over_from?: string | null;
            /** Processing Region */
            processing_region: string;
            /** Purpose */
            purpose: string;
            /**
             * Required Assurance
             * @default 0
             */
            required_assurance: number;
            /** Service Code */
            service_code: string;
        };
        /** RoutingIn */
        RoutingIn: {
            /**
             * Capture Quality Ok
             * @default true
             */
            capture_quality_ok: boolean;
            /**
             * High Impact
             * @default false
             */
            high_impact: boolean;
            /**
             * Injection Suspected
             * @default false
             */
            injection_suspected: boolean;
            /** Match Band */
            match_band?: string | null;
            /** Pad Band */
            pad_band?: string | null;
        };
        /** Scope */
        Scope: {
            /** Object Types */
            object_types?: string[];
            /** Subject Principal Ids */
            subject_principal_ids?: string[];
        };
        /** SessionConsentIn */
        SessionConsentIn: {
            /** Affirmative Action */
            affirmative_action: string;
            /** Capture Method */
            capture_method: string;
            /** Notice Version */
            notice_version: string;
        };
        /** SessionIn */
        SessionIn: {
            /** Activity Code */
            activity_code: string;
            /** Alternative Mode */
            alternative_mode?: string | null;
            /** Assurance Goal */
            assurance_goal?: string | null;
            /**
             * Data Category
             * @default D3
             * @enum {string}
             */
            data_category: "D0" | "D1" | "D2" | "D3" | "D4";
            /** Jurisdiction Pack Id */
            jurisdiction_pack_id?: string | null;
            /** Notice Version */
            notice_version?: string | null;
            /** Provider Service Ref */
            provider_service_ref?: string | null;
            /** Purpose Id */
            purpose_id: string;
            /** Region */
            region?: string | null;
            /** Subject Ref */
            subject_ref?: string | null;
            /** Subregion */
            subregion?: string | null;
        };
        /** SessionRouteIn */
        SessionRouteIn: {
            /** Mode */
            mode: string;
        };
        /** TaskIn */
        TaskIn: {
            /** Ack Ref */
            ack_ref: string;
            /** Item Id */
            item_id: string;
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
        /** VerifyBody */
        VerifyBody: {
            /** Level */
            level: number;
            /** Method */
            method: string;
            /** Verified */
            verified: boolean;
        };
        /** WithdrawBody */
        WithdrawBody: {
            /**
             * Reason
             * @default withdrawn by subject
             */
            reason: string;
        };
        /** WithdrawIn */
        WithdrawIn: {
            /**
             * Reason
             * @default withdrawn by subject
             */
            reason: string;
        };
        /** ReleaseBody */
        zid_platform__modules__evidence__api__evidence__ReleaseBody: {
            /** Reason */
            reason: string;
        };
        /** CloseBody */
        zid_platform__modules__privacy__api__privacy__CloseBody: {
            /** Exemptions */
            exemptions?: {
                [key: string]: string;
            }[];
            /** Outcome */
            outcome: string;
            /** Reason Code */
            reason_code: string;
        };
        /** NoticeBody */
        zid_platform__modules__privacy__api__privacy__NoticeBody: {
            /**
             * Approval Request Id
             * Format: uuid
             */
            approval_request_id: string;
            /** Audience */
            audience: string;
            /** Code */
            code: string;
            /** Content */
            content: string;
            /** Content Uri */
            content_uri?: string | null;
            /** Jurisdictions */
            jurisdictions?: string[];
            /** Processing Activity Codes */
            processing_activity_codes?: string[];
        };
        /** RoleBody */
        zid_platform__modules__privacy__api__privacy__RoleBody: {
            /** Approval Ref */
            approval_ref: string;
            /**
             * Approval Request Id
             * Format: uuid
             */
            approval_request_id: string;
            /** Contract Or Dpa Ref */
            contract_or_dpa_ref?: string | null;
            /**
             * Data Controller Principal Id
             * Format: uuid
             */
            data_controller_principal_id: string;
            /** Jurisdiction Pack Id */
            jurisdiction_pack_id: string;
            /** Processor Principal Ids */
            processor_principal_ids?: string[];
            /** Recipient Classes */
            recipient_classes?: string[];
            /** Valid To */
            valid_to?: string | null;
            /** Zoiko Role */
            zoiko_role: string;
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
    list_adm_v1_privacy_admin_automated_decision_profiles_get: {
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
    create_adm_v1_privacy_admin_automated_decision_profiles_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AdmBody"];
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
    data_map_v1_privacy_admin_data_map__subject_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                subject_id: string;
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
    list_notices_v1_privacy_admin_notices_get: {
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
    publish_notice_v1_privacy_admin_notices_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__privacy__api__privacy__NoticeBody"];
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
    list_activities_v1_privacy_admin_processing_activities_get: {
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
    create_activity_v1_privacy_admin_processing_activities_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ActivityBody"];
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
    assign_role_v1_privacy_admin_processing_activities__activity_id__role_assignments_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                activity_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__privacy__api__privacy__RoleBody"];
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
    activate_activity_v1_privacy_admin_processing_activities__activity_id__activate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                activity_id: string;
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
    list_processors_v1_privacy_admin_processors_get: {
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
    register_processor_v1_privacy_admin_processors_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProcessorBody"];
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
    approve_processor_v1_privacy_admin_processors__processor_id__approve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                processor_id: string;
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
    suspend_processor_v1_privacy_admin_processors__processor_id__suspend_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                processor_id: string;
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
    list_purposes_v1_privacy_admin_purposes_get: {
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
    create_purpose_v1_privacy_admin_purposes_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PurposeBody"];
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
    activate_purpose_v1_privacy_admin_purposes__purpose_id__activate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                purpose_id: string;
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
    release_check_v1_privacy_admin_release_check_get: {
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
    lift_v1_privacy_admin_restrictions__restriction_id__lift_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                restriction_id: string;
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
    list_retention_v1_privacy_admin_retention_policies_get: {
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
    create_retention_v1_privacy_admin_retention_policies_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RetentionBody"];
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
    list_requests_v1_privacy_admin_rights_requests_get: {
        parameters: {
            query?: {
                status?: string | null;
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
    open_on_behalf_v1_privacy_admin_rights_requests_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OnBehalfBody"];
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
    acknowledge_v1_privacy_admin_rights_requests__request_id__items__item_id__acknowledge_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
                item_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AckBody"];
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
    close_v1_privacy_admin_rights_requests__request_id__close_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__privacy__api__privacy__CloseBody"];
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
    fulfill_v1_privacy_admin_rights_requests__request_id__fulfill_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
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
    verify_v1_privacy_admin_rights_requests__request_id__verify_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["VerifyBody"];
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
    role_map_v1_privacy_admin_role_map_get: {
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
    consent_options_v1_privacy_consent_options_get: {
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
    my_consents_v1_privacy_consents_get: {
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
    give_consent_v1_privacy_consents_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConsentBody"];
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
    withdraw_v1_privacy_consents__consent_id__withdraw_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                consent_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WithdrawBody"];
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
    jurisdiction_packs_v1_privacy_jurisdiction_packs_get: {
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
    my_preferences_v1_privacy_preferences_get: {
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
    put_preference_v1_privacy_preferences__preference_type__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                preference_type: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PreferenceBody"];
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
    preference_signal_v1_privacy_preferences_signal_post: {
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
    purposes_v1_privacy_purposes_get: {
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
    open_request_v1_privacy_rights_requests_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RightsBody"];
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
    my_requests_v1_privacy_rights_requests_mine_get: {
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
    get_request_v1_privacy_rights_requests__request_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
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
    package_v1_privacy_rights_requests__request_id__package_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
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
    appeal_v2_decisions__decision_id__appeals_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                decision_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2ReviewIn"];
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
    human_review_v2_decisions__decision_id__human_reviews_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                decision_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2ReviewIn"];
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
    adm_evaluation_v2_privacy_adm_evaluations_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AdmIn"];
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
    _purpose_v2_privacy_authorization_evaluations_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2PurposeIn"];
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
    list_breaches_v2_privacy_breaches_get: {
        parameters: {
            query?: {
                open_only?: boolean;
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
    open_breach_v2_privacy_breaches_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2BreachIn"];
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
    get_breach_v2_privacy_breaches__breach_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                breach_id: string;
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
    pause_capture_v2_privacy_breaches__breach_id__biometric_capture_pause_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                breach_id: string;
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
    release_capture_v2_privacy_breaches__breach_id__biometric_capture_release_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                breach_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2CaptureReleaseIn"];
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
    close_breach_v2_privacy_breaches__breach_id__close_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                breach_id: string;
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
    notified_v2_privacy_breaches__breach_id__notifications_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                breach_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2NotifiedIn"];
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
    give_consent_v2_privacy_consent_records_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConsentIn"];
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
    withdraw_consent_v2_privacy_consent_records__consent_id__withdraw_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                consent_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WithdrawIn"];
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
    list_packages_v2_privacy_dpia_packages_get: {
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
    create_package_v2_privacy_dpia_packages_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2PackageIn"];
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
    get_package_v2_privacy_dpia_packages__package_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                package_id: string;
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
    decide_package_v2_privacy_dpia_packages__package_id__decisions_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                package_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2PackageDecisionIn"];
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
    sign_gate_v2_privacy_dpia_packages__package_id__gate_signoffs_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                package_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2GateIn"];
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
    set_risks_v2_privacy_dpia_packages__package_id__risks_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                package_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2RisksIn"];
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
    list_jobs_v2_privacy_erasure_jobs_get: {
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
    open_job_v2_privacy_erasure_jobs_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2ErasureIn"];
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
    get_job_v2_privacy_erasure_jobs__job_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
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
    get_proof_v2_privacy_erasure_jobs__job_id__proof_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
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
    attest_v2_privacy_erasure_jobs__job_id__tasks__task_id__attest_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
                task_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2AttestIn"];
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
    preview_job_v2_privacy_erasure_jobs_preview_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2ErasurePreviewIn"];
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
    create_export_v2_privacy_exports_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExportIn"];
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
    verify_export_v2_privacy_exports_verify_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExportVerifyIn"];
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
    create_hold_v2_privacy_holds_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["HoldIn"];
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
    release_hold_v2_privacy_holds__hold_id__release_post: {
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
    kpis_v2_privacy_kpis_get: {
        parameters: {
            query?: {
                days?: number;
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
    record_model_evaluation_v2_privacy_model_evaluations_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2EvaluationIn"];
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
    register_model_v2_privacy_models_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2ModelIn"];
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
    get_model_v2_privacy_models__model_ref__versions__version__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                model_ref: string;
                version: string;
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
    promote_model_v2_privacy_models__model_ref__versions__version__promote_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                model_ref: string;
                version: string;
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
    rollback_model_v2_privacy_models__model_ref__versions__version__rollback_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                model_ref: string;
                version: string;
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
    observe_model_v2_privacy_models__model_ref__observe_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                model_ref: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2ObserveIn"];
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
    outcome_route_v2_privacy_outcome_routing_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RoutingIn"];
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
    receipt_key_v2_privacy_processors__processor_id__receipt_key_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                processor_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2KeyIn"];
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
    provider_controls_v2_privacy_provider_controls__service_id__get: {
        parameters: {
            query: {
                country: string;
            };
            header?: never;
            path: {
                service_id: string;
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
    _purpose_v2_privacy_purpose_decisions_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2PurposeIn"];
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
    release_gate_v2_privacy_release_gates__market___product___version__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                market: string;
                product: string;
                version: string;
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
    open_request_v2_privacy_requests_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RequestIn"];
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
    export_v2_privacy_requests__request_id__export_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
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
    processor_task_v2_privacy_requests__request_id__processor_tasks_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TaskIn"];
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
    proof_of_authority_v2_privacy_requests__request_id__proof_of_authority_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProofIn"];
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
    review_decision_v2_privacy_requests__request_id__review_decisions_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2RequestReviewIn"];
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
    status_v2_privacy_requests__request_id__status_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                request_id: string;
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
    evaluate_retention_v2_privacy_retention_evaluations_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EvaluationIn"];
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
    get_policy_v2_privacy_retention_policies__policy_id__get: {
        parameters: {
            query?: {
                version?: number | null;
            };
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
    get_review_v2_privacy_reviews__review_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                review_id: string;
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
    decide_review_v2_privacy_reviews__review_id__decisions_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                review_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2ReviewDecisionIn"];
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
    notify_change_v2_privacy_subprocessor_changes_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2ChangeIn"];
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
    decide_change_v2_privacy_subprocessor_changes__change_id__decide_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                change_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2DecideIn"];
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
    transfer_v2_privacy_transfer_decisions_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2TransferIn"];
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
    list_entitlements_v2_providers_entitlements_get: {
        parameters: {
            query?: {
                provider_id?: string | null;
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
    dry_run_route_v2_providers_route_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RouteIn"];
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
    deletion_receipt_v2_providers__provider_id__deletion_receipts_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                provider_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2ReceiptIn"];
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
    record_entitlement_v2_providers__provider_id__entitlements_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                provider_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EntitlementIn"];
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
    withdrawal_impact_v2_providers__provider_id__entitlements__service__impact_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                provider_id: string;
                service: string;
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
    revoke_entitlement_v2_providers__provider_id__entitlements__service__revoke_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                provider_id: string;
                service: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReasonIn"];
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
    suspend_entitlement_v2_providers__provider_id__entitlements__service__suspend_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                provider_id: string;
                service: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReasonIn"];
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
    create_session_v2_verification_sessions_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SessionIn"];
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
    alternatives_v2_verification_sessions__session_ref__alternatives_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_ref: string;
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
    cancel_session_v2_verification_sessions__session_ref__cancel_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_ref: string;
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
    session_consent_v2_verification_sessions__session_ref__consent_records_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_ref: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SessionConsentIn"];
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
    acknowledge_v2_verification_sessions__session_ref__notices_acknowledgments_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_ref: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PrivV2AckIn"];
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
    choose_route_v2_verification_sessions__session_ref__route_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_ref: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SessionRouteIn"];
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
    subject_status_v2_verification_sessions__session_ref__subject_status_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_ref: string;
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
