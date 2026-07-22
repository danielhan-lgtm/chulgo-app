# 출고변환 백엔드 클린 재시작 (코드 수정 반영용)
# - 8081 을 물고 있는 uvicorn reloader/worker/multiprocessing 자식까지 전부 종료
# - 포트가 완전히 풀린 뒤 새 코드로 다시 기동
# - /docs 200 으로 기동 확인
# 사용: PowerShell 에서  ./restart_backend.ps1   (또는 재시작_백엔드.bat 더블클릭)

$ErrorActionPreference = 'SilentlyContinue'
$backend = Join-Path $PSScriptRoot 'backend'
$py = 'C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe'
if (-not (Test-Path $py)) { $py = 'C:\Users\User\AppData\Local\Microsoft\WindowsApps\python.exe' }
$log = Join-Path $env:TEMP 'chulgo_backend.log'
$port = 8081

Write-Host "[1/4] 8081 포트 사용 프로세스 종료 중..." -ForegroundColor Cyan
# 1) 8081 리스닝 프로세스
$c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($c) { $c.OwningProcess | Select-Object -Unique | ForEach-Object { taskkill /F /T /PID $_ 2>&1 | Out-Null } }
# 2) 커맨드라인에 8081 이 있는 python (reloader 등)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*$port*" -and $_.CommandLine -like '*uvicorn*' } |
  ForEach-Object { taskkill /F /T /PID $_.ProcessId 2>&1 | Out-Null }

Write-Host "[2/4] 포트 해제 대기..." -ForegroundColor Cyan
for ($i = 0; $i -lt 15; $i++) {
  Start-Sleep -Milliseconds 500
  if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) { break }
}
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
  Write-Host "  경고: 8081 이 아직 해제되지 않았습니다. 잔여 프로세스를 수동 확인하세요." -ForegroundColor Yellow
} else {
  Write-Host "  포트 해제됨." -ForegroundColor Green
}

Write-Host "[3/4] 백엔드 기동 (python=$py)..." -ForegroundColor Cyan
Start-Process -FilePath $py `
  -ArgumentList '-X','utf8','-m','uvicorn','main:app','--host','127.0.0.1','--port',"$port" `
  -WorkingDirectory $backend -WindowStyle Hidden `
  -RedirectStandardOutput $log -RedirectStandardError "$log.err"

Write-Host "[4/4] 기동 확인 중..." -ForegroundColor Cyan
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
  try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/docs" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ok = $true; break } } catch {}
  Start-Sleep -Seconds 1
}
if ($ok) {
  $pid8081 = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).OwningProcess -join ','
  Write-Host "✅ 백엔드 재시작 완료 (PID=$pid8081, http://127.0.0.1:$port)" -ForegroundColor Green
  Write-Host "   로그: $log"
} else {
  Write-Host "❌ 기동 실패 — 아래 에러 로그 확인:" -ForegroundColor Red
  Get-Content "$log.err" -Tail 20 -ErrorAction SilentlyContinue
}
