"""UI shared by standalone Aircall search and UTM transcript exports."""
import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

from api import Aircall
from core import PRESETS, period, parse_dt, csv_bytes
from service import zip_parts
from transcript_search import (search_options, analyze_calls, coverage, result_rows,
                               highlighted_body, search_export_parts)
from aircall_search import TAG_RULES, fetch_calls, fetch_transcript_batch, export_search
from report_display import table_rows


def search_controls(prefix):
    terms = st.text_area('Keywords or phrases (one per line)', key=prefix+'_terms',
                         placeholder='retirement protection\nsend me information',
                         help='Literal phrases, not regex. Leave blank to export calls without a text filter.')
    a, b, c = st.columns(3)
    with a:
        rule = st.selectbox('Phrase match rule', ['any', 'all'],
                            format_func=lambda x: 'Any phrase' if x == 'any' else 'All phrases',
                            key=prefix+'_rule')
    with b:
        case = st.checkbox('Case-sensitive search', False, key=prefix+'_case')
    with c:
        whole = st.checkbox('Whole words / phrase boundaries', True, key=prefix+'_whole')
    st.caption('Matches use the available transcript text. Punctuation is literal; spaces and line breaks are interchangeable. Search changes do not make API requests.')
    try:
        return search_options(terms, rule, case, whole)
    except ValueError as exc:
        st.error(str(exc))
        return None


def show_results(calls, options, prefix, matching_only=True, zone='UTC'):
    evaluated = analyze_calls(calls, options)
    if not options['terms']:
        st.info('No phrases entered: no text filter is applied. Add a keyword or phrase to search.')
    stats = coverage(evaluated)
    cols = st.columns(4)
    for col, label, key in zip(cols, ['Calls in scope', 'Searchable transcripts', 'Phrase-matching calls', 'Not searchable'],
                               ['calls', 'searchable', 'matching_calls', 'not_searchable']):
        col.metric(label, stats[key])
    if stats['not_searchable']:
        st.warning(f'{stats["not_searchable"]} calls do not have searchable transcripts. They are not counted as negative matches.')
    st.caption('Transcript status counts: '+', '.join(f'{k}: {v}' for k, v in stats['transcript_statuses'].items()))
    visible = [x for x in evaluated if not options['terms'] or not matching_only or x['search_status'] == 'match']
    st.caption('Times: '+zone)
    st.dataframe(pd.DataFrame(table_rows(result_rows(visible),zone)), hide_index=True, width='stretch')
    if not visible:
        st.info('No calls match this view. Check transcript coverage before drawing conclusions.')
        return evaluated, visible
    lookup = {x['call']['call_id']: x for x in visible}
    chosen = st.selectbox('Preview call transcript', list(lookup),
                          format_func=lambda cid: f'{lookup[cid]["call"].get("name", "Unknown")} · call {cid}',
                          key=prefix+'_preview')
    item = lookup[chosen]
    if item['searchable']:
        with st.expander('Transcript with highlighted matches', expanded=True):
            # All dynamic content is HTML-escaped in highlighted_body.
            body = highlighted_body(item['call']['transcript'], item['search']['hits'])
            st.html('<pre style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:480px;overflow:auto">'
                    + body + '</pre>')
    else:
        st.info('Transcript: '+str(item['call'].get('transcript_status', 'unknown')))
    return evaluated, visible


def report_search_ui(calls, options, prefix, metadata):
    st.subheader('Transcript keyword & phrase results')
    st.caption('Search covers fetched calls for this UTM tab only. It does not change person/tag statistics or the standard full-report files.')
    only = st.checkbox('Show phrase matches only', True, key=prefix+'_matching')
    show_results(calls, options, prefix, only, metadata.get('timezone','UTC'))
    if options['terms']:
        parts = search_export_parts(calls, options, metadata)
        if 'Transcript_matches.csv' in parts:
            st.download_button('Matching transcripts ZIP', zip_parts(parts),
                               'matching_transcripts.zip', 'application/zip',
                               key=prefix+'_download', on_click='ignore')


