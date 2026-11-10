# Run the HolmesGPT experiments on this Windows laptop, against the existing kind cluster.
#
#   .\scripts\Run-Laptop.ps1 -Action check                       # first time: cluster + runner image + tests
#   .\scripts\Run-Laptop.ps1 -Action pilot -Scenario R06         # one scenario
#   .\scripts\Run-Laptop.ps1 -Action batch                       # dev split x 3 repeats (about 1-2 hours)
#   .\scripts\Run-Laptop.ps1 -Action batch -Scenarios R06,R09 -Repeats 2 -Tag try1
#
# HolmesGPT and the harness run in a Linux container (docker/runner.Dockerfile) on Docker's "kind" network.
# The LLM key is read from this repo's .env inside the container; it is never printed or passed on a command line.
# Safety (same rules as tiktok-k8s-lab AGENTS.md): Docker Desktop is only started normally, never force-killed;
# no WSL shutdown; no PVC, namespace or cluster deletion.
[CmdletBinding()]
param(
    [ValidateSet('check', 'pilot', 'batch', 'validate', 'shell')][string]$Action = 'check',
    [string]$Scenario = 'R06',
    [string]$Scenarios = '',
    [int]$Repeats = 3,
    [string]$Tag = '',
    [switch]$Rebuild
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Lab = Join-Path (Split-Path -Parent $Repo) 'tiktok-k8s-lab'
$Kind = Join-Path $Lab '.tools\kind.exe'
$Cluster = 'tiktok-k8s-lab'
$Image = 'k8s-sop-runner'

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Need($ok, $msg) { if (-not $ok) { throw $msg } }

Need (Test-Path -LiteralPath $Lab) "tiktok-k8s-lab not found next to this repo: $Lab"
Need (Test-Path -LiteralPath $Kind) "kind.exe not found: $Kind (run tiktok-k8s-lab\scripts\Setup.ps1 first)"
Need (Test-Path -LiteralPath (Join-Path $Repo '.env')) ".env missing in $Repo (copy windows.env.example and fill LLM_API_KEY)"

Step 'Docker Desktop'
& docker info --format '{{.ServerVersion}}' *> $null
if ($LASTEXITCODE -ne 0) {
    & docker desktop start --timeout 120
    & docker info --format '{{.ServerVersion}}' *> $null
    Need ($LASTEXITCODE -eq 0) 'Docker engine is not reachable. Start Docker Desktop normally and retry (do not force-kill it).'
}

Step "kind cluster '$Cluster'"
$clusters = & $Kind get clusters 2>$null
Need ($clusters -contains $Cluster) "kind cluster '$Cluster' not found. In tiktok-k8s-lab run: .\scripts\Setup.ps1; .\scripts\Build.ps1 -Version v1; .\scripts\Build.ps1 -Version v2"

# Images the scenarios need must already be inside the kind node (imagePullPolicy: Never).
$nodeImages = (& docker exec "$Cluster-control-plane" crictl images 2>$null) -join "`n"
foreach ($v in 'v1', 'v2') {
    if ($nodeImages -notmatch "tiktok-k8s-lab\s+$v\b") {
        Step "loading app image $v into kind (tiktok-k8s-lab\scripts\Build.ps1)"
        Push-Location $Lab; try { & (Join-Path $Lab 'scripts\Build.ps1') -Version $v } finally { Pop-Location }
    }
}

# The app must be deployed (Deploy.ps1 is an idempotent apply of the lab manifests + Redis secret).
$labKube = Join-Path $Lab '.local\kubeconfig'
& kubectl --kubeconfig $labKube --context "kind-$Cluster" -n $Cluster get deploy api *> $null
if ($LASTEXITCODE -ne 0) {
    Step 'deploying the lab app (tiktok-k8s-lab\scripts\Deploy.ps1)'
    Push-Location $Lab; try { & (Join-Path $Lab 'scripts\Deploy.ps1') } finally { Pop-Location }
}

Step 'kubeconfig for containers on the kind network'
$local = Join-Path $Repo '.local'
New-Item -ItemType Directory -Force -Path $local | Out-Null
$internal = (& $Kind get kubeconfig --internal --name $Cluster) -join "`n"
Need ($LASTEXITCODE -eq 0 -and $internal) 'kind get kubeconfig --internal failed'
# UTF-8 without BOM: kubectl in the container rejects a BOM.
[System.IO.File]::WriteAllText((Join-Path $local 'admin-internal.kubeconfig'), $internal + "`n", (New-Object System.Text.UTF8Encoding($false)))

& docker image inspect $Image *> $null
if ($Rebuild -or $LASTEXITCODE -ne 0) {
    Step "building runner image '$Image' (first time takes a few minutes)"
    & docker build -t $Image -f (Join-Path $Repo 'docker\runner.Dockerfile') $Repo
    Need ($LASTEXITCODE -eq 0) 'docker build failed'
}

$runArgs = @()
switch ($Action) {
    'check'    { $runArgs = @('check') }
    'pilot'    { $runArgs = @('pilot', $Scenario) }
    'validate' { $runArgs = @('validate', $Scenario) }
    'shell'    { $runArgs = @('shell') }
    'batch'    {
        $runArgs = @('batch', '--repeats', "$Repeats")
        if ($Scenarios) { $runArgs += @('--scenarios', $Scenarios) }
        if ($Tag) { $runArgs += @('--tag', $Tag) }
    }
}
$tty = if ($Action -eq 'shell') { @('-it') } else { @() }

Step "running: $($runArgs -join ' ')"
& docker run --rm @tty --network kind `
    -v "${Repo}:/work" -v "${Lab}:/lab:ro" `
    $Image @runArgs
exit $LASTEXITCODE
