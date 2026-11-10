# Security policy

## Supported scope

This repository is a synthetic banking demonstration, not production banking
software. Security fixes target the latest source on the default branch;
there are no supported versioned releases or response-time guarantees.
Never use real customer records, financial credentials or payment instructions.

The local server is loopback-only and is not an authenticated public service.
Use the authenticated App Service deployment for hosted demonstrations.
Do not bypass its platform authentication or expose the local server with a tunnel.

## Reporting a vulnerability

Use the repository's **Security > Advisories > Report a vulnerability** option
when private vulnerability reporting is enabled. If that option is unavailable,
use a private contact method published by the repository owner on GitHub to
request a secure reporting channel before sending technical details.
If no private channel is available, do not disclose the vulnerability in a
public issue, pull request or discussion.

Include affected file paths and versions, impact, and minimal reproduction
steps using synthetic data. Do not send live credentials, access tokens,
customer records, voice recordings or transcript payloads.

For an exposed credential, revoke or rotate it immediately. Removing a file
or adding an ignore rule does not remove a credential from Git history.
Coordinate history cleanup and notification with the repository owner privately.

## Publication and dependency safeguards

Run the publication guard, offline tests and dependency audit documented in
the README before sharing. Publish normal Git commits, not a whole working-folder
archive, `.git` directory or mirror push. Local configuration, databases,
deployment outputs and recovery refs are not public artifacts.

CI checks known dependency advisories and common secret patterns; neither is
a comprehensive security assessment. CI must not receive Azure credentials,
production data or transcript content, and it must not deploy the application.
Cloud monitoring must remain disconnected until a metadata-only route is
verified and approved; source checks do not prove cloud retention or deletion.
