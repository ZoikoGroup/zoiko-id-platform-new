package com.zoiko.id;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/** The documented class of every Verify API error code and whether a retry can help. The table is the same, byte for byte, in every Zoiko SDK. */
public final class ErrorClasses {
    /** The class of one error code. */
    public record Entry(String errorClass, boolean retryable) {}

    private static final JsonNode TABLE = load();

    private ErrorClasses() {}

    private static JsonNode load() {
        try (InputStream in = ErrorClasses.class.getResourceAsStream("/zoiko/error_classes.json")) {
            if (in == null) throw new IllegalStateException("zoiko/error_classes.json is missing from the SDK");
            return new ObjectMapper().readTree(in);
        } catch (IOException e) {
            throw new IllegalStateException("zoiko/error_classes.json is not valid", e);
        }
    }

    /** The class for a code, or empty for a code the table does not know. */
    public static Optional<Entry> of(String code) {
        JsonNode n = TABLE.path("codes").path(code);
        if (n.isMissingNode()) return Optional.empty();
        return Optional.of(new Entry(n.path("class").asText(), n.path("retryable").asBoolean()));
    }

    /** Every class a code can belong to. */
    public static List<String> classes() {
        List<String> out = new ArrayList<>();
        TABLE.path("classes").forEach(c -> out.add(c.asText()));
        return out;
    }

    /** Every code in the table. */
    public static List<String> codes() {
        List<String> out = new ArrayList<>();
        TABLE.path("codes").fieldNames().forEachRemaining(out::add);
        return out;
    }
}
