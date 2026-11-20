# Run the HolmesGPT experiments on this Windows machine, against the existing kind cluster.
#
# Which cluster: -Cluster, else ADMIN_CONTEXT=kind-<name> in .env, else sg-prod-01 (see scripts\_cluster.ps1).
# The laptop's .env has ADMIN_CONTEXT=kind-tiktok-k8s-lab, so it keeps using that cluster without any flag.
# Without the tiktok-k8s-lab folder next to this repo the lab app is skipped and only Online Boutique scenarios run.
#
#   .\scripts\Run-Laptop.ps1 -Action check                       # first time: cluster + runner image + tests
#   .\scripts\Run-Laptop.ps1 -Action pilot -Scenario R06         # one scenario
#   .\scripts\Run-Laptop.ps1 -Action batch                       # dev split x 3 repeats (about 1-2 hours)
#   .\scripts\Run-Laptop.ps1 -Action batch -Scenarios R06,R09 -Repeats 2 -Tag try1
#   .\scripts\Run-Laptop.ps1 -Action loop -Scenarios BH01,BH12 -Rounds 3 -MaxSteps 100 -Tag loop-pilot   # diagnose, fix, verify, repeat
#   .\scripts\Run-Laptop.ps1 -Action noise                       # check the ambient noise applies and removes cleanly
#   .\scripts\Run-Laptop.ps1 -Action batch -Noise random -Tag noise1   # same scenarios with ambient noise
#
# HolmesGPT and the harness run in a Linux container (docker/runner.Dockerfile) on Docker's "kind" network.
# The LLM key is read from this repo's .env inside the container; it is never printed or passed on a command line.
# Safety (same rules as tiktok-k8s-lab AGENTS.md): Docker Desktop is only started normally, never force-killed;
# no WSL shutdown; no PVC, namespace or cluster deletion.
[CmdletBinding()]
param(
    [ValidateSet('check', 'pilot', 'batch', 'loop', 'validate', 'noise', 'shell', 'sweep')][string]$Action = 'check',
    [string]$Scenario = 'R06',
    [string]$Scenarios = '',
    [int]$Repeats = 3,
    [string]$Tag = '',
    [string]$Noise = '',        # batch/pilot: none | random | comma-separated ids from scenarios/noise.yaml
    [string]$Skills = '',       # batch/pilot: skill dirs relative to the repo, comma-separated; empty = no SOP
    [string]$Source = '',       # batch: handwritten (default) | generated | all
    [int]$MaxSteps = 0,         # batch/pilot: HolmesGPT step limit; 0 = the scripts' default (25)
    [string]$Frame = '',        # loop: empty = the default framing (a real production incident, judge from evidence only); 'none' = the bare prompt
    [int]$Rounds = 3,           # loop: how many diagnose-fix-verify rounds the agent gets
    [int]$Observe = 0,          # validate: keep each fault this many more seconds and record the frontend's latency meanwhile
    [string]$Cluster = '',          # kind cluster name; empty = from .env / default (scripts\_cluster.ps1)
    [string]$KindExe = '',          # path to kind.exe; empty = lab copy or kind on PATH
    [string]$AdminKubeconfig = '',  # admin kubeconfig on this machine; empty = lab copy or ~\.kube\config
    [switch]$Rebuild
)
# Not 'Stop': in Windows PowerShell 5.1 any stderr line from a native command (docker progress, a failed
# probe) becomes a terminating error under 'Stop'. Every native call below checks $LASTEXITCODE instead.
$ErrorActionPreference = 'Continue'
# 'a,b' typed unquoted reaches a [string] parameter as 'a b'; put the commas back.
$Scenarios = $Scenarios -replace '\s+', ','; $Noise = $Noise -replace '\s+', ','; $Skills = $Skills -replace '\s+', ','
$Repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot '_cluster.ps1')
$cc = Resolve-ClusterConfig $Repo $Cluster $KindExe $AdminKubeconfig
$Cluster = $cc.Cluster; $Kind = $cc.Kind; $labKube = $cc.Kubeconfig; $Lab = $cc.Lab; $HasLab = $cc.HasLab
$Image = 'k8s-sop-runner'

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Need($ok, $msg) { if (-not $ok) { throw $msg } }

Need ($Kind -and (Test-Path -LiteralPath $Kind)) "kind.exe not found (install kind, put it on PATH, or pass -KindExe)"
Need (Test-Path -LiteralPath $labKube) "admin kubeconfig not found: $labKube (pass -AdminKubeconfig or set ADMIN_KUBECONFIG in .env)"
if (-not $HasLab) {
    Write-Host "tiktok-k8s-lab not found next to this repo ($Lab): skipping the lab app; only Online Boutique scenarios will run." -ForegroundColor Yellow
}
Write-Host "cluster: $Cluster   kind: $Kind   kubeconfig: $labKube"
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
Need ($clusters -contains $Cluster) "kind cluster '$Cluster' not found (kind get clusters: $($clusters -join ', ')). Pass -Cluster or set ADMIN_CONTEXT=kind-<name> in .env"

