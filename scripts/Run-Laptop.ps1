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
# Not 'Stop': in Windows PowerShell 5.1 any stderr line from a native command (docker progress, a failed
# probe) becomes a terminating error under 'Stop'. Every native call below checks $LASTEXITCODE instead.
$ErrorActionPreference = 'Continue'
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
& docker info --format '{{.ServerVersion}}' 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Docker engine not running; starting Docker Desktop (normal start, up to 3 minutes)...'
    & docker desktop start --timeout 180 2>&1 | Out-Host
    for ($i = 0; $i -lt 36; $i++) {
        & docker info --format '{{.ServerVersion}}' 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 5
    }
    Need ($LASTEXITCODE -eq 0) 'Docker engine is not reachable. Open Docker Desktop normally, wait until it says Engine running, then retry (do not force-kill it).'
}

Step "kind cluster '$Cluster'"
$clusters = & $Kind get clusters 2>&1 | Where-Object { $_ -is [string] }
Need ($clusters -contains $Cluster) "kind cluster '$Cluster' not found. In tiktok-k8s-lab run: .\scripts\Setup.ps1; .\scripts\Build.ps1 -Version v1; .\scripts\Build.ps1 -Version v2"

# kind nodes are created with restart=on-failure, so after Docker Desktop restarts the node is often stopped.
$node = "$Cluster-control-plane"
$running = (& docker inspect -f '{{.State.Running}}' $node 2>&1 | Out-String).Trim()
if ($running -ne 'true') {
    Step "starting stopped kind node $node (normal docker start)"
    & docker start $node 2>&1 | Out-Host
    Need ($LASTEXITCODE -eq 0) "could not start $node"
}
$labKube = Join-Path $Lab '.local\kubeconfig'
for ($i = 0; $i -lt 30; $i++) {
    & kubectl --kubeconfig $labKube --context "kind-$Cluster" get --raw /readyz 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 4
}
Need ($LASTEXITCODE -eq 0) 'kind API server did not become ready within 2 minutes'

# The lab's own scripts are written for PowerShell 7 (Windows PowerShell 5.1 drops the quotes inside
# docker --format templates, which breaks their ownership check), so call them through pwsh.
$pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
function Invoke-LabScript([string]$name, [string[]]$extra = @()) {
    Need $pwsh 'PowerShell 7 (pwsh) is required for the tiktok-k8s-lab scripts: winget install Microsoft.PowerShell'
    Push-Location $Lab
    try { & $pwsh.Source -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Lab "scripts\$name") @extra } finally { Pop-Location }
    Need ($LASTEXITCODE -eq 0) "$name failed"
}

# Images the scenarios need must already be inside the kind node (imagePullPolicy: Never).
$nodeImages = (& docker exec $node crictl images 2>&1 | Out-String)
foreach ($v in 'v1', 'v2') {
    if ($nodeImages -notmatch "tiktok-k8s-lab\s+$v\b") {
        Step "loading app image $v into kind (tiktok-k8s-lab\scripts\Build.ps1)"
        Invoke-LabScript 'Build.ps1' @('-Version', $v)
    }
}

# The app must be deployed (Deploy.ps1 is an idempotent apply of the lab manifests + Redis secret).
& kubectl --kubeconfig $labKube --context "kind-$Cluster" -n $Cluster get deploy api 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Step 'deploying the lab app (tiktok-k8s-lab\scripts\Deploy.ps1)'
    Invoke-LabScript 'Deploy.ps1'
}

Step 'kubeconfig for containers on the kind network'
$local = Join-Path $Repo '.local'
New-Item -ItemType Directory -Force -Path $local | Out-Null
$internal = (& $Kind get kubeconfig --internal --name $Cluster 2>$null) -join "`n"
Need ($LASTEXITCODE -eq 0 -and $internal) 'kind get kubeconfig --internal failed'
# UTF-8 without BOM: kubectl in the container rejects a BOM.
[System.IO.File]::WriteAllText((Join-Path $local 'admin-internal.kubeconfig'), $internal + "`n", (New-Object System.Text.UTF8Encoding($false)))

& docker image inspect $Image 2>&1 | Out-Null
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
