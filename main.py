import asyncio
import hashlib
import hmac as _hmac
import io
import json as _json
import os
import random
import time
import urllib.parse
from datetime import datetime, timedelta

import pytz
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from google_auth import get_credentials
from googleapiclient.http import MediaIoBaseDownload
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhook import WebhookParser
from linebot.v3.webhooks import ImageMessageContent, MessageEvent, TextMessageContent

import bot_config
import photo_archive
from calendar_service import create_calendar_event, list_events_for_date, update_calendar_event
from mom_photo import build_drive_service, handle_mom_message
from event_parser import parse_message, parse_modification
from state_service import (
    add_reminder,
    get_due_reminders,
    load_last_event,
    mark_reminder_sent,
    save_last_event,
)
from todo_service import (
    TASKS_URL,
    add_task,
    complete_task_by_index,
    complete_task_by_keyword,
    get_pending_tasks,
)

load_dotenv()

LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]

app = FastAPI(title="DoubleA LINE Bot")
parser = WebhookParser(LINE_CHANNEL_SECRET)
line_config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

TAIPEI_TZ = pytz.timezone("Asia/Taipei")
REMINDER_MINUTES = 120


# ── LINE push ─────────────────────────────────────────────────────────────────

def _push_line(chat_id: str, text: str) -> None:
    try:
        with ApiClient(line_config) as api_client:
            MessagingApi(api_client).push_message(
                PushMessageRequest(
                    to=chat_id,
                    messages=[TextMessage(text=text)],
                )
            )
    except Exception as e:
        print(f"[DoubleA] push_message 失敗：{e}")
        raise


def _reply_line(reply_token: str, text: str) -> None:
    """使用 reply_token 回覆（免費，不計入月限額）。"""
    try:
        with ApiClient(line_config) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=text)],
                )
            )
    except Exception as e:
        print(f"[DoubleA] reply_message 失敗：{e}")
        raise


def _get_chat_id(event: MessageEvent) -> str:
    source = event.source
    if hasattr(source, "group_id") and source.group_id:
        return source.group_id
    if hasattr(source, "room_id") and source.room_id:
        return source.room_id
    return source.user_id


def _generate_share_link(ev: dict) -> str:
    """產生 Google Calendar「加入行事曆」分享連結（任何人點擊即可加入，不需 email）。"""
    start_dt = datetime.fromisoformat(ev["start"])
    end_dt = datetime.fromisoformat(ev["end"])

    # 轉成 UTC（Google Calendar URL 用 Z 結尾）
    if start_dt.tzinfo is None:
        start_dt = TAIPEI_TZ.localize(start_dt)
    if end_dt.tzinfo is None:
        end_dt = TAIPEI_TZ.localize(end_dt)

    start_utc = start_dt.astimezone(pytz.utc)
    end_utc = end_dt.astimezone(pytz.utc)
    dates = f"{start_utc.strftime('%Y%m%dT%H%M%SZ')}/{end_utc.strftime('%Y%m%dT%H%M%SZ')}"

    params: dict = {"action": "TEMPLATE", "text": ev["title"], "dates": dates}
    if ev.get("location"):
        params["location"] = ev["location"]
    if ev.get("description"):
        params["details"] = ev["description"]

    return "https://calendar.google.com/calendar/render?" + urllib.parse.urlencode(params)


# ── Formatters ────────────────────────────────────────────────────────────────

def _format_calendar_confirmation(event_data: dict, event_link: str) -> str:
    start_dt = datetime.fromisoformat(event_data["start"])
    date_str = start_dt.strftime("%-m月%-d日 %H:%M")
    location_line = f"\n📍 {event_data['location']}" if event_data.get("location") else ""
    link_line = f"\n\n🔗 {event_link}" if event_link else ""
    share_link = _generate_share_link(event_data)
    return (
        f"📅 已加入行事曆！\n\n"
        f"【{event_data['title']}】\n"
        f"🗓 {date_str}{location_line}"
        f"{link_line}\n\n"
        f"✅ Angel 已收到邀請\n"
        f"⏰ 將於開始前 2 小時提醒\n\n"
        f"📤 分享給其他人（點擊即可加入行事曆）\n{share_link}"
    )


