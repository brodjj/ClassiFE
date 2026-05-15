# ClassiFE

A local developer tool for testing LLM safety classifier models alongside task models. ClassiFE proxies between two llama.cpp server instances — a safety classifier and a general-purpose LLM — via a browser-based chat interface.

Every message passes through the classifier twice: once on the way in (input guard) and once on the way out (output guard). Both verdicts are shown as labelled category badges in the UI so you can observe classifier behaviour at both stages in real time.

---

## Prerequisites

- Python 3.10+
- [llama.cpp](https://github.com/ggerganov/llama.cpp) built with server support (`llama-server`)
- A safety classifier model in GGUF format (tested with [GA Guard](https://huggingface.co/collections/gabime/gaeguard-677e3cd3789f2a55ac23b52b))
- A general-purpose task model in GGUF format

---

## Installation

```bash
git clone https://github.com/brodjj/ClassiFE.git
cd ClassiFE
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

---

## Starting the Model Servers

ClassiFE expects two llama-server instances running before it starts:

**Classifier model** — port 8081:
```bash
llama-server \
  --model /path/to/classifier.gguf \
  --port 8081 \
  --ctx-size 2048 \
  -ngl 99
```

**Task model** — port 8080:
```bash
llama-server \
  --model /path/to/task-model.gguf \
  --port 8080 \
  --ctx-size 4096 \
  -ngl 99
```

Adjust `-ngl` (GPU layers) to suit your hardware. Remove it entirely for CPU-only inference.

---

## Running ClassiFE

```bash
source venv/bin/activate
python app.py
```

Then open `http://localhost:5000` in your browser.

Alternatively:
```bash
uvicorn app:app --port 5000
```

---

## Configuration

The sidebar in the UI exposes all runtime settings — no restart required:

| Setting | Default | Description |
|---|---|---|
| Guard Model Endpoint | `http://localhost:8081` | URL of the classifier llama-server |
| Task Model Endpoint | `http://localhost:8080` | URL of the task model llama-server |
| Guard Toggle | On | Bypass the classifier entirely when off |
| System Prompt | (helpful assistant) | System prompt sent to the task model |

---

## How the Classifier Works

ClassiFE is built around the GA Guard token format. The classifier receives text prefixed with `text: ` (required by the model's chat template) and emits one token per safety category:

```
<category_violation>  or  <category_not_violation>
```

The seven categories checked are:

- `pii_and_ip`
- `illicit_activities`
- `hate_and_abuse`
- `sexual_content`
- `prompt_security`
- `violence_and_self_harm`
- `misinformation`

### Input guard

Every user message is classified before being forwarded to the task model. If any category fires as `violation`, the message is blocked immediately — the task model is never contacted — and the UI shows **Prompt blocked: [categories]**.

### Output guard

If the input guard passes, the task model's response is classified before being shown to the user. This is the second line of defence: it catches unsafe content generated in response to prompts that successfully bypassed the input guard. If a violation is detected, the response is suppressed and the UI shows **Output blocked: [categories]**.

Both verdicts are displayed as labelled **Input** and **Output** badge rows beneath each message (green = safe, red = violation). The output guard only comes into play when a prompt clears the input guard — for most normal usage both rows will be all green.

---

## Session Logs

Each session writes a JSON log to `logs/session-<timestamp>.json`. Logs are also downloadable from the sidebar. Each entry records:

- `timestamp`
- `user_message`
- `classifier_verdict` — input guard verdict, per-category breakdown
- `output_verdict` — output guard verdict, per-category breakdown
- `blocked` — whether the prompt was blocked by the input guard
- `output_blocked` — whether the response was blocked by the output guard
- `task_response` — model response (recorded in the log even when output is blocked)
- `tokens_per_second` — task model generation speed

The `logs/` directory is excluded from version control.

---

## Ports

| Service | Default Port |
|---|---|
| ClassiFE | 5000 |
| Task model | 8080 |
| Classifier | 8081 |

All endpoints are configurable in the UI sidebar without restarting.
