from googleapiclient.discovery import build

from google_auth import get_credentials

TASKLIST_ID = "@default"
TASKS_URL = "https://tasks.google.com/"


def add_task(title: str, notes: str | None = None) -> dict:
    """Add a task to Google Tasks. Returns the created task."""
    service = build("tasks", "v1", credentials=get_credentials())
    body = {"title": title}
    if notes:
        body["notes"] = notes
    return service.tasks().insert(tasklist=TASKLIST_ID, body=body).execute()


def get_pending_tasks() -> list:
    """Return list of incomplete tasks from Google Tasks."""
    service = build("tasks", "v1", credentials=get_credentials())
    result = service.tasks().list(
        tasklist=TASKLIST_ID,
        showCompleted=False,
        showHidden=False,
    ).execute()
    return result.get("items", [])


def complete_task_by_keyword(keyword: str) -> str | None:
    """Mark first matching incomplete task as completed. Returns title or None."""
    service = build("tasks", "v1", credentials=get_credentials())
    result = service.tasks().list(
        tasklist=TASKLIST_ID,
        showCompleted=False,
    ).execute()
    for task in result.get("items", []):
        if keyword in task["title"]:
            service.tasks().patch(
                tasklist=TASKLIST_ID,
                task=task["id"],
                body={"status": "completed"},
            ).execute()
            return task["title"]
    return None


def complete_task_by_index(index: int) -> str | None:
    """Mark task at 1-based index as completed. Returns title or None."""
    service = build("tasks", "v1", credentials=get_credentials())
    result = service.tasks().list(
        tasklist=TASKLIST_ID,
        showCompleted=False,
        showHidden=False,
    ).execute()
    tasks = result.get("items", [])
    if index < 1 or index > len(tasks):
        return None
    task = tasks[index - 1]
    service.tasks().patch(
        tasklist=TASKLIST_ID,
        task=task["id"],
        body={"status": "completed"},
    ).execute()
    return task["title"]
