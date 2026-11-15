# Deploying Data Fog Agent

The whole app runs on one Linux server with Docker Compose. Caddy sits in front, gets an
HTTPS certificate from Let's Encrypt by itself, and is the only thing reachable from outside.

```
internet ──443──► Caddy ──/api/*, /mcp──► FastAPI (2 workers) ──► Postgres + pgvector
                    │                          │                      ▲
                    └──everything else──► Next.js                     │
                                               Redis ◄── arq worker ──┘
```

## What you need

- A Linux VPS with **2 vCPU, 4 GB RAM, 40 GB disk** (Ubuntu 24.04 is fine). Hetzner, DigitalOcean,
  Lightsail and similar all work. 2 GB RAM is too tight for building the frontend image.
- A domain or subdomain, for example `datafog.yourname.dev`.
- Your NVIDIA and Gemini API keys.

## 1. Point the domain at the server

At your DNS provider add an **A record**: `datafog.yourname.dev -> <server IP>`. Caddy can only get
a certificate once this resolves, so check it first: `ping datafog.yourname.dev`.

## 2. Prepare the server

```bash
ssh root@<server IP>

# Docker + Compose plugin
curl -fsSL https://get.docker.com | sh

# Firewall: SSH and web only. Postgres and Redis are never published.
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

## 3. Get the code onto the server

```bash
git clone <your repo URL> data-fog-agent
cd data-fog-agent/data-fog-agent   # the app is in this folder of the repository
```

## 4. Fill in the settings

```bash
cp .env.prod.example .env.prod
nano .env.prod
```

Set `DOMAIN`, both database passwords, `JWT_SECRET`, `CONFIG_ENCRYPTION_KEY` and the two API keys.
The file shows a one-line command to generate each secret. **Back up `CONFIG_ENCRYPTION_KEY`**:
without it, saved source settings can't be decrypted.

## 5. Start it

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

The first build takes a few minutes. Then open `https://<your domain>`. The certificate is issued
on the first request, so that request can take a few seconds.

Check that everything is healthy:

```bash
docker compose -f docker-compose.prod.yml ps
curl https://<your domain>/api/health        # {"ok":true}
```

To save typing, add an alias: `alias dc='docker compose -f docker-compose.prod.yml --env-file .env.prod'`.
The rest of this guide uses it.

## 6. Create the admin and (optionally) the demo company

```bash
dc exec backend uv run python -m app.create_admin --org "Chaiwala Co." --email you@example.com --name "Your Name"
```

For the demo company (good for showing the project):

```bash
dc exec backend uv run python /demo/generate.py
```

Then log in as the admin, open **Sources** and click **Sync now** on each source. Demo users log in
with the password `chaiwala-demo`; change or remove them before real users get access.

## Connecting Claude or another MCP client

Every user can make a personal API token on the **Settings** page. The MCP endpoint is
`https://<your domain>/mcp` (Streamable HTTP), with the token sent as `Authorization: Bearer <token>`.
For Claude Code:

```bash
claude mcp add --transport http data-fog https://<your domain>/mcp --header "Authorization: Bearer <token>"
```

A token sees exactly what its user sees, personal data stays masked, and every call is in the audit
log with actor `mcp`. Revoking the token on the Settings page cuts the client off at once.

## Tracing model calls (optional)

Set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_HOST` in `.env.prod` and restart
(`dc up -d`). Every chat and Auto-Docs model call then shows up in Langfuse with its prompt, answer,
tokens and timing. Leave the keys empty to keep tracing off.

## Updating

```bash
git pull
dc up -d --build
```

Migrations run automatically when the backend starts.

## Backups

The database holds everything that matters (catalogue, users, chats, audit log). A nightly dump:

```bash
mkdir -p /root/backups
crontab -e
# add this line:
0 3 * * * cd /root/data-fog-agent/data-fog-agent && docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T db pg_dump -U postgres datafog | gzip > /root/backups/datafog-$(date +\%F).sql.gz && find /root/backups -mtime +14 -delete
```

Copy the backups off the server too (another machine or object storage). A backup that only lives
on the server it protects is not a backup.

Restore into an empty database:

```bash
gunzip -c datafog-2026-11-14.sql.gz | dc exec -T db psql -U postgres datafog
```

## Logs and troubleshooting

```bash
dc logs -f backend          # API errors
dc logs -f worker           # sync and embedding jobs
dc logs caddy               # certificate problems
```

| Problem | Fix |
|---|---|
| Browser shows a certificate error | DNS doesn't point at the server yet, or ports 80/443 are blocked. Check `dc logs caddy`. |
| `set APP_DB_PASSWORD in .env.prod` | A required value is empty in `.env.prod`, or `--env-file .env.prod` was left out. |
| Changed a password in `.env.prod`, now the DB login fails | Database passwords are only applied when the volume is first created. Change them inside Postgres too (`ALTER ROLE app PASSWORD '...'`). |
| `429 Too many requests` | Rate limits: 10 logins per minute per IP, 30 chat messages and 60 MCP requests per minute per user. |
| MCP client gets `401` | The token is wrong or was revoked. Make a new one on the Settings page. |

## What is protected

- Only ports 80 and 443 are open. Postgres and Redis are on Docker's internal network.
- HTTPS everywhere, with HSTS. The refresh cookie is `Secure`, `HttpOnly`, `SameSite=Strict`.
- Login, chat and the MCP endpoint are rate limited.
- MCP clients need a personal API token. Only its SHA-256 hash is stored.
- The app connects to Postgres as a non-superuser role, so row-level security is enforced.
- Containers restart on failure and after a reboot; logs are capped at 50 MB per container.
