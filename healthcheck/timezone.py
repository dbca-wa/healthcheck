from datetime import datetime,UTC
from zoneinfo import ZoneInfo

from . import settings

def now():
    return datetime.now().astimezone(settings.TZ)

def get_timezone(tz):
    if not tz:
        return settings.TZ
    elif isinstance(tz,str):
        if tz == "UTC":
            return UTC
        else:
            return ZoneInfo(tz)
    else:
        return tz

def parse(dt,pattern="%Y-%m-%dT%H:%M:%S.%f",tz=None):
    tz = get_timezone(tz)

    if tz == settings.TZ:
        return datetime.strptime(dt,pattern).replace(tzinfo=settings.TZ)
    else:
        return datetime.strptime(dt,pattern).replace(tzinfo=tz).astz(settings.TZ)

def parse_time(dt,pattern="%H:%M:%S",tz=None):
    return parse(dt,pattern=pattern,tz=tz)

def format_datetime(dt=None,pattern="%Y-%m-%dT%H:%M:%S",tz=settings.TZ):
    if not dt:
        dt = now()
        dt_tzinfo = settings.TZ
    elif dt.tzinfo:
        dt_tzinfo = dt.tzinfo
    else:
        dt_tzinfo = settings.TZ
        dt = dt.replace(tzinfo=settings.TZ)

    tz = get_timezone(tz)

    if dt_tzinfo == tz:
        return dt.strftime(pattern)
    else:
        return dt.astz(tz).strftime(pattern)

def format_time(dt=None,pattern="%H:%M:%S",tz=settings.TZ):
    return format_datetime(dt=dt,pattern=pattern,tz=tz)

def fromtimestamp(timestamp,tz=None):
    tz = get_timezone(tz)
    if tz == settings.TZ:
        return datetime.fromtimestamp(timestamp).replace(tzinfo=tz)
    else:
        return datetime.strptime(dt,pattern).replace(tzinfo=tz).astz(settings.TZ)

