package com.zoiko.id;

/** An RFC 9457 problem. {@code code} is the document's public code (for example {@code unsupported_market}, {@code idempotency_conflict}). */
public final class V2Error extends RuntimeException {
    private final String code;
    private final int status;
    private final String detail;
    private final String traceId;
    private final boolean retryable;

    public V2Error(String code, int status, String detail, String traceId, boolean retryable) {
        super(detail == null || detail.isEmpty() ? code : code + ": " + detail);
        this.code = code;
        this.status = status;
        this.detail = detail == null ? "" : detail;
        this.traceId = traceId;
        this.retryable = retryable;
    }

    public String code() { return code; }
    public int status() { return status; }
    public String detail() { return detail; }
    public String traceId() { return traceId; }
    public boolean retryable() { return retryable; }
}
