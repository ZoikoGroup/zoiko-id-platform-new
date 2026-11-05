// GENERATED from contracts/openapi/public/federation.json by packages/api-client/openapi/codegen.mjs. Do not edit.
export interface paths {
    "/federation/callback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Callback */
        get: operations["callback_federation_callback_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/federation/connections/{connection_id}/link": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Link
         * @description §9.1: first federation of an existing account needs explicit, authenticated linking.
         */
        post: operations["link_federation_connections__connection_id__link_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/federation/connections/{connection_id}/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Login
         * @description `reauth=true` makes the person authenticate again at the provider (OIDC `max_age=0`, SAML `ForceAuthn`) and refuses a cached answer.
         */
        get: operations["login_federation_connections__connection_id__login_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/federation/integrations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Integrations */
        get: operations["list_integrations_federation_integrations_get"];
        put?: never;
        /** Create Integration */
        post: operations["create_integration_federation_integrations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/federation/integrations/{connection_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Update Integration
         * @description Change what an integration is called and how it is used. A change to what the provider is trusted for (linking, auth
         *     methods, assurance levels, the SP entity id, encryption) also needs fresh strong authentication. No change, no new revision.
         */
        patch: operations["update_integration_federation_integrations__connection_id__patch"];
        trace?: never;
    };
    "/federation/integrations/{connection_id}:disable": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Disable */
        post: operations["disable_federation_integrations__connection_id__disable_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/federation/integrations/{connection_id}:enable": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Enable
         * @description QUARANTINED -> ACTIVE. Restores trust, so it is an R3 change with fresh strong authentication; an active provider is left as it is.
         */
        post: operations["enable_federation_integrations__connection_id__enable_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/federation/integrations/{connection_id}:idp-initiated": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Set Idp Initiated
         * @description Enabling IdP-initiated SAML is an R3 change that always needs a second administrator's
         *     approval bound to these exact targets; switching it off needs no approval.
         */
        post: operations["set_idp_initiated_federation_integrations__connection_id__idp_initiated_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/federation/integrations/{connection_id}:rotate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Rotate
         * @description New pinned trust material: the retired key/certificate stops verifying at once (AUTH-15).
         */
        post: operations["rotate_federation_integrations__connection_id__rotate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/federation/integrations:discover": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Discover Provider
         * @description Read a provider's published settings so a person types an address, not endpoints, keys and certificates. The
         *     result is only returned: it is pinned when the integration is created, exactly as if it had been typed in.
         */
        post: operations["discover_provider_federation_integrations_discover_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/federation/saml/acs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Saml Acs */
        post: operations["saml_acs_federation_saml_acs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/federation/saml/acs/{connection_ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Saml Acs Unsolicited
         * @description Governed IdP-initiated SSO: only for a connection an approved R3 change enabled.
         */
        post: operations["saml_acs_unsolicited_federation_saml_acs__connection_ref__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/federation/sign-in-options": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Sign In Options
         * @description The company sign-ins a workspace's sign-in page may offer: only the name people see and the connection to start, for connections that are ACTIVE. No endpoint, key, client id or
         *     secret leaves here; an unknown client lists nothing, the same as a workspace with none.
         */
        get: operations["sign_in_options_federation_sign_in_options_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/federation/trust-anchors": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Anchors */
        get: operations["list_anchors_federation_trust_anchors_get"];
        put?: never;
        /**
         * Create Anchor
         * @description Configured, pinned trust: a federation can only be joined through an anchor added here.
         */
        post: operations["create_anchor_federation_trust_anchors_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/federation/trust-anchors/{anchor_id}:disable": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Disable Anchor */
        post: operations["disable_anchor_federation_trust_anchors__anchor_id__disable_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/federation/trust-anchors/{anchor_id}:enable": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Enable Anchor
         * @description Restores trust in a quarantined anchor: an R3 change with fresh strong authentication.
         */
        post: operations["enable_anchor_federation_trust_anchors__anchor_id__enable_post"];
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
        /** AnchorIn */
        AnchorIn: {
            /** Allowed Domains */
            allowed_domains: string[];
            /** Approval Request Id */
            approval_request_id?: string | null;
            /** Entity Id */
            entity_id: string;
            /** Jwks */
            jwks: {
                [key: string]: unknown;
            };
        };
        /** ApprovalIn */
        ApprovalIn: {
            /** Approval Request Id */
            approval_request_id?: string | null;
        };
        /** DiscoverIn */
        DiscoverIn: {
            /**
             * Protocol
             * @enum {string}
             */
            protocol: "OIDC" | "SAML";
            /** Url */
            url: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** IdpInitiatedIn */
        IdpInitiatedIn: {
            /** Approval Request Id */
            approval_request_id?: string | null;
            /** Enabled */
            enabled: boolean;
            /** Targets */
            targets?: string[];
        };
        /** IntegrationIn */
        IntegrationIn: {
            /** Acr Levels */
            acr_levels?: {
                [key: string]: number;
            };
            /** Algorithms */
            algorithms?: ("ES256" | "Ed25519" | "RS256" | "PS256")[];
            /**
             * Allow Idp Initiated
             * @default false
             */
            allow_idp_initiated: boolean;
            /** Approval Request Id */
            approval_request_id?: string | null;
            /** Authorization Endpoint */
            authorization_endpoint?: string | null;
            /** Client Auth */
            client_auth?: ("private_key_jwt" | "client_secret_basic") | null;
            /** Client Id */
            client_id?: string | null;
            /** Client Secret Ref */
            client_secret_ref?: string | null;
            /** Display Name */
            display_name: string;
            /** Federation Entity Id */
            federation_entity_id?: string | null;
            /** Idp Certificates */
            idp_certificates?: string[];
            /** Issuer */
            issuer: string;
            /** Jwks */
            jwks?: {
                [key: string]: unknown;
            } | null;
            /**
             * Link Policy
             * @default EXISTING_ONLY
             * @enum {string}
             */
            link_policy: "EXISTING_ONLY" | "JIT";
            /**
             * Metadata Expires At
             * Format: date-time
             */
            metadata_expires_at: string;
            /**
             * Protocol
             * @enum {string}
             */
            protocol: "OIDC" | "SAML";
            /**
             * Require Encrypted Assertions
             * @default false
             */
            require_encrypted_assertions: boolean;
            /** Scopes */
            scopes?: string[];
            /** Sp Decryption Key Ref */
            sp_decryption_key_ref?: string | null;
            /** Sp Entity Id */
            sp_entity_id?: string | null;
            /** Sso Url */
            sso_url?: string | null;
            /** Token Endpoint */
            token_endpoint?: string | null;
            /**
             * Trust Amr
             * @default true
             */
            trust_amr: boolean;
            /** Trust Anchor Id */
            trust_anchor_id?: string | null;
        };
        /** LinkIn */
        LinkIn: {
            /** Client Id */
            client_id: string;
        };
        /** RotateIn */
        RotateIn: {
            /** Approval Request Id */
            approval_request_id?: string | null;
            /** Client Auth */
            client_auth?: ("private_key_jwt" | "client_secret_basic") | null;
            /** Client Secret Ref */
            client_secret_ref?: string | null;
            /** Idp Certificates */
            idp_certificates?: string[] | null;
            /** Jwks */
            jwks?: {
                [key: string]: unknown;
            } | null;
            /**
             * Metadata Expires At
             * Format: date-time
             */
            metadata_expires_at: string;
            /** Sp Decryption Key Ref */
            sp_decryption_key_ref?: string | null;
        };
        /**
         * UpdateIn
         * @description Settings that do not change who is trusted. Issuer, endpoints, keys, certificates and anchors are never here: rotate handles keys.
         */
        UpdateIn: {
            /** Acr Levels */
            acr_levels?: {
                [key: string]: number;
            } | null;
            /** Algorithms */
            algorithms?: ("ES256" | "Ed25519" | "RS256" | "PS256")[] | null;
            /** Approval Request Id */
            approval_request_id?: string | null;
            /** Display Name */
            display_name?: string | null;
            /** Link Policy */
            link_policy?: ("EXISTING_ONLY" | "JIT") | null;
            /** Require Encrypted Assertions */
            require_encrypted_assertions?: boolean | null;
            /** Scopes */
            scopes?: string[] | null;
            /** Sp Entity Id */
            sp_entity_id?: string | null;
            /** Trust Amr */
            trust_amr?: boolean | null;
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
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    callback_federation_callback_get: {
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
    link_federation_connections__connection_id__link_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                connection_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LinkIn"];
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
    login_federation_connections__connection_id__login_get: {
        parameters: {
            query: {
                client_id: string;
                return_to?: string | null;
                reauth?: boolean;
            };
            header?: never;
            path: {
                connection_id: string;
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
    list_integrations_federation_integrations_get: {
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
    create_integration_federation_integrations_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["IntegrationIn"];
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
    update_integration_federation_integrations__connection_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                connection_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UpdateIn"];
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
    disable_federation_integrations__connection_id__disable_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                connection_id: string;
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
    enable_federation_integrations__connection_id__enable_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                connection_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["ApprovalIn"] | null;
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
    set_idp_initiated_federation_integrations__connection_id__idp_initiated_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                connection_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["IdpInitiatedIn"];
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
    rotate_federation_integrations__connection_id__rotate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                connection_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RotateIn"];
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
    discover_provider_federation_integrations_discover_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DiscoverIn"];
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
    saml_acs_federation_saml_acs_post: {
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
    saml_acs_unsolicited_federation_saml_acs__connection_ref__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                connection_ref: string;
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
    sign_in_options_federation_sign_in_options_get: {
        parameters: {
            query: {
                client_id: string;
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
    list_anchors_federation_trust_anchors_get: {
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
    create_anchor_federation_trust_anchors_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AnchorIn"];
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
    disable_anchor_federation_trust_anchors__anchor_id__disable_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                anchor_id: string;
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
    enable_anchor_federation_trust_anchors__anchor_id__enable_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                anchor_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["ApprovalIn"] | null;
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
