//go:build fts5

package main

import (
	"encoding/binary"
	"math"
	"os"
	"path/filepath"
	"sync"
	"testing"
)

// makeBlob encodes a []float32 as a little-endian float32 byte blob.
func makeBlob(v []float32) []byte {
	buf := make([]byte, len(v)*4)
	for i, f := range v {
		binary.LittleEndian.PutUint32(buf[i*4:], math.Float32bits(f))
	}
	return buf
}

// makeVec4 returns a simple 4-dimensional float32 vector.
func makeVec4(a, b, c, d float32) []float32 {
	return []float32{a, b, c, d}
}

// makeHNSW creates an HNSWIndex with dim=4 for tests.
func makeHNSW(t *testing.T, metric string) *HNSWIndex {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "test.idx")
	opts := HNSWOpts{M: 4, EfConstruction: 20, EfSearch: 10}
	return NewHNSWIndex(4, metric, path, opts)
}

// ---------------------------------------------------------------------------
// NewHNSWIndex
// ---------------------------------------------------------------------------

func TestNewHNSWIndex_DefaultsApplied(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "test.idx")
	// Pass zero opts — should use defaultHNSWOpts().
	idx := NewHNSWIndex(4, "cosine", path, HNSWOpts{})
	if idx.dimension != 4 {
		t.Fatalf("dimension: want 4, got %d", idx.dimension)
	}
	if idx.metric != "cosine" {
		t.Fatalf("metric: want cosine, got %s", idx.metric)
	}
	if idx.opts.M != 16 {
		t.Fatalf("opts.M: want 16, got %d", idx.opts.M)
	}
}

func TestNewHNSWIndex_EmptyOnCreation(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	if idx.Len() != 0 {
		t.Fatalf("new index should be empty, got %d", idx.Len())
	}
}

func TestNewHNSWIndex_L2Metric(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "test.idx")
	idx := NewHNSWIndex(4, "l2", path, HNSWOpts{M: 4, EfConstruction: 20, EfSearch: 10})
	if idx.metric != "l2" {
		t.Fatalf("metric: want l2, got %s", idx.metric)
	}
}

// ---------------------------------------------------------------------------
// Insert
// ---------------------------------------------------------------------------

func TestInsert_SingleVector(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	if err := idx.Insert("doc1", makeVec4(1, 0, 0, 0)); err != nil {
		t.Fatalf("Insert failed: %v", err)
	}
	if idx.Len() != 1 {
		t.Fatalf("after 1 insert: want len 1, got %d", idx.Len())
	}
}

func TestInsert_MultipleVectors(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	for i, v := range [][]float32{
		{1, 0, 0, 0},
		{0, 1, 0, 0},
		{0, 0, 1, 0},
	} {
		if err := idx.Insert(string(rune('A'+i)), v); err != nil {
			t.Fatalf("Insert[%d] failed: %v", i, err)
		}
	}
	if idx.Len() != 3 {
		t.Fatalf("want 3, got %d", idx.Len())
	}
}

func TestInsert_DimensionMismatch(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	err := idx.Insert("bad", []float32{1, 2}) // dim=2 vs idx dim=4
	if err == nil {
		t.Fatal("expected dimension mismatch error, got nil")
	}
}

func TestInsert_EmptyVector(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	err := idx.Insert("empty", []float32{})
	if err == nil {
		t.Fatal("expected error for empty vector")
	}
}

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------

