"""Extract a swarm task agent's final output from its transcript JSONL."""
import json


def first_user(transcript_path):
    """Cheap scan: return the first user message's text without parsing the whole file."""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message") or {}
                if msg.get("role") != "user":
                    continue
                content = msg.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            return block.get("text")
                return None
    except OSError:
        pass
    return None


def extract_output(transcript_path, last_assistant_message=None):
    """Returns {first_user, output, structured}. Prefers a StructuredOutput
    tool call's input; falls back to the last assistant text, then to the
    payload-provided last_assistant_message."""
    structured = None
    last_text = None
    first_user = None
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message") or {}
                role = msg.get("role")
                content = msg.get("content")
                if first_user is None and role == "user" and isinstance(content, str):
                    first_user = content
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if first_user is None and role == "user" and btype == "text":
                        first_user = block.get("text")
                    if role == "assistant":
                        if btype == "tool_use" and block.get("name") == "StructuredOutput":
                            structured = block.get("input")
                        elif btype == "text" and (block.get("text") or "").strip():
                            last_text = block.get("text")
    except OSError:
        pass
    if structured is not None:
        output = structured
    else:
        output = last_text or last_assistant_message or ""
    return {"first_user": first_user, "output": output, "structured": structured is not None}
