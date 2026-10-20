"""Literal transcript searches and offline, escaped HTML highlights.

No remote search service, regular expressions supplied by users, or JavaScript.
"""
import json
import re
from collections import Counter
from datetime import datetime, timezone
from html import escape

from core import csv_bytes


def search_options(terms='', rule='any', case_sensitive=False, whole_words=True):
    if rule not in ('any', 'all'):
        raise ValueError('Choose any or all search phrases.')
    raw = terms.splitlines() if isinstance(terms, str) else list(terms)
    phrases, seen = [], set()
    for value in raw:
        phrase = ' '.join(str(value).split())
        if not phrase:
            continue
        if len(phrase) > 300:
            raise ValueError('Each search phrase must be 300 characters or fewer.')
        key = phrase if case_sensitive else phrase.casefold()
        if key not in seen:
            seen.add(key)
            phrases.append(phrase)
    if len(phrases) > 50:
        raise ValueError('Use at most 50 keywords or phrases per search.')
    return dict(terms=phrases, rule=rule, case_sensitive=bool(case_sensitive),
                whole_words=bool(whole_words))


def compiled_terms(options):
    options = search_options(**options)
    flags = 0 if options['case_sensitive'] else re.IGNORECASE
    patterns = []
    for phrase in options['terms']:
        pattern = r'\s+'.join(re.escape(word) for word in phrase.split())
        if options['whole_words']:
            pattern = r'(?<!\w)' + pattern + r'(?!\w)'
        patterns.append((phrase, re.compile(pattern, flags)))
    return options, patterns


def search_text(text, options, patterns=None):
    if patterns is None:
        options, patterns = compiled_terms(options)
    hits = [(m.start(), m.end(), phrase)
            for phrase, pattern in patterns for m in pattern.finditer(text)]
    hits.sort()
    found = [phrase for phrase in options['terms'] if any(h[2] == phrase for h in hits)]
    matches = bool(found) and (options['rule'] == 'any' or len(found) == len(options['terms']))
    return dict(matches=matches, terms=found, count=len(hits), hits=hits)


def analyze_calls(calls, options):
    options, patterns = compiled_terms(options)
    evaluated = []
    for call in calls:
        text = str(call.get('transcript') or '')
        searchable = call.get('transcript_status') == 'available' and bool(text.strip())
        hit = search_text(text, options, patterns) if searchable else dict(matches=False, terms=[], count=0, hits=[])
        state = ('match' if hit['matches'] else 'no match') if searchable and options['terms'] else (
            'no query' if searchable else 'not searchable')
        evaluated.append(dict(call=call, search=hit, search_status=state, searchable=searchable))
    return evaluated


def coverage(evaluated):
    statuses = Counter(str(x['call'].get('transcript_status', 'unknown')) for x in evaluated)
    return dict(calls=len(evaluated), searchable=sum(x['searchable'] for x in evaluated),
                matching_calls=sum(x['search_status'] == 'match' for x in evaluated),
                not_searchable=sum(not x['searchable'] for x in evaluated),
                transcript_statuses=dict(statuses))


def result_rows(evaluated):
    rows = []
    for item in evaluated:
        c, hit = item['call'], item['search']
        snippets = [c['transcript'][max(0, a-90):min(len(c['transcript']), b+90)].replace('\n', ' ')
                    for a, b, _ in hit['hits'][:5]]
        row = {k: v for k, v in c.items() if k not in ('transcript', 'group_key')}
        row.update(search_status=item['search_status'], matched_phrases='; '.join(hit['terms']),
                   occurrence_count=hit['count'], excerpts=' … '.join(snippets))
        rows.append(row)
    return rows


def highlighted_body(text, hits):
    """Merge intersecting matches before escaping every source text fragment."""
    spans = []
    for start, end, _ in sorted(hits):
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    pieces, offset = [], 0
    for start, end in spans:
        pieces += [escape(text[offset:start]), '<mark>', escape(text[start:end]), '</mark>']
        offset = end
    pieces.append(escape(text[offset:]))
    return ''.join(pieces)


def highlighted_html(call, options):
    options = search_options(**options)
    text = str(call.get('transcript') or '')
    hit = search_text(text, options)
    title = f"{call.get('name') or 'Unknown contact'} — call {call['call_id']}"
    fields = {key: call.get(key, '') for key in
              ('call_id', 'phone', 'started_at', 'direction', 'agent', 'aircall_tags', 'utm_group')}
    fields.update(search_phrases='; '.join(options['terms']), match_rule=options['rule'],
                  case_sensitive=options['case_sensitive'], whole_words=options['whole_words'],
                  matched_phrases='; '.join(hit['terms']), occurrences=hit['count'],
                  exported_at=datetime.now(timezone.utc).isoformat())
    details = ''.join(f'<dt>{escape(k.replace("_", " ").title())}</dt><dd>{escape(str(v))}</dd>'
                      for k, v in fields.items())
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            f'<title>{escape(title)}</title><style>'
            'body{font:16px/1.6 system-ui,sans-serif;max-width:960px;margin:32px auto;padding:0 24px;color:#172338}'
            'dt{font-weight:600}dd{margin:0 0 8px;overflow-wrap:anywhere}'
            'pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}'
            'mark{background:#ffdf70;color:#172338;padding:0 2px}'
            '@media print{mark{print-color-adjust:exact;-webkit-print-color-adjust:exact}}'
            '</style></head><body>'
            f'<h1>{escape(title)}</h1><dl>{details}</dl>'
            '<p>Literal transcript matches, not an inferred customer status. Original wording is preserved.</p>'
            f'<pre>{highlighted_body(text, hit["hits"])}</pre></body></html>')


def search_export_parts(calls, options, metadata=None):
    """Additional search files; raw/cohort exports are never filtered or changed."""
    from service import transcript_filename
    options = search_options(**options)
    evaluated = analyze_calls(calls, options)
    matched = [x for x in evaluated if x['search_status'] == 'match']
    parts = {}
    if matched:
        parts['Transcript_matches.csv'] = csv_bytes(result_rows(matched))
        for item in matched:
            call = item['call']
            path = transcript_filename(call, call).replace('transcripts/', 'highlighted_transcripts/', 1)
            parts[path.removesuffix('.txt') + '.html'] = highlighted_html(call, options).encode('utf-8')
    settings = dict(created_at=datetime.now(timezone.utc).isoformat(), search=options,
                    scope=metadata or {}, coverage=coverage(evaluated),
                    note='Only available, nonblank transcripts were searched. Missing or unrequested transcripts are not negative results. Phrases are literal; whitespace is flexible. Excerpts show the first five occurrences; HTML contains all highlights.')
    parts['Transcript_search_settings.json'] = json.dumps(settings, indent=2, ensure_ascii=False).encode('utf-8')
    return parts
