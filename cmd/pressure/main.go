package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/netip"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"time"

	"github.com/ShubhamSingh047/api-pressure-lab/internal/loadtest"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	os.Exit(run(ctx, os.Args[1:], os.Stdout, os.Stderr))
}

func run(ctx context.Context, args []string, out, errout io.Writer) int {
	flags := flag.NewFlagSet("pressure", flag.ContinueOnError)
	flags.SetOutput(errout)
	var c loadtest.Config
	flags.StringVar(&c.URL, "url", "", "HTTP(S) GET endpoint to test")
	rates := flags.String("rates", "10,25,50", "strictly ascending requests/second per stage")
	flags.DurationVar(&c.Duration, "duration", 10*time.Second, "duration of each stage")
	flags.IntVar(&c.Workers, "workers", 32, "maximum in-flight requests")
	flags.DurationVar(&c.Timeout, "timeout", 2*time.Second, "per-request timeout including body")
	flags.DurationVar(&c.P95Limit, "p95", 250*time.Millisecond, "maximum p95 latency")
	flags.Float64Var(&c.MaxErrorRate, "max-error-rate", .01, "maximum failed fraction, 0..1")
	output := flags.String("output", "-", "JSON output path or - for stdout")
	authorized := flags.Bool("authorized", false, "confirm permission to load-test a non-loopback target")
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		return 1
	}
	fail := func(err error) int { fmt.Fprintln(errout, err); return 1 }
	if flags.NArg() != 0 {
		return fail(fmt.Errorf("unexpected positional arguments"))
	}
	u, err := url.Parse(c.URL)
	if err != nil || u.Hostname() == "" {
		return fail(fmt.Errorf("--url must be a valid HTTP(S) endpoint"))
	}
	ip, iperr := netip.ParseAddr(u.Hostname())
	loopback := strings.EqualFold(u.Hostname(), "localhost") || (iperr == nil && ip.IsLoopback())
	if !loopback && !*authorized {
		return fail(fmt.Errorf("non-loopback targets require --authorized; test only systems you own or have permission to test"))
	}
	for _, part := range strings.Split(*rates, ",") {
		n, err := strconv.Atoi(strings.TrimSpace(part))
		if err != nil {
			return fail(fmt.Errorf("invalid rate %q", part))
		}
		c.Rates = append(c.Rates, n)
	}
	result, err := loadtest.Run(ctx, c)
	if err != nil {
		return fail(err)
	}
	data, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return fail(err)
	}
	data = append(data, '\n')
	if *output == "-" {
		_, err = out.Write(data)
	} else {
		err = os.WriteFile(*output, data, 0600)
	}
	if err != nil {
		return fail(err)
	}
	fmt.Fprintf(errout, "Highest passing tested rate: %d requests/s; %s\n", result.HighestPassingRPS, result.StopReason)
	if result.StopReason == "interrupted" {
		return 130
	}
	if result.StopReason != "all_stages_passed" {
		return 2
	}
	return 0
}

// CLI formatting (2023-12-31 21:10:54): terminal summary and json flag alignment
