# Pipeline — Job Application Tracker

Pipeline is a Python (Flask) application with a responsive HTML/CSS/JavaScript
interface. Supabase provides verified email/password authentication and durable
PostgreSQL storage; Vercel runs the Python app.

## Deploy to Vercel

### 1. Create the database and authentication project

1. Create a Supabase project and open **SQL Editor**.
2. Run [`supabase/schema.sql`](./supabase/schema.sql) to create the tables,
   status history, indexes, and per-user Row Level Security (RLS) policies.
3. In **Authentication → URL Configuration**, set **Site URL** to your final
   HTTPS Vercel domain. Add that domain (including your preview domains, if
   needed) to **Redirect URLs**.
4. In **Authentication → Providers → Email**, require email confirmation.
   Configure the confirmation and password-recovery email templates to send
   users back to the app with the verification token:

   - Confirm signup: `{{ .SiteURL }}/?token_hash={{ .TokenHash }}&type=signup`
   - Reset password: `{{ .SiteURL }}/?token_hash={{ .TokenHash }}&type=recovery`

   Configure a production SMTP provider in Supabase before inviting real users;
   the default mail service has strict sending limits.
5. In **Project Settings → API**, copy the project URL and the **publishable**
   (or legacy `anon`) key. **Never use or expose the `service_role` key.**

### 2. Configure and deploy the app

Push this folder to your own GitHub repository and import it into Vercel. Add
these environment variables in the Vercel project settings for every environment
you intend to use:

| Variable | Value |
| --- | --- |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_ANON_KEY` | The publishable/anon key, never the service-role key |
| `SECRET_KEY` | A new random secret of at least 32 characters |
| `APP_ORIGIN` | The exact HTTPS origin, e.g. `https://your-app.vercel.app` (no trailing slash) |

Generate a signing secret locally with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Redeploy after setting the variables. Open `/healthz`; `storage_configured` must
be `true`. Sign up with an email you can access, complete the email
verification, sign in, and add an application to confirm the full flow.
The deployment ignore rules deliberately exclude the local SQLite database,
Python bytecode, and test files from Vercel uploads.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>. To use login and persistence locally, set the four
environment variables above, using your local app origin
`http://127.0.0.1:5000`. Never commit `.env` files or real credentials.

## Included features

- Email verification, sign-in, sign-out, verification resend, and password reset
- Encrypted, `HttpOnly`, `SameSite=Lax` session cookies; no auth tokens in
  browser storage
- Private, per-account applications with database-enforced ownership and RLS
- Applied / Interview / Offer / Rejected kanban board, drag-and-drop status
  changes, search, filters, editing, and deletion
- Status history, follow-up reminders for applications untouched for seven
  days, overview stats, and CSV export
- Input limits, same-origin checks for writes, safe text rendering, CSV formula
  protection, security headers, bounded upstream requests, and generic errors
- `/healthz` deployment check and serverless-compatible request handling

## Data, privacy, and operations

Vercel's local filesystem is ephemeral; this app stores production records in
Supabase PostgreSQL, not in the local SQLite file. The existing `tracker.db` is
left untouched for local reference and is ignored by Git. It is **not**
automatically uploaded or migrated; review and migrate any personal records
yourself only after verifying ownership and backups.

The backend uses only the Supabase publishable/anon key and the signed-in user's
access token. RLS policies and explicit owner filters isolate each user's
applications; never disable RLS or add a service-role key to Vercel. Set
`APP_ORIGIN` to the exact deployed origin, keep Supabase email confirmation
enabled, configure SMTP, use separate secrets for Preview and Production, and
review Supabase backups/retention and your privacy obligations before inviting
users. This is a hardened starter, not a substitute for a security assessment
or a legal/privacy review.

Run the local checks with:

```powershell
python -m unittest discover -s tests -v
```
