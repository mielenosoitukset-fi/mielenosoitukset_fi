package main

// Ports of the helpers in mielenosoitukset_fi/basic_routes.py that decide what
// counts as a suggested change: tag, route and organizer normalisation and the
// "same clock time" comparison. Behaviour, including its oddities, follows the
// Python source; testdata/suggestion_parity.json holds Flask's own answers.

import (
	"encoding/json"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"unicode"

	"go.mongodb.org/mongo-driver/v2/bson"
)

// ---------------------------------------------------------------- Python values

// pyTruthy follows Python truthiness for the BSON values a stored document holds.
func pyTruthy(value any) bool {
	switch typed := value.(type) {
	case nil:
		return false
	case bool:
		return typed
	case string:
		return typed != ""
	case int32:
		return typed != 0
	case int64:
		return typed != 0
	case int:
		return typed != 0
	case float64:
		return typed != 0
	case bson.A:
		return len(typed) > 0
	case []any:
		return len(typed) > 0
	case bson.D:
		return len(typed) > 0
	case bson.M:
		return len(typed) > 0
	case map[string]any:
		return len(typed) > 0
	}
	return true
}

// pyStr is str(value) for the same values.
func pyStr(value any) string {
	if text, ok := value.(string); ok {
		return text
	}
	return pyRepr(value)
}

// pyRepr is repr(value). Documents keep their stored key order (bson.D).
func pyRepr(value any) string {
	switch typed := value.(type) {
	case nil:
		return "None"
	case bool:
		if typed {
			return "True"
		}
		return "False"
	case string:
		return analyticsRepr(typed)
	case int32:
		return strconv.FormatInt(int64(typed), 10)
	case int64:
		return strconv.FormatInt(typed, 10)
	case int:
		return strconv.Itoa(typed)
	case float64:
		return analyticsNumber(json.Number(strconv.FormatFloat(typed, 'g', -1, 64)))
	case pyInt:
		return string(typed)
	case bson.ObjectID:
		return "ObjectId('" + typed.Hex() + "')"
	case bson.A:
		return pyList(typed)
	case []any:
		return pyList(typed)
	case []string:
		items := make([]any, len(typed))
		for index, item := range typed {
			items[index] = item
		}
		return pyList(items)
	case bson.D:
		parts := make([]string, len(typed))
		for index, item := range typed {
			parts[index] = analyticsRepr(item.Key) + ": " + pyRepr(item.Value)
		}
		return "{" + strings.Join(parts, ", ") + "}"
	case bson.M:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sortStrings(keys)
		parts := make([]string, len(keys))
		for index, key := range keys {
			parts[index] = analyticsRepr(key) + ": " + pyRepr(typed[key])
		}
		return "{" + strings.Join(parts, ", ") + "}"
	case pyTuple:
		parts := make([]string, len(typed))
		for index, item := range typed {
			parts[index] = pyRepr(item)
		}
		if len(parts) == 1 {
			return "(" + parts[0] + ",)"
		}
		return "(" + strings.Join(parts, ", ") + ")"
	}
	return "None"
}

func sortStrings(values []string) {
	for i := 1; i < len(values); i++ {
		for j := i; j > 0 && values[j] < values[j-1]; j-- {
			values[j], values[j-1] = values[j-1], values[j]
		}
	}
}

func pyList(items []any) string {
	parts := make([]string, len(items))
	for index, item := range items {
		parts[index] = pyRepr(item)
	}
	return "[" + strings.Join(parts, ", ") + "]"
}

type pyTuple []any

// pyInt is an integer literal kept as its decimal text.
type pyInt string

// ---------------------------------------------------------------- literal_eval

// pyLiteralList parses text as a Python literal list the way ast.literal_eval
// does for the values route fields hold: strings, numbers, None, True, False
// and nested lists and tuples. It reports false for anything else, which makes
// the caller fall back to splitting, as Flask does after a failed literal_eval.
func pyLiteralList(text string) ([]any, bool) {
	parser := &pyLiteralParser{text: text}
	parser.skip()
	if parser.peek() != '[' {
		return nil, false
	}
	value, ok := parser.value()
	if !ok {
		return nil, false
	}
	parser.skip()
	if parser.index != len(parser.text) {
		return nil, false
	}
	list, isList := value.([]any)
	return list, isList
}

type pyLiteralParser struct {
	text  string
	index int
}

func (p *pyLiteralParser) peek() byte {
	if p.index >= len(p.text) {
		return 0
	}
	return p.text[p.index]
}

