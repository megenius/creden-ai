package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// NewEmbeddingProvider factory
// ---------------------------------------------------------------------------

func TestNewEmbeddingProvider_NoneProvider(t *testing.T) {
	p, err := NewEmbeddingProvider(EmbedConfig{Provider: "none"})
	if err != nil {
		t.Fatalf("expected nil error for 'none' provider, got: %v", err)
	}
	if p != nil {
		t.Fatal("expected nil provider for 'none'")
	}
}

func TestNewEmbeddingProvider_EmptyProvider(t *testing.T) {
	p, err := NewEmbeddingProvider(EmbedConfig{Provider: ""})
	if err != nil {
		t.Fatalf("expected nil error for empty provider, got: %v", err)
	}
	if p != nil {
		t.Fatal("expected nil provider for empty string")
	}
}

func TestNewEmbeddingProvider_UnknownProvider(t *testing.T) {
	_, err := NewEmbeddingProvider(EmbedConfig{Provider: "bedrock"})
	if err == nil {
		t.Fatal("expected error for unknown provider")
	}
	if !strings.Contains(err.Error(), "unknown provider") {
		t.Fatalf("error should mention 'unknown provider', got: %v", err)
	}
}

func TestNewEmbeddingProvider_OpenAIMissingAPIKey(t *testing.T) {
	_, err := NewEmbeddingProvider(EmbedConfig{Provider: "openai", APIKey: ""})
	if err == nil {
		t.Fatal("expected error when OpenAI APIKey is empty")
	}
	if !strings.Contains(err.Error(), "APIKey") {
		t.Fatalf("error should mention APIKey, got: %v", err)
	}
}

func TestNewEmbeddingProvider_OpenAIDefaultModel(t *testing.T) {
	p, err := NewEmbeddingProvider(EmbedConfig{
		Provider:  "openai",
		APIKey:    "test-key",
		Dimension: 4,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	op := p.(*openaiProvider)
	if op.model != "text-embedding-3-small" {
		t.Fatalf("default model: want 'text-embedding-3-small', got %q", op.model)
	}
}

func TestNewEmbeddingProvider_OllamaDefaultModel(t *testing.T) {
	p, err := NewEmbeddingProvider(EmbedConfig{Provider: "ollama", Dimension: 4})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	op := p.(*ollamaProvider)
	if op.model != "nomic-embed-text" {
		t.Fatalf("default model: want 'nomic-embed-text', got %q", op.model)
	}
}

func TestNewEmbeddingProvider_OllamaDefaultBaseURL(t *testing.T) {
	p, err := NewEmbeddingProvider(EmbedConfig{Provider: "ollama", Dimension: 4})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	op := p.(*ollamaProvider)
	if op.baseURL != "http://localhost:11434" {
		t.Fatalf("default baseURL: want 'http://localhost:11434', got %q", op.baseURL)
	}
}

// ---------------------------------------------------------------------------
// OpenAI provider — mocked HTTP
// ---------------------------------------------------------------------------

// openaiTestServer creates an httptest server that mimics the OpenAI embeddings API.
// respFn receives the request body and returns the response to send.
func openaiTestServer(t *testing.T, respFn func(req openaiEmbedRequest) openaiEmbedResponse) (*httptest.Server, *openaiProvider) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req openaiEmbedRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad request", 400)
			return
		}
		resp := respFn(req)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	t.Cleanup(srv.Close)

	p := &openaiProvider{
		apiKey:    "test-key",
		model:     "text-embedding-3-small",
		dimension: 4,
		client:    srv.Client(),
	}
	// Patch the URL inside the provider via EmbedBatch by creating our own.
	// We create the provider manually and point the client at the test server.
	// Because EmbedBatch hard-codes the URL, we need to use the real implementation
	// and swap the http.Client transport to redirect to our test server.
	p.client.Transport = redirectTransport(srv.URL)
	return srv, p
}

