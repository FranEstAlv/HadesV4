# aplicar_proxy.ps1  -- reemplaza SOLO las variables de proxy del .env de la maquina 2
# Uso:  powershell -ExecutionPolicy Bypass -File aplicar_proxy.ps1
#
# Requiere: variables-proxy.env en la misma carpeta (o en ..\evidencias\_review_2026-10-22\)

$ErrorActionPreference = "Stop"

# --- locating the project root (folder with .env) -------------------------------
$raiz = $null
foreach ($cand in @($PSScriptRoot, (Split-Path -Parent $PSScriptRoot), (Get-Location).Path)) {
    if ($cand -and (Test-Path (Join-Path $cand ".env"))) { $raiz = $cand; break }
}
if (-not $raiz) { Write-Host "ERROR: no encuentro el .env del proyecto" -ForegroundColor Red; exit 1 }
Write-Host "Proyecto: $raiz" -ForegroundColor Cyan

# --- locating the new variables -------------------------------------------------
$vars = $null
foreach ($cand in @(
    (Join-Path $PSScriptRoot "variables-proxy.env"),
    (Join-Path $raiz "variables-proxy.env"),
    (Join-Path $PSScriptRoot "..\evidencias\_review_2026-10-22\variables-proxy.env"),
    (Join-Path $raiz "..\evidencias\_review_2026-10-22\variables-proxy.env")
)) {
    if (Test-Path $cand) { $vars = (Resolve-Path $cand).Path; break }
}
if (-not $vars) { Write-Host "ERROR: no encuentro variables-proxy.env" -ForegroundColor Red; exit 1 }
Write-Host "Variables: $vars" -ForegroundColor Cyan

# --- backup --------------------------------------------------------------------
$envPath = Join-Path $raiz ".env"
$bak = "$envPath.bak.$(Get-Date -Format yyyyMMdd-HHmmss)"
Copy-Item $envPath $bak -Force
Write-Host "Backup: $bak" -ForegroundColor Yellow

# --- new blocks (multi-line, between quotes) ------------------------------------
$nuevas = @()
$enVar = $false
foreach ($l in Get-Content $vars) {
    if ($l -match '^(PROXY_ROTATORY|PROXY_STICKY_MX|PROXY_STICKY_USA_3_MINUTES)=') { $enVar = $true }
    if ($enVar) {
        $nuevas += $l
        if ($l.TrimEnd().EndsWith('"')) { $enVar = $false }
    }
}
if (-not $nuevas) { Write-Host "ERROR: variables-proxy.env vacio" -ForegroundColor Red; exit 1 }

# --- removing the old blocks from .env ------------------------------------------
$viejas = @("PROXY_ROTATORY", "PROXY_STICKY_MX", "PROXY_STICKY_USA_3_MINUTES")
$salida = @()
$omitiendo = $false
foreach ($l in Get-Content $envPath) {
    $esVieja = $false
    foreach ($v in $viejas) {
        if ($l -match "^$v=") { $esVieja = $true; break }
    }
    if ($esVieja) {
        if ($l.TrimEnd().EndsWith('"')) { $omitiendo = $false } else { $omitiendo = $true }
        continue
    }
    if ($omitiendo) {
        if ($l.TrimEnd().EndsWith('"')) { $omitiendo = $false }
        continue
    }
    $salida += $l
}

# --- writing --------------------------------------------------------------------
$salida += ""
$salida += "# ---- proxy variables (updated) ----"
$salida += $nuevas
Set-Content -Path $envPath -Value $salida -Encoding UTF8

Write-Host ""
Write-Host "OK. Variables written to .env" -ForegroundColor Green
Write-Host ""
Write-Host "Verifying:"
Write-Host "  python prueba_iproyal.py --env .env"