func (p *pyLiteralParser) skip() {
	for p.index < len(p.text) {
		switch p.text[p.index] {
		case ' ', '\t', '\n', '\r', '\f', '\v':
			p.index++
		default:
			return
		}
	}
}

var pyNumber = regexp.MustCompile(`^[+-]?(?:\d+(?:_\d+)*)?(?:\.\d+(?:_\d+)*)?(?:[eE][+-]?\d+)?`)

func (p *pyLiteralParser) value() (any, bool) {
	p.skip()
	switch character := p.peek(); {
	case character == '[' || character == '(':
		return p.sequence(character)
	case character == '\'' || character == '"':
		return p.stringLiteral()
	case character == '-' || character == '+' || character >= '0' && character <= '9' || character == '.':
		match := pyNumber.FindString(p.text[p.index:])
		if match == "" || match == "+" || match == "-" || match == "." {
			return nil, false
		}
		p.index += len(match)
		clean := strings.ReplaceAll(match, "_", "")
		if strings.ContainsAny(clean, ".eE") {
			number, err := strconv.ParseFloat(clean, 64)
			if err != nil {
				return nil, false
			}
			return number, true
		}
		integer, ok := new(big.Int).SetString(strings.TrimPrefix(clean, "+"), 10)
		if !ok || (len(strings.TrimLeft(clean, "+-")) > 1 && strings.HasPrefix(strings.TrimLeft(clean, "+-"), "0")) {
			return nil, false
		}
		return pyInt(integer.String()), true
	}
	for _, word := range []struct {
		name  string
		value any
	}{{"None", nil}, {"True", true}, {"False", false}} {
		if strings.HasPrefix(p.text[p.index:], word.name) {
			p.index += len(word.name)
			return word.value, true
		}
	}
	return nil, false
}

func (p *pyLiteralParser) sequence(open byte) (any, bool) {
	closing := byte(']')
	if open == '(' {
		closing = ')'
	}
	p.index++
	items := []any{}
	trailingComma := false
	for {
		p.skip()
		if p.peek() == closing {
			p.index++
			break
		}
		item, ok := p.value()
		if !ok {
			return nil, false
		}
		items = append(items, item)
		trailingComma = false
		p.skip()
		if p.peek() == ',' {
			p.index++
			trailingComma = true
			continue
		}
		if p.peek() != closing {
			return nil, false
		}
	}
	if open == '(' {
		// A parenthesised single value without a comma is just that value.
		if len(items) == 1 && !trailingComma {
			return items[0], true
		}
		return pyTuple(items), true
	}
	return items, true
}

func (p *pyLiteralParser) stringLiteral() (any, bool) {
	var result strings.Builder
	matched := false
	// Adjacent string literals concatenate.
	for {
		p.skip()
		quote := p.peek()
		if quote != '\'' && quote != '"' {
			break
		}
		matched = true
		p.index++
		closed := false
		for p.index < len(p.text) {
			character := p.text[p.index]
			if character == quote {
				p.index++
				closed = true
				break
			}
			if character == '\n' {
				return nil, false
			}
			if character == '\\' && p.index+1 < len(p.text) {
				p.index++
				switch escape := p.text[p.index]; escape {
				case 'n':
					result.WriteByte('\n')
				case 't':
					result.WriteByte('\t')
				case 'r':
					result.WriteByte('\r')
				case '\\', '\'', '"':
					result.WriteByte(escape)
				case '\n':
				case 'x', 'u', 'U':
					width := map[byte]int{'x': 2, 'u': 4, 'U': 8}[escape]
					if p.index+width >= len(p.text) {
						return nil, false
					}
					code, err := strconv.ParseUint(p.text[p.index+1:p.index+1+width], 16, 32)
					if err != nil {
						return nil, false
					}
					result.WriteRune(rune(code))
					p.index += width
				default:
					result.WriteByte('\\')
					result.WriteByte(escape)
				}
				p.index++
				continue
			}
			result.WriteByte(character)
			p.index++
		}
		if !closed {
			return nil, false
		}
	}
	return result.String(), matched
}

// ---------------------------------------------------------------- tags, route

func normalizeTagValue(value any) string {
	if value == nil {
		return ""
	}
	return analyticsTrim(strings.TrimLeft(analyticsTrim(pyStr(value)), "#"))
}

func normalizeTagList(raw []any) []string {
	result := []string{}
	for _, tag := range raw {
		if cleaned := normalizeTagValue(tag); cleaned != "" {
			result = append(result, cleaned)
		}
	}
	return result
}

