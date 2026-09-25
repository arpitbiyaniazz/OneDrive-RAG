# Microsoft Azure & OneDrive Setup Guide

This guide details how to register an application in the Microsoft Entra ID (Azure Active Directory) portal to enable real OAuth 2.0 authentication and Microsoft Graph API synchronization with OneDrive.

---

## 1. Prerequisites
- A Microsoft 365 or Azure account with permissions to register applications.
- Access to OneDrive (Personal, OneDrive for Business, or SharePoint Online).

---

## 2. Register Application in Microsoft Entra ID

1. Sign in to the [Azure Portal](https://portal.azure.com/) or [Microsoft Entra admin center](https://entra.microsoft.com/).
2. Navigate to **Identity** > **Applications** > **App registrations**.
3. Click **+ New registration**.
4. Configure the application:
   - **Name**: `OneDrive RAG Platform`
   - **Supported account types**:
     - *Accounts in any organizational directory and personal Microsoft accounts* (recommended for multi-tenant or hybrid use)
     - OR *Accounts in this organizational directory only* (for internal enterprise tenant)
   - **Redirect URI (optional)**:
     - Platform: **Web**
     - Redirect URI: `http://localhost:8000/api/auth/callback` (or your production domain e.g. `https://api.yourdomain.com/api/auth/callback`)
5. Click **Register**.

---

## 3. Configure API Permissions (Microsoft Graph)

1. In your registered app, navigate to **API permissions** in the sidebar.
2. Click **+ Add a permission** > select **Microsoft Graph** > **Delegated permissions**.
3. Add the following permissions:
   - `User.Read`: Sign in and read user profile.
   - `Files.Read.All`: Read all files that the user can access on OneDrive and SharePoint.
   - `offline_access`: Maintain access to data even when the user is not actively present (enables refresh tokens).
4. Click **Add permissions**.
5. (Optional, Enterprise only): Click **Grant admin consent for [Tenant Name]** if required by your tenant policy.

---

## 4. Generate Client Secret

1. In the sidebar, select **Certificates & secrets** > **Client secrets** tab.
2. Click **+ New client secret**.
3. Enter a description (e.g. `OneDrive RAG Backend Secret`) and select an expiration period (e.g. 180 days).
4. Click **Add**.
5. **CRITICAL**: Copy the **Value** immediately. (Azure will never display this value again after you leave the page).

---

## 5. Configure Backend `.env`

Copy the credentials from Azure into your `.env` file:

```env
# Microsoft OAuth & Graph API
MICROSOFT_CLIENT_ID="<Application (client) ID from Overview tab>"
MICROSOFT_CLIENT_SECRET="<Client Secret Value from Step 4>"
MICROSOFT_TENANT_ID="common" # or your specific tenant ID
MICROSOFT_REDIRECT_URI="http://localhost:8000/api/auth/callback"

# Set to false to switch from local mock sandbox to live Microsoft Graph API
DEV_MOCK_ONEDRIVE=false
```

---

## 6. Verification Checklist
- [x] Backend responds with redirect URL on `GET /api/auth/login`.
- [x] Microsoft login prompt asks for user consent to read OneDrive files.
- [x] Successful callback receives authorization code, exchanges it for access/refresh tokens, and redirects to frontend with session JWT.
- [x] `GET /api/onedrive/tree` lists real user folders and files from OneDrive root.
