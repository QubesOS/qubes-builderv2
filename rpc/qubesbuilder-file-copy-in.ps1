. $env:QUBES_TOOLS\qubes-rpc-services\VMExec-Decode.ps1
. $env:QUBES_TOOLS\qubes-rpc-services\log.ps1

LogStart

try {
    $decoded = VMExec-Decode $args[0]
    LogDebug "decoded: $decoded"

    $fileReceiver = Join-Path $env:QUBES_TOOLS "qubes-rpc-services\file-receiver.exe"

    # Create destination directory
    New-Item -ItemType Directory -Path $decoded -Force | Out-Null

    # All Windows RPC executables use | as argument separator and powershell adds an extra space to the command line
    # see https://github.com/PowerShell/PowerShell/issues/13094
    Start-Process -FilePath $fileReceiver -ArgumentList "$decoded|" -LoadUserProfile -NoNewWindow -Wait
} catch [DecodeError] {
    Write-Error $_.Exception.Message
}
