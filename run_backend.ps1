# 출고 라몬 백엔드 자동 실행 런처 (Windows 작업 스케줄러용)
# - 로그온 시 시작, 백엔드가 죽으면 5초 후 자동 재시작
# - 창 숨김(작업 스케줄러에서 -WindowStyle Hidden로 호출)
$ErrorActionPreference = 'SilentlyContinue'
$backend = 'C:\Users\User\Desktop\ai 프로젝트\출고변환\backend'
$py = 'C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$log = Join-Path $env:TEMP 'chulgo_backend.log'
Set-Location $backend
while ($true) {
  if (Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue) {
    Start-Sleep -Seconds 30
    continue
  }
  if ((Test-Path $log) -and ((Get-Item $log).Length -gt 10MB)) {
    Move-Item $log "$log.old" -Force
  }
  try {
    & $py -X utf8 -m uvicorn main:app --host 127.0.0.1 --port 8081 *>> $log
  } catch { }
  Start-Sleep -Seconds 5
}
