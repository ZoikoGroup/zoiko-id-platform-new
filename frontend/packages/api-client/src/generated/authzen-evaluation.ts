// GENERATED from contracts/openapi/authzen/evaluation.json by packages/api-client/openapi/codegen.mjs. Do not edit.
export interface paths {
    "/access/v1/evaluation": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Evaluation */
        post: operations["evaluation_access_v1_evaluation_post"];
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
        /** Action */
        Action: {
            /** Name */
            name: string;
        };
        /** Entity */
        Entity: {
            /** Id */
            id: string;
            /** Properties */
            properties?: {
                [key: string]: unknown;
            };
            /** Type */
            type: string;
        };
        /**
         * EvaluationContext
         * @description Zoiko extensions: optional and namespaced by meaning; unknown keys are ignored (API §4).
         */
        EvaluationContext: {
            /** Mandate Id */
            mandate_id?: string | null;
            /** Purpose */
            purpose?: string | null;
            /** Represented Principal Id */
            represented_principal_id?: string | null;
            /** Risk Class */
            risk_class?: string | null;
            /** Tool Id */
            tool_id?: string | null;
            /** Transaction Context Id */
            transaction_context_id?: string | null;
            value?: components["schemas"]["Value"] | null;
        };
        /** EvaluationRequest */
        EvaluationRequest: {
            action: components["schemas"]["Action"];
            context?: components["schemas"]["EvaluationContext"];
            resource: components["schemas"]["Entity"];
            subject: components["schemas"]["Entity"];
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
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
        /** Value */
        Value: {
            /** Amount */
            amount: string;
            /** Currency */
            currency: string;
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
    evaluation_access_v1_evaluation_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EvaluationRequest"];
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
