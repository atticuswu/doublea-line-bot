from datetime import datetime, timedelta

import pytz
from googleapiclient.discovery import build

from google_auth import get_credentials

CALENDAR_ID = "atticus.wu@gmail.com"
WIFE_EMAIL = "angelsu0923@gmail.com"
TAIPEI_TZ = pytz.timezone("Asia/Taipei")


def list_events_for_date(date: datetime) -> list[dict]:
    """列出指定日期的所有行事曆事件（台北時間）。回傳 [{"title", "start_str"}, ...]"""
    service = build("calendar", "v3", credentials=get_credentials())

    # 當天 00:00 ~ 23:59:59
    day_start = TAIPEI_TZ.localize(datetime(date.year, date.month, date.day, 0, 0, 0))
    day_end = day_start + timedelta(days=1)

    result = (
        service.events()
        .list(
            calendarId=CALENDAR_ID,
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events = []
    for item in result.get("items", []):
        title = item.get("summary", "（無標題）")
        start = item["start"].get("dateTime") or item["start"].get("date", "")
        if start:
            try:
                dt = datetime.fromisoformat(start)
                start_str = dt.strftime("%H:%M")
            except ValueError:
                start_str = start
        else:
            start_str = ""
        events.append({"title": title, "start_str": start_str})

    return events


def create_calendar_event(event_data: dict) -> dict:
    """Create a Google Calendar event and invite Angel. Returns {"id": ..., "link": ...}"""
    service = build("calendar", "v3", credentials=get_credentials())

    body: dict = {
        "summary": event_data["title"],
        "start": {"dateTime": event_data["start"], "timeZone": "Asia/Taipei"},
        "end": {"dateTime": event_data["end"], "timeZone": "Asia/Taipei"},
        "attendees": [{"email": WIFE_EMAIL}],
        "guestsCanModifyEvent": True,
        "reminders": {"useDefault": True},
    }

    if event_data.get("location"):
        body["location"] = event_data["location"]

    if event_data.get("description"):
        body["description"] = event_data["description"]

    result = (
        service.events()
        .insert(calendarId=CALENDAR_ID, body=body, sendUpdates="all")
        .execute()
    )

    return {"id": result["id"], "link": result.get("htmlLink", "")}


def update_calendar_event(event_id: str, updates: dict) -> str:
    """Update start/end of an existing event. Returns updated event HTML link."""
    service = build("calendar", "v3", credentials=get_credentials())

    event = service.events().get(calendarId=CALENDAR_ID, eventId=event_id).execute()

    if updates.get("start"):
        event["start"] = {"dateTime": updates["start"], "timeZone": "Asia/Taipei"}
    if updates.get("end"):
        event["end"] = {"dateTime": updates["end"], "timeZone": "Asia/Taipei"}

    updated = (
        service.events()
        .update(calendarId=CALENDAR_ID, eventId=event_id, body=event, sendUpdates="all")
        .execute()
    )

    return updated.get("htmlLink", "")