func normalizeTagText(text string) []string {
	parts := strings.Split(text, ",")
	raw := make([]any, len(parts))
	for index, part := range parts {
		raw[index] = part
	}
	return normalizeTagList(raw)
}

func routeItems(items []any) []string {
	result := []string{}
	for _, item := range items {
		if point := analyticsTrim(pyStr(item)); point != "" {
			result = append(result, point)
		}
	}
	return result
}

var routeSeparator = regexp.MustCompile(`[\n,]+`)

// normalizeRoutePoints is _normalize_route_points for a form string or a stored value.
func normalizeRoutePoints(raw any) []string {
	switch typed := raw.(type) {
	case nil:
		return []string{}
	case bson.A:
		return routeItems(typed)
	case []any:
		return routeItems(typed)
	}
	text := analyticsTrim(pyStr(raw))
	if text == "" {
		return []string{}
	}
	if parsed, ok := pyLiteralList(text); ok {
		return routeItems(parsed)
	}
	result := []string{}
	for _, point := range routeSeparator.Split(text, -1) {
		if point = analyticsTrim(point); point != "" {
			result = append(result, point)
		}
	}
	return result
}

// ---------------------------------------------------------------- organizers

type organizerEntry struct {
	Name, Email, Website string
}

func (e organizerEntry) document() bson.D {
	return bson.D{{Key: "name", Value: e.Name}, {Key: "email", Value: e.Email}, {Key: "website", Value: e.Website}}
}

func splitOrganizerEntries(raw string) []string {
	text := analyticsTrim(raw)
	if text == "" {
		return nil
	}
	chunks := []string{}
	if strings.Contains(text, "\n") {
		for _, chunk := range strings.Split(text, "\n") {
			if chunk = analyticsTrim(chunk); chunk != "" {
				chunks = append(chunks, chunk)
			}
		}
		return chunks
	}
	depth := 0
	var current strings.Builder
	for _, character := range text {
		switch character {
		case '(':
			depth++
		case ')':
			if depth > 0 {
				depth--
			}
		}
		if character == ',' && depth == 0 {
			chunks = append(chunks, current.String())
			current.Reset()
			continue
		}
		current.WriteRune(character)
	}
	chunks = append(chunks, current.String())
	result := []string{}
	for _, chunk := range chunks {
		if chunk = analyticsTrim(chunk); chunk != "" {
			result = append(result, chunk)
		}
	}
	return result
}

var organizerAngle = regexp.MustCompile(`^(.*?)\s*<([^<>]+)>$`)
var organizerDetailSeparator = func(r rune) bool { return r == ',' || r == ';' }

func hasWebsitePrefix(value string) bool {
	lower := strings.ToLower(value)
	return strings.HasPrefix(lower, "http://") || strings.HasPrefix(lower, "https://") || strings.HasPrefix(lower, "www.")
}

func parseOrganizerText(chunk string) (organizerEntry, bool) {
	chunk = analyticsTrim(chunk)
	if chunk == "" {
		return organizerEntry{}, false
	}
	name, details := chunk, ""
	if strings.HasSuffix(chunk, ")") {
		if open := strings.LastIndex(chunk, "("); open != -1 {
			if candidate := analyticsTrim(chunk[:open]); candidate != "" {
				name = candidate
				details = analyticsTrim(chunk[open+1 : len(chunk)-1])
			}
		}
	}
	if details == "" {
		if match := organizerAngle.FindStringSubmatch(name); match != nil && analyticsTrim(match[1]) != "" {
			name, details = analyticsTrim(match[1]), analyticsTrim(match[2])
		}
	}
	email, website := "", ""
	if details != "" {
		for _, part := range strings.FieldsFunc(details, organizerDetailSeparator) {
			part = analyticsTrim(part)
			if part == "" {
				continue
			}
			switch {
			case website == "" && (hasWebsitePrefix(part) || strings.Contains(part, "/")):
				website = part
			case strings.Contains(part, "@") && email == "":
				email = part
			case website == "":
				website = part
			}
		}
	}
	if email == "" {
		for _, token := range strings.FieldsFunc(name, unicode.IsSpace) {
			if strings.Contains(token, "@") && !strings.Contains(token, "/") {
				email = analyticsTrim(token)
				name = analyticsTrim(strings.ReplaceAll(name, token, ""))
				break
			}
		}
	}
	if website == "" {
		for _, token := range strings.FieldsFunc(name, unicode.IsSpace) {
			if hasWebsitePrefix(token) {
				website = analyticsTrim(token)
				name = analyticsTrim(strings.ReplaceAll(name, token, ""))
				break
			}
		}
	}
	name = analyticsTrim(strings.Join(strings.FieldsFunc(name, unicode.IsSpace), " "))
	if name == "" && email == "" && website == "" {
		return organizerEntry{}, false
	}
	return organizerEntry{name, email, website}, true
}

