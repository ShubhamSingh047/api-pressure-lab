package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestCLIProducesJSONAndThresholdExit(t *testing.T) {
	for _, status := range []int{200, 503} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(status) }))
			defer s.Close()
			var out, errout bytes.Buffer
			code := run(context.Background(), []string{"--url", s.URL, "--rates", "10", "--duration", "200ms"}, &out, &errout)
			want := 0
			if status == 503 {
				want = 2
			}
			if code != want {
				t.Fatalf("code %d: %s", code, errout.String())
			}
			var data map[string]any
			if err := json.Unmarshal(out.Bytes(), &data); err != nil {
				t.Fatalf("invalid JSON: %s", out.String())
			}
			if data["schema_version"] != float64(1) {
				t.Fatal("missing schema version")
			}
		})
	}
}

func TestCLIRejectsMalformedArguments(t *testing.T) {
	for _, args := range [][]string{{}, {"--url", "http://localhost", "--rates", "abc"}, {"--url", "https://example.com"}, {"--url", "http://localhost", "unexpected"}} {
		var out, errout bytes.Buffer
		if code := run(context.Background(), args, &out, &errout); code != 1 {
			t.Fatalf("accepted args %v, code %d", args, code)
		}
	}
}

// CLI formatting (2024-04-14 18:48:56): terminal summary and json flag alignment