def _format_multi_calendar_confirmation(results: list[dict]) -> str:
    """多事件行事曆確認訊息。results 是 [{"event_data": ..., "link": ...}, ...]"""
    count = len(results)
    lines = [f"📅 已加入 {count} 個行事曆！\n"]
    for i, r in enumerate(results, 1):
        ev = r["event_data"]
        start_dt = datetime.fromisoformat(ev["start"])
        date_str = start_dt.strftime("%-m月%-d日 %H:%M")
        location_line = f"\n   📍 {ev['location']}" if ev.get("location") else ""
        share_link = _generate_share_link(ev)
        lines.append(
            f"{i}.【{ev['title']}】\n"
            f"   🗓 {date_str}{location_line}\n"
            f"   🔗 {r['link']}\n"
            f"   📤 {share_link}"
        )
    lines.append("\n✅ Angel 已收到邀請\n⏰ 將於各活動開始前 2 小時提醒")
    return "\n\n".join(lines)


def _format_todo_list(tasks: list) -> str:
    if not tasks:
        return "✅ 目前沒有待辦事項！"
    lines = ["📋 待辦清單\n"]
    for i, t in enumerate(tasks, 1):
        lines.append(f"{i}. {t['title']}")
    lines.append(f"\n🔗 {TASKS_URL}")
    return "\n".join(lines)


# ── Reminder helpers ──────────────────────────────────────────────────────────

def schedule_event_reminder(chat_id: str, event_data: dict) -> None:
    start_dt = datetime.fromisoformat(event_data["start"])
    reminder_dt = start_dt - timedelta(minutes=REMINDER_MINUTES)
    now = datetime.now(TAIPEI_TZ)
    if reminder_dt <= now:
        print(f"[DoubleA] 活動太近，略過排程提醒")
        return
    add_reminder(chat_id, event_data, reminder_dt)
    print(f"[DoubleA] 提醒已排程：{reminder_dt.strftime('%-m月%-d日 %H:%M')}")


def send_event_reminder(chat_id: str, title: str, start_str: str) -> None:
    start_dt = datetime.fromisoformat(start_str)
    date_str = start_dt.strftime("%-m月%-d日 %H:%M")
    text = (
        f"⏰ 提醒！\n\n"
        f"【{title}】\n"
        f"🗓 {date_str} 即將開始\n"
        f"還有 2 小時！"
    )
    _push_line(chat_id, text)


# ── Commands ──────────────────────────────────────────────────────────────────

def handle_command(text: str, chat_id: str, reply_token: str | None = None) -> bool:
    def _cmd_respond(msg: str) -> None:
        if reply_token:
            try:
                _reply_line(reply_token, msg)
                return
            except Exception:
                pass
        try:
            _push_line(chat_id, msg)
        except Exception:
            pass

    if text.strip() in ("待辦清單", "待辦", "todo", "TODO"):
        try:
            tasks = get_pending_tasks()
            _cmd_respond(_format_todo_list(tasks))
        except Exception as e:
            _cmd_respond(f"⚠️ 無法取得待辦清單：{e}")
        return True

    if text.startswith("完成 ") or text.startswith("done "):
        keyword = text.split(" ", 1)[1].strip()
        try:
            title = complete_task_by_keyword(keyword)
            if title:
                msg = f"✅ 已完成：【{title}】\n\n{_cheer_complete()}"
            else:
                msg = f"❓ 找不到包含「{keyword}」的待辦事項"
            _cmd_respond(msg)
        except Exception as e:
            _cmd_respond(f"⚠️ 標記失敗：{e}")
        return True

    if text.lower().startswith("del "):
        try:
            n = int(text.split(" ", 1)[1].strip())
            title = complete_task_by_index(n)
            if title:
                msg = f"✅ 已完成：【{title}】\n\n{_cheer_complete()}"
            else:
                msg = f"❓ 找不到第 {n} 項待辦事項"
            _cmd_respond(msg)
        except ValueError:
            _cmd_respond("❓ 格式錯誤，請輸入「del 1」")
        except Exception as e:
            _cmd_respond(f"⚠️ 標記失敗：{e}")
        return True

    return False


