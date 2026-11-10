# Windows 笔记本上分阶段运行本项目。在项目目录的 PowerShell 里执行，例如：
#   .\scripts\Run-LocalChecks.ps1 -Stage Setup     # 建虚拟环境、装依赖（只需一次）
#   .\scripts\Run-LocalChecks.ps1 -Stage Test      # 离线测试，不需要集群和 API Key
#   .\scripts\Run-LocalChecks.ps1 -Stage DryRun    # 真实模型 + 假集群试跑（需要 .env 里的 LLM_API_KEY）
#   .\scripts\Run-LocalChecks.ps1 -Stage Overfit   # bad case 过拟合试点：真实模型 + 假集群，剂量 0/10/40（费用上限 5 美元）
#   .\scripts\Run-LocalChecks.ps1 -Stage DiagCreds # 生成诊断器只读凭据（需要 tiktok-k8s-lab 集群在运行）
#   .\scripts\Run-LocalChecks.ps1 -Stage CheckEnv  # 检查环境健康、诊断器确实只读
#   .\scripts\Run-LocalChecks.ps1 -Stage Smoke     # 第 1 周检查点：只做 健康-注入-确认-复位，不调用模型
# 如果提示"禁止运行脚本"，先执行：Set-ExecutionPolicy -Scope Process Bypass
param(
    [Parameter(Mandatory)][ValidateSet('Setup', 'Test', 'DryRun', 'Overfit', 'DiagCreds', 'CheckEnv', 'Smoke')][string]$Stage,
    [string]$Scenario = 'S01,S03,S07',
    [int]$Rounds = 3,
    [string]$Arms = 'text,code,rule',
    [string]$Doses = '0,10,40',
    [int]$Reps = 2
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Py = Join-Path $Root '.venv\Scripts\python.exe'

function Invoke-Py {
    & $Py @args
    if ($LASTEXITCODE -ne 0) { throw "命令失败（退出码 $LASTEXITCODE）：python $($args -join ' ')" }
}

function Assert-Venv {
    if (-not (Test-Path -LiteralPath $Py)) { throw '还没有虚拟环境，先运行 -Stage Setup' }
}

function Assert-DotEnv {
    if (-not (Test-Path -LiteralPath (Join-Path $Root '.env'))) {
        throw '缺少 .env：复制 windows.env.example 为 .env，并填写 LLM_API_KEY'
    }
}

switch ($Stage) {
    'Setup' {
        if (-not (Test-Path -LiteralPath $Py)) {
            try { & py -3.12 -m venv .venv } catch { Write-Host '没有 py 启动器，改用 python' }
            if (-not (Test-Path -LiteralPath $Py)) { & python -m venv .venv }
            if (-not (Test-Path -LiteralPath $Py)) { throw '创建虚拟环境失败：需要 Python 3.10 以上' }
        }
        Invoke-Py -m pip install -r requirements.txt
        if (-not (Test-Path -LiteralPath (Join-Path $Root '.env'))) {
            Copy-Item 'windows.env.example' '.env'
            Write-Host '已生成 .env，请打开填写 LLM_API_KEY。'
        }
        Write-Host '安装完成。下一步：-Stage Test'
    }
    'Test' {
        Assert-Venv
        Invoke-Py -m pytest
    }
    'DryRun' {
        Assert-Venv; Assert-DotEnv
        Invoke-Py scripts\dry_run_llm.py --arms $Arms
    }
    'Overfit' {
        Assert-Venv; Assert-DotEnv
        Invoke-Py -m triage.experiments.overfit run --backend fake --doses $Doses --arms text,code --reps $Reps
    }
    'DiagCreds' {
        Assert-Venv; Assert-DotEnv
        Invoke-Py scripts\make_diag_kubeconfig.py
    }
    'CheckEnv' {
        Assert-Venv; Assert-DotEnv
        Invoke-Py -m triage.runner check-env
    }
    'Smoke' {
        Assert-Venv; Assert-DotEnv
        Invoke-Py -m triage.runner smoke --scenario $Scenario --rounds $Rounds
    }
}