# kind nodes are created with restart=on-failure, so after Docker Desktop restarts the node is often stopped.
# Every node of the cluster (control-plane and workers), not only the control-plane.
$node = "$Cluster-control-plane"
$allNodes = @(& $Kind get nodes --name $Cluster 2>$null | Where-Object { $_ -is [string] -and $_.Trim() })
if ($allNodes.Count -eq 0) { $allNodes = @($node) }
foreach ($n in $allNodes) {
    $running = (& docker inspect -f '{{.State.Running}}' $n 2>&1 | Out-String).Trim()
    if ($running -ne 'true') {
        Step "starting stopped kind node $n (normal docker start)"
        & docker start $n 2>&1 | Out-Host
        Need ($LASTEXITCODE -eq 0) "could not start $n"
    }
}
for ($i = 0; $i -lt 30; $i++) {
    & kubectl --kubeconfig $labKube --context "kind-$Cluster" get --raw /readyz 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 4
}
Need ($LASTEXITCODE -eq 0) 'kind API server did not become ready within 2 minutes'

# Ownership check (same intent as the lab's Assert-LabContext, written so Windows PowerShell 5.1 can run it):
# the node container must carry this cluster's kind label and the API server must be on loopback.
$nodeInfo = (& docker inspect $node 2>$null | Out-String | ConvertFrom-Json)[0]
Need ($nodeInfo.Config.Labels.'io.x-k8s.kind.cluster' -eq $Cluster) "container $node is not the kind node of cluster '$Cluster'"
$server = (& kubectl --kubeconfig $labKube --context "kind-$Cluster" config view --minify -o "jsonpath={.clusters[0].cluster.server}" 2>$null | Out-String).Trim()
Need ($server -match '^https://(127\.0\.0\.1|localhost):\d+$') "refusing to write: API server '$server' is not a loopback address"

if ($HasLab) {
# Images the scenarios need must already be inside the kind node (imagePullPolicy: Never).
$nodeImages = (& docker exec $node crictl images 2>&1 | Out-String)
foreach ($v in 'v1', 'v2') {
    if ($nodeImages -notmatch "tiktok-k8s-lab\s+$v\b") {
        $archive = Join-Path $Lab ".local\images-$v.tar"
        if (-not (Test-Path -LiteralPath $archive)) {
            Step "building app image $v"
            & docker build --platform linux/amd64 --target runtime --build-arg "APP_VERSION=$v" -t "tiktok-k8s-lab:$v" $Lab
            Need ($LASTEXITCODE -eq 0) "docker build of tiktok-k8s-lab:$v failed"
            & docker pull --platform linux/amd64 redis:7.4.2-alpine
            Need ($LASTEXITCODE -eq 0) 'docker pull redis failed'
            & docker image save --platform linux/amd64 --output $archive "tiktok-k8s-lab:$v" redis:7.4.2-alpine
            Need ($LASTEXITCODE -eq 0) 'docker image save failed'
        }
        Step "loading app image $v into the kind node"
        & $Kind load image-archive $archive --name $Cluster 2>&1 | Out-Host
        Need ($LASTEXITCODE -eq 0) "kind load of $archive failed"
    }
}

# The app must be deployed and healthy. Applying the lab manifests is idempotent.
function K { & kubectl --kubeconfig $labKube --context "kind-$Cluster" -n $Cluster --request-timeout=30s @args }
K get deploy api 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Step 'deploying the lab app'
    $pw = Join-Path $Lab '.local\redis-password.txt'
    $conf = Join-Path $Lab '.local\redis-auth.conf'
    Need ((Test-Path -LiteralPath $pw) -and (Test-Path -LiteralPath $conf)) 'Redis password files missing in tiktok-k8s-lab\.local (run its Setup.ps1 once)'
    K apply -f (Join-Path $Lab 'k8s\namespace.yaml') | Out-Host
    K get secret redis-auth 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        # values come from files; nothing secret is printed
        K create secret generic redis-auth "--from-file=password=$pw" "--from-file=redis-auth.conf=$conf" | Out-Host
        Need ($LASTEXITCODE -eq 0) 'creating the redis-auth secret failed'
    }
    K apply -k (Join-Path $Lab 'k8s') | Out-Host
    Need ($LASTEXITCODE -eq 0) 'kubectl apply -k failed'
}
Step 'waiting for redis, api, worker'
foreach ($w in 'statefulset/redis', 'deployment/api', 'deployment/worker') {
    K rollout status $w --timeout=180s 2>&1 | Out-Host
    Need ($LASTEXITCODE -eq 0) "$w is not ready"
}
}   # end if ($HasLab): lab images and lab app

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

