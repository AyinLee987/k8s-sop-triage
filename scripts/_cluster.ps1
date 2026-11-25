# Shared by Run-Laptop.ps1 and Setup-Boutique.ps1 (dot-sourced): which kind cluster, which kind.exe and which
# admin kubeconfig to use, so the same scripts run on the laptop (cluster tiktok-k8s-lab, kind.exe and kubeconfig
# inside the lab folder) and on the desktop (cluster sg-prod-01, kind on PATH, ~/.kube/config).
#
# Order for each value: script parameter > this repo's .env > what exists on this machine > default.
#   cluster     -Cluster  > ADMIN_CONTEXT=kind-<name> in .env > 'sg-prod-01'
#   kind.exe    -KindExe  > KIND_EXE in .env > <lab>\.tools\kind.exe > kind on PATH
#   kubeconfig  -AdminKubeconfig > .local\clusters\<cluster>\kubeconfig in this repo (a cluster this repo created, such as
#               the second laptop cluster of docs/PARALLEL.md) > ADMIN_KUBECONFIG in .env > <lab>\.local\kubeconfig
#               > $env:KUBECONFIG > ~\.kube\config
# The lab folder (tiktok-k8s-lab next to this repo) is optional: without it only the Online Boutique scenarios run.

function Read-DotEnv([string]$Path) {
    $values = @{}
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') { $values[$Matches[1]] = $Matches[2] }
        }
    }
    return $values
}

function Resolve-ClusterConfig([string]$Repo, [string]$Cluster, [string]$KindExe, [string]$AdminKubeconfig) {
    $dotenv = Read-DotEnv (Join-Path $Repo '.env')
    $lab = Join-Path (Split-Path -Parent $Repo) 'tiktok-k8s-lab'
    $hasLab = Test-Path -LiteralPath (Join-Path $lab 'k8s')

    if (-not $Cluster -and $dotenv['ADMIN_CONTEXT'] -match '^kind-(.+)$') { $Cluster = $Matches[1] }
    if (-not $Cluster) { $Cluster = 'sg-prod-01' }

    if (-not $KindExe) { $KindExe = $dotenv['KIND_EXE'] }
    if (-not $KindExe -and (Test-Path -LiteralPath (Join-Path $lab '.tools\kind.exe'))) { $KindExe = Join-Path $lab '.tools\kind.exe' }
    if (-not $KindExe) { $cmd = Get-Command kind -ErrorAction SilentlyContinue; if ($cmd) { $KindExe = $cmd.Source } }

    $own = Join-Path $Repo ".local\clusters\$Cluster\kubeconfig"
    if (-not $AdminKubeconfig -and (Test-Path -LiteralPath $own)) { $AdminKubeconfig = $own }
    if (-not $AdminKubeconfig) { $AdminKubeconfig = $dotenv['ADMIN_KUBECONFIG'] }
    if (-not $AdminKubeconfig -and (Test-Path -LiteralPath (Join-Path $lab '.local\kubeconfig'))) { $AdminKubeconfig = Join-Path $lab '.local\kubeconfig' }
    if (-not $AdminKubeconfig -and $env:KUBECONFIG) { $AdminKubeconfig = ($env:KUBECONFIG -split ';')[0] }
    if (-not $AdminKubeconfig) { $AdminKubeconfig = Join-Path $HOME '.kube\config' }
    if (-not [System.IO.Path]::IsPathRooted($AdminKubeconfig)) { $AdminKubeconfig = Join-Path $Repo $AdminKubeconfig }

    return [pscustomobject]@{
        Cluster    = $Cluster
        Context    = "kind-$Cluster"
        Kind       = $KindExe
        Kubeconfig = $AdminKubeconfig
        Lab        = $lab
        HasLab     = $hasLab
    }
}

# Apply a manifest, leaving out namespaced objects whose namespace does not exist and is not created by the same
# file. rbac-diag.yaml grants the diagnoser read access in tiktok-k8s-lab; on a cluster without the lab app that
# namespace is absent and a plain apply would fail on those two objects.
function Invoke-ApplyExisting([scriptblock]$Kubectl, [string]$Path) {
    $docs = ((Get-Content -LiteralPath $Path -Raw -Encoding UTF8) -split '(?m)^---\s*$') | Where-Object { $_ -match '(?m)^kind:' }
    $created = @($docs | Where-Object { $_ -match '(?m)^kind:\s*Namespace\s*$' } | ForEach-Object { if ($_ -match '(?m)^\s+name:\s*(\S+)') { $Matches[1] } })
    $keep = @()
    foreach ($doc in $docs) {
        # metadata.namespace: only the indented lines right under "metadata:" (not subjects[].namespace)
        $meta = if ($doc -match '(?m)^metadata:[ \t]*\r?\n((?:[ \t]+.*(?:\r?\n|$))*)') { $Matches[1] } else { '' }
        $ns = if ($meta -match '(?m)^\s+namespace:\s*(\S+)') { $Matches[1] } else { '' }
        if ($ns -and ($created -notcontains $ns)) {
            & $Kubectl get namespace $ns 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $what = if ($doc -match '(?m)^kind:\s*(\S+)') { $Matches[1] } else { 'object' }
                Write-Host "  skipping $what in namespace '$ns' (namespace not on this cluster)"
                continue
            }
        }
        $keep += $doc
    }
    # a temp file, not a pipe: input piped into a wrapper function does not reach the native kubectl inside it
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("apply-" + [guid]::NewGuid().ToString('N') + '.yaml')
    [System.IO.File]::WriteAllText($tmp, ($keep -join "`n---`n"), (New-Object System.Text.UTF8Encoding($false)))
    try { & $Kubectl apply -f $tmp 2>&1 | Out-Host } finally { Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue }
}