func TestDelete_ExistingDocument(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	if err := idx.Insert("doc1", makeVec4(1, 0, 0, 0)); err != nil {
		t.Fatalf("Insert: %v", err)
	}
	if err := idx.Delete("doc1"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	if idx.Len() != 0 {
		t.Fatalf("after delete: want len 0, got %d", idx.Len())
	}
}

func TestDelete_NonExistentDocument(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	err := idx.Delete("ghost")
	if err == nil {
		t.Fatal("expected error deleting non-existent document")
	}
}

func TestDelete_OneOfMany(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	ids := []string{"a", "b", "c"}
	vecs := [][]float32{{1, 0, 0, 0}, {0, 1, 0, 0}, {0, 0, 1, 0}}
	for i, id := range ids {
		_ = idx.Insert(id, vecs[i])
	}
	if err := idx.Delete("b"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	if idx.Len() != 2 {
		t.Fatalf("want 2 remaining, got %d", idx.Len())
	}
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

func TestSearch_EmptyIndex(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	results, err := idx.Search(makeVec4(1, 0, 0, 0), 5)
	if err != nil {
		t.Fatalf("Search on empty index: %v", err)
	}
	if len(results) != 0 {
		t.Fatalf("expected 0 results, got %d", len(results))
	}
}

func TestSearch_DimensionMismatch(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	_, err := idx.Search([]float32{1, 2}, 5) // wrong dim
	if err == nil {
		t.Fatal("expected dimension mismatch error")
	}
}

func TestSearch_ReturnsNearestNeighbor(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	// Insert several vectors; query is closest to "near".
	_ = idx.Insert("far", makeVec4(0, 0, 0, 1))
	_ = idx.Insert("near", makeVec4(1, 0, 0, 0))
	_ = idx.Insert("mid", makeVec4(0.5, 0.5, 0, 0))

	results, err := idx.Search(makeVec4(1, 0, 0, 0), 1)
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(results) == 0 {
		t.Fatal("expected at least 1 result")
	}
	if results[0].ID != "near" {
		t.Fatalf("nearest neighbour: want 'near', got %q", results[0].ID)
	}
}

func TestSearch_KLimitRespected(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	for i := 0; i < 10; i++ {
		v := makeVec4(float32(i)*0.1, 0, 0, 0)
		if v[0] == 0 {
			v[0] = 0.001 // avoid zero vector
		}
		_ = idx.Insert(string(rune('a'+i)), v)
	}
	results, err := idx.Search(makeVec4(1, 0, 0, 0), 3)
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(results) > 3 {
		t.Fatalf("expected at most 3 results, got %d", len(results))
	}
}

func TestSearch_DistanceAndSimilarityPopulated(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	_ = idx.Insert("doc1", makeVec4(1, 0, 0, 0))

	results, err := idx.Search(makeVec4(1, 0, 0, 0), 1)
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(results) == 0 {
		t.Fatal("no results")
	}
	r := results[0]
	if r.Distance < 0 {
		t.Fatalf("distance must be non-negative, got %f", r.Distance)
	}
	if r.Similarity < 0 || r.Similarity > 1.01 {
		t.Fatalf("cosine similarity out of [0,1]: %f", r.Similarity)
	}
}

func TestSearch_L2SimilarityPositive(t *testing.T) {
	idx := makeHNSW(t, "l2")
	_ = idx.Insert("doc1", makeVec4(1, 0, 0, 0))

	results, err := idx.Search(makeVec4(1, 0, 0, 0), 1)
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(results) == 0 {
		t.Fatal("no results")
	}
	if results[0].Similarity <= 0 {
		t.Fatalf("l2 similarity must be positive, got %f", results[0].Similarity)
	}
}

// ---------------------------------------------------------------------------
// Save / Load persistence
// ---------------------------------------------------------------------------

func TestSaveLoad_RoundTrip(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "idx.bin")
	opts := HNSWOpts{M: 4, EfConstruction: 20, EfSearch: 10}

	// Build and populate an index.
	idx := NewHNSWIndex(4, "cosine", path, opts)
	vecs := map[string][]float32{
		"a": {1, 0, 0, 0},
		"b": {0, 1, 0, 0},
		"c": {0, 0, 1, 0},
	}
	for id, v := range vecs {
		if err := idx.Insert(id, v); err != nil {
			t.Fatalf("Insert %s: %v", id, err)
		}
	}
	if err := idx.Save(); err != nil {
		t.Fatalf("Save: %v", err)
	}

	// Load it back.
	loaded, err := LoadHNSWIndex(path, 4, "cosine", opts)
	if err != nil {
		t.Fatalf("LoadHNSWIndex: %v", err)
	}
	if loaded.Len() != 3 {
		t.Fatalf("loaded index: want 3 nodes, got %d", loaded.Len())
	}

	// Verify search still works.
	results, err := loaded.Search(makeVec4(1, 0, 0, 0), 1)
	if err != nil {
		t.Fatalf("Search after load: %v", err)
	}
	if len(results) == 0 {
		t.Fatal("search after load returned no results")
	}
	if results[0].ID != "a" {
		t.Fatalf("expected 'a', got %q", results[0].ID)
	}
}

func TestLoadHNSWIndex_FileNotFound(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "nonexistent.idx")
	_, err := LoadHNSWIndex(path, 4, "cosine", defaultHNSWOpts())
	if err == nil {
		t.Fatal("expected error loading non-existent index file")
	}
}

func TestSave_CreatesFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "idx.bin")
	idx := NewHNSWIndex(4, "cosine", path, HNSWOpts{M: 4, EfConstruction: 20, EfSearch: 10})
	_ = idx.Insert("x", makeVec4(1, 0, 0, 0))
	if err := idx.Save(); err != nil {
		t.Fatalf("Save: %v", err)
	}
	if _, err := os.Stat(path); os.IsNotExist(err) {
		t.Fatal("expected index file to exist after Save")
	}
}

