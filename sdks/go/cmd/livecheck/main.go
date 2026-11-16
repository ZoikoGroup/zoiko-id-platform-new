// Command livecheck runs the Go SDK against a REAL running platform and prints one JSON line (ZID-INT-002; GID-063).
// It is a test driver, not part of the SDK API: `go run ./cmd/livecheck <base-url> <access-token> <credential-check-body-json>`.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"

	zoiko "github.com/zoiko/zoiko-id-go"
)

func must[T any](v T, err error) T {
	if err != nil {
		fmt.Fprintln(os.Stderr, "livecheck failed:", err)
		os.Exit(1)
	}
	return v
}

func asMap(v any) map[string]any { m, _ := v.(map[string]any); return m }

func main() {
	base, token := os.Args[1], os.Args[2]
	var body map[string]any
	must(0, json.Unmarshal([]byte(os.Args[3]), &body))
	ctx := context.Background()
	c := zoiko.NewV2Client(base, token, nil)
	out := map[string]any{}

	out["capabilities"] = len(must(c.Capabilities(ctx))) > 0
	alt := must(c.CaptureAlternatives(ctx, "US", ""))
	out["alternatives"] = []any{alt["counts_as_failed_attempt"], alt["rejection_possible"]}

	jur := map[string]string{}
	for k, v := range asMap(body["jurisdiction"]) {
		jur[k], _ = v.(string)
	}
	verify := func() zoiko.Doc {
		return must(c.VerifyCredential(ctx, body["credential_type"].(string), asMap(body["issuer_claim"]), asMap(body["credential_claim"]), jur, "go-live-0001", nil))
	}
	first, replay := verify(), verify()
	out["replaySame"] = first["credential_ref"] == replay["credential_ref"]
	out["read"] = must(c.Credential(ctx, first["credential_ref"].(string)))["credential_ref"] == first["credential_ref"]

	pdf := []byte("%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n")
	stored := must(c.UploadEvidence(ctx, pdf, "application/pdf", "go-live-up-1", ""))
	ref := stored["evidence_ref"].(string)
	out["upload"] = must(c.EvidenceManifest(ctx, ref))["evidence_ref"] == ref
	must(c.DeleteEvidence(ctx, ref))

	_, err := zoiko.NewV2Client(base, "not-a-token", nil).Capabilities(ctx)
	if e, ok := zoiko.AsV2Error(err); ok {
		out["bad"] = e.Status
	} else {
		out["bad"] = "no error"
	}
	line, _ := json.Marshal(out)
	fmt.Println(string(line))
}
