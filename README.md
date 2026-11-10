# Baku: banking POC and procurement sandbox

## Banking specification alignment and deployment status

The demonstration implements synthetic banking journeys for card services,
SME lending and governed payments. Private specifications, presentations and
event survey links are deliberately not included in this public source.
**The POC has real hosted workers and a deployed authenticated web application;
signed-in journeys, physical voice and native-language checks have
presenter-reported acceptance.**

On 2026-11-10, the presenter reported these checks satisfactory in the deployed
application. This records manual acceptance, not instrumented latency results
or independent proof of a particular backend Toolbox grant.

See the [implementation specification](docs/specification.md) for current
behavior, approved scope changes and the remaining acceptance gates.
See the [application architecture diagrams](docs/architecture.md) for the
deployed voice, hosted-agent and banking-policy paths, identity boundaries,
and separately labeled target integrations.

The Foundry project, banking text model and four code-hosted agents have been
deployed and actually invoked, including orchestrator-to-specialist routing.
The native voice profiles have canonical banking instructions and function tools,
confirmed by service readback. Correlated text-input turns produced transcripts
and decoded audio in English, Russian and Azerbaijani. A recorded English PCM
utterance also completed native speech recognition, a banking function call,
real hosted-worker execution and spoken output. All three live hosted/governed
banking journeys passed. These integration exercises use a local ASGI broker
fixture; they do not prove deployed presenter authentication or App Service
managed-identity execution.

The cleaned cloud source deployment, ASGI startup and site warmup completed.
Remote archive hashes match the deployed banking HTML, JavaScript and CSS.
The deployed native voice version pins match service-verified profiles, and
anonymous banking routes reject access. Microsoft sign-in is required before
using the app. Each hosted banking agent has a distinct active Foundry-managed
Entra instance identity, confirmed by agent service readback.
Deployed presenter sign-in, physical voice and native-language checks now have
presenter-reported acceptance. Independent managed-identity/Toolbox execution
evidence remains a separate technical gate. The staff console shows each
original transcript line with an English machine translation from the Foundry
model deployment (`transcripts.py`); digit runs of four or more, such as OTPs, are
withheld before translation. `evaluate_foundry.py` runs a Foundry cloud
evaluation of trilingual policy answers generated live by the model, using
built-in groundedness, task adherence, violence and hate/unfairness evaluators.
Results appear in the portal Evaluations page and locally in the ignored
`.azure\evaluation-latest.json`. It evaluates policy answers, not live voice
calls. `evaluate_attack.py` records a model-level cross-customer attack
evaluation. It sends English, Russian and Azerbaijani attack prompts to the live voice agent's
instructions and banking tool, then runs any tool calls against a temporary
deterministic bank. Results are written to the ignored
`.azure\attack-evaluation-latest.json`. In the latest run, the model refused
all three, but it never sent a cross-customer payload, so the broker's
deterministic flag was not triggered by the model.

Deployed Foundry integrations (service evidence, separate from presenter acceptance):

- **Foundry IQ** (`provision_iq.py`): a Free-tier AI Search service in France
  Central, a trilingual knowledge base and a read-only knowledge toolbox.
  Retrieval returned cited answers in all three languages. Questions outside
  the indexed policy corpus return no citable passages.
- **Governed banking gateway** (`provision_gateway.py`, `gateway.py`): a
  Toolbox MCP connection reaches the App Service `/gateway/mcp` route using the
  project managed identity through Easy Auth. Invalid grants are refused and
  audited. A positive mutation needs a broker-issued grant from a signed-in
  presenter session; independent evidence of that positive grant path remains
  open despite presenter acceptance of the journeys. The read-only knowledge
  toolbox is not the governed gateway.
- **Hosted agents version 2**: per-customer persistent session files with server-derived delegated
  isolation keys. Version 1 is retained for rollback. A live session retained
  its files across 450 seconds of inactivity. Scale-to-zero was inferred from
  the configured 300-second idle timeout and a slower first response; zero
  replicas were not observed, so scale-to-zero acceptance remains open.