// ---------------------------------------------------------------------------
// RebuildFromVecDB
// ---------------------------------------------------------------------------

func TestRebuildFromVecDB_PopulatesIndex(t *testing.T) {
	dir := t.TempDir()
	vdb, err := OpenVecDB(dir, 4, "cosine")
	if err != nil {
		t.Fatalf("OpenVecDB: %v", err)
	}
	defer vdb.Close()

	// Insert embeddings into VecDB.
	type vecEntry struct {
		id  string
		vec []float32
	}
	vecs := []vecEntry{
		{"doc1", []float32{1, 0, 0, 0}},
		{"doc2", []float32{0, 1, 0, 0}},
		{"doc3", []float32{0, 0, 1, 0}},
	}
	for _, v := range vecs {
		blob := makeBlob(v.vec)
		if err := vdb.InsertEmbedding(v.id, blob, "content", DocMeta{}); err != nil {
			t.Fatalf("InsertEmbedding %s: %v", v.id, err)
		}
	}

	idx := makeHNSW(t, "cosine")
	if err := idx.RebuildFromVecDB(vdb.db, 4); err != nil {
		t.Fatalf("RebuildFromVecDB: %v", err)
	}
	if idx.Len() != 3 {
		t.Fatalf("want 3 nodes after rebuild, got %d", idx.Len())
	}
}

func TestRebuildFromVecDB_EmptyTable(t *testing.T) {
	dir := t.TempDir()
	vdb, err := OpenVecDB(dir, 4, "cosine")
	if err != nil {
		t.Fatalf("OpenVecDB: %v", err)
	}
	defer vdb.Close()

	idx := makeHNSW(t, "cosine")
	if err := idx.RebuildFromVecDB(vdb.db, 4); err != nil {
		t.Fatalf("RebuildFromVecDB on empty table: %v", err)
	}
	if idx.Len() != 0 {
		t.Fatalf("expected empty index, got %d", idx.Len())
	}
}

func TestRebuildFromVecDB_ReplacesExistingGraph(t *testing.T) {
	dir := t.TempDir()
	vdb, err := OpenVecDB(dir, 4, "cosine")
	if err != nil {
		t.Fatalf("OpenVecDB: %v", err)
	}
	defer vdb.Close()

	idx := makeHNSW(t, "cosine")
	// Insert one vector directly (won't be in vecdb).
	_ = idx.Insert("stale", makeVec4(0.5, 0.5, 0, 0))

	// VecDB has 2 different docs.
	_ = vdb.InsertEmbedding("doc1", makeBlob(makeVec4(1, 0, 0, 0)), "", DocMeta{})
	_ = vdb.InsertEmbedding("doc2", makeBlob(makeVec4(0, 1, 0, 0)), "", DocMeta{})

	if err := idx.RebuildFromVecDB(vdb.db, 4); err != nil {
		t.Fatalf("RebuildFromVecDB: %v", err)
	}
	// After rebuild the index should have only the 2 vecdb docs.
	if idx.Len() != 2 {
		t.Fatalf("want 2 nodes after rebuild, got %d", idx.Len())
	}
}

