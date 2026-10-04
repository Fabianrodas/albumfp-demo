# AlbumFP Demo Through v1.3.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish a public-safe local-only AlbumFP Demo through v1.3.0.

**Architecture:** Start from the verified v1.0.0 product source, selecting application code, migrations, tests, and safe assets. Apply each later historical release delta in order while replacing production storage and service configuration with guarded PostgreSQL, local files, and loopback-only processes.

**Tech Stack:** Angular, TypeScript, Python, Flask, SQLAlchemy, Alembic, PostgreSQL, local filesystem media, PowerShell setup scripts.

**Spec:** `docs/superpowers/specs/2026-10-04-albumfp-demo-design.md`

## Global Constraints

- Product version ends at exactly `1.3.0`.
- Database names are `albumfp_demo` and `albumfp_demo_test`; destructive operations accept only loopback hosts and explicit allowlisted Demo databases.
- PostgreSQL is required; SQLite is not an application or test substitute.
- Frontend, backend, and database bind to loopback only.
- Runtime media and generated local state stay outside Git.
- Reconstruct in this order: foundation, v1.0.0, v1.1.0, v1.1.1, v1.2.0, v1.2.1, v1.3.0.
- Do not include deployment files, private operational documents, later-version admin behavior, external production calls, secrets, or real user data.
- Create an annotated release tag only after its tests, browser checks where applicable, public-safety review, clean-tree check, and commit push succeed.

## Review Focus

- A malformed database URL or production-like hostname is rejected before any connection or destructive action; pin this in database guard tests.
- Tests cannot target the development database; pin this in test bootstrap tests.
- Unauthorized and nonexistent media IDs have indistinguishable responses at v1.3.0; pin this in media endpoint integration tests.
- Local files with traversal names, invalid content, oversized payloads, or interrupted uploads are rejected or cleaned safely; pin these in storage and upload tests.
- Tablet/coarse-pointer layouts and long unbroken album titles do not overflow; pin this with responsive browser checks at v1.2.0 and v1.2.1.

---

### Task 1: Establish local-only foundation and continuity tracking

**Files:**
- Create: root `.env.example`, `.gitignore`, `setup.ps1`, and `dev.ps1`
- Create: `backend/app/config.py`, `backend/app/db/safety.py`, `backend/requirements.txt`, and backend test configuration
- Create: `frontend/package.json`, lockfile, Angular/TypeScript configuration, and `frontend/src/environments/environment.ts`
- Create: `docs/DEVELOPMENT.md`, `docs/TESTING.md`, `docs/RELEASE_STATUS.json`, `CHANGELOG.md`
- Test: `backend/tests/test_database_safety.py`; frontend test/build scripts

**Interfaces:**
- Backend configuration exposes a parsed local `DATABASE_URL`, a guarded SQLAlchemy engine, local `MEDIA_ROOT`, and loopback bind defaults.
- Frontend API base URL resolves to the local backend and has no production fallback.
- Setup is idempotent and refuses unsafe database targets before creating or migrating a database.

- [ ] Create the minimal Angular/Flask project configuration and dependency manifests using the v1.0.0 tag as the version source; do not import product routes or screens yet.
- [ ] Add `backend/tests/test_database_safety.py` cases for loopback/database-name allowlisting and rejection of production-like or malformed URLs.
- [ ] Add cases proving test setup requires `albumfp_demo_test` and refuses `albumfp_demo`.
- [ ] Implement `parse_demo_database_url(url, *, purpose) -> URL` and `assert_demo_database_url(url, *, purpose) -> None` in `backend/app/db/safety.py`; only test/bootstrap purposes may use their respective allowlisted database names.
- [ ] Implement guarded settings, SQLAlchemy startup, local media directories, and CORS/session defaults in `backend/app/config.py`.
- [ ] Add Windows setup/start scripts that do not inspect or operate on the sibling project.
- [ ] Run isolated backend safety tests, frontend unit tests, and a production build; fix failures.
- [ ] Commit the passing foundation; push only after confirming the exact Demo origin.

### Task 2: Reconstruct and certify v1.0.0

**Files:**
- Modify: selected `backend/app/**`, `backend/migrations/**`, and `frontend/src/**` from the v1.0.0 source tag
- Create: selected v1.0.0 unit/integration tests and synthetic seed tooling
- Modify: version metadata in frontend and backend

**Interfaces:**
- Flask routes preserve the v1.0.0 API and server-side session contracts.
- SQLAlchemy models and Alembic revisions end at `0029_asset_comments`.
- Local media operations use the guarded local storage adapter.

- [ ] Import the v1.0.0 product baseline for authentication, library, albums, public pages, sharing, search/context, themes, PWA, export, recovery codes, passkeys, activity, notifications, and comments.
- [ ] Ensure the app uses PostgreSQL for all integration/database tests and a genuinely empty database for migration bootstrap.
- [ ] Add optional synthetic users, albums, comments, notifications, and media fixtures without real user content.
- [ ] Run v1.0.0 backend unit and PostgreSQL integration suites, frontend tests, type checks, build, and responsive smoke checks.
- [ ] Review the change set for local-only endpoints, safe assets, migration ceiling, and version agreement.
- [ ] Commit, push `main`, create annotated `v1.0.0`, push the tag, and verify its peeled remote target.

### Task 3: Reconstruct and certify v1.1.0 uploads and video

**Files:**
- Modify: `backend/app/api/media.py`, `backend/app/api/media_posters.py`, `backend/app/media/previews.py`, `backend/app/storage/**`
- Modify: `frontend/src/app/components/ui/upload-panel/**`, `frontend/src/app/components/ui/media-asset/**`, `frontend/src/app/pages/dashboard/**`, and applicable API services
- Create: upload-progress, poster, Range playback, and authorization tests

