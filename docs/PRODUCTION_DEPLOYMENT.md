# Production deployment checklist

The active GitHub Actions workflows deploy the React build to
`ff-ranking-visualizer-web` and the Flask API to `ff-ranking-visualizer` when
`main` receives a push. Azure Functions are deployed separately.

## Frontend Web App: `ff-ranking-visualizer-web`

| Setting | Required value |
| --- | --- |
| `REACT_APP_API_BASE_URL` | `https://ff-ranking-visualizer.azurewebsites.net` |

`REACT_APP_*` values are compiled into the static JavaScript by `npm run build`.
They are public browser-visible values, never a place for secrets. The tracked
[frontend/.env.production](../frontend/.env.production) and the frontend GitHub
workflow both use the production backend URL.

Before pushing, verify the repository secret named
`FRONTEEND_AZURE_TENANT_ID` exists exactly with that spelling. The active
frontend workflow uses this pre-existing misspelled secret identifier.

Verified runtime: Node 20 LTS, Always On enabled, HTTPS only enabled, and
`pm2 serve /home/site/wwwroot --no-daemon --spa` is configured.

## Backend Web App: `ff-ranking-visualizer`

Required App Service settings:

| Setting | Purpose |
| --- | --- |
| `AZURE_STORAGE_CONNECTION_STRING` | Reads current boards, player data, and JSON blobs from `fantasyjsons`. |
| `AZURE_REDIS_CONNECTIONSTRING` | Recommendation and route cache. |
| `FRONTEND_URL` | Comma-separated CORS allowlist. |
| `BACKEND_URL` | Canonical backend URL. |
| `AZURE_FUNCTIONS_ENVIRONMENT` | Must be `Production`; prevents local settings loading. |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | Enables Python dependency installation in App Service. |

The deployed CORS allowlist currently contains:

```text
https://ffvisualizer.com,https://www.ffvisualizer.com,https://ff-ranking-visualizer-web.azurewebsites.net
```

Verified runtime: Python 3.11, Always On enabled, HTTPS only enabled, Azure
Cache for Redis (`rediss`) configured, and the `fantasydata` storage account
configured. Keep `USE_FIXTURE_BLOBS` absent or false: fixture blobs are local
and deliberately ignored by Git.

The backend workflow uses OIDC and needs `id-token: write`; the workflow now
sets that permission explicitly. [backend/requirements.txt](../backend/requirements.txt)
now declares `gunicorn`, avoiding reliance on a platform-global package.

After the backend workflow deploys this pending commit, configure the App
Service health-check path as `/health`. It returns `{"status":"ok"}` without
calling Redis or Azure Blob Storage.

## Azure Function App: `fantasydatascraperv2`

This is separate from the two Web Apps and requires its existing
`AZURE_STORAGE_CONNECTION_STRING`, `AzureWebJobsStorage`, Functions v4, and
Python worker settings. The active Function jobs monitor source updates and
refresh ADP. Weekly DraftSheets/ElBoberto Excel regeneration remains local.

The repository's Functions GitHub workflow is currently commented out, so a
commit does **not** deploy Functions. Continue using the explicit local Function
deployment after Function source changes, or restore a reviewed CI workflow.

## Before pushing `main`

1. Confirm each backend setting above exists in App Service Configuration.
2. Confirm both Web Apps have HTTPS only enabled.
3. Confirm [frontend/.env.production](../frontend/.env.production) still points
   to the production backend URL.
4. Push and wait for the **frontend** and **backend** GitHub Actions workflows
   to finish successfully.
5. Verify `https://ff-ranking-visualizer.azurewebsites.net/health` returns 200
   after the backend deploy, then test both frontend domains and a Draft Help
   request for a CORS error.

## Credential and artifact hygiene

- [azure-functions/local.settings.example.json](../azure-functions/local.settings.example.json)
  is safe to copy locally. The real `local.settings.json` is ignored.
- Local `.env`, `.env.local`, and `*.local` environment files are ignored.
  [frontend/.env.development.example](../frontend/.env.development.example) is
  the safe local template.
- Deployment ZIPs, Azure Functions `.python_packages`, Playwright browser
  downloads, Excel lock files, local fixture blobs, and refresh scratch output
  are ignored.
- The Azure Functions `.funcignore` is deliberately tracked so the deployment
  exclusion rules remain reproducible.
- A local development log previously exposed an Azure Storage connection string.
  The log was deleted, but the storage account key must still be rotated. Update
  the new connection string in the backend Web App, Function App, and ignored
  local settings together.
- Existing external workbook fixtures under `tests/fixtures/drafthelp/` are
  already tracked. New ignore rules do not untrack them. Confirm provider terms
  before removing them with `git rm --cached`.
