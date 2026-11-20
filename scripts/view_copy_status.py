#!/usr/bin/env python3
"""View the recorded Finnish copy audit in a local browser; standard library only."""
from __future__ import annotations

import csv
from datetime import datetime
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / 'docs/finnish-copy-status.csv'
STATUSES = {
    'REVIEWED': ('Reviewed', 'reviewed'),
    'NO OWN COPY': ('No owned text', 'neutral'),
    'REVIEWED / USAGE TODO': ('Usage uncertain', 'uncertain'),
    'EMAIL PENDING': ('Email pending', 'pending'),
    'PENDING': ('Unchecked', 'unchecked'),
}
BASE = 'https://github.com/mielenosoitukset-fi/mielenosoitukset_fi/blob/main/'


AUDITS = [
    'finnish-copy-audit.md', 'finnish-admin-editor-copy.md',
    'finnish-admin-operations-copy.md', 'finnish-admin-manual-copy.md',
    'finnish-admin-permission-copy.md', 'finnish-admin-copy-audit.md',
]


def change_records():
    """Read recorded examples; retain rejected/deferred proposals as such."""
    records = []
    applied_pairs = set()
    for document in AUDITS[:-1]:
        header = []
        sequence = 0
        for line in (ROOT / 'docs' / document).read_text().splitlines():
            if not line.startswith('|'):
                continue
            cells = [cell.strip() for cell in re.split(r'(?<!\\)\|', line.strip('|'))]
            if cells[0] in {'ID', 'Template', 'Template (under templates/)', 'Manual', 'Original catalog display text'}:
                header = cells
                continue
            if not header or all(re.fullmatch(r'[: -]+', cell) for cell in cells):
                continue
            if len(cells) != len(header):
                continue
            if document == 'finnish-copy-audit.md':
                if not re.fullmatch(r'(?:API-|R|U|T)\d+', cells[0]):
                    continue
                source, before, after, reason, recorded = cells[3:] if len(cells) == 8 else cells[1:]
                # Retained-template rows also include priority/type before source.
                if len(cells) == 8:
                    source, before, after, reason, recorded = cells[3:]
                state = 'Applied' if recorded.startswith('Applied') else 'Rejected' if recorded.startswith('Rejected') else 'Deferred'
                identifier = cells[0]
            elif document == 'finnish-admin-permission-copy.md':
                before, after = cells
                source = 'mielenosoitukset_fi/templates/admin_V2/user/edit.html'
                reason = 'Finnish display localization; permission identifiers and policies unchanged.'
                recorded, state = 'Applied in Finnish editor display; batch 5', 'Applied'
                identifier = 'Permissions'
            else:
                if len(cells) != 4:
                    continue
                source, before, after, reason = cells
                source = source.strip('`')
                if document == 'finnish-admin-manual-copy.md':
                    source = 'manuals/' + source
                if not source.startswith('mielenosoitukset_fi/'):
                    source = 'mielenosoitukset_fi/templates/' + source
                recorded, state, identifier = 'Applied in correction batch', 'Applied', 'Admin'
            sequence += 1
            record = {'id': identifier + '-' + str(sequence), 'source': source,
                      'before': before.strip('`'), 'after': after.strip('`'),
                      'reason': reason, 'status': state, 'recorded_status': recorded,
                      'evidence': document}
            records.append(record)
            if state == 'Applied':
                applied_pairs.add((record['before'], record['after']))
    audit = (ROOT / 'docs' / AUDITS[-1]).read_text().split('## Every-file inventory')[0]
    pattern = r'^- `([^`]+)` — `([^`]+)` → `([^`]+)`[.]? (.+)$'
    for index, match in enumerate(re.finditer(pattern, audit, re.M), 1):
        source, before, after, reason = match.groups()
        if (before, after) in applied_pairs:
            continue
        is_case = source.startswith('admin_V2/cases/case.html:')
        state = 'Deferred' if 'Optional / uncertain' in audit[:match.start()] else 'Proposal'
        if is_case:
            state = 'Deferred' if before in {'(ei viestia)', 'Oltu yhteydessa'} else 'Applied'
        records.append({'id': 'Admin-proposal-' + str(index), 'source': source,
                        'before': before, 'after': after, 'reason': reason,
                        'status': state, 'recorded_status': 'Case batch 1' if is_case else 'Audit proposal; not assumed applied',
                        'evidence': AUDITS[-1]})
    return records


