# Keep Wi-Fi up overnight on the UM790 ops box (no wired path).
# Run from an elevated PowerShell if you see Access denied:
#   powershell -File schwab_skill\scripts\harden_ops_box_wifi.ps1
$ErrorActionPreference = "Stop"

$wifi = Get-NetAdapter | Where-Object {
    $_.Status -eq "Up" -and $_.Name -match "Wi-?Fi"
}
if (-not $wifi) {
    Write-Host "No Wi-Fi adapter is Up. Connect wireless first."
    exit 1
}

$failed = 0
foreach ($nic in $wifi) {
    Write-Host "Hardening $($nic.Name) ($($nic.InterfaceDescription))..."
    try {
        Disable-NetAdapterPowerManagement -Name $nic.Name -NoRestart
        Write-Host "  NetAdapterPowerManagement disabled"
    } catch {
        $failed += 1
        Write-Host "  NetAdapterPowerManagement FAILED: $($_.Exception.Message)"
        Write-Host "  Re-run this script in an elevated PowerShell (Run as administrator)."
    }
    $powerProps = @(Get-NetAdapterAdvancedProperty -Name $nic.Name -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match "Power Saving|Power Save" })
    foreach ($prop in $powerProps) {
        try {
            Set-NetAdapterAdvancedProperty -Name $nic.Name -DisplayName $prop.DisplayName -DisplayValue "Disabled" -NoRestart
            Write-Host "  $($prop.DisplayName) = Disabled"
        } catch {
            $failed += 1
            Write-Host "  $($prop.DisplayName) FAILED: $($_.Exception.Message)"
            Write-Host "  Re-run this script in an elevated PowerShell (Run as administrator)."
        }
    }
}

Write-Host "Keep both UM790 Wi-Fi antennas on. AC sleep should stay Never."
if ($failed -gt 0) {
    exit 1
}
exit 0
