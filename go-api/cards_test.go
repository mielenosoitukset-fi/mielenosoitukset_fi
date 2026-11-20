package main

import "testing"

func TestCardDateRange(t *testing.T) {
	cases := []struct {
		start, end any
		want       string
	}{
		{"2026-12-01", nil, "01.12.2026"},
		{"2026-12-01", "", "01.12.2026"},
		{"2026-12-01", "2026-12-01", "01.12.2026"},
		{"2026-12-01", "2026-11-30", "01.12.2026"},
		{"2026-12-01", "bad", "01.12.2026"},
		{"2026-12-01", "2026-12-03", "01.12.–03.12.2026"},
		{"2026-12-30", "2027-01-02", "30.12.2026–02.01.2027"},
	}
	for _, c := range cases {
		got, ok := cardDateRange(c.start, c.end)
		if !ok || got != c.want {
			t.Errorf("cardDateRange(%v, %v) = %q, %v; want %q", c.start, c.end, got, ok, c.want)
		}
	}
}

func TestOverlapDates(t *testing.T) {
	dates, running := overlapDates("2026-12-20", "2026-12-21")
	if dates["$gte"] != "2026-12-07" || dates["$lte"] != "2026-12-21" {
		t.Fatalf("date bounds = %v", dates)
	}
	if running == nil {
		t.Fatal("expected a running clause")
	}
	dates, running = overlapDates("", "2026-12-21")
	if running != nil || dates["$gte"] != nil || dates["$lte"] != "2026-12-21" {
		t.Fatalf("open start = %v %v", dates, running)
	}
}
