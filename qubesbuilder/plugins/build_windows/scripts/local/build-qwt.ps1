# Build Qubes Windows Tools locally, with test signing.
# All build artifacts are copied to "repository" dir in current directory.
# The final installer is in repository/installer-windows-tools/bin.

param(
    [Parameter(Mandatory, HelpMessage="Directory containing all components' sources")] [string]$src,
    [Parameter(HelpMessage="Build configuration (Release/Debug)")] [string]$cfg = "Release"
)

$ErrorActionPreference = "Stop"

# list of required components, in order of dependencies
# source directories can also have "qubes-" prepended to the name
$components = @(
    "vmm-xen-windows-pvdrivers",
    "core-vchan-xen",
    "windows-utils",
    "core-qubesdb",
    "core-agent-windows",
    "gui-common",
    "gui-agent-windows",
    "installer-windows-tools"
)

if (! (Test-Path $src -PathType Container)) {
    Write-Error "Invalid source directory: $src"
}

$repo = ".\repository"

if (Test-Path $repo) {
    Remove-Item -Path $repo -Recurse -Force
}
New-Item -Path "$repo" -ItemType Directory -Force
$repo = Resolve-Path $repo

foreach ($component in $components) {
    if (! (Test-Path "$src\$component" -PathType Container)) {
        $component = "qubes-" + $component
        if (! (Test-Path "$src\$component" -PathType Container)) {
            Write-Error "Component '$component' not found in directory '$src'"
        }
    }
    & "$PSScriptRoot\build.ps1" "$src\$component" "$repo" $cfg
}