// redirectTransport rewrites all request URLs to point at baseURL.
type redirectTransport string

func (rt redirectTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	req2 := req.Clone(req.Context())
	req2.URL.Scheme = "http"
	req2.URL.Host = strings.TrimPrefix(string(rt), "http://")
	return http.DefaultTransport.RoundTrip(req2)
}

func TestOpenAIProvider_EmbedBatch_Success(t *testing.T) {
	_, p := openaiTestServer(t, func(req openaiEmbedRequest) openaiEmbedResponse {
		return openaiEmbedResponse{
			Data: []struct {
				Embedding []float64 `json:"embedding"`
				Index     int       `json:"index"`
			}{
				{Embedding: []float64{0.1, 0.2, 0.3, 0.4}, Index: 0},
				{Embedding: []float64{0.5, 0.6, 0.7, 0.8}, Index: 1},
			},
		}
	})

	embeddings, err := p.EmbedBatch(context.Background(), []string{"hello", "world"})
	if err != nil {
		t.Fatalf("EmbedBatch: %v", err)
	}
	if len(embeddings) != 2 {
		t.Fatalf("want 2 embeddings, got %d", len(embeddings))
	}
	if embeddings[0][0] != 0.1 {
		t.Fatalf("embeddings[0][0]: want 0.1, got %f", embeddings[0][0])
	}
	if embeddings[1][0] != 0.5 {
		t.Fatalf("embeddings[1][0]: want 0.5, got %f", embeddings[1][0])
	}
}

func TestOpenAIProvider_EmbedBatch_OutOfOrderIndex(t *testing.T) {
	// OpenAI may return items in non-sequential order.
	_, p := openaiTestServer(t, func(req openaiEmbedRequest) openaiEmbedResponse {
		return openaiEmbedResponse{
			Data: []struct {
				Embedding []float64 `json:"embedding"`
				Index     int       `json:"index"`
			}{
				{Embedding: []float64{0.5, 0.6, 0.7, 0.8}, Index: 1}, // second first
				{Embedding: []float64{0.1, 0.2, 0.3, 0.4}, Index: 0}, // first second
			},
		}
	})

	embeddings, err := p.EmbedBatch(context.Background(), []string{"a", "b"})
	if err != nil {
		t.Fatalf("EmbedBatch: %v", err)
	}
	// Despite out-of-order response, index 0 should be at embeddings[0].
	if embeddings[0][0] != 0.1 {
		t.Fatalf("index reordering failed: embeddings[0][0] = %f", embeddings[0][0])
	}
}

func TestOpenAIProvider_EmbedBatch_EmptyInput(t *testing.T) {
	_, p := openaiTestServer(t, func(req openaiEmbedRequest) openaiEmbedResponse {
		return openaiEmbedResponse{}
	})

	embeddings, err := p.EmbedBatch(context.Background(), []string{})
	if err != nil {
		t.Fatalf("empty batch should not error: %v", err)
	}
	if embeddings != nil {
		t.Fatalf("empty batch: want nil, got %v", embeddings)
	}
}

func TestOpenAIProvider_EmbedBatch_APIError(t *testing.T) {
	_, p := openaiTestServer(t, func(req openaiEmbedRequest) openaiEmbedResponse {
		return openaiEmbedResponse{
			Error: &struct {
				Message string `json:"message"`
				Type    string `json:"type"`
			}{Message: "invalid api key", Type: "authentication_error"},
		}
	})

	_, err := p.EmbedBatch(context.Background(), []string{"hello"})
	if err == nil {
		t.Fatal("expected error for API error response")
	}
	if !strings.Contains(err.Error(), "authentication_error") {
		t.Fatalf("error should contain error type, got: %v", err)
	}
}

