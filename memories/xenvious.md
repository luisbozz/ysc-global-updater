# Xenvious (/opt/Xenvious)

## Was es ist
- WPF-Desktop-App (GTA V Content Creator Helper), MVVM (Caliburn.Micro + CommunityToolkit.Mvvm/Source-Generatoren).
- **.NET Framework 4.8**, klassisches non-SDK `.csproj`, `packages.config`. ~66k LOC, 73 .cs, 22 XAML. Einzelprojekt.
- => **Windows-only**: baut/laeuft NICHT unter Linux/WSL (kein dotnet/msbuild/mono/WPF dort).

## Umgebung
- User: Windows + WSL2. VS-Code-Server laeuft in WSL (`/opt/Xenvious`). In WSL kein Build/Debug moeglich.
- Empfehlung: Xenvious NATIV unter Windows in VS Code oeffnen; Terminal = Git Bash (Unix-Tools + msbuild).
- Linux-Repos (global_extractor, ysc-global-updater = Python) bleiben in WSL.

## Wichtig
- OfflineData JSONs (`Xenvious/OfflineData/*.json`, props.json ~3,2 MB) sind `<EmbeddedResource>` und werden
  via `GetManifestResourceStream` in `OfflineData/OfflineData.cs` geladen -> Build-Action NICHT aendern.
- VS-Langsamkeit kommt v.a. von diesen grossen JSONs (Editor-Indexierung), nicht vom Projekt selbst.
- Moderne C#-Extension (Roslyn LSP, "C# standalone") unterstuetzt packages.config + Source-Generatoren NICHT
  gut -> Warnungen "MVVM Toolkit source generators might not be loaded" + NullRef-Designtime-Build.
  Das sind IntelliSense-Warnungen, KEINE Build-Fehler. `omnisharp.useModernNet` wirkt nicht (LSP != OmniSharp).
  Saubere Loesung: packages.config -> PackageReference migrieren (VS-Migrator).
- BUG (verifiziert): Versions-Mismatch CommunityToolkit.Mvvm. packages.config=8.4.0 (Zeile 6),
  csproj referenziert 8.4.2 (Zeilen 84 HintPath, 469 Import, 474 Error-Check). Build laeuft lokal nur,
  weil alter packages\CommunityToolkit.Mvvm.8.4.2\-Ordner noch existiert; sauberer `nuget restore` (=8.4.0)
  wuerde Build brechen. -> angleichen (vermutlich packages.config auf 8.4.2 hoch).

## Branch
- `feature/offline-mode` (1 Commit vor master): bettet Server-Daten ein, entfernt Updater, schaltet Features frei.

## Setup (angelegt, Stand 2026-07-09, noch untracked)
- `.vscode/settings.json`: omnisharp.useModernNet=false, dotnet.defaultSolution, search/watcher-excludes fuer grosse JSONs.
- `.vscode/tasks.json`: msbuild Build/Rebuild/Release + nuget restore, MSBuild via vswhere (fester Installer-Pfad).
- `.vscode/extensions.json`: ms-dotnettools.csharp (OmniSharp, besser fuer non-SDK als Dev Kit) + Copilot.
- Debuggen bleibt in Visual Studio.
- `.vscode/sftp.json` ist nur ein unkonfiguriertes Template (localhost/username), kein aktiver Sync.
