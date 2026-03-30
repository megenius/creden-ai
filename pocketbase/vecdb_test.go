//go:build fts5

package main

import (
	"database/sql"
	"fmt"
	"testing"
)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// openTestVecDB creates a VecDB backed by a temp directory for the test.
func openTestVecDB(t *testing.T, dim int, metric string) *VecDB {
	t.Helper()
	dir := t.TempDir()
	vdb, err := OpenVecDB(dir, dim, metric)
	if err != nil {
		t.Fatalf("OpenVecDB: %v", err)
	}
	t.Cleanup(func() { vdb.Close() })
	return vdb
}

// makeVecBlob4 is a shorthand for a 4-dim embedding blob.
func makeVecBlob4(a, b, c, d float32) []byte {
	return makeBlob([]float32{a, b, c, d})
}

// makeEmbedding64 converts float32 values to the float64 slice and then to a blob.
func embedBlob(v []float64) []byte {
	return float64sToVecBlob(v)
}

// ---------------------------------------------------------------------------
// OpenVecDB / migrate
// ---------------------------------------------------------------------------

func TestOpenVecDB_CreatesDatabase(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	if vdb == nil {
		t.Fatal("expected non-nil VecDB")
	}
}

func TestOpenVecDB_CosineMetric(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	if vdb.metric != "cosine" {
		t.Fatalf("metric: want 'cosine', got %q", vdb.metric)
	}
}

func TestOpenVecDB_L2Metric(t *testing.T) {
	vdb := openTestVecDB(t, 4, "l2")
	if vdb.metric != "l2" {
		t.Fatalf("metric: want 'l2', got %q", vdb.metric)
	}
}

func TestOpenVecDB_FTSEnabled(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	if !vdb.fts {
		t.Fatal("FTS should be enabled after migration")
	}
}

func TestOpenVecDB_MetaTablePopulated(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	var metric, version string
	err := vdb.db.QueryRow("SELECT value FROM _vec_meta WHERE key = 'metric'").Scan(&metric)
	if err != nil {
		t.Fatalf("read metric from meta: %v", err)
	}
	err = vdb.db.QueryRow("SELECT value FROM _vec_meta WHERE key = 'version'").Scan(&version)
	if err != nil {
		t.Fatalf("read version from meta: %v", err)
	}
	if metric != "cosine" {
		t.Fatalf("meta metric: want 'cosine', got %q", metric)
	}
	if version != "3" {
		t.Fatalf("meta version: want '3', got %q", version)
	}
}

func TestOpenVecDB_Idempotent(t *testing.T) {
	// Opening the same dir twice should succeed.
	dir := t.TempDir()
	vdb1, err := OpenVecDB(dir, 4, "cosine")
	if err != nil {
		t.Fatalf("first open: %v", err)
	}
	vdb1.Close()

	vdb2, err := OpenVecDB(dir, 4, "cosine")
	if err != nil {
		t.Fatalf("second open: %v", err)
	}
	vdb2.Close()
}

// ---------------------------------------------------------------------------
// InsertEmbedding
// ---------------------------------------------------------------------------

func TestInsertEmbedding_Success(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	blob := makeVecBlob4(1, 0, 0, 0)
	if err := vdb.InsertEmbedding("doc1", blob, "hello world", DocMeta{Language: "en", Category: "test"}); err != nil {
		t.Fatalf("InsertEmbedding: %v", err)
	}
}

func TestInsertEmbedding_IndexedInFTS(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	blob := makeVecBlob4(1, 0, 0, 0)
	_ = vdb.InsertEmbedding("doc1", blob, "unique phrase here", DocMeta{})

	var count int
	err := vdb.db.QueryRow("SELECT COUNT(*) FROM fts_documents WHERE document_id = 'doc1'").Scan(&count)
	if err != nil {
		t.Fatalf("query fts: %v", err)
	}
	if count != 1 {
		t.Fatalf("expected 1 FTS row, got %d", count)
	}
}

