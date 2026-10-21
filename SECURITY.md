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
- Give each app its own key in `LIGHTTRACE_API_KEYS` (long and random, e.g.
  `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`), so one can be removed without the others.
- Traces usually contain user data (prompts, answers, tool results). Set a retention period and limit
  who has the UI password.
