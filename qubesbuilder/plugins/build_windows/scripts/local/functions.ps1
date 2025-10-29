# Get component-specific directory for output artifacts.
function QB-Get-LocalComponentRepo {
    param (
        [string]$component_src_dir,
        [string]$repo_root # root repo directory for all components
    )

    $component = Split-Path $component_src_dir -Leaf
    $repo_component = $component.TrimStart("qubes-") # normalize name
    return "$repo_root\$repo_component"
}

# Create component-specific output artifacts repository.
function QB-Create-LocalComponentRepo {
    param (
        [string]$component_src_dir, # this component source directory
        [string]$repo_root # root repo directory for all components
    )

    $component_repo_dir = QB-Get-LocalComponentRepo $component_src_dir $repo_root
    if (Test-Path $component_repo_dir) {
        Remove-Item -Path $component_repo_dir -Recurse -Force
    }
    New-Item -Path $component_repo_dir -ItemType Directory -Force | Out-Null
}

# Perform required pre-build actions (download prerequisites, create test sign cert).
function QB-LocalPreBuild {
    param (
        [string]$component_src_dir, # this component source directory
        [string]$repo_root # root repo directory for all components
    )

    if (! (Test-Path -Path env:QB_LOCAL)) {
        # we're being built via the proper builder, it does its own pre/post build processing
        return
    }

    QB-Create-LocalComponentRepo $component_src_dir $repo_root

    Import-Module powershell-yaml 2>&1 | Out-Null
    $yaml = ConvertFrom-Yaml (Get-Content "$component_src_dir\.qubesbuilder" -Raw)

    # download distfiles
    if ($yaml.ContainsKey('source') -and $yaml['source'].ContainsKey('files')) {
        echo "Downloading prerequisites..."
        # for local builds, keep distfiles in the source dir for easy access by the component
        $distfiles = "$component_src_dir\.distfiles"
        New-Item -Path $distfiles -ItemType Directory -Force | Out-Null

        foreach ($entry in $yaml['source']['files']) {
            $url = $entry['url']
            $file = Split-Path $url -Leaf
            $out_path = "$distfiles\$file"
            $hash_file = $entry['sha256']
            $expected = (Get-Content "$component_src_dir\$hash_file").Trim()

            if (! (Test-Path $out_path)) {
                Invoke-WebRequest $url -OutFile $out_path
            }

            $hash = (Get-FileHash $out_path -Algorithm SHA256).Hash
            if ($hash -ne $expected) {
                Remove-Item $out_path
                Write-Error "[!] Invalid sha256 for downloaded '$file', aborting: got $hash, expected $expected"
            }
        }
    }

    # generate testsign cert
    & "$PSScriptRoot\create-cert.ps1" "$component_src_dir\sign.crt"
}

function QB-Replace-LocalPlaceholders {
    param (
        [string]$str,
        [string]$cfg, # build configuration
        [string]$ver # component version
    )
    return $str.Replace("@CONFIGURATION@", $cfg).Replace("@VERSION@", $ver)
}

function QB-LocalPostBuild {
    param (
        [string]$component_src_dir, # this component source directory
        [string]$repo_root, # root repo directory for all components
        [string]$build_configuration # Release/Debug
    )

    if (! (Test-Path -Path env:QB_LOCAL)) {
        # we're being built via the proper builder, it does its own pre/post build processing
        return
    }

    # EWDK is needed for signing
    if (! (Test-Path -Path env:EnterpriseWDK)) {
        . "$PSScriptRoot\..\common.ps1"
        $env:EWDK_PATH = Find-EWDK
        Launch-EWDK
    }

    $component_version = (Get-Content "$component_src_dir\version").Trim()

    # copy output artifacts to local repo
    $repo_dir = QB-Get-LocalComponentRepo $component_src_dir $repo_root

    Import-Module powershell-yaml 2>&1 | Out-Null
    $yaml = ConvertFrom-Yaml (Get-Content "$component_src_dir\.qubesbuilder" -Raw)
    # TODO: make this more generic
    $root = $yaml['vm']['windows']

    $kinds = @('bin', 'inc', 'lib')
    foreach ($kind in $kinds) {
        echo "Signing/copying build output..."
        New-Item -Path "$repo_dir\$kind" -ItemType Directory -Force | Out-Null
        foreach ($output in $root[$kind]) {
            # TODO: make this more generic
            $output = QB-Replace-LocalPlaceholders $output $build_configuration $component_version

            if ($kind -eq "bin") {
                # sign if needed
                $do_sign = $false
                @(".exe", ".dll", ".sys", ".cat") | % { $do_sign = $do_sign -or $output.EndsWith($_) }

                if ($do_sign) {
                    foreach ($skip in $root['skip-test-sign']) {
                        $skip = QB-Replace-LocalPlaceholders $skip $build_configuration $component_version
                        if ($output -eq $skip) {
                            $do_sign = $false
                            break
                        }
                    }

                    if ($do_sign) {
                        & "$PSScriptRoot\sign.ps1" "$component_src_dir\sign.crt" "$component_src_dir\$output"
                    }
                }
            }

            Copy-Item "$component_src_dir\$output" "$repo_dir\$kind"
        }
    }

    # copy testsign cert to local repo
    Copy-Item "$component_src_dir\sign.crt" $repo_dir

    # delete testsign cert from OS store
    & "$PSScriptRoot\delete-cert.ps1" "$component_src_dir\sign.crt"
}
