// Tests exercise rejected budgets, preserved facts and streaming passthrough against a real local HTTP server.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"math/rand"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"testing/quick"
	"time"
)

const requestBody = `{"model":"iot-qwen2.5-7b","messages":[{"role":"user","content":"P-001"}],"tools":[{"type":"function","function":{"name":"get_machine_features"}}],"max_tokens":768,"stream":true}`

// object is test-only JSON fixture decoding, not a native model result.
func object(t *testing.T, body []byte) map[string]any {
	t.Helper()
	var value map[string]any
	if err := json.Unmarshal(body, &value); err != nil {
		t.Fatal(err)
	}
	return value
}

// TestHistoryAccumulation counts distinct completed turns and ignores tool_calls attached to a non-assistant role.
func TestHistoryAccumulation(t *testing.T) {
	for _, role := range []string{"assistant", "tool"} {
		value := object(t, []byte(requestBody))
		value["tools"] = make([]any, 8)
		value["messages"] = []any{map[string]any{"role": role, "tool_calls": make([]any, 5)}, map[string]any{"role": "assistant", "tool_calls": make([]any, 4)}}
		body, _ := json.Marshal(value)
		_, err := normalize(body)
		if (err != nil) != (role == "assistant") {
			t.Fatal("history reset or non-assistant calls counted")
		}
	}
}

// TestCapabilityBudgetLowerBoundary preserves a still-needed capability and does not infer a full retrieval role from a partial set.
func TestCapabilityBudgetLowerBoundary(t *testing.T) {
	sets := [][]string{{"assess_feature_drift", "transfer_to_agent"}, {"get_machine_features", "predict_failure", "search_knowledge", "transfer_to_agent"},
		{"get_machine_features", "predict_failure"}, {"predict_failure", "search_knowledge"}, {"get_machine_features", "search_knowledge"}}
	for _, names := range sets {
		budget := len(names)
		if len(names) == 4 {
			budget = 3
		}
		if names[0] == "assess_feature_drift" {
			budget = 1
		}
		value := object(t, []byte(requestBody))
		tools := []any{}
		for _, name := range names {
			tools = append(tools, map[string]any{"function": map[string]any{"name": name}})
		}
		value["tools"] = tools
		value["messages"] = []any{map[string]any{"role": "assistant", "tool_calls": make([]any, budget-1)}}
		body, _ := json.Marshal(value)
		after, err := normalize(body)
		if err != nil || object(t, after)["tools"] == nil {
			t.Fatal("required capability removed before budget")
		}
	}
}

// TestEmptyToolsAndStopBoundary keeps plain-chat controls intact and accepts the declared 128-byte stop boundary.
func TestEmptyToolsAndStopBoundary(t *testing.T) {
	value := object(t, []byte(requestBody))
	value["tools"] = []any{}
	value["stop"] = strings.Repeat("x", 128)
	body, _ := json.Marshal(value)
	after, err := normalize(body)
	if err != nil {
		t.Fatal(err)
	}
	observed := object(t, after)
	if observed["parallel_tool_calls"] != nil || observed["stop"] != value["stop"] {
		t.Fatal("plain chat controls changed")
	}
}

// TestResourceContract verifies the actual constructed internal server's stated private-routing and resource contract.
func TestResourceContract(t *testing.T) {
	s := configuredServer()
	transport := configuredTransport()
	if transport.Proxy != nil || transport.ResponseHeaderTimeout != 150*time.Second || transport.IdleConnTimeout != 30*time.Second {
		t.Fatal("private upstream deadline contract changed")
	}
	if s.Addr != ":8080" || s.Handler == nil || s.ReadHeaderTimeout != 5*time.Second || s.ReadTimeout != 15*time.Second ||
		s.WriteTimeout != 180*time.Second || s.IdleTimeout != 30*time.Second || s.MaxHeaderBytes != 16384 {
		t.Fatal("internal HTTP resource contract changed")
	}
}

// TestNormalizationIsIdempotent preserves arbitrary Unicode user data when the same request passes the adapter twice.
func TestNormalizationIsIdempotent(t *testing.T) {
	property := func(text string) bool {
		value := object(t, []byte(requestBody))
		value["messages"] = []any{map[string]any{"role": "user", "content": text}}
		body, _ := json.Marshal(value)
		once, err := normalize(body)
		if err != nil {
			return false
		}
		twice, err := normalize(once)
		if err != nil || !bytes.Equal(once, twice) {
			return false
		}
		got := object(t, once)["messages"].([]any)[0].(map[string]any)["content"]
		return got == text
	}
	if err := quick.Check(property, &quick.Config{MaxCount: 100, Rand: rand.New(rand.NewSource(7))}); err != nil {
		t.Fatal(err)
	}
}

