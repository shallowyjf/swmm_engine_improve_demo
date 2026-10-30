param(
    [string]$SwmmExe = "",
    [string]$OutputDir = $PSScriptRoot
)

$ErrorActionPreference = "Stop"

$baseDir = [System.IO.Path]::GetFullPath($OutputDir)
$generator = Join-Path $PSScriptRoot "generate_large_city_benchmark.py"
if (-not $SwmmExe) {
    $engineDir = Join-Path (Split-Path -Parent $PSScriptRoot) "swmm524_engine"
    $exeName = if ($env:OS -eq "Windows_NT") { "runswmm.exe" } else { "runswmm" }
    $candidates = @(
        (Join-Path $engineDir "build_vs/Release/$exeName"),
        (Join-Path $engineDir "build/Release/$exeName"),
        (Join-Path $engineDir "build/$exeName")
    )
    $SwmmExe = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $SwmmExe -or -not (Test-Path -LiteralPath $SwmmExe -PathType Leaf)) {
    throw "SWMM executable not found. Build the engine or pass -SwmmExe."
}

python $generator --generate-only --output-dir $baseDir
if ($LASTEXITCODE -ne 0) { throw "Benchmark input generation failed." }

$routes = @("DW", "KW", "SF")
$benchmark = @()

foreach ($route in $routes) {
    $inp = Join-Path $baseDir ("large_city_10k_{0}.inp" -f $route)
    $rpt = Join-Path $baseDir ("large_city_10k_{0}.rpt" -f $route)
    $out = Join-Path $baseDir ("large_city_10k_{0}.out" -f $route)

    $start = Get-Date
    & $SwmmExe $inp $rpt $out
    $exitCode = $LASTEXITCODE
    $elapsed = ((Get-Date) - $start).TotalSeconds

    $benchmark += [pscustomobject]@{
        route_model = $route
        return_code = $exitCode
        elapsed_seconds = [math]::Round($elapsed, 3)
        rpt_size_bytes = (Get-Item $rpt).Length
        out_size_bytes = (Get-Item $out).Length
    }
}

$benchmark | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $baseDir "large_city_10k_terminal_benchmark.json")
python $generator --summarize-only --output-dir $baseDir
if ($LASTEXITCODE -ne 0) { throw "Benchmark summary failed." }

$benchmark | Format-Table -AutoSize