# api variants for the "slow but healthy" scenarios (L01-L03): v3 = request log, v3.1 = v3 + a code defect.
# They are derived from a copy of the lab's source (lab_variants/make_variants.py); the lab folder is only read.
$nodeImages = (& docker exec $node crictl images 2>&1 | Out-String)
if ($HasLab -and ($Rebuild -or $nodeImages -notmatch 'tiktok-k8s-lab\s+v3\s' -or $nodeImages -notmatch 'tiktok-k8s-lab\s+v3\.1\s')) {
    Step 'building api variants v3 and v3.1'
    & docker image inspect tiktok-k8s-lab:v1 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        & docker load --input (Join-Path $Lab '.local\images-v1.tar') | Out-Host
        Need ($LASTEXITCODE -eq 0) 'docker load of the v1 image failed'
    }
    & docker run --rm -v "${Repo}:/work" -v "${Lab}:/lab:ro" --entrypoint python $Image /work/lab_variants/make_variants.py /lab /work/.local/variants
    Need ($LASTEXITCODE -eq 0) 'make_variants.py failed (has the lab source changed?)'
    $vdir = Join-Path $local 'variants'
    $dockerfile = "FROM tiktok-k8s-lab:v1`nARG APP_VERSION`nENV APP_VERSION=`$APP_VERSION`nCOPY app ./app`n"
    foreach ($v in 'v3', 'v3.1') {
        [System.IO.File]::WriteAllText((Join-Path $vdir "$v\Dockerfile"), $dockerfile, (New-Object System.Text.UTF8Encoding($false)))
        & docker build --platform linux/amd64 --build-arg "APP_VERSION=$v" -t "tiktok-k8s-lab:$v" (Join-Path $vdir $v)
        Need ($LASTEXITCODE -eq 0) "docker build of tiktok-k8s-lab:$v failed"
    }
    $varArchive = Join-Path $vdir 'images-variants.tar'
    & docker image save --output $varArchive tiktok-k8s-lab:v3 tiktok-k8s-lab:v3.1
    Need ($LASTEXITCODE -eq 0) 'docker image save of the variants failed'
    & $Kind load image-archive $varArchive --name $Cluster 2>&1 | Out-Host
    Need ($LASTEXITCODE -eq 0) 'kind load of the variants failed'
}

$runArgs = @()
switch ($Action) {
    'check'    { $runArgs = @('check') }
    'pilot'    { $runArgs = @('pilot', $Scenario); if ($Noise) { $runArgs += @('--noise', $Noise) }; if ($Skills) { $runArgs += @('--skills', $Skills) }; if ($MaxSteps -gt 0) { $runArgs += @('--max-steps', "$MaxSteps") } }
    'noise'    { $runArgs = @('noise') }
    'validate' { $runArgs = @('validate'); if ($Scenarios) { $runArgs += ($Scenarios -split ',') } else { $runArgs += $Scenario }; if ($Observe -gt 0) { $runArgs += @('--observe', "$Observe") } }
    'loop'     { $runArgs = @('loop'); if ($Scenarios) { $runArgs += ($Scenarios -split ',') } else { $runArgs += $Scenario }; $runArgs += @('--rounds', "$Rounds"); if ($Frame) { $runArgs += @('--frame', $Frame) }; if ($Tag) { $runArgs += @('--tag', $Tag) }; if ($Skills) { $runArgs += @('--skills', $Skills) }; if ($MaxSteps -gt 0) { $runArgs += @('--max-steps', "$MaxSteps") } }
    'shell'    { $runArgs = @('shell') }
    'sweep'    { $runArgs = @('sweep') }
    'batch'    {
        $runArgs = @('batch', '--repeats', "$Repeats")
        if ($Scenarios) { $runArgs += @('--scenarios', $Scenarios) }
        if ($Tag) { $runArgs += @('--tag', $Tag) }
        if ($Noise) { $runArgs += @('--noise', $Noise) }
        if ($Skills) { $runArgs += @('--skills', $Skills) }
        if ($Source) { $runArgs += @('--source', $Source) }
        if ($MaxSteps -gt 0) { $runArgs += @('--max-steps', "$MaxSteps") }
    }
}
$tty = if ($Action -eq 'shell') { @('-it') } else { @() }

# One run at a time. Ctrl+C in this window stops the docker client, not the container: the run goes on in the
# background, and a second run on the same cluster mixes two scenarios' faults (2026-11-17).
if ($Action -ne 'shell') {
    $active = @(& docker ps -q --filter "ancestor=$Image")
    if ($active.Count -gt 0) {
        throw "another run is still active (container $($active -join ', ')). Stop it first with: docker stop $($active -join ' ')"
    }
}
$mounts = @('-v', "${Repo}:/work")
if ($HasLab) { $mounts += @('-v', "${Lab}:/lab:ro") }
Step "running: $($runArgs -join ' ')"
& docker run --rm @tty --network kind @mounts -e "ADMIN_CONTEXT=kind-$Cluster" `
    $Image @runArgs
exit $LASTEXITCODE