# ── Emotional value ───────────────────────────────────────────────────────────

_COMPLETE_CHEERS = [
    "🌟 今天又完成一件事了，超棒的！",
    "💪 一件一件解決，你們真的很厲害！",
    "✨ 搞定！腦袋可以去做更重要的事了。",
    "🎉 完成！每一個小進步都值得慶祝。",
    "👏 做到了！今天又往前進了一步。",
    "🙌 太好了，又少一件煩惱！",
    "⚡ 效率一流，這件事正式關閉！",
]

_CALENDAR_CHEERS = [
    "🧠 記下來了！腦袋的空間留給更重要的事。",
    "📆 安排好了，就不用一直惦記著這件事了。",
    "👍 掌握住了，時間到我來提醒你們。",
    "✅ 好，這件事交給行事曆管，放心吧！",
]

_TODO_CHEERS = [
    "📝 記下來了！不用怕忘記了。",
    "👌 收到，這件事不會漏掉的。",
    "🗂 放進清單了，想到的時候可以來查。",
    "💡 好，記著了！完成後發「del N」或「完成 關鍵字」標記。",
]


def _cheer_complete() -> str:
    return random.choice(_COMPLETE_CHEERS)


def _cheer_calendar() -> str:
    return random.choice(_CALENDAR_CHEERS)


def _cheer_todo() -> str:
    return random.choice(_TODO_CHEERS)


# ── Event time fixer ──────────────────────────────────────────────────────────

def _fix_event_times(ev: dict) -> None:
    """修正 end < start 的情況（Gemini 算跨午夜時間時容易出錯）。"""
    try:
        start_dt = datetime.fromisoformat(ev["start"])
        end_dt = datetime.fromisoformat(ev["end"])
        if end_dt <= start_dt:
            ev["end"] = (start_dt + timedelta(hours=1)).isoformat()
    except (KeyError, ValueError):
        pass


# ── Quick pre-filter（只判斷是否送「⏳」，不影響 Gemini 處理）────────────────

_TIME_KEYWORDS = [
    "今天", "明天", "後天", "大後天",
    "下週", "下星期", "這週", "這星期", "本週",
    "週一", "週二", "週三", "週四", "週五", "週六", "週日",
    "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日",
    "早上", "上午", "中午", "下午", "晚上", "凌晨",
    "點鐘", "點半", "幾點", "時候", "月", "號",
]
_TASK_KEYWORDS = [
    "記得", "要去", "要買", "要訂", "幫我", "幫你", "幫忙",
    "買", "訂", "查", "預約", "安排", "提醒",
    "預定", "預訂", "帶去", "帶來", "拿去", "拿來", "送去", "送來",
    "寄", "付", "繳", "聯絡", "通知", "確認", "回覆", "回電",
    "領", "取", "辦", "處理", "申請",
]
_MODIFY_KEYWORDS = ["修正", "更改", "改一下", "調整", "修改", "改成", "改到"]


def _should_notify(text: str) -> bool:
    for kw in _MODIFY_KEYWORDS:
        if kw in text:
            return True
    return any(kw in text for kw in _TIME_KEYWORDS) or any(kw in text for kw in _TASK_KEYWORDS)


# ── Message processing ────────────────────────────────────────────────────────

