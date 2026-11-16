<#
.SYNOPSIS
  Runs the CI checks on this machine, so a branch can be verified without GitHub Actions minutes.

.DESCRIPTION
  It runs the same commands as .github/workflows/ci.yml for the stages that can run locally, prints one line per stage and a summary, and exits
  non-zero if any stage failed. It is NOT a replacement for CI: the container image builds and scans (backend-image, frontend-image) and the
  nightly advisory audits are not run here, and a local pass does not make a pull request mergeable under a branch rule that requires CI.

.PARAMETER Stages
  Which stages to run. Default: all. Names: repo, lint, types, unit, component, secrets, controls, frontend.

.EXAMPLE
  powershell -File tools\scripts\ci_local.ps1
  powershell -File tools\scripts\ci_local.ps1 -Stages repo,secrets,controls
#>
param([string[]]$Stages = @("repo", "lint", "types", "unit", "component", "secrets", "controls", "frontend"))

$Stages = @($Stages | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })  # -File passes a comma list as one string
$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$py = Join-Path $root "backend\.venv\Scripts\python.exe"
$results = [ordered]@{}

function Step([string]$name, [scriptblock]$body) {
    if ($Stages -notcontains $name) { return }
    Write-Host "`n=== $name ===" -ForegroundColor Cyan
    $start = Get-Date
    $global:LASTEXITCODE = 0
    try { & $body } catch { Write-Host $_.Exception.Message -ForegroundColor Red; $global:LASTEXITCODE = 1 }
    $secs = [int]((Get-Date) - $start).TotalSeconds
    $results[$name] = [pscustomobject]@{ Ok = ($global:LASTEXITCODE -eq 0); Seconds = $secs }
}

if (-not (Test-Path $py)) { Write-Host "Missing $py. Create the backend environment first (uv sync)." -ForegroundColor Red; exit 2 }

# The repository and secret checks look at every file in the folder. CI looks at a clean checkout, so these two stages run on a clean copy of HEAD:
# untracked local files (a .env, local keys, build folders) are not part of what is pushed and must not fail the check.
function WithCleanCheckout([scriptblock]$body) {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("zid-ci-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    git -C $root worktree add --detach $tmp HEAD | Out-Null
    try { Push-Location $tmp; & $body $tmp } finally { Pop-Location; git -C $root worktree remove --force $tmp | Out-Null }
}

Step "repo" {
    WithCleanCheckout { param($dir) & $py tools/scripts/check_repo.py }
}

Step "lint" {
    Push-Location (Join-Path $root "backend")
    & $py -m ruff check .
    if ($LASTEXITCODE -eq 0) { & $py -m ruff format --check . }
    Pop-Location
}

Step "types" {
    Push-Location (Join-Path $root "backend")
    & $py -m mypy app/src/zid_platform assistant/src
    Pop-Location
}

Step "unit" {
    Push-Location (Join-Path $root "backend\app")
    & $py -m pytest tests/unit tests/contract -q -p no:cacheprovider -p no:logging
    Pop-Location
}

Step "component" {
    # The component tests need a disposable Postgres. Start one on port 56432 if it is not already running.
    $docker = (Get-Command docker -ErrorAction SilentlyContinue)
    if (-not $docker) { Write-Host "Docker is not available; the component stage needs it." -ForegroundColor Red; $global:LASTEXITCODE = 1; return }
    $running = docker ps --filter "name=zid-ledger-test" --format "{{.Names}}"
    if (-not $running) {
        docker run -d --rm --name zid-ledger-test -e POSTGRES_PASSWORD=test -p 56432:5432 postgres:16-alpine | Out-Null
        Start-Sleep -Seconds 12
    }
    $env:ZID_TEST_PG_URL = "postgresql+asyncpg://postgres:test@localhost:56432/postgres"
    Push-Location (Join-Path $root "backend\app")
    & $py -m pytest tests/component -q -p no:cacheprovider -p no:logging
    Pop-Location
}

Step "secrets" {
    WithCleanCheckout { param($dir) & $py tools/scripts/secret_scan.py }
}

Step "controls" {
    Push-Location (Join-Path $root "backend")
    & $py -m pytest app/tests/unit/test_devsecops_controls.py app/tests/unit/test_ratification.py -q -p no:logging -p no:cacheprovider
    if ($LASTEXITCODE -eq 0) { Push-Location $root; & $py tools/scripts/ratification.py --check; Pop-Location }
    Pop-Location
}

Step "frontend" {
    Push-Location (Join-Path $root "frontend")
    npm run lint
    if ($LASTEXITCODE -eq 0) { npm run typecheck }
    if ($LASTEXITCODE -eq 0) { npm run test }
    if ($LASTEXITCODE -eq 0) { npm run build }
    Pop-Location
}

Write-Host "`n=== summary ===" -ForegroundColor Cyan
$failed = 0
foreach ($k in $results.Keys) {
    $r = $results[$k]
    $mark = if ($r.Ok) { "PASS" } else { "FAIL"; $failed++ }
    $colour = if ($r.Ok) { "Green" } else { "Red" }
    Write-Host ("{0,-10} {1}  ({2}s)" -f $k, $mark, $r.Seconds) -ForegroundColor $colour
}
Write-Host "`nNote: the types stage can fail locally on the assistant package if its libraries (for example valkey) are not installed in backend\.venv; CI installs every package with uv sync --all-packages." -ForegroundColor Yellow
Write-Host "`nNot run here: container image builds and scans, dependency audits, and the nightly advisories. A local pass does not replace CI." -ForegroundColor Yellow
if ($failed -gt 0) { exit 1 } else { exit 0 }
