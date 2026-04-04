# MVP Backend

Minimal backend for:
- sign up / sign in
- chat sessions (sidebar list)
- file upload to Postgres tables
- transform chat (SSE stream)
- cleaned table preview + CSV download

## Run

1. Create a virtual env and install dependencies:

```powershell
pip install -r requirements.txt
```

2. Create `.env` from `.env.example`.

3. Start server:

```powershell
python run.py
```

Server runs on `http://127.0.0.1:8100` by default.

## API Overview

- `POST /api/v1/auth/signup`
- `POST /api/v1/auth/signin`
- `GET /api/v1/chat/sessions`
- `POST /api/v1/chat/sessions`
- `POST /api/v1/upload/files`
- `GET /api/v1/chat/sessions/{session_id}/tables`
- `GET /api/v1/tables/{table_name}/preview`
- `GET /api/v1/tables/{table_name}/download`
- `POST /adk-api/transform/activate`
- `POST /adk-api/transform/heartbeat`
- `POST /adk-api/transform/deactivate`
- `GET /adk-api/transform/runner-status`
- `POST /adk-api/transform/stream`
