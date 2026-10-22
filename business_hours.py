"""Classify lead arrivals and measure after-hours response from next opening."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from core import parse_dt

WEEKDAYS=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
DEFAULT_HOURS={'days':[0,1,2,3,4],'open':'09:00','close':'17:00','timezone':'America/New_York'}


def validate_hours(value=None):
    hours={**DEFAULT_HOURS,**(value or {})}
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
    return {**hours,'days':sorted(set(days)),'open':opening.isoformat(timespec='minutes'),
            'close':closing.isoformat(timespec='minutes')}


def after_hours_details(person,speed,hours=None):
    hours=validate_hours(hours)
    result=dict(after_hours_lead='Unknown',next_workday_start=None,
                after_hours_response='Unknown creation time',after_hours_response_seconds=None,
                after_hours_response_color='',business_timezone=hours['timezone'],
                business_schedule=', '.join(WEEKDAYS[d][:3] for d in hours['days'])+' '+hours['open']+'–'+hours['close'])
    created=parse_dt(person.get('created_at'))
    if not created: return result
    zone=ZoneInfo(hours['timezone']); local=created.astimezone(zone)
    opening=time.fromisoformat(hours['open']); closing=time.fromisoformat(hours['close'])
    if local.weekday() in hours['days'] and opening<=local.time()<closing:
        return {**result,'after_hours_lead':'No','after_hours_response':'Not applicable'}
    result['after_hours_lead']='Yes'
    for offset in range(8):
        day=local.date()+timedelta(days=offset)
        if day.weekday() not in hours['days']: continue
        candidate=datetime.combine(day,opening,zone).astimezone(timezone.utc)
        if candidate>=created:
            result['next_workday_start']=candidate.isoformat()
            break
    called=parse_dt(speed.get('first_called_at'))
    if not called:
        result['after_hours_response']=speed.get('speed_to_lead_status','Not fetched')
        return result
    if called<created:
        result['after_hours_response']='Unknown: call precedes creation'
        return result
    elapsed=(called-candidate).total_seconds()
    # A response before opening is explicitly identified, never a negative delay.
    result['after_hours_response_seconds']=max(0,elapsed)
    result['after_hours_response_color']='Green' if elapsed<=300 else 'Yellow' if elapsed<=600 else 'Red'
    if elapsed<0:
        result['after_hours_response']='Called before opening'
    else:
        h,rest=divmod(int(elapsed),3600); m,s=divmod(rest,60)
        result['after_hours_response']=f'{h}h {m:02d}m {s:02d}s'
    return result
