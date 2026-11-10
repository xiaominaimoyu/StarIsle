# StarIsle repository instructions

## Repository shape

StarIsle is a pnpm workspace with an Astro 5 frontend, a FastAPI private API, shared TypeScript contracts, and local infrastructure:

- `apps/web`: Astro pages and React islands. Public content, the envelope/login entry, and the private console are built here.
- `apps/api`: FastAPI `/api/v1` service, SQLite schema, local object storage, authentication, mail ingestion, retrieval/RAG endpoints, and maintenance services.
- `apps/api/app/worker_runner.py`: background durable jobs, document indexing, retention, and maintenance work. API and worker must use the same configuration and data root.
- `packages/contracts`: TypeScript request/response contracts consumed by the web app and built before the web app.
- `infra/compose`: Caddy and Compose base plus local, production, and optional CPU-model overlays.
- `scripts`: release/deployment gates, browser and backend acceptance harnesses, email worker, backup/restore, and RAG evaluation utilities.

The normal private data flow is browser same-origin `/api/v1` -> Cookie/CSRF checks -> FastAPI -> SQLite/object storage. Documents are queued for the worker; retrieval and chat may call the configured embedding/model services. The public Cloudflare Pages build is a separate static path: it builds the web app with `PUBLIC_PRIVATE_AREA_ENABLED=false`, adds Pages redirects/headers, and does not deploy the API, worker, SQLite, object storage, or model service.

## Build, test, and lint

Use Node.js 20+, pnpm 9 (the repository pins pnpm 9.15.4), and Python 3.12+; run commands from the repository root unless noted.

```powershell
pnpm install
pnpm build                         # contracts build, then Astro build
pnpm lint                          # recursive workspace lint
pnpm test                          # email-worker test, then workspace tests
pnpm build:release                 # PUBLIC_SITE_URL + static release gate
pnpm build:pages                   # public-only Cloudflare Pages artifact
```

Focused JavaScript checks:

```powershell
pnpm test:email-worker
pnpm --filter @star-isle/contracts test
pnpm --filter @star-isle/contracts exec vitest run path\to\test.ts -t "test name"
pnpm --filter @star-isle/web test
pnpm --filter @star-isle/web exec vitest run path\to\test.ts -t "test name"
pnpm --filter @star-isle/web lint       # astro check
```

For the API, use the existing virtual environment when available:

```powershell
Set-Location .\apps\api
& .\.venv\Scripts\python.exe -B -m pytest tests
& .\.venv\Scripts\python.exe -B -m pytest tests\test_api.py::test_name
& .\.venv\Scripts\python.exe -B -m tests.e2e_smoke
```

The API tests create isolated temporary data by default. Do not point tests at the repository `data/` directory or an existing runtime unless a test explicitly requires a validated external temporary root. The release and deployment checks are also available directly as `python -B scripts/release_artifact_gate.py` and `python -B scripts/deployment_preflight.py`.

For local development, start the API and worker from `apps/api` with the same environment, then run `pnpm dev` at the root. Astro proxies `/api/*` to `http://127.0.0.1:8000`; the browser should continue to use same-origin `/api/v1`.

## Important conventions

- Preserve the Astro/React/TypeScript frontend direction, existing routes, visual language, and the shared contracts. Make API or schema changes only when the task explicitly requires them.
- Authentication is same-origin Cookie-based JWT with CSRF protection. Keep tokens out of browser storage; do not add automatic replay of write requests. Refresh is used only on the established 401/session paths.
- API and worker configuration is coupled: use the same data root, retention settings, and embedding dimension. Do not silently fall back to the repository `data/` directory.
- Keep operational logs as the existing redacted, single-line JSONL events. Never log cookies, JWTs, HMAC keys, request bodies, mail/document contents, object keys, filesystem paths, hashes, or model identifiers.
- Treat `infra/compose/compose.yml` as an internal base. Combine it with exactly the appropriate local, production, or CPU overlay; run the offline `docker compose ... config --quiet` preflight before starting containers. Bind local services to loopback.
- Public Pages builds and private local/Compose builds are different modes. Do not infer that a successful static Pages build proves API, worker, Caddy, storage, model, DNS, TLS, or production behavior.
- Use isolated temporary data and evidence for browser/backend acceptance. Do not read or persist real `.env` values, private runtime data, model weights, AI session caches, or historical private browser evidence.
- Before changing code, reproduce and locate the root cause, then make the smallest compatible change. Add regression coverage only for the behavior or root cause being changed.
- Do not invent personal content, contacts, domains, audio licenses, models, or corpus data. Keep planned work, local checks, deployment results, and production acceptance evidence distinct.
- Do not overwrite unrelated work, upgrade dependencies opportunistically, change `.gitignore`, or stage/commit/push without explicit user authorization. Review exact files and sensitive-content boundaries before any authorized Git operation.

## Documentation sources

`README.md` is the authoritative local development, verification, release, Compose, and backup/restore guide. `ARCHITECTURE.md` describes system boundaries and deployment modes; `DESIGN.md` defines the visual and interaction constraints; `RULES.md` records repository-specific implementation and safety rules. Check the latest `PROGRESS.md` and linked reports before relying on historical validation claims.
