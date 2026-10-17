# Microsoft OneDrive setup (Entra ID app registration)

The app signs users in with Microsoft and reads files from one top-level OneDrive folder
(`ONEDRIVE_ROOT_FOLDER`, default `OneDrive-RAG`). It never writes to the drive.

## 1. Register the application

1. Open the [Azure portal](https://portal.azure.com) → **Microsoft Entra ID** → **App registrations** → **New registration**.
2. **Supported account types**: choose who may sign in. For personal OneDrive accounts as well as work
   accounts pick *Accounts in any organizational directory and personal Microsoft accounts* and keep
   `MICROSOFT_TENANT_ID=common`. For a single company use your tenant id.
3. **Redirect URI**: platform **Web**, value `http://localhost:5173/auth/callback`
   (production: `https://your-domain/auth/callback`). This is the web app's route; the browser hands the
   code to the backend, which redeems it with the client secret and a PKCE verifier.

## 2. Client secret

**Certificates & secrets** → **New client secret**. Copy the value (not the id) into `MICROSOFT_CLIENT_SECRET`.
Secrets expire; set a reminder to rotate it.

## 3. API permissions (delegated)

| Permission | Why |
| --- | --- |
| `openid`, `profile`, `email`, `offline_access` | sign-in and refresh tokens |
| `User.Read` | display name and stable user id |
| `Files.Read` | read the signed-in user's own OneDrive files |

`Files.Read.All` is not needed: the app only reads the user's own root folder.

## 4. Configure the backend

```bash
MICROSOFT_CLIENT_ID=<application (client) id>
MICROSOFT_CLIENT_SECRET=<secret value>
MICROSOFT_TENANT_ID=common
MICROSOFT_REDIRECT_URI=http://localhost:5173/auth/callback
ONEDRIVE_ROOT_FOLDER=OneDrive-RAG
DEV_MOCK_ONEDRIVE=false
```

## 5. Prepare the drive

Create a folder named `OneDrive-RAG` at the top level of the OneDrive and put the documents to index in it
(subfolders are fine). The app does not create it because it only has read access.

## 6. Check

- **Sign in with Microsoft** on the login page → consent screen → back in the app.
- The drive browser lists the contents of `OneDrive-RAG`. "Create a folder named ..." means the folder is missing.
- If the browser asks to reconnect, the refresh token was revoked or expired: sign in again.

Accounts are matched by the Microsoft user id, never by e-mail address. A user who first signed in with
Google can add OneDrive from the drive browser (**Connect OneDrive**) while signed in.
