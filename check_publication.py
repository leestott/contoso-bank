"""Check intended public files/history without displaying matched private values."""

import re
import subprocess
from pathlib import Path

from settings import ROOT, read_environment

PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "access token": re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "embedded credential": re.compile(
        r"""(?i)(?:api[_-]?key|client[_-]?secret|password)\s*[:=]\s*["'][A-Za-z0-9+/_.=-]{20,}"""),
    "personal document link": re.compile(r"""https://[^/\s"'<>]+\.sharepoint\.com/""", re.I),
    "personal survey link": re.compile(r"""https://forms\.cloud\.microsoft/[^\s"'<>]*[?&]id=""", re.I),
    "tenant sign-in identity": re.compile(r"""[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.onmicrosoft\.com""", re.I),
    "embedded deployment endpoint": re.compile(
        r"https://(?!(?:example|test-account)\.services\.)[a-z0-9-]+\.services\.ai\.azure\.com", re.I),
}


def private_values(environment):
    return {value for key, value in environment.items() if len(value) >= 8 and (
        key in {"AZURE_SUBSCRIPTION_ID", "AZURE_TENANT_ID", "DEMO_SURVEY_URL",
                "CLOUD_EXPECTED_ORIGIN", "CLOUD_AUTH_TENANT", "CLOUD_ALLOWED_PRINCIPAL_IDS",
                "CLOUD_REVIEWER_IDS", "CLOUD_CSRF_SECRET", "BANKING_AUTH_SECRET"}
        or key.endswith(("_SUBSCRIPTION_ID", "_TENANT_ID", "_RESOURCE_GROUP", "_FOUNDRY_ACCOUNT", "_FOUNDRY_PROJECT",
                         "_FOUNDRY_ENDPOINT", "_MODEL_DEPLOYMENT", "_AGENT_NAME",
                         "_WEB_APP", "_WEB_URL", "_WEB_CLIENT_ID")))}


def findings(text, private):
    result = [name for name, pattern in PATTERNS.items() if pattern.search(text)]
    if any(value in text for value in private):
        result.append("local deployment value")
    return result


def private_path(name):
    path = Path(name.replace("\\", "/"))
    return (any(part in {"data", ".azure", ".azd", ".git", ".venv", "__pycache__"}
                for part in path.parts)
            or (path.name.startswith(".env") and path.name != ".env.example")
            or path.name.endswith(".local.json")
            or path.suffix.lower() in {".pem", ".key", ".pfx", ".p12", ".db", ".sqlite", ".sqlite3", ".log"}
            or ".db-" in path.name)


def git(*arguments, input_text=None):
    return subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True,
                          input=input_text.encode() if input_text is not None else None,
                          check=True).stdout.decode("utf-8", errors="replace")


def main():
    private = private_values(read_environment())
    problems = []
    names = set(git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")) - {""}
    for name in sorted(names):
        path = ROOT / Path(name)
        if private_path(name):
            problems.append(f"{name}: private file is eligible for publication")
        elif path.is_file():
            for finding in findings(path.read_bytes().decode("utf-8", errors="replace"), private):
                problems.append(f"{name}: {finding}")
    objects = git("rev-list", "--objects", "--branches", "--remotes").splitlines()
    object_ids = "".join(line.split(" ", 1)[0] + "\n" for line in objects)
    if object_ids:
        for result in git("cat-file", "--batch-check", input_text=object_ids).splitlines():
            object_id, kind, _ = result.split()
            if kind == "blob":
                for finding in findings(git("cat-file", "blob", object_id), private):
                    problems.append(f"branch/remote history: {finding}")
    for name in set(git("log", "--branches", "--remotes", "--format=", "--name-only", "-m").splitlines()) - {""}:
        if private_path(name):
            problems.append(f"branch/remote history: private file {name}")
    checkpoints = git("for-each-ref", "--format=%(refname)", "refs/copilot/checkpoints").splitlines()
    if checkpoints:
        print("Warning: local recovery refs exist; never mirror-push refs or publish .git.")
    for problem in problems:
        print(problem)
    if problems:
        print("Publication check failed. No matching private values were printed.")
        return 1
    print(f"Checked {len(names)} public working files and branch/remote history; no configured private values or credential patterns found.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Publication check could not complete ({type(exc).__name__}).")
        raise SystemExit(1)
