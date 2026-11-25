package zoiko

import (
	"os"
	"testing"
)

func TestTheEmbeddedErrorTableIsTheSharedTableAndEveryCodeHasAKnownClass(t *testing.T) {
	shared, err := os.ReadFile("../../contracts/openapi/partner/error-classes.json")
	if err != nil {
		t.Skip("the shared table is not next to this SDK in a standalone checkout")
	}
	if string(shared) != string(errorClassesJSON) {
		t.Fatal("the embedded error table differs from contracts/openapi/partner/error-classes.json")
	}
	known := map[string]bool{}
	for _, c := range ErrorClasses() {
		known[c] = true
	}
	if len(table.Codes) == 0 || len(known) == 0 {
		t.Fatal("the table is empty")
	}
	for code, e := range table.Codes {
		if !known[e.Class] {
			t.Fatalf("%s has class %s which is not in the class list", code, e.Class)
		}
	}
	if c, ok := ErrorClassOf("RATE_LIMITED"); !ok || !c.Retryable || c.Class != "RATE_LIMITED" {
		t.Fatalf("RATE_LIMITED should be retryable: %+v", c)
	}
	if _, ok := ErrorClassOf("NO_SUCH_CODE"); ok {
		t.Fatal("an unknown code must have no class")
	}
}
