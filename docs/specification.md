# Contoso Bank demonstration: implementation specification

Status snapshot: 2026-11-10.

This is the public-safe specification of the implemented banking demonstration.
It replaces neither a private event brief nor a production banking specification.
It records implementation behavior, approved scope changes and outstanding
acceptance evidence without publishing private source documents, tenant details,
deployment identifiers, credentials or customer data.

The implementation is deployed, but the original specification is **not fully
accepted**. Manual acceptance and service-level evidence are recorded separately
from incomplete technical release gates.

## Purpose and scope

Demonstrate Microsoft Foundry native voice and four real hosted agents across
three synthetic banking journeys: lost-card service, SME lending and governed
payments. The presenter can use English, Russian or Azerbaijani, observe banking
actions and transcripts, and demonstrate human handoff and credit review.

All balances, identities, statements, messages, payments and decisions are
synthetic. No real banking rails, SMS delivery, wallet provisioning, production
telephony or regulatory certification are included. The separate procurement
sandbox remains in the source but is not part of this banking acceptance scope.

See the [application architecture](architecture.md) for diagrams and identity
boundaries, and the [README](../README.md) for configuration and operation.

## Functional requirements and implemented behavior

| Requirement | Implemented behavior |
|---|---|
| Guided entry | The presenter selects a language and synthetic customer journey before starting voice. The page exposes journey milestones, verification guidance and staff controls. No event-survey link is shown in the banking UI. |
| Caller verification | Account actions require the synthetic date of birth and a generated six-digit OTP. The OTP is displayed through a simulated in-app SMS channel, expires after three minutes and is consumed on success. Five failed verification attempts lock verification; human handoff remains available. |
| Lost-card services | The card-services journey supports freezing the card, viewing synthetic transactions, replacement to the registered address and a simulated virtual card. Replacement/virtual-card actions require freezing first. A replaced card cannot be reactivated. |
| SME lending | Twelve synthetic monthly statements support an indicative two-page memo. Principal input is 1,000-100,000 AZN; illustrative calculations use a 24-month term, 12% nominal annual interest and a 1.25 debt-service coverage threshold. The memo records evidence and risks, awaits human review and is not a binding credit offer. |
| Human-only credit decisions | Models cannot approve or decline credit. The authenticated reviewer must make the decision, and the application records the reviewer identity. One presenter demonstrates both caller and staff roles; this is not independently staffed separation of duties. |
| Governed payments | A displayed synthetic passcode and explicit confirmation establish a customer-bound mandate: 300 AZN per payment, 500 AZN per UTC calendar month, a 30-day lifetime and only Contoso Insurance or Baku Demo Utilities. The deterministic engine checks the mandate reference, expiry, merchant and remaining allowance before payment. |
| Exceptional requests | A one-off request records an exception request; it never changes or bypasses the mandate. Rejected actions and cross-customer attempts must not mutate another customer's state. |
| Human handoff | Handoff produces a visible queued human task. It does not claim that a real employee has joined the call or that a live contact-centre integration exists. |
| Native voice | Server-owned Foundry Voice Live connections provide native speech, captions and banking function calls. English uses Sonia, Russian Svetlana and Azerbaijani Banu. Explicit microphone and text-only connection modes are available. |
| Language changes | Changing language ends the current native call while retaining the banking session and verification state. The presenter starts a new call for the selected profile. Seamless mid-call switching and automatic language detection are not implemented. |
| Playback and interruption | Browser PCM playback uses a bounded queue and jitter headroom rather than an artificial 15-second call limit. Interruption clears queued playback; ending a call releases microphone and audio resources. Browser regression evidence is not a physical-audio acceptance substitute. |
| Staff transcripts | The staff view shows original transcript lines and English machine translations for non-English text. Long digit sequences are withheld before translation. Translation availability and failures are explicit; separate translation-fidelity acceptance remains open. |
| Hosted execution | Four distinct hosted workers implement orchestration, card services, lending and payments. The live application pins all four to v2; v1 is retained for rollback. Each worker has a dedicated Foundry-managed Entra instance identity. |
| Authoritative policy | Python banking code, not model-generated text, verifies callers and authorizes mutations. Hosted execution context excludes DOBs, OTPs, mandate passcodes and mandate contents. Native voice necessarily processes spoken synthetic inputs separately. |
| Banking tools | A governed MCP gateway and Foundry banking Toolbox validate project identity, delegated grants and server-derived customer/session scope. Discovery and invalid-grant refusal have service evidence; independent positive application-grant tracing remains open. |
| Knowledge grounding | A separate read-only Toolbox and Foundry IQ retrieve trilingual policy evidence from Azure AI Search Free tier. Service probes returned cited answers in all three languages. Local policy fallback identifies itself as local, not IQ. |
| Persistence | SQLite stores application sessions, memos, payments, transcripts and audit records. Hosted v2 additionally stores customer-isolated session files. A hosted session journal retained entries across 450 seconds of inactivity; this is not proof of zero replicas or full hosted lending-memo persistence. |

## Architecture and security requirements

1. The hosted web application requires single-tenant Microsoft Entra sign-in,
   a configured presenter allowlist and HTTPS. App Service Easy Auth validates
   the platform identity before application authorization.