def read_data():
    with CSV.open(newline='') as source:
        rows = list(csv.DictReader(source))
    stamps = [CSV.stat().st_mtime_ns] + [(ROOT / 'docs' / name).stat().st_mtime_ns for name in AUDITS]
    return {'rows': rows, 'changes': change_records(), 'version': '-'.join(map(str, stamps)),
            'updated': datetime.fromtimestamp(max(stamps) / 1e9).astimezone().strftime('%d %b %Y, %H:%M %Z')}


def rows_html(rows):
    output = []
    for row in rows:
        label, tone = STATUSES.get(row['status'], (row['status'], 'neutral'))
        path = row['path']
        output.append(
            f'<tr data-status="{html.escape(row["status"], quote=True)}">'
            f'<td><a class="path" href="{BASE + quote(path, safe="/")}">{html.escape(path)}</a>'
            f'<span class="kind">{html.escape(row["kind"])}</span>'
            f'<details><summary>Review notes</summary><p>{html.escape(row["scope"])}</p>'
            f'<a href="{BASE + quote("docs/" + row["evidence"], safe="/")}">Audit evidence</a></details></td>'
            f'<td><span class="status {tone}">{html.escape(label)}</span></td></tr>'
        )
    return ''.join(output)


def changes_html(changes):
    output = []
    for change in changes:
        tone = {'Applied': 'reviewed', 'Deferred': 'pending', 'Rejected': 'unchecked'}.get(change['status'], 'neutral')
        source = re.sub(r'\[([^]]+)\]\([^)]+\)', r'\1', change['source'])
        output.append(
            f'<article class="change" data-change-status="{change["status"]}">'
            f'<div class="change-heading"><span class="status {tone}">{change["status"]}</span>'
            f'<span>{html.escape(change["id"])}</span></div>'
            f'<p class="change-source">{html.escape(source)}</p>'
            f'<div class="comparison"><div><strong>Before</strong><pre>{html.escape(change["before"])}</pre></div>'
            f'<div><strong>After</strong><pre>{html.escape(change["after"])}</pre></div></div>'
            f'<p class="reason">{html.escape(change["reason"].replace("**", ""))}</p>'
            f'<p class="kind">Recorded: {html.escape(change["recorded_status"])}</p>'
            f'<a href="{BASE + "docs/" + quote(change["evidence"])}">Audit evidence</a></article>'
        )
    return ''.join(output)


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Finnish copy review status</title>
<meta name="description" content="Recorded Finnish language-review status for every React and Flask source, with search and filters.">
<style>
/* Hallmark · shape: app screen · theme: project civic blue · surface: absolute · posture: restrained · v2.0.0 */
/* Critique: a legible file ledger; no dashboard cards or decorative metrics. */
/* Waiver 7b: white table/control surfaces match the project's paper-like utility surfaces; blue marks selection. */
:root { --canvas:#f4f6f9; --surface:#fff; --text:#172333; --muted:#526277; --border:#d9e0e9;
 --accent:#003bb5; --soft:#eaf0ff; --good:#185536; --good-bg:#e9f5ee; --warn:#77510b; --warn-bg:#fff4d8;
 --bad:#a22b20; --bad-bg:#fff0ed; --font:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
 --radius:7px; color-scheme:light; }
