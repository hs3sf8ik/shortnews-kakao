# Windows 작업 스케줄러용 래퍼 — 매일 07:30 실행 (scripts/register_task.ps1 로 등록)
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
New-Item -ItemType Directory -Force "$root\logs" | Out-Null
$log = "$root\logs\run-$(Get-Date -Format yyyy-MM-dd).log"

# 스케줄러 환경은 PATH 가 빈약할 수 있어 python / claude 위치를 보강
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$extra = @("$env:ProgramFiles\Python313", "$env:ProgramFiles\Python313\Scripts",
           "$env:LOCALAPPDATA\Programs\Python\Python313", "$env:LOCALAPPDATA\Programs\Python\Python313\Scripts",
           "$env:APPDATA\npm", "$env:LOCALAPPDATA\Programs\claude", "$env:ProgramFiles\nodejs")
$env:PATH = ($extra | Where-Object { Test-Path $_ }) -join ";" + ";" + $env:PATH

"===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 시작 =====" | Out-File -Append -Encoding utf8 $log
python -m src.main *>> $log
$code = $LASTEXITCODE
"===== 종료 코드 $code =====" | Out-File -Append -Encoding utf8 $log

# 30일 지난 로그·수집 캐시 정리
Get-ChildItem "$root\logs" -Filter "run-*.log" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem "$root\data" -Directory -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
exit $code
