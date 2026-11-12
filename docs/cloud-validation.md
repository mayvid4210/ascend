# Cloud-hosted container validation (Phase 6A)

## Current status

The workflow is implemented and its context-preparation code has been tested locally.
Docker CLI is unavailable on the local PATH. No Docker images have been built/run
here, and no GitHub Actions execution has been triggered or verified by this session.
**Cloud container validation is pending until both matrix jobs complete successfully.**

## Trigger the workflow

After you commit and push these changes to `main`, the workflow runs automatically
when the push changes backend files, either trusted sample, the preparation script,
or `.github/workflows/validate-migration.yml`. Documentation-only or frontend-only
changes do not trigger it.

For a manual run after the workflow is on GitHub:

1. Open [Ascend Actions](https://github.com/mayvid4210/ascend/actions).
2. Select **Validate generated migration containers**.
3. Choose **Run workflow**, select `main`, and run it.
4. Open the run and inspect both **Validate flask-app** and **Validate fastapi-app**.

No Render account, deployment credentials, or service provisioning is involved.
Use standard GitHub-hosted `ubuntu-latest` runners and review your account's Actions
usage allowance. Ascend configures no paid runners or external paid services.

## What each matrix job does

1. Checks out repository-owned trusted code with read-only repository permissions
   and without persisting checkout credentials.
2. Sets up Python 3.13 and installs `backend/requirements.txt`.
3. Packages only `examples/flask-app` or `examples/fastapi-app` into an in-memory ZIP.
4. Invokes `app.api.generate.generate_archive`, the same extraction, analysis,
   readiness, and configuration-generation processor used by `POST /api/generate`.
5. Copies the trusted sample into a new temporary context and applies only newly
   generated files. Existing requirements and `.env.example` are preserved. An
   unexpected collision fails preparation rather than overwriting a source file.
6. Requires a freshly generated Dockerfile and production-server supplement; it
   does **not** read or build Dockerfiles from `docs/generated`.
7. Builds the image with plain build logs, then starts it as configured by the
   generated Dockerfile (Gunicorn for Flask; Uvicorn for FastAPI).
8. Publishes host port **18080** to container port **18081**, passing `PORT=18081`.
   The generated image default is 10000, so this exercises a real port override.
9. Polls the real `/health` endpoint, requiring HTTP **200** and JSON exactly equal
   to `{"status": "ok"}`. It retries startup failures up to 30 times with two-second
   pauses and five-second HTTP timeouts, then exits nonzero on failure.
10. Prints container status, inspection output, and logs, then removes the
    container, image, and temporary context using `always()` steps.

The sample settings are explicit non-secret CI placeholders: `SECRET_KEY` for the
Flask example and `STORAGE_PATH=/app/data` for the FastAPI example. No actual `.env`
files or local secrets are copied into the context. The Flask source intentionally
creates `./uploads`; the generated non-root user owns `/app`, allowing that write.

`fail-fast: false` lets both samples produce independent results if one fails.
Each job has a 20-minute timeout. Cleanup is configured for normal success/failure;
abrupt runner loss can prevent cleanup steps, but no persistent upload service or
cloud resource is created by this workflow.

## Inspecting results

Expand these steps in each job:

- **Generate fresh trusted sample context**: fresh generated filenames, preserved
  source files, warnings, and `context_prepared_not_built`. Preparation alone is
  never presented as a container success.
- **Build generated image**: pip dependency and Docker layer output. This must pass
  before the sample is considered buildable in the tested environment.
- **Start generated container**: port mapping and container creation failures.
- **Verify real health HTTP response**: the `PASS` line with HTTP 200 and expected
  body, or retry diagnostics followed by failure.
- **Container diagnostics**: exit codes/state and server logs, including startup
  imports, permissions, missing environment, or runtime errors.
- **Clean up container, image, and context**: cleanup completion.

Only report cloud container validation as passed after **both** matrix jobs are
green and show their health-check PASS lines. A successful context-generation step,
local unit tests, or an image build without a successful HTTP test is insufficient.

## Local preparation without Docker

From the repository root, with backend dependencies installed:

```bash
backend/.venv/bin/python scripts/prepare_validation.py \
  --sample flask-app --output /tmp/ascend-flask-context
backend/.venv/bin/python scripts/prepare_validation.py \
  --sample fastapi-app --output /tmp/ascend-fastapi-context
```

Choose new output paths: existing directories are refused. The script accepts only
the two fixed sample names, rejects symlinks, and contains no Docker execution or
arbitrary upload/project argument. Remove your temporary contexts when finished.
The workflow removes its contexts automatically. Unit tests cover preservation,
fresh generation, safe output, and cleanup after a preparation failure.

## Configurable ports in generated migration workflows

The application migration workflow emitted by the generator now exposes:

- `HOST_PORT`: the port HTTP clients use on the runner, default 10000.
- `CONTAINER_PORT`: the actual application listen port inside the container,
  default 10000 for Ascend-generated Dockerfiles.
- `CONTAINER_PORT_REVIEWED`: true for newly generated Dockerfiles; **false** when
  an existing Dockerfile is reused. The smoke-test step fails with instructions
  before building/running until this review flag is set to true.

The mapping is `127.0.0.1:${HOST_PORT}:${CONTAINER_PORT}`; HTTP uses HOST_PORT and
PORT is passed as CONTAINER_PORT. An existing image may ignore PORT entirely:
inspect its startup command and choose its real listen port before setting the
review flag. Changing HOST_PORT does not change the application's listen port.
Existing Dockerfiles are never replaced. `SMOKE_TEST_PATH` remains configurable,
and the workflow's application working directory still follows `analysis_root`.

## Common failures and limits

- Package/index or Python-image download failures: inspect build logs and retry
  transient network failures. Dependency versions and base images can change over
  time; this run validates only the versions actually resolved in that run.
- Missing production server: verify the generated supplement is in the context
  and is installed by the generated Dockerfile.
- Invalid Gunicorn/Uvicorn target or factory syntax: inspect the generated CMD
  and application import paths. Factory syntax has unit coverage; this two-sample
  runtime matrix validates application objects, not every supported factory/layout.
- Permissions: confirm `/app` and required writable directories belong to the
  non-root app user. Do not fix this by silently switching production to root.
- Connection refused/wrong port: inspect container logs, wildcard binding, PORT
  handling, and the host/container port mapping. EXPOSE is metadata, not proof of
  which port a server actually opens.
- Unexpected status/body: confirm the sample's real `/health` route and startup
  environment. A 404, 500, redirect to an unrelated route, or wrong body is not a
  passing smoke test.
- Existing output/collisions: use a fresh context path; preserve source files and
  review unexpected generator output rather than overwriting existing files.

This validates only repository-owned trusted samples. It does not build uploaded
applications, prove WebAble/browser-runtime compatibility, or validate production
databases, external services, load, persistence, or full security behavior.

## CI validation is not public cloud deployment

The containers run temporarily on GitHub-hosted Linux runners, reached through
runner-local loopback HTTP. No public application URL, Render service, persistent
disk, database, or deployment API is created. Success proves the generated sample
configuration built and answered a real HTTP request in that CI environment;
it does not prove a publicly deployed production application will succeed.
