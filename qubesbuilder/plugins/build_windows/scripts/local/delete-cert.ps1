# Deletes a public certificate file and its private key from the certificate store.
# Usage: $0 [public cert]

$ErrorActionPreference = 'Stop'

$cert_path = "qwt.cer"
if ($args[0] -ne $null) {
    $cert_path = $args[0]
}

if (! (Test-Path $cert_path)) {
    Write-Host "$cert_path not found, doing nothing"
    exit 0
}

$tp = (Get-PfxCertificate -FilePath $cert_path).Thumbprint

Remove-Item $cert_path

# remove from personal cert store
Remove-Item "cert:\CurrentUser\My\$tp"

# signtool adds it to the user's CA store so remove from there as well
Remove-Item "cert:\CurrentUser\CA\$tp"
