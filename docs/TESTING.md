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

Stage the intended release tree, then run the deterministic repository scan
from the Demo root:

```powershell
.\backend\.venv\Scripts\python.exe scripts\publication_scan.py
```

The scanner reads staged Git blobs and checks tracked paths and text for
credential patterns, literal database passwords, user home-directory paths,
non-loopback IP addresses, external runtime URLs, and ignored runtime artifacts.
It is a project-specific check, not a general secret scanner. Regression tests
for its route, test-file, and ignore rules are included in the backend suite.

For the v1.0.0 release gate, Gitleaks was unavailable in the local environment;
it was not run or represented by the deterministic scan.
