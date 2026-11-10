param(
    [int]$Port = 8765,
    [string]$Python = "",
    [ValidateSet("Serve", "Test", "TestCloud", "Provision", "ProvisionVoice", "DeployVoice", "VerifyVoice", "DeployBanking", "DeployCloud", "DeployHosted", "VerifyHosted", "DeployWeb", "VerifyIntegration", "CheckPublic")]
    [string]$Task = "Serve",
    [switch]$Apply,
    [switch]$Live,
    [ValidateSet("en-GB", "ru-RU", "az-AZ")]
    [string]$Locale = "en-GB",
    [string]$CallerWav = ""
)
$ErrorActionPreference = "Stop"
if (-not $Python) {
    $AzurePython = Join-Path ${env:ProgramFiles} "Microsoft SDKs\Azure\CLI2\python.exe"
    if (Test-Path $AzurePython) {
        $Python = $AzurePython
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $Python = (Get-Command python).Source
    } else {
        throw "Python 3.11+ is required. Install Python or pass -Python with its executable path."
    }
}
$Script = switch ($Task) {
    "Serve" { "app.py" }
    "Test" { "tests.py" }
    "TestCloud" { "test_cloud.py" }
    "Provision" { "provision.py" }
    "ProvisionVoice" { "provision_banking.py" }
    "VerifyVoice" { "verify_voice.py" }
    "DeployVoice" { "deploy_cloud.py" }
    "DeployBanking" { "deploy_banking.py" }
    "DeployCloud" { "deploy_cloud.py" }
    "CheckPublic" { "check_publication.py" }
    "DeployHosted" { "deploy_hosted.py" }
    "VerifyHosted" { "verify_hosted.py" }
    "DeployWeb" { "deploy_web.py" }
    "VerifyIntegration" { "verify_banking_integration.py" }
}
$Arguments = if ($Task -eq "Serve") { @("--port", "$Port") } else { @() }
if ($Live -and $Task -ne "VerifyIntegration") { throw "-Live is only supported with -Task VerifyIntegration." }
if ($CallerWav -and $Task -ne "VerifyIntegration") { throw "-CallerWav is only supported with -Task VerifyIntegration." }
if ($Task -eq "VerifyIntegration") {
    if (-not $Live) { throw "VerifyIntegration makes billed Foundry calls. Pass -Live to run it." }
    $Arguments = @("--live")
    if ($CallerWav) { $Arguments += @("--caller-wav", $CallerWav) }
}
if ($Task -eq "DeployVoice") { $Arguments = @("--deploy-voice") }
if ($PSBoundParameters.ContainsKey("Locale") -and $Task -notin @("DeployVoice", "VerifyVoice")) {
    throw "-Locale is only supported with -Task DeployVoice or VerifyVoice."
}
if ($Task -in @("DeployVoice", "VerifyVoice")) { $Arguments += @("--locale", $Locale) }
if ($Apply) {
    if ($Task -ne "DeployCloud") { throw "-Apply is only supported with -Task DeployCloud." }
    $Arguments = @("--apply")
}
# Azure CLI's embedded Python omits the script directory from sys.path.
& $Python -c "import sys,runpy,pathlib;root=sys.argv[1];libraries=pathlib.Path(root)/'.azure'/'cloud-python';sys.path[:0]=([str(libraries)] if libraries.is_dir() else [])+[root];sys.argv=sys.argv[2:];runpy.run_path(sys.argv[0],run_name='__main__')" $PSScriptRoot (Join-Path $PSScriptRoot $Script) @Arguments
exit $LASTEXITCODE
