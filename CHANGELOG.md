# Changelog

## v1.3.0 - Registration and media privacy hardening

- Closes registration when `REGISTRATION_MODE` is unknown or invalid.
- Returns the same not-found response for inaccessible and nonexistent media, including byte-range requests, while preserving permission errors for readable media without the requested capability.
- Audits local runtime and development dependencies for known advisories.

## v1.2.1 - Cache policy and touch targets

- Gives media-detail controls 44px touch targets on phones and tablets while preserving compact mouse controls.
- Keeps the local service worker limited to the versioned app shell; API and private media stay outside its cache, and synthetic screenshots use a content-derived revision.

## v1.2.0 - Mobile UX and WebApp

Adds phone navigation, responsive search, safe-area support, and an installable local WebApp shell. The Demo remains available only on the computer running its local services.

## v1.1.1 - Screenshot cache refresh

Versions public screenshot URLs by the content of the Demo's synthetic capture set, so updated screenshots are fetched instead of served from an older browser cache.

## v1.1.0 - Video playback and upload flow

Adds browser-captured video covers, local poster validation and storage, byte-range playback, and a single-file upload flow with real transfer progress. The Demo keeps these media files in its local filesystem.

## v1.0.0 - First versioned Demo release

The first local edition of the AlbumFP photo and video experience, with albums, library search, smart albums, sharing, comments, activity, favorites, archive, and trash.
