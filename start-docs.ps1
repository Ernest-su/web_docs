Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WebDocsRoot = Resolve-Path $ScriptDir

function Show-DocsHelp {
    @"
Usage:
  .\start-docs.ps1 [options]
  .\start-docs.cmd [options]

Options:
  -h, --help, help       Show this help message.
  -p, --port PORT        Run the docs server on PORT.
  --host HOST            Bind the docs server to HOST.
  --no-open              Do not open the browser automatically.
  --title TITLE          Set the browser page title.
  --git-pull             Enable background git pull. Enabled by default.
  --no-git-pull          Disable background git pull.
  --git-pull-interval SECONDS
                         Seconds between background git pull attempts.
  --git-pull-remote REMOTE
                         Optional git remote for pull.
  --git-pull-branch BRANCH
                         Optional git branch for pull.
  --git-pull-timeout SECONDS
                         Seconds before a git pull attempt times out.
  --mermaid-url URL      Browser module URL for Mermaid.
  --marked-url URL       Browser script URL for marked.

Environment:
  PYTHON_BIN             Python executable to use.
  WEB_DOCS_HOST      Default host when --host is not provided.
  WEB_DOCS_PORT      Default port when --port is not provided.
  WEB_DOCS_GIT_PULL  Set to 0/false/no/off to disable auto-pull.
  WEB_DOCS_GIT_PULL_INTERVAL
  WEB_DOCS_GIT_PULL_REMOTE
  WEB_DOCS_GIT_PULL_BRANCH
  WEB_DOCS_GIT_PULL_TIMEOUT
  WEB_DOCS_MERMAID_URL
  WEB_DOCS_MARKED_URL

Examples:
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  python -m pip install -r requirements.txt
  .\start-docs.ps1 --port 8091
  .\start-docs.ps1 -p 8091 --no-open
  .\start-docs.ps1 --git-pull-interval 600
"@
}

function Convert-DocsArgs {
    param(
        [string[]]$InputArgs
    )

    $result = New-Object System.Collections.Generic.List[string]
    for ($index = 0; $index -lt $InputArgs.Count; $index++) {
        $arg = $InputArgs[$index]
        if ($arg -in @("-h", "--help", "help", "/?")) {
            Show-DocsHelp
            exit 0
        } elseif ($arg -in @("-p", "--port")) {
            if ($index + 1 -ge $InputArgs.Count) {
                throw "Missing value for $arg."
            }
            $result.Add("--port")
            $result.Add($InputArgs[$index + 1])
            $index++
        } elseif ($arg.StartsWith("--port=")) {
            $result.Add("--port")
            $result.Add($arg.Substring("--port=".Length))
        } else {
            $result.Add($arg)
        }
    }
    return $result.ToArray()
}

function Find-Python {
    if ($env:PYTHON_BIN) {
        return @($env:PYTHON_BIN)
    }
    $venvPython = Join-Path $WebDocsRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return @($venvPython)
    }
    $candidates = @(
        @("py", "-3.14"),
        @("py", "-3"),
        @("python3.14"),
        @("python")
    )
    foreach ($candidate in $candidates) {
        $command = $candidate[0]
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
            continue
        }
        $candidateArgs = @()
        if ($candidate.Count -gt 1) {
            $candidateArgs = $candidate[1..($candidate.Count - 1)]
        }
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $command @candidateArgs --version *> $null
            if ($LASTEXITCODE -eq 0) {
                $ErrorActionPreference = $previousErrorActionPreference
                return $candidate
            }
        } catch {
        } finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
    }
    throw "Python was not found. Install Python 3 and retry."
}

$python = @(Find-Python)
Set-Location $WebDocsRoot
$pythonArgs = @()
if ($python.Count -gt 1) {
    $pythonArgs = $python[1..($python.Count - 1)]
}
$docsArgs = @(Convert-DocsArgs -InputArgs $args)
& $python[0] @pythonArgs "$WebDocsRoot\serve-docs.py" @docsArgs
