package loadtest

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func config(url string) Config {
	return Config{URL: url, Rates: []int{20}, Duration: 200 * time.Millisecond, Workers: 4, Timeout: time.Second, P95Limit: time.Second, MaxErrorRate: 0}
}

func TestNearestRank(t *testing.T) {
	if got := percentile([]float64{4, 1, 3, 2}, .95); got != 4 {
		t.Fatalf("p95 = %v, want 4", got)
	}
	if got := percentile([]float64{4, 1, 3, 2}, .5); got != 2 {
		t.Fatalf("p50 = %v, want 2", got)
	}
}

func TestPacedStageAccounting(t *testing.T) {
	var calls atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); w.Write([]byte("ok")) }))
	defer s.Close()
	r, err := Run(context.Background(), config(s.URL+"/?token=secret"))
	if err != nil {
		t.Fatal(err)
	}
	a := r.Stages[0]
	if a.Scheduled != 4 || a.Completed != 4 || a.Dropped != 0 || !a.Passed || r.HighestPassingRPS != 20 {
		t.Fatalf("bad stage: %+v", a)
	}
	if calls.Load() != 4 {
		t.Fatalf("calls %d", calls.Load())
	}
	if strings.Contains(r.Target, "secret") {
		t.Fatal("target leaks query")
	}
	if a.ElapsedSeconds < .19 {
		t.Fatal("requests were sent as a burst")
	}
}

func TestStopOnHTTPFailure(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(503) }))
	defer s.Close()
	c := config(s.URL)
	c.Rates = []int{20, 40}
	r, err := Run(context.Background(), c)
	if err != nil {
		t.Fatal(err)
	}
	if len(r.Stages) != 1 || r.Stages[0].Failed != 4 || r.HighestPassingRPS != 0 || r.StopReason != "threshold_exceeded" {
		t.Fatalf("unexpected result %+v", r)
	}
}

func TestSaturatedGeneratorNeverPasses(t *testing.T) {
	var active, peak atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := active.Add(1)
		defer active.Add(-1)
		for p := peak.Load(); n > p; p = peak.Load() {
			if peak.CompareAndSwap(p, n) {
				break
			}
		}
		time.Sleep(100 * time.Millisecond)
		w.Write([]byte("ok"))
	}))
	defer s.Close()
	c := config(s.URL)
	c.Rates = []int{100}
	c.Workers = 1
	r, err := Run(context.Background(), c)
	if err != nil {
		t.Fatal(err)
	}
	a := r.Stages[0]
	if a.Dropped == 0 || a.Passed || a.Completed+a.Dropped != a.Scheduled || peak.Load() != 1 {
		t.Fatalf("bad saturation result %+v, peak %d", a, peak.Load())
	}
}

func TestTimeoutAndOversizedBodyCountAsFailures(t *testing.T) {
	for _, slow := range []bool{true, false} {
		t.Run(map[bool]string{true: "timeout", false: "body"}[slow], func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if slow {
					time.Sleep(50 * time.Millisecond)
				} else {
					w.Write([]byte(strings.Repeat("x", (1<<20)+1)))
				}
			}))
			defer s.Close()
			c := config(s.URL)
			if slow {
				c.Timeout = 10 * time.Millisecond
			}
			r, err := Run(context.Background(), c)
			if err != nil {
				t.Fatal(err)
			}
			if r.Stages[0].Failed != r.Stages[0].Completed || r.Stages[0].Passed {
				t.Fatalf("failures not counted: %+v", r)
			}
		})
	}
}

func TestRedirectNotFollowed(t *testing.T) {
	var reached atomic.Bool
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { reached.Store(true) }))
	defer target.Close()
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, 302) }))
	defer s.Close()
	r, err := Run(context.Background(), config(s.URL))
	if err != nil {
		t.Fatal(err)
	}
	if reached.Load() || r.Stages[0].Passed {
		t.Fatal("redirect followed or marked success")
	}
}

func TestCancellationCannotPass(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { <-r.Context().Done() }))
	defer s.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 40*time.Millisecond)
	defer cancel()
	r, err := Run(ctx, config(s.URL))
	if err != nil {
		t.Fatal(err)
	}
	if r.StopReason != "interrupted" || r.HighestPassingRPS != 0 || r.Stages[0].Passed {
		t.Fatalf("bad cancellation %+v", r)
	}
}

func TestInvalidConfig(t *testing.T) {
	cases := []func(*Config){func(c *Config) { c.Rates = []int{0} }, func(c *Config) { c.Rates = []int{20, 10} }, func(c *Config) { c.Workers = 0 }, func(c *Config) { c.Duration = 0 }, func(c *Config) { c.Timeout = 0 }, func(c *Config) { c.MaxErrorRate = 2 }, func(c *Config) { c.URL = "file:///tmp/a" }, func(c *Config) { c.URL = "http://user:pass@example.com" }, func(c *Config) { c.Duration = time.Hour; c.Rates = []int{10000} }}
	for i, modify := range cases {
		c := config("http://localhost")
		modify(&c)
		if _, err := Run(context.Background(), c); err == nil {
			t.Errorf("case %d accepted", i)
		}
	}
}

func TestSlowSuccessfulResponsesFailLatencyBudget(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(25 * time.Millisecond)
		w.Write([]byte("ok"))
	}))
	defer s.Close()
	c := config(s.URL)
	c.P95Limit = time.Millisecond
	r, err := Run(context.Background(), c)
	if err != nil {
		t.Fatal(err)
	}
	if r.Stages[0].Failed != 0 || r.Stages[0].Passed || r.StopReason != "threshold_exceeded" {
		t.Fatalf("latency budget ignored: %+v", r)
	}
}

// Metric tuning (2023-09-18 23:13:36): nearest-rank percentile validation