func TestInsertEmbedding_EmptyContentSkipsFTS(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	blob := makeVecBlob4(1, 0, 0, 0)
	_ = vdb.InsertEmbedding("doc1", blob, "", DocMeta{})

	var count int
	err := vdb.db.QueryRow("SELECT COUNT(*) FROM fts_documents WHERE document_id = 'doc1'").Scan(&count)
	if err != nil {
		t.Fatalf("query fts: %v", err)
	}
	if count != 0 {
		t.Fatalf("empty content should not be indexed in FTS, got %d rows", count)
	}
}

func TestInsertEmbedding_MetadataStored(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	blob := makeVecBlob4(0.5, 0.5, 0, 0)
	meta := DocMeta{Language: "fr", Category: "science"}
	_ = vdb.InsertEmbedding("doc1", blob, "content", meta)

	var lang, cat sql.NullString
	err := vdb.db.QueryRow("SELECT language, category FROM vec_documents WHERE document_id = 'doc1'").Scan(&lang, &cat)
	if err != nil {
		t.Fatalf("query metadata: %v", err)
	}
	if lang.String != "fr" {
		t.Fatalf("language: want 'fr', got %q", lang.String)
	}
	if cat.String != "science" {
		t.Fatalf("category: want 'science', got %q", cat.String)
	}
}

func TestInsertEmbedding_DuplicateID(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	blob := makeVecBlob4(1, 0, 0, 0)
	_ = vdb.InsertEmbedding("doc1", blob, "first", DocMeta{})
	// Inserting the same docID should fail (PRIMARY KEY constraint).
	err := vdb.InsertEmbedding("doc1", blob, "second", DocMeta{})
	if err == nil {
		t.Fatal("expected error for duplicate document ID")
	}
}

// ---------------------------------------------------------------------------
// DeleteEmbedding
// ---------------------------------------------------------------------------

func TestDeleteEmbedding_Success(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	blob := makeVecBlob4(1, 0, 0, 0)
	_ = vdb.InsertEmbedding("doc1", blob, "hello", DocMeta{})
	if err := vdb.DeleteEmbedding("doc1"); err != nil {
		t.Fatalf("DeleteEmbedding: %v", err)
	}
}

func TestDeleteEmbedding_RemovesFromVec0(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	blob := makeVecBlob4(1, 0, 0, 0)
	_ = vdb.InsertEmbedding("doc1", blob, "", DocMeta{})
	_ = vdb.DeleteEmbedding("doc1")

	var count int
	_ = vdb.db.QueryRow("SELECT COUNT(*) FROM vec_documents WHERE document_id = 'doc1'").Scan(&count)
	if count != 0 {
		t.Fatalf("expected 0 vec rows after delete, got %d", count)
	}
}

func TestDeleteEmbedding_RemovesFromFTS(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	blob := makeVecBlob4(1, 0, 0, 0)
	_ = vdb.InsertEmbedding("doc1", blob, "some text", DocMeta{})
	_ = vdb.DeleteEmbedding("doc1")

	var count int
	_ = vdb.db.QueryRow("SELECT COUNT(*) FROM fts_documents WHERE document_id = 'doc1'").Scan(&count)
	if count != 0 {
		t.Fatalf("expected 0 FTS rows after delete, got %d", count)
	}
}

