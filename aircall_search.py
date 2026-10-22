"""Aircall-only retrieval; no Pipedrive account or person matching required."""
import copy
from datetime import datetime, timezone
from api import Aircall, APIError
from core import parse_dt, phone, six_month_boundary, csv_bytes
from transcript_search import analyze_calls, result_rows, search_export_parts

TAG_RULES = ['Any selected tag', 'All selected tags', 'Without selected tags']


def tag_matches(call, selected, rule):
    if rule not in TAG_RULES:
        raise ValueError('Choose a valid Aircall tag rule.')
    wanted = {str(x) for x in selected}
    actual = {str(t['id']) for t in (call.get('tags') or []) if 'id' in t}
    if not wanted:
        return True
    if rule == 'Without selected tags':
        return not bool(actual & wanted)
    return wanted <= actual if rule == 'All selected tags' else bool(actual & wanted)


def fetch_calls(settings, start, end, tag_ids=(), tag_rule=TAG_RULES[0],
                tag_names=None, progress=None, client=None):
    now = datetime.now(timezone.utc)
    if start < six_month_boundary(now):
        raise ValueError('Aircall exposes six months of call history; choose a more recent start.')
    if end <= start:
        raise ValueError('Activity end must be after start.')
    if end > now:
        raise ValueError('Activity end cannot be in the future.')
    # Validate even when the API returns no calls.
    tag_matches({}, tag_ids, tag_rule)
    progress = progress or (lambda text: None)
    ac = client or Aircall(settings['aircall_id'], settings['aircall_token'], progress)
    progress('Fetching all call pages in the selected period…')
    raw = ac.calls(None, start.timestamp(), end.timestamp())
    rows, seen, in_period = [], set(), 0
    for c in raw:
        cid = str(c['id'])
        if cid in seen:
            continue
        seen.add(cid)
        dt = parse_dt(c.get('started_at') or c.get('created_at'))
        if dt is None:
            raise APIError('A call has an unreadable timestamp; refusing an incomplete search.')
        if not start <= dt < end:
            continue
        in_period += 1
        if not tag_matches(c, tag_ids, tag_rule):
            continue
        contact = c.get('contact') or {}
        name = ' '.join(str(contact.get(k) or '').strip() for k in ('first_name', 'last_name')).strip()
        name = name or str(contact.get('company_name') or '') or 'Unknown contact'
        answered, ended = parse_dt(c.get('answered_at')), parse_dt(c.get('ended_at'))
        rows.append(dict(call_id=cid, name=name, contact_id=str(contact.get('id') or ''),
                         phone=phone(c.get('raw_digits')) or str(c.get('raw_digits') or ''),
                         started_at=dt.isoformat(), direction=c.get('direction', ''),
                         answered=bool(answered), duration_seconds=c.get('duration', 0),
                         answered_to_end_seconds=max(0, int((ended-answered).total_seconds())) if answered and ended else 0,
                         agent=(c.get('user') or {}).get('name') or 'Unknown/AI/system',
                         aircall_tag_ids=[str(t['id']) for t in (c.get('tags') or []) if 'id' in t],
                         aircall_tags='; '.join(str(t.get('name') or '') for t in (c.get('tags') or [])),
                         comments='\n'.join(str(t.get('content') or '') for t in (c.get('comments') or [])),
                         transcript_status='not requested', transcript=''))
    rows.sort(key=lambda c: (c['started_at'], c['call_id']))
    return dict(calls=rows, meta=dict(start=start.isoformat(), end=end.isoformat(),
                timezone=settings.get('timezone', 'UTC'), fetched_at=now.isoformat(),
                tag_ids=[str(x) for x in tag_ids], tag_names=tag_names or {},
                tag_rule=tag_rule, calls_in_period=in_period, calls_after_tag_filter=len(rows)))


def fetch_transcript_batch(dataset, settings, limit=100, progress=None, client=None):
    """Resume untouched calls only. Explicit statuses make partial coverage visible."""
    if not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError('Choose between 1 and 1000 transcript requests per batch.')
    out = copy.deepcopy(dataset)
    progress = progress or (lambda text: None)
    ac = client or Aircall(settings['aircall_id'], settings['aircall_token'], progress)
    pending = [c for c in out['calls'] if c.get('transcript_status') == 'not requested'][:limit]
    out.pop('fetch_error', None)
    for i, call in enumerate(pending, 1):
        progress(f'Retrieving transcript {i}/{len(pending)} in this batch…')
        try:
            result = ac.transcript(call['call_id'])
        except APIError as exc:
            # Preserve completed work; leave this and later calls resumable.
            out['fetch_error'] = str(exc)
            break
        call.update(transcript_status=result['status'], transcript=result['text'])
    out['meta']['transcripts_fetched_at'] = datetime.now(timezone.utc).isoformat()
    return out


def export_search(dataset, options, matching_only=True, include_text=True,
                  include_highlights=True, selected_ids=None):
    from service import transcript_filename
    evaluated = analyze_calls(dataset['calls'], options)
    chosen = [x for x in evaluated
              if (not options['terms'] or not matching_only or x['search_status'] == 'match')
              and (selected_ids is None or x['call']['call_id'] in set(selected_ids))]
    calls = [x['call'] for x in chosen]
    parts = {}
    if chosen:
        parts['Aircall_Activity.csv'] = csv_bytes(result_rows(chosen),zone=dataset['meta'].get('timezone','UTC'))
    if include_text:
        for item in chosen:
            call = item['call']
            if item['searchable']:
                parts[transcript_filename(call, call)] = call['transcript'].encode('utf-8')
    if include_highlights and options['terms']:
        parts.update(search_export_parts(calls, options, dataset['meta']))
    # Always include complete search coverage, even for a matching-only download.
    from transcript_search import coverage
    import json
    metadata = dict(report=dataset['meta'], search=options, coverage=coverage(evaluated),
                    matching_only=matching_only, selected_call_ids=selected_ids,
                    exported_calls=len(calls), created_at=datetime.now(timezone.utc).isoformat(),
                    fetch_error=dataset.get('fetch_error'),
                    note='Call tags reflect the fetched snapshot. Calls without searchable transcripts are unknown, not negative matches. Aircall contact names do not establish a Pipedrive identity.')
    parts['Aircall_search_settings.json'] = json.dumps(metadata, indent=2, ensure_ascii=False).encode('utf-8')
    return parts