* { box-sizing:border-box; } html,body { overflow-x:clip; }
body { margin:0; background:var(--canvas); color:var(--text); font:15px/1.5 var(--font); }
main { max-width:1440px; margin:auto; padding:44px 32px 60px; }
h1 { text-wrap:balance; font-size:30px; line-height:1.2; letter-spacing:-.6px; margin:0 0 12px; min-width:0; overflow-wrap:break-word; }
.lead { color:var(--muted); margin:0 0 24px; max-width:75ch; }
.toolbar { display:flex; flex-wrap:wrap; align-items:end; gap:12px; margin:24px 0 20px; }
.search { flex:1 1 260px; } label { display:block; font-weight:600; margin-bottom:8px; }
input,button { font:inherit; border:1px solid var(--border); border-radius:var(--radius); min-height:44px; }
input { width:100%; padding:8px 12px; background:var(--surface); color:var(--text); }
button { padding:8px 12px; background:var(--surface); color:var(--text); cursor:pointer; }
button:hover { border-color:var(--accent); } button[aria-pressed="true"] { background:var(--accent); border-color:var(--accent); color:var(--surface); }
button:active:not([aria-pressed="true"]) { background:var(--soft); }
button:disabled { cursor:wait; background:var(--canvas); color:var(--muted); border-color:var(--border); }
a { color:var(--accent); text-underline-offset:3px; }
:where(a,button,input,summary):focus-visible { outline:3px solid var(--accent); outline-offset:3px; }
.filters { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 20px; }
.meta { display:flex; flex-wrap:wrap; justify-content:space-between; gap:8px; color:var(--muted); font-size:13px; margin:0 0 12px; }
.table-wrap { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); overflow:auto; }
table { width:100%; border-collapse:collapse; text-align:left; }
caption { text-align:left; padding:16px; font-weight:600; border-bottom:1px solid var(--border); }
th { font-size:13px; color:var(--muted); font-weight:600; background:var(--canvas); }
th,td { padding:16px; border-bottom:1px solid var(--border); vertical-align:top; }
tr:last-child td { border-bottom:0; } td:first-child { width:80%; } .path { overflow-wrap:anywhere; font-size:14px; }
.kind { display:block; color:var(--muted); font-size:12px; margin-top:4px; }
.status { display:inline-block; padding:4px 8px; border-radius:4px; font-size:13px; white-space:nowrap; }
.reviewed { color:var(--good); background:var(--good-bg); } .neutral { color:var(--muted); background:var(--canvas); }
.uncertain,.pending { color:var(--warn); background:var(--warn-bg); } .unchecked { color:var(--bad); background:var(--bad-bg); }
summary { cursor:pointer; color:var(--accent); min-height:44px; } details p { max-width:52ch; margin:8px 0; font-size:13px; }
.help { margin-top:24px; color:var(--muted); max-width:85ch; } .empty { padding:32px 16px; text-align:center; }
#error { color:var(--bad); } [hidden] { display:none; }
.change { padding:24px; background:var(--surface); border-bottom:1px solid var(--border); }
.change-heading { display:flex; align-items:center; gap:12px; font-size:13px; color:var(--muted); }
.change-source { font-size:13px; color:var(--muted); overflow-wrap:anywhere; }
.comparison { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:24px; }
.comparison strong { font-size:13px; } pre { font:inherit; white-space:pre-wrap; overflow-wrap:anywhere; margin:8px 0; }
.reason { color:var(--muted); font-size:14px; }
@media(max-width:650px) { main { padding:24px 16px; } .comparison { grid-template-columns:minmax(0,1fr); gap:16px; } .change { padding:20px 16px; } h1 { font-size:25px; } th,td { padding:12px 8px; }
 td:first-child { width:auto; } .status { white-space:normal; } }
