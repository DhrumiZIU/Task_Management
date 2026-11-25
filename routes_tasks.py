"""Task CRUD and 7 PM meeting routes — registered onto the main app."""
from __future__ import annotations
import json
import os
import tempfile
from datetime import date, datetime, timedelta
from flask import flash, jsonify, redirect, render_template, request, session, url_for
import auth
import excel_utils as xl
import db_backup
from config import PRIORITY_OPTIONS, STATUS_OPTIONS

ALLOWED_EXTENSIONS = {
    "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
    "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
    "txt": "text/plain",
}


def register(app):
    @app.route("/task/add", methods=["GET", "POST"])
    @auth.login_required
    def add_task():
        if not auth.is_manager():
            flash("Task assignment is handled from the dashboard.", "error")
            return redirect(url_for("dashboard"))
        all_users = xl.get_all_users()
        members = [u["display_name"] or u["username"] for u in all_users if u.get("display_name") or u.get("username")]
        if request.method == "POST":
            raw_date  = request.form.get("task_date") or str(date.today())
            member    = request.form.get("team_member", "").strip()
            # Members can only assign tasks to themselves
            if not auth.is_manager():
                member = session.get("team_name") or session.get("display_name") or session.get("username", "")
            task      = request.form.get("task", "").strip()
            priority  = request.form.get("priority", "Medium")
            dll       = request.form.get("done_looks_like", "").strip()
            hrs_raw   = request.form.get("est_hours", "").strip()
            est_hours = float(hrs_raw) if hrs_raw else None
            assigned_by = session.get("display_name") or session.get("username", "")
            if not task or not member:
                flash("Task description and team member are required.", "error")
            else:
                d = datetime.strptime(raw_date, "%Y-%m-%d").date()
                if not auth.is_manager() and d < date.today():
                    flash("You cannot add tasks for past dates.", "error")
                    return redirect(url_for("add_task"))
                # Write to Excel (manager view) then immediately write to SQLite
                excel_row = xl.add_task(d, member, task, priority, dll, est_hours)
                db_backup.write_task_db(excel_row, d, member, task, priority,
                                        "Not started", dll, est_hours, "",
                                        assigned_by=assigned_by)
                db_backup.log_activity("add_task", session.get("username", ""),
                                       session.get("display_name", ""),
                                       f"Task for {member}: {task[:60]}")
                flash(f"Task added for {member}.", "success")
                return redirect(url_for("dashboard"))
        return render_template("add_task.html", members=members,
                               priorities=PRIORITY_OPTIONS, today=date.today(),
                               is_manager=auth.is_manager())

    @app.route("/task/edit/<int:task_id>", methods=["GET", "POST"])
    @auth.login_required
    def edit_task(task_id: int):
        tasks = db_backup.get_tasks_db()
        task = next((t for t in tasks if t["id"] == task_id), None)
        if not task:
            flash("Task not found.", "error")
            return redirect(url_for("dashboard"))
        if not auth.can_edit_task(task["team_member"]):
            flash("You can only edit your own tasks.", "error")
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            if auth.is_manager():
                t_text = request.form.get("task", task["task"])
                t_pri  = request.form.get("priority", task["priority"])
                t_stat = request.form.get("status", task["status"])
                t_dll  = request.form.get("done_looks_like", task["done_looks_like"] or "")
                t_hrs  = float(request.form.get("est_hours") or 0) or None
                t_7pm  = request.form.get("update_7pm", task["update_7pm"] or "")
                db_backup.update_task_full_by_id(task_id, task["date"], task["team_member"],
                                                 t_text, t_pri, t_stat, t_dll, t_hrs, t_7pm)
            else:
                t_stat = request.form.get("status", task["status"])
                t_7pm  = request.form.get("update_7pm", task["update_7pm"] or "")
                db_backup.update_task_status_by_id(task_id, t_stat, t_7pm)
            db_backup.log_activity("update_task", session.get("username", ""),
                                   session.get("display_name", ""),
                                   f"ID {task_id}: {task.get('task','')[:60]}")
            flash("Task updated.", "success")
            back = request.form.get("back") or url_for("dashboard")
            return redirect(back)
        back = request.referrer or url_for("dashboard")
        return render_template("edit_task.html", task=task,
                               statuses=STATUS_OPTIONS, priorities=PRIORITY_OPTIONS,
                               is_manager=auth.is_manager(), back=back)

    @app.route("/task/status/<int:task_id>", methods=["POST"])
    @auth.login_required
    def quick_status(task_id: int):
        """Inline status update from the dashboard — no full edit page needed."""
        tasks = db_backup.get_tasks_db()
        task  = next((t for t in tasks if t["id"] == task_id), None)
        if not task or not auth.can_edit_task(task["team_member"]):
            flash("Not allowed.", "error")
            return redirect(url_for("dashboard"))
        new_status = request.form.get("status", "").strip()
        if new_status not in STATUS_OPTIONS:
            flash("Invalid status.", "error")
            return redirect(url_for("dashboard"))
        db_backup.update_task_status_by_id(task_id, new_status, task.get("update_7pm") or "")
        db_backup.log_activity("status_change", session.get("username", ""),
                               session.get("display_name", ""),
                               f"ID {task_id} → {new_status}")
        # AJAX callers send X-Requested-With header
        from flask import jsonify
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": True, "status": new_status})
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/task/delete/<int:row_num>", methods=["POST"])
    @auth.manager_required
    def delete_task(row_num: int):
        xl.delete_task(row_num)
        db_backup.delete_task_db(row_num)
        db_backup.log_activity("delete_task", session.get("username", ""),
                               session.get("display_name", ""), f"Row {row_num}")
        flash("Task deleted.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/task/delete/id/<int:task_id>", methods=["POST"])
    @auth.manager_required
    def delete_task_by_id(task_id: int):
        db_backup.delete_task_by_id(task_id)
        db_backup.log_activity("delete_task", session.get("username", ""),
                               session.get("display_name", ""), f"ID {task_id}")
        flash("Task deleted.", "success")
        # Preserve the current view (today/tomorrow/all) so user stays on the same tab
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/meeting", methods=["GET", "POST"])
    @auth.login_required
    def meeting():
        if auth.is_manager():
            all_users = xl.get_all_users()
            members = [u["display_name"] or u["username"] for u in all_users if u.get("display_name") or u.get("username")]
        else:
            members = xl.get_team_members()
        tomorrow  = date.today() + timedelta(days=1)
        team_name = session.get("team_name", "")
        if request.method == "POST":
            allowed = members if auth.is_manager() else ([team_name] if team_name else [])
            added = 0
            for m in allowed:
                tasks_list = request.form.getlist(f"tasks_{m}")
                pris_list  = request.form.getlist(f"priorities_{m}")
                dlls_list  = request.form.getlist(f"dlls_{m}")
                for i, t in enumerate(tasks_list):
                    t = t.strip()
                    if t:
                        pri = pris_list[i] if i < len(pris_list) else "Medium"
                        dll = dlls_list[i].strip() if i < len(dlls_list) else ""
                        excel_row = xl.add_task(tomorrow, m, t, pri or "Medium", dll)
                        db_backup.write_task_db(excel_row, tomorrow, m, t,
                                                pri or "Medium", "Not started", dll, None, "")
                        db_backup.log_activity("add_task", session.get("username", ""),
                                               session.get("display_name", ""),
                                               f"7PM — Task for {m}: {t[:60]}")
                        added += 1
            msg = "Task added for tomorrow." if added == 1 else f"{added} tasks added for tomorrow."
            flash(msg, "success")
            return redirect(url_for("meeting"))
        grouped_next = {m: db_backup.get_tasks_db(filter_date=tomorrow, member=m) for m in members}
        latest_meeting = db_backup.get_latest_meeting()
        all_meetings = db_backup.get_all_meetings()
        return render_template("meeting.html", members=members, tomorrow=tomorrow,
                               grouped_next=grouped_next, priorities=PRIORITY_OPTIONS,
                               is_manager=auth.is_manager(), team_name=team_name,
                               latest_meeting=latest_meeting, all_meetings=all_meetings)

    @app.route("/upload-meeting", methods=["POST"])
    @auth.login_required
    def upload_meeting():
        if not auth.is_manager():
            flash("Only the admin can upload meeting recordings.", "error")
            return redirect(url_for("meeting"))
        file = request.files.get("recording")
        if not file or not file.filename:
            flash("Please select a file to upload.", "error")
            return redirect(url_for("meeting"))

        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            flash("Unsupported file type. Upload MP3, WAV, M4A, MP4, MOV, WEBM, or TXT.", "error")
            return redirect(url_for("meeting"))

        mime_type = ALLOWED_EXTENSIONS[ext]
        meeting_id = db_backup.save_meeting(session.get("display_name", ""), file.filename)

        # Save to temp file
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}")
        file.save(tmp.name)
        tmp.close()

        # Store audio bytes in DB for playback (audio/video files only)
        if mime_type.startswith(("audio/", "video/")):
            try:
                with open(tmp.name, "rb") as f:
                    db_backup.save_meeting_audio(meeting_id, f.read(), mime_type)
            except Exception:
                pass

        try:
            # Get member names from DB
            users = db_backup.get_users_backup()
            member_names = [u["display_name"] for u in users if u["role"] == "member" and u["display_name"]]

            import gemini_service
            result = gemini_service.process_meeting(tmp.name, mime_type, member_names)

            overall_summary = result.get("overall_summary", "")
            db_backup.update_meeting_result(meeting_id, result.get("transcript", ""), overall_summary, "done")

            # Auto-assign next-day tasks
            tomorrow = date.today() + timedelta(days=1)
            assigned_count = 0
            for member_name, info in result.get("members", {}).items():
                next_task = (info.get("next_task") or "").strip()
                if next_task and member_name in member_names:
                    priority = info.get("priority", "Medium")
                    if priority not in PRIORITY_OPTIONS:
                        priority = "Medium"
                    excel_row = xl.add_task(tomorrow, member_name, next_task, priority, "")
                    db_backup.write_task_db(excel_row, tomorrow, member_name, next_task,
                                            priority, "Not started", "", None,
                                            f"[Auto from meeting #{meeting_id}]")
                    assigned_count += 1

            db_backup.log_activity("meeting_upload", session.get("username", ""),
                                   session.get("display_name", ""),
                                   f"Meeting #{meeting_id} — {assigned_count} tasks auto-assigned")
            flash(f"Meeting processed! Summary generated and {assigned_count} tasks auto-assigned for tomorrow.", "success")

        except Exception as e:
            db_backup.update_meeting_result(meeting_id, "", f"Error: {e}", "failed")
            flash(f"Processing failed: {e}", "error")
        finally:
            os.unlink(tmp.name)

        return redirect(url_for("meeting"))

    @app.route("/api/meeting-audio/<int:meeting_id>")
    @auth.login_required
    def api_meeting_audio(meeting_id):
        from flask import Response
        row = db_backup.get_meeting_audio(meeting_id)
        if not row or not row["audio_data"]:
            return Response("Not found", status=404)
        mime = row["audio_mime"] or "audio/mpeg"
        return Response(row["audio_data"], mimetype=mime, headers={
            "Content-Disposition": f'inline; filename="{row["filename"]}"',
            "Accept-Ranges": "bytes",
        })

    @app.route("/meeting/history")
    @auth.login_required
    def meeting_history():
        all_meetings = db_backup.get_all_meetings()
        return render_template("meeting_history.html", all_meetings=all_meetings)

    @app.route("/api/meeting-summary")
    @auth.login_required
    def api_meeting_summary():
        meeting = db_backup.get_latest_meeting()
        if not meeting:
            return jsonify({"meeting": None})
        return jsonify({"meeting": {
            "id": meeting["id"],
            "status": meeting["status"],
            "overall_summary": meeting["overall_summary"] or "",
            "uploaded_by": meeting["uploaded_by"] or "",
            "uploaded_at": meeting["uploaded_at"] or "",
            "filename": meeting["filename"] or "",
        }})
