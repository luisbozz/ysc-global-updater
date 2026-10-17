# Extracts every weapon*.meta from the game's RPF archives into $Out (CodeWalker).
param([Parameter(Mandatory)][string]$Game, [Parameter(Mandatory)][string]$Out, [Parameter(Mandatory)][string]$CodeWalker)
$ErrorActionPreference = 'Continue'
[Reflection.Assembly]::LoadFrom($CodeWalker) | Out-Null
New-Item -ItemType Directory -Force -Path $Out | Out-Null
[CodeWalker.GameFiles.GTA5Keys]::LoadFromPath($Game, $true, $null)
$noop = [Action[string]]{ param($s) }
function Walk($rpf) {
  foreach ($e in $rpf.AllEntries) {
    if ($e.Name -match '^weapon.*\.meta$') {
      try {
        $data = $e.File.ExtractFile($e)
        [IO.File]::WriteAllBytes((Join-Path $Out ($e.Path -replace '[\\/:]', '__')), $data)
      } catch { Write-Output "ERR $($e.Path)" }
    }
  }
  if ($rpf.Children) { foreach ($c in $rpf.Children) { Walk $c } }
}
foreach ($f in (Get-ChildItem $Game -Filter *.rpf -Recurse -ErrorAction SilentlyContinue)) {
  try {
    $rpf = New-Object CodeWalker.GameFiles.RpfFile($f.FullName, $f.FullName.Substring($Game.Length + 1))
    $rpf.ScanStructure($noop, $noop)
    Walk $rpf
  } catch { Write-Output "ERR $($f.FullName) $($_.Exception.Message)" }
}
Write-Output DONE
