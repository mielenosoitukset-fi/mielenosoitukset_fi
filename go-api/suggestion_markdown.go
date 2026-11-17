package main

// Port of mielenosoitukset_fi/utils/content_formatting.markdown_to_html: the
// public suggestion forms turn the reporter's Markdown into the same safe HTML
// subset Flask stores. Flask runs Python-Markdown 3.7 (sane_lists, nl2br) over
// html.escape()d text and then keeps only an allowlist of tags, so no raw HTML
// or attribute can survive. This file reproduces the block processors and the
// inline patterns for that pipeline; testdata/suggestion_parity.json (written
// by tests/test_go_suggestion_parity.py from the real Flask code) is the
// acceptance corpus. Known gaps are listed in docs/go-public-api.md.

import (
	"fmt"
	"html"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

type mdNode struct {
	tag, text, tail string
	href            string
	hasHref         bool
	atomic          bool
	kids            []*mdNode
}

func (n *mdNode) last() *mdNode {
	if len(n.kids) == 0 {
		return nil
	}
	return n.kids[len(n.kids)-1]
}

func (n *mdNode) add(tag string) *mdNode {
	child := &mdNode{tag: tag}
	n.kids = append(n.kids, child)
	return child
}

var (
	mdHashHeader   = regexp.MustCompile(`(?:^|\n)(#{1,6})((?:\\.|[^\\])*?)#*(?:\n|$)`)
	mdSetext       = regexp.MustCompile(`(?m)\A.*?\n[=-]+[ ]*(?:\n|$)`)
	mdRule         = regexp.MustCompile(`(?m)^[ ]{0,3}(?:(?:-+[ ]{0,2}){3,}|(?:_+[ ]{0,2}){3,}|(?:\*+[ ]{0,2}){3,})[ ]*$`)
	mdOrderedStart = regexp.MustCompile(`^[ ]{0,3}\d+\.[ ]+`)
	mdBulletStart  = regexp.MustCompile(`^[ ]{0,3}[*+-][ ]+`)
	mdOrderedChild = regexp.MustCompile(`^[ ]{0,3}(\d+\.)[ ]+(.*)`)
	mdBulletChild  = regexp.MustCompile(`^[ ]{0,3}([*+-])[ ]+(.*)`)
	mdListIndented = regexp.MustCompile(`^[ ]{4,7}(?:(?:\d+\.)|[*+-])[ ]+.*`)
	mdIndent       = regexp.MustCompile(`^((?: {4})+)`)
	mdReference    = regexp.MustCompile(`(?m)^[ ]{0,3}\[([^\[\]]*)\]:[ ]*\n?[ ]*([^\s]+)[ ]*(?:\n[ ]*)?(?:\((.*)\)[ ]*)?$`)
)

var mdEscapeAll = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", "\"", "&quot;", "'", "&#x27;")

func mdCodeEscape(text string) string {
	return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;").Replace(text)
}

type mdParser struct {
	state        []string
	refs         map[string]string
	placeholders map[string]any // placeholder -> string literal or *mdNode
	counter      int
}

func (p *mdParser) isState(name string) bool {
	return len(p.state) > 0 && p.state[len(p.state)-1] == name
}
func (p *mdParser) set(name string) { p.state = append(p.state, name) }
func (p *mdParser) reset() {
	if len(p.state) > 0 {
		p.state = p.state[:len(p.state)-1]
	}
}

func isList(tag string) bool { return tag == "ul" || tag == "ol" }

func mdDetab(text string) (string, string) {
	lines := strings.Split(text, "\n")
	out := []string{}
	for _, line := range lines {
		if strings.HasPrefix(line, "    ") {
			out = append(out, line[4:])
		} else if strings.TrimSpace(line) == "" {
			out = append(out, "")
		} else {
			break
		}
	}
	return strings.Join(out, "\n"), strings.Join(lines[len(out):], "\n")
}

func mdLooseDetab(text string, level int) string {
	prefix := strings.Repeat(" ", 4*level)
	lines := strings.Split(text, "\n")
	for index, line := range lines {
		if strings.HasPrefix(line, prefix) {
			lines[index] = line[len(prefix):]
		}
	}
	return strings.Join(lines, "\n")
}

func (p *mdParser) parseChunk(parent *mdNode, text string) {
	blocks := strings.Split(text, "\n\n")
	p.parseBlocks(parent, &blocks)
}

func (p *mdParser) parseBlocks(parent *mdNode, blocks *[]string) {
	for len(*blocks) > 0 {
		block := (*blocks)[0]
		for _, processor := range p.processors() {
			if processor.test(parent, block) {
				if processor.run(parent, blocks) {
					break
				}
			}
		}
	}
}

