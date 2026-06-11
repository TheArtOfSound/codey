# Codey Droplet Deploy

Target host: `codey.imagineqira.com`

## Prerequisites

1. Point an `A` record for `codey.imagineqira.com` to the droplet IP.
2. Install Docker Engine and Docker Compose plugin on the droplet.
3. Copy this repo to the droplet.
4. Copy `.env.prod.example` to `.env.prod` and fill in the secrets.

## Required env values

- `POSTGRES_PASSWORD`
- `SECRET_KEY`
- `ANTHROPIC_API_KEY`
- `SENDGRID_API_KEY` or `RESEND_API_KEY`
- `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`
- `STRIPE_SECRET_KEY`
- `STRIPE_PUBLISHABLE_KEY`
- `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `ACME_EMAIL`

Keep these values for the one-domain setup:

- `FRONTEND_URL=https://codey.imagineqira.com`
- `API_URL=https://codey.imagineqira.com/api/proxy`

## Launch

```bash
cp .env.prod.example .env.prod
$EDITOR .env.prod
docker compose -f docker-compose.prod.yml up -d --build
```

## What this stack does

- `caddy` terminates TLS for `codey.imagineqira.com`
- `/api/proxy/*` is reverse-proxied to the FastAPI backend
- `/sessions/*` and `/build/*/stream` are reverse-proxied to backend websockets
- all other traffic goes to the Next.js frontend
- `celery_worker` and `celery_beat` keep autonomous jobs running continuously

## Updates

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build
```
