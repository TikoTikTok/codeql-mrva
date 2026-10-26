<#
.SYNOPSIS
  Install the codeql-mrva skill into Claude Code, Hermes Agent and/or Codex (Windows PowerShell 5.1+).

.EXAMPLE
  .\install.ps1                                   # every agent detected on this machine (or all three)
  .\install.ps1 -Agents claude,codex              # a subset
  .\install.ps1 -Agents hermes -HermesProfile default,myprofile   # base directory plus one profile
  .\install.ps1 -Agents hermes -HermesProfile all # base directory plus every profile
  .\install.ps1 -Link                             # junction instead of copy, so git pull updates every agent
  .\install.ps1 -Project                          # project-level folders under the current directory
  .\install.ps1 -Uninstall

  User-level targets:
    Claude Code : ~\.claude\skills\codeql-mrva
    Hermes      : <hermes home>\skills\security\codeql-mrva                  (profile "default")
                  <hermes home>\profiles\NAME\skills\security\codeql-mrva    (profile NAME)
                  hermes home = $env:HERMES_HOME, else %LOCALAPPDATA%\hermes, else ~\.hermes
    Codex       : ~\.agents\skills\codeql-mrva
#>
param(
    [string[]]$Agents = @(),
    [string[]]$HermesProfile = @("default"),
    [switch]$Link,
    [switch]$Project,
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Name = "codeql-mrva"
$Src = Join-Path $Here "skills\$Name"
if (-not (Test-Path (Join-Path $Src "SKILL.md"))) { throw "$Src\SKILL.md not found; run from a clone of the repository" }

function Get-HermesHome {
    if ($env:HERMES_HOME) { return $env:HERMES_HOME }
    if ($env:LOCALAPPDATA -and (Test-Path (Join-Path $env:LOCALAPPDATA "hermes"))) { return (Join-Path $env:LOCALAPPDATA "hermes") }
    return (Join-Path $HOME ".hermes")
}

function Test-Agent([string]$cmd, [string[]]$dirs) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { return $true }
    foreach ($d in $dirs) { if (Test-Path $d) { return $true } }
    return $false
}

function Get-Targets([string]$agent) {
    switch ($agent) {
        "claude" { if ($Project) { return @(Join-Path $PWD ".claude\skills\$Name") } else { return @(Join-Path $HOME ".claude\skills\$Name") } }
        "codex"  { if ($Project) { return @(Join-Path $PWD ".agents\skills\$Name") } else { return @(Join-Path $HOME ".agents\skills\$Name") } }
        "hermes" {
            if ($Project) { return @(Join-Path $PWD ".hermes\skills\$Name") }
            $home_ = Get-HermesHome
            $out = @()
            foreach ($p in $HermesProfile) {
                if ($p -eq "default") { $out += (Join-Path $home_ "skills\security\$Name") }
                elseif ($p -eq "all") {
                    $out += (Join-Path $home_ "skills\security\$Name")
                    $profDir = Join-Path $home_ "profiles"
                    if (Test-Path $profDir) {
                        Get-ChildItem -Directory $profDir | ForEach-Object { $out += (Join-Path $_.FullName "skills\security\$Name") }
                    }
                } else {
                    if (-not (Test-Path (Join-Path $home_ "profiles\$p"))) { Write-Warning "Hermes profile '$p' not found under $home_\profiles; creating its skills directory anyway" }
                    $out += (Join-Path $home_ "profiles\$p\skills\security\$Name")
                }
            }
            return $out
        }
        default  { throw "unknown agent '$agent' (claude, hermes, codex)" }
    }
}

function Remove-Existing([string]$t) {
    if (-not (Test-Path $t)) { return }
    $item = Get-Item $t -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        # junction or symlink: remove the link only
        [IO.Directory]::Delete($t)
        return
    }
    if (-not (Test-Path (Join-Path $t "SKILL.md"))) { throw "$t exists and is not a skill folder; not touching it" }
    Remove-Item -Recurse -Force $t
}

if ($Agents.Count -eq 0) {
    $found = @()
    if (Test-Agent "claude" @((Join-Path $HOME ".claude"))) { $found += "claude" }
    if (Test-Agent "hermes" @((Get-HermesHome))) { $found += "hermes" }
    if (Test-Agent "codex" @((Join-Path $HOME ".codex"), (Join-Path $HOME ".agents"))) { $found += "codex" }
    if ($found.Count -eq 0) { $found = @("claude", "hermes", "codex") }
    $Agents = $found
}
# allow "-Agents claude,codex" and "-HermesProfile a,b" as one string
$Agents = @($Agents | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$HermesProfile = @($HermesProfile | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

foreach ($agent in $Agents) {
    foreach ($t in (Get-Targets $agent)) {
        if ($Uninstall) {
            if (Test-Path $t) { Remove-Existing $t; Write-Host "${agent}: removed $t" } else { Write-Host "${agent}: nothing at $t" }
            continue
        }
        $parent = Split-Path -Parent $t
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force $parent | Out-Null }
        Remove-Existing $t
        if ($Link) {
            try {
                New-Item -ItemType SymbolicLink -Path $t -Target $Src | Out-Null
                Write-Host "${agent}: linked $t -> $Src"
            } catch {
                New-Item -ItemType Junction -Path $t -Target $Src | Out-Null
                Write-Host "${agent}: junction $t -> $Src"
            }
        } else {
            Copy-Item -Recurse -Force $Src $t
            Write-Host "${agent}: copied to $t"
        }
    }
}

if (-not $Uninstall) {
    Write-Host ""
    Write-Host "Next:"
    Write-Host "  Claude Code : /codeql-mrva ...   (run /reload-skills in an open session)"
    Write-Host "  Hermes      : /codeql-mrva ...   (project installs need: hermes skills trust; other profiles: -HermesProfile NAME)"
    Write-Host '  Codex       : $codeql-mrva ...   (restart Codex if it does not appear)'
}
