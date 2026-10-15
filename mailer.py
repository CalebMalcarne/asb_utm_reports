"""SendGrid v3 mail. One POST; ambiguous failures are never retried automatically."""
import base64
import hashlib
import json
import re
import requests
from service import export_parts

class DeliveryUnknown(RuntimeError): pass

def normalize_key(value):
    key=(value or '').strip()
    if len(key)>1 and key[0]==key[-1] and key[0] in ('"', "'"): key=key[1:-1].strip()
    if key.lower().startswith('bearer '): key=key[7:].strip()
    if not key: raise ValueError('Enter the complete SendGrid API key in Settings, then save it.')
    if any(c.isspace() or ord(c)<33 or ord(c)>126 for c in key) or any(c in key for c in ('"', "'")):
        raise ValueError('The SendGrid key contains spaces, line breaks, quotes, or non-ASCII characters. Paste the complete key on one line.')
    return key

def sendgrid_endpoint(settings):
    region=settings.get('sendgrid_region','US')
    if region not in ('US','EU'): raise ValueError('Choose SendGrid US/global or EU in Settings.')
    return ('https://api.eu.sendgrid.com' if region=='EU' else 'https://api.sendgrid.com')+'/v3/mail/send'

def connection_label(settings):
    key=normalize_key(settings.get('sendgrid_key'))
    return f'{sendgrid_endpoint(settings)} · saved key fingerprint {hashlib.sha256(key.encode()).hexdigest()[:12]}'

def rejection_message(response,settings,key):
    """Only structured error messages; redact secrets before truncation or logging."""
    try: data=response.json()
    except (ValueError,AttributeError): data={}
    errors=data.get('errors',[]) if isinstance(data,dict) else []
    messages=[]
    for error in errors[:3] if isinstance(errors,list) else []:
        if not isinstance(error,dict) or not isinstance(error.get('message'),str): continue
        message=error['message']
        for secret in (key,settings.get('sendgrid_key',''),settings.get('pipedrive_token',''),settings.get('aircall_token','')):
            if secret: message=message.replace(secret,'[redacted]')
        message=re.sub(r'SG\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+','[redacted]',message)
        message=re.sub(r'(?i)Bearer\s+[^\s,;]+','Bearer [redacted]',message)
        messages.append(' '.join(message.split())[:400])
    status=response.status_code
    if status==401:
        guidance=('Authentication was rejected. Use the full secret from SendGrid Settings > API Keys, '
                  'not its name/ID or a Twilio SMS key. Save the new key and check its Mail Send permission. '
                  'Use US/global unless this is an EU regional subuser. If a new key still fails, check '
                  'SendGrid account status and IP Access Management; the provider message below may identify the cause.')
    elif status==403:
        guidance='Access was denied. Check the provider message for Mail Send permission, sender verification, account restrictions, or IP access rules.'
    else: guidance='The request was rejected. Check the provider message below.'
    detail=' SendGrid says: '+' | '.join(messages) if messages else ' No structured provider error was returned.'
    return f'SendGrid HTTP {status} at {sendgrid_endpoint(settings)}. {guidance}{detail}'

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

def _request(settings,payload,post=None,*,validation=False):
    post=post or requests.post
    key=normalize_key(settings.get('sendgrid_key'))
    endpoint=sendgrid_endpoint(settings)
    try:
        response=post(endpoint,headers={'Authorization':'Bearer '+key},
                      json=payload,timeout=(15,90),allow_redirects=False)
    except requests.RequestException:
        if validation: raise ValueError('SendGrid validation could not connect. No delivery was requested; retry the check.') from None
        raise DeliveryUnknown('Send outcome unknown after a connection error. Check SendGrid activity before resending.') from None
    if validation:
        if response.status_code==200: return 'Sandbox validation passed. No email was sent. This does not confirm delivery or all sender/account sending checks.'
        raise ValueError(rejection_message(response,settings,key))
    if response.status_code==202: return response.headers.get('X-Message-Id','accepted')
    if response.status_code>=500:
        raise DeliveryUnknown(f'SendGrid HTTP {response.status_code}; outcome uncertain. Check SendGrid before resending.')
    raise ValueError(rejection_message(response,settings,key))

def send(settings,payload,post=None):
    return _request(settings,payload,post)

def check_connection(settings,post=None):
    payload=make_payload(settings,settings['sender_email'],'ASB Reports connection check','SendGrid sandbox validation only.')
    payload['mail_settings']={'sandbox_mode':{'enable':True}}
    return _request(settings,payload,post,validation=True)

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