def process_message(text: str, chat_id: str, reply_token: str | None = None) -> None:
    print(f"[DoubleA] 收到訊息：{text}")

    # 優先用 reply_token（免費），失敗或無 token 時 fallback 到 push
    _used_reply: list[bool] = [False]

    def _respond(msg: str) -> None:
        if reply_token and not _used_reply[0]:
            try:
                _reply_line(reply_token, msg)
                _used_reply[0] = True
                return
            except Exception:
                pass  # 已記錄，fallback 到 push
        _push_line(chat_id, msg)

    if handle_command(text, chat_id, reply_token):
        return

    # 有 push 額度就送「⏳」即時回饋；額度用盡（429）則靜默略過，
    # 後續確認訊息仍會透過 reply_message 送出，功能不受影響
    if _should_notify(text):
        try:
            _push_line(chat_id, "⏳ 收到！處理中，請稍候...")
        except Exception:
            pass

    now = datetime.now(TAIPEI_TZ)
    result = parse_message(text, now)
    msg_type = result.get("type", "ignore")
    print(f"[DoubleA] 分類：{msg_type}　{result}")

    if msg_type == "modify":
        last = load_last_event()
        if not last:
            _respond("❓ 找不到最近的行事曆事件，無法修改")
            return
        updates = parse_modification(text, last, now)
        if not updates:
            _respond("⚠️ 無法解析修改內容，請重新描述")
            return
        try:
            link = update_calendar_event(last["id"], updates)
            save_last_event(last["id"], {**last, **updates})
            start_dt = datetime.fromisoformat(updates["start"])
            date_str = start_dt.strftime("%-m月%-d日 %H:%M")
            reply = f"✅ 行事曆已更新！\n\n【{last['title']}】\n🗓 {date_str}\n\n🔗 {link}"
            print(f"[DoubleA] 行事曆修改：{link}")
        except Exception as e:
            print(f"[DoubleA] 修改錯誤：{e}")
            reply = "⚠️ 行事曆修改失敗，請稍後再試。"
        _respond(reply)

    elif msg_type == "calendar":
        events = result.get("events", [])
        # 相容舊格式（直接帶 title/start/end 的單一事件）
        if not events and result.get("title"):
            events = [result]

        if not events:
            _respond("⚠️ 無法解析行事曆事件，請重新描述。")
        elif len(events) == 1:
            ev = events[0]
            ev["description"] = text
            _fix_event_times(ev)
            try:
                created = create_calendar_event(ev)
                save_last_event(created["id"], ev)
                schedule_event_reminder(chat_id, ev)
                reply = _format_calendar_confirmation(ev, created["link"]) + f"\n\n{_cheer_calendar()}"
                print(f"[DoubleA] 行事曆建立：{created['link']}")
            except Exception as e:
                print(f"[DoubleA] 行事曆錯誤：{e}")
                reply = "⚠️ 行事曆寫入失敗，請稍後再試。"
            _respond(reply)
        else:
            # 多事件：逐一建立
            succeeded = []
            failed = []
            for ev in events:
                ev["description"] = text
                _fix_event_times(ev)
                try:
                    created = create_calendar_event(ev)
                    save_last_event(created["id"], ev)
                    schedule_event_reminder(chat_id, ev)
                    succeeded.append({"event_data": ev, "link": created["link"]})
                    print(f"[DoubleA] 行事曆建立：{ev['title']} {created['link']}")
                except Exception as e:
                    print(f"[DoubleA] 行事曆錯誤（{ev.get('title')}）：{e}")
                    failed.append(ev.get("title", "未知事件"))

            if succeeded:
                reply = _format_multi_calendar_confirmation(succeeded) + f"\n\n{_cheer_calendar()}"
                if failed:
                    reply += f"\n\n⚠️ 以下事件建立失敗：{'、'.join(failed)}"
            else:
                reply = "⚠️ 行事曆寫入失敗，請稍後再試。"
            _respond(reply)

    elif msg_type == "todo":
        try:
            task = add_task(result["title"], result.get("description"))
            reply = (
                f"📌 已記錄到 Google Tasks！\n\n"
                f"【{task['title']}】\n\n"
                f"🔗 {TASKS_URL}\n\n"
                f"完成後發「del N」或「完成 {task['title']}」即可標記\n\n"
                f"{_cheer_todo()}"
            )
            print(f"[DoubleA] 待辦建立：{task['title']}")
        except Exception as e:
            print(f"[DoubleA] 待辦錯誤：{e}")
            reply = "⚠️ 待辦事項記錄失敗，請稍後再試。"
        _respond(reply)

    else:
        print(f"[DoubleA] 略過（ignore）")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/photo/{file_id}")