// ---------------------------------------------------------------------------
// blobToFloat32Slice
// ---------------------------------------------------------------------------

func TestBlobToFloat32Slice_RoundTrip(t *testing.T) {
	original := []float32{1.5, -2.5, 3.14, 0}
	blob := makeBlob(original)
	got, err := blobToFloat32Slice(blob)
	if err != nil {
		t.Fatalf("blobToFloat32Slice: %v", err)
	}
	if len(got) != len(original) {
		t.Fatalf("length: want %d, got %d", len(original), len(got))
	}
	for i, v := range original {
		if got[i] != v {
			t.Errorf("[%d]: want %f, got %f", i, v, got[i])
		}
	}
}

func TestBlobToFloat32Slice_NotMultipleOf4(t *testing.T) {
	_, err := blobToFloat32Slice([]byte{0x01, 0x02, 0x03}) // 3 bytes, not a multiple of 4
	if err == nil {
		t.Fatal("expected error for non-multiple-of-4 blob length")
	}
}

func TestBlobToFloat32Slice_EmptyBlob(t *testing.T) {
	got, err := blobToFloat32Slice([]byte{})
	if err != nil {
		t.Fatalf("empty blob should not error: %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("want empty slice, got len %d", len(got))
	}
}

func TestBlobToFloat32Slice_SpecialValues(t *testing.T) {
	// Encode zero and a known float.
	vals := []float32{0, 1}
	blob := makeBlob(vals)
	got, err := blobToFloat32Slice(blob)
	if err != nil {
		t.Fatalf("blobToFloat32Slice: %v", err)
	}
	if got[0] != 0 || got[1] != 1 {
		t.Fatalf("got %v, want [0 1]", got)
	}
}

// ---------------------------------------------------------------------------
// Concurrency
// ---------------------------------------------------------------------------

func TestHNSW_ConcurrentInsert(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			v := makeVec4(float32(i+1)*0.1, 0, 0, 0)
			_ = idx.Insert(string(rune('A'+i)), v)
		}(i)
	}
	wg.Wait()
	if idx.Len() == 0 {
		t.Fatal("expected some inserts to succeed")
	}
}

func TestHNSW_ConcurrentReadSearch(t *testing.T) {
	idx := makeHNSW(t, "cosine")
	for i := 0; i < 5; i++ {
		_ = idx.Insert(string(rune('a'+i)), makeVec4(float32(i+1)*0.2, 0, 0, 0))
	}

	var wg sync.WaitGroup
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, _ = idx.Search(makeVec4(1, 0, 0, 0), 3)
		}()
	}
	wg.Wait()
}

// ---------------------------------------------------------------------------
// distanceFunc helper
// ---------------------------------------------------------------------------

func TestDistanceFunc_Cosine(t *testing.T) {
	fn := distanceFunc("cosine")
	a := []float32{1, 0, 0, 0}
	b := []float32{0, 1, 0, 0}
	d := fn(a, b)
	// Perpendicular vectors have cosine similarity 0, so distance should be ~1.
	if d < 0.99 || d > 1.01 {
		t.Fatalf("cosine distance of perpendicular vectors: want ~1, got %f", d)
	}
}

func TestDistanceFunc_L2(t *testing.T) {
	fn := distanceFunc("l2")
	a := []float32{0, 0, 0, 0}
	b := []float32{1, 0, 0, 0}
	d := fn(a, b)
	if d <= 0 {
		t.Fatalf("l2 distance of distinct vectors must be positive, got %f", d)
	}
}

func TestDistanceFuncName(t *testing.T) {
	if distanceFuncName("cosine") != "cosine" {
		t.Fatal("expected 'cosine'")
	}
	if distanceFuncName("l2") != "euclidean" {
		t.Fatal("expected 'euclidean'")
	}
	if distanceFuncName("unknown") != "euclidean" {
		t.Fatal("expected 'euclidean' for unknown metric")
	}
}
