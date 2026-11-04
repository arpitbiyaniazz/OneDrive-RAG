# Deploy it live for free

Two free ways to put the app on the internet. Both keep your data in your own database and use your own Mistral
key and Google OAuth client.

| | A. Oracle Cloud server | B. Render + Neon |
| --- | --- | --- |
| Runs | the whole `docker-compose.prod.yml` stack on one free VM | the API and the UI on Render, the database on Neon |
| Server size | Arm VM, 2 OCPUs and 12 GB of memory | 0.1 CPU and 512 MB (files up to 20 MB, indexed one at a time) |
| Always on | yes | no: the API sleeps after 15 minutes without visitors and takes about a minute to wake |
| Database | your own Postgres on the VM's disk (about 50 GB by default, up to 200 GB free) | 1 GB (roughly a few thousand documents) |
| Indexing and syncs you start | run to completion | pause while the API sleeps and continue when it wakes |
| Card needed | Oracle asks for one at sign-up (Always Free resources are not charged) | no |
| Setup time | about an hour | about 30 minutes |
| Files | `docker-compose.prod.yml`, `deploy/caddy/Caddyfile`, `deploy/server-setup.sh`, `deploy/.env.production.example` | `render.yaml` |

There is no automatic schedule in either option: press **Sync** in the app to pick up new and changed files.
Free tiers change. These limits were checked on 2026-11-02 against the providers' own pages
([Oracle](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm),
[Render](https://render.com/docs/free), [Neon](https://neon.com/faqs/free-plan-limits-and-quotas)).

## 0. Before you start

1. **Commit and push the deployment files** (`DEPLOY.md`, `render.yaml`, `docker-compose.prod.yml`, `deploy/`)
   and deploy the branch that has them and the production code (merge it into `main`, or use that branch below).
2. **Pick your address** now, because Google needs it:
   - Option A: reserve a free name at [duckdns.org](https://www.duckdns.org) (sign in, add a subdomain such as
     `yourname`): your address is `https://yourname.duckdns.org`. You point it at the server in step A3.
   - Option B: in `render.yaml`, replace `yourname` (three places: both service names and the CSP line) with
     something unique, such as your GitHub username, and commit. Your UI address is
     `https://rag-web-yourname.onrender.com` and the API's is `https://rag-api-yourname.onrender.com`.

## 1. Google sign-in (both options)

In the [Google Cloud console](https://console.cloud.google.com), project of your OAuth client (no OAuth client
yet? first follow [docs/GOOGLE_SETUP.md](docs/GOOGLE_SETUP.md) sections 1 to 3: enable the Google Drive API, set up
the consent screen with the `drive.readonly` scope, create a *Web application* client):

1. **Google Auth Platform → Branding → Authorized domains → Add domain**: your full host name
   (`yourname.duckdns.org` or `rag-web-yourname.onrender.com`). `duckdns.org` and `onrender.com` are public
   suffixes, so the full host is the domain. Save.
2. **Clients → your Web application client**:
   - Authorized JavaScript origins: `https://<your address>`
   - Authorized redirect URIs: `https://<your address>/auth/google/callback` (exactly, no trailing slash)
3. **Audience → Publishing status → Publish app** (status *In production*).
   - Why: while the app is in *Testing*, Google's refresh tokens expire after 7 days, so Drive access and sync
     stop working every week ([Google](https://developers.google.com/identity/protocols/oauth2)).
   - Until Google verifies the app, users see a "Google hasn't verified this app" screen (choose
     *Advanced → Go to ...*), and at most **100 new users** can ever sign in
     ([Google](https://support.google.com/cloud/answer/7454865)). Verification for `drive.readonly` (a restricted
     scope) is needed only for a public launch.
   - **Once published, any Google account can start the sign-in.** The 100-user cap is not an allowlist, so set
     `ALLOWED_EMAILS` (the accounts or `@domains` that may use the app) in the steps below. Accounts not on the
     list are refused, and removing one ends its sessions.
   - With a Google Workspace organization, user type **Internal** limits sign-in to that organization without
     verification.
4. In the Google Drive of each user, create a folder named **`OneDrive-RAG`** directly in *My Drive* and put the
   documents in it (the name is `GOOGLE_DRIVE_ROOT_FOLDER`).

## 2. Option A: Oracle Cloud Always Free server (always on)

What you get for free: an Arm VM with up to **2 OCPUs and 12 GB of memory**, 200 GB of disk in total and 10 TB of
outbound traffic a month. Caddy gets a free Let's Encrypt certificate and renews it by itself.

1. **Create the VM.** Oracle Cloud console → *Compute → Instances → Create instance*:
   - Image: **Canonical Ubuntu 24.04** (not *Minimal*).
   - Shape: **VM.Standard.A1.Flex** with 2 OCPUs and 12 GB of memory.
   - Boot volume: **Specify a custom boot volume size**, up to 200 GB (the default is about 50 GB; boot and
     block volumes share the 200 GB).
   - Add your SSH public key, create the instance, and note its **public IP address**. (If Oracle reports no
     capacity, try another availability domain or try again later.)
2. **Open ports 80 and 443 in Oracle's firewall.** *Networking → Virtual cloud networks → your VCN → Subnet →
   Security list → Add ingress rules*: source `0.0.0.0/0`, IP protocol TCP, destination ports `80` and `443`.
3. **Point your DuckDNS name at the VM**: on duckdns.org, set the subdomain's IP to the VM's public IP.
4. **Set up the server.** SSH in (`ssh ubuntu@<public IP>`) and run, with your branch and allowed accounts:
   ```bash
   sudo apt-get update && sudo apt-get install -y git nano
   git clone -b <branch> <your repository URL> rag && cd rag
   ./deploy/server-setup.sh yourname.duckdns.org you@gmail.com,colleague@gmail.com
   ```
   The script installs Docker, opens ports 80/443 in the VM's own firewall, and creates `.env` (readable only by
   you) with the domain, `ALLOWED_EMAILS` and new random secrets. For a private repository, clone with a deploy
   key or enter a GitHub token when git asks for the password.
5. **Add your keys** to `.env` (`nano .env`): `MISTRAL_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.
   Keep a private copy of the finished `.env` off the server: its `TOKEN_ENCRYPTION_KEY` is needed to use a
   restored database.
6. **Start it** (log out and back in first, or prefix with `sudo`, so the docker group applies):
   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```
   The first start builds the images, creates the database and gets the certificate: give it a few minutes.
7. **Check it:**
   ```bash
   docker compose -f docker-compose.prod.yml ps                          # all services running, postgres/backend healthy
   curl https://yourname.duckdns.org/api/health/ready                     # {"status":"ready",...}
   docker compose -f docker-compose.prod.yml exec backend python -m app.cli doctor   # keys, models, ALLOWED_EMAILS
   ```
8. Open `https://yourname.duckdns.org`, sign in with Google, open **Manage documents**, select your folder,
   and index it.

**Keep in mind:** Oracle may reclaim an Always Free VM it considers idle: over 7 days, CPU use (95th
percentile), network use and memory use all below 20%. A quietly used personal app can look idle. Upgrading the
account to *Pay As You Go* is commonly reported to prevent this while Always Free resources stay free, but Oracle
does not state it on its page, so check before relying on it. Keep backups (below).

**Day-to-day commands** (from the `rag` folder; `rag` is the default database user and name):

| Task | Command |
| --- | --- |
| Update the code and pick up security fixes in the images (run it monthly) | `git pull && docker compose -f docker-compose.prod.yml pull --ignore-buildable && docker compose -f docker-compose.prod.yml build --pull && docker compose -f docker-compose.prod.yml up -d && docker compose -f docker-compose.prod.yml restart caddy` |
| Follow the API logs | `docker compose -f docker-compose.prod.yml logs -f backend` |
| Back up the database | `mkdir -p ~/rag-backups && docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U rag -Fc rag > ~/rag-backups/rag-$(date +%F).dump` |
| Restore a backup (stops the API meanwhile) | `docker compose -f docker-compose.prod.yml stop backend && docker compose -f docker-compose.prod.yml exec -T postgres pg_restore -U rag -d rag --clean --if-exists --single-transaction < ~/rag-backups/rag-YYYY-MM-DD.dump && docker compose -f docker-compose.prod.yml start backend` |
| Stop everything | `docker compose -f docker-compose.prod.yml down` (data stays in the volumes) |

Nightly backups, keeping two weeks: `mkdir -p ~/rag-backups && chmod 700 ~/rag-backups`, then `crontab -e` and add
(change the path if the repository is elsewhere):

```text
0 3 * * * cd /home/ubuntu/rag && docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U rag -Fc rag > /home/ubuntu/rag-backups/rag-$(date +\%F).dump.tmp && mv /home/ubuntu/rag-backups/rag-$(date +\%F).dump.tmp /home/ubuntu/rag-backups/rag-$(date +\%F).dump; find /home/ubuntu/rag-backups -name 'rag-*.dump*' -mtime +14 -delete
```

Copy backups off the server from time to time (for example with `scp`).

## 3. Option B: Render + Neon (no card, sleeps when idle)

1. **Create the database on [Neon](https://neon.com)** (free plan): new project, region **AWS Asia Pacific
   (Singapore)** to match `render.yaml` (or change `region` there). Open **Connect**, turn **connection
   pooling off** (the pooled endpoint breaks the asyncpg driver this app uses), and copy the connection string.
   Rewrite it for asyncpg:
   ```text
   Neon gives:  postgresql://USER:PASSWORD@ep-xxxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
   Use:         postgresql+asyncpg://USER:PASSWORD@ep-xxxx.ap-southeast-1.aws.neon.tech/neondb?ssl=verify-full
   ```
   (`sslmode` and `channel_binding` make asyncpg fail with "unexpected keyword argument"; `verify-full` checks
   Neon's certificate using the `PGSSLROOTCERT` set in `render.yaml`.) The app creates its tables and the
   pgvector extension by itself on first start.
2. **Push the repository to GitHub** with your edited `render.yaml` (step 0).
3. **Create the Blueprint.** [Render dashboard](https://dashboard.render.com) → **New → Blueprint** → pick the
   repository and the branch. Render reads `render.yaml` and creates `rag-api-yourname` (Docker web service,
   free) and `rag-web-yourname` (static site). It asks once for the values marked `sync: false`:

   | Variable | Value |
   | --- | --- |
   | `DATABASE_URL` | the rewritten Neon string from step 1 |
   | `ALLOWED_EMAILS` | the accounts that may sign in, e.g. `you@gmail.com,colleague@gmail.com` |
   | `MISTRAL_API_KEY` | your Mistral key |
   | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | from your Google OAuth client |
   | `FRONTEND_URL`, `CORS_ORIGINS` | `https://rag-web-yourname.onrender.com` |
   | `GOOGLE_REDIRECT_URI` | `https://rag-web-yourname.onrender.com/auth/google/callback` |
   | `VITE_API_BASE_URL` (on the UI) | `https://rag-api-yourname.onrender.com/api` |

   `SECRET_KEY` and `TOKEN_ENCRYPTION_KEY` are generated by Render.
4. **Check the real addresses** on each service's page. If Render still added a suffix (the name was taken):
   - on the API, set `FRONTEND_URL`, `CORS_ORIGINS` and `GOOGLE_REDIRECT_URI` to the UI's real address;
   - on the UI, set `VITE_API_BASE_URL` to the API's real address and apply it with **Save, rebuild, and
     deploy** (the address is built into the UI; *Save and deploy* would reuse the old build);
   - in `render.yaml`, change the CSP line's API address, commit and push;
   - in Google Cloud, add the real UI host under *Authorized domains* and use the real addresses in the client,
     and remove the ones you guessed.
5. **Check it:** `https://rag-api-yourname.onrender.com/api/health/ready` shows `"status":"ready"`. The free plan
   has no shell, so instead of `doctor` open the API's **Logs**: a line with "is not usable" names a key or model
   problem. Then open the UI, sign in, select your folder in **Manage documents** and index it.

**Keep in mind:**
- After 15 minutes without visitors the API sleeps. The next visit waits about a minute, and indexing or syncs
  you started pause while it sleeps and continue when it wakes. Render gives 750 free instance hours a month.
- Neon's free plan has 1 GB of storage and 100 CU-hours of compute a month: about 400 hours at the smallest
  size, fewer if it scales up while indexing. The database stays on whenever the API is awake (the job worker
  checks it every few seconds), so the hours last only if the API sleeps most of the time. Do not keep the API
  awake with an uptime pinger. When the hours run out, Neon suspends the database until the next month.
- Do not use Render's own free Postgres: it expires after 30 days and is then deleted.

## 4. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Google shows `redirect_uri_mismatch` | `GOOGLE_REDIRECT_URI` must equal an authorized redirect URI in the Google client exactly. |
| "This Google account is not allowed to use this app" | Add the account (or its `@domain`) to `ALLOWED_EMAILS` and restart the API (option B: save the variable). |
| Drive stops working after about a week | The OAuth app is still in *Testing*: publish it (section 1), then sign in again. |
| "Google hasn't verified this app" | Expected for an unverified app in production: *Advanced → Go to ...*. |
| API keeps restarting, log says `Invalid production configuration` | A required value is missing or too short: `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY` (32+ characters), `MISTRAL_API_KEY`. |
| `connect() got an unexpected keyword argument 'sslmode'` | Rewrite `DATABASE_URL` with `?ssl=verify-full` (option B, step 1). |
| `root certificate file ... does not exist` | `PGSSLROOTCERT` is missing: it is set in `render.yaml` (`/etc/ssl/certs/ca-certificates.crt`). |
| `prepared statement ... does not exist` | `DATABASE_URL` uses Neon's pooled (`-pooler`) host: use the direct one. |
| Answers fail at once | `doctor` (option A) or the API log at startup (option B) names the chat model: keep `MISTRAL_MODEL=ministral-8b-2512`, or pick a model your key has quota for. |
| Option A: the browser shows a certificate error | DNS does not point at the VM yet, or port 80/443 is closed (Oracle security list, step 2). Check `docker compose -f docker-compose.prod.yml logs caddy`. |
| Option A: `502 Bad Gateway` | The API is starting or migrating: wait a minute. If it persists after an update, run `docker compose -f docker-compose.prod.yml restart frontend` and check `logs backend`. |
| Option B: the first page load takes about a minute | The free API was asleep. |
