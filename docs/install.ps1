# TagTeam installer for Windows. Run in PowerShell:
#   irm https://mop4r.github.io/tagteam/install.ps1 | iex
#
# 1. Checks Windows and Python 3.11+ (offers to install Python with winget if it is missing).
# 2. Downloads the latest TagTeam release package from GitHub and checks it against the SHA-256 that GitHub
#    publishes for it; nothing is extracted if it does not match.
# 3. Puts it in ~\.claude\skills\tagteam (where Claude Code finds it as a skill) and opens tagteam.py, the
#    installer and monitor, where you choose which AIs to sync.
# Nothing else is sent anywhere. Read this file before running it if you like: it is short on purpose.
# Everything runs inside one script block, so nothing (variables, functions, error settings) is left behind in your
# PowerShell session, and a failure prints one line in red instead of closing the window.

& {
    $ErrorActionPreference = 'Stop'
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $Pt = (Get-Culture).Name -like 'pt*'
    function Say([string]$pt, [string]$en) { if ($Pt) { Write-Host $pt } else { Write-Host $en } }
    function Fail([string]$pt, [string]$en) { throw $(if ($Pt) { $pt } else { $en }) }

    $Api = 'https://api.github.com/repos/mop4r/tagteam/releases/latest'
    $Dest = Join-Path $HOME '.claude\skills\tagteam'
    $Keep = @('SKILL.md', 'README.md', 'README.pt-BR.md', 'LICENSE', 'scripts', 'references', 'assets')

    function Find-Python {
        # Cada candidato e testado rodando-o: na pasta WindowsApps pode estar o py.exe de verdade (Python Install
        # Manager) ou so o atalho da loja, que com argumentos apenas devolve erro (nao abre nada) e fica de fora.
        $candidatos = @()
        $candidatos += Get-Command py, python, python3 -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }
        foreach ($padrao in "$env:LOCALAPPDATA\Python\pythoncore-3*\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe") {
            $candidatos += Get-ChildItem $padrao -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object { $_.FullName }
        }
        foreach ($exe in ($candidatos | Select-Object -Unique)) {
            $saida = Test-Python $exe
            if ($saida) { return $saida }
        }
        return $null
    }

    function Test-Python([string]$exe) {
        # entrada fechada e prazo: um candidato que pergunte algo (ou trave) nao prende o instalador
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $exe
        $extra = if ((Split-Path $exe -Leaf) -eq 'py.exe') { '-3 ' } else { '' }
        $psi.Arguments = $extra + '-c "import sys; print(sys.executable); print(int(sys.version_info >= (3, 11)))"'
        $psi.UseShellExecute = $false
        $psi.RedirectStandardInput = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        try { $p = [System.Diagnostics.Process]::Start($psi) } catch { return $null }
        $p.StandardInput.Close()
        if (-not $p.WaitForExit(15000)) {
            taskkill /PID $p.Id /T /F | Out-Null
            return $null
        }
        $linhas = $p.StandardOutput.ReadToEnd().Trim() -split "`r?`n"
        if ($p.ExitCode -eq 0 -and $linhas.Count -ge 2 -and $linhas[1].Trim() -eq '1') { return $linhas[0].Trim() }
        return $null
    }

    function Get-Python {
        $python = Find-Python
        if ($python) { return $python }
        Say 'Python 3.11 ou mais novo nao foi encontrado.' 'Python 3.11 or newer was not found.'
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Fail 'Instale o Python em https://www.python.org/downloads/ e rode este comando de novo.' 'Install Python from https://www.python.org/downloads/ and run this command again.'
        }
        $resp = Read-Host $(if ($Pt) { 'Instalar o Python 3.12 agora pelo winget (oficial da Microsoft)? [S/n]' } else { 'Install Python 3.12 now with winget (Microsoft official)? [Y/n]' })
        if ($resp -and $resp.Trim().ToLower()[0] -notin @('s', 'y')) {
            Fail 'Sem Python o TagTeam nao roda. Nada foi instalado.' 'TagTeam needs Python. Nothing was installed.'
        }
        winget install --exact --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        $python = Find-Python
        if (-not $python) { Fail 'O Python foi instalado, mas nao achei o python.exe. Abra um PowerShell novo e rode de novo.' 'Python was installed but python.exe was not found. Open a new PowerShell and run again.' }
        return $python
    }

    function Install-Package {
        Say 'Baixando a versao mais nova do GitHub...' 'Downloading the latest version from GitHub...'
        $rel = Invoke-RestMethod -Uri $Api -Headers @{ 'User-Agent' = 'tagteam-installer'; 'Accept' = 'application/vnd.github+json' } -TimeoutSec 30
        $asset = $rel.assets | Where-Object { $_.name -match '^tagteam-v?\d+\.\d+\.\d+\.zip$' } | Select-Object -First 1
        if (-not $asset -or $asset.digest -notmatch '^sha256:([0-9a-f]{64})$') {
            Fail 'A release mais nova nao traz o pacote com SHA-256. Nada foi instalado.' 'The latest release has no package with a SHA-256. Nothing was installed.'
        }
        $esperado = $Matches[1]
        if ($asset.browser_download_url -notlike 'https://github.com/*') { Fail 'Endereco do pacote fora do GitHub. Nada foi instalado.' 'Package URL is not on GitHub. Nothing was installed.' }
        $tmp = Join-Path ([IO.Path]::GetTempPath()) ("tagteam-" + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $tmp | Out-Null
        try {
            $zip = Join-Path $tmp $asset.name
            Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing -TimeoutSec 120
            $hash = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash.ToLower()
            if ($hash -ne $esperado) { Fail 'O pacote baixado nao confere com o SHA-256 da release. Nada foi instalado.' 'The downloaded package does not match the release SHA-256. Nothing was installed.' }
            Say "Pacote $($rel.tag_name) conferido (SHA-256)." "Package $($rel.tag_name) verified (SHA-256)."
            $ext = Join-Path $tmp 'x'
            Expand-Archive -Path $zip -DestinationPath $ext
            $raiz = Join-Path $ext 'tagteam'
            if (-not (Test-Path (Join-Path $raiz 'scripts\tagteam.py'))) { Fail 'Pacote sem a estrutura do TagTeam. Nada foi instalado.' 'Package does not look like TagTeam. Nothing was installed.' }
            New-Item -ItemType Directory -Force -Path $Dest | Out-Null
            foreach ($item in $Keep) {
                $origem = Join-Path $raiz $item
                if (Test-Path $origem) { Copy-Item -Path $origem -Destination $Dest -Recurse -Force }
            }
            Say "Instalado em $Dest" "Installed in $Dest"
        } finally {
            Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        }
    }

    try {
        Say 'TagTeam: uma conversa entre Claude Code, Gemini, Grok, Codex e Cursor.' 'TagTeam: one conversation across Claude Code, Gemini, Grok, Codex and Cursor.'
        if ([Environment]::OSVersion.Version.Major -lt 10) { Fail 'O TagTeam precisa do Windows 10 ou 11.' 'TagTeam needs Windows 10 or 11.' }
        $Python = Get-Python
        Say "Python: $Python" "Python: $Python"
        if (Test-Path (Join-Path $Dest '.git')) {
            Say "Ja instalado com git em $Dest (atualize com git pull). Abrindo o TagTeam..." "Already installed with git in $Dest (update with git pull). Opening TagTeam..."
        } else {
            Install-Package
        }
        & $Python -I (Join-Path $Dest 'scripts\tagteam.py')
    } catch {
        Write-Host ""
        Write-Host $_.Exception.Message -ForegroundColor Red
    }
}