async def serve_photo(file_id: str, sig: str = "", expires: int = 0):
    """將 Google Drive 照片串流給 LINE 伺服器（需 HMAC 簽章）。"""
    secret = os.environ.get("PHOTO_SERVE_SECRET", os.environ.get("LINE_CHANNEL_SECRET", ""))
    if not secret or not sig or not expires:
        raise HTTPException(status_code=403, detail="Missing signature")
    if time.time() > expires:
        raise HTTPException(status_code=403, detail="Signature expired")
    msg = f"{file_id}:{expires}".encode()
    expected = _hmac.HMAC(secret.encode(), msg, hashlib.sha256).hexdigest()
    if not _hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=403, detail="Invalid signature")
    def _get_meta_and_token() -> tuple[str, str]:
        creds = get_credentials()
        service = build_drive_service(creds)
        meta = service.files().get(fileId=file_id, fields="mimeType").execute()
        mime = meta.get("mimeType", "image/jpeg")
        return mime, creds.token

    mime_type, access_token = await asyncio.to_thread(_get_meta_and_token)

    import httpx

    async def _stream():
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET", url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            ) as r:
                async for chunk in r.aiter_bytes(65536):
                    yield chunk

    return StreamingResponse(
        _stream(),
        media_type=mime_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _dispatch_mom(reply_token: str) -> None:
    with ApiClient(line_config) as api_client:
        handle_mom_message(reply_token, MessagingApi(api_client))


def _route_event(event) -> tuple | None:
    """決定事件的處理方式。回傳 (action, *args) 或 None（略過）。"""
    if not isinstance(event, MessageEvent):
        return None
    chat_id = _get_chat_id(event)
    user_id = getattr(event.source, "user_id", None) or ""

    # 1. 媽媽照片：不限群組
    if user_id and user_id == bot_config.get_mom_user_id():
        return ("mom_photo", event.reply_token)

    # 2. 照片歸檔：綁定群組的圖片與「相簿」指令
    if bot_config.is_feature_on("photo_archive", chat_id):
        if isinstance(event.message, ImageMessageContent):
            return ("archive_photo", event.message.id)
        if isinstance(event.message, TextMessageContent):
            text = event.message.text.strip()
            if text.startswith("相簿 "):
                return ("album_command", text, event.reply_token)

    # 3. 待辦：綁定群組的文字訊息
    if bot_config.is_feature_on("todo", chat_id) and isinstance(
        event.message, TextMessageContent
    ):
        return ("todo", event.message.text.strip(), chat_id, event.reply_token)

    return None


def _dispatch_album_command(text: str, reply_token: str) -> None:
    with ApiClient(line_config) as api_client:
        photo_archive.handle_album_command(text, reply_token, MessagingApi(api_client))


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    try:
        events = parser.parse(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent):
            chat_id = _get_chat_id(event)
            chat_type = "group" if getattr(event.source, "group_id", None) else (
                "room" if getattr(event.source, "room_id", None) else "user"
            )
            background_tasks.add_task(bot_config.register_chat, chat_id, chat_type)

        action = _route_event(event)
        if action is None:
            continue
        kind = action[0]
        if kind == "mom_photo":
            background_tasks.add_task(_dispatch_mom, action[1])
        elif kind == "archive_photo":
            background_tasks.add_task(photo_archive.archive_photo, action[1])
        elif kind == "album_command":
            background_tasks.add_task(_dispatch_album_command, action[1], action[2])
        elif kind == "todo":
            background_tasks.add_task(process_message, action[1], action[2], action[3])
    return JSONResponse(content={"status": "ok"})


@app.post("/morning-briefing")
async def morning_briefing():
    """Cloud Scheduler 每天 08:00 呼叫：今日行事曆 + 未完成待辦。"""
    chat_id = bot_config.get_todo_chat_id()
    if not chat_id:
        return JSONResponse(content={"status": "no_chat_id"})

    now = datetime.now(TAIPEI_TZ)
    sections = []

    # 今日行事曆
    try:
        cal_events = list_events_for_date(now)
        if cal_events:
            lines = ["📅 今日行事曆\n"]
            for ev in cal_events:
                time_prefix = f"{ev['start_str']} " if ev["start_str"] else ""
                lines.append(f"・{time_prefix}{ev['title']}")
            sections.append("\n".join(lines))
    except Exception as e:
        print(f"[DoubleA] 早安行事曆取得失敗：{e}")

    # 未完成待辦
    try:
        tasks = get_pending_tasks()
        if tasks:
            lines = ["📋 待辦清單\n"]
            for i, t in enumerate(tasks, 1):
                lines.append(f"{i}. {t['title']}")
            lines.append(f"\n🔗 {TASKS_URL}")
            sections.append("\n".join(lines))
    except Exception as e:
        print(f"[DoubleA] 早安待辦取得失敗：{e}")

    if not sections:
        msg = "🌅 早安！今天沒有特別安排，好好享受吧！"
    else:
        msg = "🌅 早安！今天的安排：\n\n" + "\n\n".join(sections)

    _push_line(chat_id, msg)
    print(f"[DoubleA] 早安摘要發送")
    return JSONResponse(content={"status": "ok"})


@app.post("/daily-reminder")
async def daily_reminder():
    """Cloud Scheduler 每天 17:00 呼叫：未完成待辦 + 今日剩餘行事曆。"""
    chat_id = bot_config.get_todo_chat_id()
    if not chat_id:
        return JSONResponse(content={"status": "no_chat_id"})

    now = datetime.now(TAIPEI_TZ)
    sections = []

    # 今日剩餘行事曆（只顯示 17:00 之後的）
    try:
        cal_events = list_events_for_date(now)
        remaining = [ev for ev in cal_events if ev["start_str"] > "17:00"]
        if remaining:
            lines = ["📅 今晚行事曆\n"]
            for ev in remaining:
                lines.append(f"・{ev['start_str']} {ev['title']}")
            sections.append("\n".join(lines))
    except Exception as e:
        print(f"[DoubleA] 下午行事曆取得失敗：{e}")

    # 未完成待辦
    try:
        tasks = get_pending_tasks()
        if tasks:
            lines = ["📋 待辦提醒\n"]
            for i, t in enumerate(tasks, 1):
                lines.append(f"{i}. {t['title']}")
            lines.append(f"\n🔗 {TASKS_URL}")
            sections.append("\n".join(lines))
    except Exception as e:
        print(f"[DoubleA] 下午待辦取得失敗：{e}")

    if not sections:
        print("[DoubleA] 下午無內容，略過")
        return JSONResponse(content={"status": "nothing_to_send"})

    msg = "🌆 下午好！來看看今天還有什麼：\n\n" + "\n\n".join(sections)
    _push_line(chat_id, msg)
    print(f"[DoubleA] 下午提醒發送")
    return JSONResponse(content={"status": "ok"})


@app.post("/check-reminders")
async def check_reminders():
    """Cloud Scheduler 每 15 分鐘呼叫此端點，檢查並發送到期的活動提醒。"""
    now = datetime.now(TAIPEI_TZ)
    due = get_due_reminders(now)
    sent_count = 0
    stale_count = 0
    for r in due:
        doc_id = r.get("_doc_id", r.get("_id"))
        # 事件已經開始（甚至結束），代表這則提醒因故卡住沒送出（如 push 配額用盡）。
        # 「還有 2 小時」對已過去的事件沒有意義，直接標記已送出、丟棄，不補發也不浪費配額。
        if datetime.fromisoformat(r["start"]) <= now:
            print(f"[DoubleA] 提醒已過期，略過：{r['title']}")
            mark_reminder_sent(doc_id)
            stale_count += 1
            continue
        try:
            send_event_reminder(r["chat_id"], r["title"], r["start"])
            mark_reminder_sent(doc_id)
            sent_count += 1
            print(f"[DoubleA] 活動提醒發送：{r['title']}")
        except Exception as e:
            print(f"[DoubleA] 提醒發送失敗：{e}")
    return JSONResponse(
        content={"status": "ok", "sent": sent_count, "stale_discarded": stale_count}
    )


# ── Admin Dashboard ───────────────────────────────────────────────────────────

FEATURE_LABELS = {
    "todo": "待辦 + 早安推播",
    "mom_photo": "媽媽照片回覆",
    "photo_archive": "照片歸檔",
}


def _check_admin_token(token: str) -> None:
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected or not _hmac.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(token: str = ""):
    _check_admin_token(token)
    cfg = bot_config.load_config()
    return ADMIN_HTML.replace("__CONFIG__", _json.dumps(cfg, ensure_ascii=False)) \
                     .replace("__LABELS__", _json.dumps(FEATURE_LABELS, ensure_ascii=False)) \
                     .replace("__TOKEN__", _json.dumps(token))


@app.post("/admin/config")
async def admin_save(request: Request, token: str = ""):
    _check_admin_token(token)
    cfg = await request.json()
    bot_config.save_config(cfg)
    return JSONResponse(content={"status": "ok"})


ADMIN_HTML = """<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DoubleA Bot Dashboard</title>
<style>
body{font-family:-apple-system,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem;background:#fafafa}
h1{font-size:1.4rem} h2{font-size:1.1rem;margin-top:2rem}
.card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:1rem;margin:.8rem 0}
.feature-head{display:flex;justify-content:space-between;align-items:center}
label.chat{display:block;margin:.3rem 0 .3rem 1rem}
input[type=text]{width:12rem;padding:.2rem}
button{background:#06c755;color:#fff;border:none;border-radius:6px;padding:.6rem 1.4rem;font-size:1rem;cursor:pointer}
.toggle{width:2.6rem;height:1.5rem}
#msg{margin-left:1rem;color:#06c755}
</style></head><body>
<h1>🤖 DoubleA Bot Dashboard</h1>
<div id="features"></div>
<h2>已知群組</h2>
<div id="chats"></div>
<button onclick="save()">儲存</button><span id="msg"></span>
<script>
const cfg = __CONFIG__;
const labels = __LABELS__;
const token = __TOKEN__;

function esc(s){return String(s).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function chatLabel(id){
  const c = cfg.known_chats[id] || {};
  return (c.note || c.name || id) + " (" + id.slice(0,8) + "…)";
}
function render(){
  const f = document.getElementById("features");
  f.innerHTML = "";
  for(const [name, feat] of Object.entries(cfg.features)){
    const card = document.createElement("div"); card.className = "card";
    let html = `<div class="feature-head"><strong>${esc(labels[name]||name)}</strong>
      <input class="toggle" type="checkbox" ${feat.enabled?"checked":""}
        onchange="cfg.features['${esc(name)}'].enabled=this.checked"></div>`;
    if(name !== "mom_photo"){
      for(const id of Object.keys(cfg.known_chats)){
        const on = feat.chat_ids.includes(id);
        html += `<label class="chat"><input type="checkbox" ${on?"checked":""}
          onchange="toggleChat('${esc(name)}','${esc(id)}',this.checked)"> ${esc(chatLabel(id))}</label>`;
      }
    } else {
      html += `<label class="chat">依媽媽的 user_id 觸發，不限群組</label>`;
    }
    card.innerHTML = html; f.appendChild(card);
  }
  const ch = document.getElementById("chats");
  ch.innerHTML = "";
  for(const [id, c] of Object.entries(cfg.known_chats)){
    const card = document.createElement("div"); card.className = "card";
    card.innerHTML = `<code>${esc(id)}</code> [${esc(c.type)}] ${esc(c.name||"")}
      備註：<input type="text" value="${esc(c.note||"")}"
        onchange="cfg.known_chats['${esc(id)}'].note=this.value">`;
    ch.appendChild(card);
  }
}
function toggleChat(f, id, on){
  const arr = cfg.features[f].chat_ids;
  if(on && !arr.includes(id)) arr.push(id);
  if(!on) cfg.features[f].chat_ids = arr.filter(x=>x!==id);
}
async function save(){
  const r = await fetch(`/admin/config?token=${token}`, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(cfg)});
  document.getElementById("msg").textContent = r.ok ? "已儲存 ✓" : "失敗！";
  setTimeout(()=>document.getElementById("msg").textContent="", 3000);
}
render();
</script></body></html>"""


@app.get("/health")
def health():
    return {"status": "ok", "bot": "DoubleA", "env": "cloud" if os.environ.get("K_SERVICE") else "local"}
