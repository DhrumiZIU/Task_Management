"""Upload the generated Excel report to a manager's OneDrive via Microsoft Graph API.

Required environment variables (set in Railway):
    AZURE_TENANT_ID      — from Azure AD app registration
    AZURE_CLIENT_ID      — from Azure AD app registration
    AZURE_CLIENT_SECRET  — client secret value (not ID)
    ONEDRIVE_USER_EMAIL  — manager's Microsoft 365 email (e.g. manager@company.com)
    ONEDRIVE_FOLDER      — OneDrive folder path to upload into (e.g. "Team Planner" or
                           "Reports/Daily" — leave blank to upload to root)
    ONEDRIVE_FILENAME    — (optional) name of the file on OneDrive,
                           defaults to "Team Daily Planner.xlsx"
"""
from __future__ import annotations
import io, logging, os, threading
from pathlib import Path

log = logging.getLogger(__name__)

# ── config from env ──────────────────────────────────────────────────────────

_TENANT_ID    = os.environ.get("AZURE_TENANT_ID", "")
_CLIENT_ID    = os.environ.get("AZURE_CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")
_USER_EMAIL   = os.environ.get("ONEDRIVE_USER_EMAIL", "")
_FOLDER       = os.environ.get("ONEDRIVE_FOLDER", "").strip("/")
_FILENAME     = os.environ.get("ONEDRIVE_FILENAME", "Team Daily Planner.xlsx")

_ENABLED = all([_TENANT_ID, _CLIENT_ID, _CLIENT_SECRET, _USER_EMAIL])

_lock = threading.Lock()


def is_configured() -> bool:
    return _ENABLED


def _get_token() -> str:
    """Acquire an app-only access token via client credentials flow."""
    import urllib.request, urllib.parse, json
    url  = f"https://login.microsoftonline.com/{_TENANT_ID}/oauth2/v2.0/token"
    data = urllib.parse.urlencode({
        "grant_type":    "client_credentials",
        "client_id":     _CLIENT_ID,
        "client_secret": _CLIENT_SECRET,
        "scope":         "https://graph.microsoft.com/.default",
    }).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["access_token"]


def _upload(excel_bytes: bytes, token: str):
    """PUT the Excel bytes to the manager's OneDrive using the Graph upload API."""
    import urllib.request
    # Build the Graph API path:
    #   /users/{email}/drive/root:/{folder}/{filename}:/content
    if _FOLDER:
        item_path = f"{_FOLDER}/{_FILENAME}"
    else:
        item_path = _FILENAME
    url = (
        f"https://graph.microsoft.com/v1.0/users/{_USER_EMAIL}"
        f"/drive/root:/{item_path}:/content"
    )
    req = urllib.request.Request(
        url,
        data=excel_bytes,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status  # 200 updated, 201 created


def push_to_onedrive(excel_bytes: bytes | None = None) -> bool:
    """Generate (or accept) Excel bytes and upload to OneDrive.

    Returns True on success, False on any error.
    Called from db_backup after every task/user write.
    Runs in a background thread so it never blocks a web request.
    """
    if not _ENABLED:
        return False

    def _run():
        with _lock:   # one upload at a time
            try:
                data = excel_bytes if excel_bytes is not None else _build_excel()
                if data is None:
                    return
                token  = _get_token()
                status = _upload(data, token)
                log.info("OneDrive sync: HTTP %s → %s/%s", status, _FOLDER, _FILENAME)
            except Exception as exc:
                log.error("OneDrive sync failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()
    return True


def push_to_onedrive_sync() -> tuple[bool, str]:
    """Blocking version — used by the manual-sync route so the manager sees a result."""
    if not _ENABLED:
        return False, "OneDrive sync is not configured (missing env vars)."
    with _lock:
        try:
            data = _build_excel()
            if data is None:
                return False, "Could not generate Excel — no tasks found."
            token  = _get_token()
            status = _upload(data, token)
            folder_label = _FOLDER or "(root)"
            return True, (
                f"Uploaded '{_FILENAME}' to OneDrive folder '{folder_label}' "
                f"(HTTP {status})."
            )
        except Exception as exc:
            log.error("OneDrive sync failed: %s", exc)
            return False, str(exc)


# ── Excel builder (SQLite → openpyxl) ───────────────────────────────────────

def _build_excel() -> bytes | None:
    """Build a fresh Excel workbook from the current SQLite data and return bytes."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        import db_backup

        tasks = db_backup.get_tasks_db()
        if not tasks:
            return None

        wb = Workbook()
        ws = wb.active
        ws.title = "Tasks"

        # Header row
        headers = ["Date", "Team Member", "Task", "Priority",
                   "Status", "Done Looks Like", "Est. Hours", "7PM Update"]
        header_fill = PatternFill("solid", fgColor="4472C4")
        header_font = Font(bold=True, color="FFFFFF")
        for col, h in enumerate(headers, 1):
            cell = ws.cell(1, col, h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        # Data rows
        status_colors = {
            "Done":        "C6EFCE",
            "In progress": "FFEB9C",
            "Blocked":     "FFC7CE",
            "Not started": "FFFFFF",
        }
        for task in sorted(tasks, key=lambda t: (str(t["date"]), t["team_member"])):
            row_vals = [
                str(task["date"]),
                task["team_member"],
                task["task"],
                task["priority"],
                task["status"],
                task["done_looks_like"] or "",
                task["est_hours"] or "",
                task["update_7pm"] or "",
            ]
            ws.append(row_vals)
            fill_color = status_colors.get(task["status"], "FFFFFF")
            fill = PatternFill("solid", fgColor=fill_color)
            for cell in ws[ws.max_row]:
                cell.fill = fill

        # Auto-fit columns (approximate)
        col_widths = [12, 18, 45, 10, 14, 35, 10, 35]
        for col, width in enumerate(col_widths, 1):
            ws.column_dimensions[ws.cell(1, col).column_letter].width = width

        # Users sheet
        users_ws = wb.create_sheet("Members")
        users_ws.append(["Username", "Display Name", "Role", "Team Name"])
        for u in db_backup.get_users_backup():
            users_ws.append([u["username"], u["display_name"], u["role"], u["team_name"]])

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    except Exception as exc:
        log.error("Excel build failed: %s", exc)
        return None
