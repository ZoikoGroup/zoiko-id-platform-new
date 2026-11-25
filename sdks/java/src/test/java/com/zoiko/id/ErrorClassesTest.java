package com.zoiko.id;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;

class ErrorClassesTest {
    @Test
    void theShippedTableIsTheSharedTableAndEveryCodeHasAKnownClass() throws Exception {
        Path shared = Path.of("..", "..", "contracts", "openapi", "partner", "error-classes.json");
        if (Files.exists(shared)) {  // absent in a standalone checkout of this SDK
            try (InputStream in = ErrorClasses.class.getResourceAsStream("/zoiko/error_classes.json")) {
                assertArrayEquals(Files.readAllBytes(shared), in.readAllBytes(), "the shipped table differs from the shared one");
            }
        }
        var known = ErrorClasses.classes();
        assertFalse(known.isEmpty());
        assertFalse(ErrorClasses.codes().isEmpty());
        for (String code : ErrorClasses.codes()) {
            assertTrue(known.contains(ErrorClasses.of(code).orElseThrow().errorClass()), code + " has a class that is not listed");
        }
        var limited = ErrorClasses.of("RATE_LIMITED").orElseThrow();
        assertEquals("RATE_LIMITED", limited.errorClass());
        assertTrue(limited.retryable());
        assertTrue(ErrorClasses.of("NO_SUCH_CODE").isEmpty());
    }
}
