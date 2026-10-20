"""Read-only CRM/phone clients. Secrets never appear in error strings."""
import re
import math
import time
import requests

class APIError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

class Client:
    def __init__(self, base, headers=None, auth=None, progress=None, interval=1.05):
        self.base = base
        self.session = requests.Session()
        self.session.headers.update(headers or {})
        self.session.auth = auth
        self.progress = progress or (lambda text: None)
        self.interval, self.last = interval, 0

    def get(self, path, params=None):
        for attempt in range(6):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                r = self.session.get(self.base + path, params=params, timeout=(15, 90), allow_redirects=False)
            except requests.RequestException:
                if attempt == 5: raise APIError('Connection failed after retries.') from None
                time.sleep(min(2 ** attempt, 30))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                try: delay = max(1, float(r.headers.get('Retry-After', 2 ** attempt)))
                except ValueError: delay = 30
                if delay > 120 or attempt == 5:
                    raise APIError(f'API busy/quota exceeded (HTTP {r.status_code}). Retry later.', r.status_code)
                self.progress(f'Waiting {delay:.0f}s for API retry…')
                time.sleep(delay)
                continue
            if r.status_code >= 300:
                raise APIError(f'HTTP {r.status_code} from {path}; check credentials, access, or availability.', r.status_code)
            try: data = r.json()
            except ValueError: raise APIError('API returned invalid JSON.') from None
            if not isinstance(data, dict) or data.get('success') is False:
                raise APIError('API returned an unsuccessful response.')
            return data
        raise APIError('Request failed.')

class Pipedrive(Client):
    @staticmethod
    def normalize_company(company):
        value = (company or '').strip().lower()
        match = re.fullmatch(r'(?:https://)?([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)\.pipedrive\.com(?:/[^?#]*)?', value)
        if match: value = match.group(1)
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', value):
            raise ValueError('Enter your Pipedrive company subdomain or its https://company.pipedrive.com URL.')
        return value

    def __init__(self, company, token, progress=None):
        self.company = self.normalize_company(company)
        token = (token or '').strip()
        if not token: raise ValueError('Save a Pipedrive API token in Settings.')
        if any(c.isspace() or ord(c) < 32 or ord(c) > 126 for c in token) or token.startswith(('"', "'")):
            raise ValueError('Paste only the Pipedrive personal API token, without quotes, spaces, or a Bearer prefix.')
        super().__init__(f'https://{self.company}.pipedrive.com/api/', {'x-api-token': token}, progress=progress, interval=.3)

    def get(self, path, params=None, *, version='v2'):
        try:
            return super().get(f'{version}/{path}', params)
        except APIError as exc:
            if exc.status == 401:
                raise APIError(
                    f'Pipedrive rejected authentication (HTTP 401) for {self.company}.pipedrive.com. '
                    'In that same Pipedrive company, open Personal preferences > API and copy your current personal API token. '
                    'Enter it in Settings, then click Save & connect Pipedrive / refresh fields. '
                    'Use a personal API token, not an OAuth client secret or an Aircall/SendGrid key. '
                    'Tokens are specific to a user and company; an old or regenerated token will not work.', 401) from None
            if exc.status == 403:
                raise APIError('Pipedrive denied access (HTTP 403). Ask your Pipedrive admin to check API access and your user permissions.', 403) from None
            raise

    def current_user(self):
        data = self.get('users/me', version='v1').get('data')
        if not isinstance(data, dict) or not data.get('id'):
            raise APIError('Pipedrive returned an invalid account response.')
        return data

    def pages(self, resource, params=None):
        query = {'limit':500, **(params or {})}
        seen = set()
        while True:
            data = self.get(resource, query)
            rows = data.get('data') or []
            if not isinstance(rows, list): raise APIError('Unexpected Pipedrive response shape.')
            yield rows
            cursor = (data.get('additional_data') or {}).get('next_cursor')
            if not cursor: break
            if cursor in seen: raise APIError('Repeated Pipedrive cursor; refusing incomplete data.')
            seen.add(cursor)
            query['cursor'] = cursor

    def fields(self):
        return [f for page in self.pages('personFields') for f in page]

    def people(self, start, end, custom_codes):
        from core import parse_dt
        # Sort by creation; locally enforce creation bounds (updated_since is NOT creation).
        params = {'sort_by':'add_time', 'sort_direction':'desc', 'include_labels':'true', 'include_option_labels':'true'}
        if custom_codes: params['custom_fields'] = ','.join(sorted(set(custom_codes)))
        if len(set(custom_codes)) > 15: raise ValueError('Choose no more than 15 custom fields.')
        result = {}
        for page in self.pages('persons', params):
            crossed = False
            for p in page:
                dt = parse_dt(p.get('add_time'))
                if dt is None: raise APIError('A Pipedrive person has an unreadable creation timestamp.')
                if dt < start: crossed = True
                elif dt < end: result[str(p['id'])] = p
            self.progress(f'Pipedrive: {len(result):,} people in the selected creation period…')
            if crossed: break
        return list(result.values())