func normalizeOrganizerText(raw string) []organizerEntry {
	entries := []organizerEntry{}
	for _, chunk := range splitOrganizerEntries(raw) {
		if parsed, ok := parseOrganizerText(chunk); ok {
			entries = append(entries, parsed)
		}
	}
	return entries
}

// documentValue looks up a key in the document shapes the driver may return.
func documentValue(document any, key string) (any, bool) {
	switch typed := document.(type) {
	case bson.D:
		for _, item := range typed {
			if item.Key == key {
				return item.Value, true
			}
		}
	case bson.M:
		value, ok := typed[key]
		return value, ok
	case map[string]any:
		value, ok := typed[key]
		return value, ok
	}
	return nil, false
}

func isDocument(value any) bool {
	switch value.(type) {
	case bson.D, bson.M, map[string]any:
		return true
	}
	return false
}

// coerceOrganizer is _coerce_organizer_dict, reduced to the three fields that
// take part in comparison (stored metadata is deliberately ignored).
func coerceOrganizer(raw any) (organizerEntry, bool) {
	switch typed := raw.(type) {
	case nil:
		return organizerEntry{}, false
	case string:
		text := analyticsTrim(typed)
		if text == "" {
			return organizerEntry{}, false
		}
		return organizerEntry{Name: text}, true
	}
	if !isDocument(raw) {
		return organizerEntry{}, false
	}
	field := func(key string) string {
		value, _ := documentValue(raw, key)
		if !pyTruthy(value) {
			return ""
		}
		return analyticsTrim(pyStr(value))
	}
	entry := organizerEntry{field("name"), field("email"), field("website")}
	if entry == (organizerEntry{}) {
		return organizerEntry{}, false
	}
	return entry, true
}

// normalizeStoredOrganizers is _normalize_organizers for a stored value.
func normalizeStoredOrganizers(raw any) []organizerEntry {
	entries := []organizerEntry{}
	appendValue := func(item any) {
		if text, ok := item.(string); ok {
			entries = append(entries, normalizeOrganizerText(text)...)
		} else if entry, ok := coerceOrganizer(item); ok {
			entries = append(entries, entry)
		}
	}
	switch typed := raw.(type) {
	case nil:
	case string:
		entries = append(entries, normalizeOrganizerText(typed)...)
	case bson.A:
		for _, item := range typed {
			appendValue(item)
		}
	case []any:
		for _, item := range typed {
			appendValue(item)
		}
	default:
		if entry, ok := coerceOrganizer(raw); ok {
			entries = append(entries, entry)
		}
	}
	return entries
}

// ---------------------------------------------------------------- clock times

var (
	clockSeconds = regexp.MustCompile(`^(2[0-3]|[0-1]\d|\d):([0-5]\d|\d):(6[0-1]|[0-5]\d|\d)$`)
	clockMinutes = regexp.MustCompile(`^(2[0-3]|[0-1]\d|\d):([0-5]\d|\d)$`)
)

// parseClock is _parse_clock: strptime with %H:%M:%S, then %H:%M.
func parseClock(value string) (string, bool) {
	if match := clockSeconds.FindStringSubmatch(value); match != nil {
		hour, _ := strconv.Atoi(match[1])
		minute, _ := strconv.Atoi(match[2])
		second, _ := strconv.Atoi(match[3])
		if second <= 59 {
			return strconv.Itoa(hour*3600 + minute*60 + second), true
		}
	}
	if match := clockMinutes.FindStringSubmatch(value); match != nil {
		hour, _ := strconv.Atoi(match[1])
		minute, _ := strconv.Atoi(match[2])
		return strconv.Itoa(hour*3600 + minute*60), true
	}
	return "", false
}

// sameClock reports whether two stored/submitted times are equal for the
// purpose of "did the reporter change it": equal clock times or equal text.
func sameClock(a, b string) bool {
	left, leftOK := parseClock(a)
	right, rightOK := parseClock(b)
	if leftOK != rightOK {
		return false
	}
	if leftOK {
		return left == right
	}
	return a == b
}

func lowerASCII(value string) string { return strings.ToLower(value) }
