// sandbox-probe performs read-only operator diagnostics inside an existing gVisor actor.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path"
	"reflect"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const runscDigest = "f18a948bf9c8bbb54eb998549a3a8d719a1c7de2efbe8fdd2ff0ee5fecd06f19"
const runscPath = "/var/lib/ateom-gvisor/static-files/runsc-" + runscDigest

// HTTPResult exposes only observed status and error type, never response bodies or credential-bearing errors.
type HTTPResult struct {
	Status    int    `json:"http_status"`
	ErrorType string `json:"error_type,omitempty"`
}

// Report keeps the injected diagnostic identity distinct from the observed PID1 identity.
type Report struct {
	Role               string            `json:"role"`
	OperatorUID        int               `json:"operator_uid"`
	OperatorGID        int               `json:"operator_gid"`
	PID1UID            int               `json:"pid1_uid"`
	Kernel             string            `json:"kernel"`
	Filesystem         map[string]string `json:"filesystem"`
	Allowed            HTTPResult        `json:"allowed_mcp"`
	Blocked            HTTPResult        `json:"blocked_other_role_mcp"`
	FilesystemVerified bool              `json:"filesystem_boundary_verified"`
	EgressVerified     bool              `json:"egress_policy_verified"`
	Passed             bool              `json:"passed"`
	Scope              string            `json:"scope"`
}

// roleURLs fixes both destinations to the two owned services; this program is not an arbitrary HTTP tool.
func roleURLs(role string) (string, string, error) {
	retrieval := "http://iot-retrieval-mcp.iot-agents.svc.cluster.local:8080/health/live"
	drift := "http://iot-drift-mcp.iot-agents.svc.cluster.local:8080/health/live"
	switch role {
	case "retrieval":
		return retrieval, drift, nil
	case "drift":
		return drift, retrieval, nil
	default:
		return "", "", errors.New("the diagnostic supports only the two specialist roles")
	}
}

// readUID reads PID1's reported identity without assuming it equals the injected operator's user.
func readUID(contents string) int {
	for _, line := range strings.Split(contents, "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 2 && fields[0] == "Uid:" {
			value, err := strconv.Atoi(fields[1])
			if err == nil && value >= 0 {
				return value
			}
		}
	}
	return -1
}

// fileState distinguishes actual absence, permission denial, presence and an uninterpretable error.
func fileState(filename string) string {
	_, err := os.Stat(filename)
	switch {
	case err == nil:
		return "present"
	case os.IsNotExist(err):
		return "absent"
	case os.IsPermission(err):
		return "permission-denied"
	default:
		return "unknown-error"
	}
}

// protected reports the measured absence/denial, leaving unexpected failures unaccepted.
func protected(state string) bool {
	return state == "absent" || state == "permission-denied"
}

// checkHTTP respects the actor's actual proxy environment, bounds time and refuses redirects.
func checkHTTP(url string, timeout time.Duration) HTTPResult {
	transport := &http.Transport{Proxy: http.ProxyFromEnvironment}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: timeout,
		CheckRedirect: func(req *http.Request, via []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Get(url)
	if err != nil {
		return HTTPResult{ErrorType: reflect.TypeOf(err).String()}
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
	return HTTPResult{Status: response.StatusCode}
}

// collect observes one actor; a failed network request alone is not treated as verified policy denial.
func collect(role string) (Report, error) {
	if runtime.GOOS != "linux" {
		return Report{}, errors.New("actor diagnostics require the retained Linux runtime")
	}
	allowed, blocked, err := roleURLs(role)
	if err != nil {
		return Report{}, err
	}
	process, _ := os.ReadFile("/proc/1/status")
	kernel, _ := os.ReadFile("/proc/version")
	files := map[string]string{
		"kubernetes_serviceaccount_token": fileState("/var/run/secrets/kubernetes.io/serviceaccount/token"),
		"host_coursework_data":            fileState("/srv/iot-data"),
	}
	report := Report{Role: role, OperatorUID: os.Getuid(), OperatorGID: os.Getgid(), PID1UID: readUID(string(process)),
		Kernel: strings.TrimSpace(string(kernel)), Filesystem: files,
		Allowed: checkHTTP(allowed, 8*time.Second), Blocked: checkHTTP(blocked, 8*time.Second),
		Scope: "Read-only operator process inside one gVisor actor. PID1 identity is observed separately. No agent execution tool, host compromise, production HA or full coursework acceptance is established."}
	report.FilesystemVerified = protected(files["kubernetes_serviceaccount_token"]) && protected(files["host_coursework_data"])
	report.EgressVerified = report.Allowed.Status == 200 && report.Blocked.Status == 403
	report.Passed = report.PID1UID >= 0 && report.Kernel != "" && report.FilesystemVerified && report.EgressVerified
	return report, nil
}

// injectionArgs permits only the pinned runtime and one owned actor's state, without a shell or arbitrary command.
func injectionArgs(runtimePath, state, role string) ([]string, error) {
	if runtimePath != runscPath || !regexp.MustCompile(`^/var/lib/ateom-gvisor/actors/iot-agents:asr-[a-z0-9-]+/runsc-state$`).MatchString(path.Clean(state)) || path.Clean(state) != state {
		return nil, errors.New("require the pinned runtime and one owned actor state directory")
	}
	if _, _, err := roleURLs(role); err != nil {
		return nil, err
	}
	return []string{"--root=" + state, "exec", "--exec-fd=3", "--user=0:0", "kagent", "/iot-sandbox-probe", "--role=" + role}, nil
}

// inject uses an executable file descriptor in the trusted worker, so the actor image needs no diagnostic utilities.
func inject(runtimePath, state, role string) error {
	if runtime.GOOS != "linux" {
		return errors.New("actor injection requires the retained Linux runtime")
	}
	args, err := injectionArgs(runtimePath, state, role)
	if err != nil {
		return err
	}
	content, err := os.ReadFile(runtimePath)
	if err != nil {
		return err
	}
	digest := sha256.Sum256(content)
	if hex.EncodeToString(digest[:]) != runscDigest {
		return errors.New("the runtime checksum differs")
	}
	filename, err := os.Executable()
	if err != nil {
		return err
	}
	binary, err := os.Open(filename)
	if err != nil {
		return err
	}
	defer binary.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, runtimePath, args...)
	command.ExtraFiles = []*os.File{binary}
	command.Stdout, command.Stderr = os.Stdout, os.Stderr
	return command.Run()
}

// main separates trusted-worker injection from the read-only actor-side probe.
func main() {
	role := flag.String("role", "retrieval", "owned specialist role")
	worker := flag.Bool("inject", false, "run the operator injector inside the observed trusted worker")
	runtimePath := flag.String("runsc", runscPath, "pinned runtime binary")
	state := flag.String("state-root", "", "observed owned actor state directory")
	flag.Parse()
	if *worker {
		if err := inject(*runtimePath, *state, *role); err != nil {
			fmt.Fprintln(os.Stderr, "The operator injection did not complete; preserve the native runsc receipt.")
			os.Exit(1)
		}
		return
	}
	report, err := collect(*role)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	_ = json.NewEncoder(os.Stdout).Encode(report)
	if !report.Passed {
		os.Exit(1)
	}
}
