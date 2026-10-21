/** Typed client for the Zoiko iD cell API.
 *
 * Request bodies are generated from contracts/openapi (`npm run codegen`); response models are in
 * ./models (the contracts publish no response schemas). In the browser, token-bearing calls go to
 * the app's BFF (`/api/bff`) and sign-in flows go same-origin through the passthrough; the browser
 * never holds an access token. */
export { createCellApi, createFlowApi, createOperatorApi, type CellApi, type FlowApi, type OperatorApi } from "./endpoints";
export type * from "./endpoints";
export type * from "./models";
export { ApiError, type FieldIssue } from "./runtime/errors";
export { createTransport, newCorrelationId, seg, type RequestOptions, type Transport, type TransportConfig } from "./runtime/transport";