// TestEndpointAndBodyBoundaries accepts exactly one MiB, rejects one byte more and refuses wrong methods.
func TestEndpointAndBodyBoundaries(t *testing.T) {
	target, _ := url.Parse("http://unused.invalid")
	h := handler(target, failedTransport{})
	for _, method := range []string{"GET", "PUT", "PATCH", "DELETE"} {
		r := httptest.NewRecorder()
		h.ServeHTTP(r, httptest.NewRequest(method, "/v1/chat/completions", strings.NewReader(requestBody)))
		if r.Code != 404 {
			t.Fatal("wrong method accepted")
		}
	}
	for _, path := range []string{"/health", "/metrics", "/unknown"} {
		r := httptest.NewRecorder()
		h.ServeHTTP(r, httptest.NewRequest("POST", path, strings.NewReader(requestBody)))
		if r.Code != 404 {
			t.Fatal("non-generation POST forwarded")
		}
	}
	r := httptest.NewRecorder()
	h.ServeHTTP(r, httptest.NewRequest("GET", "/unknown", nil))
	if r.Code != 404 {
		t.Fatal("unknown GET forwarded")
	}
	for _, n := range []int{1048576, 1048577, 2097152} {
		body := requestBody + strings.Repeat(" ", n-len(requestBody))
		r = httptest.NewRecorder()
		h.ServeHTTP(r, httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(body)))
		want := 502
		if n > 1048576 {
			want = 413
		}
		if r.Code != want {
			t.Fatal("body boundary changed", n, r.Code)
		}
	}
}

// statusTransport makes use of the explicitly supplied transport observable without network or DNS.
type statusTransport struct{}

func (statusTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	if r.Header.Get("Content-Type") != "application/json" {
		return nil, errors.New("JSON content type missing")
	}
	return &http.Response{StatusCode: 202, Header: http.Header{}, Body: io.NopCloser(strings.NewReader("actual-transport-result"))}, nil
}

// TestTransportContract checks private transport selection and normalization of a caller's incorrect content type.
func TestTransportContract(t *testing.T) {
	target, _ := url.Parse("http://unused.invalid")
	r := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(requestBody))
	request.Header.Set("Content-Type", "text/plain")
	handler(target, statusTransport{}).ServeHTTP(r, request)
	if r.Code != 202 || r.Body.String() != "actual-transport-result" {
		t.Fatal("configured transport or JSON type lost")
	}
}

// TestNormalization preserves actual request facts while enforcing one complete call per generation.
func TestNormalization(t *testing.T) {
	for _, stops := range []any{nil, "END", []string{"END"}, []string{"</tool_call>"}} {
		before := object(t, []byte(requestBody))
		before["stop"] = stops
		body, _ := json.Marshal(before)
		after, err := normalize(body)
		if err != nil {
			t.Fatal(err)
		}
		value := object(t, after)
		if value["parallel_tool_calls"] != false {
			t.Fatal("parallel calls enabled")
		}
		list := value["stop"].([]any)
		count := 0
		for _, stop := range list {
			if stop == "</tool_call>" {
				count++
			}
		}
		if count != 1 {
			t.Fatal("missing or duplicated termination marker")
		}
		for _, name := range []string{"model", "messages", "tools", "max_tokens", "stream"} {
			a, _ := json.Marshal(before[name])
			b, _ := json.Marshal(value[name])
			if !bytes.Equal(a, b) {
				t.Fatal("changed request fact", name)
			}
		}
	}
}

