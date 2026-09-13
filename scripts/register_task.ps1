# 작업 스케줄러 등록/갱신: 매일 07:30, 놓치면 PC 켜진 직후 실행
param([string]$Time = "07:30", [string]$TaskName = "shortnews-kakao")

$script = Join-Path $PSScriptRoot "run_daily.ps1"
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
           -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 40) `
            -MultipleInstances IgnoreNew -WakeToRun
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "짧은 뉴스 카카오톡 발송 (Desktop\Claude\shortnews-kakao)" -Force | Out-Null
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
(Get-ScheduledTask -TaskName $TaskName).Triggers | Select-Object StartBoundary, Enabled
