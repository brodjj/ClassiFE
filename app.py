import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI()

# The seven policy categories the guard model scores every message against.
# Order matches the classifier's output and the badge order in the frontend.
CATEGORIES = [
    "pii_and_ip",
    "illicit_activities",
    "hate_and_abuse",
    "sexual_content",
    "prompt_security",
    "violence_and_self_harm",
    "misinformation",
]

# Matches tokens like <illicit_activities_violation> or <pii_and_ip_not_violation>
# emitted by the guard model in its raw completion text.
_VERDICT_RE = re.compile(
    r"<(" + "|".join(CATEGORIES) + r")_(violation|not_violation)>"
)

# Session log: one JSON file per process lifetime, created at startup.
# The file is rewritten in full after each exchange so it's always valid JSON
# and can be inspected or downloaded mid-session.
LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)
_session_ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
_log_path = LOGS_DIR / f"session-{_session_ts}.json"
_session_log: list[dict] = []


# ── Request models ────────────────────────────────────────────────────────────
# Config mirrors the sidebar fields sent by the frontend on every request,
# so the user can change endpoints or toggle the guard without restarting.

class Config(BaseModel):
    guard_endpoint: str
    task_endpoint: str
    guard_enabled: bool
    system_prompt: str


class ChatRequest(BaseModel):
    message: str
    history: list[dict]   # full conversation so far, as role/content pairs
    config: Config


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_verdict(content: str) -> dict:
    # Default every category to not_violation, then overwrite with whatever
    # tokens the model actually emitted. Missing tokens stay as not_violation.
    verdict = {cat: "not_violation" for cat in CATEGORIES}
    for m in _VERDICT_RE.finditer(content):
        verdict[m.group(1)] = m.group(2)
    return verdict


def classify(endpoint: str, message: str) -> dict:
    # The guard model's chat template expects the user turn prefixed with
    # "text: " — this is baked into the GGUF and cannot be changed at runtime.
    t0 = time.monotonic()
    try:
        r = requests.post(
            f"{endpoint}/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": f"text: {message}"}],
                "max_tokens": 256,
            },
            timeout=10,
        )
        r.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise HTTPException(502, f"Guard model unreachable at {endpoint}")
    except requests.exceptions.Timeout:
        elapsed = time.monotonic() - t0
        print(f"[guard] TIMEOUT after {elapsed:.1f}s at {endpoint}", flush=True)
        raise HTTPException(504, "Guard model timed out")
    except requests.exceptions.HTTPError as e:
        raise HTTPException(502, f"Guard model error: {e}")

    elapsed = time.monotonic() - t0
    content = r.json()["choices"][0]["message"]["content"]
    verdict = parse_verdict(content)
    violations = [k for k, v in verdict.items() if v == "violation"]
    print(f"[guard] {elapsed:.2f}s — {violations or 'clean'}", flush=True)
    return verdict


def generate(endpoint: str, messages: list[dict]) -> tuple[str, float | None]:
    # Sends the full message list (system prompt + history + new user turn)
    # to the task model. tokens_per_sec comes from llama.cpp's timings object,
    # which is not part of the OpenAI spec and may be absent on other backends.
    try:
        r = requests.post(
            f"{endpoint}/v1/chat/completions",
            json={"messages": messages},
            timeout=30,
        )
        r.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise HTTPException(502, f"Task model unreachable at {endpoint}")
    except requests.exceptions.Timeout:
        raise HTTPException(504, "Task model timed out")
    except requests.exceptions.HTTPError as e:
        raise HTTPException(502, f"Task model error: {e}")

    data = r.json()
    content = data["choices"][0]["message"]["content"]
    tokens_per_sec = data.get("timings", {}).get("predicted_per_second")
    return content, tokens_per_sec


def append_log(entry: dict) -> None:
    # Append to the in-memory list and flush the whole array to disk so the
    # file is always valid JSON even if the process is killed mid-session.
    _session_log.append(entry)
    _log_path.write_text(json.dumps(_session_log, indent=2))


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    # Serve index.html directly from disk so edits take effect on refresh
    # without restarting the server.
    html = (Path(__file__).parent / "index.html").read_text()
    return HTMLResponse(content=html)


@app.post("/api/chat")
def chat(req: ChatRequest):
    verdict = None
    output_verdict = None
    blocked = False
    output_blocked = False
    response = None
    tokens_per_sec = None

    # Step 1: classify user input. A single violation blocks the request.
    if req.config.guard_enabled:
        verdict = classify(req.config.guard_endpoint, req.message)
        blocked = any(v == "violation" for v in verdict.values())

    # Step 2: if not blocked, forward to the task model.
    if not blocked:
        messages = []
        if req.config.system_prompt:
            messages.append({"role": "system", "content": req.config.system_prompt})
        messages.extend(req.history)
        messages.append({"role": "user", "content": req.message})
        response, tokens_per_sec = generate(req.config.task_endpoint, messages)

    # Step 3: classify the model's response (output-side guard).
    if req.config.guard_enabled and response is not None:
        output_verdict = classify(req.config.guard_endpoint, response)
        output_blocked = any(v == "violation" for v in output_verdict.values())

    # Step 4: log the exchange regardless of outcome.
    # task_response is always logged for audit purposes even when output is blocked.
    append_log({
        "timestamp": datetime.now().isoformat(),
        "user_message": req.message,
        "classifier_verdict": verdict,
        "output_verdict": output_verdict,
        "blocked": blocked,
        "output_blocked": output_blocked,
        "task_response": response,
        "tokens_per_second": tokens_per_sec,
    })

    return {
        "response": None if output_blocked else response,
        "verdict": verdict,
        "output_verdict": output_verdict,
        "blocked": blocked,
        "output_blocked": output_blocked,
        "tokens_per_sec": tokens_per_sec,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)
