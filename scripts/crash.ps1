<#
  Hard-kills one running demo process, simulating a crash: the process gets no chance to clean up
  or send DISCONNECT (Stop-Process -Force calls TerminateProcess).

  Usage:  crash.ps1 -Target tenant-juan     (any client id: tenant-jerome, caretaker, manager-office, ...)
          crash.ps1 -Target broker
#>
param([Parameter(Mandatory = $true)][string]$Target)

$victims = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" | Where-Object {
    if ($Target -eq 'broker') { $_.CommandLine -match 'pubsub\.broker' }
    else { $_.CommandLine -match ('--id\s+' + [regex]::Escape($Target) + '(\s|$)') }
}

if (-not $victims) {
    Write-Host "No running process found for '$Target'."
    exit 1
}
foreach ($p in $victims) {
    Stop-Process -Id $p.ProcessId -Force
    Write-Host "CRASHED $Target (process $($p.ProcessId) killed without warning)"
}
