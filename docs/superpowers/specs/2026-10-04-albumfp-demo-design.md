# AlbumFP Demo Through v1.3.0

## Purpose

Build a faithful, public-safe edition of AlbumFP that runs on one developer's
computer. The six historical product tags are the source of truth for behavior
and appearance. The Demo is versioned in the same release sequence and ends at
1.3.0.

## User-visible product

Keep AlbumFP's Angular and TypeScript interface, routes, copy, responsive
layouts, themes, account and session flows, media library, albums, search,
favorites, archive, trash, sharing, comments, Smart Albums, activity,
notifications, export, recovery codes, passkeys, and PWA behavior as they exist
through v1.3.0. Reuse safe product code and assets from each historical release.
Do not add behavior from later releases.

The historical sequence is incremental:

| Demo release | Product change |
| --- | --- |
| v1.0.0 | Complete initial product baseline and account/library workflows |
| v1.1.0 | Upload progress, video posters and seek playback, and product polish |
| v1.1.1 | Public screenshot cache revision behavior |
| v1.2.0 | Phone navigation, responsive refinements, and WebApp/PWA experience |
| v1.2.1 | Cache policy and touch-target stabilization |
| v1.3.0 | Fail-closed registration and media privacy hardening |

The product experience is not redesigned. The local-only differences are
limited to infrastructure: database and files stay on the developer's machine,
and the browser uses only the local Demo services.

## Runtime architecture

- Frontend: Angular and TypeScript, with release-specific dependencies and
  visual behavior derived from the historical tags.
- Backend: Python, Flask, SQLAlchemy, and Alembic, preserving product API and
  session semantics.
- Database: PostgreSQL only. Development uses `albumfp_demo`; tests use the
  separate `albumfp_demo_test` database. Destructive setup and test commands
  require loopback hosts and an explicit allowlisted database name. The Demo
  cluster uses dedicated port `55432`; the guard rejects default port `5432` so
  the app cannot attach to an existing local PostgreSQL service by accident.
- Media: local filesystem storage under an ignored, repository-external runtime
  directory. No remote media origin is part of the Demo.
- Network: frontend, backend, and PostgreSQL listen on loopback. Default
  frontend/backend ports are documented in `.env.example` and can be changed
  for local conflicts.

All configuration is explicit. `.env.example` contains placeholders and safe
local defaults; setup may generate a local session secret into an ignored
`.env`. Registration mode, media paths, and database endpoints fail closed.
Optional integrations that require third-party credentials are disabled unless
the user explicitly configures a safe local-compatible substitute. No feature
silently calls the real product domain.

## Data and privacy

The repository contains code and synthetic fixtures only. Runtime uploads,
temporary files, quarantined files, local database exports, and generated QA
artifacts are ignored. Seed data is optional and synthetic. Public screenshots
are captured from the completed Demo with synthetic data.

Use only product code, public-facing assets, migrations, and tests from the
verified historical tags. Exclude deployment configuration, operations
documents, credentials, real user media, and Git metadata. The final migration
lineage is exactly `0001` through `0029_asset_comments`. Post-v1.3.0 behavior,
including admin functionality, is out of scope.

## Release and verification

Each release is implemented from its exact tag delta, tested, committed, and
published as an annotated Demo tag only after its gate passes. The v1.3.0 gate
includes PostgreSQL bootstrap and migrations, backend tests, frontend tests and
production build, local-only and publication scans, and real-browser QA on
desktop, phone, and tablet. Pushes target only the configured Demo origin and
`main`; no deployment workflow is created.

README is a user-facing product overview with a logo, product link, current
synthetic screenshots, and short Windows setup instructions. Detailed local
development and test commands live in `docs/DEVELOPMENT.md` and
`docs/TESTING.md`.

## Acceptance criteria

1. Product behavior and UI match the six historical releases, in order, through
   exactly v1.3.0.
2. The app uses PostgreSQL and local filesystem media with fail-closed database
   safeguards.
3. Runtime services bind to loopback and the browser needs no production or
   cloud service.
4. No private configuration, secrets, real user data, or later-version admin
   behavior is published.
5. Fresh setup, automated tests, browser QA, publication scans, and README
   instructions pass before final certification.
6. The six annotated Demo tags are present locally and remotely, `main` is
   pushed, and the final working tree is clean.