**Interfaces:**
- Upload API reports real per-file transfer progress and preserves existing validation/errors.
- Video delivery supports authorized byte ranges and poster retrieval from local media.

- [ ] Apply only the v1.0.0-to-v1.1.0 product changes for upload UX, video posters/cover selection, Range/206 playback, and the associated layout polish.
- [ ] Adapt poster and streaming reads to local storage without a remote media origin.
- [ ] Test interrupted/large uploads, poster persistence, authenticated seek requests, and 206/416 Range behavior.
- [ ] Run the v1.0 regression suite and browser checks for upload, video, comments, and public pages.
- [ ] Commit, push, create annotated `v1.1.0`, push it, and verify the remote peeled target.

### Task 4: Reconstruct and certify v1.1.1 screenshot cache behavior

**Files:**
- Modify: `frontend/src/app/components/ui/shot-frame/shot-frame.ts`, `frontend/src/app/core/site.ts`
- Test: screenshot revision and public cache behavior

- [ ] Apply the historical cache-revision change without production web-server configuration.
- [ ] Verify changed screenshot URLs invalidate browser caches while other bundled assets remain correct.
- [ ] Run frontend tests/build and public-page smoke checks.
- [ ] Commit, push, create annotated `v1.1.1`, push it, and verify the remote peeled target.

### Task 5: Reconstruct and certify v1.2.0 mobile/WebApp experience

**Files:**
- Create: `frontend/src/app/components/layout/mobile-nav/**`, `frontend/src/app/pages/webapp/**`, and install-prompt service
- Modify: dashboard layout, sidebar, topbar, icon/media grid, theme initialization, routes, and global styles
- Test: mobile navigation, WebApp page, responsive and safe-area behavior

- [ ] Apply the historical phone information architecture, mobile navigation, safe-area styling, install prompt, theme/status behavior, and WebApp page.
- [ ] Preserve desktop routes/layout and use the historical breakpoint behavior.
- [ ] Test phone sizes, public/authenticated navigation, light/dark mode, and PWA shell behavior in browser emulation.
- [ ] Run frontend tests/build and v1.0/v1.1 regression checks.
- [ ] Commit, push, create annotated `v1.2.0`, push it, and verify the remote peeled target.

### Task 6: Reconstruct and certify v1.2.1 stabilization

**Files:**
- Modify: `frontend/src/app/core/site.ts`, `frontend/src/app/pages/dashboard/media-detail/media-detail.css`, and applicable local static serving/cache config
- Test: cache policy, tablet, long-title, and coarse-pointer behavior

- [ ] Apply the local equivalent of the cache policy fix; do not import production proxy configuration.
- [ ] Apply the historical touch-target changes and retain the long-title overflow fix.
- [ ] Browser-check desktop, phone, 768/1024 tablet widths, and coarse-pointer media controls.
- [ ] Run the complete pre-v1.3 regression suite and frontend build.
- [ ] Commit, push, create annotated `v1.2.1`, push it, and verify the remote peeled target.

### Task 7: Reconstruct and certify v1.3.0 security behavior

**Files:**
- Modify: `backend/app/api/media.py`, `backend/app/api/media_context.py`, `backend/app/domain/rules.py`, and safe dependency manifests
- Create/modify: registration-mode, media existence-oracle, quota, supply-chain, Range, and authorization tests
- Modify: product version sources to `1.3.0`

**Interfaces:**
- Invalid or unknown registration mode fails closed.
- Inaccessible and nonexistent media requests return the same externally observable status/body/header shape.
- Media range and context behavior continue to use local storage and the v1.3.0 API contract.

- [ ] Apply v1.3.0 application/security deltas while omitting infrastructure-specific media-origin and deployment changes.
- [ ] Add regression coverage for every unknown registration mode and every relevant media permission/ID condition.
- [ ] Audit dependency changes against lock files and current dependency advisories; change only justified ranges and regenerate locks reproducibly.
- [ ] Run the full PostgreSQL backend suite, empty-database migration gate, frontend unit/type/build gates, and local-only/publication scans.
- [ ] Run browser QA for guest/authenticated workflows, upload/image/video/seek, sharing/comments, library actions, Smart Albums/activity, recovery/passkeys where practical, themes, screen sizes, and media 404 equivalence.
- [ ] Fix failures and rerun affected regressions; capture final synthetic desktop/light/dark/mobile screenshots from the Demo.
- [ ] Finish README, Windows setup/test documentation, CI, changelog, and certification checklist.
- [ ] Review the exact push diff and history for secrets, user data, private configuration, and production endpoints.
- [ ] Commit and push the passing release, create annotated `v1.3.0`, push the tag, verify the peeled remote target, and confirm clean `main` with no later-version tags.

### Task 8: Final repository certification

**Files:**
- Modify: `README.md`, `docs/DEVELOPMENT.md`, `docs/TESTING.md`, `docs/RELEASE_STATUS.json`
- Test: all backend/frontend/security/browser gates and release/tag checks

- [ ] Re-run setup from a clean clone/database and verify every README command.
- [ ] Confirm all six annotated tags exist locally/remotely and `v1.3.0` is latest.
- [ ] Confirm no `.env`, runtime data, real user data, later-version code, or sensitive source material exists in the tree or pushed history.
- [ ] Confirm the golden source worktree remains unchanged.
- [ ] Record actual test, browser, publication scan, push, tag SHA, and final-tree results in the continuity checklist.
