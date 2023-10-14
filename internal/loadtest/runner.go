// Package loadtest runs bounded, open-loop HTTP capacity experiments.
package loadtest

import (
	"context"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"runtime"
	"sort"
	"sync"
	"time"
)

type Config struct {
	URL          string
	Rates        []int
	Duration     time.Duration
	Workers      int
	Timeout      time.Duration
	P95Limit     time.Duration
	MaxErrorRate float64
}

type Thresholds struct {
	P95MS        float64 `json:"p95_ms"`
	MaxErrorRate float64 `json:"max_error_rate"`
}
type Stage struct {
	OfferedRPS      int         `json:"offered_rps"`
	DurationSeconds float64     `json:"duration_seconds"`
	ElapsedSeconds  float64     `json:"elapsed_seconds"`
	Scheduled       int         `json:"scheduled"`
	Completed       int         `json:"completed"`
	Failed          int         `json:"failed"`
	Dropped         int         `json:"dropped"`
	AchievedRPS     float64     `json:"achieved_rps"`
	ErrorRate       float64     `json:"error_rate"`
	P50MS           float64     `json:"p50_ms"`
	P95MS           float64     `json:"p95_ms"`
	P99MS           float64     `json:"p99_ms"`
	Passed          bool        `json:"passed"`
	StatusCodes     map[int]int `json:"status_codes"`
}
type Result struct {
	SchemaVersion     int            `json:"schema_version"`
	StartedAt         string         `json:"started_at"`
	Target            string         `json:"target"`
	Environment       map[string]any `json:"environment"`
	Thresholds        Thresholds     `json:"thresholds"`
	Stages            []Stage        `json:"stages"`
	HighestPassingRPS int            `json:"highest_passing_rps"`
	StopReason        string         `json:"stop_reason"`
}

func (c Config) validate() error {
	u, err := url.Parse(c.URL)
	if err != nil || u.Hostname() == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil || u.Fragment != "" {
		return fmt.Errorf("URL must be HTTP(S), without credentials or fragment")
	}
	if c.Duration <= 0 || c.Duration > time.Hour || c.Timeout <= 0 || c.Timeout > time.Minute || c.P95Limit <= 0 {
		return fmt.Errorf("duration must be (0,1h], timeout (0,1m], and p95 limit positive")
	}
	if c.Workers < 1 || c.Workers > 1024 || math.IsNaN(c.MaxErrorRate) || math.IsInf(c.MaxErrorRate, 0) || c.MaxErrorRate < 0 || c.MaxErrorRate > 1 {
		return fmt.Errorf("workers must be 1..1024 and error rate 0..1")
	}
	if len(c.Rates) == 0 || len(c.Rates) > 100 {
		return fmt.Errorf("provide 1..100 ascending rates")
	}
	previous := 0
	for _, r := range c.Rates {
		count := math.Ceil(float64(r) * c.Duration.Seconds())
		if r <= previous || r > 100000 || count > 1000000 {
			return fmt.Errorf("rates must increase, be 1..100000, and schedule at most 1000000 requests per stage")
		}
		previous = r
	}
	return nil
}

func percentile(values []float64, q float64) float64 {
	if len(values) == 0 {
		return 0
	}
	sorted := append([]float64(nil), values...)
	sort.Float64s(sorted)
	return sorted[int(math.Ceil(q*float64(len(sorted))))-1]
}

