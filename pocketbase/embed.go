package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// EmbeddingProvider is the interface for text embedding services.
type EmbeddingProvider interface {
	// Embed returns a single embedding vector for the given text.
	Embed(ctx context.Context, text string) ([]float64, error)
	// EmbedBatch returns embedding vectors for a slice of texts.
	EmbedBatch(ctx context.Context, texts []string) ([][]float64, error)
	// Dimension returns the size of each embedding vector.
	Dimension() int
}

// EmbedConfig holds configuration for creating an EmbeddingProvider.
type EmbedConfig struct {
	// Provider is one of "openai", "ollama", or "none" / empty string.
	Provider string
	// Model is the embedding model name.
	Model string
	// APIURL is the base URL used by Ollama (ignored by OpenAI).
	APIURL string
	// APIKey is the API key used by OpenAI (ignored by Ollama).
	APIKey string
	// Dimension is the expected embedding dimension.
	Dimension int
}

// NewEmbeddingProvider returns an EmbeddingProvider for the given config.
// Returns nil, nil when Provider is "none" or empty, indicating that
// auto-embedding is disabled.
func NewEmbeddingProvider(cfg EmbedConfig) (EmbeddingProvider, error) {
	switch cfg.Provider {
	case "", "none":
		return nil, nil
	case "openai":
		return newOpenAIProvider(cfg)
	case "ollama":
		return newOllamaProvider(cfg)
	default:
		return nil, fmt.Errorf("embed: unknown provider %q (use \"openai\", \"ollama\", or \"none\")", cfg.Provider)
	}
}

// ---------------------------------------------------------------------------
// OpenAI provider
// ---------------------------------------------------------------------------

type openaiProvider struct {
	apiKey    string
	model     string
	dimension int
	client    *http.Client
}

func newOpenAIProvider(cfg EmbedConfig) (*openaiProvider, error) {
	if cfg.APIKey == "" {
		return nil, fmt.Errorf("embed: openai provider requires a non-empty APIKey (OPENAI_API_KEY)")
	}
	model := cfg.Model
	if model == "" {
		model = "text-embedding-3-small"
	}
	return &openaiProvider{
		apiKey:    cfg.APIKey,
		model:     model,
		dimension: cfg.Dimension,
		client:    &http.Client{Timeout: 30 * time.Second},
	}, nil
}

func (p *openaiProvider) Dimension() int { return p.dimension }

func (p *openaiProvider) Embed(ctx context.Context, text string) ([]float64, error) {
	results, err := p.EmbedBatch(ctx, []string{text})
	if err != nil {
		return nil, err
	}
	if len(results) == 0 {
		return nil, fmt.Errorf("embed: openai returned no embeddings")
	}
	return results[0], nil
}

// openaiEmbedRequest is the JSON body sent to the OpenAI embeddings endpoint.
type openaiEmbedRequest struct {
	Model string   `json:"model"`
	Input []string `json:"input"`
}

// openaiEmbedResponse is the JSON response from the OpenAI embeddings endpoint.
type openaiEmbedResponse struct {
	Data []struct {
		Embedding []float64 `json:"embedding"`
		Index     int       `json:"index"`
	} `json:"data"`
	Error *struct {
		Message string `json:"message"`
		Type    string `json:"type"`
	} `json:"error"`
}

