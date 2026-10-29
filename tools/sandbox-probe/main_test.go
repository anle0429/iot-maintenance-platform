// Unit checks cover diagnostic contracts without claiming to exercise a deployed gVisor actor.
package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestRoleURLs prevents the operator from becoming a caller-selected network probe.
func TestRoleURLs(t *testing.T) {
	a, b, err := roleURLs("retrieval")
	if err != nil || a == b || !strings.Contains(a, "iot-retrieval-mcp") || !strings.Contains(b, "iot-drift-mcp") {
		t.Fatal("retrieval scope differs")
	}
	c, d, err := roleURLs("drift")
	if err != nil || c != b || d != a {
		t.Fatal("drift scope differs")
	}
	if _, _, err := roleURLs("coordinator"); err == nil {
		t.Fatal("unsupported role accepted")
	}
}

// TestPID1Identity never substitutes the operator identity for missing workload evidence.
func TestPID1Identity(t *testing.T) {
	for input, want := range map[string]int{"Name:\tworker\nUid:\t1001\t1001\t1001\t1001\n": 1001, "Uid: 0 0 0 0": 0, "Uid: unknown": -1, "": -1} {
		if got := readUID(input); got != want {
			t.Fatalf("identity mismatch: got %d want %d", got, want)
		}
	}
}

// TestFilesystemStates keeps presence and unexpected errors out of the accepted boundary states.
func TestFilesystemStates(t *testing.T) {
	directory := t.TempDir()
	if err := os.WriteFile(filepath.Join(directory, "present"), []byte("unit fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	if fileState(filepath.Join(directory, "present")) != "present" || fileState(filepath.Join(directory, "absent")) != "absent" {
		t.Fatal("filesystem observations differ")
	}
	if !protected("absent") || !protected("permission-denied") || protected("present") || protected("unknown-error") {
		t.Fatal("unknown/present path accepted")
	}
}

// TestHTTPReceipts records actual status while omitting bodies and refusing redirect traversal.
func TestHTTPReceipts(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/redirect" {
			w.Header().Set("Location", "/private")
			w.WriteHeader(302)
			return
		}
		w.WriteHeader(200)
		_, _ = w.Write([]byte("unit-body-not-for-the-report"))
	}))
	defer server.Close()
	for endpoint, want := range map[string]int{"/": 200, "/redirect": 302} {
		result := checkHTTP(server.URL+endpoint, time.Second)
		if result.Status != want || result.ErrorType != "" {
			t.Fatal("HTTP observation differs")
		}
		encoded, _ := json.Marshal(result)
		if strings.Contains(string(encoded), "unit-body") {
			t.Fatal("response body leaked")
		}
	}
}

// TestInjectionScope accepts no alternate runtime, state traversal, foreign namespace or arbitrary command.
func TestInjectionScope(t *testing.T) {
	state := "/var/lib/ateom-gvisor/actors/iot-agents:asr-unit-test/runsc-state"
	args, err := injectionArgs(runscPath, state, "retrieval")
	if err != nil || len(args) != 7 || args[2] != "--exec-fd=3" || args[3] != "--user=0:0" || args[4] != "kagent" {
		t.Fatal("fixed injection arguments differ")
	}
	for _, invalid := range []string{"/tmp/runsc-state", strings.Replace(state, "iot-agents:", "another:", 1), state + "/../runsc-state"} {
		if _, err := injectionArgs(runscPath, invalid, "retrieval"); err == nil {
			t.Fatal("foreign or traversed state accepted")
		}
	}
	if _, err := injectionArgs("/tmp/another-runtime", state, "retrieval"); err == nil {
		t.Fatal("unpinned runtime accepted")
	}
}