func TestOpenAIProvider_EmbedBatch_DimensionMismatch(t *testing.T) {
	_, p := openaiTestServer(t, func(req openaiEmbedRequest) openaiEmbedResponse {
		return openaiEmbedResponse{
			Data: []struct {
				Embedding []float64 `json:"embedding"`
				Index     int       `json:"index"`
			}{
				// Return 3-dim embedding but provider expects 4.
				{Embedding: []float64{0.1, 0.2, 0.3}, Index: 0},
			},
		}
	})
	p.dimension = 4

	_, err := p.EmbedBatch(context.Background(), []string{"hello"})
	if err == nil {
		t.Fatal("expected error for dimension mismatch")
	}
	if !strings.Contains(err.Error(), "dimension") {
		t.Fatalf("error should mention dimension, got: %v", err)
	}
}

func TestOpenAIProvider_EmbedBatch_CountMismatch(t *testing.T) {
	_, p := openaiTestServer(t, func(req openaiEmbedRequest) openaiEmbedResponse {
		// Return only 1 embedding for 2 inputs.
		return openaiEmbedResponse{
			Data: []struct {
				Embedding []float64 `json:"embedding"`
				Index     int       `json:"index"`
			}{
				{Embedding: []float64{0.1, 0.2, 0.3, 0.4}, Index: 0},
			},
		}
	})

	_, err := p.EmbedBatch(context.Background(), []string{"hello", "world"})
	if err == nil {
		t.Fatal("expected error for count mismatch")
	}
}

func TestOpenAIProvider_Embed_SingleText(t *testing.T) {
	_, p := openaiTestServer(t, func(req openaiEmbedRequest) openaiEmbedResponse {
		return openaiEmbedResponse{
			Data: []struct {
				Embedding []float64 `json:"embedding"`
				Index     int       `json:"index"`
			}{
				{Embedding: []float64{1, 2, 3, 4}, Index: 0},
			},
		}
	})

	emb, err := p.Embed(context.Background(), "test text")
	if err != nil {
		t.Fatalf("Embed: %v", err)
	}
	if len(emb) != 4 {
		t.Fatalf("want 4 dimensions, got %d", len(emb))
	}
}

func TestOpenAIProvider_Dimension(t *testing.T) {
	p := &openaiProvider{dimension: 1536}
	if p.Dimension() != 1536 {
		t.Fatalf("Dimension(): want 1536, got %d", p.Dimension())
	}
}

func TestOpenAIProvider_EmbedBatch_OutOfRangeIndex(t *testing.T) {
	_, p := openaiTestServer(t, func(req openaiEmbedRequest) openaiEmbedResponse {
		return openaiEmbedResponse{
			Data: []struct {
				Embedding []float64 `json:"embedding"`
				Index     int       `json:"index"`
			}{
				{Embedding: []float64{0.1, 0.2, 0.3, 0.4}, Index: 99}, // out of range
			},
		}
	})

	_, err := p.EmbedBatch(context.Background(), []string{"hello"})
	if err == nil {
		t.Fatal("expected error for out-of-range index")
	}
}

func TestOpenAIProvider_EmbedBatch_NoDimensionValidation(t *testing.T) {
	// When dimension == 0, no dimension validation should occur.
	_, p := openaiTestServer(t, func(req openaiEmbedRequest) openaiEmbedResponse {
		return openaiEmbedResponse{
			Data: []struct {
				Embedding []float64 `json:"embedding"`
				Index     int       `json:"index"`
			}{
				{Embedding: []float64{0.1, 0.2, 0.3}, Index: 0},
			},
		}
	})
	p.dimension = 0 // no validation

	embeddings, err := p.EmbedBatch(context.Background(), []string{"hello"})
	if err != nil {
		t.Fatalf("expected no error with dimension=0, got: %v", err)
	}
	if len(embeddings[0]) != 3 {
		t.Fatalf("want 3, got %d", len(embeddings[0]))
	}
}

// ---------------------------------------------------------------------------
// Ollama provider — mocked HTTP
// ---------------------------------------------------------------------------

