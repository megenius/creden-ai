package main

import (
	"database/sql"
	"fmt"
	"log"
	"path/filepath"
)

// VecDB wraps the sqlite-vec database and provides vector operations.
type VecDB struct {
	db        *sql.DB
	dimension int
	metric    string // "cosine" or "l2"
	dataDir   string
	hnsw      *HNSWIndex // optional; when set, SearchKNN uses HNSW
	fts       bool       // true when FTS5 table is ready
}

// SetHNSW attaches an HNSW index to the VecDB. Pass nil to disable.
func (vdb *VecDB) SetHNSW(idx *HNSWIndex) {
	vdb.hnsw = idx
}

// OpenVecDB creates (or opens) the sqlite-vec database, runs migrations,
// and returns a ready-to-use VecDB.
func OpenVecDB(dataDir string, dimension int, metric string) (*VecDB, error) {
	db, err := sql.Open("sqlite3", filepath.Join(dataDir, "vectors.db"))
	if err != nil {
		return nil, err
	}

	// Verify extension loaded.
	var version string
	if err := db.QueryRow("SELECT vec_version()").Scan(&version); err != nil {
		return nil, fmt.Errorf("sqlite-vec not loaded (is CGO enabled?): %w", err)
	}
	log.Printf("[vectordb] sqlite-vec %s loaded | dimensions=%d metric=%s", version, dimension, metric)

	vdb := &VecDB{
		db:        db,
		dimension: dimension,
		metric:    metric,
		dataDir:   dataDir,
	}

	if err := vdb.migrate(); err != nil {
		return nil, fmt.Errorf("migration: %w", err)
	}

	return vdb, nil
}

// schemaVersion is bumped whenever the vec0 table layout changes.
// v3 = initial multi-file split; v4 = metadata columns + cosine metric on vec0.
const schemaVersion = "4"

// migrate ensures the vec0 table and meta table exist with the correct schema.
// When upgrading from an older version it exports existing embeddings, drops the
// table, recreates with the full schema, and re-imports.
func (vdb *VecDB) migrate() error {
	// Create meta table for tracking schema version and config.
	if _, err := vdb.db.Exec(`
		CREATE TABLE IF NOT EXISTS _vec_meta (
			key   TEXT PRIMARY KEY,
			value TEXT
		)
	`); err != nil {
		return fmt.Errorf("create _vec_meta: %w", err)
	}

	// Read stored version and metric.
	var storedVersion, storedMetric string
	_ = vdb.db.QueryRow("SELECT value FROM _vec_meta WHERE key = 'version'").Scan(&storedVersion)
	_ = vdb.db.QueryRow("SELECT value FROM _vec_meta WHERE key = 'metric'").Scan(&storedMetric)

	needsRecreate := false
	if storedVersion != "" && storedVersion != schemaVersion {
		log.Printf("[vectordb] schema version %s → %s, upgrading vec0 table", storedVersion, schemaVersion)
		needsRecreate = true
	}
	if storedMetric != "" && storedMetric != vdb.metric {
		log.Printf("[vectordb] metric changed %s → %s, recreating vec0 table", storedMetric, vdb.metric)
		needsRecreate = true
	}

	// Export existing embeddings before dropping, so we can re-import.
	type savedRow struct {
		DocID     string
		Embedding []byte
	}
	var saved []savedRow

	if needsRecreate {
		rows, err := vdb.db.Query("SELECT document_id, embedding FROM vec_documents")
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var s savedRow
				if err := rows.Scan(&s.DocID, &s.Embedding); err == nil {
					saved = append(saved, s)
				}
			}
			rows.Close()
		}
		log.Printf("[vectordb] exported %d embeddings for migration", len(saved))

		if _, err := vdb.db.Exec("DROP TABLE IF EXISTS vec_documents"); err != nil {
			return fmt.Errorf("drop vec_documents: %w", err)
		}
	}

	// Build CREATE statement with optional distance_metric clause and metadata columns.
	distClause := ""
	if vdb.metric == "cosine" {
		distClause = " distance_metric=cosine"
	}

	createSQL := fmt.Sprintf(`
		CREATE VIRTUAL TABLE IF NOT EXISTS vec_documents USING vec0(
			document_id TEXT PRIMARY KEY,
			embedding   float[%d]%s,
			language    TEXT,
			category    TEXT
		)
	`, vdb.dimension, distClause)

	if _, err := vdb.db.Exec(createSQL); err != nil {
		return fmt.Errorf("create vec_documents: %w", err)
	}

	// Re-import saved embeddings (metadata will be blank — repopulated later from PocketBase).
	if len(saved) > 0 {
		tx, err := vdb.db.Begin()
		if err != nil {
			return fmt.Errorf("begin reimport tx: %w", err)
		}
		var imported int
		for _, s := range saved {
			if _, err := tx.Exec(
				"INSERT INTO vec_documents(document_id, embedding, language, category) VALUES (?, ?, '', '')",
				s.DocID, s.Embedding,
			); err != nil {
				log.Printf("[vectordb] reimport %q: %v", s.DocID, err)
				continue
			}
			imported++
		}
		if err := tx.Commit(); err != nil {
			return fmt.Errorf("commit reimport: %w", err)
		}
		log.Printf("[vectordb] re-imported %d/%d embeddings into upgraded vec0", imported, len(saved))
	}

	// FTS5 full-text search index.
	if _, err := vdb.db.Exec(`
		CREATE VIRTUAL TABLE IF NOT EXISTS fts_documents USING fts5(
			content,
			document_id UNINDEXED
		)
	`); err != nil {
		return fmt.Errorf("create fts_documents: %w", err)
	}
	vdb.fts = true

	// Store current config.
	if _, err := vdb.db.Exec(
		"INSERT OR REPLACE INTO _vec_meta(key, value) VALUES ('metric', ?), ('dimension', ?), ('version', ?)",
		vdb.metric, fmt.Sprintf("%d", vdb.dimension), schemaVersion,
	); err != nil {
		return fmt.Errorf("write meta: %w", err)
	}

	return nil
}

