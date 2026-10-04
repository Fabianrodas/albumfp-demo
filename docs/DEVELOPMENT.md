# Local development

AlbumFP Demo runs entirely on this computer. On Windows, install Python 3.13,
Node.js 22 or newer, and PostgreSQL 14 or newer. `setup.ps1` creates a separate
Demo-owned PostgreSQL cluster on `127.0.0.1:55432`; it does not use the default
PostgreSQL service or the real AlbumFP database.

From the repository root, run:

```powershell
.\setup.ps1
.\dev.ps1
```

Open `http://localhost:4200`. The Flask API listens on `127.0.0.1:5000`. Press
Ctrl+C in the `dev.ps1` window to stop its frontend, backend, and any database
cluster it started.

Setup generates `.env` with local-only credentials and a session key. It also
places PostgreSQL files and uploaded media under
`%LOCALAPPDATA%\AlbumFP-Demo`, outside the repository. `.env` and all runtime
data are ignored by Git. The databases are `albumfp_demo` and
`albumfp_demo_test`.
