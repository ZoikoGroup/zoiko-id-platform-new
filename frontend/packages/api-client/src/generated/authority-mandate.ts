// GENERATED from contracts/openapi/public/authority-mandate.json by packages/api-client/openapi/codegen.mjs. Do not edit.
export interface paths {
    "/v1/approval-requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List All */
        get: operations["list_all_v1_approval_requests_get"];
        put?: never;
        /** Create */
        post: operations["create_v1_approval_requests_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/approval-requests/awaiting-me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Awaiting Me
         * @description Pending requests the caller did not open, has not decided, and holds authority to decide.
         */
        get: operations["awaiting_me_v1_approval_requests_awaiting_me_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/approval-requests/for-change": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * For Change
         * @description The caller's own most recent unexpired request for exactly this change, or null.
         */
        get: operations["for_change_v1_approval_requests_for_change_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/approval-requests/{request_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read */
        get: operations["read_v1_approval_requests__request_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/approval-requests/{request_id}/decisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Decide */
        post: operations["decide_v1_approval_requests__request_id__decisions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/authority-grants": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List All
         * @description Newest first. `next_cursor` (when present) continues the list after the last row of this page.
         */
        get: operations["list_all_v1_authority_grants_get"];
        put?: never;
        /** Create Grant */
        post: operations["create_grant_v1_authority_grants_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/authority-grants/{grant_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Grant */
        get: operations["read_grant_v1_authority_grants__grant_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/authority-grants/{grant_id}/chain": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Chain */
        get: operations["read_chain_v1_authority_grants__grant_id__chain_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/authority-grants/{grant_id}:revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Revoke */
        post: operations["revoke_v1_authority_grants__grant_id__revoke_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/mandate-reservations/{reservation_id}:commit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Commit */
        post: operations["commit_v1_mandate_reservations__reservation_id__commit_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/mandate-reservations/{reservation_id}:release": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Release */
        post: operations["release_v1_mandate_reservations__reservation_id__release_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/mandates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List All */
        get: operations["list_all_v1_mandates_get"];
        put?: never;
        /** Create */
        post: operations["create_v1_mandates_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/mandates/{mandate_id}/balance": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Balance */
        get: operations["balance_v1_mandates__mandate_id__balance_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/mandates/{mandate_id}/reservations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reserve */
        post: operations["reserve_v1_mandates__mandate_id__reservations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/mandates/{mandate_id}:activate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Activate */
        post: operations["activate_v1_mandates__mandate_id__activate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/mandates/{mandate_id}:pause": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Pause */
        post: operations["pause_v1_mandates__mandate_id__pause_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/mandates/{mandate_id}:revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Revoke */
        post: operations["revoke_v1_mandates__mandate_id__revoke_post"];
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
        /**
         * ApprovalState
         * @enum {string}
         */
        ApprovalState: "PENDING" | "APPROVED" | "DENIED" | "EXPIRED" | "STALE";
        /** CommitBody */
        CommitBody: {
            /** Execution Id */
            execution_id?: string | null;
            /** Recheck Passed */
            recheck_passed: boolean;
        };
        /**
         * CompositeMode
         * @enum {string}
         */
        CompositeMode: "SINGLE" | "ALL_OF" | "K_OF_N" | "ROLE_PAIR" | "EXTERNAL_CONFIRMATION";
        /**
         * ContinuityMode
         * @description DEC-C2 / CR-001: one enum used everywhere.
         * @enum {string}
         */
        ContinuityMode: "PERSON_BOUND" | "ROLE_CONTINUOUS" | "ORGANIZATION_CONTINUOUS" | "CONTRACT_BOUND";
        /**
         * DecisionCreate
         * @description `authority_grant_id` and `transaction_context` may be left out for a request that records its action,
         *     resource and change: the grant is then the caller's own qualifying one and the context is rebuilt from the
         *     request, so approving is one click on a readable summary.
         */
        DecisionCreate: {
            /** Authority Grant Id */
            authority_grant_id?: string | null;
            /**
             * Risk Class
             * @default 3
             */
            risk_class: number;
            /** Transaction Context */
            transaction_context?: string | null;
            verdict: components["schemas"]["Verdict"];
        };
        /** Delegation */
        Delegation: {
            /**
             * Allowed
             * @default false
             */
            allowed: boolean;
            /**
             * Max Depth
             * @default 0
             */
            max_depth: number;
        };
        /** GrantCreate */
        GrantCreate: {
            /** Actions */
            actions: string[];
            /** Authority Root Assertion Id */
            authority_root_assertion_id?: string | null;
            /** @default PERSON_BOUND */
            continuity_mode: components["schemas"]["ContinuityMode"];
            /** Count Limit */
            count_limit?: number | null;
            /** Data Categories */
            data_categories?: string[] | null;
            /** Data Limit Bytes */
            data_limit_bytes?: number | null;
            delegation?: components["schemas"]["Delegation"];
            /** @default ROLE_ISSUED */
            grant_type: components["schemas"]["GrantType"];
            /** Grantee Principal Id */
            grantee_principal_id: string;
            /** Jurisdictions */
            jurisdictions?: string[] | null;
            /**
             * Min Assurance
             * @default 0
             */
            min_assurance: number;
            /** Parent Grant Id */
            parent_grant_id?: string | null;
            /** Purposes */
            purposes?: string[] | null;
            /** Represented Principal Id */
            represented_principal_id: string;
            /** Resources */
            resources: string[];
            /**
             * Risk Class
             * @default 3
             */
            risk_class: number;
            /** Tools */
            tools?: string[] | null;
            /**
             * Valid From
             * Format: date-time
             */
            valid_from: string;
            /**
             * Valid To
             * Format: date-time
             */
            valid_to: string;
            value_limit?: components["schemas"]["Money"] | null;
        };
        /**
         * GrantStatus
         * @enum {string}
         */
        GrantStatus: "DRAFT" | "PENDING_APPROVAL" | "ACTIVE" | "REVALIDATION_PENDING" | "SUSPENDED" | "REVOKED" | "EXPIRED" | "SUPERSEDED";
        /**
         * GrantType
         * @enum {string}
         */
        GrantType: "PERSONAL" | "ROLE_ISSUED" | "ORGANIZATION_DIRECT" | "SERVICE_DELEGATION" | "AGENT_DELEGATION" | "EXTERNAL_DELEGATION";
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** MandateCreate */
        MandateCreate: {
            /** Allowed Actions */
            allowed_actions: string[];
            /**
             * Authority Grant Id
             * Format: uuid
             */
            authority_grant_id: string;
            /** Count Limit */
            count_limit?: number | null;
            /** Data Limit Bytes */
            data_limit_bytes?: number | null;
            delegation?: components["schemas"]["Delegation"];
            /**
             * Executor Principal Id
             * Format: uuid
             */
            executor_principal_id: string;
            /** Jurisdictions */
            jurisdictions?: string[];
            per_transaction_value_limit?: components["schemas"]["ValueAmount"] | null;
            /**
             * Purpose Id
             * Format: uuid
             */
            purpose_id: string;
            /** Resource Scopes */
            resource_scopes: string[];
            /**
             * Risk Class
             * @default 3
             */
            risk_class: number;
            /** Tools */
            tools?: string[];
            /**
             * Valid From
             * Format: date-time
             */
            valid_from: string;
            /**
             * Valid To
             * Format: date-time
             */
            valid_to: string;
            value_limit?: components["schemas"]["ValueAmount"] | null;
        };
        /**
         * MandateStatus
         * @enum {string}
         */
        MandateStatus: "PENDING_APPROVAL" | "ACTIVE" | "PAUSED" | "EXHAUSTED" | "COMPLETED" | "EXPIRED" | "REVOKED";
        /** Money */
        Money: {
            /** Amount */
            amount: string;
            /** Currency */
            currency: string;
        };
        /**
         * RequestCreate
         * @description Either the exact `transaction_context`, or the `action`, `resource` and `change` it is made of (the app
         *     builds the context itself, so nobody pastes a string). `summary` says in words what the request is for.
         */
        RequestCreate: {
            /** Action */
            action?: string | null;
            /**
             * Change
             * @default
             */
            change: string;
            /**
             * K
             * @default 1
             */
            k: number;
            /** @default SINGLE */
            mode: components["schemas"]["CompositeMode"];
            /** Resource */
            resource?: string | null;
            /** Roles */
            roles: string[];
            /** Subject Ref */
            subject_ref: string;
            /** Summary */
            summary?: string | null;
            /** Transaction Context */
            transaction_context?: string | null;
            /**
             * Ttl Seconds
             * @default 3600
             */
            ttl_seconds: number;
        };
        /** ReserveBody */
        ReserveBody: {
            /** Action */
            action: string;
            /**
             * Count
             * @default 0
             */
            count: number;
            /**
             * Data Bytes
             * @default 0
             */
            data_bytes: number;
            /** Tool */
            tool?: string | null;
            /** Transaction Context */
            transaction_context: string;
            value?: components["schemas"]["ValueAmount"] | null;
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
        /** ValueAmount */
        ValueAmount: {
            /** Amount */
            amount: string;
            /** Currency */
            currency: string;
        };
        /**
         * Verdict
         * @enum {string}
         */
        Verdict: "APPROVE" | "DENY";
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    list_all_v1_approval_requests_get: {
        parameters: {
            query?: {
                state?: components["schemas"]["ApprovalState"] | null;
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
    create_v1_approval_requests_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RequestCreate"];
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
    awaiting_me_v1_approval_requests_awaiting_me_get: {
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
    for_change_v1_approval_requests_for_change_get: {
        parameters: {
            query: {
                action: string;
                resource: string;
                change?: string;
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
    read_v1_approval_requests__request_id__get: {
        parameters: {
            query: {
                transaction_context: string;
            };
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
    decide_v1_approval_requests__request_id__decisions_post: {
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
                "application/json": components["schemas"]["DecisionCreate"];
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
    list_all_v1_authority_grants_get: {
        parameters: {
            query?: {
                status?: components["schemas"]["GrantStatus"] | null;
                grantee_principal_id?: string | null;
                limit?: number;
                cursor?: string | null;
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
    create_grant_v1_authority_grants_post: {
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
                "application/json": components["schemas"]["GrantCreate"];
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
    read_grant_v1_authority_grants__grant_id__get: {
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
    read_chain_v1_authority_grants__grant_id__chain_get: {
        parameters: {
            query?: {
                risk_class?: number;
            };
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
    revoke_v1_authority_grants__grant_id__revoke_post: {
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
    commit_v1_mandate_reservations__reservation_id__commit_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                reservation_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CommitBody"];
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
    release_v1_mandate_reservations__reservation_id__release_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                reservation_id: string;
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
    list_all_v1_mandates_get: {
        parameters: {
            query?: {
                status?: components["schemas"]["MandateStatus"] | null;
                executor_principal_id?: string | null;
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
    create_v1_mandates_post: {
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
                "application/json": components["schemas"]["MandateCreate"];
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
    balance_v1_mandates__mandate_id__balance_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                mandate_id: string;
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
    reserve_v1_mandates__mandate_id__reservations_post: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": string;
            };
            path: {
                mandate_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReserveBody"];
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
    activate_v1_mandates__mandate_id__activate_post: {
        parameters: {
            query?: {
                risk_class?: number;
            };
            header?: never;
            path: {
                mandate_id: string;
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
    pause_v1_mandates__mandate_id__pause_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                mandate_id: string;
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
    revoke_v1_mandates__mandate_id__revoke_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                mandate_id: string;
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
