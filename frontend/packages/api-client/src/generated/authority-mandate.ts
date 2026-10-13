// GENERATED from contracts/openapi/public/authority-mandate.json by packages/api-client/openapi/codegen.mjs. Do not edit.
export interface paths {
    "/v1/approval-requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create */
        post: operations["create_v1_approval_requests_post"];
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
        get?: never;
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
        /** DecisionCreate */
        DecisionCreate: {
            /**
             * Authority Grant Id
             * Format: uuid
             */
            authority_grant_id: string;
            /**
             * Risk Class
             * @default 3
             */
            risk_class: number;
            /** Transaction Context */
            transaction_context: string;
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
         * GrantType
         * @enum {string}
         */
        GrantType: "PERSONAL" | "ROLE_ISSUED" | "ORGANIZATION_DIRECT" | "SERVICE_DELEGATION" | "AGENT_DELEGATION" | "EXTERNAL_DELEGATION";
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** Money */
        Money: {
            /** Amount */
            amount: string;
            /** Currency */
            currency: string;
        };
        /** RequestCreate */
        RequestCreate: {
            /**
             * K
             * @default 1
             */
            k: number;
            /** @default SINGLE */
            mode: components["schemas"]["CompositeMode"];
            /** Roles */
            roles: string[];
            /** Subject Ref */
            subject_ref: string;
            /** Transaction Context */
            transaction_context: string;
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
