package main

import (
	"testing"
)

// ---------------------------------------------------------------------------
// tagsMatch
// ---------------------------------------------------------------------------

// tagsMatch checks if all required tags appear somewhere in the JSON tags string.

func TestTagsMatch_EmptyRecordTags(t *testing.T) {
	if tagsMatch("", []string{"go"}) {
		t.Fatal("empty record tags should not match any required tag")
	}
}

func TestTagsMatch_EmptyRequired(t *testing.T) {
	// With an empty required slice the loop body never runs, so the function
	// returns true (vacuous truth — all zero requirements are satisfied).
	if !tagsMatch(`["go","python"]`, []string{}) {
		t.Fatal("empty required slice should return true (vacuous truth)")
	}
}

func TestTagsMatch_SingleTagPresent(t *testing.T) {
	if !tagsMatch(`["go","python","rust"]`, []string{"go"}) {
		t.Fatal("expected 'go' to match in tags JSON")
	}
}

func TestTagsMatch_SingleTagAbsent(t *testing.T) {
	if tagsMatch(`["go","python"]`, []string{"rust"}) {
		t.Fatal("'rust' should not match in [go, python]")
	}
}

func TestTagsMatch_AllTagsPresent(t *testing.T) {
	if !tagsMatch(`["go","python","rust"]`, []string{"go", "rust"}) {
		t.Fatal("all required tags present but match returned false")
	}
}

func TestTagsMatch_OneTagMissing(t *testing.T) {
	if tagsMatch(`["go","python"]`, []string{"go", "rust"}) {
		t.Fatal("match should fail when one required tag is absent")
	}
}

func TestTagsMatch_NoSubstringMatch(t *testing.T) {
	// The implementation uses json.Unmarshal and exact set membership.
	// "go" does NOT match "golang" because they are different strings.
	if tagsMatch(`["golang"]`, []string{"go"}) {
		t.Fatal("'go' should not match 'golang' — implementation uses exact matching")
	}
}

func TestTagsMatch_ExactTagInJSONArray(t *testing.T) {
	// A typical PocketBase JSON serialisation.
	if !tagsMatch(`["science","technology","news"]`, []string{"technology"}) {
		t.Fatal("expected 'technology' to match")
	}
}

func TestTagsMatch_MultipleTagsAllPresent(t *testing.T) {
	json := `["alpha","beta","gamma","delta"]`
	if !tagsMatch(json, []string{"alpha", "delta"}) {
		t.Fatal("expected all required tags to match")
	}
}

func TestTagsMatch_MultipleTagsOneAbsent(t *testing.T) {
	json := `["alpha","beta","gamma"]`
	if tagsMatch(json, []string{"alpha", "omega"}) {
		t.Fatal("match should fail — 'omega' is absent")
	}
}

func TestTagsMatch_CaseSensitive(t *testing.T) {
	// The implementation is case-sensitive (plain string comparison).
	if tagsMatch(`["Go"]`, []string{"go"}) {
		t.Fatal("match should be case-sensitive: 'go' != 'Go'")
	}
}

func TestTagsMatch_TagInMiddleOfString(t *testing.T) {
	// "super" is not a JSON element — the array only contains "super-tag".
	// Exact set matching means "super" does NOT match "super-tag".
	if tagsMatch(`["super-tag"]`, []string{"super"}) {
		t.Fatal("'super' should not match 'super-tag' with exact matching")
	}
}

func TestTagsMatch_EmptyStringRequired(t *testing.T) {
	// An empty string as a required tag will only match if "" is literally in the JSON array.
	// ["anything"] does not contain ""; it contains "anything".
	if tagsMatch(`["anything"]`, []string{""}) {
		t.Fatal("empty string required tag should not match when '' is not in the tags array")
	}
}

func TestTagsMatch_EmptyStringInArray(t *testing.T) {
	// If "" IS in the array, an empty-string requirement should be satisfied.
	if !tagsMatch(`["","other"]`, []string{""}) {
		t.Fatal("empty string required tag should match when '' is in the tags array")
	}
}

// ---------------------------------------------------------------------------
// addDocumentRequest helpers (struct field access)
// ---------------------------------------------------------------------------

func TestAddDocumentRequest_Defaults(t *testing.T) {
	req := addDocumentRequest{}
	if req.Content != "" || req.Language != "" || req.Category != "" || len(req.Tags) != 0 {
		t.Fatal("zero-value addDocumentRequest should have empty fields")
	}
}

// ---------------------------------------------------------------------------
// searchRequest defaults
// ---------------------------------------------------------------------------

func TestSearchRequest_Defaults(t *testing.T) {
	req := searchRequest{}
	if req.Limit != 0 {
		t.Fatal("zero-value Limit should be 0")
	}
	if req.MaxDistance != 0 {
		t.Fatal("zero-value MaxDistance should be 0")
	}
}

// ---------------------------------------------------------------------------
// hybridSearchRequest
// ---------------------------------------------------------------------------

func TestHybridSearchRequest_Defaults(t *testing.T) {
	req := hybridSearchRequest{}
	if req.Weights.Vector != 0 || req.Weights.Text != 0 {
		t.Fatal("zero-value weights should be 0")
	}
}

// ---------------------------------------------------------------------------
// SearchFilters JSON tags
// ---------------------------------------------------------------------------

func TestSearchFilters_TagsField(t *testing.T) {
	f := SearchFilters{Tags: []string{"go", "test"}}
	if len(f.Tags) != 2 {
		t.Fatalf("want 2 tags, got %d", len(f.Tags))
	}
}

// ---------------------------------------------------------------------------
// batchInsertRequest
// ---------------------------------------------------------------------------

func TestBatchInsertRequest_MaxDocs(t *testing.T) {
	// Ensure the request type can hold 100 documents (the stated limit).
	req := batchInsertRequest{
		Documents: make([]addDocumentRequest, 100),
	}
	if len(req.Documents) != 100 {
		t.Fatalf("want 100 documents, got %d", len(req.Documents))
	}
}

// ---------------------------------------------------------------------------
// updateDocumentRequest
// ---------------------------------------------------------------------------

func TestUpdateDocumentRequest_Defaults(t *testing.T) {
	req := updateDocumentRequest{}
	if req.Content != "" || req.Language != "" || req.Category != "" || len(req.Tags) != 0 {
		t.Fatal("zero-value updateDocumentRequest should have empty fields")
	}
}
