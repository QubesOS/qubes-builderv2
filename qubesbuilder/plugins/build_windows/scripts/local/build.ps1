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

$script_dir = Resolve-Path "$PSScriptRoot\.."

# create local repo, without version since it's stripped by the build script anyway
$component = Split-Path $dir -Leaf
$repo_component = $component.TrimStart("qubes-") # normalize name
$repo_dir = "$repo\$repo_component"
if (Test-Path $repo_dir) {
    Remove-Item -Path $repo_dir -Recurse -Force
}
New-Item -Path $repo_dir -ItemType Directory -Force

$component_version = (Get-Content "$dir\version").Trim()

function ReplacePlaceholders {
    param([string]$str)
    return $str.Replace("@CONFIGURATION@", $cfg).Replace("@VERSION@", $component_version)
}

Import-Module powershell-yaml

$yaml = ConvertFrom-Yaml (Get-Content "$dir\.qubesbuilder" -Raw)

# download distfiles
if ($yaml.ContainsKey('source') -and $yaml['source'].ContainsKey('files')) {
    # for local builds, keep distfiles in the source dir for easy access by the component
    $distfiles = "$dir\.distfiles"
    New-Item -Path $distfiles -ItemType Directory -Force

    foreach ($entry in $yaml['source']['files']) {
        $url = $entry['url']
        $file = Split-Path $url -Leaf
        $out_path = "$distfiles\$file"
        $hash_file = $entry['sha256']
        $expected = (Get-Content "$dir\$hash_file").Trim()

        if (! (Test-Path $out_path)) {
            Invoke-WebRequest $url -OutFile $out_path
        }

        $hash = (Get-FileHash $out_path -Algorithm SHA256).Hash
        if ($hash -ne $expected) {
            Remove-Item $out_path
            Write-Error "Invalid sha256 for downloaded '$file', aborting: got $hash, expected $expected"
        }
    }
}

# TODO: make this more generic
$root = $yaml['vm']['windows']

. "$script_dir\common.ps1"

# need our own EWDK environment for signing
$env:EWDK_PATH = Find-EWDK
Launch-EWDK

# generate testsign cert
& "$script_dir\local\create-cert.ps1" "$dir\sign.crt"

foreach ($target in $root['build']) {
    # build
    $args = @(
        "$script_dir\build-sln.ps1",
        "-solution", "$dir\$target",
        "-configuration", $cfg,
        "-repo", $repo,
        "-testsign",
        "-noisy"
    )

    if ($distfiles -ne $null) {
        $args += @("-distfiles", $distfiles)
    }

    $proc = Start-Process -NoNewWindow -PassThru powershell -ArgumentList $args
    $proc.WaitForExit()

    # copy artifacts to local repo
    $kinds = @('bin', 'inc', 'lib')
    foreach ($kind in $kinds) {
        New-Item -Path "$repo_dir\$kind" -ItemType Directory -Force
        foreach ($output in $root[$kind]) {
            # TODO: make this more generic
            $output = ReplacePlaceholders $output

            if ($kind -eq "bin") {
                # sign if needed
                $do_sign = $false
                @(".exe", ".dll", ".sys", ".cat") | % { $do_sign = $do_sign -or $output.EndsWith($_) }

                if ($do_sign) {
                    foreach ($skip in $root['skip-test-sign']) {
                        $skip = ReplacePlaceholders $skip
                        if ($output -eq $skip) {
                            $do_sign = $false
                            break
                        }
                    }

                    if ($do_sign) {
                        & "$script_dir\local\sign.ps1" "$dir\sign.crt" "$dir\$output"
                    }
                }
            }

            Copy-Item "$dir\$output" "$repo_dir\$kind"
        }
    }

    # copy testsign cert to local repo
    Copy-Item "$dir\sign.crt" $repo_dir
    # delete testsign cert from OS store
    & "$script_dir\local\delete-cert.ps1" "$dir\sign.crt"
}
