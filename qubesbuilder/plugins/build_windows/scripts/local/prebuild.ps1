param (
    [string]$component_src_dir, # component source directory
    [string]$repo_root # root repo directory for all components
)

$ErrorActionPreference = "Stop"

# Skip if running from Qubes Builder
if (! (Test-Path -Path env:QB_LOCAL)) {
    exit 0
}

. $PSScriptRoot\functions.ps1

QB-LocalPreBuild $component_src_dir $repo_root
