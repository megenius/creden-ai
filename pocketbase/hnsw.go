package main

import (
	"database/sql"
	"encoding/binary"
	"fmt"
	"log"
	"math"
	"os"
	"sync"

	"github.com/TFMV/hnsw"
)

// HNSWOpts holds tuning parameters for the HNSW graph.
type HNSWOpts struct {
	// M is the maximum number of neighbors per node. Default 16.
	M int
	// EfConstruction is the search width during index construction. Default 200.
	EfConstruction int
	// EfSearch is the search width during queries. Default 50.
	EfSearch int
}

// defaultHNSWOpts returns sensible defaults.
func defaultHNSWOpts() HNSWOpts {
	return HNSWOpts{M: 16, EfConstruction: 200, EfSearch: 50}
}

// HNSWIndex wraps a TFMV/hnsw SavedGraph for approximate nearest-neighbor
// search, keyed by document_id strings.
type HNSWIndex struct {
	mu        sync.RWMutex
	graph     *hnsw.SavedGraph[string]
	dimension int
	metric    string
	path      string
	opts      HNSWOpts
}

// distanceFunc returns the HNSW DistanceFunc appropriate for the given metric.
// "cosine" → cosine distance (1 - cosine_similarity)
// anything else → Euclidean distance
func distanceFunc(metric string) hnsw.DistanceFunc {
	if metric == "cosine" {
		return hnsw.CosineDistance
	}
	return hnsw.EuclideanDistance
}

// distanceFuncName returns the registered name for the distance function.
// TFMV/hnsw registers "cosine" and "euclidean" by default.
func distanceFuncName(metric string) string {
	if metric == "cosine" {
		return "cosine"
	}
	return "euclidean"
}

// buildGraph constructs a new hnsw.Graph with the configured parameters.
func buildGraph(metric string, opts HNSWOpts) *hnsw.Graph[string] {
	g := hnsw.NewGraph[string]()
	g.M = opts.M
	g.EfSearch = opts.EfSearch
	g.Distance = distanceFunc(metric)
	// Ml controls the level-promotion probability; 0.25 is the library default.
	g.Ml = 0.25
	return g
}

// NewHNSWIndex creates an empty in-memory HNSW index backed by the given
// file path (the file is only written when Save is called).
func NewHNSWIndex(dim int, metric string, path string, opts HNSWOpts) *HNSWIndex {
	if opts.M == 0 {
		opts = defaultHNSWOpts()
	}

	g := buildGraph(metric, opts)
	sg := &hnsw.SavedGraph[string]{Graph: g, Path: path}

	return &HNSWIndex{
		graph:     sg,
		dimension: dim,
		metric:    metric,
		path:      path,
		opts:      opts,
	}
}

// LoadHNSWIndex loads an HNSW index from disk.
// Returns an error if the file does not exist.
func LoadHNSWIndex(path string, dim int, metric string, opts HNSWOpts) (*HNSWIndex, error) {
	if opts.M == 0 {
		opts = defaultHNSWOpts()
	}

	// Require that the file already exists.
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return nil, fmt.Errorf("hnsw index file not found: %s", path)
	}

	sg, err := hnsw.LoadSavedGraph[string](path)
	if err != nil {
		return nil, fmt.Errorf("load hnsw index: %w", err)
	}

	// Apply runtime opts that may differ from what was serialised.
	sg.Graph.EfSearch = opts.EfSearch

	return &HNSWIndex{
		graph:     sg,
		dimension: dim,
		metric:    metric,
		path:      path,
		opts:      opts,
	}, nil
}

// Save persists the index to disk atomically.
func (h *HNSWIndex) Save() error {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return h.graph.Save()
}

// Insert adds or replaces a vector in the index.
func (h *HNSWIndex) Insert(id string, embedding []float32) error {
	if len(embedding) != h.dimension {
		return fmt.Errorf("dimension mismatch: want %d, got %d", h.dimension, len(embedding))
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.graph.Add(hnsw.MakeNode(id, embedding))
}

// Delete removes a vector from the index.
// Returns an error if the key was not found.
func (h *HNSWIndex) Delete(id string) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	if !h.graph.Delete(id) {
		return fmt.Errorf("hnsw: document %q not found", id)
	}
	return nil
}

// Search performs an approximate k-nearest-neighbor search and returns
// results ordered by ascending distance.
func (h *HNSWIndex) Search(query []float32, k int) ([]SearchResult, error) {
	if len(query) != h.dimension {
		return nil, fmt.Errorf("dimension mismatch: want %d, got %d", h.dimension, len(query))
	}

	h.mu.RLock()
	defer h.mu.RUnlock()

	if h.graph.Len() == 0 {
		return nil, nil
	}

	nodes, err := h.graph.Search(query, k)
	if err != nil {
		return nil, fmt.Errorf("hnsw search: %w", err)
	}

	dfn := distanceFunc(h.metric)
	results := make([]SearchResult, 0, len(nodes))
	for _, n := range nodes {
		dist := float64(dfn(query, n.Value))
		var sim float64
		if h.metric == "cosine" {
			// cosine distance is in [0,2]; similarity = 1 - distance
			sim = 1.0 - dist
		} else {
			sim = 1.0 / (1.0 + dist)
		}
		results = append(results, SearchResult{
			ID:         n.Key,
			Distance:   dist,
			Similarity: sim,
		})
	}
	return results, nil
}

// Len returns the number of vectors currently in the index.
func (h *HNSWIndex) Len() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return h.graph.Len()
}

// RebuildFromVecDB scans all rows in the vec_documents table and bulk-inserts
// them into a freshly initialised graph, then replaces the in-memory graph.
// This is used on startup (when no saved index exists) and by the
// POST /api/index/rebuild endpoint.
func (h *HNSWIndex) RebuildFromVecDB(db *sql.DB, dim int) error {
	rows, err := db.Query("SELECT document_id, embedding FROM vec_documents")
	if err != nil {
		return fmt.Errorf("query vec_documents: %w", err)
	}
	defer rows.Close()

	// Build into a fresh graph so the existing one stays live while we work.
	newGraph := buildGraph(h.metric, h.opts)

	var count int
	for rows.Next() {
		var docID string
		var blob []byte
		if err := rows.Scan(&docID, &blob); err != nil {
			log.Printf("[hnsw] scan error, skipping row: %v", err)
			continue
		}

		vec, err := blobToFloat32Slice(blob)
		if err != nil {
			log.Printf("[hnsw] decode embedding for %q: %v", docID, err)
			continue
		}
		if len(vec) != dim {
			log.Printf("[hnsw] dimension mismatch for %q: want %d, got %d", docID, dim, len(vec))
			continue
		}

		if err := newGraph.Add(hnsw.MakeNode(docID, vec)); err != nil {
			log.Printf("[hnsw] add %q: %v", docID, err)
			continue
		}
		count++
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("iterate vec_documents: %w", err)
	}

	h.mu.Lock()
	h.graph.Graph = newGraph
	h.mu.Unlock()

	log.Printf("[hnsw] rebuilt index with %d vectors", count)
	return nil
}

// blobToFloat32Slice converts a little-endian float32 byte blob (as stored by
// sqlite-vec) into a []float32.
func blobToFloat32Slice(blob []byte) ([]float32, error) {
	if len(blob)%4 != 0 {
		return nil, fmt.Errorf("blob length %d is not a multiple of 4", len(blob))
	}
	n := len(blob) / 4
	out := make([]float32, n)
	for i := range out {
		bits := binary.LittleEndian.Uint32(blob[i*4 : i*4+4])
		out[i] = math.Float32frombits(bits)
	}
	return out, nil
}
