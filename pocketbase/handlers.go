package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"

	"github.com/pocketbase/pocketbase/core"
)

// ---------------------------------------------------------------------------
// Request / response types
// ---------------------------------------------------------------------------

type addDocumentRequest struct {
	Content   string    `json:"content"`
	Embedding []float64 `json:"embedding"`
	Language  string    `json:"language,omitempty"`
	Category  string    `json:"category,omitempty"`
	Tags      []string  `json:"tags,omitempty"`
}

type searchRequest struct {
	Query       string        `json:"query,omitempty"`    // text query (auto-embedded)
	Embedding   []float64     `json:"embedding"`
	Limit       int           `json:"limit"`
	Offset      int           `json:"offset"`
	MaxDistance  float64       `json:"max_distance"`
	Normalize   bool          `json:"normalize"`
	Filters     SearchFilters `json:"filters"`
}

type hybridSearchRequest struct {
	Query     string        `json:"query"`
	Embedding []float64     `json:"embedding"`
	Limit     int           `json:"limit"`
	Weights   struct {
		Vector float64 `json:"vector"`
		Text   float64 `json:"text"`
	} `json:"weights"`
	Filters SearchFilters `json:"filters"`
}

type searchHit struct {
	ID         string  `json:"id"`
	Content    string  `json:"content"`
	Distance   float64 `json:"distance"`
	Similarity float64 `json:"similarity,omitempty"`
}

