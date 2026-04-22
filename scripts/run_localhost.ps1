$runtimePython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (-not (Test-Path $runtimePython)) {
    Write-Error "No se encontro el runtime Python de Codex en: $runtimePython"
    exit 1
}

$env:PYTHONPATH = "$PSScriptRoot\..\src;$PSScriptRoot\.."
& $runtimePython "$PSScriptRoot\run_localhost.py" @args
