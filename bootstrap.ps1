$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\Users\Matte\ai-desktop-agent"
$VenvPath = Join-Path $ProjectRoot ".venv"
$PythonExe = Join-Path $VenvPath "Scripts\python.exe"
$PipExe = Join-Path $VenvPath "Scripts\pip.exe"
$SettingsPath = Join-Path $ProjectRoot "config\settings.yaml"
$SecretsPath = Join-Path $ProjectRoot "config\secrets.yaml"
$SmokeTestPath = Join-Path $ProjectRoot "smoke_test.py"

Write-Host ""
Write-Host "== AI Desktop Agent Bootstrap =="
Write-Host "Project: $ProjectRoot"
Write-Host ""

if (!(Test-Path $ProjectRoot)) {
    throw "Project folder not found: $ProjectRoot"
}

Set-Location $ProjectRoot

# 1. Check Python launcher
Write-Host "[1/7] Checking Python..."
try {
    py --version | Out-Null
}
catch {
    throw "Python launcher 'py' not found. Install Python first."
}

# 2. Create venv if missing
Write-Host "[2/7] Ensuring virtual environment..."
if (!(Test-Path $PythonExe)) {
    py -m venv $VenvPath
}

# 3. Install requirements
Write-Host "[3/7] Installing requirements..."
& $PythonExe -m pip install --upgrade pip
& $PipExe install -r (Join-Path $ProjectRoot "requirements.txt")

# 4. Ensure config folder exists
Write-Host "[4/7] Ensuring config..."
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "config") | Out-Null

if (!(Test-Path $SettingsPath)) {
@"
model: "gpt-5.4"
reasoning:
  effort: "medium"
base_url: "https://api.openai.com/v1"

dry_run: true
risky_tools_enabled: false
input_tools_enabled: false
tool_timeout_seconds: 10
emergency_stop_key: "f12"
block_dangerous_shell: true
max_iterations: 20
"@ | Set-Content -Encoding UTF8 $SettingsPath
    Write-Host "Created default settings.yaml"
}

# Optional legacy secrets file fallback
if (!(Test-Path $SecretsPath)) {
@"
api_key: ""
"@ | Set-Content -Encoding UTF8 $SecretsPath
    Write-Host "Created placeholder secrets.yaml"
}

# 5. Check API key
Write-Host "[5/7] Checking API key..."
$ApiKey = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    Write-Host ""
    Write-Host "OPENAI_API_KEY is not set."
    Write-Host "Set it with:"
    Write-Host 'setx OPENAI_API_KEY "your_key_here"'
    Write-Host ""
    Write-Host "Then close and reopen PowerShell and run this bootstrap again."
    exit 1
}

# 6. Write smoke test
Write-Host "[6/7] Writing smoke test..."
@"
import importlib

mods = [
    "main",
    "core.agent",
    "core.loop",
    "core.state",
    "core.safety",
    "core.llm_client",
    "tools.registry",
    "tools.files",
    "tools.shell",
    "tools.input",
]

failed = []
for mod in mods:
    try:
        importlib.import_module(mod)
        print(f"[OK] {mod}")
    except Exception as e:
        print(f"[FAIL] {mod}: {e}")
        failed.append((mod, str(e)))

if failed:
    raise SystemExit(1)

print("Smoke test passed.")
"@ | Set-Content -Encoding UTF8 $SmokeTestPath

& $PythonExe $SmokeTestPath

# 7. Launch
Write-Host "[7/7] Launching agent..."
& $PythonExe (Join-Path $ProjectRoot "main.py")