type mdProcessor struct {
	test func(parent *mdNode, block string) bool
	run  func(parent *mdNode, blocks *[]string) bool
}

func (p *mdParser) processors() []mdProcessor {
	return []mdProcessor{
		{func(_ *mdNode, b string) bool { return b == "" || strings.HasPrefix(b, "\n") }, p.runEmpty},
		{func(parent *mdNode, b string) bool {
			if !strings.HasPrefix(b, "    ") || p.isState("detabbed") {
				return false
			}
			return parent.tag == "li" || (len(parent.kids) > 0 && isList(parent.last().tag))
		}, p.runIndent},
		{func(_ *mdNode, b string) bool { return strings.HasPrefix(b, "    ") }, p.runCode},
		{func(_ *mdNode, b string) bool { return mdHashHeader.MatchString(b) }, p.runHash},
		{func(_ *mdNode, b string) bool { return mdSetext.MatchString(b) }, p.runSetext},
		{func(_ *mdNode, b string) bool { return mdRule.MatchString(b) }, p.runRule},
		{func(_ *mdNode, b string) bool { return mdOrderedStart.MatchString(b) }, func(parent *mdNode, blocks *[]string) bool { return p.runList(parent, blocks, "ol") }},
		{func(_ *mdNode, b string) bool { return mdBulletStart.MatchString(b) }, func(parent *mdNode, blocks *[]string) bool { return p.runList(parent, blocks, "ul") }},
		{func(_ *mdNode, _ string) bool { return true }, p.runReference},
		{func(_ *mdNode, _ string) bool { return true }, p.runParagraph},
	}
}

func (p *mdParser) runEmpty(parent *mdNode, blocks *[]string) bool {
	block := (*blocks)[0]
	*blocks = (*blocks)[1:]
	filler := "\n\n"
	if block != "" {
		filler = "\n"
		if rest := block[1:]; rest != "" {
			*blocks = append([]string{rest}, *blocks...)
		}
	}
	if sibling := parent.last(); sibling != nil && sibling.tag == "pre" && len(sibling.kids) > 0 && sibling.kids[0].tag == "code" {
		sibling.kids[0].text += filler
	}
	return true
}

func (p *mdParser) indentLevel(parent *mdNode, block string) (int, *mdNode) {
	indent := 0
	if match := mdIndent.FindStringSubmatch(block); match != nil {
		indent = len(match[1]) / 4
	}
	level := 0
	if p.isState("list") {
		level = 1
	}
	for indent > level {
		child := parent.last()
		if child != nil && (isList(child.tag) || child.tag == "li") {
			if isList(child.tag) {
				level++
			}
			parent = child
		} else {
			break
		}
	}
	return level, parent
}

func (p *mdParser) runIndent(parent *mdNode, blocks *[]string) bool {
	block := (*blocks)[0]
	*blocks = (*blocks)[1:]
	level, sibling := p.indentLevel(parent, block)
	block = mdLooseDetab(block, level)
	p.set("detabbed")
	switch {
	case parent.tag == "li":
		if len(parent.kids) > 0 && isList(parent.last().tag) {
			p.parseBlocks(parent.last(), &[]string{block})
		} else {
			p.parseBlocks(parent, &[]string{block})
		}
	case sibling.tag == "li":
		p.parseBlocks(sibling, &[]string{block})
	case len(sibling.kids) > 0 && sibling.last().tag == "li":
		item := sibling.last()
		if item.text != "" {
			paragraph := &mdNode{tag: "p", text: item.text}
			item.text = ""
			item.kids = append([]*mdNode{paragraph}, item.kids...)
		}
		p.parseChunk(item, block)
	default:
		item := sibling.add("li")
		p.parseBlocks(item, &[]string{block})
	}
	p.reset()
	return true
}

func (p *mdParser) runCode(parent *mdNode, blocks *[]string) bool {
	sibling := parent.last()
	block := (*blocks)[0]
	*blocks = (*blocks)[1:]
	var rest string
	if sibling != nil && sibling.tag == "pre" && len(sibling.kids) > 0 && sibling.kids[0].tag == "code" {
		code := sibling.kids[0]
		block, rest = mdDetab(block)
		code.text = code.text + "\n" + mdCodeEscape(strings.TrimRightFunc(block, unicode.IsSpace)) + "\n"
	} else {
		pre := parent.add("pre")
		code := pre.add("code")
		code.atomic = true
		block, rest = mdDetab(block)
		code.text = mdCodeEscape(strings.TrimRightFunc(block, unicode.IsSpace)) + "\n"
	}
	if rest != "" {
		*blocks = append([]string{rest}, *blocks...)
	}
	return true
}

