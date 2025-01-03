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
    # root of local builder repository with dependencies, sets QUBES_REPO env variable
    [Parameter(Mandatory=$true)] [string]$repo,
    # directory with distfiles (additional downloaded source files), sets QUBES_DISTFILES env variable
    [string]$distfiles = "",
    [string]$configuration = "Release",
    [int]$threads = 1,
    [switch]$testsign = $false,  # used to set TEST_SIGN env variable so the installer can bundle public certs
    [switch]$noisy = $false,
    [switch]$log = $false
)

$ErrorActionPreference = "Stop"

$arch = "x64"
$log_file = (Split-Path -Parent -Resolve $solution) + "\msbuild.binlog"

. $PSScriptRoot\common.ps1

LogStart
LogInfo "Building $solution, $configuration, $arch, $threads thread(s)"

$ewdk = Find-EWDK

if ($ewdk -eq $null) {
    LogError "EWDK not found. If it's not attached as a drive, set its location in the EWDK_PATH environment variable."
}

$env:EWDK_PATH = $ewdk
$msbuild = "$env:EWDK_PATH\Program Files\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe"
if (! (Test-Path -Path $msbuild)) {
    LogError "$msbuild not found."
}

# Prepare environment for build
Launch-EWDK

$ewdk_version = $env:Version_Number
if ($ewdk_version -eq $null) {
    LogError "EWDK environment initialization failed."
}

$ewdk_inc_dir = "$env:EWDK_PATH\Program Files\Windows Kits\10\Include\$ewdk_version"
$ewdk_inc = "$ewdk_inc_dir\shared;$ewdk_inc_dir\um;$ewdk_inc_dir\ucrt"

$ewdk_lib_dir = "$env:EWDK_PATH\Program Files\Windows Kits\10\Lib\$ewdk_version"
$ewdk_lib = "$ewdk_lib_dir\um\$arch;$ewdk_lib_dir\ucrt\$arch"

$env:WindowsSDK_IncludePath = $ewdk_inc
Set-Item -Path "env:WindowsSDK_LibraryPath_$arch" -Value $ewdk_lib
$env:PATH += ";$env:EWDK_PATH\Program Files\Windows Kits\10\bin\$ewdk_version\$arch"

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
if (! (Test-Path $repo -PathType Container)) {
    LogError "Invalid repository directory: $repo"
}

$env:QUBES_REPO = Resolve-Path $repo
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

LogDebug "QUBES_INCLUDES = $env:QUBES_INCLUDES"
LogDebug "QUBES_LIBS = $env:QUBES_LIBS"

if ($distfiles -ne "") {
    if (! (Test-Path $distfiles -PathType Container)) {
        LogError "Invalid distfiles directory: $distfiles"
    }
    $env:QUBES_DISTFILES = Resolve-Path $distfiles
    LogDebug "QUBES_DISTFILES = $env:QUBES_DISTFILES"
} else {
    LogDebug "no distfiles"
}

LogDebug "msbuild args: $build_args"

# Start-Process -Wait hangs here for some reason, but waiting separately works properly
# seems to be related to msbuild leaving some worker processes running
# TODO: investigate, this doesn't return correct exit code if build fails
# ($proc.ExitCode is null?!)
$proc = Start-Process -FilePath $msbuild -NoNewWindow -PassThru -ArgumentList $build_args
$proc.WaitForExit()
return $proc.ExitCode
