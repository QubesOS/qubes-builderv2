
$src = "c:\qubes"

Start-Process -Wait -FilePath "msiexec.exe" -ArgumentList "/i","$src\win-opensshd.msi","/passive"

New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -Program 'c:\Program Files\OpenSSH\sshd.exe'

$akeys = "c:\ProgramData\ssh\administrators_authorized_keys"
cp "$src\win-build.key.pub" "$akeys"

# set permissions to only allow administrators
$acl = Get-Acl $akeys
$acl.SetAccessRuleProtection($true, $false)  # disable ACL inheritance and remove all ACEs
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule("BUILTIN\Administrators", "FullControl", "None", "None", "Allow")
$acl.SetAccessRule($rule)
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule("NT Authority\SYSTEM", "FullControl", "None", "None", "Allow")
$acl.SetAccessRule($rule)
Set-Acl $akeys $acl

$config = "c:\ProgramData\ssh\sshd_config"
cp -Force "$src\sshd_config" "$config"
# allow reading by authenticated users
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule("NT Authority\Authenticated Users", "ReadAndExecute", "None", "None", "Allow")
$acl.SetAccessRule($rule)
Set-Acl $config $acl

Restart-Service -Name sshd
