package zoiko

import (
	_ "embed"
	"encoding/json"
)

//go:embed error_classes.json
var errorClassesJSON []byte

// ErrorClass is the documented class of an error code and whether a retry can help.
type ErrorClass struct {
	Class     string `json:"class"`
	Retryable bool   `json:"retryable"`
}

type errorTable struct {
	Classes []string              `json:"classes"`
	Codes   map[string]ErrorClass `json:"codes"`
}

var table = func() errorTable {
	var t errorTable
	if err := json.Unmarshal(errorClassesJSON, &t); err != nil {
		panic("zoiko: error_classes.json is not valid: " + err.Error())
	}
	return t
}()

// ErrorClassOf returns the documented class for a Verify API error code. The table is the same, byte for byte, in every Zoiko SDK.
func ErrorClassOf(code string) (ErrorClass, bool) {
	c, ok := table.Codes[code]
	return c, ok
}

// ErrorClasses lists every class a code can belong to.
func ErrorClasses() []string { return append([]string(nil), table.Classes...) }