// ollamaTestServer creates an httptest server that mimics the Ollama /api/embed endpoint.
func ollamaTestServer(t *testing.T, respFn func(req ollamaEmbedRequest) ollamaEmbedResponse) (*httptest.Server, *ollamaProvider) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/embed" {
			http.Error(w, "not found", 404)
			return
		}
		var req ollamaEmbedRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad request", 400)
			return
		}
		resp := respFn(req)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	t.Cleanup(srv.Close)

	p := &ollamaProvider{
		baseURL:   srv.URL,
		model:     "nomic-embed-text",
		dimension: 4,
		client:    srv.Client(),
	}
	return srv, p
}

func TestOllamaProvider_EmbedBatch_Success(t *testing.T) {
	_, p := ollamaTestServer(t, func(req ollamaEmbedRequest) ollamaEmbedResponse {
		return ollamaEmbedResponse{
			Model: "nomic-embed-text",
			Embeddings: [][]float64{
				{0.1, 0.2, 0.3, 0.4},
				{0.5, 0.6, 0.7, 0.8},
			},
		}
	})

	embeddings, err := p.EmbedBatch(context.Background(), []string{"hello", "world"})
	if err != nil {
		t.Fatalf("EmbedBatch: %v", err)
	}
	if len(embeddings) != 2 {
		t.Fatalf("want 2 embeddings, got %d", len(embeddings))
	}
}

func TestOllamaProvider_EmbedBatch_EmptyInput(t *testing.T) {
	_, p := ollamaTestServer(t, func(req ollamaEmbedRequest) ollamaEmbedResponse {
		return ollamaEmbedResponse{}
	})

	embeddings, err := p.EmbedBatch(context.Background(), []string{})
	if err != nil {
		t.Fatalf("empty batch should not error: %v", err)
	}
	if embeddings != nil {
		t.Fatal("empty batch should return nil")
	}
}

func TestOllamaProvider_EmbedBatch_APIError(t *testing.T) {
	_, p := ollamaTestServer(t, func(req ollamaEmbedRequest) ollamaEmbedResponse {
		return ollamaEmbedResponse{Error: "model not found"}
	})

	_, err := p.EmbedBatch(context.Background(), []string{"hello"})
	if err == nil {
		t.Fatal("expected error for API error response")
	}
	if !strings.Contains(err.Error(), "model not found") {
		t.Fatalf("error should contain API error message, got: %v", err)
	}
}

func TestOllamaProvider_EmbedBatch_DimensionMismatch(t *testing.T) {
	_, p := ollamaTestServer(t, func(req ollamaEmbedRequest) ollamaEmbedResponse {
		return ollamaEmbedResponse{
			Embeddings: [][]float64{
				{0.1, 0.2, 0.3}, // 3-dim, but provider expects 4
			},
		}
	})
	p.dimension = 4

	_, err := p.EmbedBatch(context.Background(), []string{"hello"})
	if err == nil {
		t.Fatal("expected error for dimension mismatch")
	}
	if !strings.Contains(err.Error(), "dimension") {
		t.Fatalf("error should mention dimension, got: %v", err)
	}
}

func TestOllamaProvider_EmbedBatch_CountMismatch(t *testing.T) {
	_, p := ollamaTestServer(t, func(req ollamaEmbedRequest) ollamaEmbedResponse {
		// Return 1 embedding for 2 inputs.
		return ollamaEmbedResponse{
			Embeddings: [][]float64{
				{0.1, 0.2, 0.3, 0.4},
			},
		}
	})

	_, err := p.EmbedBatch(context.Background(), []string{"hello", "world"})
	if err == nil {
		t.Fatal("expected error for count mismatch")
	}
}