class Aircall(Client):
    CAP, PAGE_SIZE = 10000, 50
    def __init__(self, api_id, token, progress=None):
        if not api_id or not token: raise ValueError('Save the Aircall API ID and token in Settings.')
        super().__init__('https://api.aircall.io/v1/', auth=(api_id,token), progress=progress)

    def search_page(self, number, lo, hi, page=1):
        params = {'from':lo,'to':hi,'page':page,'per_page':self.PAGE_SIZE,'order':'asc'}
        if number:
            params['phone_number'] = number
        else:
            params['fetch_contact'] = 'true'
        return self.get('calls/search', params)

    def tags(self):
        result, page = {}, 1
        while True:
            data = self.get('tags', {'page':page,'per_page':self.PAGE_SIZE})
            rows = data.get('tags') or []
            if not isinstance(rows, list):
                raise APIError('Unexpected Aircall tag response.')
            before = len(result)
            for tag in rows:
                result[str(tag['id'])] = tag
            if not (data.get('meta') or {}).get('next_page_link'):
                return list(result.values())
            if len(result) == before:
                raise APIError('Aircall tag pagination did not advance; retry.')
            page += 1

    def calls(self, number, start, end):
        result = {}
        def split(lo,hi):
            if lo >= hi: raise APIError('One second exceeds the Aircall retrieval cap; an export is required.')
            mid=(lo+hi)//2
            visit(lo,mid); visit(mid+1,hi)
        def visit(lo,hi):
            data=self.search_page(number,lo,hi)
            total=(data.get('meta') or {}).get('total')
            if total is not None and int(total)>=self.CAP:
                split(lo,hi); return
            rows=list(data.get('calls') or [])
            page=1
            while (data.get('meta') or {}).get('next_page_link'):
                if len(rows)>=self.CAP:
                    split(lo,hi); return
                page+=1
                if page>self.CAP//self.PAGE_SIZE+1: raise APIError('Unexpected Aircall pagination.')
                data=self.search_page(number,lo,hi,page)
                rows.extend(data.get('calls') or [])
            if len(rows)>=self.CAP:
                split(lo,hi); return
            if total is not None and len(rows)<int(total): raise APIError('Aircall pagination/count changed; retry the import.')
            result.update({str(c['id']):c for c in rows})
        if start<end: visit(int(start),math.ceil(end)-1)
        return list(result.values())

    def transcript(self, call_id):
        try:
            data=self.get(f'calls/{int(call_id)}/transcription',{'mode':'async'})
        except APIError as exc:
            if exc.status==404: return {'status':'unavailable','text':'','raw':None}
            if exc.status==403: return {'status':'forbidden','text':'','raw':None}
            raise
        obj=data.get('transcription') or data
        content=obj.get('content') or {}
        utterances=content.get('utterances',[]) if isinstance(content,dict) else []
        lines=[]
        for u in utterances:
            who=u.get('participant_type','unknown')
            ident=u.get('user_id') or u.get('phone_number')
            if ident: who+=f' ({ident})'
            position=u.get('start_time',u.get('timestamp',''))
            lines.append(f'[{position}] {who}: {u.get("text","")}')
        text='\n'.join(lines)
        return {'status':'available' if text else 'empty','text':text,'raw':data}