func (p *mdParser) runHash(parent *mdNode, blocks *[]string) bool {
	block := (*blocks)[0]
	*blocks = (*blocks)[1:]
	match := mdHashHeader.FindStringSubmatchIndex(block)
	before, after := block[:match[0]], block[match[1]:]
	if before != "" {
		p.parseBlocks(parent, &[]string{before})
	}
	heading := parent.add("h" + strconv.Itoa(match[3]-match[2]))
	heading.text = analyticsTrim(block[match[4]:match[5]])
	if after != "" {
		if p.isState("looselist") {
			after = mdLooseDetab(after, 1)
		}
		*blocks = append([]string{after}, *blocks...)
	}
	return true
}

func (p *mdParser) runSetext(parent *mdNode, blocks *[]string) bool {
	lines := strings.Split((*blocks)[0], "\n")
	*blocks = (*blocks)[1:]
	level := "2"
	if strings.HasPrefix(lines[1], "=") {
		level = "1"
	}
	heading := parent.add("h" + level)
	heading.text = analyticsTrim(lines[0])
	if len(lines) > 2 {
		*blocks = append([]string{strings.Join(lines[2:], "\n")}, *blocks...)
	}
	return true
}

func (p *mdParser) runRule(parent *mdNode, blocks *[]string) bool {
	block := (*blocks)[0]
	*blocks = (*blocks)[1:]
	match := mdRule.FindStringIndex(block)
	if pre := strings.TrimRight(block[:match[0]], "\n"); pre != "" {
		p.parseBlocks(parent, &[]string{pre})
	}
	parent.add("hr")
	if post := strings.TrimLeft(block[match[1]:], "\n"); post != "" {
		*blocks = append([]string{post}, *blocks...)
	}
	return true
}

func (p *mdParser) listItems(block, tag string) []string {
	child := mdBulletChild
	if tag == "ol" {
		child = mdOrderedChild
	}
	items := []string{}
	for _, line := range strings.Split(block, "\n") {
		if match := child.FindStringSubmatch(line); match != nil {
			items = append(items, match[2])
		} else if mdListIndented.MatchString(line) && len(items) > 0 {
			if strings.HasPrefix(items[len(items)-1], "    ") {
				items[len(items)-1] += "\n" + line
			} else {
				items = append(items, line)
			}
		} else if len(items) > 0 {
			items[len(items)-1] += "\n" + line
		} else {
			items = append(items, line)
		}
	}
	return items
}

func (p *mdParser) runList(parent *mdNode, blocks *[]string, tag string) bool {
	items := p.listItems((*blocks)[0], tag)
	*blocks = (*blocks)[1:]
	sibling := parent.last()
	var list *mdNode
	switch {
	case sibling != nil && sibling.tag == tag:
		list = sibling
		item := list.last()
		if item.text != "" {
			paragraph := &mdNode{tag: "p", text: item.text}
			item.text = ""
			item.kids = append([]*mdNode{paragraph}, item.kids...)
		}
		if lastChild := item.last(); lastChild != nil && lastChild.tail != "" {
			paragraph := item.add("p")
			paragraph.text = strings.TrimLeftFunc(lastChild.tail, unicode.IsSpace)
			lastChild.tail = ""
		}
		item = list.add("li")
		p.set("looselist")
		first := items[0]
		items = items[1:]
		p.parseBlocks(item, &[]string{first})
		p.reset()
	case isList(parent.tag):
		list = parent
	default:
		list = parent.add(tag)
	}
	p.set("list")
	for _, item := range items {
		if strings.HasPrefix(item, "    ") {
			p.parseBlocks(list.last(), &[]string{item})
		} else {
			li := list.add("li")
			p.parseBlocks(li, &[]string{item})
		}
	}
	p.reset()
	return true
}

func (p *mdParser) runReference(parent *mdNode, blocks *[]string) bool {
	block := (*blocks)[0]
	*blocks = (*blocks)[1:]
	match := mdReference.FindStringSubmatchIndex(block)
	if match == nil {
		*blocks = append([]string{block}, *blocks...)
		return false
	}
	id := strings.ToLower(analyticsTrim(block[match[2]:match[3]]))
	link := strings.TrimRight(strings.TrimLeft(block[match[4]:match[5]], "<"), ">")
	p.refs[id] = link
	if rest := block[match[1]:]; analyticsTrim(rest) != "" {
		*blocks = append([]string{strings.TrimLeft(rest, "\n")}, *blocks...)
	}
	if before := block[:match[0]]; analyticsTrim(before) != "" {
		*blocks = append([]string{strings.TrimRight(before, "\n")}, *blocks...)
	}
	return true
}

