# Local development

AlbumFP Demo stays within the v1.3 product boundary; its current Demo release is
v1.3.1, a Demo-only patch. It runs entirely on this computer. On Windows,
install Python 3.13 or newer,
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

If `MEDIA_STORAGE_ROOT` is on a separately mounted local volume, you can set
`MEDIA_STORAGE_REQUIRED_MOUNTPOINT` to that mount root. The Demo checks that it
is mounted and contains the configured media directory before writing media.
Setup can create an empty runtime directory at the configured path, so mount
the volume before starting the Demo.

To create an optional screenshot account and four synthetic, EXIF-free images,
run this once from `backend`:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe scripts\seed_synthetic_gallery.py --confirm-local-demo
```

The command requires an interactive password prompt and accepts only the
`albumfp_demo` database on PostgreSQL loopback port `55432`. It creates the
fixed account `albumfp_demo_synthetic_gallery`; if that name already exists,
it stops without changing the account. It also requires `REGISTRATION_MODE=open`
and uses the normal registration route, so a closed or invite-only setup stays
closed to the seed command.

## Safe public export

To create a ZIP for sharing, run this from the repository root:

```powershell
.\scripts\export-public.ps1
```

The script requires a clean Git worktree, runs the publication scan, and
archives tracked `HEAD` files only. It writes the ZIP under
`%LOCALAPPDATA%\AlbumFP-Demo\exports` and prints its SHA256 digest.
