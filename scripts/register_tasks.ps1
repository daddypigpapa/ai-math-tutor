# 관리자 PowerShell 에서 1회 실행: Windows 작업 스케줄러에 두 작업을 등록한다.
#   1) AI가정교사-서버     : 부팅 시 자동 실행 (서버 재부팅 후 자동 시작)
#   2) AI가정교사-주간보고 : 매주 일요일 21:00 에 주간 보고서 생성·디스코드 전송
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root ".venv\Scripts\python.exe"

$server = New-ScheduledTaskAction -Execute $py -Argument "-m uvicorn app.main:app --host 0.0.0.0 --port 8000" -WorkingDirectory $root
$atBoot = New-ScheduledTaskTrigger -AtStartup
$set = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
Register-ScheduledTask -TaskName "AI가정교사-서버" -Action $server -Trigger $atBoot -Settings $set -RunLevel Highest -Force | Out-Null

$rep = New-ScheduledTaskAction -Execute $py -Argument "-m app.report --learner all" -WorkingDirectory $root
$weekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 21:00
Register-ScheduledTask -TaskName "AI가정교사-주간보고" -Action $rep -Trigger $weekly -RunLevel Highest -Force | Out-Null

Write-Host "등록 완료. 확인: Get-ScheduledTask -TaskName 'AI가정교사-*'"
