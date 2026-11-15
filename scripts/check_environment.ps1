param(
    [string]$ComfyRoot,
    [string]$Python,
    [string]$ComfyUrl,
    [string]$Config,
    [string]$ClientPython,
    [string]$ApiUrl,
    [string]$KeyFile,
    [string]$NativeKeyFile,
    [string]$GatewayDir,
    [ValidateSet(4,8)][int]$Steps = 8,
    [ValidateSet('depth','ref2v','character_swap')][string]$Route = 'depth'
)
$ErrorActionPreference = 'Stop'
$report = Join-Path ([Environment]::GetFolderPath('Desktop')) 'H3-Environment-Check'
New-Item -ItemType Directory -Path $report -Force | Out-Null
if (!$Python -and $ComfyRoot) {
    foreach ($candidate in @((Join-Path $ComfyRoot '.venv\Scripts\python.exe'), (Join-Path (Split-Path $ComfyRoot) 'python_embeded\python.exe'))) {
        if (Test-Path $candidate) { $Python = $candidate; break }
    }
}
$launcher = @()
if ($ClientPython) { $launcher = @($ClientPython) }
elseif ($Python) { $launcher = @($Python) }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $launcher = @('py', '-3') }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $launcher = @('python') }
if (!$launcher.Count) {
    $message = 'Python not found. No installation attempted. Locate ComfyUI portable python_embeded\python.exe or Desktop/venv Python, then rerun with -Python and -ComfyRoot. If ComfyUI is absent, follow references/onboarding.md and approve an installation plan first.'
    $message | Set-Content -Encoding UTF8 (Join-Path $report 'bootstrap.txt')
    Write-Host $message
    exit 2
}
$arguments = @('-X', 'utf8', (Join-Path $PSScriptRoot 'check_environment.py'), '--out', (Join-Path $report 'report.json'))
if ($Config) { $arguments += @('--config', $Config) }
else { $arguments += @('--route', $Route, '--steps', "$Steps") }
if ($ComfyUrl) { $arguments += @('--comfy-url', $ComfyUrl) }
if ($ComfyRoot) { $arguments += @('--comfy-root', $ComfyRoot) }
if ($Python) { $arguments += @('--python', $Python) }
if ($ApiUrl) { $arguments += @('--api-url', $ApiUrl) }
if ($KeyFile) { $arguments += @('--key-file', $KeyFile) }
if ($NativeKeyFile) { $arguments += @('--native-key-file', $NativeKeyFile) }
if ($GatewayDir) { $arguments += @('--gateway-dir', $GatewayDir) }
$exe = $launcher[0]
$prefixArgs = @($launcher | Select-Object -Skip 1)
& $exe @prefixArgs @arguments
exit $LASTEXITCODE
