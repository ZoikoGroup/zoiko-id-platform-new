// GENERATED from contracts/openapi/public/integration.json by packages/api-client/openapi/codegen.mjs. Do not edit.
export interface paths {
    "/v1/integration/applications": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Applications */
        get: operations["list_applications_v1_integration_applications_get"];
        put?: never;
        /** Register Application */
        post: operations["register_application_v1_integration_applications_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/integration/applications/{application_id}:transition": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Transition Application */
        post: operations["transition_application_v1_integration_applications__application_id__transition_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/integration/connectors": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Connectors */
        get: operations["list_connectors_v1_integration_connectors_get"];
        put?: never;
        /** Register Connector */
        post: operations["register_connector_v1_integration_connectors_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/integration/connectors/{connector_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Connector */
        get: operations["read_connector_v1_integration_connectors__connector_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/integration/connectors/{connector_id}/credential": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Put Credential */
        put: operations["put_credential_v1_integration_connectors__connector_id__credential_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/integration/connectors/{connector_id}/mapping-releases": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Releases */
        get: operations["list_releases_v1_integration_connectors__connector_id__mapping_releases_get"];
        put?: never;
        /** Create Release */
        post: operations["create_release_v1_integration_connectors__connector_id__mapping_releases_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/integration/connectors/{connector_id}:quarantine": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Quarantine */
        post: operations["quarantine_v1_integration_connectors__connector_id__quarantine_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/integration/connectors/{connector_id}:release": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Release */
        post: operations["release_v1_integration_connectors__connector_id__release_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/integration/mapping-releases/{release_id}:activate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Activate
         * @description Activation and rollback alike: an approval bound to this exact release content.
         */
        post: operations["activate_v1_integration_mapping_releases__release_id__activate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/integration/mapping-releases/{release_id}:simulate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Simulate */
        post: operations["simulate_v1_integration_mapping_releases__release_id__simulate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/integration/oauth-clients": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Oauth Clients */
        get: operations["list_oauth_clients_v1_integration_oauth_clients_get"];
        put?: never;
        /** Register Oauth Client */
        post: operations["register_oauth_client_v1_integration_oauth_clients_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/integration/oauth-clients/{client_id}:retire": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retire Oauth Client */
        post: operations["retire_oauth_client_v1_integration_oauth_clients__client_id__retire_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/integration/routes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Routes */
        get: operations["list_routes_v1_integration_routes_get"];
        put?: never;
        /** Add Route */
        post: operations["add_route_v1_integration_routes_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/integration/routes:resolve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resolve Route */
        post: operations["resolve_route_v1_integration_routes_resolve_post"];
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
        /** AppBody */
        AppBody: {
            /** Credential Justification */
            credential_justification?: string | null;
            /** @default private_key_jwt */
            credential_profile: components["schemas"]["CredentialProfile"];
            environment: components["schemas"]["Environment"];
            /**
             * Evidence Floor
             * @default 1
             */
            evidence_floor: number;
            /** Name */
            name: string;
            /** Owner Principal Id */
            owner_principal_id?: string | null;
            /** Pep Client Id */
            pep_client_id?: string | null;
            /** Pep Context Fields */
            pep_context_fields?: string[];
            /** Pep Obligations */
            pep_obligations?: string[];
            /** Protocols */
            protocols: components["schemas"]["Protocol"][];
            /** Redirect Uris */
            redirect_uris?: string[];
            /** Resource Ids */
            resource_ids?: string[];
            /**
             * Risk Floor
             * @default 1
             */
            risk_floor: number;
        };
        /**
         * AppStatus
         * @enum {string}
         */
        AppStatus: "DRAFT" | "TESTING" | "ACTIVE" | "SUSPENDED" | "RETIRED";
        /**
         * AuthProfile
         * @enum {string}
         */
        AuthProfile: "OAUTH_CLIENT" | "MTLS" | "WORKLOAD_IDENTITY" | "SECRET";
        /**
         * Capability
         * @enum {string}
         */
        Capability: "AUTHN" | "PROVISIONING" | "GROUPS" | "HR_LIFECYCLE" | "SIGNALS" | "WEBHOOK" | "DIRECTORY_LOOKUP";
        /** ConnectorBody */
        ConnectorBody: {
            /** @default OAUTH_CLIENT */
            auth_profile: components["schemas"]["AuthProfile"];
            /** Capabilities */
            capabilities: components["schemas"]["Capability"][];
            connector_type: components["schemas"]["ConnectorType"];
            /** @default FAIL_CLOSED_HIGH_RISK */
            disable_behavior: components["schemas"]["DisableBehavior"];
            /** Display Name */
            display_name: string;
            /** Freshness Minutes */
            freshness_minutes?: number | null;
            /** Owner Principal Id */
            owner_principal_id?: string | null;
            /** Source Of Truth Scope */
            source_of_truth_scope?: string[];
            /** Source Ref */
            source_ref?: string | null;
        };
        /**
         * ConnectorType
         * @enum {string}
         */
        ConnectorType: "OIDC_IDP" | "SAML_IDP" | "SCIM" | "HRIS" | "SIGNALS" | "WEBHOOK" | "DIRECTORY";
        /** CredentialBody */
        CredentialBody: {
            /** Not After */
            not_after?: string | null;
            /**
             * Overlap Hours
             * @default 24
             */
            overlap_hours: number;
            /** Secret */
            secret: string;
        };
        /**
         * CredentialProfile
         * @enum {string}
         */
        CredentialProfile: "private_key_jwt" | "mtls" | "workload_identity" | "secret_exception";
        /**
         * DisableBehavior
         * @enum {string}
         */
        DisableBehavior: "FAIL_CLOSED_HIGH_RISK" | "SUSPEND_DEPENDENTS";
        /**
         * Environment
         * @enum {string}
         */
        Environment: "DEV" | "TEST" | "STAGE" | "PROD";
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** MappingBody */
        MappingBody: {
            /** Rules */
            rules: {
                [key: string]: unknown;
            }[];
        };
        /** OAuthClientBody */
        OAuthClientBody: {
            /**
             * Client Type
             * @enum {string}
             */
            client_type: "PUBLIC" | "CONFIDENTIAL";
            /** Jwks */
            jwks?: {
                [key: string]: unknown;
            } | null;
            /** Name */
            name: string;
            /** Redirect Uris */
            redirect_uris: string[];
            /** Scopes */
            scopes?: string[];
        };
        /**
         * Protocol
         * @enum {string}
         */
        Protocol: "OIDC" | "SAML" | "SCIM" | "AUTHZEN" | "WEBHOOK" | "API" | "MCP" | "OTHER";
        /**
         * RouteKind
         * @enum {string}
         */
        RouteKind: "APPLICATION" | "DOMAIN";
        /** SimulateBody */
        SimulateBody: {
            /** Records */
            records: {
                [key: string]: unknown;
            }[];
        };
        /** Transition */
        Transition: {
            status: components["schemas"]["AppStatus"];
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
        /** QuarantineBody */
        zid_platform__modules__integration__api__integration__QuarantineBody: {
            /** Reason Code */
            reason_code: string;
        };
        /** ReleaseBody */
        zid_platform__modules__integration__api__integration__ReleaseBody: {
            /** Approval Request Id */
            approval_request_id: string;
        };
        /** ResolveBody */
        zid_platform__modules__integration__api__integration__ResolveBody: {
            /** Application Id */
            application_id?: string | null;
            /** Login Domain */
            login_domain?: string | null;
        };
        /** RouteBody */
        zid_platform__modules__integration__api__integration__RouteBody: {
            /** Connection Ref */
            connection_ref: string;
            kind: components["schemas"]["RouteKind"];
            /** Match */
            match: string;
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
    list_applications_v1_integration_applications_get: {
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
    register_application_v1_integration_applications_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AppBody"];
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
    transition_application_v1_integration_applications__application_id__transition_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                application_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Transition"];
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
    list_connectors_v1_integration_connectors_get: {
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
    register_connector_v1_integration_connectors_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConnectorBody"];
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
    read_connector_v1_integration_connectors__connector_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                connector_id: string;
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
    put_credential_v1_integration_connectors__connector_id__credential_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                connector_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CredentialBody"];
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
    list_releases_v1_integration_connectors__connector_id__mapping_releases_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                connector_id: string;
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
    create_release_v1_integration_connectors__connector_id__mapping_releases_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                connector_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MappingBody"];
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
    quarantine_v1_integration_connectors__connector_id__quarantine_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                connector_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__integration__api__integration__QuarantineBody"];
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
    release_v1_integration_connectors__connector_id__release_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                connector_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__integration__api__integration__ReleaseBody"];
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
    activate_v1_integration_mapping_releases__release_id__activate_post: {
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
                "application/json": components["schemas"]["zid_platform__modules__integration__api__integration__ReleaseBody"];
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
    simulate_v1_integration_mapping_releases__release_id__simulate_post: {
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
                "application/json": components["schemas"]["SimulateBody"];
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
    list_oauth_clients_v1_integration_oauth_clients_get: {
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
    register_oauth_client_v1_integration_oauth_clients_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OAuthClientBody"];
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
    retire_oauth_client_v1_integration_oauth_clients__client_id__retire_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                client_id: string;
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
    list_routes_v1_integration_routes_get: {
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
    add_route_v1_integration_routes_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__integration__api__integration__RouteBody"];
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
    resolve_route_v1_integration_routes_resolve_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["zid_platform__modules__integration__api__integration__ResolveBody"];
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
}
