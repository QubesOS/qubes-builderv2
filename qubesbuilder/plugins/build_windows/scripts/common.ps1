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
        if (! (Test-Path -Path "env:$var_name")) {
            Set-Item -Path "env:$var_name" -Value $var_value
        }
    }
}

function LogStart {
    $logDir = "c:\builder\log"
    New-Item -Path $logDir -ItemType Directory -Force
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
    Write-Error $msg
}

function LogWarning {
    param([string]$msg)
    Log 2 $msg
    Write-Warning $msg
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
