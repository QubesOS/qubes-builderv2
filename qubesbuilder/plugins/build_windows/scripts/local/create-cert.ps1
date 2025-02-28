# create test-sign certificate
# the private part is saved in the certificate store
# usage: $0 <public cert path>

$cert_path = $args[0]

$cn = "Qubes Tools"
$end_date = (Get-Date).AddYears(5)

$cert = New-SelfSignedCertificate -KeyUsage DigitalSignature -KeySpec Signature -Type CodeSigningCert -HashAlgorithm sha256 -CertStoreLocation "Cert:\CurrentUser\My" -Subject $cn -NotAfter $end_date

Export-Certificate -Cert $cert -FilePath $cert_path
