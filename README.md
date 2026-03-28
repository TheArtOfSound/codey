# CODEY

**AI-powered code generation platform that builds production-ready software.**

## What Codey Does

- **Intelligent Code Generation** -- Describe what you want in natural language and Codey generates complete, tested codebases using NFET-guided quality analysis and multi-provider LLM routing.
- **Autonomous Repository Management** -- Connect your GitHub repos and Codey continuously monitors, analyzes, and submits PRs with improvements, refactors, and security fixes on a nightly schedule.
- **Adaptive Memory System** -- Codey learns your coding style, framework preferences, and project conventions across sessions, producing code that feels like yours from day one.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI, Python 3.12, SQLAlchemy (async) |
| Frontend | Next.js 14, TypeScript, Tailwind CSS |
| Database | PostgreSQL 16 + pgvector |
| Cache / Broker | Redis 7 |
| Task Queue | Celery + Celery Beat |
| Auth | JWT, GitHub OAuth, Google OAuth |
| Payments | Stripe (subscriptions + credit top-ups) |
| Sandbox | E2B cloud sandboxes (local fallback) |
| Monitoring | Sentry |
| CI/CD | GitHub Actions |

## Quick Start

```bash
# Clone the repository
git clone https://github.com/qira-ai/codey.git
cd codey

# Copy the environment template and fill in your keys
cp .env.example .env

# Start all services
docker-compose up --build
```

The API will be available at `http://localhost:8000` and the frontend at `http://localhost:3000`.

## Project Structure

```
codey/
  codey/
    saas/
      api/            # FastAPI route handlers
      auth/           # Authentication (JWT, OAuth)
      billing/        # Stripe integration, credit management
      build_mode/     # Multi-phase project builder
      credits/        # Credit ledger and charging
      intelligence/   # LLM routing, research, caching
      memory/         # Adaptive user memory engine
      migrations/     # SQL schema (init.sql)
      models/         # SQLAlchemy / Pydantic models
      repos/          # GitHub repository integration
      sandbox/        # E2B / local code execution
      security/       # Audit logging, API key management
      sessions/       # Coding session orchestration
      tasks/          # Celery background jobs
      vault/          # Project versioning and export
    llm/              # Prompt builder, code agent
    graph/            # NFET dependency graph engine
    parser/           # AST extraction
    autonomous/       # Autonomous monitoring
    dashboard/        # Local dev dashboard
  frontend/           # Next.js application
  docker-compose.yml
  Dockerfile
  requirements.txt
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `SECRET_KEY` | JWT signing secret |
| `STRIPE_SECRET_KEY` | Stripe API secret key |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret |
| `GITHUB_CLIENT_ID` | GitHub OAuth app client ID |
| `GITHUB_CLIENT_SECRET` | GitHub OAuth app client secret |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `ANTHROPIC_API_KEY` | Anthropic Claude API key |
| `E2B_API_KEY` | E2B sandbox API key (optional, falls back to local) |
| `SENTRY_DSN` | Sentry error tracking DSN (optional) |
| `SENDGRID_API_KEY` | SendGrid email API key |
| `FRONTEND_URL` | Frontend URL for CORS |
| `API_URL` | Backend API URL |

## License

Proprietary - Qira LLC. All rights reserved.

## Contact

bryan@qira.ai
