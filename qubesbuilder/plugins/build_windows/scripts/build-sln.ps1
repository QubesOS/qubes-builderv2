# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2024 Rafał Wojdyła <omeg@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Generic script for building VS solutions using EWDK

param(
    [Parameter(Mandatory=$true)] [string]$solution,
    [string]$configuration = "Release",
    [string]$repo = "",  # root of local builder repository with dependencies, sets QUBES_REPO env variable
    [int]$threads = 1,
    [switch]$testsign = $false,  # used to set TEST_SIGN env variable so the installer can bundle public certs
    [switch]$noisy = $false,
    [switch]$log = $false
)

$ErrorActionPreference = "Stop"

$arch = "x64"
$ewdk_arch = "x86_amd64"
$log_file = "msbuild.binlog"

Write-Host "Building $solution, $configuration, $arch, $threads thread(s)"

# Get EWDK root from the environment or find it if not set
if (Test-Path -Path env:EWDK_PATH) {
    $ewdk_path = $env:EWDK_PATH
} else {
    foreach ($drive in Get-PSDrive) {
        if ($drive.Provider.Name -eq "FileSystem") {
            $root = $drive.Root
            $path = "$root\LaunchBuildEnv.cmd"
            if (Test-Path -Path $path) {
                $ewdk_path = $root
                $env:EWDK_PATH = $ewdk_path
                break
            }
        }
    }
}

if ($ewdk_path -eq $null) {
    Write-Error "EWDK not found. If it's not attached as a drive, set its location in the EWDK_PATH environment variable."
}

$msbuild = "$ewdk_path\Program Files\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe"
if (! (Test-Path -Path $msbuild)) {
    Write-Error "$msbuild not found."
}

# Launch EWDK's environment setup script and grab variables that were set
$ewdk_env_cmd = "$ewdk_path\BuildEnv\SetupBuildEnv.cmd"
$ewdk_vars_txt = cmd /c "$ewdk_env_cmd $ewdk_arch > nul & set"

foreach ($line in $ewdk_vars_txt) {
    $kv = $line.split("=")
    $var_name = $kv[0]
    $var_value = $kv[1]
    if (! (Test-Path -Path "env:$var_name")) {
        Set-Item -Path "env:$var_name" -Value $var_value
    }
}

# Prepare environment for build
$ewdk_version = $env:Version_Number
if ($ewdk_version -eq $null) {
    Write-Error "EWDK environment initialization failed."
}

$ewdk_inc_dir = "$ewdk_path\Program Files\Windows Kits\10\Include\$ewdk_version"
$ewdk_inc = "$ewdk_inc_dir\shared;$ewdk_inc_dir\um;$ewdk_inc_dir\ucrt"

$ewdk_lib_dir = "$ewdk_path\Program Files\Windows Kits\10\Lib\$ewdk_version"
$ewdk_lib = "$ewdk_lib_dir\um\$arch;$ewdk_lib_dir\ucrt\$arch"

$env:WindowsSDK_IncludePath = $ewdk_inc
Set-Item -Path "env:WindowsSDK_LibraryPath_$arch" -Value $ewdk_lib
$env:PATH += ";$ewdk_path\Program Files\Windows Kits\10\bin\$ewdk_version\$arch"

$build_args = @("$solution", "-restore", "-t:Rebuild", "-p:Platform=$arch", "-p:Configuration=$configuration", "-m:$threads", "-nologo")

if (! $noisy) {
    $build_args += "-v:quiet"
}

if ($log) {
    $build_args += "-bl:$log_file"
}

if ($testsign) {
    $env:TEST_SIGN = 1
}

# Iterate over builder's local repository to collect dependencies
if ($repo -ne "") {
	$env:QUBES_REPO = $repo
	foreach ($dep in Get-ChildItem -Path $repo) {
		# strip version numbers from directories so projects can use constant paths for deps
		if ($dep.name.lastindexof('_') -ge 0) {
			$new_dep = $dep.name.substring(0, $dep.name.lastindexof('_'))
			mv "$repo\$dep" "$repo\$new_dep"
			$dep = $new_dep
		}
		$inc_path = "$repo\$dep\inc"
		if (Test-Path -Path $inc_path) {
			$env:QUBES_INCLUDES += ";$inc_path"
		}
		$lib_path = "$repo\$dep\lib"
		if (Test-Path -Path $lib_path) {
			$env:QUBES_LIBS += ";$lib_path"
		}
	}
}

# Start-Process -Wait hangs here for some reason, but waiting separately works properly
# seems to be related to msbuild leaving some worker processes running
# TODO: investigate, this doesn't return correct exit code if build fails
# ($proc.ExitCode is null?!)
$proc = Start-Process -FilePath $msbuild -NoNewWindow -PassThru -ArgumentList $build_args
$proc.WaitForExit()
return $proc.ExitCode