- **Portal tracing**: **intentionally disconnected** pending a metadata-only
  route. Once connected, the Foundry voice/agent and Responses services recorded
  synthetic message content service-side, violating the no-transcript telemetry
  condition. The account- and project-scope Application Insights connections were removed
  on 2026-11-10 at 19:24:08Z. No content rows have arrived since then.

  The capped Log Analytics workspace and Application Insights resource remain. A
  user-approved scoped purge was **submitted, and Azure accepted it**, on 2026-11-10.
  It covers only the captured content rows, all before 19:24:08Z:

  - `AppGenAIContent`: 161 rows.
  - `agentsv2` `AppDependencies`: 58 rows.
  - `agentsv2` `AppRequests`: 33 rows.

  Content-free operational rows are preserved. The purge operations were still
  **pending** at the last check. Azure purges are asynchronous, so the rows stay
  queryable until the purge completes.

  Restoring tracing requires an approved content-protection route.
An ARM resource ID or advertised endpoint alone is **not** proof of readiness.

`infra\banking-foundry.json` is a parameterized, incremental ARM template pinned
to API `2025-06-01`. Following the official Foundry starter template, it
serializes account, regional model and then project creation.
`deploy_banking.py` reads target values from `.env` and verifies both ARM state
and agent API access. On deployment failure, it also refreshes the saved project
state and preserves Azure's error diagnostics. A failed ARM deployment can leave
a project processing asynchronously; inspect its live state and deployment
operations before retrying. Pending or failed projects never count as ready.
`provision_banking.py` creates and reads back the draft
native `voice-agent.json`, saving the confirmed version only in `.env`.
The voice definition and multilingual audio configuration have been read back,
but multilingual speech quality is not yet validated end to end. Choose a region supporting both
[voice agents and hosted agents](https://learn.microsoft.com/azure/foundry/agents/concepts/limits-quotas-regions)
and the required [Voice Live models](https://learn.microsoft.com/azure/ai-services/speech-service/regions?tabs=voice-live).
Managed models may use global or data-zone inference; resource location alone
does not establish single-region data processing.

### Working local banking prototype

Run `.\run.ps1`, then open **http://127.0.0.1:8765/banking**. The original
procurement demo remains at `/`, with its separate configuration in `.env`.
The banking page explicitly displays the deployment
status from local configuration. This loopback mode uses manual banking controls,
not the cloud voice bridge. It has no microphone or browser TTS fallback.
Use the authenticated HTTPS deployment for microphone and native voice controls.
Choose a language and journey, then select **Start demo**. The cloud workspace
keeps voice controls and the synthetic DOB guide together; manual controls and
raw technical evidence expand on demand. Local mode opens manual controls
automatically because it has no microphone integration. OTP messages and human
credit review remain visible. The banking page has no survey link or feedback
section.

The manual controls exercise real deterministic Python/SQLite mock workflows:

| Scene | Implemented local behavior |
|---|---|
| Leyla, Azerbaijani | Isolated session; DOB plus expiring, single-use simulated OTP; freeze lost card; three transactions; registered-address replacement; simulated virtual card and SMS-style summary |
| Dmitri, Russian to English | Locale state changes without creating another session; twelve synthetic statements; code-calculated amortization and coverage; persisted two-page memo; explicitly acknowledged human indicative decision |
| James, English | Passcode-confirmed 300 AZN/transaction, 500 AZN/UTC-calendar-month, two-merchant, 30-day mandate; two insurance options; matching-cover renewal and electricity payment; 650 AZN upgrade blocked for both amount and merchant; non-executing one-off review request |
| Governance | Customer-scope refusal and audit flag; human handoff queue; local trilingual policy corpus; exportable hash-linked audit |

Start a fresh session with the selected synthetic caller. Its synthetic DOB is
prefilled for rehearsal. Send the simulated OTP, read it from the SMS-style app
panel, and verify. James's **synthetic** mandate passcode is `246810`. Money is
stored in integer qapik. Each bill can be paid once, and only one insurance
renewal can be paid. Serialized SQLite transactions enforce concurrent payment
and monthly-spend checks. Reopening the application preserves sessions, memos
and audit in the gitignored `data\banking.db`.

All staff roles are simulated by one local operator, **not independently
authenticated humans**. Changing the locale selector is not a demonstration of
live spoken language switching. The two-page memo is locally persisted text
with page separation in the UI/print styles, not a document produced in hosted
agent session storage. The policy corpus is **not Foundry IQ**, the local
dispatcher is **not an MCP gateway**, and the audit actor is accurately labeled
`local-demo-operator`, **not** an Entra agent identity. The injection replay tests
the deterministic customer boundary; it is not an LLM prompt-injection evaluation.

Signed-in journeys, physical voice and native-language checks were accepted by
the presenter on 2026-11-10. Remaining technical and rehearsal gates include
positive managed-identity/Toolbox grant evidence, hosted memo and scale-to-zero
proof, privacy-safe monitoring and confirmed transcript cleanup, strict
secretless sign-in, measured venue-network latency and acknowledgement timing,
separately recorded barge-in evidence, fallback recordings, and
event participation and email/reminder logistics. The selected demo interaction
uses explicit language buttons: changing language ends the current native call
but preserves the banking session and verification. Seamless mid-call switching
is not implemented or claimed, and a survey close is not part of the banking UI.
The Toolbox, Foundry IQ, tracing and hosted persistent-file integrations are
deployed with the service evidence described above; presenter acceptance does
not close their outstanding technical checks. Transcript translations are
machine output for staff; separate review of English translation fidelity has
not been itemized. No measured latency result, survey completion or regulatory
assurance is claimed.

## Original procurement demo

A runnable enterprise procurement demo exploring agentic-commerce research themes.
An Azure Foundry prompt agent compares fictional products. A separate deterministic
policy engine decides whether a simulated purchase may proceed. No payment
provider, real merchant, bank account or survey submission is connected.

## Run locally

Requires Python 3.11+ and, for live agent calls, Azure CLI signed into the configured
tenant. The manual local app has no pip or Node dependencies. Cloud serving and
cloud tests require the Python packages in `requirements.txt` and
`requirements-dev.txt`, respectively.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# Fill .env with your own deployment settings for live calls.
.\run.ps1
# Open http://127.0.0.1:8765
```

`run.ps1` supports the Python runtime bundled with Azure CLI on Windows, including
its isolated import path. To choose another interpreter:

```powershell
.\run.ps1 -Python "C:\Path\To\python.exe" -Port 8765
```

With a regular Python installation, `python app.py` also works. The server binds
only to `127.0.0.1`. Do not expose it through a public tunnel or use it for real
procurement. The explicit **Rule-based** mode runs without Azure sign-in and
selects the cheapest allowed supplier in a category; it does not interpret the
free-text request. Failed live calls surface an error and never silently switch modes.

## Azure configuration

All deployment values belong in the local, **gitignored `.env`** or exported
environment variables, which take precedence. `.env.example` contains only
empty values and public defaults. The parser supports literal unquoted,
single-quoted and JSON double-quoted values, optional `export`, and comments.
It does not execute shell code or expand variables. Invalid or duplicate
assignments fail explicitly without including their values in errors.

| Variables | Purpose |
|---|---|
| `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID` | Exact Azure identity boundary |
| `COMMERCE_*` | Procurement project, model and saved agent |
| `BANKING_*` | Separate banking project, region, model and voice version |
| `BANKING_AZURE_SUBSCRIPTION_ID`, `BANKING_AZURE_TENANT_ID` | Optional banking identity overrides; preserve the procurement subscription when banking uses another tenant |
| `DEMO_SURVEY_URL` | Optional HTTPS survey link, hidden when unset |

Never put credentials, account names, tenant/subscription IDs, personal links,
deployment outputs or customer data in source or documentation. `.env*`,
private certificates, local parameter files, Azure state and `data\` are ignored
by Git and container build context. Only the placeholder `.env.example` is public.
Review exported archives and hosted-agent source ZIPs separately before upload.

```powershell
.\run.ps1 -Task CheckPublic
```

The publication guard checks publishable working files and branch/remote history
against private `.env` values and common credential patterns, without printing
matching values. It cannot guarantee that every possible secret is detected.
App-local recovery/checkpoint refs are not normal branch history: never use
`git push --mirror` or publish a copy of `.git`. If sensitive material reaches a
published commit, deleting its current file or adding an ignore rule is not
enough: rotate exposed credentials and clean history before release.

### Public repository safeguards

Source is licensed under the [MIT License](LICENSE). See the
[security policy](SECURITY.md) for private vulnerability reporting and demo
limitations. Private reporting availability depends on the repository settings;
do not report vulnerabilities or credentials in public issues.

The `Public source checks` GitHub Actions workflow runs offline regression tests
on Linux and Windows, scans eligible files and branch history, and audits web,
test, voice and hosted-worker dependencies against known advisories. Actions
are pinned to immutable commits, use read-only repository permissions, and do
not receive Azure credentials or run deployment/live-service tests. The Edge
browser tests run only where Edge and websocket-client are installed; these
tests do not establish physical microphone or speaker acceptance.
Dependabot checks Python dependencies and action pins weekly.
These workflows take effect only after the files are committed and pushed.

Run the same source checks locally in a dedicated virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt "pip-audit>=2.9,<3"
.\.venv\Scripts\python.exe check_publication.py
.\.venv\Scripts\python.exe -m unittest test_deploy_web test_voice_browser test_cloud test_settings test_banking tests
.\.venv\Scripts\python.exe -m pip_audit -r requirements-dev.txt -r requirements-voice.txt -r cloud\hosted\requirements.txt
```

Dependency auditing uses package indexes and public advisory data; it does not
upload application source or local configuration. It checks freshly resolved
versions, not the package inventory of an existing Azure deployment. A passing
scan does not establish that deployed packages are current.

Model calls and hosted services incur Azure usage charges; offline mode does not.
`DeployWeb` explicitly deploys a separate authenticated App Service application.
Creating the Foundry project alone does not deploy the banking web application.

Local agent calls use an in-memory Entra token obtained from Azure CLI for the
exact configured subscription, with tenant verification. The cloud application
uses its App Service managed identity. No Azure access tokens reach the browser.

```powershell
az login
.\run.ps1 -Task Provision
```

`Provision` creates the saved agent only if absent. For an existing agent, it checks
the configured version and refuses a definition mismatch instead of overwriting.
Project-scoped **Foundry User** is required for agent operations. Azure RBAC
changes can take time to propagate. To deploy the banking infrastructure in
your configured subscription, without changing your default subscription:

```powershell
.\run.ps1 -Task DeployBanking
# After project readiness and project-scoped permissions are confirmed:
.\run.ps1 -Task ProvisionVoice
```

Do not grant roles at account or subscription scope just to run this demo without
reviewing the broader access.

When using the Foundry `azd` workflows, verify its identity separately from Azure
CLI. To delegate to the already authenticated Azure CLI without changing another
project's global `azd` configuration, use a worktree-local, ignored configuration:

```powershell
$env:AZD_CONFIG_DIR = Join-Path (Get-Location) ".azd"
azd config set auth.useAzCliAuth true
azd auth login --check-status
```

Set `AZD_CONFIG_DIR` in every deployment shell that uses this configuration.
This does not sign in, switch Azure CLI accounts, or prove that provisioning,
agent invocation or native voice is working.

### Fresh Foundry installation with azd

`cloud\azure.yaml` describes an isolated Foundry project, banking model and
managed voice agent. `voice-agent.json` is the canonical source of its banking
instructions and function tools, injected into the generated manifest.
Application hosting and code-hosted workers are separate deployment steps.
Audio retention and response storage are disabled in the voice definition;
this is not a guarantee of zero service retention.

The native voice authoring workflow currently requires the preview agent
extension; the stable `1.0.0` agent initializer does not expose `prompt-voice`.
Use an isolated CLI configuration as above, then install the matching official
extensions:

```powershell
azd extension install microsoft.foundry
azd extension install azure.ai.agents --version 1.0.0-beta.19 --force
.\run.ps1 -Task DeployCloud
# Review the preview before applying:
.\run.ps1 -Task DeployCloud -Apply
# Deploy native voice instructions and tools after the project is ready:
.\run.ps1 -Task DeployVoice
# Optional voice client dependency, not needed by the local app or unit tests:
python -m pip install -r requirements-voice.txt
.\run.ps1 -Task VerifyVoice
# Native local-voice profiles use independent versions:
.\run.ps1 -Task DeployVoice -Locale ru-RU
.\run.ps1 -Task VerifyVoice -Locale ru-RU
.\run.ps1 -Task DeployVoice -Locale az-AZ
.\run.ps1 -Task VerifyVoice -Locale az-AZ
```

`deploy_cloud.py` reads the banking target from `.env` and checks the Azure CLI
tenant before invoking azd. Because the Foundry provider does not expand all
custom manifest fields, it resolves model names, voice instructions and tools into a generated
manifest under ignored `.azure\foundry-project`, never into public source.
Private azd state is isolated by subscription, resource group and project so a
new tenant cannot accidentally reuse the previous deployment's account outputs.
Subscription, region, resource group and project name are passed through its
private azd environment. The workflow previews by default, never automatically
retries a failed apply, refreshes the actual project state after failure, and
updates only banking account configuration. The procurement configuration is
preserved. Infrastructure readiness is not voice or full-solution readiness;
deploying agents and verifying actual audio and banking tool calls remain
separate acceptance gates.

The Azure Speech transcription configuration requires Azure semantic VAD;
the manifests use `azure_semantic_vad_multilingual`, not `server_vad`. The
service rejected the incompatible pairing during the first audio test.
`VerifyVoice` uses a unique request marker and matching response ID, drains an
optional greeting, and requires a completed transcript and decoded audio bytes.
An automatic greeting alone cannot pass. It keeps tokens in memory and does
not save audio. This text-driven check is not microphone, language-switching,
barge-in, pronunciation or banking-tool acceptance.

The canonical native model is `gpt-realtime-2.1`. English uses Sonia, Russian
uses Svetlana, and Azerbaijani uses Banu with MAI-Transcribe-2 (`az`).
Azerbaijani output must explicitly use `az-Latn-AZ`: omitting the output locale
causes the native agent service to supply `az-AZ`, which Voice Live rejects with
`invalid_locale_string`. The public caller language remains `az-AZ`.

### Hosted workers and authenticated cloud application

```powershell
.\run.ps1 -Task DeployHosted
.\run.ps1 -Task VerifyHosted
.\run.ps1 -Task DeployWeb
# Regular Python: use a dedicated virtual environment for cloud dependencies.
python -m pip install -r requirements-dev.txt
.\run.ps1 -Task TestCloud
.\run.ps1 -Task VerifyIntegration -Live
# Optional recorded caller: mono PCM16 WAV, 24000 Hz.
.\run.ps1 -Task VerifyIntegration -Live -CallerWav "C:\Private\caller.wav"
```

For Azure CLI's embedded Python, do not modify its shared packages. Install
dependencies with `-m pip install --target .azure\cloud-python -r requirements-dev.txt`
instead. The runner automatically uses that private directory when present.
The isolated official azd hosted-agent build uses Python 3.13 and remote dependency
resolution; no local Docker or Node installation is required.

`DeployHosted` creates four actual `kind: hosted` workers: `banking-orchestrator`,
`banking-card`, `banking-lending` and `banking-payments`. Private generated manifests
and azd state are isolated by subscription, resource group and project. A worker
must complete its real model call and return matching execution references before
the application runs a banking action. OTPs, passcodes, DOBs and mandate contents
are excluded from hosted-worker context. Native voice necessarily processes
spoken synthetic inputs separately. Financial policy and database mutations
remain authoritative in `Bank`, not in model-generated explanations.

`DeployWeb` provisions a single-worker Linux B1 Python App Service, owner-only
single-tenant Entra authentication, a system-managed identity with project-scoped
Foundry User, and an allowlisted source package. It saves only deployment
identifiers to ignored `.env`; credentials remain in Azure app settings.
The source upload is asynchronous: inspect its deployment ID/status and runtime
startup logs before claiming completion. A CLI timeout is an uncertain outcome,
not permission to retry; inspect the existing deployment first.

Open the saved `BANKING_WEB_URL` with `/banking`; use `/.auth/login/aad` for explicit
Microsoft sign-in if needed. Use an external browser when MFA or a security key
cannot complete inside the embedded preview; its signed-in session is not shared
with the preview. `DeployWeb` approves only the site's exact HTTPS origin in Easy
Auth's redirect list so cookie-authenticated browser POSTs can reach the API.
The API still enforces the canonical Origin, a presenter-bound CSRF token and
session ownership. Missing origin approval can produce empty platform refusals
that appear as JSON parsing errors in the browser.
The page reports the API path and HTTP status for empty or malformed replies and
asks for external-browser sign-in when authentication expires; it never treats
an empty response as a successful banking action or retries a mutation automatically.
Only the configured presenter can access customer
sessions. Credit decisions require the authenticated reviewer and record the
actual human object ID. This is still a one-presenter demonstration, not
independently staffed production separation of duties.

**Start microphone** explicitly requests permission and streams AudioWorklet PCM16
to a server-owned native Foundry voice connection. **Connect text-only** does not
use a microphone. Both modes play native audio and display captions/tool results.
Typed messages wait for the current agent turn and its audible playback to finish.
Native PCM can arrive faster than it plays: the browser queues it continuously
with a short jitter buffer instead of ending the call at 15 seconds of queued
speech. Queued audio has an 8 MiB memory bound; speaking to interrupt still clears
playback immediately. **End call** releases microphone
tracks, queued playback, the audio graph and WebSocket. Use current Edge/Chrome
over HTTPS. Permission denial is an explicit error, never a fake voice fallback.
On Windows, `python -m unittest test_voice_browser` checks the real browser client
in headless Edge with deterministic PCM bursts, playback completion and interruption.
This regression does not establish physical speaker or microphone acceptance.

### Guided banking and voice rehearsal

The welcome screen asks the presenter to choose **English**, **Русский**, or
**Azərbaycan dili** before starting a session. The matching native profile
greets the caller after **Start microphone** or **Connect text-only**.
The same real voice connection serves all three banking journeys, not just
lost-card support. Voice availability is reported per language; an unconfigured
profile is explicitly unavailable and never silently replaced by English.
Changing language ends the current call but preserves the banking session,
verification and mandates. Start another call to hear the newly selected voice.
This is explicit language selection, not automatic language detection.

Each journey shows a synthetic caller guide, session milestones derived from
server state, and localized suggested prompts. Selecting a prompt only drafts
it: it does not send text, pass verification, or authorize a transaction.
The prominent caller guide shows the DOB to speak when the agent asks:

| Synthetic caller | Journey | Demo DOB |
|---|---|---|
| Leyla Mammadova | Lost card | 17 April 1992 |
| Dmitri Sokolov | SME lending | 8 February 1974 |
| James Carter | Agentic payments | 21 June 1985 |

After DOB verification, read the current simulated OTP from **Staff & banking
app > SMS-style app messages**, or enter it in **Verify caller**. Codes expire
after three minutes and are not returned to the agent in tool results.
James's synthetic mandate passcode is `246810`, shown beside his DOB in the
caller guide even when manual controls are collapsed. It is not the SMS OTP.
The agent must still obtain confirmation of the displayed limits and merchants;
showing the fixture never fills or confirms the mandate. Lending decisions remain
human-only. All names, dates, accounts and money in these fixtures are synthetic.

`VerifyHosted` and `VerifyIntegration -Live` make billed real Foundry calls.
Integration acceptance uses local ASGI trusted-broker fixtures to exercise
governed journeys and optional recorded PCM -> recognition -> native banking
tool -> hosted worker -> spoken output. It does **not** prove deployed Entra
sign-in, the App Service managed identity, physical microphone hardware, native
AZ/RU pronunciation or venue latency. Presenter-reported sign-in, physical
voice and native-language acceptance is recorded separately above; venue
latency and independent backend execution evidence remain open.

## Demo walkthrough

1. **Within budget:** evaluate one headset from Northstar Supply (£45).
   It is within the default £100 autonomous limit. Select **Simulate purchase**.
1. **Needs approval:** evaluate the £229 monitor. Purchase is unavailable until
   the demo approver acknowledges and approves it. Approval does not charge funds.
1. **Blocked merchant:** the scenario evaluates a £29 Outlet Direct headset.
   The allowlist blocks it despite its lower price. Approval cannot override it.
1. **Hard limits:** evaluate two £349 monitors. The £698 total exceeds the
   £500 per-transaction ceiling and remains blocked.
1. **Reversal:** inspect a purchased order and reverse it. A simulated refund
   restores the original purchase day's available budget.
1. **Policy change:** tighten the policy after a recommendation. Purchase checks
   the current limits, allowlist and UTC daily spend again.

The default UTC daily budget is £1,000. Values use integer pence, inclusive limits
and server-owned prices. SQLite transactions serialise checkout, preventing
parallel requests from overspending or purchasing/refunding an order twice.
Pending requests do not reserve budget, so a later purchase may be blocked.
If current policy changes make a request ineligible, create a new recommendation.

The order ledger, policy and hash-linked audit trail persist in
`data\commerce.db` (gitignored). Stop the server before deleting that **specific
file** for a fresh demo. Audit JSON is available through the UI. The UI shows the
latest 30 events; the export includes all events.

## Architecture and trust boundary

```text
Browser workbench
  -> loopback Python HTTP API
     -> Foundry saved prompt agent -> validated product ID + explanation
     -> deterministic policy engine -> approval / block / ready
     -> simulated purchase or refund
     -> SQLite order state + hash-linked events
```

The agent has no execution tools. Its structured output is checked against the
requested category's server-owned catalog. It cannot provide a new price, grant
approval or bypass policy. The browser never receives an Azure token.

The demo maps the survey's spending limits, merchant restrictions, human approval,
explanations, agent identity, transaction audit trail and reversibility to working
features. It **does not** claim real fraud protection, regulated payment compliance,
merchant interoperability, negotiated pricing, real refunds or verified identity.
No anonymous survey responses are collected or submitted.

This is a **single-user local sandbox**, not a production approval system:

- Operator, policy owner and approver labels are simulated roles in one local session.
  There is no independent approver authentication or separation of duties.
- Host and Origin checks, a per-server CSRF token and a restrictive CSP defend the
  local browser boundary, but are not a replacement for authentication.
- Hash linkage detects edits against the stored chain. A local database owner can
  rewrite the chain or truncate it; it is not immutable or an externally anchored log.
- Use synthetic requests only. Live requests send request text, catalog and policy
  to Azure. `store: false` is set on Responses calls; this is not a guarantee of
  zero service retention. Local orders retain the request and decision explanation.
- Before production, add authenticated actors, independently authorised approvals,
  managed identity, durable externally anchored audit storage, data-retention controls,
  real payment-provider idempotency, fraud checks and deployment hardening.

## Verify

```powershell
.\run.ps1 -Task Test
# Regular Python alternative:
python tests.py
```

The standard-library tests cover both the original procurement flow and the new
local banking domain/API: identity verification and OTP expiry/reuse, customer
isolation, exact mandate boundaries, concurrent/idempotent payments, memo
calculations, human-only decisions, persistence, refusal audit, and handoffs.
They also cover policy boundaries, explicit approval, blocking,
reversals, persistence, invalid inputs, stale policy, concurrent budget enforcement,
CSRF/Host/Origin checks, fail-closed Foundry outputs, `.env` parsing and precedence,
missing-cloud offline behavior, private-value-free errors and publication guards.
They use isolated temporary
databases and mock Azure; they neither consume model tokens nor change cloud resources.

## References

- [Create Foundry projects](https://learn.microsoft.com/azure/foundry/how-to/create-projects)
- [Foundry REST agents and Responses API](https://learn.microsoft.com/rest/api/microsoft-foundry/aiproject)
- [Foundry role-based access control](https://learn.microsoft.com/azure/foundry/concepts/rbac-foundry)