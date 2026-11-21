# Deploy Online Boutique v0.10.3 (the application Cloud-OpsBench uses) into namespace "boutique" on the existing
# kind cluster (next to the lab app, if there is one). Only adds things: namespace, workloads, a read-only Role
# for the diagnoser.
#
# Which cluster: -Cluster, else ADMIN_CONTEXT=kind-<name> in .env, else sg-prod-01 (see scripts\_cluster.ps1).
#
#   .\scripts\Setup-Boutique.ps1                  # deploy and wait until every Deployment is ready
#   .\scripts\Setup-Boutique.ps1 -Action status   # show what is there
#
# The kind node pulls the images itself (us-central1-docker.pkg.dev and Docker Hub), about 1.5 GB the first time.
# Safety (same rules as tiktok-k8s-lab AGENTS.md): Docker Desktop is only started normally, never force-killed;
# no WSL shutdown; nothing is deleted.
[CmdletBinding()]
param(
    [ValidateSet('deploy', 'status')][string]$Action = 'deploy',
    [int]$TimeoutMinutes = 20,
    [string]$Cluster = '',          # kind cluster name; empty = from .env / default
    [string]$KindExe = '',          # path to kind.exe; empty = lab copy or kind on PATH
    [string]$AdminKubeconfig = ''   # admin kubeconfig on this machine; empty = lab copy or ~\.kube\config
)
$ErrorActionPreference = 'Continue'   # Windows PowerShell 5.1: native stderr must not become a terminating error
$Repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot '_cluster.ps1')
$cc = Resolve-ClusterConfig $Repo $Cluster $KindExe $AdminKubeconfig
$Cluster = $cc.Cluster; $Kind = $cc.Kind; $labKube = $cc.Kubeconfig
$Ns = 'boutique'
$Manifest = Join-Path $Repo 'boutique\kubernetes-manifests-v0.10.3.yaml'

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Need($ok, $msg) { if (-not $ok) { throw $msg } }

Need ($Kind -and (Test-Path -LiteralPath $Kind)) "kind.exe not found (install kind, put it on PATH, or pass -KindExe)"
Need (Test-Path -LiteralPath $labKube) "admin kubeconfig not found: $labKube (pass -AdminKubeconfig or set ADMIN_KUBECONFIG in .env)"
Need (Test-Path -LiteralPath $Manifest) "manifest not found: $Manifest"

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
    Need ($LASTEXITCODE -eq 0) 'Docker engine is not reachable. Open Docker Desktop normally, wait until it says Engine running, then retry.'
}

Step "kind cluster '$Cluster'"
$clusters = & $Kind get clusters 2>&1 | Where-Object { $_ -is [string] }
Need ($clusters -contains $Cluster) "kind cluster '$Cluster' not found (kind get clusters: $($clusters -join ', ')). Pass -Cluster or set ADMIN_CONTEXT=kind-<name> in .env"
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
Write-Host "cluster: $Cluster   kubeconfig: $labKube"
for ($i = 0; $i -lt 30; $i++) {
    & kubectl --kubeconfig $labKube --context "kind-$Cluster" get --raw /readyz 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 4
}
Need ($LASTEXITCODE -eq 0) 'kind API server did not become ready within 2 minutes'
$nodeInfo = (& docker inspect $node 2>$null | Out-String | ConvertFrom-Json)[0]
Need ($nodeInfo.Config.Labels.'io.x-k8s.kind.cluster' -eq $Cluster) "container $node is not the kind node of cluster '$Cluster'"
$server = (& kubectl --kubeconfig $labKube --context "kind-$Cluster" config view --minify -o "jsonpath={.clusters[0].cluster.server}" 2>$null | Out-String).Trim()
Need ($server -match '^https://(127\.0\.0\.1|localhost):\d+$') "refusing to write: API server '$server' is not a loopback address"

function K { & kubectl --kubeconfig $labKube --context "kind-$Cluster" --request-timeout=60s @args }

function Show-Status {
    Step "pods in namespace $Ns"
    K -n $Ns get pods -o wide 2>&1 | Out-Host
    Step 'node capacity in use (requests / limits)'
    foreach ($n in $allNodes) { Write-Host "-- $n"; K describe node $n 2>&1 | Select-String -Pattern 'Allocated resources' -Context 0, 8 | Out-Host }
    Step 'Docker memory'
    & docker info --format 'CPUs={{.NCPU}} MemTotal={{.MemTotal}}' 2>&1 | Out-Host
    & docker stats --no-stream --format '{{.Name}}: mem {{.MemUsage}} cpu {{.CPUPerc}}' @allNodes 2>&1 | Out-Host
}

if ($Action -eq 'status') { Show-Status; exit 0 }

Step "namespace $Ns"
K get namespace $Ns 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    K create namespace $Ns 2>&1 | Out-Host
    Need ($LASTEXITCODE -eq 0) "could not create namespace $Ns"
}

Step 'applying Online Boutique v0.10.3'
K -n $Ns apply -f $Manifest 2>&1 | Out-Host
Need ($LASTEXITCODE -eq 0) 'kubectl apply failed'

Step "waiting for the 12 Deployments (image pulls can take a while; up to $TimeoutMinutes minutes in total)"
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$names = (K -n $Ns get deployments -o "jsonpath={.items[*].metadata.name}" 2>$null | Out-String).Trim() -split '\s+'
$notReady = @()
foreach ($d in $names) {
    $left = [int][Math]::Max(30, ($deadline - (Get-Date)).TotalSeconds)
    K -n $Ns rollout status "deployment/$d" "--timeout=${left}s" 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { $notReady += $d }
}

Step 'read-only access for the diagnoser'
# rbac-diag.yaml also grants read access in tiktok-k8s-lab; that part is skipped when the lab app is not here
Invoke-ApplyExisting ${function:K} (Join-Path $Repo 'k8s\rbac-diag.yaml')
K apply -f (Join-Path $Repo 'k8s\rbac-diag-boutique.yaml') 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { Write-Host 'WARNING: applying the read-only Role failed' -ForegroundColor Yellow }

Step 'smoke test: home page through the API server proxy'
$page = (K -n $Ns get --raw "/api/v1/namespaces/$Ns/services/frontend:80/proxy/" 2>&1 | Out-String)
$homeOk = ($LASTEXITCODE -eq 0) -and ($page -match 'Online Boutique')
if ($homeOk) { Write-Host 'frontend answered with the Online Boutique home page' -ForegroundColor Green }
else { Write-Host 'frontend did not return the home page' -ForegroundColor Yellow; Write-Host ($page.Substring(0, [Math]::Min(400, $page.Length))) }

Show-Status
if ($notReady.Count -gt 0) {
    Write-Host ("NOT READY: " + ($notReady -join ', ')) -ForegroundColor Red
    Step 'events of pods that are not running'
    K -n $Ns get events --sort-by=.lastTimestamp 2>&1 | Select-Object -Last 25 | Out-Host
    exit 1
}
if (-not $homeOk) { exit 1 }
Write-Host 'Online Boutique is deployed and answering.' -ForegroundColor Green
exit 0
