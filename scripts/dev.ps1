# FORMA — Windows PowerShell helper. Usage: .\scripts\dev.ps1 <install|api|web|synth|seed|eval|test|reset>
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
  "eval"  { Push-Location $backend; & $forma eval --corpus ../data/synthetic --report ../data/eval_report.json; Pop-Location }
  "test"  { Push-Location $backend; & $py -m pytest; Pop-Location }
  "lint"  { Push-Location $backend; & $py -m ruff check .; & $py -m mypy; Pop-Location }
  "reset" { Remove-Item -Recurse -Force (Join-Path $root "data\forma.db*"), (Join-Path $root "data\files"), (Join-Path $root "data\datasets"), (Join-Path $root "data\exports") -ErrorAction SilentlyContinue }
  default { Write-Host "usage: .\scripts\dev.ps1 install|api|web|synth|seed|eval|test|lint|reset" }
}
