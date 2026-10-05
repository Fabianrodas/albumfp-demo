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

Dependency advisory checks use the Python requirement manifests and npm lock:

```powershell
Set-Location ..\backend
.\.venv\Scripts\pip-audit.exe -r requirements.txt -r requirements-dev.txt
Set-Location ..\frontend
npm.cmd audit
```

Never point tests or setup at a database other than `albumfp_demo_test` or the
Demo-owned `albumfp_demo`. The backend URL guard requires an explicit local
PostgreSQL URL on port `55432` and refuses the default local PostgreSQL port.

## Publication scan

Run the deterministic repository scan from the Demo root:

```powershell
.\backend\.venv\Scripts\python.exe scripts\publication_scan.py
```

The scanner checks indexed Git blobs and modified tracked working copies. It
checks paths and text for credential patterns, unsafe database URLs, private
infrastructure addresses, private host/configuration material, stale
production-specific storage terms, external runtime URLs, and local artifacts.
It reads only tracked files, not ignored dependencies, caches, or `.env`. It is
a project-specific check, not a general secret scanner. Regression tests for
its detection rules are included in the backend suite.

For the v1.0.0 release gate, Gitleaks was unavailable in the local environment;
it was not run or represented by the deterministic scan.