func (p *mdParser) runParagraph(parent *mdNode, blocks *[]string) bool {
	block := (*blocks)[0]
	*blocks = (*blocks)[1:]
	if analyticsTrim(block) == "" {
		return true
	}
	if p.isState("list") {
		if sibling := parent.last(); sibling != nil {
			if sibling.tail != "" {
				sibling.tail += "\n" + block
			} else {
				sibling.tail = "\n" + block
			}
		} else if parent.text != "" {
			parent.text += "\n" + block
		} else {
			parent.text = strings.TrimLeftFunc(block, unicode.IsSpace)
		}
		return true
	}
	paragraph := parent.add("p")
	paragraph.text = strings.TrimLeftFunc(block, unicode.IsSpace)
	return true
}

// ---------------------------------------------------------------- inline

const (
	mdSTX = "\x02"
	mdETX = "\x03"
)

func (p *mdParser) stash(value any) string {
	id := fmt.Sprintf("%04d", p.counter)
	p.counter++
	key := "klzzwxh:" + id
	p.placeholders[key] = value
	return mdSTX + key + mdETX
}

const mdEscapedChars = "\\`*_{}[]()>#+-.!"

func isWordRune(r rune) bool { return r == '_' || unicode.IsLetter(r) || unicode.IsDigit(r) }

func runeBefore(s string, index int) (rune, bool) {
	if index <= 0 {
		return 0, false
	}
	r, _ := utf8.DecodeLastRuneInString(s[:index])
	return r, true
}

func runeAt(s string, index int) (rune, bool) {
	if index >= len(s) {
		return 0, false
	}
	r, _ := utf8.DecodeRuneInString(s[index:])
	return r, true
}

// inline applies the inline patterns, in Python-Markdown's priority order, to
// one text. The result still contains placeholders; split resolves them.
func (p *mdParser) inline(data string, inLink bool) string {
	data = p.patternBacktick(data)
	data = p.patternEscape(data)
	if !inLink {
		data = p.patternLinks(data)
	}
	data = p.replaceBreaks(data, "  \n")
	data = p.patternNotStrong(data)
	data = p.patternEmphasis(data, '*')
	data = p.patternEmphasis(data, '_')
	data = p.replaceBreaks(data, "\n")
	return data
}

// replaceBreaks swaps every occurrence of separator for its own <br> node.
func (p *mdParser) replaceBreaks(data, separator string) string {
	parts := strings.Split(data, separator)
	var out strings.Builder
	for index, part := range parts {
		if index > 0 {
			out.WriteString(p.stash(&mdNode{tag: "br"}))
		}
		out.WriteString(part)
	}
	return out.String()
}

