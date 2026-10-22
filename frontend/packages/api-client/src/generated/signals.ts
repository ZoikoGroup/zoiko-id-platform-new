// GENERATED from contracts/openapi/public/signals.json by packages/api-client/openapi/codegen.mjs. Do not edit.
export interface paths {
    "/security-state/{subject_type}/{subject_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read State */
        get: operations["read_state_security_state__subject_type___subject_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/security-state/{subject_type}/{subject_id}:reevaluate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reevaluate */
        post: operations["reevaluate_security_state__subject_type___subject_id__reevaluate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/signals": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Publish */
        post: operations["publish_signals_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ssf/events/push/{stream_ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Push */
        post: operations["push_ssf_events_push__stream_ref__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ssf/jwks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Set Keys */
        get: operations["set_keys_ssf_jwks_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ssf/poll/{stream_ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Poll */
        post: operations["poll_ssf_poll__stream_ref__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ssf/streams": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Streams
         * @description Pinned keys and the push credential's hash never leave the store (same view as one stream).
         */
        get: operations["list_streams_ssf_streams_get"];
        put?: never;
        /** Create Stream */
        post: operations["create_stream_ssf_streams_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ssf/streams/{stream_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Stream */
        get: operations["read_stream_ssf_streams__stream_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ssf/streams/{stream_id}/propagation": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Propagation
         * @description FR-SIG-003/004: commit -> receiver acknowledgement latency against the risk-class SLOs.
         */
        get: operations["propagation_ssf_streams__stream_id__propagation_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ssf/streams/{stream_id}/subjects": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Map Subject */
        post: operations["map_subject_ssf_streams__stream_id__subjects_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ssf/streams/{stream_id}:disable": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Disable */
        post: operations["disable_ssf_streams__stream_id__disable_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ssf/streams/{stream_id}:enable": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Enable */
        post: operations["enable_ssf_streams__stream_id__enable_post"];
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
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** OverrideIn */
        OverrideIn: {
            /** Approval Request Id */
            approval_request_id?: string | null;
            /** Reason */
            reason: string;
            /**
             * State
             * @enum {string}
             */
            state: "TRUSTED" | "ELEVATED_RISK" | "STEP_UP_REQUIRED" | "REAUTH_REQUIRED" | "RESTRICTED";
        };
        /** PollIn */
        PollIn: {
            /** Ack */
            ack?: string[];
            /**
             * Maxevents
             * @default 10
             */
            maxEvents: number;
            /**
             * Returnimmediately
             * @default true
             */
            returnImmediately: boolean;
        };
        /** SignalIn */
        SignalIn: {
            /** Effective At */
            effective_at?: string | null;
            /** Event Type */
            event_type: string;
            /** Source Event Id */
            source_event_id: string;
            /**
             * Subject Id
             * Format: uuid
             */
            subject_id: string;
            /**
             * Subject Type
             * @enum {string}
             */
            subject_type: "PRINCIPAL" | "SESSION" | "CREDENTIAL";
        };
        /** StreamIn */
        StreamIn: {
            /** Algorithms */
            algorithms?: ("ES256" | "Ed25519" | "RS256" | "PS256")[];
            /** Audience */
            audience: string;
            /**
             * Direction
             * @enum {string}
             */
            direction: "RECEIVER" | "TRANSMITTER";
            /** Events */
            events: string[];
            /** Federation Issuer */
            federation_issuer?: string | null;
            /** Issuer */
            issuer?: string | null;
            /** Jwks */
            jwks?: {
                [key: string]: unknown;
            } | null;
            /** Receiver Client Id */
            receiver_client_id?: string | null;
            /**
             * Source Trust
             * @default TRUSTED
             * @enum {string}
             */
            source_trust: "INFORMATIONAL" | "TRUSTED" | "AUTHORITATIVE";
            /** Subject Formats */
            subject_formats?: ("iss_sub" | "opaque")[];
        };
        /** SubjectIn */
        SubjectIn: {
            /** Opaque Id */
            opaque_id: string;
            /**
             * Principal Id
             * Format: uuid
             */
            principal_id: string;
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
    read_state_security_state__subject_type___subject_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                subject_type: "PRINCIPAL" | "SESSION" | "CREDENTIAL";
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
    reevaluate_security_state__subject_type___subject_id__reevaluate_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                subject_type: "PRINCIPAL" | "SESSION" | "CREDENTIAL";
                subject_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OverrideIn"];
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
    publish_signals_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SignalIn"];
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
    push_ssf_events_push__stream_ref__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                stream_ref: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
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
    set_keys_ssf_jwks_get: {
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
    poll_ssf_poll__stream_ref__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                stream_ref: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PollIn"];
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
    list_streams_ssf_streams_get: {
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
    create_stream_ssf_streams_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["StreamIn"];
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
    read_stream_ssf_streams__stream_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                stream_id: string;
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
    propagation_ssf_streams__stream_id__propagation_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                stream_id: string;
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
    map_subject_ssf_streams__stream_id__subjects_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                stream_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SubjectIn"];
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
    disable_ssf_streams__stream_id__disable_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                stream_id: string;
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
    enable_ssf_streams__stream_id__enable_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                stream_id: string;
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
