# Tests and local checks

Run setup first so dependencies and the isolated PostgreSQL databases are
available:

```powershell
.\setup.ps1
```

Backend tests run from `backend` and use PostgreSQL, including the dedicated
`albumfp_demo_test` database:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest
```

Frontend tests and the production build run from `frontend`:

```powershell
Set-Location ..\frontend
npm.cmd test
npm.cmd run build
```

Never point tests or setup at a database other than `albumfp_demo_test` or the
Demo-owned `albumfp_demo`. The backend URL guard requires an explicit local
PostgreSQL URL on port `55432` and refuses the default local PostgreSQL port.
