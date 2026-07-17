param(
    [switch]$Integration
)

$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param([scriptblock]$Command)

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE"
    }
}

Write-Host "[1/3] Verifying backend"
Push-Location "$PSScriptRoot\..\backend"
try {
    Invoke-Checked { uv sync --locked }
    Invoke-Checked { uv run ruff check . }
    Invoke-Checked { uv run ruff format --check . }
    Invoke-Checked { uv run mypy app }
    Invoke-Checked { uv run pytest -m "not integration" }
    if ($Integration) {
        if (-not $env:TEST_DATABASE_URL) {
            throw "TEST_DATABASE_URL is required when -Integration is used"
        }
        $env:DATABASE_URL = $env:TEST_DATABASE_URL
        Invoke-Checked { uv run alembic upgrade head }
        Invoke-Checked { uv run pytest -m integration }
    }
}
finally {
    Pop-Location
}

Write-Host "[2/3] Verifying frontend"
Push-Location "$PSScriptRoot\..\frontend"
try {
    Invoke-Checked { npm ci }
    Invoke-Checked { npm run lint }
    Invoke-Checked { npm run test:unit -- --run }
    Invoke-Checked { npm run build }
}
finally {
    Pop-Location
}

Write-Host "[3/3] Verifying Docker Compose"
Invoke-Checked { docker compose --env-file "$PSScriptRoot\..\.env.example" -f "$PSScriptRoot\..\compose.yaml" config --quiet }
Write-Host "All checks passed."