type hybridSearchHit struct {
	ID            string  `json:"id"`
	Content       string  `json:"content"`
	Distance      float64 `json:"distance"`
	Similarity    float64 `json:"similarity,omitempty"`
	BM25Score     float64 `json:"bm25_score,omitempty"`
	CombinedScore float64 `json:"combined_score"`
	MatchSource   string  `json:"match_source"`
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

// handleAddDocument inserts content into PocketBase and the embedding into
// the vec0 virtual table, linked by the PocketBase record ID.
func handleAddDocument(app core.App) func(*core.RequestEvent) error {
	return func(e *core.RequestEvent) error {
		var req addDocumentRequest
		if err := e.BindBody(&req); err != nil {
			return e.BadRequestError("Invalid JSON.", err)
		}
		if req.Content == "" {
			return e.BadRequestError("'content' is required.", nil)
		}

		// Auto-embed if no embedding provided and embedder is configured.
		if len(req.Embedding) == 0 {
			if embedder == nil {
				return e.BadRequestError("'embedding' is required when auto-embedding is not configured.", nil)
			}
			ctx, cancel := context.WithTimeout(e.Request.Context(), 30*time.Second)
			defer cancel()
			emb, err := embedder.Embed(ctx, req.Content)
			if err != nil {
				return e.InternalServerError("Auto-embedding failed.", err)
			}
			req.Embedding = emb
		}

		if len(req.Embedding) != vecDimension {
			return e.BadRequestError(
				fmt.Sprintf("'embedding' must have %d dimensions, got %d.",
					vecDimension, len(req.Embedding)), nil)
		}

		// 1. Save document to PocketBase.
		col, err := app.FindCollectionByNameOrId("documents")
		if err != nil {
			return e.InternalServerError("Collection not found.", err)
		}
		record := core.NewRecord(col)
		record.Set("content", req.Content)
		if req.Language != "" {
			record.Set("language", req.Language)
		}
		if req.Category != "" {
			record.Set("category", req.Category)
		}
		if len(req.Tags) > 0 {
			record.Set("tags", req.Tags)
		}
		if err := app.Save(record); err != nil {
			return e.InternalServerError("Failed to save document.", err)
		}

		// 2. Save embedding to vec table + FTS5.
		blob := float64sToVecBlob(req.Embedding)
		meta := DocMeta{Language: req.Language, Category: req.Category}
		if err := vecdb.InsertEmbedding(record.Id, blob, req.Content, meta); err != nil {
			_ = app.Delete(record) // best-effort rollback
			return e.InternalServerError("Failed to save embedding.", err)
		}

		return e.JSON(http.StatusOK, map[string]any{
			"id":      record.Id,
			"content": req.Content,
		})
	}
}

// handleSearch performs a KNN similarity search.
func handleSearch(app core.App) func(*core.RequestEvent) error {
	return func(e *core.RequestEvent) error {
		var req searchRequest
		if err := e.BindBody(&req); err != nil {
			return e.BadRequestError("Invalid JSON.", err)
		}

		// Auto-embed text query if no embedding provided.
		if len(req.Embedding) == 0 && req.Query != "" {
			if embedder == nil {
				return e.BadRequestError("'embedding' is required when auto-embedding is not configured.", nil)
			}
			ctx, cancel := context.WithTimeout(e.Request.Context(), 30*time.Second)
			defer cancel()
			emb, err := embedder.Embed(ctx, req.Query)
			if err != nil {
				return e.InternalServerError("Auto-embedding query failed.", err)
			}
			req.Embedding = emb
		}

		if len(req.Embedding) == 0 {
			return e.BadRequestError("'embedding' or 'query' (with auto-embedding configured) is required.", nil)
		}
		if len(req.Embedding) != vecDimension {
			return e.BadRequestError(
				fmt.Sprintf("'embedding' must have %d dimensions, got %d.",
					vecDimension, len(req.Embedding)), nil)
		}
		if req.Limit <= 0 || req.Limit > 100 {
			req.Limit = 5
		}

		// For pagination, fetch extra results to account for post-filtering.
		fetchLimit := req.Limit + req.Offset
		if len(req.Filters.Tags) > 0 {
			fetchLimit = fetchLimit * 3 // oversample for tag post-filtering
		}

		blob := float64sToVecBlob(req.Embedding)
		results, err := vecdb.SearchKNN(blob, fetchLimit, req.MaxDistance, req.Normalize, req.Filters)
		if err != nil {
			return e.InternalServerError("Vector search failed.", err)
		}

		// Enrich with content from PocketBase and post-filter tags.
		allHits := make([]searchHit, 0, len(results))
		for _, r := range results {
			rec, err := app.FindRecordById("documents", r.ID)
			if err != nil {
				continue
			}
			if len(req.Filters.Tags) > 0 {
				recTags := rec.GetString("tags")
				if !tagsMatch(recTags, req.Filters.Tags) {
					continue
				}
			}
			allHits = append(allHits, searchHit{
				ID:         r.ID,
				Content:    rec.GetString("content"),
				Distance:   r.Distance,
				Similarity: r.Similarity,
			})
		}

		// Apply pagination offset.
		hits := allHits
		if req.Offset > 0 && req.Offset < len(hits) {
			hits = hits[req.Offset:]
		} else if req.Offset >= len(hits) {
			hits = nil
		}
		if len(hits) > req.Limit {
			hits = hits[:req.Limit]
		}

		return e.JSON(http.StatusOK, map[string]any{
			"results": hits,
		})
	}
}

// handleRebuildIndex triggers a full HNSW index rebuild from the vec_documents
// table and persists the result to disk. Returns a 409 when HNSW is not
// enabled (VEC_INDEX != "hnsw").
func handleRebuildIndex(app core.App) func(*core.RequestEvent) error {
	return func(e *core.RequestEvent) error {
		if vecdb.hnsw == nil {
			return e.JSON(409, map[string]any{
				"error": "HNSW index is not enabled (set VEC_INDEX=hnsw to enable).",
			})
		}

		if err := vecdb.hnsw.RebuildFromVecDB(vecdb.db, vecDimension); err != nil {
			return e.InternalServerError("Rebuild failed.", err)
		}

		if err := vecdb.hnsw.Save(); err != nil {
			// Non-fatal: the in-memory index is up to date.
			return e.JSON(200, map[string]any{
				"ok":      true,
				"vectors": vecdb.hnsw.Len(),
				"warning": fmt.Sprintf("index rebuilt but could not be persisted: %v", err),
			})
		}

		return e.JSON(200, map[string]any{
			"ok":      true,
			"vectors": vecdb.hnsw.Len(),
		})
	}
}

// handleDeleteDocument removes a document from PocketBase and vec0.
func handleDeleteDocument(app core.App) func(*core.RequestEvent) error {
	return func(e *core.RequestEvent) error {
		id := e.Request.PathValue("id")
		if id == "" {
			return e.BadRequestError("Document ID is required.", nil)
		}

		// Delete from vec0.
		if err := vecdb.DeleteEmbedding(id); err != nil {
			return e.InternalServerError("Failed to delete embedding.", err)
		}

		// Delete from PocketBase.
		rec, err := app.FindRecordById("documents", id)
		if err != nil {
			// Embedding deleted, but PocketBase record not found — still OK.
			return e.JSON(http.StatusOK, map[string]any{
				"deleted": true,
				"id":      id,
			})
		}
		if err := app.Delete(rec); err != nil {
			return e.InternalServerError("Failed to delete document.", err)
		}

		return e.JSON(http.StatusOK, map[string]any{
			"deleted": true,
			"id":      id,
		})
	}
}

// handleHybridSearch performs a combined vector + full-text search.
func handleHybridSearch(app core.App) func(*core.RequestEvent) error {
	return func(e *core.RequestEvent) error {
		var req hybridSearchRequest
		if err := e.BindBody(&req); err != nil {
			return e.BadRequestError("Invalid JSON.", err)
		}
		if len(req.Embedding) == 0 && req.Query == "" {
			return e.BadRequestError("'embedding' or 'query' is required.", nil)
		}

		// Auto-embed query text for the vector component of hybrid search.
		if len(req.Embedding) == 0 && req.Query != "" && embedder != nil {
			ctx, cancel := context.WithTimeout(e.Request.Context(), 30*time.Second)
			defer cancel()
			emb, err := embedder.Embed(ctx, req.Query)
			if err != nil {
				return e.InternalServerError("Auto-embedding query failed.", err)
			}
			req.Embedding = emb
		}

		if len(req.Embedding) > 0 && len(req.Embedding) != vecDimension {
			return e.BadRequestError(
				fmt.Sprintf("'embedding' must have %d dimensions, got %d.",
					vecDimension, len(req.Embedding)), nil)
		}
		if req.Limit <= 0 || req.Limit > 100 {
			req.Limit = 5
		}
		vecWeight := req.Weights.Vector
		textWeight := req.Weights.Text
		if vecWeight == 0 && textWeight == 0 {
			vecWeight, textWeight = 0.7, 0.3
		}

		var blob []byte
		if len(req.Embedding) > 0 {
			blob = float64sToVecBlob(req.Embedding)
		}

		results, err := vecdb.SearchHybrid(blob, req.Query, req.Limit, vecWeight, textWeight, req.Filters)
		if err != nil {
			return e.InternalServerError("Hybrid search failed.", err)
		}

		hits := make([]hybridSearchHit, 0, len(results))
		for _, r := range results {
			rec, err := app.FindRecordById("documents", r.ID)
			if err != nil {
				continue
			}
			// Tag filtering.
			if len(req.Filters.Tags) > 0 {
				recTags := rec.GetString("tags")
				if !tagsMatch(recTags, req.Filters.Tags) {
					continue
				}
			}
			hits = append(hits, hybridSearchHit{
				ID:            r.ID,
				Content:       rec.GetString("content"),
				Distance:      r.Distance,
				Similarity:    r.Similarity,
				BM25Score:     r.BM25Score,
				CombinedScore: r.CombinedScore,
				MatchSource:   r.MatchSource,
			})
		}

		return e.JSON(http.StatusOK, map[string]any{
			"results": hits,
		})
	}
}

// tagsMatch checks if all required tags exist in the record's JSON tags array.
func tagsMatch(recTagsJSON string, required []string) bool {
	if recTagsJSON == "" {
		return false
	}
	var tags []string
	if err := json.Unmarshal([]byte(recTagsJSON), &tags); err != nil {
		return false
	}
	tagSet := make(map[string]struct{}, len(tags))
	for _, t := range tags {
		tagSet[t] = struct{}{}
	}
	for _, r := range required {
		if _, ok := tagSet[r]; !ok {
			return false
		}
	}
	return true
}

// ---------------------------------------------------------------------------
// Batch insert
// ---------------------------------------------------------------------------

type batchInsertRequest struct {
	Documents []addDocumentRequest `json:"documents"`
}

func handleBatchInsert(app core.App) func(*core.RequestEvent) error {
	return func(e *core.RequestEvent) error {
		var req batchInsertRequest
		if err := e.BindBody(&req); err != nil {
			return e.BadRequestError("Invalid JSON.", err)
		}
		if len(req.Documents) == 0 {
			return e.BadRequestError("'documents' array is required.", nil)
		}
		if len(req.Documents) > 100 {
			return e.BadRequestError("Maximum 100 documents per batch.", nil)
		}

		col, err := app.FindCollectionByNameOrId("documents")
		if err != nil {
			return e.InternalServerError("Collection not found.", err)
		}

		// Auto-embed all documents that lack embeddings.
		if embedder != nil {
			var toEmbed []string
			var indices []int
			for i, doc := range req.Documents {
				if len(doc.Embedding) == 0 && doc.Content != "" {
					toEmbed = append(toEmbed, doc.Content)
					indices = append(indices, i)
				}
			}
			if len(toEmbed) > 0 {
				ctx, cancel := context.WithTimeout(e.Request.Context(), 60*time.Second)
				defer cancel()
				embeddings, err := embedder.EmbedBatch(ctx, toEmbed)
				if err != nil {
					return e.InternalServerError("Batch auto-embedding failed.", err)
				}
				for j, idx := range indices {
					req.Documents[idx].Embedding = embeddings[j]
				}
			}
		}

		var ids []string
		var errors []string
		for i, doc := range req.Documents {
			if doc.Content == "" {
				errors = append(errors, fmt.Sprintf("doc[%d]: content is required", i))
				continue
			}
			if len(doc.Embedding) != vecDimension {
				errors = append(errors, fmt.Sprintf("doc[%d]: embedding dimension %d != %d", i, len(doc.Embedding), vecDimension))
				continue
			}

			record := core.NewRecord(col)
			record.Set("content", doc.Content)
			if doc.Language != "" {
				record.Set("language", doc.Language)
			}
			if doc.Category != "" {
				record.Set("category", doc.Category)
			}
			if len(doc.Tags) > 0 {
				record.Set("tags", doc.Tags)
			}
			if err := app.Save(record); err != nil {
				errors = append(errors, fmt.Sprintf("doc[%d]: save failed: %v", i, err))
				continue
			}

			blob := float64sToVecBlob(doc.Embedding)
			meta := DocMeta{Language: doc.Language, Category: doc.Category}
			if err := vecdb.InsertEmbedding(record.Id, blob, doc.Content, meta); err != nil {
				_ = app.Delete(record)
				errors = append(errors, fmt.Sprintf("doc[%d]: embedding insert failed: %v", i, err))
				continue
			}

			// Insert into HNSW if enabled.
			if vecdb.hnsw != nil {
				vec := make([]float32, len(doc.Embedding))
				for j, v := range doc.Embedding {
					vec[j] = float32(v)
				}
				if err := vecdb.hnsw.Insert(record.Id, vec); err != nil {
					log.Printf("[hnsw] batch insert %q failed: %v", record.Id, err)
				}
			}

			ids = append(ids, record.Id)
		}

		return e.JSON(http.StatusOK, map[string]any{
			"inserted": len(ids),
			"ids":      ids,
			"errors":   errors,
		})
	}
}

// ---------------------------------------------------------------------------
// Update document
// ---------------------------------------------------------------------------

type updateDocumentRequest struct {
	Content  string   `json:"content,omitempty"`
	Language string   `json:"language,omitempty"`
	Category string   `json:"category,omitempty"`
	Tags     []string `json:"tags,omitempty"`
}

func handleUpdateDocument(app core.App) func(*core.RequestEvent) error {
	return func(e *core.RequestEvent) error {
		id := e.Request.PathValue("id")
		if id == "" {
			return e.BadRequestError("Document ID is required.", nil)
		}

		var req updateDocumentRequest
		if err := e.BindBody(&req); err != nil {
			return e.BadRequestError("Invalid JSON.", err)
		}

		rec, err := app.FindRecordById("documents", id)
		if err != nil {
			return e.NotFoundError("Document not found.", err)
		}

		// Update PocketBase fields.
		if req.Content != "" {
			rec.Set("content", req.Content)
		}
		if req.Language != "" {
			rec.Set("language", req.Language)
		}
		if req.Category != "" {
			rec.Set("category", req.Category)
		}
		if len(req.Tags) > 0 {
			rec.Set("tags", req.Tags)
		}
		if err := app.Save(rec); err != nil {
			return e.InternalServerError("Failed to update document.", err)
		}

		// Re-embed if content changed and embedder is available.
		if req.Content != "" {
			// Generate new embedding BEFORE deleting old one to avoid data loss on failure.
			var embedding []float64
			if embedder != nil {
				ctx, cancel := context.WithTimeout(e.Request.Context(), 30*time.Second)
				defer cancel()
				emb, err := embedder.Embed(ctx, req.Content)
				if err != nil {
					return e.InternalServerError("Re-embedding failed.", err)
				}
				embedding = emb
			}

			if len(embedding) == vecDimension {
				// Now safe to delete old and insert new.
				_ = vecdb.DeleteEmbedding(id)
				if vecdb.hnsw != nil {
					_ = vecdb.hnsw.Delete(id)
				}

				blob := float64sToVecBlob(embedding)
				meta := DocMeta{
					Language: rec.GetString("language"),
					Category: rec.GetString("category"),
				}
				if err := vecdb.InsertEmbedding(id, blob, req.Content, meta); err != nil {
					return e.InternalServerError("Failed to update embedding.", err)
				}
				if vecdb.hnsw != nil {
					vec := make([]float32, len(embedding))
					for j, v := range embedding {
						vec[j] = float32(v)
					}
					if err := vecdb.hnsw.Insert(id, vec); err != nil {
						log.Printf("[hnsw] insert %q after update failed: %v", id, err)
					}
				}
			}
		} else {
			// Content didn't change but metadata might have — update vec0 metadata.
			lang := rec.GetString("language")
			cat := rec.GetString("category")
			_, _ = vecdb.db.Exec(
				"UPDATE vec_documents SET language = ?, category = ? WHERE document_id = ?",
				lang, cat, id,
			)
		}

		return e.JSON(http.StatusOK, map[string]any{
			"id":      id,
			"updated": true,
		})
	}
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------

func handleStats(app core.App) func(*core.RequestEvent) error {
	return func(e *core.RequestEvent) error {
		// Count documents.
		var docCount int
		_ = vecdb.db.QueryRow("SELECT COUNT(*) FROM vec_documents").Scan(&docCount)

		// Vec version.
		var vecVersion string
		_ = vecdb.db.QueryRow("SELECT vec_version()").Scan(&vecVersion)

		stats := map[string]any{
			"documents":     docCount,
			"vec_dimension": vecDimension,
			"vec_metric":    vecMetric,
			"vec_version":   vecVersion,
			"index_type":    vecIndex,
			"fts_enabled":   vecdb.fts,
		}

		if vecdb.hnsw != nil {
			stats["hnsw_nodes"] = vecdb.hnsw.Len()
			stats["hnsw_m"] = hnswOpts.M
			stats["hnsw_ef_search"] = hnswOpts.EfSearch
		}

		if embedder != nil {
			stats["embed_provider"] = embedCfg.Provider
			stats["embed_model"] = embedCfg.Model
		}

		return e.JSON(http.StatusOK, stats)
	}
}

