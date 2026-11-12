# Ascend

**From localhost to the cloud**  
Automated Application-to-Cloud Migration Toolkit

Ascend is a Cloud Computing course project intended to help developers prepare
local Python applications for cloud migration. Phases 1–2 provide a FastAPI health
endpoint, a React landing page with live backend status, and secure ZIP uploads
with deterministic static analysis. Phase 3 adds rule-based cloud migration
readiness findings, provisional scores, and prioritized recommendations.
Phase 4 adds downloadable migration configuration review packages.
Uploaded applications are never executed or
installed. All tools used here are free.

## Requirements

- Python 3.11 or newer (developed and tested with Python 3.13)
- Node.js 22.12+ on the 22.x line, 24.x, or 26+ and npm (tested with Node 26)
- Git

## Structure

```text
backend/
  app/
    main.py          # FastAPI app, health endpoint, CORS, request limit
    api/             # ZIP upload API
    services/        # Safe archive extraction and static scanner
    models/          # Pydantic analysis response models
  tests/
  requirements.txt
  .env.example
frontend/
  src/               # React, TypeScript, Tailwind CSS, Vitest tests
  .env.example
examples/            # Trusted Flask and FastAPI sample applications
docs/                # Reserved for later project documentation
```

## Backend setup and development

From the project root:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --env-file .env
```

On Windows, use `py -m venv .venv` and `.venv\Scripts\Activate.ps1`
in PowerShell; copy the environment example with `Copy-Item .env.example .env`.

- Health endpoint: <http://localhost:8000/api/health>
- Interactive API docs: <http://localhost:8000/docs>
- Expected health response: `{"name":"Ascend","status":"ok"}`

`CORS_ORIGINS` is a comma-separated list of allowed frontend origins. By default,
both `http://localhost:5173` and `http://127.0.0.1:5173` are allowed. Uvicorn loads
the backend `.env` when started with `--env-file .env`.

## Frontend setup and development

In a second terminal, from the project root:

```bash
cd frontend
npm ci
cp .env.example .env
npm run dev
```

Open <http://127.0.0.1:5173>. The development server uses a fixed port so that its
origin matches the backend CORS configuration.

`VITE_API_BASE_URL` defaults to `http://localhost:8000`. Change it in
`frontend/.env` if the API runs elsewhere, then restart Vite. Vite also embeds
this value when building the frontend; variables prefixed with `VITE_` are
public browser configuration and must never contain secrets.

The page checks `/api/health` on mount. It displays loading, connected, or
unavailable status; requests time out after five seconds. Start the backend and
refresh the page to reconnect after an unavailable result.

## Tests and production build

From the project root:

```bash
cd backend
.venv/bin/python -m pytest -q
cd ../frontend
npm test
npm run build
```

On Windows, use `.venv\Scripts\python -m pytest -q` for the backend tests.
Backend tests cover health, CORS, archive validation, static analysis, and cleanup.
Frontend tests cover health status, ZIP selection, analysis results, API failures,
and request timeouts.
The frontend build checks TypeScript and outputs static files to `frontend/dist/`.
To inspect that build locally, run `npm run preview` from `frontend/` and open
the URL it prints. The separate backend must still be running.

