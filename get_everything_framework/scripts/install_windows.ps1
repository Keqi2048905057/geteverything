param(
    [switch]$CheckOnly,
    [switch]$SkipSystem,
    [switch]$SkipPythonDeps,
    [switch]$SkipGoTools,
    [switch]$WithOptional,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$RequirementsFile = Join-Path $RootDir "requirement.txt"
$ScriptDir = $PSScriptRoot
$GoBin = Join-Path $env:USERPROFILE "go\bin"
$InstallSummary = [ordered]@{}

$WingetPackages = [ordered]@{
    "python" = "Python.Python.3"
    "go" = "GoLang.Go"
    "git" = "Git.Git"
    "nmap" = "Insecure.Nmap"
}

$GoTools = [ordered]@{
    "subfinder"   = "github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest"
    "shuffledns"  = "github.com/projectdiscovery/shuffledns/cmd/shuffledns@latest"
    "alterx"      = "github.com/projectdiscovery/alterx/cmd/alterx@latest"
    "gospider"    = "github.com/jaeles-project/gospider@latest"
    "dnsx"        = "github.com/projectdiscovery/dnsx/cmd/dnsx@latest"
    "httpx"       = "github.com/projectdiscovery/httpx/cmd/httpx@latest"
    "naabu"       = "github.com/projectdiscovery/naabu/v2/cmd/naabu@latest"
    "waybackurls" = "github.com/tomnomnom/waybackurls@latest"
    "katana"      = "github.com/projectdiscovery/katana/cmd/katana@latest"
    "assetfinder" = "github.com/tomnomnom/assetfinder@latest"
}

$ExpectedTools = @(
    "subfinder", "dnsx", "httpx", "http-x", "naabu", "nmap", "katana", "gospider",
    "waybackurls", "feroxbuster", "dirsearch", "oneforall", "enscan", "assetfinder",
    "shuffledns", "alterx", "amass"
)

function Set-ToolStatus {
    param([string]$Name, [string]$Status)
    $InstallSummary[$Name.ToLower()] = $Status
}

function Test-Command {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-NativeCommand {
    param([string[]]$Command)
    Write-Host "[*] $($Command -join ' ')"
    if ($DryRun) {
        return
    }
    & $Command[0] @($Command[1..($Command.Count - 1)])
}

function Ensure-GoBin {
    if (-not $DryRun) {
        New-Item -ItemType Directory -Path $GoBin -Force | Out-Null
    }
}

function Add-UserPath {
    param([string]$PathToAdd)
    if (-not $PathToAdd) {
        return
    }

    $currentProcessPath = [Environment]::GetEnvironmentVariable("PATH", "Process")
    if (($currentProcessPath -split ";") -notcontains $PathToAdd) {
        [Environment]::SetEnvironmentVariable("PATH", "$currentProcessPath;$PathToAdd", "Process")
    }

    $userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    if (($userPath -split ";") -contains $PathToAdd) {
        return
    }

    Write-Host "[*] Add user PATH: $PathToAdd"
    if (-not $DryRun) {
        $newPath = if ($userPath) { "$userPath;$PathToAdd" } else { $PathToAdd }
        [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    }
}

function Update-ProcessPathFromRegistry {
    $machinePath = [Environment]::GetEnvironmentVariable("PATH", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    [Environment]::SetEnvironmentVariable("PATH", "$machinePath;$userPath", "Process")
}

function Resolve-ToolPath {
    param([string]$Name)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    return $null
}

function Get-PythonCommand {
    if (Test-Command "py") { return "py" }
    if (Test-Command "python") { return "python" }
    return $null
}

function Ensure-HttpxAlias {
    $target = Join-Path $GoBin "httpx.exe"
    $aliasPath = Join-Path $GoBin "http-x.cmd"

    if (-not (Test-Path $target)) {
        Write-Host "[!] ProjectDiscovery httpx.exe not found at: $target"
        return
    }

    Write-Host "[*] Create http-x alias: $aliasPath"
    if (-not $DryRun) {
        @(
            "@echo off",
            "`"$target`" %*"
        ) | Set-Content -Path $aliasPath -Encoding ASCII
    }
}

function Test-HttpxInstallation {
    $expectedHttpx = Join-Path $GoBin "httpx.exe"
    $expectedAlias = Join-Path $GoBin "http-x.cmd"
    $resolvedHttpx = Resolve-ToolPath "httpx"
    $resolvedAlias = Resolve-ToolPath "http-x"

    Write-Host "[i] Expected ProjectDiscovery httpx: $expectedHttpx"
    Write-Host "[i] where httpx => $resolvedHttpx"
    Write-Host "[i] where http-x => $resolvedAlias"

    if (-not (Test-Path $expectedHttpx)) { return $false }
    if (-not (Test-Path $expectedAlias)) { return $false }
    if ($resolvedAlias -ne $expectedAlias) { return $false }

    if ($resolvedHttpx -and ($resolvedHttpx -ne $expectedHttpx)) {
        Write-Host "[!] httpx resolves to another executable. The project will use http-x instead."
    }

    Write-Host "[ok] ProjectDiscovery httpx alias is ready"
    return $true
}

function Install-WingetPackage {
    param([string]$CommandName, [string]$PackageId)

    if (Test-Command $CommandName) {
        Write-Host "[=] $CommandName already exists in PATH"
        return
    }

    if (-not (Test-Command "winget")) {
        Write-Host "[!] winget is not available. Install $CommandName manually."
        return
    }

    Invoke-NativeCommand @(
        "winget", "install", "--id", $PackageId, "-e",
        "--accept-source-agreements", "--accept-package-agreements"
    )
}

function Install-SystemDependencies {
    Write-Host "`n=== System dependencies ==="
    foreach ($name in $WingetPackages.Keys) {
        Install-WingetPackage $name $WingetPackages[$name]
    }

    Update-ProcessPathFromRegistry
    $goInstallDir = Join-Path $env:ProgramFiles "Go\bin"
    if (Test-Path $goInstallDir) {
        Add-UserPath $goInstallDir
    }
    Add-UserPath $GoBin
}

function Install-PythonDependencies {
    Write-Host "`n=== Python dependencies ==="
    $python = Get-PythonCommand
    if (-not $python) {
        Write-Host "[!] Python is not installed or not in PATH."
        return
    }
    if (-not (Test-Path $RequirementsFile)) {
        Write-Host "[!] Missing requirements file: $RequirementsFile"
        return
    }

    Invoke-NativeCommand @($python, "-m", "pip", "install", "--upgrade", "pip")
    Invoke-NativeCommand @($python, "-m", "pip", "install", "-r", $RequirementsFile)
}

function Install-Amass {
    Write-Host "`n=== amass ==="
    if (Test-Command "amass") {
        Write-Host "[=] amass already exists in PATH"
        return
    }

    if (Test-Command "winget") {
        Invoke-NativeCommand @(
            "winget", "install", "--id", "OWASP.Amass", "-e",
            "--accept-source-agreements", "--accept-package-agreements"
        )
    } else {
        Write-Host "[!] Install amass manually from GitHub Releases."
    }
}

function Install-GoTools {
    Write-Host "`n=== Go tools ==="
    if (-not (Test-Command "go")) {
        Write-Host "[!] Go is not installed or not in PATH."
        return
    }

    Ensure-GoBin
    Add-UserPath $GoBin

    foreach ($tool in $GoTools.Keys) {
        $exePath = Join-Path $GoBin ($tool + ".exe")
        if (($tool -ne "httpx") -and (Test-Path $exePath)) {
            Write-Host "[=] $tool already installed in Go bin"
            continue
        }
        if (($tool -eq "httpx") -and (Test-Path (Join-Path $GoBin "httpx.exe"))) {
            Write-Host "[=] httpx already installed in Go bin"
            continue
        }
        Invoke-NativeCommand @("go", "install", $GoTools[$tool])
    }

    Ensure-HttpxAlias
    Test-HttpxInstallation | Out-Null
    Install-Amass
}

function Find-LocalBinary {
    param([string[]]$Names)
    foreach ($name in $Names) {
        $candidate = Join-Path $ScriptDir $name
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    return $null
}

function Copy-LocalBinary {
    param(
        [string]$ToolName,
        [string[]]$SourceNames,
        [string]$TargetFileName,
        [string]$HelpArg = "-h"
    )

    Ensure-GoBin
    $source = Find-LocalBinary -Names $SourceNames
    if (-not $source) {
        Write-Host "[WARN] $ToolName binary not found beside installer."
        Set-ToolStatus $ToolName "failed"
        return
    }

    $target = Join-Path $GoBin $TargetFileName
    Write-Host "[*] Copy $source -> $target"
    if (-not $DryRun) {
        Copy-Item $source $target -Force
    }

    if ($DryRun -or (Test-Path $target)) {
        if (-not $DryRun) {
            try {
                & $target $HelpArg *> $null
            } catch {
                Write-Host "[WARN] $ToolName exists but help command is not supported."
            }
        }
        Set-ToolStatus $ToolName "success"
    } else {
        Set-ToolStatus $ToolName "failed"
    }
}

function Install-Feroxbuster {
    Write-Host "`n=== feroxbuster ==="
    Ensure-GoBin

    $FeroxUrl = "https://github.com/epi052/feroxbuster/releases/download/v2.13.1/x86-windows-feroxbuster.exe.zip"
    $TempZip = Join-Path $env:TEMP "feroxbuster.zip"
    $TempDir = Join-Path $env:TEMP "feroxbuster_extract"
    $FeroxTarget = Join-Path $GoBin "feroxbuster.exe"

    if (Test-Path $FeroxTarget) {
        Write-Host "[=] feroxbuster already installed"
        Set-ToolStatus "feroxbuster" "skipped"
        return
    }

    if (-not $DryRun) {
        Invoke-WebRequest -Uri $FeroxUrl -OutFile $TempZip
        if (Test-Path $TempDir) {
            Remove-Item $TempDir -Recurse -Force
        }
        New-Item -ItemType Directory -Path $TempDir -Force | Out-Null
        Expand-Archive -Path $TempZip -DestinationPath $TempDir -Force
        $FeroxExe = Get-ChildItem -Path $TempDir -Recurse -Filter "feroxbuster.exe" | Select-Object -First 1
        if ($FeroxExe) {
            Copy-Item $FeroxExe.FullName $FeroxTarget -Force
            Write-Host "[OK] feroxbuster copied to $FeroxTarget"
        } else {
            Write-Host "[WARN] feroxbuster.exe not found after extraction"
        }
    } else {
        Write-Host "[*] Dry run download: $FeroxUrl"
    }

    if ($DryRun -or (Test-Path $FeroxTarget)) {
        if (-not $DryRun) {
            try { & $FeroxTarget --version *> $null } catch { try { & $FeroxTarget -h *> $null } catch {} }
        }
        Set-ToolStatus "feroxbuster" "success"
    } else {
        Set-ToolStatus "feroxbuster" "failed"
    }
}

function Install-ENScan {
    Write-Host "`n=== enscan ==="
    Ensure-GoBin

    $target = Join-Path $GoBin "enscan.exe"
    if (Test-Path $target) {
        Write-Host "[=] enscan already installed"
        Set-ToolStatus "enscan" "skipped"
        return
    }

    if (-not (Test-Command "go")) {
        Write-Host "[WARN] Go is not installed or not in PATH, cannot build enscan."
        Set-ToolStatus "enscan" "failed"
        return
    }

    if (-not (Test-Command "git")) {
        Write-Host "[WARN] Git is not installed or not in PATH, cannot fetch ENScan_GO."
        Set-ToolStatus "enscan" "failed"
        return
    }

    $repoDir = Join-Path $env:TEMP "ENScan_go_install"
    if (-not $DryRun) {
        if (Test-Path $repoDir) {
            Remove-Item $repoDir -Recurse -Force
        }
        Invoke-NativeCommand @("git", "clone", "--depth", "1", "https://github.com/wgpsec/ENScan_GO.git", $repoDir)
        Push-Location $repoDir
        try {
            $oldGobin = $env:GOBIN
            $env:GOBIN = $GoBin
            Invoke-NativeCommand @("go", "install", ".")
        } finally {
            $env:GOBIN = $oldGobin
            Pop-Location
        }

        $builtExe = Join-Path $GoBin "ENScan.exe"
        if ((Test-Path $builtExe) -and (-not (Test-Path $target))) {
            Move-Item $builtExe $target -Force
        }
    } else {
        Write-Host "[*] Dry run clone/build: https://github.com/wgpsec/ENScan_GO.git"
    }

    if ($DryRun -or (Test-Path $target)) {
        if (-not $DryRun) {
            try { & $target -h *> $null } catch { try { & $target -v *> $null } catch {} }
        }
        Set-ToolStatus "enscan" "success"
    } else {
        Set-ToolStatus "enscan" "failed"
    }
}

function Install-OptionalTools {
    Write-Host "`n=== Optional tools ==="
    Install-Feroxbuster
    Copy-LocalBinary -ToolName "dirsearch" -SourceNames @("dirsearch.exe", "Dirsearch.exe") -TargetFileName "dirsearch.exe"
    Copy-LocalBinary -ToolName "oneforall" -SourceNames @("oneforall.exe", "OneForAll.exe", "one_for_all.exe") -TargetFileName "oneforall.exe"
    Install-ENScan
}

function Test-Environment {
    Write-Host "`n=== Verification ==="
    foreach ($tool in $ExpectedTools) {
        if (Test-Command $tool) {
            Write-Host "[ok] $tool"
        } else {
            Write-Host "[--] $tool not found"
        }
    }
    Test-HttpxInstallation | Out-Null
}

function Print-InstallSummary {
    Write-Host "`nTool install summary:"
    foreach ($tool in @("ENScan", "OneForAll", "dirsearch", "naabu", "feroxbuster")) {
        $key = $tool.ToLower()
        $status = if ($InstallSummary.Contains($key)) { $InstallSummary[$key] } else { "skipped" }
        Write-Host ("- {0}: {1}" -f $tool, $status)
    }

    Write-Host "`nGo bin path:"
    Write-Host ("- Windows: {0}" -f $GoBin)

    Write-Host "`nPATH status:"
    $processPath = [Environment]::GetEnvironmentVariable("PATH", "Process")
    if (($processPath -split ";") -contains $GoBin) {
        Write-Host "- Go bin is already in PATH"
    } else {
        Write-Host "- Go bin is not in PATH. Please add it manually."
    }
}

Write-Host "get_everything_framework Windows installer"
Write-Host "Project root: $RootDir"

if ($CheckOnly) {
    Test-Environment
    Print-InstallSummary
    exit 0
}

if (-not $SkipSystem) { Install-SystemDependencies }
if (-not $SkipPythonDeps) { Install-PythonDependencies }
if (-not $SkipGoTools) { Install-GoTools }

Install-OptionalTools

if (Test-Path (Join-Path $GoBin "naabu.exe")) { Set-ToolStatus "naabu" "success" } else { Set-ToolStatus "naabu" "failed" }
if (-not $InstallSummary.Contains("oneforall")) { Set-ToolStatus "oneforall" "skipped" }
if (-not $InstallSummary.Contains("dirsearch")) { Set-ToolStatus "dirsearch" "skipped" }
if (-not $InstallSummary.Contains("enscan")) { Set-ToolStatus "enscan" "skipped" }
if (-not $InstallSummary.Contains("feroxbuster")) { Set-ToolStatus "feroxbuster" "skipped" }

Test-Environment
Print-InstallSummary
Write-Host "`n[+] Done. Reopen PowerShell if newly installed commands are still not found."