2. The broker enforces canonical origin, presenter-bound CSRF validation,
   session ownership, request limits and reviewer authorization. The Toolbox
   gateway identity is distinct from the presenter and accepted only on the
   gateway route.
3. App Service accesses Foundry through managed identity. Browser clients never
   receive Foundry credentials or access tokens.
4. Strict secretless web sign-in is a separate requirement. Tested provisioning
   code supports a dedicated user-assigned managed identity and federated
   credential, but Azure identity creation currently fails with `TenantNotFound`.
   Working Easy Auth remains unchanged and still references a server-side
   client secret. Strict no-client-secret compliance is not achieved.
5. Models cannot select another customer's scope or override banking policy.
   Hosted files and delegated grants derive customer/session binding on the
   server rather than trusting model-supplied identity.
6. Audio, verification codes and transcript payloads must not enter monitoring.
   Automatic Foundry monitoring violated the approved metadata-only boundary;
   its account- and project-scope App Insights connections were disconnected.
   They must not be reconnected until a metadata-only route is verified and
   approved.
7. Source publication excludes local configuration, cloud outputs, databases,
   private documents and credentials. The [security policy](../SECURITY.md),
   publication guard, offline CI and dependency checks support public sharing;
   they are not a production security certification.

## Acceptance evidence and remaining gates

The following is a dated evidence snapshot, not an assertion that the underlying
cloud state is continuously monitored.

| Area | Evidence as of 2026-11-10 | Remaining acceptance |
|---|---|---|
| Banking policy | Automated regression coverage includes DOB/OTP, customer/session isolation, mandate limits, merchant restrictions and human credit decisions. | Preserve these controls in every deployed path; regression success alone does not establish a specific live Toolbox trace. |
| Signed-in journeys | Presenter reported successful signed-in journey testing in the deployed application. | Manual category accepted; do not reopen it merely because independent instrumentation is absent. |
| Physical/native voice | Native service probes returned transcripts and decoded audio in EN/RU/AZ. Presenter reported physical voice and native-language checks satisfactory. | Manual category accepted. Separate translation-fidelity review, itemized interruption evidence and numeric performance measurements remain open. |
| Hosted identities | Four real hosted workers, distinct identities and live v2 application pins confirmed. | No open deployment-pin gate at this snapshot. |
| Banking Toolbox | Tool discovery and invalid-grant refusal/audit demonstrated. | Capture an independently correlated successful presenter-bound banking action through the real Toolbox and delegated grant. Do not bypass authentication to obtain it. |
| Foundry IQ | Cited answers demonstrated in all three languages at service level. | Capture citations for actual application policy-question paths; service probes do not prove every browser journey used IQ. |
| Hosted persistence | Customer isolation and retained journal entries demonstrated after 450 seconds idle. | Observe zero replicas directly, resume successfully and verify the full two-page hosted lending memo. A 300-second timeout and slower first response only imply scale-down. |
| Policy evaluations | Seven sampled policy questions passed four criteria each: 28 checks. | Full correlated banking-journey tracing and ongoing metadata-only safety monitoring are not accepted. |
| Attack replay | Three model-level cross-customer attacks were refused. | Demonstrate the runtime broker flag in a real governed execution. Model refusal did not exercise the broker because no cross-customer payload reached it. |
| Telemetry containment | Both content-capturing connections removed. No new content rows observed after disconnection. | Observations do not establish safe telemetry under new traffic; metadata-only monitoring remains unimplemented/unaccepted. |
| Captured-content cleanup | Azure accepted three approved scoped purge jobs for 252 content-bearing records. All three were pending at 19:44:01Z; records remained queryable. Content-free operational records are excluded. | Confirm terminal purge completion and count-only absence. Do not resubmit pending jobs or claim deletion before verification. |
| Secretless sign-in | Source implementation and fail-closed readback tests exist. The live identity-creation attempt failed without switching working authentication. | Resolve the Azure platform issue, verify fresh federated sign-in, then retire the legacy credential safely. |
| Performance/rehearsal | Functional playback fixes and presenter voice acceptance exist. | Measure response latency below 1.5 seconds and long-work acknowledgement within 2 seconds; record the measurement convention and venue/network context. Complete itemized barge-in evidence, fallback recordings and event rehearsal. |

## Approved scope changes

- Language choice is explicit; changing language restarts the call while
  preserving banking state.
- The banking survey UI was removed.
- Banking resources are in Sweden Central; approved Free-tier Search in France
  Central is a cross-region exception. Resource location is not proof of
  exclusive in-region model processing.
- The browser application is an authenticated single-presenter demonstration,
  not a production contact centre or multi-staff operational deployment.

## Source verification and release boundaries

Offline regression tests, publication checks and dependency auditing are
documented in the [README](../README.md#public-repository-safeguards).
CI performs source checks without Azure credentials or deployment privileges.
Live verification scripts are separate, explicitly invoked, potentially billed
operations; do not run them automatically in pull-request checks.

Adding or committing this specification does not deploy the application,
complete a cloud purge, enable secretless sign-in or close any remaining
acceptance gate. Keep this document and the architecture in agreement whenever
implementation behavior or accepted evidence changes.
