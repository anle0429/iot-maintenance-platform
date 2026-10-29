// model-control adds a bounded Qwen tool-turn policy and forwards original model responses unchanged.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"time"
)

const modelName = "iot-qwen2.5-7b"
const backend = "http://iot-model-gpu.iot-llm.svc.cluster.local:8080"
const maxBody = 1 << 20
const maxToolCalls = 8

// normalize preserves message/tool contents; only generation controls change, never facts or model output.
func normalize(body []byte) ([]byte, error) {
	var value map[string]json.RawMessage
	if json.Unmarshal(body, &value) != nil || value == nil {
		return nil, errors.New("require a JSON object")
	}
	var model string
	if json.Unmarshal(value["model"], &model) != nil || model != modelName {
		return nil, errors.New("require the pinned served model")
	}
	var messages []struct {
		Role  string            `json:"role"`
		Calls []json.RawMessage `json:"tool_calls"`
	}
	if json.Unmarshal(value["messages"], &messages) != nil || len(messages) == 0 {
		return nil, errors.New("require conversation messages")
	}
	calls := 0
	for _, message := range messages {
		if message.Role == "assistant" {
			calls += len(message.Calls)
		}
	}
	if calls > maxToolCalls {
		return nil, errors.New("tool history exceeds the bounded policy")
	}
	var tools []json.RawMessage
	if raw, exists := value["tools"]; exists {
		if json.Unmarshal(raw, &tools) != nil || bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
			return nil, errors.New("require a tool array")
		}
	}
	var stops []string
	if raw, exists := value["stop"]; exists && !bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		var single string
		if json.Unmarshal(raw, &single) == nil {
			stops = []string{single}
		} else if json.Unmarshal(raw, &stops) != nil {
			return nil, errors.New("require string stop sequences")
		}
	}
	for _, stop := range stops {
		if len(stop) == 0 || len(stop) > 128 {
			return nil, errors.New("invalid stop sequence length")
		}
	}
	var limit int
	if raw, exists := value["max_tokens"]; exists {
		if json.Unmarshal(raw, &limit) != nil || limit < 1 || limit > 1024 {
			return nil, errors.New("require a token budget from 1 to 1024")
		}
	} else {
		value["max_tokens"] = json.RawMessage("768")
	}
	if len(tools) > 0 {
		// Each declared capability gets at most one selection in the standard specialist/coordinator turn.
		// At the budget, request synthesis from the actual history rather than allowing unbounded retries.
		budget := len(tools)
		names := map[string]bool{}
		for _, raw := range tools {
			var capability struct {
				Function struct {
					Name string `json:"name"`
				} `json:"function"`
			}
			if json.Unmarshal(raw, &capability) == nil {
				names[capability.Function.Name] = true
			}
		}
		// ADK can append orchestration capabilities; they do not increase the specialist's business-tool budget.
		if names["get_machine_features"] && names["predict_failure"] && names["search_knowledge"] {
			budget = 3
		}
		if names["assess_feature_drift"] {
			budget = 1
		}
		if budget > maxToolCalls {
			budget = maxToolCalls
		}
		if calls >= budget {
			delete(value, "tools")
			value["tool_choice"] = json.RawMessage(`"none"`)
		} else {
			found := false
			for _, stop := range stops {
				if stop == "</tool_call>" {
					found = true
				}
			}
			if !found {
				stops = append(stops, "</tool_call>")
			}
			encoded, _ := json.Marshal(stops)
			value["stop"] = encoded
			value["parallel_tool_calls"] = json.RawMessage("false")
		}
	}
	return json.Marshal(value)
}

// handler limits the internal endpoint surface and streams the backend's unmodified response bytes and status.
func handler(target *url.URL, transport http.RoundTripper) http.Handler {
	proxy := httputil.NewSingleHostReverseProxy(target)
	proxy.Transport = transport
	proxy.ErrorLog = log.New(io.Discard, "", 0)
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		http.Error(w, "model_backend_unavailable", http.StatusBadGateway)
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "GET" && (r.URL.Path == "/health" || r.URL.Path == "/metrics") {
			proxy.ServeHTTP(w, r)
			return
		}
		if r.Method != "POST" || r.URL.Path != "/v1/chat/completions" || r.URL.RawQuery != "" {
			http.NotFound(w, r)
			return
		}
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBody))
		if err != nil {
			http.Error(w, "invalid_or_oversized_request", http.StatusRequestEntityTooLarge)
			return
		}
		normalized, err := normalize(body)
		if err != nil {
			http.Error(w, "invalid_model_policy_request", http.StatusBadRequest)
			return
		}
		r.Body = io.NopCloser(bytes.NewReader(normalized))
		r.ContentLength = int64(len(normalized))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Del("Content-Length")
		w.Header().Set("X-Iot-Tool-Control", "serial-close-v1")
		proxy.ServeHTTP(w, r)
	})
}

// configuredServer fixes the private destination and operational resource/deadline contract.
func configuredServer() *http.Server {
	target, _ := url.Parse(backend)
	return &http.Server{Addr: ":8080", Handler: handler(target, configuredTransport()),
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 180 * time.Second,
		IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16 << 10}
}

// configuredTransport keeps the model destination private and bounds stalled upstream headers and idle connections.
func configuredTransport() *http.Transport {
	return &http.Transport{Proxy: nil, ResponseHeaderTimeout: 150 * time.Second, IdleConnTimeout: 30 * time.Second}
}

// main exits nonzero when the unprivileged internal server cannot start.
func main() {
	if configuredServer().ListenAndServe() != nil {
		os.Exit(1)
	}
}
