import com.fasterxml.jackson.databind.ObjectMapper;
import com.zoiko.id.V2Client;
import com.zoiko.id.V2Error;

import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** A test driver (ZID-INT-002; GID-063): runs the Java SDK against a real running platform and prints one JSON line. Not part of the package. */
public final class LiveCheck {
    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        ObjectMapper json = new ObjectMapper();
        Map<String, Object> body = json.readValue(args[2], Map.class);
        V2Client c = new V2Client(args[0], args[1]);
        Map<String, Object> out = new LinkedHashMap<>();

        out.put("capabilities", !c.capabilities().isEmpty());
        Map<String, Object> alt = c.captureAlternatives("US", null);
        out.put("alternatives", List.of(alt.get("counts_as_failed_attempt"), alt.get("rejection_possible")));

        Map<String, Object> first = verify(c, body, "java-live-0001");
        Map<String, Object> replay = verify(c, body, "java-live-0001");
        out.put("replaySame", first.get("credential_ref").equals(replay.get("credential_ref")));
        out.put("read", c.credential((String) first.get("credential_ref")).get("credential_ref").equals(first.get("credential_ref")));

        byte[] pdf = "%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n".getBytes(StandardCharsets.US_ASCII);
        Map<String, Object> stored = c.uploadEvidence(pdf, "application/pdf", "java-live-up-1", null);
        String ref = (String) stored.get("evidence_ref");
        out.put("upload", c.evidenceManifest(ref).get("evidence_ref").equals(ref));
        c.deleteEvidence(ref);

        try {
            new V2Client(args[0], "not-a-token").capabilities();
            out.put("bad", "no error");
        } catch (V2Error e) {
            out.put("bad", e.status());
        }
        System.out.println(json.writeValueAsString(out));
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> verify(V2Client c, Map<String, Object> body, String key) {
        Map<String, String> jur = new LinkedHashMap<>();
        ((Map<String, Object>) body.get("jurisdiction")).forEach((k, v) -> jur.put(k, (String) v));
        Map<String, Object> issuer = (Map<String, Object>) body.get("issuer_claim");
        Map<String, Object> claim = (Map<String, Object>) body.get("credential_claim");
        try {
            return c.verifyCredential((String) body.get("credential_type"), issuer, claim, jur, key, null);
        } catch (V2Error e) {
            System.err.println("verifyCredential refused: status=" + e.status() + " code=" + e.code() + " detail=" + e.detail());
            throw e;
        }
    }
}
