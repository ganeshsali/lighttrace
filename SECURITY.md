# Security

## Reporting a vulnerability

Please **don't open a public issue**. Report it privately through GitHub: the repository's
**Security** tab → **Report a vulnerability**. Include what you found, how to reproduce it, and which
version or commit you used. You'll get a reply as soon as a maintainer can look at it; please give us a
reasonable time to fix it before disclosing.

## Supported versions

Fixes go into the latest release only.

## Deploying safely

- lighttrace binds `127.0.0.1` by default. Expose it through a TLS reverse proxy (see the README's
  Caddy section) and set `LIGHTTRACE_COOKIE_SECURE=true`.
- Give each app a **project key** (UI → project → Keys) instead of an admin key from
  `LIGHTTRACE_API_KEYS`; a project key can only write to its own project and can be revoked.
- The OTLP/gRPC port has no TLS of its own: keep it on a private network or behind a TLS proxy.
- Traces usually contain user data (prompts, answers, tool results). Set a retention period and limit
  who has the UI password.
