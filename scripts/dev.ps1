# FORMA — Windows PowerShell helper. Usage: .\scripts\dev.ps1 <install|api|web|up|down|status|worker|synth|seed|eval|bench|test|reset>
# If scripts are blocked on this machine: powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1 up
param([Parameter(Position=0)][string]$cmd = "help")
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$py = Join-Path $backend ".venv\Scripts\python.exe"
$forma = Join-Path $backend ".venv\Scripts\forma.exe"
$logs = Join-Path $root "data\logs"
$ports = @{ api = 8000; web = 5173 }

function Test-Listening([int]$port) {
  [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}

function Start-Detached([string]$dir, [string]$command, [string]$log) {
  # Created through WMI, so the process is not a child of this shell or of the app that ran it: it keeps
  # running after the window or app closes, until `down` or a restart. Output goes to the log file.
  $startup = New-CimInstance -ClassName Win32_ProcessStartup -Property @{ ShowWindow = [uint16]0 } -ClientOnly
  $line = "cmd.exe /d /s /c `"$command >> `"$log`" 2>&1`""
  $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $line; CurrentDirectory = $dir; ProcessStartupInformation = $startup
  }
  if ($r.ReturnValue -ne 0) { throw "could not start ($($r.ReturnValue)): $command" }
}

function Get-FormaProcesses {
  Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and (
      $_.CommandLine -match 'forma\.cli serve --host 127\.0\.0\.1 --port 8000' -or
      ($_.CommandLine -match 'vite' -and $_.CommandLine -match 'preview' -and $_.CommandLine -match '--port 5173') -or
      ($_.CommandLine -match 'run preview' -and $_.CommandLine -match '--port 5173'))
  }
}

function Show-Status {
  foreach ($name in "api", "web") {
    $port = $ports[$name]
    $state = if (Test-Listening $port) { "running" } else { "stopped" }
    Write-Host ("{0,-4} http://127.0.0.1:{1}  {2}" -f $name, $port, $state)
  }
  Write-Host "logs: $logs"
}

switch ($cmd) {
  "install" {
    Push-Location $backend; uv venv --python 3.12; uv pip install -e ".[dev]"; Pop-Location
    Push-Location $frontend; npm install; Pop-Location
  }
  "api"   { Push-Location $backend; & $forma serve --reload; Pop-Location }
  "web"   { Push-Location $frontend; npm run dev; Pop-Location }
  "up" {
    # API on 8000 and the built web on 5173, in the background. Automatic AI retry (ADR-077) stays off
    # unless FORMA_AI_RETRY_ENABLED is set before running this: it would call the paid model by itself.
    New-Item -ItemType Directory -Force $logs | Out-Null
    $retry = if ($env:FORMA_AI_RETRY_ENABLED) { $env:FORMA_AI_RETRY_ENABLED } else { "false" }
    if (Test-Listening $ports.api) { Write-Host "api: already running on $($ports.api)" }
    else {
      Start-Detached $backend "set `"FORMA_AI_RETRY_ENABLED=$retry`"&& `"$py`" -m forma.cli serve --host 127.0.0.1 --port $($ports.api)" (Join-Path $logs "api.log")
    }
    if (Test-Listening $ports.web) { Write-Host "web: already running on $($ports.web)" }
    else {
      if (-not (Test-Path (Join-Path $frontend "dist\index.html"))) { Push-Location $frontend; npm.cmd run build; Pop-Location }
      Start-Detached $frontend "npm.cmd run preview -- --host 127.0.0.1 --port $($ports.web) --strictPort" (Join-Path $logs "web.log")
    }
    # the first start after a reboot can take a few minutes while Python loads its libraries
    $deadline = (Get-Date).AddMinutes(4)
    while ((Get-Date) -lt $deadline -and -not ((Test-Listening $ports.api) -and (Test-Listening $ports.web))) { Start-Sleep -Seconds 2 }
    Show-Status
  }
  "down" {
    $found = @(Get-FormaProcesses)
    $found | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Host "stopped $($found.Count) process(es)"
    Start-Sleep -Seconds 1
    Show-Status
  }
  "status" { Show-Status }
  # The production stack on Podman (deploy/podman/compose.local.yml). It runs on Podman's root connection:
  # rootless Podman on WSL has no cgroup controllers, so the sandbox's memory and CPU limits cannot apply.
  # podman-compose lives in ~/.forma-tools/podman-compose (pip install podman-compose).
  { $_ -in "podman-up", "podman-down", "podman-status" } {
    $env:CONTAINER_CONNECTION = "podman-machine-default-root"
    $compose = Join-Path $env:USERPROFILE ".forma-tools\podman-compose\Scripts\podman-compose.exe"
    $files = @("-f", "docker-compose.prod.yml", "-f", "deploy/podman/compose.local.yml", "--env-file", ".env.production")
    Push-Location $root
    try {
      if ($cmd -eq "podman-up") {
        podman machine start 2>$null | Out-Null  # already running is fine
        # recreate: Podman will not restart containers whose one-shot jobs (migrate) have exited
        & $compose @files down 2>&1 | Out-Null
        & $compose @files up -d 2>&1 | Select-String -Pattern "Error" | ForEach-Object { Write-Host $_.Line }
      } elseif ($cmd -eq "podman-down") {
        & $compose @files down 2>&1 | Out-Null
      }
      podman ps -a --format "{{.Names}}  {{.Status}}" | Select-String -Pattern "^forma_" | ForEach-Object { Write-Host $_.Line }
      Write-Host "web: http://127.0.0.1:5173"
    } finally { Pop-Location }
  }
  "synth" { Push-Location $backend; & $forma synth --out ../data/synthetic --seed 42 --variants 30; Pop-Location }
  "seed"  { Push-Location $backend; & $forma seed --corpus ../data/synthetic; Pop-Location }
  "worker" { Push-Location $backend; & $forma worker; Pop-Location }
  "bench" { Push-Location $backend; & $forma bench --files 300 --rows 100000 --out ../data/bench; Pop-Location }
  "bench-short" { Push-Location $backend; & $forma bench --files 6 --rows 2000 --distinct 3 --out ../data/bench_short; Pop-Location }
  "eval-gate" { Push-Location $backend; & $forma eval --corpus ../data/synthetic --report ../data/eval_report.json --baseline ../data/eval_baseline.json; Pop-Location }
  "config-example" { Push-Location $backend; & $forma config example --out ../.env.production.example; Pop-Location }
  "prod-up" { docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build }
  "prod-down" { docker compose -f docker-compose.prod.yml --env-file .env.production down }
  "eval"  { Push-Location $backend; & $forma eval --corpus ../data/synthetic --report ../data/eval_report.json; Pop-Location }
  "test"  { Push-Location $backend; & $py -m pytest; Pop-Location; Push-Location $frontend; npm test; Pop-Location }
  "lint"  { Push-Location $backend; & $py -m ruff check .; & $py -m mypy; Pop-Location }
  "reset" { Remove-Item -Recurse -Force (Join-Path $root "data\forma.db*"), (Join-Path $root "data\files"), (Join-Path $root "data\datasets"), (Join-Path $root "data\exports"), (Join-Path $root "data\sandbox") -ErrorAction SilentlyContinue }
  default { Write-Host "usage: .\scripts\dev.ps1 install|api|web|up|down|status|podman-up|podman-down|podman-status|worker|synth|seed|eval|eval-gate|bench|bench-short|config-example|prod-up|prod-down|test|lint|reset" }
}
