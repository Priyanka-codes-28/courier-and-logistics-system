from datetime import timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

def to_ist(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)

def format_ist(dt, fmt="%b %d, %Y %I:%M %p"):
    ist_dt = to_ist(dt)
    if ist_dt is None:
        return ""
    return ist_dt.strftime(fmt)