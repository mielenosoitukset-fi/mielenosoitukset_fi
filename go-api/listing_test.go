package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

type fakeListingStore struct {
	fakeStore
	loads atomic.Int32
	fail  atomic.Bool
	gate  chan struct{}
}

func (s *fakeListingStore) FindListing(ctx context.Context, q listingQuery) (listingPage, error) {
	s.loads.Add(1)
	if _, ok := ctx.Deadline(); !ok {
		panic("missing listing deadline")
	}
	if s.gate != nil {
		select {
		case <-s.gate:
		case <-ctx.Done():
			return listingPage{}, ctx.Err()
		}
	}
	if s.fail.Load() {
		return listingPage{}, errors.New("mongodb://secret")
	}
	return listingPage{Page: q.Page, PerPage: q.PerPage, TotalPages: 1, Results: []bson.M{}}, nil
}

func TestListingHTTPContract(t *testing.T) {
	for _, raw := range []string{"lang=zz", "page=-1", "page=100000&per_page=100", "per_page=101", "date_start=2026-02-30", "date_start=2026-12-02&date_end=2026-12-01", "search=a&search=b", "search=" + strings.Repeat("a", 201), "include_private=true", "parent_id=invalid", "parent_id=000000000000000000000000", "organization_id=000000000000000000000000", "city=%00", "in_past=whatever"} {
		store := &fakeListingStore{}
		w := httptest.NewRecorder()
		(&API{store: store}).handler().ServeHTTP(w, httptest.NewRequest("GET", "/api/demonstrations?"+raw, nil))
		if w.Code != 400 || store.loads.Load() != 0 || w.Header().Get("Cache-Control") != "no-store" {
			t.Fatalf("invalid %q: %d %s", raw, w.Code, w.Body.String())
		}
	}
	store := &fakeListingStore{}
	for _, cookie := range []string{"", "session=admin"} {
		request := httptest.NewRequest("GET", "/api/demonstrations?per_page=12&lang=fi", nil)
		request.Header.Set("Cookie", cookie)
		w := httptest.NewRecorder()
		(&API{store: store}).handler().ServeHTTP(w, request)
		if w.Code != 200 || !strings.Contains(w.Body.String(), `"per_page":12`) || w.Header().Get("Cache-Control") != "public, max-age=30, s-maxage=30" {
			t.Fatalf("unexpected listing response: %d %s", w.Code, w.Body.String())
		}
	}
	store.fail.Store(true)
	w := httptest.NewRecorder()
	(&API{store: store}).handler().ServeHTTP(w, httptest.NewRequest("GET", "/api/demonstrations", nil))
	if w.Code != 503 || strings.Contains(w.Body.String(), "secret") || w.Header().Get("Cache-Control") != "no-store" {
		t.Fatalf("database failure: %d %s", w.Code, w.Body.String())
	}
}

func TestListingCacheCoalescingFreshnessAndBounds(t *testing.T) {
	ctx := context.Background()
	source := &fakeListingStore{gate: make(chan struct{})}
	cache := newListingCache(source, ctx)
	query, _ := parseListingQuery("per_page=12", time.Now())
	var wg sync.WaitGroup
	for range 8 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, stale, err := cache.get(ctx, query); err != nil || stale {
				t.Errorf("cold load: stale=%t err=%v", stale, err)
			}
		}()
	}
	for source.loads.Load() == 0 {
		time.Sleep(time.Millisecond)
	}
	close(source.gate)
	wg.Wait()
	if source.loads.Load() != 1 {
		t.Fatalf("concurrent pages loaded %d times", source.loads.Load())
	}
	now := time.Now()
	cache.now = func() time.Time { return now.Add(31 * time.Second) }
	source.fail.Store(true)
	if _, stale, err := cache.get(ctx, query); err != nil || !stale {
		t.Fatalf("failed refresh did not retain bounded stale result: stale=%t err=%v", stale, err)
	}
	if _, _, err := cache.get(ctx, query); err != nil || source.loads.Load() != 2 {
		t.Fatal("failure backoff did not avoid another database read")
	}
	cache.now = func() time.Time { return now.Add(121 * time.Second) }
	if _, _, err := cache.get(ctx, query); err == nil {
		t.Fatal("hard-expired public result was served")
	}
	source.fail.Store(false)
	for index := range 140 {
		query.Search = strconv.Itoa(index)
		if _, _, err := cache.get(ctx, query); err != nil {
			t.Fatal(err)
		}
	}
	if len(cache.items) > listingMaxItems || cache.bytes > listingMaxBytes {
		t.Fatalf("unbounded query cache: items=%d bytes=%d", len(cache.items), cache.bytes)
	}
	first, _ := parseListingQuery("city=Tampere,Helsinki,Helsinki&search=TEST&per_page=12", now)
	second, _ := parseListingQuery("per_page=12&search=test&city=helsinki,tampere", now)
	a, _ := json.Marshal(first)
	b, _ := json.Marshal(second)
	if string(a) != string(b) {
		t.Fatal("equivalent filters cannot share the query cache")
	}
}

