package main

import (
	"encoding/binary"
	"fmt"
	"log"
	"math"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"syscall"

	sqlite_vec "github.com/asg017/sqlite-vec-go-bindings/cgo"
	_ "github.com/mattn/go-sqlite3"
	"github.com/pocketbase/pocketbase"
	"github.com/pocketbase/pocketbase/core"
)

// vecDimension is the embedding size. Override with VEC_DIMENSION env var.
// Default 384 matches models like all-MiniLM-L6-v2.
var vecDimension = 384

// vecMetric is the distance metric. Override with VEC_METRIC env var.
// "cosine" or "l2" (default: cosine).
var vecMetric = "cosine"

// vecIndex controls which index backend is used.
// "flat" (default) uses sqlite-vec; "hnsw" uses in-memory HNSW.
var vecIndex = "flat"

// hnswOpts are parsed from HNSW_M, HNSW_EF_CONSTRUCTION, HNSW_EF_SEARCH env vars.
var hnswOpts HNSWOpts

// vecdb is the global vector database instance.
var vecdb *VecDB

// embedder is the optional auto-embedding provider.
var embedder EmbeddingProvider

// embedCfg holds embedding configuration parsed from env vars.
var embedCfg EmbedConfig

func init() {
	sqlite_vec.Auto()

	if d := os.Getenv("VEC_DIMENSION"); d != "" {
		if n, err := strconv.Atoi(d); err == nil && n > 0 {
			vecDimension = n
		}
	}
	if m := os.Getenv("VEC_METRIC"); m == "l2" || m == "cosine" {
		vecMetric = m
	}
	if idx := os.Getenv("VEC_INDEX"); idx == "hnsw" {
		vecIndex = "hnsw"
	}

	hnswOpts = defaultHNSWOpts()
	if v, err := strconv.Atoi(os.Getenv("HNSW_M")); err == nil && v > 0 {
		hnswOpts.M = v
	}
	if v, err := strconv.Atoi(os.Getenv("HNSW_EF_CONSTRUCTION")); err == nil && v > 0 {
		hnswOpts.EfConstruction = v
	}
	if v, err := strconv.Atoi(os.Getenv("HNSW_EF_SEARCH")); err == nil && v > 0 {
		hnswOpts.EfSearch = v
	}

	embedCfg = EmbedConfig{
		Provider:  os.Getenv("EMBED_PROVIDER"),
		Model:     os.Getenv("EMBED_MODEL"),
		APIURL:    os.Getenv("EMBED_API_URL"),
		APIKey:    os.Getenv("OPENAI_API_KEY"),
		Dimension: vecDimension,
	}
}

