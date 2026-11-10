# Legacy procurement sandbox

This is the original, separate agentic-commerce demonstration.
For the current multilingual banking journeys and native voice, start with the
[main README](../README.md#start-the-banking-demo),
[banking specification](specification.md) and
[banking architecture](architecture.md).

An Azure Foundry prompt agent compares fictional products. A separate
deterministic policy engine decides whether a simulated purchase may proceed.
No payment provider, real merchant, bank account or survey submission is
connected. This sandbox does not use the four hosted banking workers or the
banking voice bridge.

## Run locally

Run these commands from the repository root. Requires Python 3.11+ and, for live
agent calls, Azure CLI signed into the configured tenant. The local app has no
pip or Node dependencies.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# Fill .env with your own COMMERCE_* settings for live calls.
.\run.ps1
# Open http://127.0.0.1:8765
```

The local root `/` opens procurement; `/banking` opens the separate manual
banking demo. With regular Python, `python app.py` also starts the local server.
The runner supports Azure CLI's embedded Python and its isolated import path.
To choose another interpreter:

```powershell
.\run.ps1 -Python "C:\Path\To\python.exe" -Port 8765
```

The server binds only to `127.0.0.1`. Do not expose it through a public tunnel or
use it for real procurement. The explicit **Rule-based** mode runs without Azure
sign-in and selects the cheapest allowed supplier in a category; it does not
interpret the free-text request. Failed live calls surface an error and never
silently switch modes.

## Configure the optional Foundry prompt agent

Use local, gitignored `.env` values or exported environment variables for
`AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID` and `COMMERCE_*`.
See [Azure configuration](../README.md#azure-configuration) for configuration
parsing, identity boundaries and publication safeguards. Banking configuration
is separate and must not be repurposed for this sandbox.

```powershell
az login
.\run.ps1 -Task Provision
```

`Provision` creates the saved agent only if absent. For an existing agent, it
checks the configured version and refuses a definition mismatch instead of
overwriting. Project-scoped **Foundry User** is required for agent operations;
Azure RBAC changes can take time to propagate. Do not broaden role assignments
without reviewing the access.

Local agent calls use an in-memory Entra token obtained from Azure CLI for the
exact configured subscription, with tenant verification. The browser never
receives an Azure token. Live model calls incur Azure usage charges; rule-based
mode does not.

## Procurement walkthrough

1. **Within budget:** Evaluate one headset from Northstar Supply (GBP 45).
   It is within the default GBP 100 autonomous limit. Select **Simulate purchase**.
2. **Needs approval:** Evaluate the GBP 229 monitor. Purchase is unavailable until
   the demo approver acknowledges and approves it. Approval does not charge funds.
3. **Blocked merchant:** Evaluate a GBP 29 Outlet Direct headset.
   The allowlist blocks it despite its lower price. Approval cannot override it.
4. **Hard limits:** Evaluate two GBP 349 monitors. The GBP 698 total exceeds the
   GBP 500 per-transaction ceiling and remains blocked.
5. **Reversal:** Inspect a purchased order and reverse it. A simulated refund
   restores the original purchase day's available budget.
6. **Policy change:** Tighten the policy after a recommendation. Purchase checks
   the current limits, allowlist and UTC daily spend again.

The default UTC daily budget is GBP 1,000. Values use integer pence, inclusive
limits and server-owned prices. SQLite transactions serialize checkout,
preventing parallel requests from overspending or purchasing/refunding an order
twice. Pending requests do not reserve budget, so a later purchase may be
blocked. If current policy changes make a request ineligible, create a new
recommendation.

The order ledger, policy and hash-linked audit trail persist in
`data\commerce.db` (gitignored). Stop the server before deleting that specific
file for a fresh demo. Audit JSON is available through the UI. The UI shows the
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

The prompt agent has no execution tools. Its structured output is checked against
the requested category's server-owned catalog. It cannot provide a new price,
grant approval or bypass policy.

The sandbox maps research themes such as spending limits, merchant restrictions,
human approval, explanations, agent identity, audit and reversibility to working
features. It does not claim real fraud protection, regulated payment compliance,
merchant interoperability, negotiated pricing, real refunds or verified identity.
No anonymous survey responses are collected or submitted.

This is a single-user local sandbox, not a production approval system:

- Operator, policy owner and approver labels are simulated roles in one local
  session, without independent approver authentication or separation of duties.
- Host and Origin checks, a per-server CSRF token and a restrictive CSP defend
  the local browser boundary but do not replace authentication.
- Hash linkage detects edits against the stored chain. A local database owner
  can rewrite or truncate it; it is not immutable or externally anchored.
- Use synthetic requests only. Live requests send request text, catalog and
  policy to Azure. Responses calls set `store: false`, which is not a guarantee
  of zero service retention. Local orders retain requests and explanations.
- Production use would require authenticated actors, independently authorized
  approvals, managed identity, externally anchored audit storage, data-retention
  controls, real payment-provider idempotency, fraud checks and deployment hardening.

## Offline verification

```powershell
.\run.ps1 -Task Test
# Regular Python alternative:
python tests.py
```

The tests cover policy limits, explicit approvals, blocking, reversals,
persistence, invalid inputs, stale policy, concurrent budget enforcement,
CSRF/Host/Origin checks and fail-closed Foundry outputs. They use isolated
temporary databases and mock Azure; they neither consume model tokens nor change
cloud resources. See the [main verification guide](../README.md#offline-verification)
for the shared banking and configuration coverage.