func TestMongoListingVisibilityFiltersAndPagination(t *testing.T) {
	uri := os.Getenv("TEST_MONGODB_URI")
	if uri == "" {
		t.Skip("set TEST_MONGODB_URI for isolated MongoDB listing integration")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	client, err := mongo.Connect(options.Client().ApplyURI(uri))
	if err != nil {
		t.Fatal(err)
	}
	defer client.Disconnect(context.Background())
	db := ciMongoDB(t, client, "moso_go_listing_test_" + bson.NewObjectID().Hex(), "main")
	defer db.Drop(context.Background())
	store := mongoStore{db.Collection("demonstrations"), client}
	parent := bson.NewObjectID()
	first, second := bson.NewObjectID(), bson.NewObjectID()
	for _, doc := range []bson.M{
		{"_id": first, "approved": true, "date": "2026-12-01", "start_time": "10:00", "title": "Literal [test]", "city": "Helsinki", "parent": parent, "slug": "literal-test", "running_number": 123, "description": "PRIVATE-NOT-CARD", "cover_image": "https://example.test/cover.jpg", "translations": bson.M{"en": bson.M{"title": "Translated march", "tags": bson.A{"rights"}}}},
		{"_id": second, "approved": true, "date": "2026-12-01", "start_time": "11:00", "title": "Second", "city": "Tampere", "tags": bson.A{"[test]"}},
		{"_id": bson.NewObjectID(), "approved": true, "date": "2026-12-02", "title": "Cancelled", "cancelled": true},
		{"_id": bson.NewObjectID(), "approved": true, "date": "2026-12-02", "title": "Hidden", "hide": true},
		{"_id": bson.NewObjectID(), "approved": true, "date": "2026-12-02", "title": "Malformed hide", "hide": "false"},
		{"_id": bson.NewObjectID(), "approved": bson.A{true}, "date": "2026-12-02", "title": "Malformed approval"},
		{"_id": bson.NewObjectID(), "approved": true, "date": bson.A{"2026-12-02"}, "title": "Malformed date"},
		{"_id": bson.NewObjectID(), "approved": false, "date": "2026-12-02", "title": "Unapproved"},
	} {
		if _, err := store.collection.InsertOne(ctx, doc); err != nil {
			t.Fatal(err)
		}
	}
	read := func(raw string) listingPage {
		query, err := parseListingQuery(raw, time.Date(2026, 11, 1, 12, 0, 0, 0, time.UTC))
		if err != nil {
			t.Fatal(err)
		}
		page, err := store.FindListing(ctx, query)
		if err != nil {
			t.Fatal(err)
		}
		return page
	}
	page := read("per_page=1&page=2")
	if page.Total != 2 || page.TotalPages != 2 || page.ResultStart != 2 || page.ResultEnd != 2 || page.Results[0]["_id"] != second.Hex() {
		t.Fatalf("visibility or pagination lost: %+v", page)
	}
	for _, raw := range []string{"search=%5Btest%5D", "tag=%5Btest%5D", "search=translated", "tag=rights", "city=helsinki", "parent_id=" + parent.Hex(), "date_start=2026-12-01&date_end=2026-12-01&title=literal"} {
		if page := read(raw); page.Total < 1 || page.Total > 2 {
			t.Fatalf("filter %q: %+v", raw, page)
		}
	}
	translated := read("lang=en&search=translated")
	card := translated.Results[0]
	encoded, _ := json.Marshal(card)
	if card["title"] != "Translated march" || card["parent"] != parent.Hex() || card["date_display"] != "01.12.2026" || card["detail_url"] != "/demonstration/literal-test" || strings.Contains(string(encoded), "PRIVATE-NOT-CARD") {
		t.Fatalf("card projection/localization: %s", encoded)
	}
	if read("include_cancelled=true").Total != 3 || len(read("page=99").Results) != 0 {
		t.Fatal("cancelled inclusion or out-of-range page contract lost")
	}
}

// DATA-01: walking every page of a scope must return exactly Total rows, in the
// same order as one large page, with no duplicate and no gap, even when many
// rows share a date and start time or have no start time at all.
func TestMongoListingPagesWalkWithoutDuplicatesOrGaps(t *testing.T) {
	uri := os.Getenv("TEST_MONGODB_URI")
	if uri == "" {
		t.Skip("set TEST_MONGODB_URI for isolated MongoDB listing integration")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	client, err := mongo.Connect(options.Client().ApplyURI(uri))
	if err != nil {
		t.Fatal(err)
	}
	defer client.Disconnect(context.Background())
	db := ciMongoDB(t, client, "moso_go_walk_test_" + bson.NewObjectID().Hex(), "main")
	defer db.Drop(context.Background())
	store := mongoStore{db.Collection("demonstrations"), client}
	organization := bson.NewObjectID()
	for index := 0; index < 53; index++ {
		doc := bson.M{"_id": bson.NewObjectID(), "approved": true, "title": "Walk", "date": "2026-12-0" + strconv.Itoa(1+index%3),
			"city": []string{"Helsinki", "Tampere"}[index%2], "tags": bson.A{"walk"}, "cancelled": index%7 == 0}
		if index%4 != 0 {
			doc["start_time"] = "12:00"
		}
		if index%5 == 0 {
			doc["organizers"] = bson.A{bson.M{"organization_id": organization}}
		}
		if _, err := store.collection.InsertOne(ctx, doc); err != nil {
			t.Fatal(err)
		}
	}
	now := time.Date(2026, 11, 1, 12, 0, 0, 0, time.UTC)
	read := func(raw string) listingPage {
		query, err := parseListingQuery(raw, now)
		if err != nil {
			t.Fatal(err)
		}
		page, err := store.FindListing(ctx, query)
		if err != nil {
			t.Fatal(err)
		}
		return page
	}
	for _, scope := range []string{"", "city=Helsinki", "city=tampere", "tag=walk", "include_cancelled=true", "organization_id=" + organization.Hex(), "city=Helsinki&include_cancelled=true"} {
		whole := read(scope + "&per_page=100")
		if int64(len(whole.Results)) != whole.Total {
			t.Fatalf("%q: total %d but %d rows on one page", scope, whole.Total, len(whole.Results))
		}
		for _, size := range []int{1, 7, 10, 20} {
			var walked []string
			pages := read(scope + "&per_page=" + strconv.Itoa(size)).TotalPages
			for number := int64(1); number <= pages; number++ {
				page := read(scope + "&per_page=" + strconv.Itoa(size) + "&page=" + strconv.FormatInt(number, 10))
				if page.Total != whole.Total || page.TotalPages != pages {
					t.Fatalf("%q size %d page %d: total moved %d/%d", scope, size, number, page.Total, page.TotalPages)
				}
				if len(page.Results) > 0 && page.ResultStart != int64(len(walked))+1 || page.ResultEnd != page.ResultStart+int64(len(page.Results))-1 && len(page.Results) > 0 {
					t.Fatalf("%q size %d page %d: bad range %d-%d after %d rows", scope, size, number, page.ResultStart, page.ResultEnd, len(walked))
				}
				for _, row := range page.Results {
					walked = append(walked, row["_id"].(string))
				}
			}
			if len(walked) != len(whole.Results) {
				t.Fatalf("%q size %d: walked %d of %d", scope, size, len(walked), len(whole.Results))
			}
			for position, id := range walked {
				if id != whole.Results[position]["_id"] {
					t.Fatalf("%q size %d: order differs at %d (duplicate or gap)", scope, size, position)
				}
			}
		}
	}
	if past := read("per_page=10&page=99"); past.Total == 0 || len(past.Results) != 0 || past.ResultStart != 0 {
		t.Fatalf("out-of-range page contract: %+v", past)
	}
}
