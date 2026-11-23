"""Meeting processor: Gemini native audio/video/text understanding (multilingual)."""
from __future__ import annotations
import json
import os
import time

# Try models in order — first available wins
_MODELS = [
    "gemini-3.5-flash",       # primary — confirmed working
    "gemini-3.5-flash-lite",  # lighter fallback
]

_PROMPT_TEMPLATE = """
You are analyzing a team standup meeting recording/transcript.
The meeting may be in English, Hindi, Gujarati, or a mixture of these languages.
Understand all languages and respond in English only.

Team members are: {members}

From this meeting, extract:
1. An overall meeting summary (2-4 sentences covering what was discussed)
2. For each team member who was mentioned or spoke:
   - What work they completed today
   - What issues/blockers they faced
   - What work is pending
   - Their next-day task (what they should do tomorrow)

Rules:
- Speakers may introduce themselves OR the manager may assign tasks directly
- If a task is mentioned with no clear owner, assign it to "unassigned"
- Match names to this exact list: {members}
- If a member is not mentioned at all, skip them
- Priority should be "High", "Medium", or "Low" based on urgency

Return ONLY valid JSON in this exact format:
{{
  "overall_summary": "...",
  "members": {{
    "Member Name": {{
      "work_done": "...",
      "issues": "...",
      "pending": "...",
      "next_task": "...",
      "priority": "Medium"
    }}
  }},
  "unassigned_tasks": ["task1", "task2"]
}}
"""


def _get_client():
    from dotenv import load_dotenv
    load_dotenv(override=True)
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set. Add it to your .env file.")
    from google import genai
    return genai.Client(api_key=api_key)


def _generate_with_fallback(client, contents):
    """Try each model in order; return (model_used, response)."""
    last_error = None
    for model in _MODELS:
        try:
            response = client.models.generate_content(model=model, contents=contents)
            return model, response
        except Exception as e:
            err_str = str(e)
            # Only fall through on unavailable/quota/not-found errors
            if any(code in err_str for code in ["503", "429", "404", "UNAVAILABLE", "not found", "quota"]):
                last_error = e
                continue
            raise  # Re-raise unexpected errors immediately
    raise RuntimeError(
        f"Gemini is temporarily unavailable (Google's servers are busy). "
        f"Please wait 2–3 minutes and try uploading again. Last error: {last_error}"
    )


def process_meeting(file_path: str, mime_type: str, member_names: list[str]) -> dict:
    """
    Send meeting file to Gemini for native multilingual understanding.
    Supports audio (mp3/wav/m4a), video (mp4/mov/webm), and text files.
    Languages: English, Hindi, Gujarati, or any mixture.
    """
    client = _get_client()

    members_str = ", ".join(member_names)
    prompt = _PROMPT_TEMPLATE.format(members=members_str)

    if mime_type == "text/plain":
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            text_content = f.read()
        if not text_content.strip():
            raise ValueError("The uploaded text file is empty.")
        _, response = _generate_with_fallback(
            client, f"Meeting transcript:\n\n{text_content}\n\n{prompt}"
        )
        transcript = text_content
    else:
        # Upload audio/video to Gemini File API — handles all languages natively
        try:
            uploaded = client.files.upload(file=file_path)
        except Exception as e:
            raise RuntimeError(
                f"Failed to upload file to Gemini: {e}\n"
                "Check your internet connection and API key."
            )

        # Wait for file to be ready (max 60s)
        ready = False
        for _ in range(30):
            f = client.files.get(name=uploaded.name)
            if f.state.name == "ACTIVE":
                ready = True
                break
            time.sleep(2)

        if not ready:
            raise RuntimeError(
                "Gemini file processing timed out after 60 seconds. "
                "The file may be too large or the service is slow. Try a shorter recording."
            )

        from google.genai import types
        contents = [
            types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime_type),
            prompt
        ]
        _, response = _generate_with_fallback(client, contents)
        transcript = ""  # audio — no raw transcript

    raw = response.text.strip() if response.text else ""
    if not raw:
        raise RuntimeError(
            "Gemini returned an empty response. "
            "The audio may be too quiet, in an unsupported format, or the meeting had no clear speech."
        )

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Gemini's response could not be parsed as JSON: {e}\n"
            "This sometimes happens with very short or unclear recordings. Try again."
        )

    result.setdefault("overall_summary", "")
    result.setdefault("members", {})
    result.setdefault("unassigned_tasks", [])
    result["transcript"] = transcript or raw
    return result
