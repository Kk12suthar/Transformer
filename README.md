# ProjectX MVP

## Structure

- `backend/`: FastAPI + Postgres API + agent stream endpoints
- `frontend/`: React UI (ChatGPT-dark style) wired to backend

## Start Backend

```powershell
cd MVP\backend
pip install -r requirements.txt
Copy-Item .env.example .env
python run.py
```

## Start Frontend

```powershell
cd MVP\frontend
npm install
Copy-Item .env.example .env
npm run dev
```

## Core User Flow

1. Sign up / sign in.
2. Create chat session from sidebar.
3. Upload CSV/XLSX table.
4. Run transform query and watch live stream events.
5. Review final output.
6. Download cleaned table as CSV.

## CI/CD

- `CI` runs on every push and pull request to `master` or `main`.
- It checks backend syntax/imports and makes sure the frontend can build.
- `CD` runs on pushes to `master` or `main` and uploads build artifacts.
- This is continuous delivery for now, not automatic deployment to a live server yet.
