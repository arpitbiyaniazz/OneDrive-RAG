# Google Cloud Console & Google Drive Setup Guide

This guide details how to configure a Google Cloud Platform (GCP) project to enable real OAuth 2.0 authentication, Google Drive API v3 file and folder navigation, native Google Workspace document export conversion, and webhook push synchronization.

---

## 1. Prerequisites

- A [Google Cloud Console](https://console.cloud.google.com/) account with permissions to create projects and enable APIs.
- A Google account with personal Google Drive files or access to a Google Workspace Shared Drive.
- The OneDrive/Multi-Cloud RAG application running locally or on a server.

---

## 2. Create or Select a Google Cloud Project

1. Log in to the [Google Cloud Console](https://console.cloud.google.com/).
2. In the top navigation bar, click the project dropdown and select **New Project**.
3. Configure your project:
   - **Project Name**: `Enterprise-RAG-MultiCloud` (or your preferred name)
   - **Organization**: Select your organization or leave as *No organization*
4. Click **Create** and ensure your newly created project is selected in the top bar.

---

## 3. Enable Required Google APIs

1. In the left-hand navigation menu, go to **APIs & Services** > **Library**.
2. Search for and enable the following APIs:
   - **Google Drive API** (`drive.googleapis.com`) — required for folder traversal, file metadata, binary downloads, native Google Workspace export, and delta tracking.
   - **Google People API** (or standard OpenID Connect profile APIs) — enables retrieving the user's email address and profile name during authentication.
3. Click **Enable** for each API.

---

## 4. Configure OAuth Consent Screen

Before generating client credentials, you must configure the OAuth consent screen that users see when signing in:

1. Navigate to **APIs & Services** > **OAuth consent screen**.
2. Select your **User Type**:
   - **Internal**: Recommended if using Google Workspace and you only want members of your enterprise organization to sign in. (Does not require external Google verification).
   - **External**: Required if allowing personal Gmail accounts or users from outside your Google Workspace organization.
3. Click **Create**.
4. Fill in **App Information**:
   - **App name**: `Enterprise RAG Assistant`
   - **User support email**: Select your email address.
   - **App logo**: (Optional).
   - **Developer contact information**: Enter your email address.
5. Click **Save and Continue**.
6. **Configure Scopes**:
   Click **Add or Remove Scopes** and select:
   - `openid` (Associate you with your personal info on Google)
   - `.../auth/userinfo.email` (See your primary Google Account email address)
   - `.../auth/userinfo.profile` (See your personal info, including any personal info you've made publicly available)
   - `https://www.googleapis.com/auth/drive.readonly` (See and download all your Google Drive files)
   > [!NOTE]
   > `drive.readonly` gives the RAG platform permission to inspect folder hierarchies and read document contents for vector embedding without granting write or delete access.
7. Click **Save and Continue**.
8. **Test Users** (Only if User Type is External & in "Testing" mode):
   - Add the Gmail addresses of the users who will be testing the application.
   - Click **Save and Continue** and finish the wizard.

---

## 5. Generate OAuth 2.0 Client Credentials

1. Navigate to **APIs & Services** > **Credentials**.
2. Click **+ Create Credentials** at the top and select **OAuth client ID**.
3. Select **Application type**: **Web application**.
4. Set **Name**: `Enterprise RAG Web Client`.
5. **Authorized JavaScript origins**:
   - Add: `http://localhost:5173` (Vite frontend dev server)
   - Add: `http://localhost:8000` (FastAPI backend server)
   - *(Add production domain when deploying, e.g. `https://rag.yourdomain.com`)*
6. **Authorized redirect URIs**:
   - Add: `http://localhost:5173/auth/google/callback` (Frontend OAuth code exchange route)
   - Add: `http://localhost:8000/api/auth/google/callback` (Backend API direct callback)
   - *(Add production domain callback when deploying, e.g. `https://rag.yourdomain.com/auth/google/callback`)*
7. Click **Create**.
8. A modal will appear showing your **Client ID** and **Client Secret**. Copy both values securely.

---

## 6. Configure Backend `.env`

Update your `.env` file in the project root or `backend/.env`:

```env
# ==========================================
# Google Drive & OAuth 2.0 Credentials
# ==========================================
GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET="your-client-secret"
GOOGLE_REDIRECT_URI="http://localhost:5173/auth/google/callback"

# Switch DEV_MOCK_GDRIVE to false to connect to real Google Cloud APIs
DEV_MOCK_GDRIVE=false

# Webhook verification secret token for push notifications
GOOGLE_WEBHOOK_SECRET="gdrive-rag-webhook-secret-token"
```

> [!TIP]
> **Mock Sandbox Mode**: When `DEV_MOCK_GDRIVE=true`, the application uses an in-memory realistic Google Drive provider with pre-built folders, Google Docs, Sheets, Slides, and incident postmortems. This enables complete end-to-end development, testing, and UI demonstration without requiring live Google Cloud credentials.

---

## 7. Supported Google Drive Formats & Export Pipeline

The platform automatically handles both standard binaries and native Google Workspace cloud documents:

| Google Workspace Format | Google MIME Type | Export Target Format | Parser Pipeline |
| :--- | :--- | :--- | :--- |
| **Google Docs** | `application/vnd.google-apps.document` | `.docx` (OpenXML) | `DOCXParser` (Structure-aware) |
| **Google Sheets** | `application/vnd.google-apps.spreadsheet` | `.xlsx` (OpenXML) | `XLSXParser` (Tabular structure) |
| **Google Slides** | `application/vnd.google-apps.presentation` | `.pptx` (OpenXML) | `PPTXParser` (Slide & notes hierarchy) |
| **PDF Documents** | `application/pdf` | Raw Binary | `PDFParser` (PyMuPDF) |
| **Plain Text / Code** | `text/plain`, `text/markdown` | Raw UTF-8 | `TXTParser` |

Google Workspace files cannot be directly downloaded as raw binaries via the standard `/files/{id}?alt=media` endpoint; our connector automatically invokes Google Drive's `/files/{id}/export` endpoint with the appropriate OpenXML MIME type so structure and headings are fully preserved during chunking and vector indexing.

---

## 8. Webhook Push Notifications (Production)

To receive real-time updates when files are added, modified, or trashed in Google Drive without polling:

1. **Domain Verification**:
   - In Google Cloud Console, navigate to **APIs & Services** > **Domain verification**.
   - Add your production domain (e.g. `api.yourdomain.com`) and verify ownership via Google Search Console.
   > *Note: Google Drive push notification webhooks require an HTTPS endpoint with a valid SSL certificate. `localhost` is not supported by Google's push servers without a tunneling service such as ngrok or cloudflared.*
2. **Webhook Endpoint**:
   - Register your webhook at `https://api.yourdomain.com/api/webhooks/gdrive`.
   - The backend validates the `X-Goog-Channel-Token` header against your configured `GOOGLE_WEBHOOK_SECRET` and handles state changes (`sync`, `add`, `update`, `trash`).

---

## 9. Verification & Testing Checklist

- [ ] **Auth URL Generation**: `curl -s http://localhost:8000/api/auth/google/login | jq` returns a valid Google consent URL with `client_id`, `scope`, `access_type=offline`, and `prompt=consent`.
- [ ] **OAuth Consent**: Clicking **Sign in with Google** on the web login page redirects to Google's consent screen showing the requested scopes.
- [ ] **Token Exchange**: Upon granting consent, Google redirects to `/auth/google/callback`, where the backend securely exchanges the code for access/refresh tokens and stores them AES-256 encrypted in the PostgreSQL `oauth_accounts` table.
- [ ] **Cloud Drive Exploration**: In the frontend **Cloud Drive Explorer**, clicking the **Google Drive** tab renders the folder hierarchy and files with distinct Google Workspace icons.
- [ ] **Ingestion**: Clicking **Index** on a Google Doc, Sheet, Slide, or PDF processes the file through the parser pipeline, generating vector embeddings in pgvector.
- [ ] **Grounded Chat**: Submitting a query in the chat assistant returns answers with green `[G-Drive]` citation badges linking directly to the Google Drive document URLs.

---

## 10. Troubleshooting & FAQ

### 1. `redirect_uri_mismatch` Error (400)
- **Cause**: The redirect URI passed in the request does not exactly match the URIs listed in your Google Cloud Console OAuth 2.0 client.
- **Fix**: Check `GOOGLE_REDIRECT_URI` in `.env`. Ensure that `http://localhost:5173/auth/google/callback` is added under **Authorized redirect URIs** in GCP without trailing slashes.

### 2. `Access blocked: App has not completed the Google verification process`
- **Cause**: The OAuth consent screen is set to **External** in **Testing** status, and the signing-in Google account is not added as a test user.
- **Fix**: Go to **APIs & Services** > **OAuth consent screen** > **Test users**, click **+ Add users**, and enter your Google account email.

### 3. Missing Refresh Token
- **Cause**: Google only issues a `refresh_token` on the first authorization or when `prompt=consent` and `access_type=offline` are included in the authorization query parameters.
- **Fix**: The backend automatically includes `access_type=offline&prompt=consent`. If you previously authorized without a refresh token, revoke access in your [Google Account Permissions](https://myaccount.google.com/permissions) and log in again.

### 4. `403 File not exportable` on Google Workspace Files
- **Cause**: Attempting to use `/files/{id}?alt=media` on a Google Doc or Google Sheet instead of the `/files/{id}/export` endpoint.
- **Fix**: The built-in `GoogleDriveService.download_file_bytes` automatically routes Google Docs, Sheets, and Slides through `/files/{id}/export`. Ensure your document is not corrupted or restricted by Workspace enterprise DLP rules.
