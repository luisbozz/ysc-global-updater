# Reads the texts of the labels in $Labels (one per line) from every global.gxt2 of the six
# languages Xenvious has, base game and DLC, into $Out: label <TAB> rpf path <TAB> text (UTF-8).
param([Parameter(Mandatory)][string]$Game, [Parameter(Mandatory)][string]$Labels, [Parameter(Mandatory)][string]$Out, [Parameter(Mandatory)][string]$CodeWalker)
[Reflection.Assembly]::LoadFrom($CodeWalker) | Out-Null
[CodeWalker.GameFiles.GTA5Keys]::LoadFromPath($Game, $true, $null)
$noop = [Action[string]]{ param($s) }
$hashes = @{}
foreach ($l in (Get-Content $Labels)) { if ($l.Trim()) { $hashes[[uint32][CodeWalker.GameFiles.JenkHash]::GenHash($l.Trim().ToLowerInvariant())] = $l.Trim() } }
$global:lines = New-Object System.Collections.Generic.List[string]
function Walk($r) {
  foreach ($e in $r.AllEntries) {
    if ($e.Name -like '*.gxt2' -and ($e.Path -match '(american|german|russian|polish|french|chinesesimp)')) {
      try {
        $g = New-Object CodeWalker.GameFiles.Gxt2File
        $g.Load($r.ExtractFile($e), $e)
        foreach ($t in $g.TextEntries) { $k = [uint32]$t.Hash; if ($hashes.ContainsKey($k)) { $global:lines.Add("$($hashes[$k])`t$($e.Path)`t$($t.Text)") } }
      } catch {}
    }
  }
  foreach ($c in $r.Children) { Walk $c }
}
foreach ($f in (Get-ChildItem $Game -Filter *.rpf -Recurse -ErrorAction SilentlyContinue)) {
  try {
    $rpf = New-Object CodeWalker.GameFiles.RpfFile($f.FullName, $f.FullName.Substring($Game.Length + 1))
    $rpf.ScanStructure($noop, $noop)
    Walk $rpf
  } catch {}
}
[IO.File]::WriteAllLines($Out, $global:lines, (New-Object Text.UTF8Encoding($false)))
Write-Output "DONE $($global:lines.Count)"
