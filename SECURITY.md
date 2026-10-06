# Security Policy

## Scope

This security policy applies to the public **AlbumFP Demo** repository only.

AlbumFP Demo is a local-only, source-available demonstration project. It is separate from the private/production AlbumFP application and infrastructure.

The following are **out of scope unless the owner gives separate, explicit authorization**:

- the production AlbumFP application;
- `albumfp.com`;
- private servers, storage systems, databases, VPNs, or infrastructure;
- third-party services or accounts;
- social engineering, phishing, credential attacks, or denial-of-service testing;
- accessing, modifying, deleting, or exfiltrating data that is not yours.

Do not use the Demo repository as authorization to test any production system.

## Supported Version

The current supported Demo release is:

| Version | Supported |
| --- | --- |
| `v1.3.1` | Yes |
| `v1.3.0` and older | No active support |
| `v1.4+` | Does not exist |

AlbumFP Demo is a closed demonstration project rather than an actively developed product. Security reports for the current release are still welcome, but response and remediation timeframes are not guaranteed.

## Reporting a Vulnerability

Please **do not open a public GitHub Issue** for a vulnerability that could expose users, credentials, private data, security controls, or a practical exploit.

Prefer one of these private channels:

1. GitHub's private vulnerability reporting / Security Advisory workflow for this repository, if it is enabled.
2. Otherwise, contact the repository owner through a verified private channel associated with the GitHub account.

Do not include passwords, access tokens, API keys, private keys, session cookies, personal data, or other sensitive material unless it is strictly necessary to explain the issue. If sensitive evidence is necessary, first establish a private reporting channel.

A useful report should include:

- a concise description of the issue;
- the affected version or commit;
- the affected component or route;
- clear reproduction steps;
- expected versus observed behavior;
- the realistic security impact;
- any prerequisites required for exploitation;
- a minimal proof of concept, when appropriate;
- suggested remediation, if known.

Please avoid sending large archives, production data, or unrelated local files.

## Responsible Testing

Security research must be performed only against systems and data you are authorized to use.

For AlbumFP Demo:

- use your own local installation;
- use synthetic/test data;
- avoid destructive testing unless it is necessary and confined to your own environment;
- do not attempt to discover or access real AlbumFP infrastructure;
- do not attempt credential stuffing, password spraying, phishing, social engineering, or denial-of-service activity;
- do not publish a working exploit before the owner has had a reasonable opportunity to review the report.

If a test unexpectedly exposes credentials, private information, or access to a system outside the local Demo, stop testing and report the issue privately.

## What to Report

Examples of useful reports include:

- authentication or authorization bypasses;
- unintended access to another user's data;
- session or CSRF weaknesses;
- insecure direct object references;
- path traversal or arbitrary file access;
- unsafe upload handling;
- injection vulnerabilities;
- secret or credential exposure;
- security-sensitive misconfiguration in the Demo;
- vulnerabilities that materially bypass the Demo's documented local-only or access-control boundaries.

Reports about general code quality, feature requests, unsupported operating systems, or non-security UI issues should not use the security-reporting channel.

## Secrets Found in the Repository

If you believe a real credential, token, private key, password, or other secret has been committed:

1. do not use it;
2. do not post it publicly;
3. report the file path and commit privately;
4. avoid reproducing the secret value in the report unless absolutely necessary.

The repository is intended to contain no real production secrets.

## Third-Party Dependencies

Third-party frameworks, libraries, packages, fonts, and other dependencies remain subject to their own security policies and upstream support processes.

If a report concerns an upstream dependency rather than AlbumFP Demo code, please identify the affected dependency and version. Reports are still useful when the Demo requires an update or mitigation.

## Disclosure

Please allow the repository owner a reasonable opportunity to investigate and, where appropriate, prepare a fix before public disclosure.

Coordinated disclosure is appreciated. No promise of payment, bounty, or other compensation is made unless explicitly agreed in writing before submission.

## License and Security Research

This security policy does not expand the permissions granted by the repository's license.

AlbumFP Demo remains **source-available, non-commercial, and not open-source software**. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE) for the applicable terms.

Copyright © 2026 Fabian Rodas. All rights reserved.
