import json
import os
from datetime import datetime

from google import genai
from google.genai import types


def parse_message(message: str, current_time: datetime) -> dict:
    """
    Classifies a LINE message into one of four types:
    - {"type": "calendar", "events": [{"title", "start", "end", "location"}, ...]}
    - {"type": "todo", "title": ..., "description": ...}
    - {"type": "modify"}
    - {"type": "ignore"}
    """
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    weekday_map = {
        "Monday": "週一", "Tuesday": "週二", "Wednesday": "週三",
        "Thursday": "週四", "Friday": "週五", "Saturday": "週六", "Sunday": "週日",
    }
    weekday_zh = weekday_map.get(current_time.strftime("%A"), "")
    now_str = current_time.strftime(f"%Y-%m-%d %H:%M {weekday_zh}")

    prompt = f"""現在時間：{now_str}（台北時間，UTC+8）

分析這則 LINE 訊息，輸出以下四種 JSON 格式之一。

━━ 判斷規則 ━━

【modify】含有「修正/更改/改一下/調整/修改」且指向剛才建立的行事曆
→ 輸出：{{"type": "modify"}}

【calendar】含有「明確時間/日期 + 未來的事」
→ 輸出：{{"type": "calendar", "events": [...]}}
⚠️ events 必須是陣列，每個獨立的時間點都是一筆獨立事件
⚠️ 不可把第二個時間點當作第一個事件的 end，它們是兩個不同事件

【todo】有任務性質但沒有明確時間
→ 輸出：{{"type": "todo", "title": "任務簡短描述", "description": null}}

【ignore】日常聊天、情感、問句、已發生的事
→ 輸出：{{"type": "ignore"}}

━━ 多事件範例 ━━

訊息：「明天早上7點半出發去台北，後天晚上10點回到玉里」
正確輸出：
{{"type": "calendar", "events": [
  {{"title": "出發去台北", "start": "2026-05-26T07:30:00+08:00", "end": "2026-05-26T08:30:00+08:00", "location": "台北"}},
  {{"title": "回到玉里", "start": "2026-05-27T22:00:00+08:00", "end": "2026-05-27T23:00:00+08:00", "location": "玉里"}}
]}}

━━ 現在分析這則訊息 ━━

訊息：「{message}」

只回傳 JSON，不要加任何說明或 markdown。

events 陣列格式：每筆事件 = {{"title": "中文標題10字內", "start": "ISO8601+08:00", "end": "ISO8601+08:00", "location": null或地點}}

規則：
- 每個獨立的時間點 = 一筆獨立事件，不可合併
- 若無結束時間：end = start + 1小時（醫療/出行類 + 2小時）
- 若只有日期無時間：start 用 09:00"""

    response_schema = {
        "type": "OBJECT",
        "properties": {
            "type": {"type": "STRING"},
            "events": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "title":    {"type": "STRING"},
                        "start":    {"type": "STRING"},
                        "end":      {"type": "STRING"},
                        "location": {"type": "STRING"},
                    },
                    "required": ["title", "start", "end"],
                },
            },
            "title":       {"type": "STRING"},
            "description": {"type": "STRING"},
        },
        "required": ["type"],
    }

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )

    try:
        return json.loads(response.text.strip())
    except (json.JSONDecodeError, KeyError, IndexError):
        return {"type": "ignore"}


def parse_modification(instruction: str, original: dict, current_time: datetime) -> dict | None:
    """
    Given a modification instruction and original event, returns updated start/end.
    """
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    now_str = current_time.strftime("%Y-%m-%d %H:%M")

    prompt = f"""現在時間：{now_str}（台北時間，UTC+8）

原始行事曆事件：
- 標題：{original["title"]}
- 開始：{original["start"]}
- 結束：{original["end"]}

修改指令：「{instruction}」

根據修改指令計算修改後的新時間，只回傳 JSON，不要加其他文字：
{{"start": "新的ISO8601+08:00", "end": "新的ISO8601+08:00"}}

規則：
- 只改日期時，保留原本的時間
- 只改時間時，保留原本的日期
- end 與 start 的時間差距保持不變"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=0)
        ),
    )

    try:
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except (json.JSONDecodeError, KeyError, IndexError):
        return None