</style></head><body><main>
<h1>Finnish copy review status</h1>
<p class="lead">Every recorded React and Flask source, in one place. Reviewed means the text was inspected; it does not mean every fix has shipped.</p>
<nav class="filters" aria-label="Choose view"><button id="files-view" type="button" aria-pressed="true">Files</button><button id="changes-view" type="button" aria-pressed="false">Changes</button></nav>
<div class="toolbar"><div class="search"><label for="search">Search</label><input id="search" type="search" placeholder="Search files, original text or corrections" autocomplete="off"></div>
<button id="refresh" type="button">Refresh status</button><a id="export" href="/source.csv">Download CSV</a></div>
<div class="filters" role="group" aria-label="Filter by review status">@@FILTERS@@</div>
<div id="change-filters" class="filters" role="group" aria-label="Filter corrections" hidden>@@CHANGEFILTERS@@</div>
<div class="meta"><span id="result" role="status">@@TOTAL@@ files</span><span id="updated">Records updated @@UPDATED@@</span></div>
<p id="error" role="alert" hidden></p>
<div id="files-panel" class="table-wrap"><table><caption>Source review ledger</caption><thead><tr><th scope="col">File</th><th scope="col">Check status</th></tr></thead><tbody id="rows">@@ROWS@@</tbody></table>
<p id="empty" class="empty" hidden>No matching files. Clear the search or choose another status.</p></div>
<section id="changes-panel" hidden><p class="lead">Recorded examples, not automatic writing rules. Applied means recorded in an implementation batch, not human approval. Some entries are source fragments or markup; curate examples before building a skill.</p><div id="changes">@@CHANGES@@</div><p id="changes-empty" class="empty" hidden>No matching changes.</p></section>
<details class="help"><summary>What these statuses mean</summary>
<p>Reviewed: source-language review recorded. No owned text: a wrapper or helper receives its text elsewhere. Usage uncertain: source reviewed, but current reachability needs checking. Email pending and unchecked: not reviewed yet.</p>
<p>This page reads the recorded CSV and audit reports, not the code itself. It checks for CSV updates every 30 seconds. It does not mark files reviewed or track deployment.</p></details>
<noscript><style>#changes-panel { display:block; }</style><p>All recorded files are listed above. Enable JavaScript for filtering and automatic refresh.</p></noscript>
</main><script type="application/json" id="initial">@@DATA@@</script><script>
const labels = {'REVIEWED':'Reviewed','NO OWN COPY':'No owned text','REVIEWED / USAGE TODO':'Usage uncertain','EMAIL PENDING':'Email pending','PENDING':'Unchecked'};
let data = JSON.parse(document.getElementById('initial').textContent);
const search = document.getElementById('search'), buttons = [...document.querySelectorAll('[data-filter]')];
const parameters = new URLSearchParams(location.search);
search.value = parameters.get('q') || '';
let mode = parameters.get('view') === 'changes' ? 'changes' : 'files';
let status = parameters.get('status') || '';
const changeButtons = [...document.querySelectorAll('[data-change-filter]')];
function filter() {
 const query = search.value.trim().toLocaleLowerCase(); let shown = 0;
 document.getElementById('files-panel').hidden = mode !== 'files'; document.getElementById('changes-panel').hidden = mode !== 'changes';
 document.querySelector('[aria-label="Filter by review status"]').hidden = mode !== 'files'; document.getElementById('change-filters').hidden = mode !== 'changes';
 document.getElementById('files-view').setAttribute('aria-pressed',String(mode === 'files')); document.getElementById('changes-view').setAttribute('aria-pressed',String(mode === 'changes'));
 const exportLink=document.getElementById('export'); exportLink.href=mode==='changes'?'/examples.json':'/source.csv'; exportLink.textContent=mode==='changes'?'Export applied examples':'Download CSV';
 if(mode === 'changes') {
  [...document.querySelectorAll('#changes article')].forEach((article,index) => { const item=data.changes[index]; const match=(!status || item.status===status) && (!query || [item.id,item.source,item.before,item.after,item.reason].join(' ').toLocaleLowerCase().includes(query)); article.hidden=!match; if(match) shown++; });
  changeButtons.forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.changeFilter===status)));
  document.getElementById('result').textContent=`${shown} of ${data.changes.length} recorded changes`; document.getElementById('changes-empty').hidden=shown!==0;
 } else {
 [...document.querySelectorAll('#rows tr')].forEach((row,index) => {
  const item = data.rows[index]; const match = (!status || item.status === status) && (!query || [item.path,item.kind,item.scope].join(' ').toLocaleLowerCase().includes(query));
  row.hidden = !match; if(match) shown++;
 });
 buttons.forEach(button => button.setAttribute('aria-pressed',String(button.dataset.filter === status)));
 document.getElementById('result').textContent = `${shown} of ${data.rows.length} files`;
 document.getElementById('empty').hidden = shown !== 0;
 }
 const url = new URL(location.href); url.search = '';
 if(mode==='changes') url.searchParams.set('view','changes'); if(search.value) url.searchParams.set('q',search.value); if(status) url.searchParams.set('status',status);
 history.replaceState(null,'',url);
}
search.addEventListener('input',filter);
for(const choice of ['files','changes']) document.getElementById(choice+'-view').addEventListener('click',()=>{mode=choice;status='';filter();});
changeButtons.forEach(button=>button.addEventListener('click',()=>{status=button.dataset.changeFilter;filter();}));
buttons.forEach(button => button.addEventListener('click',() => { status = button.dataset.filter; filter(); }));
let refreshing = false;
async function refresh() {
 if(refreshing) return; refreshing=true; document.getElementById('refresh').setAttribute('aria-busy','true'); document.getElementById('refresh').disabled=true;
 try {
  const response = await fetch('/status.json',{cache:'no-store'}); if(!response.ok) throw new Error('Status could not be loaded.');
  const next = await response.json();
  if(next.version !== data.version) {
   const correctionResponse=await fetch('/changes.html',{cache:'no-store'}); if(!correctionResponse.ok) throw new Error('Changes unavailable');
   const correctionMarkup=await correctionResponse.text();
   data = next;
   const fragment = document.createDocumentFragment();
   for(const item of data.rows) {
    const row=document.createElement('tr'); row.dataset.status=item.status;
    const file=document.createElement('td'), link=document.createElement('a');
    link.className='path'; link.href=@@BASE@@+item.path.split('/').map(encodeURIComponent).join('/'); link.textContent=item.path;
    const kind=document.createElement('span'); kind.className='kind'; kind.textContent=item.kind; file.append(link,kind);
    const state=document.createElement('td'), badge=document.createElement('span'); badge.className='status '+({'REVIEWED':'reviewed','NO OWN COPY':'neutral','REVIEWED / USAGE TODO':'uncertain','EMAIL PENDING':'pending','PENDING':'unchecked'}[item.status] || 'neutral'); badge.textContent=labels[item.status] || item.status; state.append(badge);
    const details=document.createElement('details'), summary=document.createElement('summary'), note=document.createElement('p'), audit=document.createElement('a');
    summary.textContent='Review notes'; note.textContent=item.scope; audit.textContent='Audit evidence'; audit.href=@@BASE@@+'docs/'+encodeURIComponent(item.evidence); details.append(summary,note,audit); file.append(details); row.append(file,state); fragment.append(row);
   }
   document.getElementById('rows').replaceChildren(fragment);
   document.getElementById('changes').innerHTML=correctionMarkup;
   buttons.forEach(button => { const key=button.dataset.filter; button.textContent=(key ? labels[key] : 'All files')+' ('+data.rows.filter(row=>!key || row.status===key).length+')'; });
   document.getElementById('updated').textContent='Records updated '+data.updated; filter();
  }
  document.getElementById('error').hidden=true;
 } catch(error) { const message=document.getElementById('error'); message.textContent='Cannot refresh status. Last loaded records remain visible. Try Refresh status again.'; message.hidden=false; }
 finally { refreshing=false; document.getElementById('refresh').removeAttribute('aria-busy'); document.getElementById('refresh').disabled=false; }
}
document.getElementById('refresh').addEventListener('click',refresh);
filter(); setInterval(refresh,30000);
</script></body></html>'''


def render(data):
    filters = [('', 'All files')] + [(key, value[0]) for key, value in STATUSES.items()]
    controls = ''.join(
        f'<button type="button" data-filter="{html.escape(key, quote=True)}" '
        f'aria-pressed="{str(not key).lower()}">{label} '
        f'({sum(not key or row["status"] == key for row in data["rows"])})</button>'
        for key, label in filters
    )
    change_controls=''.join(f'<button type="button" data-change-filter="{key}" aria-pressed="{str(not key).lower()}">{key or "All changes"}</button>' for key in ['', 'Applied', 'Deferred', 'Rejected', 'Proposal'])
    values = {'CHANGES': changes_html(data['changes']), 'CHANGEFILTERS': change_controls, 'FILTERS': controls, 'TOTAL': str(len(data['rows'])),
              'UPDATED': html.escape(data['updated']), 'ROWS': rows_html(data['rows']),
              'DATA': json.dumps(data).replace('<', '\u003c'), 'BASE': json.dumps(BASE)}
    page = PAGE
    for key, value in values.items():
        page = page.replace('@@' + key + '@@', value)
    return page


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == '/':
                body, content_type = render(read_data()).encode(), 'text/html; charset=utf-8'
            elif path == '/status.json':
                body, content_type = json.dumps(read_data()).encode(), 'application/json; charset=utf-8'
            elif path == '/changes.html':
                body, content_type = changes_html(change_records()).encode(), 'text/html; charset=utf-8'
            elif path == '/examples.json':
                examples = [row for row in change_records() if row['status'] == 'Applied']
                body = json.dumps({'notes': 'Recorded applied changes, not human approval. Curate source fragments before making writing rules.', 'examples': examples}, ensure_ascii=False, indent=2).encode()
                content_type = 'application/json; charset=utf-8'
            elif path == '/source.csv':
                body, content_type = CSV.read_bytes(), 'text/csv; charset=utf-8'
            elif path == '/favicon.ico':
                self.send_response(204); self.end_headers(); return
            else:
                self.send_error(404); return
        except (OSError, csv.Error, KeyError):
            self.send_error(503, 'Status CSV is unavailable or invalid'); return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        if path == '/examples.json':
            self.send_header('Content-Disposition', 'attachment; filename="finnish-copy-examples.json"')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


if __name__ == '__main__':
    address = ('127.0.0.1', 8765)
    with ThreadingHTTPServer(address, Handler) as server:
        print(f'Copy status: http://{address[0]}:{address[1]}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
