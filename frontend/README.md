# MVP Frontend

Production-style React UI for:
- auth (sign in / sign up)
- chat session sidebar
- file upload to session
- live transform event stream
- final output summary
- table preview + CSV download

## Run

1. Copy env:

```powershell
Copy-Item .env.example .env
```

2. Install and start:

```powershell
npm install
npm run dev
```

3. Build for production:

```powershell
npm run build
```

Default frontend URL: `http://localhost:5173`  
Expected backend URL: `http://127.0.0.1:8100`
