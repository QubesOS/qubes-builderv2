. $env:QUBES_TOOLS\qubes-rpc-services\VMExec-Decode.ps1
. $env:QUBES_TOOLS\qubes-rpc-services\log.ps1

LogStart

try {
    $decoded = VMExec-Decode $args[0]
    LogDebug "decoded: $decoded"

    $fileSender = Join-Path $env:QUBES_TOOLS "qubes-rpc-services\file-sender.exe"
    Start-Process -FilePath $fileSender -ArgumentList "$decoded" -LoadUserProfile -NoNewWindow -Wait
} catch [DecodeError] {
    Write-Error $_.Exception.Message
}