Tailwind uses its [official Vite integration](https://tailwindcss.com/docs/installation/using-vite).
See also the [Vite guide](https://vite.dev/guide/).

## Version control and scope

Git is initialized locally. No commit or remote push is performed automatically.
Environment files, virtual environments, dependencies, caches, and build outputs
are ignored; `.env.example` files and the npm lockfile belong in version control.

Ascend has no database, authentication, or actual cloud deployment. Phase 4 generates
configuration review templates; existing deployment files are preserved. Docker
startup inspection is limited to the static forms described below.

## Phase 2: ZIP upload and analysis

Choose a ZIP using the upload section and click **Analyze project**. The page
shows progress, clear request errors, and the actual backend report. The health
status remains independent of analysis requests.

`POST /api/analyze` accepts exactly one file using multipart field `file`:

```bash
curl --fail-with-body -X POST http://localhost:8000/api/analyze \
  -F 'file=@/tmp/flask-app.zip'
```

The original filename must end in `.zip`; archive contents are validated rather
than trusting the MIME type. Uploads are limited to 10 MiB (10 × 1024 × 1024
bytes), with a separate total request cap allowing 64 KiB of multipart overhead.
Archives allow at most 1,000 entries (including directories) and 50 MiB of
uncompressed data, including ignored files. These are binary MB limits.

The API rejects unsafe paths, links/special files, duplicate paths (including case
collisions), encrypted archives, and malformed ZIPs. It reads every regular entry
to verify CRC and streamed size limits. Extraction uses temporary storage that is
removed after success or failure. Uploaded multipart files are also closed.
No archive path is passed to `extractall`.

Errors use HTTP 400 for malformed/unsafe archives, 413 for size/count limits,
415 for unsupported upload formats, and 422 for missing files or projects with
no retained Python sources. A Python project whose framework is unsupported
returns HTTP 200 with `framework: "Unknown"` and explicit warnings, so its known
metadata remains useful. Most errors have a JSON `detail` message; total request
limit responses may be plain text.

### Try the sample applications

From the repository root on macOS/Linux:

```bash
python3 -m zipfile -c /tmp/flask-app.zip examples/flask-app
python3 -m zipfile -c /tmp/fastapi-app.zip examples/fastapi-app
```

Upload either archive through the page or curl. No sample dependencies need to
be installed to analyze it. A nested project wrapper directory is supported.
On Windows, use a writable local path such as `flask-app.zip` in place of `/tmp/`.

The trusted examples each have a `/health` endpoint. The Flask sample deliberately
uses local `./uploads` storage, `127.0.0.1`, and port 5000. The FastAPI sample uses
a local storage setting, `localhost`, and port 8001. These are teaching inputs
for the readiness engine: Phase 3 identifies their networking and potential
persistence concerns and returns provisional scores.

Example Flask sample response (analysis fields only; readiness fields are described below):

```json
{
  "project_name": "flask-app",
  "project_type": "web_application",
  "analysis_root": ".",
  "project_root_candidates": ["."],
  "dependency_files": ["requirements.txt"],
  "excluded_application_objects": [],
  "framework": "Flask",
  "python_version": null,
  "dependencies": [{"name": "Flask", "version": "<4,>=3.1"}],
  "dependency_file_present": true,
  "entry_point": "app:app",
  "entry_point_type": "application_object",
  "entry_point_candidate_types": {"app:app": "application_object"},
  "entry_point_candidates": ["app:app"],
  "environment_variables": ["SECRET_KEY"],
  "detected_ports": [5000],
  "detected_hosts": ["127.0.0.1"],
  "dockerfile_present": false,
  "dockerignore_present": false,
  "env_example_present": true,
  "deployment_files": [],
  "files_scanned": 3,
  "analysis_warnings": []
}
```

### Analysis behavior and known limitations

- Flask/FastAPI detection uses primary-source Python imports and known framework
  package metadata. Imports alone never classify a project as a web application.
  AST inspection recognizes direct,
  module-level `Flask(...)` and `FastAPI(...)` assignments and basic factories,
  including simple import aliases. Multiple startup candidates yield a null entry
  point and type, with a warning. Conditional initialization, dynamic imports, re-exports, and complex
  alias/shadowing behavior are not resolved. Static findings are potential startup
  targets, not proof that an application can run.
- `requirements.txt` files are parsed with the
  [Packaging requirement parser](https://packaging.pypa.io/en/stable/requirements.html).
  Declared version constraints are reported, not installed/resolved versions.
  At the selected root, `requirements.txt` takes precedence; otherwise basic
  `pyproject.toml` `[project].dependencies` are supported. Requirement includes,
  pip options, editable paths, lockfiles, optional/development dependency groups,
  and dynamic dependencies are not expanded. Direct dependency URLs are never
  returned. Dependencies from separate subprojects are never combined.
- Python declarations are read from `.python-version`, `runtime.txt`,
  `pyproject.toml` (`project.requires-python`), and `setup.cfg`
  (`options.python_requires`). Different declarations yield null with a warning;
  constraint equivalence is not resolved.
- Literal environment variable names are detected through `os.getenv`,
  `os.environ[...]`, `.get`, `.setdefault`, simple import aliases, and `.env.example`
  keys. Actual `.env` and `.env.*` files are excluded except `.env.example`.
  Environment values and source contents are never included in reports.
- Literal keyword host/port arguments are detected in recognized app/server
  startup calls. Computed values and CLI startup commands are not evaluated.
- Dockerfiles, `.dockerignore`, GitHub Actions YAML files, and common deployment
  filenames are listed; their contents and validity are not analyzed. This is a
  finite filename-based detector, not universal infrastructure discovery.
- `.git`, `.venv`, `venv`, `__pycache__`, `node_modules`, build outputs, common
  caches, compiled Python files, logs, and actual environment files are ignored.
  `files_scanned` counts files in the selected primary analysis scope, including
  deployment metadata. It is zero when multiple project roots remain unselected.
  UTF-8 text files over 1 MiB and unparseable files produce incomplete-analysis
  warnings; they are not executed. All lists are sorted for deterministic output.
- This local course tool has bounded per-request input, but no authentication,
  rate limiting, or process isolation for concurrent public uploads. It is intended
  for local development. Browser visual verification is separate from unit tests.

Multipart parsing and total-body limits use
[Starlette's request support](https://starlette.dev/requests/).
Phase 2 tests cover normal uploads, scanner metadata, secrets, malformed archives,
archive limits, cleanup, upload UI states, API failures, and all Phase 1 tests.

### Phase 2.1: repository classification and analysis scope

Reports now distinguish `web_application`, `python_library`,
`framework_repository`, `ambiguous`, and `unsupported`. These are static
classifications, not cloud readiness assessments. A primary module-level Flask
or FastAPI object or a supported application factory is required for a clear web application classification.
Libraries and framework repositories without such an object receive guidance
to upload a specific application directory instead. Known Flask/FastAPI source
repositories are recognized using project names and their matching package layout.

Scope is determined relative to the uploaded project root, after removing ZIP
wrapper directories. Conventional test paths and filenames (`tests/`, `test_*.py`,
`*_test.py`, `conftest.py`), documentation, examples/samples, benchmarks, and common
development tooling are excluded from primary runtime and environment detection.
Their module-level application objects appear separately in
`excluded_application_objects` with a path, object name, and scope. They never
become primary startup targets. GitHub Actions YAML remains deployment metadata.

Examples are retained in the archive, not globally ignored. To analyze an example
intentionally, ZIP that application's own directory or its contents. For example,
upload `examples/flask-app` using the sample command above; its internal files
become primary source. A standalone example wrapper with its own declaration and
Python source is also supported.

Project boundaries use `requirements.txt`, `pyproject.toml`, `setup.cfg`, and
`setup.py` locations. Each primary Python file belongs to its closest boundary.
If one root is selected, `analysis_root` identifies it relative to the repository
(`.` means the uploaded root), and `dependency_files` names the declaration used.
`dependency_file_present` now covers supported declarations in either requirements
or pyproject format. If multiple roots own primary Python files, the report lists
`project_root_candidates`, leaves `analysis_root` and `entry_point` null, and
does not combine their dependencies, environment variables, or runtime metadata.
Multiple legitimate application objects within one root also keep a null entry
point and an ambiguous classification. For multiple roots, entry point candidates
are repository-relative locations for inspection, not verified startup commands.

These rules are deliberately conservative. Unconventional test/tooling layouts
can be misidentified; extra metadata boundaries can require the user to narrow
the ZIP even within one logical application. Complex factories and dynamic
construction remain unsupported. Optional extras, Poetry-specific dependency
tables, and dynamically generated dependencies are not resolved. Repository
classification does not execute uploaded source or prove deployability.

### Basic application factories and package targets

Ascend recognizes synchronous module-level factory functions that instantiate
Flask or FastAPI and explicitly return that app in a final `return` statement.
Both `app = Flask(...); return app` and `return FastAPI()` are supported. Optional
parameters such as `create_app(test_config=None)` and ordinary configuration
branches are allowed. Factories in tests, examples, documentation, benchmarks,
and tooling do not become production candidates.

An application in `flaskr/__init__.py` retains its package name. For a Flask
tutorial-style factory, the report includes:

```json
{
  "entry_point": "flaskr:create_app",
  "entry_point_type": "application_factory",
  "entry_point_candidates": ["flaskr:create_app"],
  "entry_point_candidate_types": {"flaskr:create_app": "application_factory"}
}
```

This is an explicit package target usable with `flask --app flaskr:create_app run`.
The Flask shortcut `flaskr` relies on Flask's own automatic factory discovery;
Ascend reports the explicit target to avoid inventing a second candidate for the
same factory. FastAPI factories use the same `module:function` notation (with
Uvicorn's `--factory` option when started manually). Module-level app instances
have `entry_point_type: "application_object"`. Multiple valid candidates retain
their individual types in `entry_point_candidate_types`, while the selected
`entry_point` and `entry_point_type` stay null.

Detection remains static and conservative. Decorated, async, generator, and
required-argument factories are not selected. App reassignments and returns
through conditional branches, indirect helper calls, or complex control flow
are not inferred. Runtime success is not verified and uploaded code is never
executed. A package directory must be present to infer its name; a bare
`__init__.py` does not establish an importable package target.

The representative tutorial fixture is in
`backend/tests/fixtures/flask-tutorial/flaskr/__init__.py`. It is only read as
test input; no Flask installation or application execution is needed to analyze it.

## Phase 3: cloud migration readiness

`POST /api/analyze` now also returns `readiness_score`, `readiness_status`,
`assessment_provisional`, `findings`, `severity_summary`,
`migration_recommendations`, and `assessment_limitations`. The existing analysis
fields and upload contract are preserved. All checks run before the scoped
temporary project directory is removed.

A score is available only for a supported Flask/FastAPI `web_application` with
one selected analysis root. Unambiguous application factories are valid startup
targets. Libraries, framework source repositories, unsupported projects, and
ambiguous projects return a null score and `readiness_status: "unavailable"`,
with guidance to select a specific application. Their zero issue counts mean
they were not assessed, not that they passed the checks.

Each finding has a stable rule ID, title, severity, confidence, category,
explanation, and recommended fix. A source-relative file path and one-based line
number are included when supported by AST evidence; absent-file findings have
null locations. There is at most one finding per rule, with a representative
source location. Repeated occurrences are not additional severity counts.
No source lines, literal secret values, absolute-path values, or actual `.env`
contents are included in readiness findings.

### Supported rules

| Rule | Concern | Severity | Confidence |
|---|---|---|---|
| DEP001 | No supported runtime dependency declaration at the selected root | Warning | Confirmed |
| RUN001 | No single importable startup target detected | Warning | Potential |
| NET001 | A server startup call binds to loopback | Warning | Potential |
| NET002 | A server startup call has a literal port | Warning | Potential |
| STO001 | An absolute literal in a filesystem operation or file/directory configuration may assume a local layout | Warning | Potential |
| STO002 | SQLite, file writes, or local storage configuration may need persistence | Warning | Potential |
| SEC001 | Sensitive configuration has a literal value or fallback | Critical | Potential |
| CTR001 | Dockerfile not detected | Informational | Confirmed |
| CTR002 | .dockerignore not detected | Informational | Confirmed |
| ENV001 | Exact referenced environment names are absent from scoped .env.example keys, with detected default/usage information | Warning | Confirmed |
| RUN002 | Python runtime declaration is missing, unsupported, or conflicting | Informational | Potential |

Confirmed means the static observation is established, not that deployment
will fail. For example, ENV001 confirms missing documentation; a variable may
still have a valid default. A hardcoded port can be appropriate, and a loopback
development command can be overridden by the production server command. SQLite
and temporary writes are not automatically unsuitable: review persistence,
durable volumes, backups, and whether the data can be ephemeral. A Dockerfile is
optional on platforms that build directly from source.

### Transparent scoring

Start at 100 and deduct using this table:

| Severity | Confirmed deduction | Potential deduction |
|---|---:|---:|
| Critical | 25 | 15 |
| Warning | 10 | 5 |
| Informational | 2 | 1 |

Deduplicate by rule. Closely related rules share only their **largest** deduction:
NET001/NET002 share a server-binding group; STO001/STO002 share a storage group;
CTR001/CTR002 share a containerization group. Other rule deductions are added.
Clamp the final result to 0–100. Severity counts describe findings, not deduction
groups. Findings and checklist steps are ordered by severity (critical first),
then confirmed before potential, then rule ID. Duplicate checklist steps are
removed, and a generic container-based PaaS approach is suggested last.

- 85–100: `ready_for_migration_review` — Ready for migration review.
- 60–84: `needs_improvements` — Needs improvements.
- 0–59: `significant_migration_concerns` — Significant migration concerns.
- Null: `unavailable` — No readiness assessment for this project classification.

Mark the assessment provisional when there are potential findings, scanner
warnings, unresolved environment names, unknown production binding, missing
startup/runtime information, or no parsed dependencies. Unknown host binding and
computed environment names receive no inferred issue or deduction. Missing or
conflicting runtime declarations receive the small RUN002 potential deduction.
An existing but empty/unparsed dependency file is not treated as confirmed
absence; it produces a provisional assessment and a limitation instead.

Even a nonprovisional score of 100 is a static review result. It does not guarantee
successful deployment, valid dependencies, or a secure application.

### Actual trusted sample reports

These reports were generated by ZIP-uploading the existing trusted samples to
`POST /api/analyze`; the JSON files contain the complete returned responses:

- [Flask sample report](docs/sample-reports/flask-app.json)
- [FastAPI sample report](docs/sample-reports/fastapi-app.json)

| Sample | Score | Status | Provisional | Critical / warning / informational |
|---|---:|---|---|---|
| flask-app | 87 | Ready for migration review | Yes | 0 / 3 / 3 |
| fastapi-app | 87 | Ready for migration review | Yes | 0 / 3 / 3 |

Both samples have NET001, NET002, STO002, CTR001, CTR002, and RUN002. Their score
is `100 - 5 (network group) - 5 (storage group) - 2 (container group) - 1 (unknown
Python declaration) = 87`. The checklist recommends reviewing production binding
and port configuration, persistence, container configuration, and runtime pinning.
The label means ready for migration **review**, with those concerns still open.

### Readiness limitations

- This is a finite set of static heuristics, not a deployment validator or a full
  security scanner. No uploaded code, dependencies, startup commands, containers,
  or cloud services are run by the API. Phase 4 configuration generation is a separate action.
- Only primary scoped Python ASTs and supported metadata are inspected. Deployment
  files are generally detected by filename; Phase 3.1 also inspects straightforward
  Docker CMD/ENTRYPOINT declarations without validating or executing them.
  Existing scanner limits (UTF-8, 1 MiB text parsing, unsupported syntax,
  factories, metadata formats, and project-root heuristics) still apply.
- Secret detection checks sensitive assignment/dictionary/keyword names and literal
  defaults, so it can flag safe placeholders and miss obfuscated, encoded, or
  unusually named credentials. Potential secrets are never printed in findings.
- Local storage and absolute-path hints can be false positives. Absolute-path checks
  require filesystem-operation or file/directory-configuration evidence, rather
  than flagging arbitrary URL-like strings; explicit in-memory SQLite connections
  and read-only file opens are not persistence findings. Dynamic paths, writes
  through arbitrary helper methods, and externally configured storage may be missed.
- Production startup may override detected development ports and hosts. Referenced
  environment variables may be optional; computed names cannot be resolved.
- Recommendations suggest a generic managed HTTP container-based PaaS approach,
  with environment-injected configuration, health checks, and persistent storage
  where needed. Ascend selects no provider, provisions no resources, and deploys
  nothing. Provider suitability and pricing are outside this assessment.

### Phase 3.1: finding accuracy and existing configuration

ENV001 now lists the exact undocumented names in its explanation and in a
structured `environment_variables` list on the finding. Each item contains only
`name` and `usage`:

```json
[
  {"name": "DATABASE_URL", "usage": "required_access"},
  {"name": "DEBUG", "usage": "default_detected"},
  {"name": "TOKEN", "usage": "no_default_detected"}
]
```

`required_access` means an `os.environ[...]` lookup was detected;
`no_default_detected` means no non-None default was detected; both are described
as **potentially** required. `default_detected` means the variable may be optional,
not that its default is validated. Empty strings, zero, and false count as defaults;
explicit None does not. If the same name has different usages, the strictest read
is reported. Values, default expressions, and actual `.env` contents are never
returned. Computed variable names remain an assessment limitation. The ENV001
deduction remains a documentation-gap deduction, not a claim that an optional
variable is mandatory.

Networking checks can inspect one scoped root Dockerfile for a straightforward
Uvicorn command whose target matches the selected application object or factory.
Supported forms include exec arrays, simple shell commands, `python -m uvicorn`,
and exec-array ENTRYPOINT plus CMD arguments. Factories must use `--factory`.
Shell `$PORT` / `${PORT}` arguments can resolve a guarded development port concern;
JSON exec-form variables are not assumed to expand. Quoted literal or escaped
dollar expressions are not treated as resolved environment configuration.

An explicit wildcard host can remove NET001 for startup calls under
`if __name__ == "__main__"`; a shell environment port can similarly remove NET002
for those guarded calls. Import-time Python server calls remain potential concerns.
A literal Docker port still produces NET002, with the Docker source location.
Unguarded calls, unmatched targets, multiple Dockerfiles, other possible deployment
overrides, multistage builds, scripts, custom SHELL directives, complex shell commands, unsupported options,
and ambiguous commands are not assumed to resolve networking concerns. Custom base
images without an explicit entrypoint are also conservative because of possible
inherited entrypoints. No Docker instructions or application commands are executed.

STO001 now prefers `open`, pathlib constructors, SQLite connections, filesystem
operations, and file/directory configuration names. Arbitrary routes, endpoint
lists, mount paths, and HTTP client URL arguments are not filesystem evidence.
Simple uniquely assigned literals passed to a filesystem operation can be followed.
This is not full data-flow analysis: dynamic paths and unconventional configuration
names can be missed, and misleading path-named configuration can still be flagged.

Recommendations now favor reviewing and reusing detected Dockerfiles and relevant
existing deployment files. Presence and detected startup settings are not validation
of build correctness, production overrides, or deployment success. Scoring weights,
severity groups, and thresholds are unchanged: removing a duplicate networking
finding may leave the shared deduction unchanged while reducing the issue count.
Resolving both networking findings removes that group's deduction.

The trusted Flask and FastAPI samples remain **87/100**, provisional, with
0 critical, 3 warning, and 3 informational findings. They contain no production
Docker override, so their concerns and scores are unchanged from Phase 3. The
linked sample JSON reports have been refreshed using actual API responses.

## Phase 4: migration configuration generation

After analysis, click **Generate Migration Files**. Ascend uploads the selected
ZIP again through `POST /api/generate`, reuses the same bounded multipart parser,
safe extraction, scoped scanner, and readiness engine, and deletes temporary files
on success or failure. It never executes the application, pip, Docker, or provider
commands. Generated files have code previews, copy buttons, and a ZIP download.

Only a supported Flask/FastAPI web application with one selected root and one
importable object/factory target is eligible. Libraries, framework repositories,
unknown projects, ambiguous targets/roots, and unsafe root path names are ineligible.
Application roots currently use simple ASCII letters, digits, underscores, slashes,
dots, and hyphens; extract unusual directory names into a simpler application root
before generating. `src/` import targets get an explicit `/app/src` PYTHONPATH and
adjusted target; verify imports before using the resulting configuration.

Every package has `status: "manual_review"`; no package is claimed to be deployable.
Missing/unparsed dependencies, unsupported requirement directives or direct URLs,
incomplete analysis, or incompatible/unresolved Python versions withhold Docker,
CI, and Render templates. Safe environment/ignore suggestions and a guide can
still be produced. Resolve the limitations and re-upload to generate those files.

### Generated files

| Path | Purpose |
|---|---|
| `Dockerfile` | Official Python slim image, declared dependency install, writable `/app`, non-root user, production server on `0.0.0.0`, and `${PORT:-10000}` |
| `requirements.ascend.txt` | Explicit production-server supplement: Gunicorn for Flask or Uvicorn for FastAPI; review/pin a compatible version |
| `.dockerignore` | Excludes Git, environments, Python/test caches, local environment files, and local dependencies; database/build exclusions are commented review suggestions to preserve required assets |
| `.env.example` | Detected names only, with empty values and usage comments; defaulted variables are commented out to avoid overriding defaults |
| `.github/workflows/ascend-migration.yml` | Push/PR container build, container start, and retrying configurable HTTP smoke test on `ubuntu-latest`; no deployment credentials |
| `render.yaml` | Manual-review Render Docker web-service template, explicitly free plan, auto-deploy disabled, port 10000, no disks/databases/paid resources |
| `MIGRATION.md` | Project-specific setup, conflicts, environment usage, unresolved findings, specialized runtime warnings, and validation steps |

Flask object targets use `python -m gunicorn ... module:app`; factories use
`'package:create_app()'`, following [Gunicorn's factory syntax](https://gunicorn.org/run/).
FastAPI targets use `python -m uvicorn ...`, with `--factory` for a factory, as
documented in [Uvicorn settings](https://uvicorn.dev/settings/).
The server supplement is explicit even if the server is already declared; pip
does not upgrade an already satisfied installed version by default. Review
dependency compatibility and pinning before relying on it.

Supported generated image versions are Python 3.11–3.14. Exact declarations are
retained, and supported basic constraints select a satisfying minor version.
Unknown versions use `ASCEND_DEFAULT_PYTHON_VERSION` (default `3.13`), disclosed in
the warnings and guide. Set this backend setting in `.env` and restart the API
to change it. Known unsupported constraints are not silently replaced with the
default. Patch-image availability and application compatibility require a real build.

The generator installs `requirements.txt`, or uses `pip install .` for a basic
PEP 621 project. It does not resolve requirement includes, direct/private URLs,
editable projects, dynamic dependencies, package discovery, or native build
requirements. A manual Docker/pip build can execute source build hooks; inspect
untrusted source before any such build.

### Existing configuration protection

Existing files are listed and skipped, not copied into the download or overwritten.
Existing Dockerfiles and Render files receive reuse/review instructions. If any
CI workflow already exists, integrate the smoke test manually instead of creating
a competing workflow. Repository-level workflows/Render files are also protected
when the selected application is nested. The generated supplement and migration
guide get distinct numbered names on conflicts. Every emitted file is marked
`requires_manual_review: true`.

Apply Dockerfile, supplement, ignore/environment files, and guide in the selected
application root. Place the workflow and Render YAML at the **repository root**;
their generated working directory/build context points to `analysis_root`. Adjust
these paths if moving the app into a new standalone repository. Never extract a
package over existing configuration blindly; inspect the skipped-file list first.

### API and downloads

```bash
curl --fail-with-body http://localhost:8000/api/generate \
  -F 'file=@/tmp/fastapi-app.zip' -o /tmp/generation.json
python3 -c 'import base64,json; from pathlib import Path; r=json.loads(Path("/tmp/generation.json").read_text()); Path("/tmp/ascend-migration.zip").write_bytes(base64.b64decode(r["download_zip_base64"]))'
```

Only decode the ZIP if `download_zip_base64` is non-null. Ineligible responses
have no download. The JSON response includes `eligible`, `status`, `analysis_root`,
`generated_files` (path/content/manual-review flag), `existing_files`, `skipped_files`
(path/reason), `generation_warnings`, `unresolved_findings`, `deployment_instructions`,
and `download_zip_base64`. The browser creates the ZIP download directly from those
bytes; there are no stored uploads, persistent download tokens, or download paths
selected by a client. ZIP entries are generator-controlled relative paths with
deterministic timestamps. No source files, source dependency manifests, actual
environment files, existing configuration contents, or secret values are exported.

### Local Docker validation and CI setup

After reviewing the generated guide and applying the files to a trusted app root:

```bash
docker build -t ascend-app .
docker run --rm -p 10000:10000 -e PORT=10000 ascend-app
# In another terminal, using the detected/configured path:
curl --fail http://localhost:10000/health
```

Use `/health` only if the application actually defines it. Direct literal GET
decorators on an identified app can supply a smoke path; otherwise the workflow's
`SMOKE_TEST_PATH` is a clearly disclosed `/` placeholder to configure. Authentication,
route behavior, startup side effects, and external services are not verified. The
workflow passes only PORT by default; configure required application variables
before enabling it. A failing build/startup/HTTP response fails the job and prints
container logs. Review runner usage allowances for your account; no paid service
or deployment credential is configured by the template.

### Render template review

The template's fields were checked against Render's current
[Blueprint specification](https://render.com/docs/blueprint-spec),
[web-service port requirements](https://render.com/docs/web-services), and
[free-instance documentation](https://render.com/docs/free) on November 13, 2026.
It explicitly uses `runtime: docker`, `plan: free`, `autoDeployTrigger: "off"`,
`dockerfilePath`, `dockerContext`, and PORT 10000. A detected smoke route supplies
`healthCheckPath`; if none is detected, configure it manually. Non-defaulted
referenced names use `sync: false` dashboard prompts, not embedded values. Remove
optional prompts as appropriate; a prompt is not proof that a variable is mandatory.
Defaulted variables are left unset. Review the generated fixed service name and
ensure it does not select an existing service unintentionally before applying it.

This is a review template, not a Render CLI/API validation or an executed deploy.
Review current schema/free-plan availability and all service settings before manually
using it. Free instances have ephemeral storage and do not support persistent disks;
Ascend adds no paid plan, disk, database, secret value, or resource provisioning.
Applications needing durable data or specialized runtimes require a separate plan.

### Actual saved sample packages and verification limits

Representative files returned by the generation API are saved under:

- [Flask Dockerfile](docs/generated/flask-app/Dockerfile) and
  [migration guide](docs/generated/flask-app/MIGRATION.md)
- [FastAPI Dockerfile](docs/generated/fastapi-app/Dockerfile) and
  [migration guide](docs/generated/fastapi-app/MIGRATION.md)

Each sample package contains six files: Dockerfile, production-server supplement,
.dockerignore, CI workflow, Render YAML, and migration guide. Its existing
`.env.example` is preserved and therefore excluded from the download. The adjacent
`generation-report.json` records the actual API response without repeating base64
download bytes. Guides and reports are generated from the trusted examples, not
fabricated deployment results.

The Docker CLI was unavailable on PATH in the current workspace. Consequently no image
builds, running-container checks, or real container HTTP smoke tests were performed.
The generated commands and configurations are covered by unit/API tests only.
Browser visual verification is separate from frontend unit tests.

Known limitations include finite Python versions, conservative static package/root
detection, unverified dependency resolution, and skipped existing configuration.
Obvious Playwright/Selenium/browser, native ML/image/PDF, and database-driver packages
produce manual-review warnings; generic images do not automatically install browsers,
OS packages, fonts, models, or other runtime assets. Review data/build exclusions
before building to avoid packaging private local data or omitting required assets.
No configuration package guarantees compatibility or successful deployment.

## Phase 6A: cloud-hosted container validation

The [validation workflow](.github/workflows/validate-migration.yml) generates fresh
configurations for the trusted Flask/FastAPI samples, builds and runs their images
on GitHub-hosted Ubuntu runners, and requires real HTTP 200 health responses with
the expected JSON body. It runs on relevant pushes to `main` and supports manual
dispatch. It never builds arbitrary uploaded applications and does not deploy to
Render or create a public cloud service.

See [cloud-validation.md](docs/cloud-validation.md) for trigger instructions,
logs, cleanup, expected results, and the distinction between CI and deployment.
The generated application smoke-test workflow now separates `HOST_PORT` from
`CONTAINER_PORT` (both default 10000). Reused Dockerfiles require explicit container
port review before that workflow will build/run; existing images may ignore PORT.

Docker CLI is still unavailable locally. Cloud container validation is **pending**
until the workflow actually runs successfully after you push. Local unit/build
checks and prepared contexts are not evidence of successful container execution.