func (p *openaiProvider) EmbedBatch(ctx context.Context, texts []string) ([][]float64, error) {
	if len(texts) == 0 {
		return nil, nil
	}

	reqBody := openaiEmbedRequest{
		Model: p.model,
		Input: texts,
	}
	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("embed: openai marshal request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		"https://api.openai.com/v1/embeddings", bytes.NewReader(bodyBytes))
	if err != nil {
		return nil, fmt.Errorf("embed: openai create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+p.apiKey)

	resp, err := p.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("embed: openai request failed: %w", err)
	}
	defer resp.Body.Close()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("embed: openai read response: %w", err)
	}

	var result openaiEmbedResponse
	if err := json.Unmarshal(respBytes, &result); err != nil {
		return nil, fmt.Errorf("embed: openai parse response: %w", err)
	}

	if result.Error != nil {
		return nil, fmt.Errorf("embed: openai API error (%s): %s", result.Error.Type, result.Error.Message)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("embed: openai unexpected status %d: %s", resp.StatusCode, string(respBytes))
	}

	if len(result.Data) != len(texts) {
		return nil, fmt.Errorf("embed: openai returned %d embeddings for %d inputs", len(result.Data), len(texts))
	}

	// OpenAI returns items in arbitrary order; sort by index.
	embeddings := make([][]float64, len(texts))
	for _, item := range result.Data {
		if item.Index < 0 || item.Index >= len(texts) {
			return nil, fmt.Errorf("embed: openai returned out-of-range index %d", item.Index)
		}
		embeddings[item.Index] = item.Embedding
	}

	// Validate dimensions when a target dimension is configured.
	if p.dimension > 0 {
		for i, emb := range embeddings {
			if len(emb) != p.dimension {
				return nil, fmt.Errorf("embed: openai embedding[%d] has dimension %d, expected %d", i, len(emb), p.dimension)
			}
		}
	}

	return embeddings, nil
}

// ---------------------------------------------------------------------------
// Ollama provider
// ---------------------------------------------------------------------------

type ollamaProvider struct {
	baseURL   string
	model     string
	dimension int
	client    *http.Client
}

func newOllamaProvider(cfg EmbedConfig) (*ollamaProvider, error) {
	baseURL := cfg.APIURL
	if baseURL == "" {
		baseURL = "http://localhost:11434"
	}
	model := cfg.Model
	if model == "" {
		model = "nomic-embed-text"
	}
	return &ollamaProvider{
		baseURL:   baseURL,
		model:     model,
		dimension: cfg.Dimension,
		client:    &http.Client{Timeout: 30 * time.Second},
	}, nil
}

func (p *ollamaProvider) Dimension() int { return p.dimension }

func (p *ollamaProvider) Embed(ctx context.Context, text string) ([]float64, error) {
	results, err := p.EmbedBatch(ctx, []string{text})
	if err != nil {
		return nil, err
	}
	if len(results) == 0 {
		return nil, fmt.Errorf("embed: ollama returned no embeddings")
	}
	return results[0], nil
}

// ollamaEmbedRequest is the JSON body sent to the Ollama /api/embed endpoint.
type ollamaEmbedRequest struct {
	Model string   `json:"model"`
	Input []string `json:"input"`
}

// ollamaEmbedResponse is the JSON response from the Ollama /api/embed endpoint.
type ollamaEmbedResponse struct {
	Model      string      `json:"model"`
	Embeddings [][]float64 `json:"embeddings"`
	Error      string      `json:"error"`
}

func (p *ollamaProvider) EmbedBatch(ctx context.Context, texts []string) ([][]float64, error) {
	if len(texts) == 0 {
		return nil, nil
	}

	reqBody := ollamaEmbedRequest{
		Model: p.model,
		Input: texts,
	}
	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("embed: ollama marshal request: %w", err)
	}

	url := p.baseURL + "/api/embed"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(bodyBytes))
	if err != nil {
		return nil, fmt.Errorf("embed: ollama create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := p.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("embed: ollama request failed: %w", err)
	}
	defer resp.Body.Close()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("embed: ollama read response: %w", err)
	}

	var result ollamaEmbedResponse
	if err := json.Unmarshal(respBytes, &result); err != nil {
		return nil, fmt.Errorf("embed: ollama parse response: %w", err)
	}

	if result.Error != "" {
		return nil, fmt.Errorf("embed: ollama API error: %s", result.Error)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("embed: ollama unexpected status %d: %s", resp.StatusCode, string(respBytes))
	}

	if len(result.Embeddings) != len(texts) {
		return nil, fmt.Errorf("embed: ollama returned %d embeddings for %d inputs", len(result.Embeddings), len(texts))
	}

	// Update cached dimension from the first response when not yet set.
	if p.dimension == 0 && len(result.Embeddings) > 0 {
		p.dimension = len(result.Embeddings[0])
	}

	// Validate dimensions when a target dimension is configured.
	if p.dimension > 0 {
		for i, emb := range result.Embeddings {
			if len(emb) != p.dimension {
				return nil, fmt.Errorf("embed: ollama embedding[%d] has dimension %d, expected %d", i, len(emb), p.dimension)
			}
		}
	}

	return result.Embeddings, nil
}
