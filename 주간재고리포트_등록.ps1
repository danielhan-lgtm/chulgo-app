# 주간 재고 비교 리포트 — Windows 작업스케줄러 자동 등록
# 매주 월요일 오전 9시에 백엔드 /reconcile/weekly-report 를 호출해 스냅샷 저장.
# 사용법: PowerShell에서 우클릭 → "PowerShell로 실행" (또는 관리자 권한 권장)
#
# 주의: 이 .ps1 파일은 UTF-8 BOM으로 저장되어야 한글이 깨지지 않음 (PowerShell 5.1).

$ErrorActionPreference = "Stop"

# ── 설정 ──────────────────────────────────────────────
$BackendUrl  = "http://localhost:8081"
$LocationIds = "228640"          # 아워박스 호법
$TaskName    = "BH_OB_주간재고리포트"
$RunTime     = "11:00"           # 월요일 실행 시각
$TokenFile   = Join-Path $PSScriptRoot "config.json"   # 토큰은 루트 config.json에 있음

# ── 호출 스크립트 본문 (스케줄러가 실행할 명령) ──────────
# config.json에서 토큰을 읽어 weekly-report 호출
$InnerScript = @"
try {
  `$cfg = Get-Content '$TokenFile' -Raw -Encoding UTF8 | ConvertFrom-Json
  `$tok = `$cfg.api_token
  if (-not `$tok) { `$tok = `$cfg.config.api_token }
  `$url = '$BackendUrl/api/reconcile/weekly-report?token=' + `$tok + '&location_ids=$LocationIds'
  Invoke-RestMethod -Method Post -Uri `$url -TimeoutSec 300
} catch {
  # 백엔드가 꺼져있으면 1회 건너뜀 (다음 주 재시도)
  Write-Output ('weekly-report 실패: ' + `$_.Exception.Message)
}
"@

$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($InnerScript))
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -EncodedCommand $encoded"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At $RunTime
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun:$false `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "BoxHero↔OurBox 호법 재고 비교 주간 스냅샷 자동 저장" -Force

Write-Host ""
Write-Host "✅ 등록 완료: '$TaskName'" -ForegroundColor Green
Write-Host "   매주 월요일 $RunTime 자동 실행 (백엔드가 켜져 있어야 함)"
Write-Host "   결과는 앱 '재고 현황 → 주간 리포트'에서 확인"
Write-Host ""
Write-Host "지금 바로 한 번 실행해 테스트하려면:" -ForegroundColor Yellow
Write-Host "   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "등록 해제:"
Write-Host "   Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
