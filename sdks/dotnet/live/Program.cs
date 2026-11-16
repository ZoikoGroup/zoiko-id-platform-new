using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Zoiko.Id;

var (baseUrl, token, bodyJson) = (args[0], args[1], args[2]);
var body = JsonNode.Parse(bodyJson)!.AsObject();
var c = new V2Client(baseUrl, token);
var jur = body["jurisdiction"]!.AsObject().ToDictionary(kv => kv.Key, kv => kv.Value!.GetValue<string>());
var output = new JsonObject();

output["capabilities"] = (await c.Capabilities()).Count > 0;
var alt = await c.CaptureAlternatives("US");
output["alternatives"] = new JsonArray(alt["counts_as_failed_attempt"]!.DeepClone(), alt["rejection_possible"]!.DeepClone());

async Task<JsonObject> Verify() => await c.VerifyCredential(
    body["credential_type"]!.GetValue<string>(), body["issuer_claim"]!.DeepClone(), body["credential_claim"]!.DeepClone(), jur, "dotnet-live-0001");
var first = await Verify();
var replay = await Verify();
output["replaySame"] = first["credential_ref"]!.GetValue<string>() == replay["credential_ref"]!.GetValue<string>();
output["read"] = (await c.Credential(first["credential_ref"]!.GetValue<string>()))["credential_ref"]!.GetValue<string>() == first["credential_ref"]!.GetValue<string>();

var pdf = Encoding.ASCII.GetBytes("%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n");
var stored = await c.UploadEvidence(pdf, "application/pdf", "dotnet-live-up-1");
var reference = stored["evidence_ref"]!.GetValue<string>();
output["upload"] = (await c.EvidenceManifest(reference))["evidence_ref"]!.GetValue<string>() == reference;
await c.DeleteEvidence(reference);

try { await new V2Client(baseUrl, "not-a-token").Capabilities(); output["bad"] = "no error"; }
catch (V2Error e) { output["bad"] = e.Status; }
Console.WriteLine(output.ToJsonString());