def render_aircall_search(settings, show_error):
    st.title('Aircall export & search')
    st.caption('Independent of Pipedrive and UTM reports. Search call dates and current Aircall call tags, then search available transcripts.')
    demo = st.checkbox('Use synthetic Aircall demo', False, key='ac_demo')
    from demo import DemoAircallSearch
    def client(progress=None):
        return DemoAircallSearch() if demo else Aircall(settings['aircall_id'], settings['aircall_token'], progress)

    # Never reuse standalone call data or tag IDs across different connections.
    identity = 'demo' if demo else hashlib.sha256(
        (settings.get('aircall_id', '')+'\0'+settings.get('aircall_token', '')).encode()).hexdigest()
    if st.session_state.get('ac_identity') != identity:
        for key in ['ac_dataset', 'ac_catalog', 'ac_tags']:
            st.session_state.pop(key, None)
        st.session_state.ac_identity = identity
    if demo:
        st.info('Synthetic calls only. No API requests are made.')
        st.session_state.ac_catalog = {str(t['id']): t['name'] for t in client().tags()}
    if st.button('Refresh Aircall tags', key='ac_refresh_tags'):
        try:
            tags = client().tags()
            st.session_state.ac_catalog = {str(t['id']): str(t['name']) for t in tags}
            st.session_state.ac_tags = [x for x in st.session_state.get('ac_tags', []) if x in st.session_state.ac_catalog]
        except Exception as exc:
            show_error(exc)
    catalog = st.session_state.get('ac_catalog', {})
    tag_ids = st.multiselect('Aircall tags (blank = any call)', list(catalog),
                            format_func=lambda x: f'{catalog[x]} [{x}]', key='ac_tags')
    if not catalog:
        st.caption('Refresh tags to choose a tag filter. You can fetch all calls without loading tags.')
    tag_rule = st.selectbox('Aircall tag rule', TAG_RULES, key='ac_tag_rule')

    tz = settings.get('timezone', 'UTC')
    now = datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(tz)).date()
    preset = st.selectbox('Call activity period', PRESETS, index=1, key='ac_period')
    custom = None
    if preset == 'Custom':
        a, b = st.columns(2)
        with a:
            begin = st.date_input('Calls from', today-timedelta(days=7), key='ac_begin')
        with b:
            finish = st.date_input('Calls through (inclusive)', today, key='ac_finish')
        custom = (begin, finish)
    try:
        start, end, a, b = period(preset, tz, now, custom)
    except ValueError as exc:
        show_error(exc)
        return
    st.caption(f'{a} through {b} · {tz}. The API exposes the last six months. Blank tags include untagged calls.')
    batch = int(st.number_input('Transcript requests per batch', min_value=1, max_value=1000,
                                value=100, step=1, key='ac_batch'))
    get_text = st.checkbox('Fetch transcripts with calls', True, key='ac_get_text')
    st.caption('Retrieval is rate-limited and may take minutes. Continue with another batch for large ranges; pending calls stay visible.')
    if st.button('Fetch Aircall calls', type='primary', key='ac_fetch'):
        try:
            with st.status('Retrieving Aircall data…', expanded=True) as status:
                message = st.empty()
                ac = client(message.write)
                dataset = fetch_calls(settings, start, end, tag_ids, tag_rule,
                                      {t: catalog[t] for t in tag_ids}, message.write, ac)
                dataset['meta']['demo'] = demo
                if get_text:
                    dataset = fetch_transcript_batch(dataset, settings, batch, message.write, ac)
                st.session_state.ac_dataset = dataset
                status.update(label='Calls loaded; review transcript coverage below',
                              state='error' if dataset.get('fetch_error') else 'complete')
        except Exception as exc:
            show_error(exc)
    dataset = st.session_state.get('ac_dataset')
    if not dataset:
        st.info('Fetch Aircall calls to begin. API keys are configured in Settings.')
        return
    meta = dataset['meta']
    st.caption(f'Loaded calls: {meta["start"]} → {meta["end"]} (exclusive) · {meta["timezone"]} · fetched {meta["fetched_at"]}')
    st.caption(f'Loaded tag filter: {meta["tag_rule"]} · '+(', '.join(meta['tag_names'].values()) or 'Any call'))
    if (parse_dt(meta['start']) != start or abs((parse_dt(meta['end'])-end).total_seconds()) > 60
            or set(meta['tag_ids']) != set(tag_ids) or meta['tag_rule'] != tag_rule or meta['timezone'] != tz):
        st.warning('Retrieval settings differ from the loaded data. Fetch again to apply them. Downloads use the loaded snapshot shown above.')
    if dataset.get('fetch_error'):
        st.error('Transcript batch stopped: '+dataset['fetch_error'])
    pending = sum(c['transcript_status'] == 'not requested' for c in dataset['calls'])
    st.caption(f'{meta["calls_in_period"]} calls in period; {len(dataset["calls"])} after tag filtering; {pending} transcripts not yet requested.')
    if st.button('Fetch next transcript batch', disabled=not pending, key='ac_next'):
        try:
            with st.status('Fetching next transcript batch…', expanded=True) as status:
                message = st.empty()
                st.session_state.ac_dataset = fetch_transcript_batch(dataset, settings, batch, message.write, client(message.write))
                status.update(label='Batch finished', state='complete')
            st.rerun()
        except Exception as exc:
            show_error(exc)
    st.subheader('Keyword & phrase search')
    options = search_controls('ac_search')
    if options is None:
        return
    matching_only = st.checkbox('Show/export phrase matches only', True, key='ac_only',
                               disabled=not options['terms'])
    _, visible = show_results(dataset['calls'], options, 'ac_results', matching_only, meta['timezone'])
    st.subheader('Export options')
    include_text = st.checkbox('Include original transcript TXT files', True, key='ac_export_text')
    include_highlights = st.checkbox('Include highlighted transcript HTML files', True,
                                     disabled=not options['terms'], key='ac_export_html')
    scope = st.radio('Export calls', ['All displayed calls', 'Choose displayed calls'],
                     horizontal=True, key='ac_export_scope')
    selected_ids = None
    if scope == 'Choose displayed calls':
        names = {x['call']['call_id']: x['call'].get('name', 'Unknown') for x in visible}
        if 'ac_export_ids' in st.session_state:
            st.session_state.ac_export_ids = [cid for cid in st.session_state.ac_export_ids if cid in names]
        selected_ids = st.multiselect('Calls to export', list(names),
                                      format_func=lambda cid: f'{names[cid]} · call {cid}', key='ac_export_ids')
    parts = export_search(dataset, options, matching_only, include_text, include_highlights, selected_ids)
    label = meta['start'][:10]+'_'+meta['end'][:10]
    a, b = st.columns(2)
    with a:
        if 'Aircall_Activity.csv' in parts:
            st.download_button('Aircall results CSV', parts['Aircall_Activity.csv'],
                               'aircall_'+label+'.csv', 'text/csv', key='ac_csv', on_click='ignore')
    with b:
        st.download_button('Aircall export ZIP', zip_parts(parts),
                           'aircall_'+label+'.zip', 'application/zip', key='ac_zip', on_click='ignore')
    st.caption('Exports follow the displayed search and call selection. HTML highlights every occurrence and opens offline in a browser. CSV/TXT cannot retain colored highlighting. Empty data files are omitted; search settings and coverage are always included.')
    if st.button('Clear Aircall search data', key='ac_clear'):
        st.session_state.pop('ac_dataset', None)
        st.rerun()

