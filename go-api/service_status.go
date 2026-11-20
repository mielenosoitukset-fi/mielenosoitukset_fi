package main

import (
	"context"
	"net/http"
	"sync"
	"time"
)

type statusService struct {
	Name    string `json:"name"`
	Status  string `json:"status"`
	Message string `json:"message"`
}

type serviceStatus struct {
	OverallOK bool            `json:"overall_ok"`
	Services  []statusService `json:"services"`
	UpdatedAt string          `json:"updated_at"`
	LatencyMS int64           `json:"latency_ms"`
}

// serviceStatusHandler answers GET /api/status. The database row is always
// there; extra rows (cache, file storage) come from statusChecksFromEnv and are
// off unless configured. Probes run together, each with a one-second deadline,
// at most once a minute per process, and never show their errors.
func serviceStatusHandler(ping func(context.Context) error, extra ...statusCheck) http.Handler {
	checks := append([]statusCheck{{"Tietokanta", "Yhteys kunnossa", ping}}, extra...)
	var mutex sync.Mutex
	var checked time.Time
	var services []statusService
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Robots-Tag", "noindex")
		started := time.Now()
		mutex.Lock()
		if checked.IsZero() || clock().Sub(checked) >= time.Minute {
			services = probeStatusChecks(checks)
			checked = clock()
		}
		current := append([]statusService(nil), services...)
		mutex.Unlock()
		overall := true
		for _, service := range current {
			overall = overall && service.Status == "ok"
		}
		result := serviceStatus{overall, current,
			time.Now().UTC().Format("02.01.2006 15:04:05 UTC"), time.Since(started).Milliseconds()}
		if r.Method == http.MethodHead {
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.WriteHeader(http.StatusOK)
			return
		}
		writeJSON(w, http.StatusOK, result)
	})
}

func probeStatusChecks(checks []statusCheck) []statusService {
	services := make([]statusService, len(checks))
	var wait sync.WaitGroup
	for index, check := range checks {
		wait.Go(func() {
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			err := check.probe(ctx)
			cancel()
			services[index] = statusService{check.name, "ok", check.okMessage}
			if err != nil {
				services[index].Status, services[index].Message = "err", "Ei vastaa"
			}
		})
	}
	wait.Wait()
	return services
}