// DocMeta holds optional metadata for a document.
type DocMeta struct {
	Language string
	Category string
}

// InsertEmbedding adds an embedding (with optional metadata) to vec0 and FTS5 atomically.
func (vdb *VecDB) InsertEmbedding(docID string, embedding []byte, content string, meta DocMeta) error {
	tx, err := vdb.db.Begin()
	if err != nil {
		return fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	if _, err := tx.Exec(
		"INSERT INTO vec_documents(document_id, embedding, language, category) VALUES (?, ?, ?, ?)",
		docID, embedding, meta.Language, meta.Category,
	); err != nil {
		return err
	}

	if vdb.fts && content != "" {
		if _, err := tx.Exec(
			"INSERT INTO fts_documents(document_id, content) VALUES (?, ?)",
			docID, content,
		); err != nil {
			return fmt.Errorf("fts insert: %w", err)
		}
	}

	return tx.Commit()
}

// DeleteEmbedding removes an embedding from vec0 and FTS5 atomically.
func (vdb *VecDB) DeleteEmbedding(docID string) error {
	tx, err := vdb.db.Begin()
	if err != nil {
		return fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback()

	if _, err := tx.Exec("DELETE FROM vec_documents WHERE document_id = ?", docID); err != nil {
		return err
	}
	if vdb.fts {
		if _, err := tx.Exec("DELETE FROM fts_documents WHERE document_id = ?", docID); err != nil {
			return fmt.Errorf("fts delete: %w", err)
		}
	}

	return tx.Commit()
}

// SearchResult holds a single KNN search hit.
type SearchResult struct {
	ID         string  `json:"id"`
	Distance   float64 `json:"distance"`
	Similarity float64 `json:"similarity,omitempty"`
}

// SearchFilters holds optional metadata filters for search.
type SearchFilters struct {
	Language string   `json:"language,omitempty"`
	Category string   `json:"category,omitempty"`
	Tags     []string `json:"tags,omitempty"` // matched against PocketBase tags field
}

func (f SearchFilters) empty() bool {
	return f.Language == "" && f.Category == "" && len(f.Tags) == 0
}

// SearchKNN performs a k-nearest-neighbor search with optional metadata filters.
// When an HNSW index is attached, it is used with oversampled post-filtering.
func (vdb *VecDB) SearchKNN(query []byte, k int, maxDistance float64, normalize bool, filters SearchFilters) ([]SearchResult, error) {
	if vdb.hnsw != nil {
		return vdb.searchKNNHNSW(query, k, maxDistance, normalize, filters)
	}
	return vdb.searchKNNFlat(query, k, maxDistance, normalize, filters)
}

// searchKNNHNSW routes a KNN query through the in-memory HNSW index.
// Metadata filters use oversampled post-filtering (query k*3, then filter).
func (vdb *VecDB) searchKNNHNSW(query []byte, k int, maxDistance float64, normalize bool, filters SearchFilters) ([]SearchResult, error) {
	vec, err := blobToFloat32Slice(query)
	if err != nil {
		return nil, fmt.Errorf("decode query blob: %w", err)
	}

	fetchK := k
	if !filters.empty() {
		fetchK = k * 3 // oversample for post-filtering
	}

	raw, err := vdb.hnsw.Search(vec, fetchK)
	if err != nil {
		return nil, err
	}

	var results []SearchResult
	for _, r := range raw {
		if maxDistance > 0 && r.Distance > maxDistance {
			continue
		}
		if !normalize {
			r.Similarity = 0
		}
		results = append(results, r)
	}

	// Post-filter by metadata if needed (tags checked in handler via PocketBase).
	if !filters.empty() && (filters.Language != "" || filters.Category != "") {
		results = vdb.postFilterByMeta(results, filters)
	}

	if len(results) > k {
		results = results[:k]
	}
	return results, nil
}

// postFilterByMeta filters HNSW results by checking metadata in vec0.
func (vdb *VecDB) postFilterByMeta(results []SearchResult, filters SearchFilters) []SearchResult {
	var filtered []SearchResult
	for _, r := range results {
		var lang, cat sql.NullString
		err := vdb.db.QueryRow(
			"SELECT language, category FROM vec_documents WHERE document_id = ?", r.ID,
		).Scan(&lang, &cat)
		if err != nil {
			continue
		}
		if filters.Language != "" && lang.String != filters.Language {
			continue
		}
		if filters.Category != "" && cat.String != filters.Category {
			continue
		}
		filtered = append(filtered, r)
	}
	return filtered
}

// searchKNNFlat performs an exact KNN search via sqlite-vec with inline metadata filters.
func (vdb *VecDB) searchKNNFlat(query []byte, k int, maxDistance float64, normalize bool, filters SearchFilters) ([]SearchResult, error) {
	// Build query with optional WHERE clauses for metadata pre-filtering.
	q := `SELECT document_id, distance FROM vec_documents WHERE embedding MATCH ? AND k = ?`
	args := []any{query, k}

	if filters.Language != "" {
		q += " AND language = ?"
		args = append(args, filters.Language)
	}
	if filters.Category != "" {
		q += " AND category = ?"
		args = append(args, filters.Category)
	}

	rows, err := vdb.db.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []SearchResult
	for rows.Next() {
		var r SearchResult
		if err := rows.Scan(&r.ID, &r.Distance); err != nil {
			continue
		}
		if maxDistance > 0 && r.Distance > maxDistance {
			continue
		}
		if normalize {
			if vdb.metric == "cosine" {
				r.Similarity = 1.0 - r.Distance
			} else {
				r.Similarity = 1.0 / (1.0 + r.Distance)
			}
		}
		results = append(results, r)
	}
	return results, rows.Err()
}

// HybridResult extends SearchResult with BM25 and combined scoring.
type HybridResult struct {
	SearchResult
	BM25Score     float64 `json:"bm25_score,omitempty"`
	CombinedScore float64 `json:"combined_score"`
	MatchSource   string  `json:"match_source"` // "vector", "text", or "both"
}

// SearchHybrid performs a combined vector KNN + FTS5 BM25 search.
func (vdb *VecDB) SearchHybrid(query []byte, textQuery string, k int, vecWeight, textWeight float64, filters SearchFilters) ([]HybridResult, error) {
	if vecWeight == 0 && textWeight == 0 {
		vecWeight, textWeight = 0.7, 0.3
	}

	fetchK := k * 2

	// 1. Vector search.
	vecResults, err := vdb.SearchKNN(query, fetchK, 0, true, filters)
	if err != nil {
		return nil, fmt.Errorf("hybrid vector search: %w", err)
	}

	// 2. FTS5 text search.
	type ftsHit struct {
		DocID string
		Rank  float64
	}
	var ftsResults []ftsHit
	if vdb.fts && textQuery != "" {
		rows, err := vdb.db.Query(
			`SELECT document_id, rank FROM fts_documents WHERE fts_documents MATCH ? ORDER BY rank LIMIT ?`,
			textQuery, fetchK,
		)
		if err != nil {
			return nil, fmt.Errorf("hybrid fts search: %w", err)
		}
		defer rows.Close()
		for rows.Next() {
			var h ftsHit
			if err := rows.Scan(&h.DocID, &h.Rank); err != nil {
				continue
			}
			ftsResults = append(ftsResults, h)
		}
	}

	// 3. Merge into a map keyed by document_id.
	type merged struct {
		vecSim  float64
		bm25    float64
		dist    float64
		hasVec  bool
		hasText bool
	}
	m := make(map[string]*merged)

	for _, r := range vecResults {
		m[r.ID] = &merged{vecSim: r.Similarity, dist: r.Distance, hasVec: true}
	}

	// Normalize BM25 scores to 0-1 (FTS5 rank is negative, lower = better).
	var minRank, maxRank float64
	if len(ftsResults) > 0 {
		minRank, maxRank = ftsResults[0].Rank, ftsResults[0].Rank
		for _, h := range ftsResults[1:] {
			if h.Rank < minRank {
				minRank = h.Rank
			}
			if h.Rank > maxRank {
				maxRank = h.Rank
			}
		}
	}
	normBM25 := func(rank float64) float64 {
		if maxRank == minRank {
			return 1.0
		}
		// FTS5 rank: more negative = better match. Normalize so best = 1.0.
		return (maxRank - rank) / (maxRank - minRank)
	}

	for _, h := range ftsResults {
		if e, ok := m[h.DocID]; ok {
			e.bm25 = normBM25(h.Rank)
			e.hasText = true
		} else {
			m[h.DocID] = &merged{bm25: normBM25(h.Rank), hasText: true}
		}
	}

	// 4. Compute combined scores and sort.
	results := make([]HybridResult, 0, len(m))
	for id, e := range m {
		source := "vector"
		if e.hasVec && e.hasText {
			source = "both"
		} else if e.hasText {
			source = "text"
		}
		combined := vecWeight*e.vecSim + textWeight*e.bm25
		results = append(results, HybridResult{
			SearchResult: SearchResult{
				ID:         id,
				Distance:   e.dist,
				Similarity: e.vecSim,
			},
			BM25Score:     e.bm25,
			CombinedScore: combined,
			MatchSource:   source,
		})
	}

	// Sort by combined score descending.
	sortHybridResults(results)

	if len(results) > k {
		results = results[:k]
	}
	return results, nil
}

// sortHybridResults sorts by CombinedScore descending.
func sortHybridResults(r []HybridResult) {
	for i := 1; i < len(r); i++ {
		for j := i; j > 0 && r[j].CombinedScore > r[j-1].CombinedScore; j-- {
			r[j], r[j-1] = r[j-1], r[j]
		}
	}
}

// RepopulateFTS backfills the fts_documents table for any vec_documents rows
// that are missing from FTS5. It fetches content from PocketBase via the
// provided lookup function.
func (vdb *VecDB) RepopulateFTS(lookupContent func(docID string) (string, error)) error {
	if !vdb.fts {
		return nil
	}

	// Find vec_documents IDs that are NOT in fts_documents.
	rows, err := vdb.db.Query(`
		SELECT v.document_id FROM vec_documents v
		WHERE v.document_id NOT IN (SELECT document_id FROM fts_documents)
	`)
	if err != nil {
		return fmt.Errorf("query missing fts docs: %w", err)
	}
	defer rows.Close()

	var missing []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err == nil {
			missing = append(missing, id)
		}
	}
	if len(missing) == 0 {
		return nil
	}

	log.Printf("[vectordb] backfilling %d documents into FTS5", len(missing))
	tx, err := vdb.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	var filled int
	for _, id := range missing {
		content, err := lookupContent(id)
		if err != nil || content == "" {
			continue
		}
		if _, err := tx.Exec(
			"INSERT INTO fts_documents(document_id, content) VALUES (?, ?)",
			id, content,
		); err != nil {
			log.Printf("[vectordb] fts backfill %q: %v", id, err)
			continue
		}
		filled++
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit fts backfill: %w", err)
	}
	log.Printf("[vectordb] backfilled %d/%d documents into FTS5", filled, len(missing))
	return nil
}

// UpdateMetadata updates the language and category columns in vec0 for a document.
func (vdb *VecDB) UpdateMetadata(docID, language, category string) error {
	_, err := vdb.db.Exec(
		"UPDATE vec_documents SET language = ?, category = ? WHERE document_id = ?",
		language, category, docID,
	)
	return err
}

// Close closes the underlying database connection.
func (vdb *VecDB) Close() error {
	return vdb.db.Close()
}
