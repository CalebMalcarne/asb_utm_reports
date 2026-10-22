"""Session-owned background lookup. Workers never access Streamlit state."""
import copy
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from threading import Event, Lock, Thread

from api import Aircall, APIError
from core import parse_dt, phone, six_month_boundary
from report_display import speed_to_lead


def pending_result(message='Loading…'):
    return dict(first_called_at=None, first_call_id=None, speed_to_lead_seconds=None,
                speed_to_lead=message, speed_to_lead_color='', speed_to_lead_status=message)


def lookup_key(report, settings):
    # Credentials affect cache identity but are never exposed in the UI or exports.
    value=[report['meta']['fetched_at'],report['meta']['demo'],
           [(r['person_id'],r['created_at'],r['phones']) for r in report['rows']],
           settings.get('aircall_id',''),settings.get('aircall_token','')]
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


class SpeedLookup:
    def __init__(self, report, settings, client=None, now=None):
        self.rows=copy.deepcopy(report['rows'])
        self.credentials=(settings.get('aircall_id',''),settings.get('aircall_token',''))
        self.client=client
        self.end=now or datetime.now(timezone.utc)
        self.cancelled=Event()
        self.lock=Lock()
        self.results={}
        self.done=False
        self.started=False

    def start(self):
        if not self.started:
            self.started=True
            self.thread=Thread(target=self._run,daemon=True,name='aircall-speed-lookup')
            self.thread.start()

    def snapshot(self):
        with self.lock:
            return dict(self.results),self.done

    def _run(self):
        owners=defaultdict(set)
        for row in self.rows:
            for number in row['phones']: owners[number].add(row['person_id'])
        failure=None
        try:
            client=self.client or Aircall(*self.credentials)
            for row in self.rows:
                if self.cancelled.is_set(): break
                if failure:
                    result=pending_result(failure)
                else:
                    try:
                        result=self._person(row,owners,client)
                    except APIError as exc:
                        failure=f'Aircall lookup failed (HTTP {exc.status})' if exc.status else 'Aircall lookup failed; retry'
                        result=pending_result(failure)
                with self.lock: self.results[row['person_id']]=result
        except Exception:
            # No raw exception text: third-party errors can contain credentials.
            failure='Aircall lookup failed; check Settings and retry'
        finally:
            with self.lock:
                if failure:
                    for row in self.rows:
                        self.results.setdefault(row['person_id'],pending_result(failure))
                self.done=True
            if self.client is None and 'client' in locals(): client.session.close()

    def _person(self,row,owners,client):
        created=parse_dt(row.get('created_at'))
        if not created: return pending_result('Unknown creation time')
        if not row['phones']: return pending_result('Unknown: no valid phone')
        if any(len(owners[p])>1 for p in row['phones']):
            return pending_result('Unknown: shared phone — review')
        if created<six_month_boundary(self.end):
            return pending_result('Unknown: creation outside Aircall history')
        if created>=self.end: return pending_result('No outbound call as of lookup')
        calls=[]
        for number in row['phones']:
            if self.cancelled.is_set(): return pending_result('Lookup cancelled')
            for call in client.calls(number,created.timestamp(),self.end.timestamp()):
                if phone(call.get('raw_digits',''))!=number: continue
                calls.append(dict(person_id=row['person_id'],call_id=str(call['id']),
                                  direction=call.get('direction'),
                                  started_at=call.get('started_at') or call.get('created_at')))
        result=speed_to_lead({**row,'activity_start':created.isoformat(),
                              'activity_end':self.end.isoformat(),'aircall_status':'complete'},calls)
        if result['speed_to_lead']=='No outbound call in fetched period':
            result.update(speed_to_lead='No outbound call as of lookup',speed_to_lead_status='No outbound call as of lookup')
        return result
