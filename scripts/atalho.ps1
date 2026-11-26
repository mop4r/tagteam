# Cria ou atualiza um atalho .lnk (chamado pelo instalar.py, com os valores em parametros: sem montar texto de comando).
# Escreve "igual" quando o atalho ja esta como pedido, "gravado" quando criou ou mudou.
param(
    [Parameter(Mandatory)][string]$Lnk,
    [Parameter(Mandatory)][string]$Alvo,
    [string]$Argumentos = "",
    [string]$Pasta = "",
    [string]$Icone = "",
    [switch]$Admin
)
$ErrorActionPreference = 'Stop'
$sh = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $Lnk) {
    $a = $sh.CreateShortcut($Lnk)
    $bytes = [IO.File]::ReadAllBytes($Lnk)
    $ehAdmin = [bool]($bytes[0x15] -band 0x20)
    if ($a.TargetPath -ieq $Alvo -and $a.Arguments -ceq $Argumentos -and $a.WorkingDirectory -ieq $Pasta -and
        ($Icone -eq "" -or $a.IconLocation -ieq $Icone) -and $ehAdmin -eq [bool]$Admin) {
        "igual"; return
    }
}
$s = $sh.CreateShortcut($Lnk)
$s.TargetPath = $Alvo
$s.Arguments = $Argumentos
$s.WorkingDirectory = $Pasta
if ($Icone -ne "") { $s.IconLocation = $Icone }
$s.Save()
# "Executar como administrador": bit 0x20 do byte 0x15 do cabecalho do .lnk (o COM nao expoe isso). Liga ou
# desliga conforme o pedido: o Save() do COM mantem o bit de um atalho que ja existia.
$bytes = [IO.File]::ReadAllBytes($Lnk)
$antes = $bytes[0x15]
if ($Admin) { $bytes[0x15] = $bytes[0x15] -bor 0x20 } else { $bytes[0x15] = $bytes[0x15] -band 0xDF }
if ($bytes[0x15] -ne $antes) { [IO.File]::WriteAllBytes($Lnk, $bytes) }
"gravado"