func TestOllamaProvider_EmbedBatch_AutoSetsDimension(t *testing.T) {
	_, p := ollamaTestServer(t, func(req ollamaEmbedRequest) ollamaEmbedResponse {
		return ollamaEmbedResponse{
			Embeddings: [][]float64{{0.1, 0.2, 0.3, 0.4, 0.5}}, // 5-dim
		}
	})
	p.dimension = 0 // unset

	_, err := p.EmbedBatch(context.Background(), []string{"hello"})
	if err != nil {
		t.Fatalf("EmbedBatch: %v", err)
	}
	if p.dimension != 5 {
		t.Fatalf("dimension should be auto-set to 5, got %d", p.dimension)
	}
}

func TestOllamaProvider_Embed_SingleText(t *testing.T) {
	_, p := ollamaTestServer(t, func(req ollamaEmbedRequest) ollamaEmbedResponse {
		return ollamaEmbedResponse{
			Embeddings: [][]float64{{1.0, 2.0, 3.0, 4.0}},
		}
	})

	emb, err := p.Embed(context.Background(), "test")
	if err != nil {
		t.Fatalf("Embed: %v", err)
	}
	if len(emb) != 4 {
		t.Fatalf("want 4 dimensions, got %d", len(emb))
	}
	if emb[0] != 1.0 {
		t.Fatalf("emb[0]: want 1.0, got %f", emb[0])
	}
}

func TestOllamaProvider_Dimension(t *testing.T) {
	p := &ollamaProvider{dimension: 768}
	if p.Dimension() != 768 {
		t.Fatalf("Dimension(): want 768, got %d", p.Dimension())
	}
}

func TestOllamaProvider_UsesCustomBaseURL(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		resp := ollamaEmbedResponse{
			Embeddings: [][]float64{{0.1, 0.2, 0.3, 0.4}},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer srv.Close()

	p, err := NewEmbeddingProvider(EmbedConfig{
		Provider:  "ollama",
		Model:     "test-model",
		APIURL:    srv.URL,
		Dimension: 4,
	})
	if err != nil {
		t.Fatalf("NewEmbeddingProvider: %v", err)
	}

	op := p.(*ollamaProvider)
	if op.baseURL != srv.URL {
		t.Fatalf("baseURL: want %q, got %q", srv.URL, op.baseURL)
	}
}

func TestOllamaProvider_SendsCorrectModel(t *testing.T) {
	var capturedModel string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req ollamaEmbedRequest
		json.NewDecoder(r.Body).Decode(&req)
		capturedModel = req.Model
		resp := ollamaEmbedResponse{
			Embeddings: [][]float64{{0.1, 0.2, 0.3, 0.4}},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer srv.Close()

	p := &ollamaProvider{
		baseURL:   srv.URL,
		model:     "custom-model",
		dimension: 4,
		client:    srv.Client(),
	}
	_, _ = p.EmbedBatch(context.Background(), []string{"text"})

	if capturedModel != "custom-model" {
		t.Fatalf("model sent: want 'custom-model', got %q", capturedModel)
	}
}

// ---------------------------------------------------------------------------
// Context cancellation
// ---------------------------------------------------------------------------

func TestOpenAIProvider_EmbedBatch_ContextCancelled(t *testing.T) {
	// Server that blocks until the client disconnects.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	}))
	defer srv.Close()

	p := &openaiProvider{
		apiKey:    "key",
		model:     "model",
		dimension: 4,
		client:    srv.Client(),
	}
	p.client.Transport = redirectTransport(srv.URL)

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	_, err := p.EmbedBatch(ctx, []string{"hello"})
	if err == nil {
		t.Fatal("expected error for cancelled context")
	}
}

func TestOllamaProvider_EmbedBatch_ContextCancelled(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	}))
	defer srv.Close()

	p := &ollamaProvider{
		baseURL:   srv.URL,
		model:     "model",
		dimension: 4,
		client:    srv.Client(),
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, err := p.EmbedBatch(ctx, []string{"hello"})
	if err == nil {
		t.Fatal("expected error for cancelled context")
	}
}