func Run(ctx context.Context, c Config) (Result, error) {
	if err := c.validate(); err != nil {
		return Result{}, err
	}
	u, _ := url.Parse(c.URL)
	u.RawQuery = ""
	u.ForceQuery = false
	result := Result{SchemaVersion: 1, StartedAt: time.Now().UTC().Format(time.RFC3339Nano), Target: u.String(), Environment: map[string]any{"os": runtime.GOOS, "arch": runtime.GOARCH, "cpus": runtime.NumCPU(), "go": runtime.Version(), "workers": c.Workers, "timeout_ms": float64(c.Timeout) / float64(time.Millisecond)}, Thresholds: Thresholds{float64(c.P95Limit) / float64(time.Millisecond), c.MaxErrorRate}, Stages: []Stage{}, StopReason: "all_stages_passed"}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.MaxIdleConns = c.Workers
	transport.MaxIdleConnsPerHost = c.Workers
	transport.MaxConnsPerHost = c.Workers
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: c.Timeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	for _, r := range c.Rates {
		stage := runStage(ctx, client, c, r)
		result.Stages = append(result.Stages, stage)
		if ctx.Err() != nil {
			result.StopReason = "interrupted"
			break
		}
		if !stage.Passed {
			if stage.Dropped > 0 {
				result.StopReason = "generator_limited"
			} else {
				result.StopReason = "threshold_exceeded"
			}
			break
		}
		result.HighestPassingRPS = r
	}
	return result, nil
}

func runStage(ctx context.Context, client *http.Client, c Config, rate int) Stage {
	stage := Stage{OfferedRPS: rate, DurationSeconds: c.Duration.Seconds(), StatusCodes: map[int]int{}}
	count := int(math.Ceil(float64(rate) * c.Duration.Seconds()))
	latencies := make([]float64, 0, count)
	slots := make(chan struct{}, c.Workers)
	var wg sync.WaitGroup
	var mu sync.Mutex
	start := time.Now()
	// Due times are relative to stage start, avoiding cumulative ticker drift.
	for i := 0; i < count; i++ {
		due := start.Add(time.Duration(float64(i) * float64(time.Second) / float64(rate)))
		if !waitUntil(ctx, due) {
			break
		}
		stage.Scheduled++
		if time.Since(due) >= time.Duration(float64(time.Second)/float64(rate)) {
			stage.Dropped++
			continue
		}
		select {
		case slots <- struct{}{}:
			wg.Add(1)
			go func() {
				defer wg.Done()
				defer func() { <-slots }()
				began := time.Now()
				status := 0
				failed := false
				req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.URL, nil)
				if err != nil {
					failed = true
				} else {
					resp, err := client.Do(req)
					if err != nil {
						failed = true
					} else {
						status = resp.StatusCode
						n, readErr := io.Copy(io.Discard, io.LimitReader(resp.Body, (1<<20)+1))
						resp.Body.Close()
						failed = status < 200 || status >= 300 || readErr != nil || n > 1<<20
					}
				}
				latency := float64(time.Since(began)) / float64(time.Millisecond)
				mu.Lock()
				defer mu.Unlock()
				stage.Completed++
				if failed {
					stage.Failed++
				}
				stage.StatusCodes[status]++
				latencies = append(latencies, latency)
			}()
		default:
			stage.Dropped++
		}
	}
	waitUntil(ctx, start.Add(c.Duration))
	wg.Wait()
	stage.ElapsedSeconds = time.Since(start).Seconds()
	stage.AchievedRPS = float64(stage.Completed) / stage.ElapsedSeconds
	if stage.Completed > 0 {
		stage.ErrorRate = float64(stage.Failed) / float64(stage.Completed)
	}
	stage.P50MS = percentile(latencies, .50)
	stage.P95MS = percentile(latencies, .95)
	stage.P99MS = percentile(latencies, .99)
	stage.Passed = ctx.Err() == nil && stage.Completed > 0 && stage.Dropped == 0 && stage.ErrorRate <= c.MaxErrorRate && stage.P95MS <= float64(c.P95Limit)/float64(time.Millisecond)
	return stage
}

func waitUntil(ctx context.Context, deadline time.Time) bool {
	if ctx.Err() != nil {
		return false
	}
	duration := time.Until(deadline)
	if duration <= 0 {
		return true
	}
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return ctx.Err() == nil
	}
}

// Metric tuning (2023-10-14 14:56:03): nearest-rank percentile validation
