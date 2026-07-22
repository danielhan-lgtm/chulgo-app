# 출고 라몬 백엔드 — Windows 작업스케줄러 자동 시작 등록
# 로그온 시 run_backend.ps1을 숨김 창으로 실행 (백엔드가 죽으면 런처가 5초 후 자동 재시작).
# 사용법: PowerShell에서 우클릭 → "PowerShell로 실행"
#
# 주의: 이 .ps1 파일은 UTF-8 BOM으로 저장되어야 한글이 깨지지 않음 (PowerShell 5.1).

$ErrorActionPreference = "Stop"

$TaskName = "출고라몬_백엔드_자동시작"
$Launcher = Join-Path $PSScriptRoot "run_backend.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
# ExecutionTimeLimit 0 = 무제한 (기본 3일 제한이면 백엔드가 3일마다 강제 종료됨)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "출고 라몬 백엔드(uvicorn 8081) 로그온 시 자동 시작 + 죽으면 자동 재시작" -Force

Write-Host ""
Write-Host "✅ 등록 완료: '$TaskName'" -ForegroundColor Green
Write-Host "   로그온하면 백엔드가 자동으로 뜹니다 (창 없음, 로그: %TEMP%\chulgo_backend.log)"
Write-Host ""
Write-Host "지금 바로 시작하려면:" -ForegroundColor Yellow
Write-Host "   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "등록 해제:"
Write-Host "   Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
