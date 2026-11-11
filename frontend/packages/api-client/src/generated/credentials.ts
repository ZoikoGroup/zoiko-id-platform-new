// GENERATED from contracts/openapi/public/credentials.json by packages/api-client/openapi/codegen.mjs. Do not edit.
export interface paths {
    "/credential-configurations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Configurations */
        get: operations["list_configurations_credential_configurations_get"];
        put?: never;
        /** Create Configuration */
        post: operations["create_configuration_credential_configurations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/credential-configurations/{configuration_id}:activate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Activate Configuration */
        post: operations["activate_configuration_credential_configurations__configuration_id__activate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/credential-issuer-trust": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Trust */
        get: operations["list_trust_credential_issuer_trust_get"];
        put?: never;
        /** Create Trust */
        post: operations["create_trust_credential_issuer_trust_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/credential-issuer-trust:rotate-keys": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Rotate Trust Keys
         * @description Issuer key rollover for a pinned issuer: the new key set replaces the old one. A key that
         *     carries `not_after` (epoch seconds) stays valid only until then (bounded overlap).
         */
        post: operations["rotate_trust_keys_credential_issuer_trust_rotate_keys_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/credential-issuer-trust:status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Trust Status */
        post: operations["trust_status_credential_issuer_trust_status_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/credentials": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Credentials */
        get: operations["list_credentials_credentials_get"];
        put?: never;
        /** Issue Credential */
        post: operations["issue_credential_credentials_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/credentials/nonce": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Nonce */
        post: operations["nonce_credentials_nonce_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/credentials/status-lists/{ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Status List */
        get: operations["status_list_credentials_status_lists__ref__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/credentials/{credential_id}:reinstate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reinstate */
        post: operations["reinstate_credentials__credential_id__reinstate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/credentials/{credential_id}:revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Revoke */
        post: operations["revoke_credentials__credential_id__revoke_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/credentials/{credential_id}:suspend": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Suspend */
        post: operations["suspend_credentials__credential_id__suspend_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/presentations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Presentation */
        post: operations["create_presentation_presentations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/presentations/response/{tenant_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Response */
        post: operations["response_presentations_response__tenant_id__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/presentations/{presentation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Presentation */
        get: operations["read_presentation_presentations__presentation_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/presentations/{ref}/request": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Request Object */
        get: operations["request_object_presentations__ref__request_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/relying-party-registrations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Registrations */
        get: operations["list_registrations_relying_party_registrations_get"];
        put?: never;
        /** Register Rp */
        post: operations["register_rp_relying_party_registrations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/consumers/zoikorum/decisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Zoikorum Decision
         * @description A machine-verifiable decision for Zoikorum only: signed, bound to its consumer, with no raw credential in it.
         */
        post: operations["zoikorum_decision_v1_consumers_zoikorum_decisions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credential-packs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Packs */
        get: operations["list_packs_v1_credential_packs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credential-packs/{country}/{profession}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Packs For
         * @description Which checks are available for one country and profession, and how far each can be relied on. Scope and state
         *     only; no legal reference, owner or source id.
         */
        get: operations["packs_for_v1_credential_packs__country___profession__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/academic/checks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Check Academic Award */
        post: operations["check_academic_award_v1_credentials_academic_checks_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/appeals/{appeal_id}:resolve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resolve Appeal */
        post: operations["resolve_appeal_v1_credentials_appeals__appeal_id__resolve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/assertions/{check_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Assertion */
        get: operations["read_assertion_v1_credentials_assertions__check_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/assertions/{ref}/verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Public Verify
         * @description No account: the capability is the grant. Only a PUBLIC_VERIFIER grant still in force reveals anything, and only the
         *     bounded view (what the claim is, the decision, its tier, when it was checked); never a name, a birth date or a document.
         */
        get: operations["public_verify_v1_credentials_assertions__ref__verify_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/cases/{check_id}/appeals": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Raise Appeal */
        post: operations["raise_appeal_v1_credentials_cases__check_id__appeals_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/certifications/checks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Check Industry Certificate */
        post: operations["check_industry_certificate_v1_credentials_certifications_checks_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/continuing-education/checks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Check Cpd Record */
        post: operations["check_cpd_record_v1_credentials_continuing_education_checks_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/disciplinary/checks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Check Disciplinary */
        post: operations["check_disciplinary_v1_credentials_disciplinary_checks_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/disclosures": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Grant */
        post: operations["create_grant_v1_credentials_disclosures_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/disclosures/{grant_id}:read": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Read Grant */
        post: operations["read_grant_v1_credentials_disclosures__grant_id__read_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/disclosures/{grant_id}:withdraw": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Withdraw Grant */
        post: operations["withdraw_grant_v1_credentials_disclosures__grant_id__withdraw_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/dossiers/{subject_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Dossier
         * @description WF-02: what a Zoikorum reviewer needs about one person's credential checks in one answer: the current checks (five separate
         *     facts each), the decisions made from them, and any appeals. It reads; it decides nothing.
         */
        get: operations["dossier_v1_credentials_dossiers__subject_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/experience/checks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Check Employment Assertion */
        post: operations["check_employment_assertion_v1_credentials_experience_checks_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/insurance/checks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Check Insurance Assertion */
        post: operations["check_insurance_assertion_v1_credentials_insurance_checks_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/monitoring": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Source Event */
        post: operations["source_event_v1_credentials_monitoring_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/monitoring/dead-letters": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Dead Letters */
        get: operations["list_dead_letters_v1_credentials_monitoring_dead_letters_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/monitoring/dead-letters/{letter_id}/replay": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Replay Dead Letter */
        post: operations["replay_dead_letter_v1_credentials_monitoring_dead_letters__letter_id__replay_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/professional-licenses/checks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Check Professional License */
        post: operations["check_professional_license_v1_credentials_professional_licenses_checks_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/registry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Registry View
         * @description Read-only view of what the platform trusts (WF-04): issuers, source contracts, eligibility policies, certifications and the
         *     pack version. It shows scope and state only: no outreach addresses, price-book references or certificate references.
         */
        get: operations["registry_view_v1_credentials_registry_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/source-outreach": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Open Outreach */
        post: operations["open_outreach_v1_credentials_source_outreach_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/source-outreach/{request_id}:conclude": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Conclude Outreach
         * @description Conclude a request that was never answered: after its deadline it becomes an UNVERIFIABLE check, never a finding.
         */
        post: operations["conclude_outreach_v1_credentials_source_outreach__request_id__conclude_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/source-outreach/{request_id}:response": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Record Response */
        post: operations["record_response_v1_credentials_source_outreach__request_id__response_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/source-outreach/{request_id}:review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Review Outreach */
        post: operations["review_outreach_v1_credentials_source_outreach__request_id__review_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/credentials/vc/verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Verify Signed Credential
         * @description Verify a signed credential as evidence. The answer states five separate facts and is never a primary-source
         *     confirmation or a license; an EU Digital Credential e-seal is refused because this path cannot validate it.
         */
        post: operations["verify_signed_credential_v1_credentials_vc_verify_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/eligibility/engagement/evaluate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Evaluate Engagement */
        post: operations["evaluate_engagement_v1_eligibility_engagement_evaluate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/eligibility/professional/evaluate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Evaluate Professional */
        post: operations["evaluate_professional_v1_eligibility_professional_evaluate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/issuers/resolution": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resolve Issuer */
        post: operations["resolve_issuer_v1_issuers_resolution_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/issuers/{issuer_id}/trust": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Issuer Trust */
        get: operations["issuer_trust_v1_issuers__issuer_id__trust_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/capabilities": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Capabilities
         * @description What can be checked, honestly: a capability with no live source says so (`live_coverage_countries` empty).
         */
        get: operations["capabilities_v2_capabilities_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/coverage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Coverage View
         * @description Packs and source coverage for one place. A place with no pack is reported UNAVAILABLE, never as covered.
         */
        get: operations["coverage_view_v2_coverage_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/credentials/verify": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Verify Credential */
        post: operations["verify_credential_v2_credentials_verify_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/credentials/{credential_ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Credential */
        get: operations["read_credential_v2_credentials__credential_ref__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/eligibility/evaluate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Evaluate */
        post: operations["evaluate_v2_eligibility_evaluate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/eligibility/{eligibility_ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Eligibility */
        get: operations["read_eligibility_v2_eligibility__eligibility_ref__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/issuer-checks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Open Issuer Check */
        post: operations["open_issuer_check_v2_issuer_checks_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/issuer-checks/{issuer_check_ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Issuer Check */
        get: operations["read_issuer_check_v2_issuer_checks__issuer_check_ref__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/presentations/requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Request Presentation */
        post: operations["request_presentation_v2_presentations_requests_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/presentations/{presentation_ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Presentation */
        get: operations["read_presentation_v2_presentations__presentation_ref__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/verifications": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Open Case */
        post: operations["open_case_v2_verifications_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/verifications/{case_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Case */
        get: operations["read_case_v2_verifications__case_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/verifications/{case_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel Case */
        post: operations["cancel_case_v2_verifications__case_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/verifications/{case_id}/tasks/{check}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Submit Task */
        post: operations["submit_task_v2_verifications__case_id__tasks__check__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v2/zoikorum/engagement-checks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Engagement Check
         * @description Eligibility for each requested service, separately. The overall answer is only as good as the worst service, and it is
         *     signed for Zoikorum alone with no raw credential in it. Zoikorum owns its marketplace rules; this answers only the evidence.
         */
        post: operations["engagement_check_v2_zoikorum_engagement_checks_post"];
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
        /** ConfigurationIn */
        ConfigurationIn: {
            /** Assurance Profile Id */
            assurance_profile_id?: string | null;
            /** Credential Type */
            credential_type: string;
            /**
             * Format
             * @enum {string}
             */
            format: "SD_JWT_VC" | "MDOC" | "VC_JWT";
            /**
             * Holder Binding Required
             * @default true
             */
            holder_binding_required: boolean;
            /**
             * Issuance Enabled
             * @default false
             */
            issuance_enabled: boolean;
            /** Optional Claims */
            optional_claims?: string[];
            /** Required Claims */
            required_claims: string[];
            /**
             * Risk Class
             * @default 2
             */
            risk_class: number;
            /**
             * Status Mandatory
             * @default true
             */
            status_mandatory: boolean;
            /**
             * Status Method
             * @enum {string}
             */
            status_method: "TOKEN_STATUS_LIST" | "BITSTRING_STATUS_LIST" | "NONE";
        };
        /** CredAppealIn */
        CredAppealIn: {
            /** Correction */
            correction?: {
                [key: string]: string;
            };
            /** Reason */
            reason: string;
        };
        /** CredCheckIn */
        CredCheckIn: {
            /** Birth Year */
            birth_year?: number | null;
            /** Claim */
            claim: {
                [key: string]: unknown;
            };
            /** Consent Ref */
            consent_ref?: string | null;
            /**
             * Consumer
             * @default direct
             */
            consumer: string;
            /** Former Names */
            former_names?: string[];
            /** Holder Name */
            holder_name?: string | null;
            jurisdiction: components["schemas"]["CredJurisdictionIn"];
            /**
             * Name Change Evidence
             * @default false
             */
            name_change_evidence: boolean;
            /** Profession Code */
            profession_code?: string | null;
            /**
             * Purpose
             * @default PROFESSIONAL_ENGAGEMENT
             */
            purpose: string;
            /** Subject Principal Id */
            subject_principal_id?: string | null;
        };
        /**
         * CredDisciplinaryCheckIn
         * @description A sensitive-category check: it always names its basis and a reviewer other than the caller.
         */
        CredDisciplinaryCheckIn: {
            /** Birth Year */
            birth_year?: number | null;
            /** Consent Ref */
            consent_ref?: string | null;
            /** Former Names */
            former_names?: string[];
            /** Holder Name */
            holder_name?: string | null;
            jurisdiction: components["schemas"]["CredJurisdictionIn"];
            /** Legal Basis Ref */
            legal_basis_ref?: string | null;
            /**
             * Name Change Evidence
             * @default false
             */
            name_change_evidence: boolean;
            /**
             * Purpose
             * @default PROFESSIONAL_ENGAGEMENT
             */
            purpose: string;
            /** Reference */
            reference?: string | null;
            /** Regulator */
            regulator: string;
            /**
             * Reviewer Principal Id
             * Format: uuid
             */
            reviewer_principal_id: string;
            /** Subject Principal Id */
            subject_principal_id?: string | null;
        };
        /** CredEligibilityIn */
        CredEligibilityIn: {
            /** Country */
            country: string;
            /** Profession Code */
            profession_code: string;
            /** Service Code */
            service_code: string;
            /** Subdivision */
            subdivision?: string | null;
            /** Subject Principal Id */
            subject_principal_id?: string | null;
        };
        /** CredEngagementIn */
        CredEngagementIn: {
            /** Country */
            country: string;
            /**
             * Engagement Days
             * @default 365
             */
            engagement_days: number;
            /**
             * Insurance Required
             * @default true
             */
            insurance_required: boolean;
            /** Profession Code */
            profession_code: string;
            /** Service Code */
            service_code: string;
            /** Subdivision */
            subdivision?: string | null;
            /** Subject Principal Id */
            subject_principal_id?: string | null;
        };
        /** CredGrantIn */
        CredGrantIn: {
            /** Consumer */
            consumer: string;
            /**
             * Days
             * @default 30
             */
            days: number;
            /** Fields */
            fields: string[];
            /** Parent Grant Id */
            parent_grant_id?: string | null;
            /** Purpose */
            purpose: string;
            /** Recipient */
            recipient: string;
        };
        /** CredIssuerResolveIn */
        CredIssuerResolveIn: {
            /** Country */
            country?: string | null;
            /** Name */
            name: string;
            /** On */
            on?: string | null;
        };
        /** CredJurisdictionIn */
        CredJurisdictionIn: {
            /** Country */
            country: string;
            /** Subdivision */
            subdivision?: string | null;
        };
        /** CredMonitorIn */
        CredMonitorIn: {
            /**
             * Check Id
             * Format: uuid
             */
            check_id: string;
            /** Event Id */
            event_id: string;
            /**
             * Observed At
             * Format: date-time
             */
            observed_at: string;
            /** Sequence */
            sequence?: number | null;
            /** Status */
            status: string;
        };
        /** CredOutreachIn */
        CredOutreachIn: {
            /** Claim */
            claim?: {
                [key: string]: string;
            } | null;
            /** Claim Type */
            claim_type: string;
            /**
             * Days
             * @default 14
             */
            days: number;
            /** Issuer Id */
            issuer_id: string;
            /** Jurisdiction */
            jurisdiction?: {
                [key: string]: string;
            } | null;
            /** Profession Code */
            profession_code?: string | null;
            /** Subject Principal Id */
            subject_principal_id?: string | null;
        };
        /** CredOutreachReviewIn */
        CredOutreachReviewIn: {
            /** Approve */
            approve: boolean;
        };
        /** CredReadIn */
        CredReadIn: {
            /**
             * Check Id
             * Format: uuid
             */
            check_id: string;
            /** Consumer */
            consumer: string;
            /** Purpose */
            purpose: string;
            /** Recipient */
            recipient: string;
        };
        /** CredResolveAppealIn */
        CredResolveAppealIn: {
            /** Resolution */
            resolution: string;
            /** Upheld */
            upheld: boolean;
        };
        /** CredResponseIn */
        CredResponseIn: {
            /** Nonce */
            nonce: string;
            /** Outcome */
            outcome: string;
            /** Payload B64 */
            payload_b64: string;
            /** Sender */
            sender: string;
            /** Signature B64 */
            signature_b64: string;
        };
        /** CredVcVerifyIn */
        CredVcVerifyIn: {
            /** Credential */
            credential: string;
            /** Holder Proof */
            holder_proof?: string | null;
            /** Jurisdiction */
            jurisdiction?: string | null;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** PresentationIn */
        PresentationIn: {
            /** Claims */
            claims: string[];
            /**
             * Configuration Id
             * Format: uuid
             */
            configuration_id: string;
            /** Jurisdiction */
            jurisdiction: string;
            /** Purpose */
            purpose: string;
            /**
             * Response Mode
             * @default direct_post
             * @enum {string}
             */
            response_mode: "direct_post" | "direct_post.jwt";
            /** Subject Principal Id */
            subject_principal_id?: string | null;
        };
        /** RegistrationIn */
        RegistrationIn: {
            /** Allowed Attributes */
            allowed_attributes: string[];
            /** Client Id */
            client_id: string;
            /** Purpose */
            purpose: string;
            /**
             * Retention Days
             * @default 0
             */
            retention_days: number;
            /**
             * Valid To
             * Format: date-time
             */
            valid_to: string;
        };
        /** TrustIn */
        TrustIn: {
            /** Algorithms */
            algorithms?: ("ES256" | "Ed25519" | "RS256" | "PS256")[];
            /** Credential Types */
            credential_types: string[];
            /** Iaca Certificates */
            iaca_certificates?: string[] | null;
            /** Issuer Identifier */
            issuer_identifier: string;
            /** Jurisdictions */
            jurisdictions?: string[];
            /** Jwks */
            jwks?: {
                [key: string]: unknown;
            } | null;
            /** Trust Anchor Id */
            trust_anchor_id?: string | null;
            /**
             * Trust Source
             * @enum {string}
             */
            trust_source: "PINNED" | "FEDERATION";
            /**
             * Valid To
             * Format: date-time
             */
            valid_to: string;
        };
        /** TrustKeysIn */
        TrustKeysIn: {
            /** Issuer Identifier */
            issuer_identifier: string;
            /** Jwks */
            jwks: {
                [key: string]: unknown;
            };
        };
        /** TrustStatusIn */
        TrustStatusIn: {
            /** Issuer Identifier */
            issuer_identifier: string;
            /**
             * Status
             * @enum {string}
             */
            status: "SUSPENDED" | "REVOKED" | "QUARANTINED" | "ACTIVE";
        };
        /** V2CaseIn */
        V2CaseIn: {
            /** Callback Ref */
            callback_ref?: string | null;
            /** Client Ref */
            client_ref?: string | null;
            /** Consent Ref */
            consent_ref?: string | null;
            jurisdiction: components["schemas"]["V2Jurisdiction"];
            /** Purpose */
            purpose: string;
            /** Requested Checks */
            requested_checks: string[];
            /** Subject Ref */
            subject_ref?: string | null;
            /** Tenant Ref */
            tenant_ref?: string | null;
            workflow: components["schemas"]["V2Workflow"];
        };
        /** V2CredentialIn */
        V2CredentialIn: {
            /** Consent Ref */
            consent_ref?: string | null;
            /**
             * Consumer
             * @default direct
             */
            consumer: string;
            /** Credential Claim */
            credential_claim?: {
                [key: string]: unknown;
            };
            /** Credential Type */
            credential_type: string;
            /** Evidence Refs */
            evidence_refs?: string[];
            issuer_claim: components["schemas"]["V2IssuerClaim"];
            jurisdiction: components["schemas"]["V2Jurisdiction"];
            /**
             * Purpose
             * @default professional_onboarding
             */
            purpose: string;
            /**
             * Source Preference
             * @default authoritative_first
             */
            source_preference: string;
            /** Subject Ref */
            subject_ref?: string | null;
        };
        /** V2EligibilityIn */
        V2EligibilityIn: {
            jurisdiction: components["schemas"]["V2Jurisdiction"];
            /** Profession Code */
            profession_code: string;
            /** Service Code */
            service_code: string;
            /** Subject Ref */
            subject_ref?: string | null;
        };
        /** V2EngagementIn */
        V2EngagementIn: {
            /** Client Ref */
            client_ref?: string | null;
            /** Consent Ref */
            consent_ref?: string | null;
            /**
             * Decision Profile
             * @default zoikorum-v1
             */
            decision_profile: string;
            /** Engagement Ref */
            engagement_ref: string;
            jurisdiction: components["schemas"]["V2Jurisdiction"];
            /** Minimum Insurance Policy Ref */
            minimum_insurance_policy_ref?: string | null;
            /** Profession Code */
            profession_code: string;
            /** Professional Subject Ref */
            professional_subject_ref: string;
            /** Requested Services */
            requested_services: string[];
        };
        /** V2IssuerCheckIn */
        V2IssuerCheckIn: {
            /** Credential Type */
            credential_type: string;
            /**
             * Days
             * @default 14
             */
            days: number;
            /** Issuer Ref */
            issuer_ref: string;
        };
        /** V2IssuerClaim */
        V2IssuerClaim: {
            /** Country */
            country?: string | null;
            /** Name */
            name: string;
        };
        /** V2Jurisdiction */
        V2Jurisdiction: {
            /** Country */
            country: string;
            /** Subdivision */
            subdivision?: string | null;
        };
        /** V2PresentationIn */
        V2PresentationIn: {
            /**
             * Configuration Id
             * Format: uuid
             */
            configuration_id: string;
            /** Consent Ref */
            consent_ref?: string | null;
            /**
             * Expires In Seconds
             * @default 600
             */
            expires_in_seconds: number;
            /** Jurisdiction */
            jurisdiction: {
                [key: string]: string;
            };
            /** Purpose */
            purpose: string;
            /** Recipient Ref */
            recipient_ref: string;
            /** Requested Claims */
            requested_claims: string[];
            /**
             * Response Mode
             * @default direct_post
             */
            response_mode: string;
            /** Subject Ref */
            subject_ref?: string | null;
        };
        /** V2TaskIn */
        V2TaskIn: {
            /** Credential Claim */
            credential_claim?: {
                [key: string]: unknown;
            } | null;
            issuer_claim?: components["schemas"]["V2IssuerClaim"] | null;
            /** Verification Id */
            verification_id?: string | null;
        };
        /** V2Workflow */
        V2Workflow: {
            /** Profile Id */
            profile_id: string;
            /** Version */
            version: string;
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
        /** CredentialIn */
        zid_platform__modules__credential__api__credentials__CredentialIn: {
            /**
             * Credential Configuration Id
             * Format: uuid
             */
            credential_configuration_id: string;
            /** Proofs */
            proofs: {
                [key: string]: string[];
            };
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
    list_configurations_credential_configurations_get: {
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
    create_configuration_credential_configurations_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConfigurationIn"];
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
    activate_configuration_credential_configurations__configuration_id__activate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                configuration_id: string;
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
    list_trust_credential_issuer_trust_get: {
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
    create_trust_credential_issuer_trust_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TrustIn"];
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
    rotate_trust_keys_credential_issuer_trust_rotate_keys_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TrustKeysIn"];
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
    trust_status_credential_issuer_trust_status_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TrustStatusIn"];
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
    list_credentials_credentials_get: {
        parameters: {
            query?: {
                status?: ("VALID" | "SUSPENDED" | "REVOKED" | "EXPIRED" | "UNKNOWN") | null;
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
    issue_credential_credentials_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__credential__api__credentials__CredentialIn"];
            };
        };
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
    nonce_credentials_nonce_post: {
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
                    "application/json": unknown;
                };
            };
        };
    };
    status_list_credentials_status_lists__ref__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                ref: string;
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
    reinstate_credentials__credential_id__reinstate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                credential_id: string;
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
    revoke_credentials__credential_id__revoke_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                credential_id: string;
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
    suspend_credentials__credential_id__suspend_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                credential_id: string;
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
    create_presentation_presentations_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PresentationIn"];
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
    response_presentations_response__tenant_id__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
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
    read_presentation_presentations__presentation_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                presentation_id: string;
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
    request_object_presentations__ref__request_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                ref: string;
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
    list_registrations_relying_party_registrations_get: {
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
    register_rp_relying_party_registrations_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RegistrationIn"];
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
    zoikorum_decision_v1_consumers_zoikorum_decisions_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredEngagementIn"];
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
    list_packs_v1_credential_packs_get: {
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
    packs_for_v1_credential_packs__country___profession__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                country: string;
                profession: string;
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
    check_academic_award_v1_credentials_academic_checks_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredCheckIn"];
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
    resolve_appeal_v1_credentials_appeals__appeal_id__resolve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                appeal_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredResolveAppealIn"];
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
    read_assertion_v1_credentials_assertions__check_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                check_id: string;
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
    public_verify_v1_credentials_assertions__ref__verify_get: {
        parameters: {
            query: {
                grant: string;
            };
            header?: never;
            path: {
                ref: string;
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
    raise_appeal_v1_credentials_cases__check_id__appeals_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                check_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredAppealIn"];
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
    check_industry_certificate_v1_credentials_certifications_checks_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredCheckIn"];
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
    check_cpd_record_v1_credentials_continuing_education_checks_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredCheckIn"];
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
    check_disciplinary_v1_credentials_disciplinary_checks_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredDisciplinaryCheckIn"];
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
    create_grant_v1_credentials_disclosures_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredGrantIn"];
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
    read_grant_v1_credentials_disclosures__grant_id__read_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                grant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredReadIn"];
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
    withdraw_grant_v1_credentials_disclosures__grant_id__withdraw_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                grant_id: string;
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
    dossier_v1_credentials_dossiers__subject_id__get: {
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
    check_employment_assertion_v1_credentials_experience_checks_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredCheckIn"];
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
    check_insurance_assertion_v1_credentials_insurance_checks_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredCheckIn"];
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
    source_event_v1_credentials_monitoring_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredMonitorIn"];
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
    list_dead_letters_v1_credentials_monitoring_dead_letters_get: {
        parameters: {
            query?: {
                state?: string;
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
    replay_dead_letter_v1_credentials_monitoring_dead_letters__letter_id__replay_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                letter_id: string;
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
    check_professional_license_v1_credentials_professional_licenses_checks_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredCheckIn"];
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
    registry_view_v1_credentials_registry_get: {
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
    open_outreach_v1_credentials_source_outreach_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredOutreachIn"];
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
    conclude_outreach_v1_credentials_source_outreach__request_id__conclude_post: {
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
    record_response_v1_credentials_source_outreach__request_id__response_post: {
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
                "application/json": components["schemas"]["CredResponseIn"];
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
    review_outreach_v1_credentials_source_outreach__request_id__review_post: {
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
                "application/json": components["schemas"]["CredOutreachReviewIn"];
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
    verify_signed_credential_v1_credentials_vc_verify_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredVcVerifyIn"];
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
    evaluate_engagement_v1_eligibility_engagement_evaluate_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredEngagementIn"];
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
    evaluate_professional_v1_eligibility_professional_evaluate_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredEligibilityIn"];
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
    resolve_issuer_v1_issuers_resolution_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredIssuerResolveIn"];
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
    issuer_trust_v1_issuers__issuer_id__trust_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                issuer_id: string;
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
    capabilities_v2_capabilities_get: {
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
    coverage_view_v2_coverage_get: {
        parameters: {
            query: {
                country: string;
                profession?: string | null;
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
    verify_credential_v2_credentials_verify_post: {
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
                "application/json": components["schemas"]["V2CredentialIn"];
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
    read_credential_v2_credentials__credential_ref__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                credential_ref: string;
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
    evaluate_v2_eligibility_evaluate_post: {
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
                "application/json": components["schemas"]["V2EligibilityIn"];
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
    read_eligibility_v2_eligibility__eligibility_ref__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                eligibility_ref: string;
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
    open_issuer_check_v2_issuer_checks_post: {
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
                "application/json": components["schemas"]["V2IssuerCheckIn"];
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
    read_issuer_check_v2_issuer_checks__issuer_check_ref__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                issuer_check_ref: string;
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
    request_presentation_v2_presentations_requests_post: {
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
                "application/json": components["schemas"]["V2PresentationIn"];
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
    read_presentation_v2_presentations__presentation_ref__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                presentation_ref: string;
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
    open_case_v2_verifications_post: {
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
                "application/json": components["schemas"]["V2CaseIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
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
    read_case_v2_verifications__case_id__get: {
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
    cancel_case_v2_verifications__case_id__cancel_post: {
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
    submit_task_v2_verifications__case_id__tasks__check__post: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": string;
            };
            path: {
                case_id: string;
                check: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["V2TaskIn"];
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
    engagement_check_v2_zoikorum_engagement_checks_post: {
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
                "application/json": components["schemas"]["V2EngagementIn"];
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
}
