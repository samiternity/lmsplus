# Contributing to lms+

Open a pull request against `main` with a short description of what changed and how you tested it. For bugs, include the steps to reproduce the problem and what you expected to happen. Use made-up account details and records in tests, reports, and screenshots. Keep credentials, cookies, private endpoints, and real university records out of Git and GitHub comments.

Changes are accepted under the [MIT license](LICENSE). Keep existing third-party notices and tell us where any borrowed code or assets came from.

Report security issues through **Security → Advisories → Report a vulnerability** when private reporting is available. Include the affected version and steps to reproduce with synthetic data. If the form is unavailable, ask for a private reporting channel without sharing the details publicly.

## Run a copy on your computer

Install Python 3.11 and Node.js 22. Run these commands from the repository root.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt pytest
npm ci
cp .env.example .env.local
cp .dev.vars.example .dev.vars
```

To work on public pages, run `python run.py --env local` and open <http://127.0.0.1:5000>. Local mode keeps account and session files in ignored runtime directories. The automated tests use synthetic data.

To sign in, use an account you are authorized to use on Bahria University's real CMS and LMS. A local copy needs its own Cloudflare Worker for login and file transfers. Use your own provider accounts and keys.

1. Run `openssl rand -hex 32` three times. Put the three separate values in `.env.local` as `SECRET_KEY`, `LMS_WORKER_PROXY_KEY`, and `DOWNLOAD_ENCRYPTION_KEY`.
2. In `.dev.vars`, set `LMS_PROXY_SERVICE_KEY` to the value of `LMS_WORKER_PROXY_KEY`. Set `DOWNLOAD_ENCRYPTION_KEY` to the same value you gave it in `.env.local`.
3. Set `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY` in `.env.local` using [Cloudflare's local test keys](https://developers.cloudflare.com/turnstile/troubleshooting/testing/).
4. Start the Worker in one terminal and the app in another.

```sh
npx wrangler dev --local --config wrangler.example.toml --ip 127.0.0.1 --port 8787
```

```sh
. .venv/bin/activate
python run.py --env local
```

Open <http://127.0.0.1:5000>. If your network can't reach the university, deploy the Worker to your own account and update `LMS_WORKER_ORIGIN` in `.env.local`.

## Host your own copy

Start with the Cloudflare Worker and Flask app. Add the other services as you need them. Keep test and production provider accounts or projects separate where possible, and use different service keys for each deployment.

### Cloudflare Worker

Copy `wrangler.example.toml` to `wrangler.local.toml` and give the Worker a name in your account.

```sh
cp wrangler.example.toml wrangler.local.toml
npx wrangler login
npx wrangler deploy --config wrangler.local.toml
npx wrangler secret put LMS_PROXY_SERVICE_KEY --config wrangler.local.toml
npx wrangler secret put DOWNLOAD_ENCRYPTION_KEY --config wrangler.local.toml
```

Use the Worker's HTTPS origin as `LMS_WORKER_ORIGIN` in Flask and match the two keys as in the local setup. The proxy key stays on the server. Browsers use short-lived tokens for file requests. Create a Turnstile widget for your app hostname and use its own site and secret keys in the hosted app.

### Optional proxies

Give each provider its own 64-character hex key as `LMS_PROXY_SERVICE_KEY`. Set the matching key and HTTPS origin in Flask. Enter the origin without a path or query. Flask adds the proxy path.

| Provider | Flask origin | Flask secret | Path |
| --- | --- | --- | --- |
| Supabase | `LMS_SUPABASE_ORIGIN` | `LMS_SUPABASE_PROXY_KEY` | `/functions/v1/proxy` |
| Netlify | `LMS_NETLIFY_ORIGIN` | `LMS_NETLIFY_PROXY_KEY` | `/` |
| Vercel | `LMS_VERCEL_ORIGIN` | `LMS_VERCEL_PROXY_KEY` | `/api/proxy` |
| Val Town | `LMS_VAL_TOWN_ORIGIN` | `LMS_VAL_TOWN_PROXY_KEY` | `/` |

- Supabase. From the repo root, run `supabase login`, `supabase link --project-ref "$SUPABASE_PROJECT_REF"`, and `supabase functions deploy proxy`. Set the key in your project's Edge Function secrets. The function checks the key itself.
- Netlify. Link your own project from `proxies/netlify` with `netlify link`. Set the key for Functions in the intended deploy context and run `netlify deploy` for a preview. Use `public/` as the publish directory.
- Vercel. Link your project from `proxies/vercel` with `vercel link`. Use the Other framework preset, set the key in the matching environment, and run `vercel` for a preview.
- Val Town. Create an HTTP val, copy in `proxies/val-town/main.ts`, and set the key in the val's environment. Use the deployed URL from Val Town.

A proxy should return HTTP 401 when the key is missing or wrong and HTTP 403 when an authenticated request targets a host outside the university allowlist. Leave the optional origin unset until its checks pass. Keep proxy keys out of browser code and shell history.

### Cloud Run and Firebase

1. Enable Cloud Run, Cloud Build, Artifact Registry, and Secret Manager in your Google Cloud project. Create a runtime service account and grant it access to the individual secrets the app needs.
2. Deploy the root Dockerfile with `gcloud run deploy --source .`. Select your project, region, and runtime service account. Set `APP_ENV=production`, `APP_URL` to your app's HTTPS address, and your Worker origin and Turnstile site key. Bind `SECRET_KEY`, file and proxy keys, and `TURNSTILE_SECRET_KEY` from [Secret Manager](https://docs.cloud.google.com/run/docs/configuring/services/secrets). If you set a separate `CMS_ENCRYPTION_KEY`, keep it stable for as long as you use the data it protects. Otherwise, keep `SECRET_KEY` stable.
3. To use Firestore, create a database in your own project. Grant the app's runtime account the necessary database access and set `USE_FIRESTORE=true` and `FIREBASE_PROJECT_ID`. Deploy the included rules and indexes. The rules deny direct browser access.
4. For Firebase Hosting, copy the example below and change the rewrite to your Cloud Run service and region. Set `APP_URL` and the Turnstile hostname to the final site address.

```sh
firebase deploy --only firestore:rules,firestore:indexes --project "$FIREBASE_PROJECT_ID"
cp firebase.hosting.example.json firebase.hosting.local.json
# Edit the local Hosting file with your service name and region before deploying.
firebase deploy --config firebase.hosting.local.json --only hosting --project "$FIREBASE_PROJECT_ID"
```

For browser push, use your own Firebase browser app settings and public VAPID key from the variables in [`.env.example`](.env.example). Verify token registration and delivery before relying on it. Cloud Tasks also needs a separate authentication check on a fresh installation.

### Other integrations

- Cloud Scheduler. Use a separate `SCHEDULER_SECRET` for your deployment. Send it as the bearer token from Scheduler to the app's scheduler routes.
- Discord. Create your own app and bot. Register `/discord/callback` on your app hostname. Put `DISCORD_CLIENT_SECRET`, `DISCORD_BOT_TOKEN`, and any `DISCORD_LOG_WEBHOOK_URL` in secret storage. Set the client, server and role settings you need from [`.env.example`](.env.example).
- Analytics. Use your own `GA4_MEASUREMENT_ID` and keep `GA4_USER_ID_SECRET` on the server.
- Assignment connector. Use a dedicated Worker with `LMS_MCP_PRIVATE_PROXY=true` and a separate `LMS_MCP_DOWNLOAD_KEY`. Match the key in Flask, set the Worker URL and allowed proxy hosts, and configure the allowed accounts before enabling it. Leave the operator probe disabled on self-hosted installations.

## Check your changes

From the repo root, run the tests that cover your change. These are useful starting points for proxy changes.

```sh
python -m pytest tests/test_proxy_transport.py
node --test tests/js/*.test.cjs
```

For a hosted setup, check the homepage first. Then sign in with your authorized university account and verify the dashboard, a single download, a ZIP download, and an upload to a designated test assignment. Test optional integrations separately before enabling them for other people.
