"""Weekly working hours and the 24-elapsed-hour after-hours response target."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from core import parse_dt

WEEKDAYS=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
DEFAULT_HOURS={'days':[0,1,2,3,4,5],'open':'09:00','close':'18:00',
               'overrides':{'5':{'open':'11:00','close':'14:00'}},'timezone':'America/New_York'}


def migrate_hours(value):
    # Replace the former app default even when it was saved in an existing install.
    # Other deliberately customized schedules remain intact.
    if value and value.get('days')==[0,1,2,3,4] and value.get('open')=='09:00' and value.get('close')=='17:00' and 'overrides' not in value:
        return {**DEFAULT_HOURS,'timezone':value.get('timezone','America/New_York')}
    return value


def day_hours(hours,day):
    override=hours.get('overrides',{}).get(str(day),{})
    return time.fromisoformat(override.get('open',hours['open'])),time.fromisoformat(override.get('close',hours['close']))


def schedule_label(hours):
    return '; '.join(WEEKDAYS[d][:3]+' '+day_hours(hours,d)[0].strftime('%H:%M')+'–'+day_hours(hours,d)[1].strftime('%H:%M') for d in hours['days'])


def validate_hours(value=None):
    hours={**DEFAULT_HOURS,**(value or {})}
    if value is not None: hours['overrides']=value.get('overrides',{})
    days=hours['days']
    if not days or any(type(day) is not int or day not in range(7) for day in days):
        raise ValueError('Select at least one business day.')
    try:
        opening=time.fromisoformat(hours['open']); closing=time.fromisoformat(hours['close'])
        ZoneInfo(hours['timezone'])
    except (ValueError,TypeError,KeyError):
        raise ValueError('Choose valid business hours and timezone.') from None
    if opening.tzinfo or closing.tzinfo or closing<=opening:
        raise ValueError('Closing time must be after opening time on the same day.')
    overrides={}
    for day,entry in hours.get('overrides',{}).items():
        if str(day) not in {str(d) for d in range(7)}: raise ValueError('Invalid business day override.')
        try: a,b=time.fromisoformat(entry['open']),time.fromisoformat(entry['close'])
        except (ValueError,TypeError,KeyError): raise ValueError('Choose valid daily opening and closing times.') from None
        if a.tzinfo or b.tzinfo or b<=a: raise ValueError('Each closing time must be after its opening time.')
        overrides[str(day)]={'open':a.isoformat(timespec='minutes'),'close':b.isoformat(timespec='minutes')}
    return {**hours,'days':sorted(set(days)),'open':opening.isoformat(timespec='minutes'),
            'close':closing.isoformat(timespec='minutes'),'overrides':overrides}


def after_hours_details(person,speed,hours=None):
    hours=validate_hours(hours)
    result=dict(after_hours_lead='Unknown',next_workday_start=None,
                after_hours_response='Unknown creation time',after_hours_response_seconds=None,
                after_hours_response_color='',business_timezone=hours['timezone'],
                business_schedule=schedule_label(hours),response_deadline=None,
                response_target='Workday: green ≤5 min; yellow ≤10 min; red >10 min',response_status=speed.get('speed_to_lead_status','Not fetched'))
    created=parse_dt(person.get('created_at'))
    if not created: return result
    zone=ZoneInfo(hours['timezone']); local=created.astimezone(zone)
    opening,closing=day_hours(hours,local.weekday())
    if local.weekday() in hours['days'] and opening<=local.time()<closing:
        return {**result,'after_hours_lead':'No','after_hours_response':'Not applicable'}
    result['after_hours_lead']='Yes'
    deadline=created+timedelta(hours=24)
    result.update(response_deadline=deadline.isoformat(),response_target='24 elapsed hours from arrival',speed_to_lead_color='')
    for offset in range(8):
        day=local.date()+timedelta(days=offset)
        if day.weekday() not in hours['days']: continue
        candidate=datetime.combine(day,day_hours(hours,day.weekday())[0],zone).astimezone(timezone.utc)
        if candidate>=created:
            result['next_workday_start']=candidate.isoformat()
            break
    called=parse_dt(speed.get('first_called_at'))
    if not called:
        result['after_hours_response']=speed.get('speed_to_lead_status','Not fetched')
        checked=parse_dt(speed.get('first_call_checked_through'))
        if checked and speed.get('speed_to_lead_status') in ('No outbound call as of lookup','No outbound call in fetched period'):
            overdue=checked>deadline
            result.update(response_status='Overdue — no call as of lookup' if overdue else 'Awaiting callback — within 24h as of lookup',
                          after_hours_response_color='Red' if overdue else '',speed_to_lead_color='Red' if overdue else '')
        return result
    if called<created:
        result['after_hours_response']='Unknown: call precedes creation'
        return result
    elapsed=(called-created).total_seconds()
    color='Green' if elapsed<=86400 else 'Red'
    result.update(after_hours_response_seconds=elapsed,after_hours_response_color=color,speed_to_lead_color=color,
                  response_status='Called within 24h' if elapsed<=86400 else 'Late callback — over 24h')
    h,rest=divmod(int(elapsed),3600); m,s=divmod(rest,60)
    result['after_hours_response']=f'{h}h {m:02d}m {s:02d}s'
    return result
