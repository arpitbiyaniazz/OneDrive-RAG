# Google Drive setup (Google Cloud OAuth client)

The app signs users in with Google and reads files from one folder in *My Drive*
(`GOOGLE_DRIVE_ROOT_FOLDER`, default `OneDrive-RAG`). Google Docs, Sheets and Slides are exported to
DOCX, XLSX and PPTX before indexing (PDF when an export exceeds Google's 10 MB limit).

## 1. Project and API

1. In the [Google Cloud console](https://console.cloud.google.com) create (or pick) a project.
2. **APIs & Services → Library** → enable **Google Drive API**.

## 2. OAuth consent screen

1. **APIs & Services → OAuth consent screen**: user type *External* (or *Internal* for a Workspace domain).
2. Scopes:
   - `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`
   - `https://www.googleapis.com/auth/drive.readonly`
3. While the app is in **Testing** status only listed **test users** can sign in, and Google expires their
   refresh tokens after **7 days**. Users then see "reconnect your drive". Publish the app (verification is
   required for `drive.readonly`, a restricted scope) to remove both limits.

## 3. OAuth client

**APIs & Services → Credentials → Create credentials → OAuth client ID**, type **Web application**.

- Authorized JavaScript origins: `http://localhost:5173` (production: `https://your-domain`)
- Authorized redirect URIs: `http://localhost:5173/auth/google/callback` (production: `https://your-domain/auth/google/callback`)

## 4. Configure the backend

```bash
GOOGLE_CLIENT_ID=<client id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<client secret>
GOOGLE_REDIRECT_URI=http://localhost:5173/auth/google/callback
GOOGLE_DRIVE_ROOT_FOLDER=OneDrive-RAG
DEV_MOCK_GDRIVE=false
```

## 5. Prepare the drive

Create a folder named `OneDrive-RAG` directly in *My Drive* and put the documents to index in it
(subfolders are fine). Shortcuts to files are followed; shortcuts to folders are skipped so nothing outside
the folder can be reached.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `redirect_uri_mismatch` | `GOOGLE_REDIRECT_URI` must match an authorized redirect URI exactly (no trailing slash). |
| `access_denied` / "app not verified" | add the account as a test user, or publish the app. |
| Drive browser says "reconnect" | refresh token expired (Testing status) or access was revoked; sign in again. |
| "Create a folder named ..." | the root folder is missing from *My Drive* or is named differently. |

Stored OAuth tokens are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) using `TOKEN_ENCRYPTION_KEY`.
