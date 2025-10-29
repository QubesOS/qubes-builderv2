# Build a Windows component locally, with test signing

param(
    [Parameter(Mandatory, HelpMessage="Component's source directory")] [string]$dir,
    [Parameter(Mandatory, HelpMessage="Repository directory for build artifacts")] [string]$repo,
    [Parameter(Mandatory, HelpMessage="Build configuration (Release/Debug)")] [string]$cfg = "Release"
)

$ErrorActionPreference = "Stop"

if (! (Test-Path $dir -PathType Container)) {
    Write-Error "Invalid component directory: $dir"
}

$dir = Resolve-Path $dir

. "$PSScriptRoot\..\common.ps1"
$env:EWDK_PATH = Find-EWDK
Launch-EWDK

$env:QB_LOCAL = 1
$env:QUBES_REPO = $repo

Import-Module powershell-yaml
$yaml = ConvertFrom-Yaml (Get-Content "$dir\.qubesbuilder" -Raw)

# TODO: make this more generic
$root = $yaml['vm']['windows']

foreach ($target in $root['build']) {
    # build
    $args = @(
        "$PSScriptRoot\..\build-sln.ps1",
        "-solution", "$dir\$target",
        "-configuration", $cfg,
        "-repo", $repo,
        "-testsign",
        "-noisy"
    )

    if (Test-Path "$dir\.distfiles") {
        $args += @("-distfiles", "$dir\.distfiles")
    }

    $status = StreamProcess "powershell.exe" $args

    if ($status -ne 0) {
        exit $status
    }
}
