# Railway Deployment

This repo can run on Railway with:

- one `web` service for FastAPI
- one `cron` service for APScheduler
- one Railway Postgres database

The repo ships with:

- a `Dockerfile`, so Railway does not have to guess between the Python app and
  the Node-based Tailwind asset build
- a shared `railway.toml` config-as-code file for deploy settings that are safe
  for both services

Because both services deploy the same repo, service-specific behavior stays in
service variables like `APP_ROLE` instead of `railway.toml`.

## One-time setup

Create and link the project from the repo root:

```bash
railway init --name family-events
```

Add Postgres plus the two app services:

```bash
railway add --database postgres
railway add --service web
railway add --service cron
```

Link the local repo to the web service:

```bash
railway link --service web
```

## Service variables

Set the required shared variables on both services:

```bash
railway variable set OPENAI_API_KEY=... SESSION_SECRET=... APP_BASE_URL=https://<your-domain> --service web
railway variable set OPENAI_API_KEY=... SESSION_SECRET=... APP_BASE_URL=https://<your-domain> --service cron
```

Point both services at the Railway Postgres service:

```bash
railway variable set DATABASE_URL='${{Postgres.DATABASE_URL}}' --service web
railway variable set DATABASE_URL='${{Postgres.DATABASE_URL}}' --service cron
```

Set the service role:

```bash
railway variable set APP_ROLE=web --service web
railway variable set APP_ROLE=cron --service cron
```

Recommended production hardening for the web service:

```bash
railway variable set SESSION_COOKIE_SECURE=true SESSION_COOKIE_SAME_SITE=lax --service web
```

Optional notification/weather secrets can be added the same way:

- `WEATHER_API_KEY`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM_NUMBER`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `RESEND_API_KEY`
- `EMAIL_FROM`

## Deploy

Deploy the web service:

```bash
railway up --service web
```

Deploy the scheduler worker:

```bash
railway up --service cron
```

Generate a Railway domain for the web service:

```bash
railway domain --service web
```

After the domain exists, update `APP_BASE_URL` on both services to that HTTPS
origin and redeploy the web service.

## Runtime behavior

- `APP_ROLE=web` runs Alembic migrations, then starts `uv run python -m src.main serve`
- `APP_ROLE=cron` starts `uv run python -m src.cron`
- the web service should expose port `8000`, which Railway maps through `$PORT`
- `railway.toml` forces Dockerfile builds and shared deploy behavior for both services

## Service-specific config-as-code

Railway supports custom config file paths per service. If you want separate
config-as-code files later, create files such as `railway.web.toml` and
`railway.cron.toml`, then point each Railway service at the matching absolute
repository path in the Railway service settings.

## Verification

Check the linked project:

```bash
railway status
```

Open logs:

```bash
railway logs --service web
railway logs --service cron
```

Health probe:

```bash
curl -fsS https://<your-domain>/health
```

## GitHub Actions

The repo includes [`railway-deploy.yml`](../.github/workflows/railway-deploy.yml).

Behavior:

- pull requests build the Docker image only
- pushes to `main` build, then deploy `web` and `cron`
- manual runs via GitHub Actions also build and deploy both services

Required GitHub secret:

- `RAILWAY_TOKEN`: use a Railway project token scoped to this project/environment

The workflow currently targets:

- project id `852d77af-81b9-40a0-b51a-09048159f911`
- environment `production`

If you move this repo to a different Railway project, update those workflow env values.
