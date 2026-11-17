# DATA-01: public pagination and totals

Checked on 17 November 2026 (Helsinki time, about 09:40 to 10:30). Public HTTPS GET
requests only, about 2 requests per second, no admin access. The production database
was briefly overloaded by a backup dump at the start (503 from both hosts); probing
resumed once it recovered, and one transient 503 later in the run was retried.

## Endpoints and pages

| Page (React) | Go API request |
| --- | --- |
| `/demonstrations` (all) | `GET /api/demonstrations` |
| `/city/{city}` | `city=` |
| `/tag/{tag}` | `tag=` |
| `/mielenosoitukset-tanaan`, `/city/{city}/tanaan` | `today=true` (+ `city=`) |
| organization page | `organization_id=` |
| series page | `parent_id=` |

All of them use the one handler `getListing` in `go-api/listing.go` and one React
component, `PublicListingPage`. Order is `date`, `start_time`, `_id`, so ties are stable.

## Production results (`api.mielenosoitukset.fi`)

For each scope the whole result was read with `per_page=12` and `per_page=100`.

| Scope | Total | Rows over all pages | Duplicates | Order | Page after last |
| --- | --- | --- | --- | --- | --- |
| all upcoming | 243 | 243 | 0 | sorted | 0 rows, total kept |
| city Helsinki | 243 | 243 | 0 | sorted | 0 rows |
| tag ilmastonmuutos (case-insensitive) | 140 | 140 | 0 | sorted | 0 rows |
| tag palestiina | 97 | 97 | 0 | sorted | 0 rows |
| tag luontokato | 12 | 12 | 0 | sorted | 0 rows |
| series (two parents) | 104 and 104 | 104 and 104 | 0 | sorted | 0 rows |
| `include_cancelled=true` | 251 | 251 | 0 | sorted | 0 rows |
| past and upcoming, Helsinki | 440 | 440 | 0 | sorted | 0 rows |

- Page sizes 12 and 100 return the same ids in the same order.
- `result_start` follows the previous page's end on every page.
- Cancelled events are excluded from upcoming lists (243 against 251). Series and
  organization lists include them on purpose; one series has 8 cancelled children,
  which is why its total is 104 and not 96.
- Tag, city and city-with-tag totals agree with `/api/cities` upcoming counts.
- Today lists were empty on the day, so only the empty state was checked.
- The production data is almost all Helsinki, so other cities (Tampere, Oulu, Turku)
  have no upcoming events to page through; they correctly report total 0.

## Browser (production, built-in browser pane)

`/city/helsinki`: first view says "1–12 (yhteensä 243)". Load-more was pressed 20
times: the window holds the last five pages (17 to 21), 51 cards, no duplicate cards,
summary "193–243 (yhteensä 243)", load-more hidden, no empty state. The URL ends at
`page=21`.

## Bug found and fixed

A link with a page number past the last page (a bookmark, or events that have since
passed) returned 200 with zero rows and the real total. React showed the "no results"
card although the total was above zero. `PublicListingPage` now fetches the last page
in that case, both for server-rendered initial data and for client requests.

Tests:

- `go-api/listing_test.go`: `TestMongoListingPagesWalkWithoutDuplicatesOrGaps` walks every
  page at sizes 1, 7, 10 and 20 over mixed dates, missing start times, cancelled events and
  organization filters (needs `TEST_MONGODB_URI`).
- `tests/test_react_listing_pagination.py`: load-more reaches the exact total and hides
  at the end; a page past the end shows the last page.

## Not covered

- No load or timing measurement.
- Today lists with real events; the legacy `controller.tsx` widget (not served by the
  production React host).
- After a long session, the summary total comes from the first loaded page; if events
  change between loads it can lag by a few rows until the next reload.
