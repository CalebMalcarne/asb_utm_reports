"""SendGrid v3 mail. One POST; ambiguous failures are never retried automatically."""
import base64
import json
import re
import requests
from service import export_parts

class DeliveryUnknown(RuntimeError): pass

def recipients(text):
    values=list(dict.fromkeys(v.strip() for v in re.split(r'[,;\n]',text or '') if v.strip()))
    if not values or any(not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+',v) for v in values):
        raise ValueError('Enter valid recipient email addresses, separated by commas or newlines.')
    if len(values)>50: raise ValueError('This internal report tool supports up to 50 recipients per send.')
    return values

def make_payload(settings,to,subject,body,attachments=None):
    recipients(settings['sender_email'])
    if len(recipients(settings['sender_email']))!=1: raise ValueError('Enter one verified sender email.')
    if not settings.get('sendgrid_key'): raise ValueError('Save a SendGrid API key in Settings.')
    payload={'personalizations':[{'to':[{'email':address}]} for address in recipients(to)],
             'from':{'email':settings['sender_email'],'name':settings.get('sender_name') or 'ASB Reports'},
             'subject':subject,'content':[{'type':'text/plain','value':body}],
             'tracking_settings':{'click_tracking':{'enable':False},'open_tracking':{'enable':False}}}
    if attachments:
        payload['attachments']=[{'content':base64.b64encode(content).decode(),'type':'text/csv',
                                 'filename':name,'disposition':'attachment'} for name,content in attachments.items()]
    # Conservative headroom below SendGrid's 30MB entire-message limit.
    if len(json.dumps(payload).encode())>25_000_000: raise ValueError('Email exceeds the 25 MB application limit. Select fewer UTM groups or omit transcripts/details.')
    return payload

def send(settings,payload,post=None):
    post=post or requests.post
    base='https://api.eu.sendgrid.com' if settings.get('sendgrid_region')=='EU' else 'https://api.sendgrid.com'
    try:
        response=post(base+'/v3/mail/send',headers={'Authorization':'Bearer '+settings['sendgrid_key']},
                      json=payload,timeout=(15,90),allow_redirects=False)
    except requests.RequestException:
        raise DeliveryUnknown('Send outcome unknown after a connection error. Check SendGrid activity before resending.') from None
    if response.status_code==202: return response.headers.get('X-Message-Id','accepted')
    if response.status_code>=500:
        raise DeliveryUnknown(f'SendGrid HTTP {response.status_code}; outcome uncertain. Check SendGrid before resending.')
    raise ValueError(f'SendGrid rejected the request (HTTP {response.status_code}). Check API key permissions and verified sender; no automatic retry.')

def report_payload(settings,to,report,groups=None,matched_only=False,details=False,calls=False,transcripts=False):
    parts=export_parts(report,groups,matched_only,details,calls,transcripts)
    attachments={name:content for name,content in parts.items() if name.endswith('.csv')}
    body=(f'ASB UTM report\nPerson creation period: {report["meta"]["start"]} through {report["meta"]["end"]} (exclusive).\n'
          f'Pipedrive fetched: {report["meta"]["fetched_at"]}\n'
          'Tags represent current Pipedrive state, not the date of qualification or purchase.\n'
          'Summary totals include all UTM people in selected groups. Detail filters do not change the denominator.\n')
    if report['issues']: body+=f'\nReview: {len(report["issues"])} activity matching warnings. Shared phones are excluded.\n'
    if not calls: body+='Aircall activity is not attached.\n'
    return make_payload(settings,to,'ASB UTM report',body,attachments)
