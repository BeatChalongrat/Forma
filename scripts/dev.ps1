# FORMA — Windows PowerShell helper. Usage: .\scripts\dev.ps1 <install|api|web|worker|synth|seed|eval|bench|test|reset>
param([Parameter(Position=0)][string]$cmd = "help")
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$py = Join-Path $backend ".venv\Scripts\python.exe"
$forma = Join-Path $backend ".venv\Scripts\forma.exe"
switch ($cmd) {
  "install" {
    Push-Location $backend; uv venv --python 3.12; uv pip install -e ".[dev]"; Pop-Location
    Push-Location $frontend; npm install; Pop-Location
  }
  "api"   { Push-Location $backend; & $forma serve --reload; Pop-Location }
  "web"   { Push-Location $frontend; npm run dev; Pop-Location }
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
  "test"  { Push-Location $backend; & $py -m pytest; Pop-Location }
  "lint"  { Push-Location $backend; & $py -m ruff check .; & $py -m mypy; Pop-Location }
  "reset" { Remove-Item -Recurse -Force (Join-Path $root "data\forma.db*"), (Join-Path $root "data\files"), (Join-Path $root "data\datasets"), (Join-Path $root "data\exports") -ErrorAction SilentlyContinue }
  default { Write-Host "usage: .\scripts\dev.ps1 install|api|web|worker|synth|seed|eval|eval-gate|bench|bench-short|config-example|prod-up|prod-down|test|lint|reset" }
}