func (p *mdParser) patternBacktick(data string) string {
	var out strings.Builder
	// Python rescans the rewritten string, so a lookbehind sees what the
	// earlier replacements left, not the original characters.
	previousIsBackslash := func() bool {
		text := out.String()
		return len(text) > 0 && text[len(text)-1] == '\\'
	}
	index := 0
	for index < len(data) {
		if data[index] == '\\' {
			end := index
			for end < len(data) && data[end] == '\\' {
				end++
			}
			if (end-index)%2 == 0 && end < len(data) && data[end] == '`' && !previousIsBackslash() {
				out.WriteString(p.stash(`\`))
				index = end
				continue
			}
			out.WriteString(data[index:end])
			index = end
			continue
		}
		if data[index] != '`' || previousIsBackslash() {
			out.WriteByte(data[index])
			index++
			continue
		}
		end := index
		for end < len(data) && data[end] == '`' {
			end++
		}
		// Like the regex, try the whole run first and then shorter openers.
		closed := false
		for width := end - index; width >= 1 && !closed; width-- {
			run := strings.Repeat("`", width)
			for search := index + width + 1; search <= len(data)-width; search++ {
				if !strings.HasPrefix(data[search:], run) || data[search-1] == '`' || (search+width < len(data) && data[search+width] == '`') {
					continue
				}
				content := data[index+width : search]
				out.WriteString(p.stash(&mdNode{tag: "code", atomic: true, text: mdCodeEscape(analyticsTrim(content))}))
				index = search + width
				closed = true
				break
			}
		}
		if !closed {
			out.WriteByte('`')
			index++
		}
	}
	return out.String()
}

func (p *mdParser) patternEscape(data string) string {
	var out strings.Builder
	for index := 0; index < len(data); index++ {
		if data[index] == '\\' && index+1 < len(data) {
			character, size := utf8.DecodeRuneInString(data[index+1:])
			if strings.ContainsRune(mdEscapedChars, character) {
				out.WriteString(p.stash(string(character)))
				index += size
				continue
			}
			// Not an escapable character: Python consumes the pair and leaves it.
			out.WriteString(data[index : index+1+size])
			index += size
			continue
		}
		out.WriteByte(data[index])
	}
	return out.String()
}

// bracketText returns the text of a link starting just after '[' and the index
// after the closing ']'.
func bracketText(data string, index int) (string, int, bool) {
	count := 1
	for position := index; position < len(data); position++ {
		switch data[position] {
		case ']':
			count--
		case '[':
			count++
		}
		if count == 0 {
			return data[index:position], position + 1, true
		}
	}
	return "", 0, false
}

var mdRefID = regexp.MustCompile(`^\s?\[([^\]]*)\]`)
var mdSpaces = regexp.MustCompile(`\s+`)

func (p *mdParser) patternLinks(data string) string {
	var out strings.Builder
	index := 0
	for index < len(data) {
		image := data[index] == '!' && index+1 < len(data) && data[index+1] == '['
		if data[index] != '[' && !image {
			out.WriteByte(data[index])
			index++
			continue
		}
		if data[index] == '[' && index > 0 && data[index-1] == '!' {
			out.WriteByte(data[index])
			index++
			continue
		}
		start := index + 1
		if image {
			start = index + 2
		}
		text, after, ok := bracketText(data, start)
		if !ok {
			out.WriteString(data[index:start])
			index = start
			continue
		}
		// Inline link: [text](href)
		if after < len(data) && data[after] == '(' {
			href, end, handled := inlineHref(data, after+1)
			if handled {
				out.WriteString(p.linkNode(text, href, image))
				index = end
				continue
			}
		}
		// Reference link: [text][id] or [id][] / [id]
		if match := mdRefID.FindStringSubmatchIndex(data[after:]); match != nil {
			id := strings.ToLower(data[after+match[2] : after+match[3]])
			if id == "" {
				id = strings.ToLower(text)
			}
			id = mdSpaces.ReplaceAllString(id, " ")
			if href, found := p.refs[id]; found {
				out.WriteString(p.linkNode(text, href, image))
				index = after + match[1]
				continue
			}
		}
		id := mdSpaces.ReplaceAllString(strings.ToLower(text), " ")
		if href, found := p.refs[id]; found {
			out.WriteString(p.linkNode(text, href, image))
			index = after
			continue
		}
		out.WriteString(data[index:start])
		index = start
	}
	return out.String()
}

func (p *mdParser) linkNode(text, href string, image bool) string {
	if image {
		return p.stash("")
	}
	node := &mdNode{tag: "a", text: text, href: href, hasHref: true}
	return p.stash(node)
}

func inlineHref(data string, index int) (string, int, bool) {
	for index < len(data) && unicode.IsSpace(rune(data[index])) {
		index++
	}
	start := index
	depth := 1
	for position := start; position < len(data); position++ {
		switch data[position] {
		case '(':
			depth++
		case ')':
			depth--
			if depth == 0 {
				return analyticsTrim(data[start:position]), position + 1, true
			}
		}
	}
	return "", 0, false
}

func (p *mdParser) patternNotStrong(data string) string {
	var out strings.Builder
	for index := 0; index < len(data); {
		character := data[index]
		if character != '*' && character != '_' {
			out.WriteByte(character)
			index++
			continue
		}
		end := index
		for end < len(data) && data[end] == character {
			end++
		}
		before, hasBefore := runeBefore(data, index)
		after, hasAfter := runeAt(data, end)
		if end-index <= 3 && (!hasBefore || unicode.IsSpace(before)) && (!hasAfter || unicode.IsSpace(after)) {
			out.WriteString(p.stash(data[index:end]))
		} else {
			out.WriteString(data[index:end])
		}
		index = end
	}
	return out.String()
}

type emphasisMatch struct {
	kind   string // single, double, double2
	tags   [2]string
	g2, g3 string
	end    int
	index  int
}

var (
	mdAsteriskPatterns = []struct {
		re   *regexp.Regexp
		kind string
		tags [2]string
	}{
		{regexp.MustCompile(`(?s)^\*\*\*(.+?)\*(.*?)\*\*`), "double", [2]string{"strong", "em"}},
		{regexp.MustCompile(`(?s)^\*\*\*(.+?)\*\*(.*?)\*`), "double", [2]string{"em", "strong"}},
		{regexp.MustCompile(`(?s)^\*\*([^*]+?)\*([^*].*?)\*\*\*`), "double2", [2]string{"strong", "em"}},
		{regexp.MustCompile(`(?s)^\*\*(.+?)\*\*`), "single", [2]string{"strong", ""}},
		{regexp.MustCompile(`(?s)^\*([^*]+)\*`), "single", [2]string{"em", ""}},
	}
	mdUnderscorePatterns = []struct {
		re   *regexp.Regexp
		kind string
		tags [2]string
	}{
		{regexp.MustCompile(`(?s)^___(.+?)_(.*?)__`), "double", [2]string{"strong", "em"}},
		{regexp.MustCompile(`(?s)^___(.+?)__(.*?)_`), "double", [2]string{"em", "strong"}},
	}
)

// emphasisAt tries one pattern (by index) at data[pos:].
func emphasisAt(data string, pos int, marker byte, index int) *emphasisMatch {
	rest := data[pos:]
	if marker == '*' {
		pattern := mdAsteriskPatterns[index]
		match := pattern.re.FindStringSubmatchIndex(rest)
		if match == nil {
			return nil
		}
		result := &emphasisMatch{kind: pattern.kind, tags: pattern.tags, index: index, end: pos + match[1], g2: rest[match[2]:match[3]]}
		if len(match) > 5 && match[4] >= 0 {
			result.g3 = rest[match[4]:match[5]]
		}
		return result
	}
	var result *emphasisMatch
	switch index {
	case 0, 1:
		pattern := mdUnderscorePatterns[index]
		if match := pattern.re.FindStringSubmatchIndex(rest); match != nil {
			result = &emphasisMatch{kind: pattern.kind, tags: pattern.tags, end: pos + match[1], g2: rest[match[2]:match[3]], g3: rest[match[4]:match[5]]}
		}
	case 2:
		result = smartStrongEm(data, pos)
	case 3:
		if result = smartSingle(data, pos, 2); result != nil {
			result.tags = [2]string{"strong", ""}
		}
	case 4:
		if result = smartSingle(data, pos, 1); result != nil {
			result.tags = [2]string{"em", ""}
		}
	}
	if result != nil {
		result.index = index
	}
	return result
}

func emphasisPatternCount(marker byte) int { return 5 }

func notWordBefore(data string, pos int) bool {
	r, ok := runeBefore(data, pos)
	return !ok || !isWordRune(r)
}

func notWordAt(data string, pos int) bool {
	r, ok := runeAt(data, pos)
	return !ok || !isWordRune(r)
}

// smartSingle: (?<!\w)(_{n})(?!_)(.+?)(?<!_)\1(?!\w)
func smartSingle(data string, pos, width int) *emphasisMatch {
	marker := strings.Repeat("_", width)
	if !notWordBefore(data, pos) || !strings.HasPrefix(data[pos:], marker) {
		return nil
	}
	start := pos + width
	if start < len(data) && data[start] == '_' {
		return nil
	}
	for close := start + 1; close+width <= len(data); close++ {
		if !strings.HasPrefix(data[close:], marker) || data[close-1] == '_' || !notWordAt(data, close+width) {
			continue
		}
		return &emphasisMatch{kind: "single", g2: data[start:close], end: close + width}
	}
	return nil
}

// smartStrongEm: (?<!\w)(\_)\1(?!\1)(.+?)(?<!\w)\1(?!\1)(.+?)\1{3}(?!\w)
func smartStrongEm(data string, pos int) *emphasisMatch {
	if !notWordBefore(data, pos) || !strings.HasPrefix(data[pos:], "__") {
		return nil
	}
	start := pos + 2
	if start < len(data) && data[start] == '_' {
		return nil
	}
	for middle := start + 1; middle < len(data); middle++ {
		if data[middle] != '_' || !notWordBefore(data, middle) || (middle+1 < len(data) && data[middle+1] == '_') {
			continue
		}
		for close := middle + 2; close+3 <= len(data); close++ {
			if strings.HasPrefix(data[close:], "___") && notWordAt(data, close+3) {
				return &emphasisMatch{kind: "double2", tags: [2]string{"strong", "em"}, g2: data[start:middle], g3: data[middle+1 : close], end: close + 3}
			}
		}
	}
	return nil
}

func (p *mdParser) buildEmphasis(match *emphasisMatch, marker byte) *mdNode {
	switch match.kind {
	case "double2":
		outer := &mdNode{tag: match.tags[0]}
		inner := &mdNode{tag: match.tags[1]}
		p.subPatterns(match.g2, outer, nil, match.index, marker)
		outer.kids = append(outer.kids, inner)
		p.subPatterns(match.g3, inner, nil, match.index, marker)
		return outer
	case "double":
		outer := &mdNode{tag: match.tags[0]}
		inner := &mdNode{tag: match.tags[1]}
		p.subPatterns(match.g2, inner, nil, match.index, marker)
		outer.kids = append(outer.kids, inner)
		if match.g3 != "" {
			p.subPatterns(match.g3, outer, inner, match.index, marker)
		}
		return outer
	}
	node := &mdNode{tag: match.tags[0]}
	p.subPatterns(match.g2, node, nil, match.index, marker)
	return node
}

func (p *mdParser) subPatterns(data string, parent, last *mdNode, idx int, marker byte) {
	offset, position := 0, 0
	for position < len(data) {
		if data[position] != marker {
			position++
			continue
		}
		matched := false
		for index := 0; index < emphasisPatternCount(marker); index++ {
			if index <= idx {
				continue
			}
			match := emphasisAt(data, position, marker, index)
			if match == nil {
				continue
			}
			if text := data[offset:position]; text != "" {
				if last != nil {
					last.tail = text
				} else {
					parent.text = text
				}
			}
			element := p.buildEmphasis(match, marker)
			parent.kids = append(parent.kids, element)
			last = element
			offset, position = match.end, match.end
			matched = true
		}
		if !matched {
			position++
		}
	}
	if text := data[offset:]; text != "" {
		if last != nil {
			last.tail = text
		} else {
			parent.text = text
		}
	}
}

// patternEmphasis rescans from the start after every replacement, as
// Python-Markdown does, so a leftover marker can pair with a later one.
func (p *mdParser) patternEmphasis(data string, marker byte) string {
	for {
		replaced := false
		for index := 0; index < len(data); index++ {
			if data[index] != marker {
				continue
			}
			var match *emphasisMatch
			for pattern := 0; pattern < emphasisPatternCount(marker) && match == nil; pattern++ {
				match = emphasisAt(data, index, marker, pattern)
			}
			if match == nil {
				continue
			}
			data = data[:index] + p.stash(p.buildEmphasis(match, marker)) + data[match.end:]
			replaced = true
			break
		}
		if !replaced {
			return data
		}
	}
}

// split resolves placeholders: leading text plus nodes each carrying its tail.
func (p *mdParser) split(data string) (string, []*mdNode) {
	lead := strings.Builder{}
	var nodes []*mdNode
	var current *mdNode
	appendText := func(text string) {
		if current != nil {
			current.tail += text
		} else {
			lead.WriteString(text)
		}
	}
	for data != "" {
		start := strings.Index(data, mdSTX)
		if start < 0 {
			appendText(data)
			break
		}
		end := strings.Index(data[start:], mdETX)
		if end < 0 {
			appendText(data)
			break
		}
		appendText(data[:start])
		key := data[start+1 : start+end]
		data = data[start+end+1:]
		switch value := p.placeholders[key].(type) {
		case string:
			appendText(value)
		case *mdNode:
			nodes = append(nodes, value)
			current = value
			current.tail = ""
		default:
			// Escaped character code placeholder such as \x0292\x03.
			if code, err := strconv.Atoi(key); err == nil {
				appendText(string(rune(code)))
			}
		}
	}
	return lead.String(), nodes
}

func (p *mdParser) processElement(element *mdNode, inLink, underEmphasis bool) {
	// Python-Markdown skips links inside a link, but not once an emphasis
	// element sits in between (observed against the real converter).
	switch element.tag {
	case "a":
		inLink = !underEmphasis
	case "em", "strong":
		inLink, underEmphasis = false, true
	}
	original := append([]*mdNode(nil), element.kids...)
	if element.text != "" && !element.atomic {
		lead, nodes := p.split(p.inline(element.text, inLink))
		element.text = lead
		element.kids = append(nodes, element.kids...)
	}
	for _, child := range original {
		if child.tail == "" {
			continue
		}
		lead, nodes := p.split(p.inline(child.tail, inLink))
		child.tail = lead
		position := 0
		for index, candidate := range element.kids {
			if candidate == child {
				position = index + 1
			}
		}
		element.kids = append(element.kids[:position], append(nodes, element.kids[position:]...)...)
	}
	for _, child := range element.kids {
		p.processElement(child, inLink, underEmphasis)
	}
}

// ---------------------------------------------------------------- output

var mdBlockLevel = map[string]bool{"blockquote": true, "h1": true, "h2": true, "h3": true, "h4": true, "h5": true, "h6": true, "hr": true, "li": true, "ol": true, "p": true, "pre": true, "ul": true}

func prettify(element *mdNode) {
	if mdBlockLevel[element.tag] && element.tag != "code" && element.tag != "pre" {
		if strings.TrimSpace(element.text) == "" && len(element.kids) > 0 && mdBlockLevel[element.kids[0].tag] {
			element.text = "\n"
		}
		for _, child := range element.kids {
			if mdBlockLevel[child.tag] {
				prettify(child)
			}
		}
		if strings.TrimSpace(element.tail) == "" {
			element.tail = "\n"
		}
	}
	if strings.TrimSpace(element.tail) == "" {
		element.tail = "\n"
	}
}

func fixBreaks(element *mdNode) {
	if element.tag == "pre" && len(element.kids) > 0 && element.kids[0].tag == "code" {
		if code := element.kids[0]; len(code.kids) == 0 && code.text != "" {
			code.text = strings.TrimRightFunc(code.text, unicode.IsSpace) + "\n"
		}
	}
	for _, child := range element.kids {
		if child.tag == "br" {
			if strings.TrimSpace(child.tail) == "" {
				child.tail = "\n"
			} else {
				child.tail = "\n" + child.tail
			}
		}
		fixBreaks(child)
	}
}

var mdAllowed = map[string]bool{"a": true, "blockquote": true, "br": true, "code": true, "em": true, "h1": true, "h2": true, "h3": true, "h4": true, "h5": true, "h6": true, "hr": true, "li": true, "ol": true, "p": true, "pre": true, "strong": true, "ul": true}

func safeLinkTarget(value string) string {
	target := analyticsTrim(value)
	// Python's urlparse drops leading control characters and tab/newlines.
	probe := strings.TrimLeftFunc(target, func(r rune) bool { return r <= 0x20 })
	probe = strings.NewReplacer("\t", "", "\r", "", "\n", "").Replace(probe)
	colon := strings.IndexByte(probe, ':')
	if colon <= 0 {
		return ""
	}
	scheme := probe[:colon]
	for index, r := range scheme {
		letter := r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z'
		if !(letter || index > 0 && (r >= '0' && r <= '9' || r == '+' || r == '-' || r == '.')) {
			return ""
		}
	}
	switch strings.ToLower(scheme) {
	case "http", "https", "mailto":
		return target
	}
	return ""
}

func renderMarkdownNode(builder *strings.Builder, element *mdNode, root bool) {
	if !root && mdAllowed[element.tag] {
		switch element.tag {
		case "br", "hr":
			builder.WriteString("<" + element.tag + ">")
			builder.WriteString(element.tail)
			return
		case "a":
			target := ""
			if element.hasHref {
				target = safeLinkTarget(html.UnescapeString(element.href))
			}
			if target != "" {
				builder.WriteString(`<a href="` + mdEscapeAll.Replace(target) + `">`)
			} else {
				builder.WriteString("<a>")
			}
		default:
			builder.WriteString("<" + element.tag + ">")
		}
	}
	builder.WriteString(element.text)
	for _, child := range element.kids {
		renderMarkdownNode(builder, child, false)
	}
	if !root && mdAllowed[element.tag] {
		builder.WriteString("</" + element.tag + ">")
	}
	if !root {
		builder.WriteString(element.tail)
	}
}

func markdownExpandTabs(source string) string {
	if !strings.Contains(source, "\t") {
		return source
	}
	lines := strings.Split(source, "\n")
	for index, line := range lines {
		if !strings.Contains(line, "\t") {
			continue
		}
		var out strings.Builder
		column := 0
		for _, r := range line {
			if r == '\t' {
				spaces := 4 - column%4
				out.WriteString(strings.Repeat(" ", spaces))
				column += spaces
				continue
			}
			out.WriteRune(r)
			column++
		}
		lines[index] = out.String()
	}
	return strings.Join(lines, "\n")
}

// markdownToHTML mirrors content_formatting.markdown_to_html.
func markdownToHTML(value string) string {
	text := analyticsTrim(value)
	if text == "" {
		return ""
	}
	source := mdEscapeAll.Replace(text)
	source = strings.NewReplacer("\x02", "", "\x03", "").Replace(source)
	source = strings.ReplaceAll(strings.ReplaceAll(source, "\r\n", "\n"), "\r", "\n") + "\n\n"
	source = markdownExpandTabs(source)
	lines := strings.Split(source, "\n")
	for index := 1; index < len(lines)-1; index++ {
		if lines[index] != "" && strings.Trim(lines[index], " ") == "" {
			lines[index] = ""
		}
	}
	source = strings.Join(lines, "\n")

	parser := &mdParser{refs: map[string]string{}, placeholders: map[string]any{}}
	root := &mdNode{tag: "div"}
	parser.parseChunk(root, source)
	parser.processElement(root, false, false)
	for _, child := range root.kids {
		prettify(child)
	}
	fixBreaks(root)
	var builder strings.Builder
	renderMarkdownNode(&builder, root, true)
	return analyticsTrim(builder.String())
}
