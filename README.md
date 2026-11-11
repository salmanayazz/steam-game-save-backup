# Steam Game Save Backup

Back up Steam Cloud saves with a web interface that allows you to view and download your saves

## Run

```
uv run uvicorn app.main:app
```

Or with Docker:

```
docker compose up -d
```

Open http://127.0.0.1:8000, sign in with the QR code (will require Steam Guard on mobile), and click **Sync now**.
