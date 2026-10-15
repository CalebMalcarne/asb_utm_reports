import pytest
import requests
from mailer import send,check_connection,connection_label,normalize_key,DeliveryUnknown

KEY='SG.synthetic_id.synthetic_secret'

def settings(**values):
    return {'sendgrid_key':KEY,'sendgrid_region':'US','sender_email':'reports@example.invalid',**values}

class Response:
    def __init__(self,status,message=None):
        self.status_code=status; self.headers={'X-Message-Id':'mock-id'}
        self.message=message
    def json(self): return {'errors':[{'message':self.message}]} if self.message else {}

@pytest.mark.parametrize('region,host',[('US','https://api.sendgrid.com'),('EU','https://api.eu.sendgrid.com')])
def test_sandbox_checks_actual_mail_endpoint_and_never_requests_delivery(region,host):
    calls=[]
    def post(url,**kwargs): calls.append((url,kwargs)); return Response(200)
    message=check_connection(settings(sendgrid_region=region),post)
    assert 'No email was sent' in message
    assert len(calls)==1
    url,request=calls[0]
    assert url==host+'/v3/mail/send'
    assert request['headers']['Authorization']=='Bearer '+KEY
    assert request['json']['mail_settings']['sandbox_mode']['enable'] is True
    assert 'attachments' not in request['json']
    assert not request['allow_redirects']
    assert 'sandbox_mode' not in settings()

@pytest.mark.parametrize('status,message',[
    (401,'The provided authorization grant is invalid, expired, or revoked.'),
    (401,'Account Disabled'),
    (403,'The from address does not match a verified Sender Identity.'),
])
def test_rejection_preserves_provider_diagnosis_and_endpoint_without_retry(status,message):
    calls=[]
    def post(*args,**kwargs): calls.append(kwargs);return Response(status,message)
    with pytest.raises(ValueError) as error: send(settings(),{},post)
    text=str(error.value)
    assert message in text and str(status) in text
    assert 'api.sendgrid.com/v3/mail/send' in text
    assert KEY not in text and len(calls)==1

def test_reflected_credentials_redacted_before_truncation():
    other='SG.some_other_id.some_other_secret'
    response=Response(401,'Bad token '+KEY+' '+other+' Bearer arbitrary-secret')
    with pytest.raises(ValueError) as error: send(settings(),{},lambda *a,**k:response)
    text=str(error.value)
    assert KEY not in text and other not in text and 'arbitrary-secret' not in text
    assert '[redacted]' in text

def test_pasted_wrappers_removed_and_long_keys_not_truncated():
    long_key=KEY+'x'*100
    assert normalize_key(' "Bearer '+long_key+'" ')==long_key
    calls=[]
    def post(*args,**kwargs): calls.append(kwargs);return Response(202)
    assert send(settings(sendgrid_key=' Bearer '+long_key+' '),{},post)=='mock-id'
    assert calls[0]['headers']['Authorization']=='Bearer '+long_key
    assert long_key not in connection_label(settings(sendgrid_key=long_key))
    assert connection_label(settings())!=connection_label(settings(sendgrid_key=long_key))

def test_malformed_key_rejected_locally_and_validation_timeouts_are_not_delivery_uncertain():
    def forbidden(*a,**k): pytest.fail('malformed credentials must not reach the API')
    with pytest.raises(ValueError): send(settings(sendgrid_key=KEY+'\nbroken'),{},forbidden)
    def timeout(*a,**k): raise requests.Timeout('sensitive request details')
    with pytest.raises(ValueError,match='No delivery was requested'): check_connection(settings(),timeout)
    with pytest.raises(DeliveryUnknown): send(settings(),{},timeout)

def test_sandbox_rejection_never_falls_back_to_real_send_or_other_region():
    calls=[]
    def post(url,**kwargs): calls.append((url,kwargs));return Response(401,'Unauthorized')
    with pytest.raises(ValueError): check_connection(settings(sendgrid_region='EU'),post)
    assert len(calls)==1
    assert calls[0][0]=='https://api.eu.sendgrid.com/v3/mail/send'
    assert calls[0][1]['json']['mail_settings']['sandbox_mode']['enable'] is True

def test_maximum_credits_is_classified_as_quota_not_invalid_key():
    calls=[]
    def post(*args,**kwargs):
        calls.append(kwargs)
        return Response(401,'Maximum credits exceeded')
    with pytest.raises(ValueError) as error: send(settings(),{},post)
    message=str(error.value)
    assert 'Sending allowance exceeded' in message
    assert 'Email API plan' in message and 'billing' in message
    assert 'Authentication was rejected' not in message
    assert 'Use the full secret' not in message
    assert 'SendGrid says: Maximum credits exceeded' in message
    assert len(calls)==1