func main() {
	app := pocketbase.New()

	app.OnServe().BindFunc(func(se *core.ServeEvent) error {
		// --- Vector DB ---
		var err error
		vecdb, err = OpenVecDB(se.App.DataDir(), vecDimension, vecMetric)
		if err != nil {
			return fmt.Errorf("vector db: %w", err)
		}

		// --- HNSW index (optional) ---
		if vecIndex == "hnsw" {
			hnswPath := filepath.Join(se.App.DataDir(), "hnsw.idx")
			idx, loadErr := LoadHNSWIndex(hnswPath, vecDimension, vecMetric, hnswOpts)
			if loadErr != nil {
				// Index file absent or corrupt — rebuild from vec0.
				log.Printf("[hnsw] no saved index, rebuilding from vec_documents (%v)", loadErr)
				idx = NewHNSWIndex(vecDimension, vecMetric, hnswPath, hnswOpts)
				if rebuildErr := idx.RebuildFromVecDB(vecdb.db, vecDimension); rebuildErr != nil {
					return fmt.Errorf("hnsw rebuild: %w", rebuildErr)
				}
				if saveErr := idx.Save(); saveErr != nil {
					log.Printf("[hnsw] warning: could not persist index: %v", saveErr)
				}
			} else {
				log.Printf("[hnsw] loaded index with %d vectors from %s", idx.Len(), hnswPath)
			}
			vecdb.SetHNSW(idx)

			// Save the HNSW index on SIGTERM / SIGINT.
			go func() {
				ch := make(chan os.Signal, 1)
				signal.Notify(ch, syscall.SIGTERM, syscall.SIGINT)
				<-ch
				log.Println("[hnsw] saving index before exit...")
				if err := idx.Save(); err != nil {
					log.Printf("[hnsw] save error: %v", err)
				}
			}()
		}

		// --- Embedding provider (optional) ---
		if embedCfg.Provider != "" && embedCfg.Provider != "none" {
			var embedErr error
			embedder, embedErr = NewEmbeddingProvider(embedCfg)
			if embedErr != nil {
				return fmt.Errorf("embedding provider: %w", embedErr)
			}
			log.Printf("[vectordb] embedding provider=%s model=%s", embedCfg.Provider, embedCfg.Model)
		}

		// --- PocketBase collection ---
		if err := ensureDocumentsCollection(se.App); err != nil {
			return fmt.Errorf("collection: %w", err)
		}

		// --- Backfill FTS5 + metadata for existing documents ---
		if err := vecdb.RepopulateFTS(func(docID string) (string, error) {
			rec, err := se.App.FindRecordById("documents", docID)
			if err != nil {
				return "", err
			}
			// Also backfill metadata columns in vec0 while we're here.
			lang := rec.GetString("language")
			cat := rec.GetString("category")
			if lang != "" || cat != "" {
				_ = vecdb.UpdateMetadata(docID, lang, cat)
			}
			return rec.GetString("content"), nil
		}); err != nil {
			log.Printf("[vectordb] FTS backfill warning: %v", err)
		}

		// --- API routes ---
		se.Router.POST("/api/add-document", handleAddDocument(se.App))
		se.Router.POST("/api/search", handleSearch(se.App))
		se.Router.POST("/api/search/hybrid", handleHybridSearch(se.App))
		se.Router.DELETE("/api/documents/{id}", handleDeleteDocument(se.App))
		se.Router.POST("/api/index/rebuild", handleRebuildIndex(se.App))
		se.Router.POST("/api/documents/batch", handleBatchInsert(se.App))
		se.Router.PUT("/api/documents/{id}", handleUpdateDocument(se.App))
		se.Router.GET("/api/stats", handleStats(se.App))

		return se.Next()
	})

	if err := app.Start(); err != nil {
		log.Fatal(err)
	}
}

// ensureDocumentsCollection creates the "documents" collection in PocketBase
// if it doesn't already exist.
func ensureDocumentsCollection(app core.App) error {
	col, err := app.FindCollectionByNameOrId("documents")
	if err != nil {
		// Create new collection with all fields.
		col = core.NewBaseCollection("documents")
		col.Fields.Add(&core.TextField{Name: "content", Required: true})
		col.Fields.Add(&core.TextField{Name: "language"})
		col.Fields.Add(&core.TextField{Name: "category"})
		col.Fields.Add(&core.JSONField{Name: "tags", MaxSize: 2000})
		return app.Save(col)
	}

	// Ensure metadata fields exist (migration for existing DBs).
	changed := false
	if col.Fields.GetByName("language") == nil {
		col.Fields.Add(&core.TextField{Name: "language"})
		changed = true
	}
	if col.Fields.GetByName("category") == nil {
		col.Fields.Add(&core.TextField{Name: "category"})
		changed = true
	}
	if col.Fields.GetByName("tags") == nil {
		col.Fields.Add(&core.JSONField{Name: "tags", MaxSize: 2000})
		changed = true
	}
	if changed {
		return app.Save(col)
	}
	return nil
}

// float64sToVecBlob converts []float64 into the little-endian float32 byte
// slice that sqlite-vec expects.
func float64sToVecBlob(v []float64) []byte {
	buf := make([]byte, len(v)*4)
	for i, f := range v {
		binary.LittleEndian.PutUint32(buf[i*4:], math.Float32bits(float32(f)))
	}
	return buf
}