func TestDeleteEmbedding_NonExistentID(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	// sqlite-vec DELETE on a missing key should not error.
	err := vdb.DeleteEmbedding("ghost")
	if err != nil {
		t.Fatalf("expected no error for non-existent delete, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// SearchKNN flat (no HNSW)
// ---------------------------------------------------------------------------

func TestSearchKNNFlat_EmptyDatabase(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	blob := makeVecBlob4(1, 0, 0, 0)
	results, err := vdb.SearchKNN(blob, 5, 0, false, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchKNN on empty db: %v", err)
	}
	if len(results) != 0 {
		t.Fatalf("expected 0 results, got %d", len(results))
	}
}

func TestSearchKNNFlat_FindsInsertedDocument(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	_ = vdb.InsertEmbedding("doc1", makeVecBlob4(1, 0, 0, 0), "", DocMeta{})

	results, err := vdb.SearchKNN(makeVecBlob4(1, 0, 0, 0), 5, 0, false, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchKNN: %v", err)
	}
	if len(results) == 0 {
		t.Fatal("expected 1 result")
	}
	if results[0].ID != "doc1" {
		t.Fatalf("expected 'doc1', got %q", results[0].ID)
	}
}

func TestSearchKNNFlat_KLimitRespected(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	for i := 0; i < 10; i++ {
		v := []float64{float64(i+1) * 0.1, 0, 0, 0}
		_ = vdb.InsertEmbedding(fmt.Sprintf("doc%d", i), embedBlob(v), "", DocMeta{})
	}
	results, err := vdb.SearchKNN(makeVecBlob4(1, 0, 0, 0), 3, 0, false, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchKNN: %v", err)
	}
	if len(results) > 3 {
		t.Fatalf("expected at most 3 results, got %d", len(results))
	}
}

func TestSearchKNNFlat_MaxDistanceFilters(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	// Insert a very different vector (high cosine distance from query).
	_ = vdb.InsertEmbedding("far", makeVecBlob4(0, 0, 0, 1), "", DocMeta{})
	_ = vdb.InsertEmbedding("close", makeVecBlob4(1, 0, 0, 0), "", DocMeta{})

	// With a tight max_distance, only the close document should appear.
	blob := makeVecBlob4(1, 0, 0, 0)
	results, err := vdb.SearchKNN(blob, 5, 0.01, false, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchKNN: %v", err)
	}
	for _, r := range results {
		if r.Distance > 0.01 {
			t.Fatalf("result %q has distance %f exceeding maxDistance 0.01", r.ID, r.Distance)
		}
	}
}

func TestSearchKNNFlat_NormalizeCosine(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	_ = vdb.InsertEmbedding("doc1", makeVecBlob4(1, 0, 0, 0), "", DocMeta{})

	results, err := vdb.SearchKNN(makeVecBlob4(1, 0, 0, 0), 1, 0, true, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchKNN: %v", err)
	}
	if len(results) == 0 {
		t.Fatal("no results")
	}
	r := results[0]
	// cosine similarity = 1 - distance; for identical vectors, distance ~0 → similarity ~1.
	if r.Similarity < 0.99 || r.Similarity > 1.01 {
		t.Fatalf("cosine similarity for identical vectors: want ~1.0, got %f", r.Similarity)
	}
}

func TestSearchKNNFlat_NormalizeL2(t *testing.T) {
	vdb := openTestVecDB(t, 4, "l2")
	_ = vdb.InsertEmbedding("doc1", makeVecBlob4(1, 0, 0, 0), "", DocMeta{})

	results, err := vdb.SearchKNN(makeVecBlob4(1, 0, 0, 0), 1, 0, true, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchKNN: %v", err)
	}
	if len(results) == 0 {
		t.Fatal("no results")
	}
	// l2 similarity = 1 / (1 + distance); for identical vectors distance ~0 → similarity ~1.
	if results[0].Similarity < 0.99 {
		t.Fatalf("l2 similarity for identical vectors: want ~1.0, got %f", results[0].Similarity)
	}
}

func TestSearchKNNFlat_NoNormalizeZeroSimilarity(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	_ = vdb.InsertEmbedding("doc1", makeVecBlob4(1, 0, 0, 0), "", DocMeta{})

	results, err := vdb.SearchKNN(makeVecBlob4(1, 0, 0, 0), 1, 0, false, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchKNN: %v", err)
	}
	if len(results) == 0 {
		t.Fatal("no results")
	}
	if results[0].Similarity != 0 {
		t.Fatalf("without normalize, similarity should be 0, got %f", results[0].Similarity)
	}
}

func TestSearchKNNFlat_LanguageFilter(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	_ = vdb.InsertEmbedding("en1", makeVecBlob4(1, 0, 0, 0), "", DocMeta{Language: "en"})
	_ = vdb.InsertEmbedding("fr1", makeVecBlob4(0.9, 0.1, 0, 0), "", DocMeta{Language: "fr"})

	results, err := vdb.SearchKNN(makeVecBlob4(1, 0, 0, 0), 5, 0, false, SearchFilters{Language: "en"})
	if err != nil {
		t.Fatalf("SearchKNN with language filter: %v", err)
	}
	for _, r := range results {
		if r.ID == "fr1" {
			t.Fatal("language filter should have excluded fr1")
		}
	}
}

func TestSearchKNNFlat_CategoryFilter(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	_ = vdb.InsertEmbedding("sci", makeVecBlob4(1, 0, 0, 0), "", DocMeta{Category: "science"})
	_ = vdb.InsertEmbedding("art", makeVecBlob4(0.9, 0.1, 0, 0), "", DocMeta{Category: "art"})

	results, err := vdb.SearchKNN(makeVecBlob4(1, 0, 0, 0), 5, 0, false, SearchFilters{Category: "science"})
	if err != nil {
		t.Fatalf("SearchKNN with category filter: %v", err)
	}
	for _, r := range results {
		if r.ID == "art" {
			t.Fatal("category filter should have excluded art")
		}
	}
}

// ---------------------------------------------------------------------------
// SearchKNN via HNSW routing
// ---------------------------------------------------------------------------

func TestSearchKNNHNSW_RoutesToHNSW(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	idx := makeHNSW(t, "cosine")
	vdb.SetHNSW(idx)

	_ = vdb.InsertEmbedding("doc1", makeVecBlob4(1, 0, 0, 0), "", DocMeta{})
	_ = idx.Insert("doc1", makeVec4(1, 0, 0, 0))

	results, err := vdb.SearchKNN(makeVecBlob4(1, 0, 0, 0), 1, 0, false, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchKNN via HNSW: %v", err)
	}
	if len(results) == 0 {
		t.Fatal("expected at least 1 result")
	}
	if results[0].ID != "doc1" {
		t.Fatalf("expected 'doc1', got %q", results[0].ID)
	}
}

func TestSearchKNNHNSW_OversamplesForFilter(t *testing.T) {
	// When filters are set, HNSW should oversample (k*3) then post-filter.
	// Verify results still respect the language filter.
	vdb := openTestVecDB(t, 4, "cosine")
	idx := makeHNSW(t, "cosine")
	vdb.SetHNSW(idx)

	_ = vdb.InsertEmbedding("en1", makeVecBlob4(1, 0, 0, 0), "", DocMeta{Language: "en"})
	_ = vdb.InsertEmbedding("fr1", makeVecBlob4(0.9, 0.1, 0, 0), "", DocMeta{Language: "fr"})
	_ = idx.Insert("en1", makeVec4(1, 0, 0, 0))
	_ = idx.Insert("fr1", makeVec4(0.9, 0.1, 0, 0))

	results, err := vdb.SearchKNN(makeVecBlob4(1, 0, 0, 0), 5, 0, false, SearchFilters{Language: "en"})
	if err != nil {
		t.Fatalf("SearchKNN HNSW with filter: %v", err)
	}
	for _, r := range results {
		if r.ID == "fr1" {
			t.Fatal("HNSW post-filter should have excluded fr1")
		}
	}
}

func TestSearchKNNHNSW_MaxDistanceFilters(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	idx := makeHNSW(t, "cosine")
	vdb.SetHNSW(idx)

	_ = vdb.InsertEmbedding("close", makeVecBlob4(1, 0, 0, 0), "", DocMeta{})
	_ = vdb.InsertEmbedding("far", makeVecBlob4(0, 0, 0, 1), "", DocMeta{})
	_ = idx.Insert("close", makeVec4(1, 0, 0, 0))
	_ = idx.Insert("far", makeVec4(0, 0, 0, 1))

	results, err := vdb.SearchKNN(makeVecBlob4(1, 0, 0, 0), 5, 0.01, false, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchKNN HNSW maxDistance: %v", err)
	}
	for _, r := range results {
		if r.Distance > 0.01 {
			t.Fatalf("result %q distance %f exceeds maxDistance", r.ID, r.Distance)
		}
	}
}

// ---------------------------------------------------------------------------
// SetHNSW
// ---------------------------------------------------------------------------

func TestSetHNSW_AttachDetach(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	if vdb.hnsw != nil {
		t.Fatal("expected no HNSW initially")
	}
	idx := makeHNSW(t, "cosine")
	vdb.SetHNSW(idx)
	if vdb.hnsw == nil {
		t.Fatal("expected HNSW to be set")
	}
	vdb.SetHNSW(nil)
	if vdb.hnsw != nil {
		t.Fatal("expected HNSW to be nil after detach")
	}
}

// ---------------------------------------------------------------------------
// postFilterByMeta
// ---------------------------------------------------------------------------

func TestPostFilterByMeta_LanguageFilter(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	_ = vdb.InsertEmbedding("en1", makeVecBlob4(1, 0, 0, 0), "", DocMeta{Language: "en"})
	_ = vdb.InsertEmbedding("fr1", makeVecBlob4(0, 1, 0, 0), "", DocMeta{Language: "fr"})

	input := []SearchResult{{ID: "en1"}, {ID: "fr1"}}
	filtered := vdb.postFilterByMeta(input, SearchFilters{Language: "en"})
	if len(filtered) != 1 {
		t.Fatalf("want 1 result, got %d", len(filtered))
	}
	if filtered[0].ID != "en1" {
		t.Fatalf("expected en1, got %q", filtered[0].ID)
	}
}

func TestPostFilterByMeta_CategoryFilter(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	_ = vdb.InsertEmbedding("sci", makeVecBlob4(1, 0, 0, 0), "", DocMeta{Category: "science"})
	_ = vdb.InsertEmbedding("art", makeVecBlob4(0, 1, 0, 0), "", DocMeta{Category: "art"})

	input := []SearchResult{{ID: "sci"}, {ID: "art"}}
	filtered := vdb.postFilterByMeta(input, SearchFilters{Category: "science"})
	if len(filtered) != 1 || filtered[0].ID != "sci" {
		t.Fatalf("expected only 'sci', got %v", filtered)
	}
}

func TestPostFilterByMeta_MissingDocSkipped(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	input := []SearchResult{{ID: "ghost"}}
	filtered := vdb.postFilterByMeta(input, SearchFilters{Language: "en"})
	if len(filtered) != 0 {
		t.Fatalf("expected 0 results for missing doc, got %d", len(filtered))
	}
}

func TestPostFilterByMeta_NoFiltersReturnsAll(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	_ = vdb.InsertEmbedding("d1", makeVecBlob4(1, 0, 0, 0), "", DocMeta{Language: "en"})
	_ = vdb.InsertEmbedding("d2", makeVecBlob4(0, 1, 0, 0), "", DocMeta{Language: "fr"})

	input := []SearchResult{{ID: "d1"}, {ID: "d2"}}
	// Empty filters — but postFilterByMeta is called with specific filter values,
	// so we skip language/category — test that both pass when language="" and category="".
	filtered := vdb.postFilterByMeta(input, SearchFilters{Language: "", Category: ""})
	if len(filtered) != 2 {
		t.Fatalf("expected 2 results with no filter, got %d", len(filtered))
	}
}

// ---------------------------------------------------------------------------
// SearchHybrid
// ---------------------------------------------------------------------------

func TestSearchHybrid_VectorOnlyNoFTSQuery(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	_ = vdb.InsertEmbedding("doc1", makeVecBlob4(1, 0, 0, 0), "technology news", DocMeta{})

	results, err := vdb.SearchHybrid(makeVecBlob4(1, 0, 0, 0), "", 5, 0.7, 0.3, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchHybrid: %v", err)
	}
	// Without a text query only the vector component fires.
	if len(results) == 0 {
		t.Fatal("expected at least 1 result")
	}
}

func TestSearchHybrid_DefaultWeightsApplied(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	_ = vdb.InsertEmbedding("doc1", makeVecBlob4(1, 0, 0, 0), "content", DocMeta{})

	// vecWeight and textWeight both 0 — defaults should be 0.7/0.3.
	results, err := vdb.SearchHybrid(makeVecBlob4(1, 0, 0, 0), "content", 5, 0, 0, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchHybrid: %v", err)
	}
	if len(results) == 0 {
		t.Fatal("expected results")
	}
	// Combined score for a vector-only match should be > 0.
	if results[0].CombinedScore <= 0 {
		t.Fatalf("combined score should be positive, got %f", results[0].CombinedScore)
	}
}

func TestSearchHybrid_CombinedScoreSortedDescending(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	for i := 0; i < 5; i++ {
		v := float32(i+1) * 0.2
		_ = vdb.InsertEmbedding(fmt.Sprintf("doc%d", i), makeVecBlob4(v, 0, 0, 0), fmt.Sprintf("word%d", i), DocMeta{})
	}

	results, err := vdb.SearchHybrid(makeVecBlob4(1, 0, 0, 0), "word4", 10, 0.7, 0.3, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchHybrid: %v", err)
	}
	for i := 1; i < len(results); i++ {
		if results[i].CombinedScore > results[i-1].CombinedScore {
			t.Fatalf("results not sorted by CombinedScore desc at position %d", i)
		}
	}
}

func TestSearchHybrid_MatchSourceBoth(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	_ = vdb.InsertEmbedding("doc1", makeVecBlob4(1, 0, 0, 0), "unique content here", DocMeta{})

	results, err := vdb.SearchHybrid(makeVecBlob4(1, 0, 0, 0), "unique", 5, 0.7, 0.3, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchHybrid: %v", err)
	}
	for _, r := range results {
		if r.ID == "doc1" && r.MatchSource != "both" {
			t.Fatalf("doc1 matched by both vector and text but source=%q", r.MatchSource)
		}
	}
}

func TestSearchHybrid_MatchSourceText(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	// Insert a doc with a very different vector so it won't appear in vector search,
	// but with text that matches the FTS query.
	_ = vdb.InsertEmbedding("textonly", makeVecBlob4(0, 0, 0, 1), "xylophone music", DocMeta{})

	// Large fetchK via vector with a far vector won't catch "textonly",
	// but FTS will.  Use a near query vector to trigger vector part for a different doc.
	_ = vdb.InsertEmbedding("veconly", makeVecBlob4(1, 0, 0, 0), "no match here", DocMeta{})

	results, err := vdb.SearchHybrid(makeVecBlob4(1, 0, 0, 0), "xylophone", 10, 0.7, 0.3, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchHybrid: %v", err)
	}
	for _, r := range results {
		if r.ID == "textonly" {
			// It's possible vector search also returns it (with worse score), so
			// MatchSource could be "text" or "both".
			if r.MatchSource != "text" && r.MatchSource != "both" {
				t.Fatalf("textonly match source: want 'text' or 'both', got %q", r.MatchSource)
			}
		}
	}
}

func TestSearchHybrid_KLimitRespected(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	for i := 0; i < 10; i++ {
		_ = vdb.InsertEmbedding(
			fmt.Sprintf("doc%d", i),
			makeVecBlob4(float32(i+1)*0.1, 0, 0, 0),
			fmt.Sprintf("text %d", i),
			DocMeta{},
		)
	}

	results, err := vdb.SearchHybrid(makeVecBlob4(1, 0, 0, 0), "text", 3, 0.5, 0.5, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchHybrid: %v", err)
	}
	if len(results) > 3 {
		t.Fatalf("expected at most 3 results, got %d", len(results))
	}
}

func TestSearchHybrid_EmptyDB(t *testing.T) {
	vdb := openTestVecDB(t, 4, "cosine")
	results, err := vdb.SearchHybrid(makeVecBlob4(1, 0, 0, 0), "anything", 5, 0.7, 0.3, SearchFilters{})
	if err != nil {
		t.Fatalf("SearchHybrid empty: %v", err)
	}
	if len(results) != 0 {
		t.Fatalf("expected 0 results from empty db, got %d", len(results))
	}
}

// ---------------------------------------------------------------------------
// SearchFilters.empty()
// ---------------------------------------------------------------------------

func TestSearchFilters_Empty(t *testing.T) {
	f := SearchFilters{}
	if !f.empty() {
		t.Fatal("zero-value SearchFilters should be empty")
	}
}

func TestSearchFilters_NotEmptyLanguage(t *testing.T) {
	f := SearchFilters{Language: "en"}
	if f.empty() {
		t.Fatal("SearchFilters with Language should not be empty")
	}
}

func TestSearchFilters_NotEmptyCategory(t *testing.T) {
	f := SearchFilters{Category: "science"}
	if f.empty() {
		t.Fatal("SearchFilters with Category should not be empty")
	}
}

func TestSearchFilters_NotEmptyTags(t *testing.T) {
	f := SearchFilters{Tags: []string{"go"}}
	if f.empty() {
		t.Fatal("SearchFilters with Tags should not be empty")
	}
}

// ---------------------------------------------------------------------------
// sortHybridResults
// ---------------------------------------------------------------------------

func TestSortHybridResults_Descending(t *testing.T) {
	results := []HybridResult{
		{CombinedScore: 0.3},
		{CombinedScore: 0.9},
		{CombinedScore: 0.1},
		{CombinedScore: 0.7},
	}
	sortHybridResults(results)
	for i := 1; i < len(results); i++ {
		if results[i].CombinedScore > results[i-1].CombinedScore {
			t.Fatalf("not sorted at index %d: %f > %f", i, results[i].CombinedScore, results[i-1].CombinedScore)
		}
	}
}

func TestSortHybridResults_SingleElement(t *testing.T) {
	results := []HybridResult{{CombinedScore: 0.5}}
	sortHybridResults(results)
	if results[0].CombinedScore != 0.5 {
		t.Fatal("single element sort should not change value")
	}
}

func TestSortHybridResults_Empty(t *testing.T) {
	// Should not panic.
	sortHybridResults(nil)
	sortHybridResults([]HybridResult{})
}

// ---------------------------------------------------------------------------
// float64sToVecBlob (defined in main.go)
// ---------------------------------------------------------------------------

func TestFloat64sToVecBlob_RoundTrip(t *testing.T) {
	input := []float64{1.5, -2.5, 3.14, 0.0}
	blob := float64sToVecBlob(input)
	got, err := blobToFloat32Slice(blob)
	if err != nil {
		t.Fatalf("blobToFloat32Slice: %v", err)
	}
	if len(got) != len(input) {
		t.Fatalf("length: want %d, got %d", len(input), len(got))
	}
	// float64 → float32 truncation, so compare as float32.
	for i, v := range input {
		want := float32(v)
		if got[i] != want {
			t.Errorf("[%d]: want %f, got %f", i, want, got[i])
		}
	}
}

func TestFloat64sToVecBlob_Empty(t *testing.T) {
	blob := float64sToVecBlob([]float64{})
	if len(blob) != 0 {
		t.Fatalf("expected empty blob, got len %d", len(blob))
	}
}

func TestFloat64sToVecBlob_LittleEndian(t *testing.T) {
	// float32(1.0) has IEEE bits 0x3F800000; in little-endian: 00 00 80 3F.
	blob := float64sToVecBlob([]float64{1.0})
	if len(blob) != 4 {
		t.Fatalf("want 4 bytes, got %d", len(blob))
	}
	if blob[0] != 0x00 || blob[1] != 0x00 || blob[2] != 0x80 || blob[3] != 0x3F {
		t.Fatalf("little-endian encoding: got % X", blob)
	}
}

// ---------------------------------------------------------------------------
// Close
// ---------------------------------------------------------------------------

func TestVecDB_Close(t *testing.T) {
	dir := t.TempDir()
	vdb, err := OpenVecDB(dir, 4, "cosine")
	if err != nil {
		t.Fatalf("OpenVecDB: %v", err)
	}
	if err := vdb.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
}
