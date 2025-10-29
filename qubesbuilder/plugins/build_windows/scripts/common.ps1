# Get EWDK root from the environment or find it if not set
function Find-EWDK {
    if (Test-Path -Path env:EWDK_PATH) {
        return $env:EWDK_PATH
    } else {
        foreach ($drive in Get-PSDrive) {
            if ($drive.Provider.Name -eq "FileSystem") {
                $root = $drive.Root
                $path = "$root\LaunchBuildEnv.cmd"
                if (Test-Path -Path $path) {
                    return $root
                }
            }
        }
    }
    return $null
}

# Launch EWDK's environment setup script and grab variables that were set
function Launch-EWDK {
    $ewdk_env_cmd = "$env:EWDK_PATH\BuildEnv\SetupBuildEnv.cmd"
    $ewdk_vars_txt = cmd /c "$ewdk_env_cmd x86_amd64 > nul & set"

    foreach ($line in $ewdk_vars_txt) {
        $kv = $line.split("=")
        $var_name = $kv[0]
        $var_value = $kv[1]
        Set-Item -Path "env:$var_name" -Value $var_value
    }
}

# Launch process, stream its stdout to console, highlight errors.
# Return exit code.
function StreamProcess {
    param (
        [string]$exe,
        [string[]]$exe_args
    )
    # We use the below instead of Start-Process because processes started
    # using Start-Process don't return proper ExitCode for... reasons?
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = $exe_args
    $psi.RedirectStandardOutput = $true
    # msbuild and powershell don't use stderr
    $psi.RedirectStandardError  = $false
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi

    $proc.Start() | Out-Null
    $stdout = $proc.StandardOutput

    while (-not $stdout.EndOfStream) {
        $line = $stdout.ReadLine()
        if ($line -ne $null) {
            if (($line -match 'FAILED') -or
                ($line -match ': error C') -or
                ($line -match 'error LNK') -or
                ($line -match 'error MSB')) {
                LogWarning $line
            } else {
                Write-Host $line
            }
        }
    }

    $proc.WaitForExit()
    # If we used Start-Process, here $proc.ExitCode would be $null usually
    return $proc.ExitCode
}

function LogStart {
    $logDir = "c:\builder\log"
    New-Item -Path $logDir -ItemType Directory -Force | Out-Null
    $baseName = (Get-Item $MyInvocation.PSCommandPath).BaseName
    $logname = "$baseName-$(Get-Date -Format "yyyyMMdd-HHmmss")-$PID.log"
    $global:qwtLogPath = "$logDir\$logName"
    $global:qwtLogLevel = 4
}

function Log {
    param (
        [ValidateRange(1,5)][int]$level,
        [string]$msg
    )

    if ($level -le $qwtLogLevel) {
        $ts = Get-Date -Format "yyyyMMdd.HHmmss.fff"
        Add-Content $qwtLogPath -value "[$ts-$("EWIDV"[$level-1])] $msg"
    }
}

function LogError {
    param([string]$msg)
    Log 1 $msg
    # this causes script exit if $ErrorActionPreference == "Stop"
    Write-Error $msg
}

function LogWarning {
    param([string]$msg)
    Log 2 $msg
    # we don't use Write-Warning because it appends "WARNING" by default
    Write-Host $msg -ForegroundColor DarkYellow
}

function LogInfo {
    param([string]$msg)
    Log 3 $msg
    Write-Host $msg
}

function LogDebug {
    param([string]$msg)
    Log 4 $msg
    Write-Host $msg
}

function LogVerbose {
    param([string]$msg)
    Log 5 $msg
} 