// TestToolBudget rejects an already excessive history and forces synthesis at the exact limit.
func TestToolBudget(t *testing.T) {
	for _, n := range []int{7, 8, 9} {
		value := object(t, []byte(requestBody))
		value["tools"] = make([]any, 9)
		calls := make([]any, n)
		for i := range calls {
			calls[i] = map[string]any{"id": "test-call"}
		}
		value["messages"] = []any{map[string]any{"role": "assistant", "tool_calls": calls}, map[string]any{"role": "tool", "content": "actual result fixture"}}
		body, _ := json.Marshal(value)
		result, err := normalize(body)
		if n == 9 {
			if err == nil {
				t.Fatal("excessive history accepted")
			}
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		observed := object(t, result)
		if n == 8 && (observed["tools"] != nil || observed["tool_choice"] != "none") {
			t.Fatal("tool budget not enforced")
		}
		if n == 7 && observed["tools"] == nil {
			t.Fatal("stopped before budget")
		}
	}
}

// TestCapabilityBudget forces synthesis after one drift call or three retrieval calls, before context overflow.
func TestCapabilityBudget(t *testing.T) {
	for _, capabilities := range []int{1, 3} {
		value := object(t, []byte(requestBody))
		value["tools"] = make([]any, capabilities)
		value["messages"] = []any{map[string]any{"role": "assistant", "tool_calls": make([]any, capabilities)}}
		body, _ := json.Marshal(value)
		result, err := normalize(body)
		if err != nil || object(t, result)["tools"] != nil || object(t, result)["tool_choice"] != "none" {
			t.Fatal("capability budget not enforced")
		}
	}
}

// TestOrchestrationDoesNotExtendBudget models an SDK-injected capability alongside the real business tools.
func TestOrchestrationDoesNotExtendBudget(t *testing.T) {
	for _, names := range [][]string{{"assess_feature_drift", "transfer_to_agent"}, {"get_machine_features", "predict_failure", "search_knowledge", "transfer_to_agent"}} {
		value := object(t, []byte(requestBody))
		tools := []any{}
		for _, name := range names {
			tools = append(tools, map[string]any{"function": map[string]any{"name": name}})
		}
		value["tools"] = tools
		value["messages"] = []any{map[string]any{"role": "assistant", "tool_calls": make([]any, len(names)-1)}}
		body, _ := json.Marshal(value)
		result, err := normalize(body)
		if err != nil || object(t, result)["tools"] != nil {
			t.Fatal("orchestration extended the specialist budget")
		}
	}
}

// TestInvalidRequests covers malformed partitions and exact generation-budget boundaries.
func TestInvalidRequests(t *testing.T) {
	for _, body := range []string{"", "null", "[]", "{", `{}`, `{"model":3}`,
		strings.Replace(requestBody, modelName, "another-model", 1),
		strings.Replace(requestBody, `[{"role":"user","content":"P-001"}]`, `[]`, 1),
		strings.Replace(requestBody, `[{"role":"user","content":"P-001"}]`, `3`, 1)} {
		if _, err := normalize([]byte(body)); err == nil {
			t.Fatal("invalid object accepted", body)
		}
	}
	for _, pair := range []struct {
		name   string
		values []any
	}{
		{"tools", []any{nil, 3, "tool"}}, {"stop", []any{3, []any{3}, "", strings.Repeat("x", 129)}},
		{"max_tokens", []any{nil, 0, -1, 1025, 1.5, "768"}},
	} {
		for _, bad := range pair.values {
			value := object(t, []byte(requestBody))
			value[pair.name] = bad
			body, _ := json.Marshal(value)
			if _, err := normalize(body); err == nil {
				t.Fatal("invalid field accepted", pair.name, bad)
			}
		}
	}
	for _, limit := range []int{1, 1024} {
		value := object(t, []byte(requestBody))
		value["max_tokens"] = limit
		body, _ := json.Marshal(value)
		if _, err := normalize(body); err != nil {
			t.Fatal("valid boundary rejected", err)
		}
	}
	value := object(t, []byte(requestBody))
	delete(value, "max_tokens")
	delete(value, "tools")
	body, _ := json.Marshal(value)
	after, err := normalize(body)
	if err != nil || object(t, after)["max_tokens"] != float64(768) || object(t, after)["stop"] != nil {
		t.Fatal("plain chat changed")
	}
}

// TestPassthrough observes original SSE bytes, backend failure status and independent health responses.
func TestPassthrough(t *testing.T) {
	stream := "data: {\"choices\":[{\"delta\":{\"content\":null}}]}\n\ndata: [DONE]\n\n"
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "GET" {
			w.WriteHeader(503)
			_, _ = w.Write([]byte("backend-not-ready"))
			return
		}
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
		}
		value := object(t, body)
		if value["parallel_tool_calls"] != false || r.ContentLength != int64(len(body)) {
			t.Error("control not forwarded")
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(200)
		for _, line := range strings.SplitAfter(stream, "\n") {
			_, _ = io.WriteString(w, line)
			w.(http.Flusher).Flush()
		}
	}))
	defer upstream.Close()
	target, _ := url.Parse(upstream.URL)
	h := handler(target, http.DefaultTransport)
	r := httptest.NewRecorder()
	h.ServeHTTP(r, httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(requestBody)))
	if r.Code != 200 || r.Body.String() != stream || r.Header().Get("X-Iot-Tool-Control") != "serial-close-v1" {
		t.Fatal("stream changed")
	}
	for _, path := range []string{"/health", "/metrics"} {
		r = httptest.NewRecorder()
		h.ServeHTTP(r, httptest.NewRequest("GET", path, nil))
		if r.Code != 503 || r.Body.String() != "backend-not-ready" {
			t.Fatal("backend health changed")
		}
	}
	for _, path := range []string{"/", "/v1/chat/completions?redirect=x"} {
		r = httptest.NewRecorder()
		h.ServeHTTP(r, httptest.NewRequest("POST", path, strings.NewReader(requestBody)))
		if r.Code != 404 {
			t.Fatal("unowned path accepted")
		}
	}
	for _, pair := range []struct {
		body string
		code int
	}{{"{}", 400}, {strings.Repeat("x", maxBody+1), 413}} {
		r = httptest.NewRecorder()
		h.ServeHTTP(r, httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(pair.body)))
		if r.Code != pair.code {
			t.Fatal("invalid request forwarded", r.Code)
		}
	}
}

// failedTransport produces an upstream failure without using a flaky external destination.
type failedTransport struct{}

func (failedTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	return nil, errors.New("test-only transport failure")
}

// TestBackendFailure checks fail-closed status without exposing a transport error or request body.
func TestBackendFailure(t *testing.T) {
	target, _ := url.Parse("http://unused.invalid")
	r := httptest.NewRecorder()
	handler(target, failedTransport{}).ServeHTTP(r, httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(requestBody)))
	if r.Code != 502 || r.Body.String() != "model_backend_unavailable\n" {
		t.Fatal("backend failure hidden or leaked")
	}
}
