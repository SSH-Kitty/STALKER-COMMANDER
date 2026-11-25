; Inno Setup script for STALKER COMMANDER (Windows).
;
; Build from the repo root, after PyInstaller has produced dist\COMMANDER:
;
;   python packaging\windows\installer\make_images.py
;   ISCC packaging\windows\installer\COMMANDER.iss
;
; Output: dist\COMMANDER-Installer-<version>.exe
;
; Override on the command line when needed (CI passes the real version):
;   ISCC /DSourceDir=C:\path\to\COMMANDER /DAppVersion=1.4.1 /DNumericVersion=1.4.1 COMMANDER.iss

#ifndef AppVersion
  #define AppVersion "1.4.1"
#endif
#ifndef NumericVersion
  ; VersionInfoVersion must be digits only (AppVersion may be "1.4.0-unstable2").
  #define NumericVersion "1.4.1"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\..\dist\COMMANDER"
#endif

#define AppName "STALKER COMMANDER"
#define AppPublisher "SSH-Kitty"
#define AppUrl "https://github.com/SSH-Kitty/STALKER-COMMANDER"
#define AppExe "COMMANDER.exe"

[Setup]
; Never change this GUID: it is how a newer Setup finds and upgrades this install.
AppId={{67127C51-1884-40A2-9DD9-FC228192E4FD}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
AppCopyright=Copyright (C) {#AppPublisher}. GPL-3.0-or-later.
; Setup's own Details tab: blank fields are one more "unknown program" signal
; to antivirus heuristics.
VersionInfoVersion={#NumericVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoCopyright=Copyright (C) {#AppPublisher}. GPL-3.0-or-later.
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#NumericVersion}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoOriginalFileName=COMMANDER-Installer-{#AppVersion}.exe
; Qt 6.11 (PySide6) needs Windows 10 1809 or newer.
MinVersion=10.0.17763

; Per user only (no administrator prompt). No "all users" option: COMMANDER's
; settings, game folders and Windows changes are per user anyway, an admin
; install would need UAC for every silent self-update, and an uninstall
; elevated with another account's password would clean up that account's
; profile instead of the player's.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
CloseApplications=yes
RestartApplications=no
; Created by a running COMMANDER (commander_gui/main.py). Closing the window
; only sends it to the tray, so Setup could not close it and then failed on
; files in use; with the mutex Setup asks the user to quit it first.
AppMutex=STALKER-COMMANDER-running

; Looks: COMMANDER's black theme. "modern dark" forces dark mode; includetitlebar
; draws the title bar in the same style.
WizardStyle=modern dark includetitlebar
WizardBackColor=#000000
WizardBackColorDynamicDark=#000000
WizardImageFile=images\wizard-large-164x314.png,images\wizard-large-192x386.png,images\wizard-large-246x459.png,images\wizard-large-273x556.png,images\wizard-large-328x628.png,images\wizard-large-355x700.png,images\wizard-large-410x797.png
WizardImageFileDynamicDark=images\wizard-large-164x314.png,images\wizard-large-192x386.png,images\wizard-large-246x459.png,images\wizard-large-273x556.png,images\wizard-large-328x628.png,images\wizard-large-355x700.png,images\wizard-large-410x797.png
WizardSmallImageFile=images\wizard-small-55.png,images\wizard-small-64.png,images\wizard-small-80.png,images\wizard-small-96.png,images\wizard-small-110.png,images\wizard-small-128.png
WizardSmallImageFileDynamicDark=images\wizard-small-55.png,images\wizard-small-64.png,images\wizard-small-80.png,images\wizard-small-96.png,images\wizard-small-110.png,images\wizard-small-128.png
; Inno 6 hides the Welcome page by default; the big side image lives on it.
DisableWelcomePage=no
WizardImageAlphaFormat=defined
SetupIconFile=images\app.ico

LicenseFile=..\..\..\LICENSE
OutputDir=..\..\..\dist
OutputBaseFilename=COMMANDER-Installer-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
; The 150 MB payload is mostly already-compressed Qt/Python files.
LZMAUseSeparateProcess=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel1=Welcome to {#AppName}
WelcomeLabel2=This will install {#AppName} {#AppVersion} on your computer.%n%nCOMMANDER installs, updates, launches and repairs S.T.A.L.K.E.R. Anomaly with the GAMMA modpack.%n%nSetup can also install the Visual C++ and DirectX runtimes the game needs, so nothing else has to be set up by hand.
FinishedHeadingLabel=You're all set, Stalker
FinishedLabelNoIcons=COMMANDER is installed. Your game folders and settings were left untouched.
FinishedLabel=COMMANDER is installed. Your game folders and settings were left untouched.

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "runtimes"; Description: "Install the Visual C++ and DirectX runtimes the game needs (recommended; asks for administrator permission)"; GroupDescription: "Game requirements:"; Check: RuntimesMissing

[InstallDelete]
; An upgrade copies over the old install: Qt plugins or .pyd files the new
; version no longer has would be left in data and could be loaded.
; _internal is where builds before the rename kept them.
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Played once, on the last page (see [Code]); never installed.
Source: "..\..\..\commander_gui\assets\pda.wav"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
; COMMANDER's self-update (commander_gui/self_update.py, setup_command) runs
; Setup with /SILENT /RELAUNCH=1 after quitting, so the box above is skipped:
; this reopens it, as the user who started the update (not elevated, even when
; an all-users install made Setup ask for administrator rights).
; Keep this entry: the Setup being downloaded is the one that runs it.
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser skipifnotsilent; Check: RelaunchAfterUpdate

[Registry]
; "Start COMMANDER when I log in" (commander_gui/autostart.py) writes this
; value; never created here, only removed on uninstall so it does not point
; at a deleted exe.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "STALKER COMMANDER"; Flags: uninsdeletevalue dontcreatekey

; Uninstall also takes back what COMMANDER changed in Windows (see
; CurUninstallStepChanged): the per-game graphics card choices, its Steam
; shortcuts and, after asking, the Defender exclusions. COMMANDER's settings,
; logs and backups go only when the user says so. The Anomaly / GAMMA folders
; (the game, saves, mods) are never touched.

[Code]
// ---------------------------------------------------------------- sound
// Inno Setup has no sound support of its own; winmm's PlaySound does it.
function PlaySound(pszSound: String; hmod: Integer; fdwSound: Integer): Boolean;
  external 'PlaySoundW@winmm.dll stdcall';

const
  SND_ASYNC = $0001;
  SND_NODEFAULT = $0002;
  SND_FILENAME = $00020000;

procedure PlayFinishSound;
var
  Played: Boolean;
begin
  if WizardSilent then
    Exit;
  ExtractTemporaryFile('pda.wav');
  Played := PlaySound(ExpandConstant('{tmp}\pda.wav'), 0, SND_FILENAME or SND_ASYNC or SND_NODEFAULT);
  Log('Finish sound (pda.wav) started: ' + IntToStr(Integer(Played)));
end;

// ------------------------------------------------------------- runtimes
// The same DLLs commander_gui/winruntimes.py looks for. {sys} is the native
// System32 here (ArchitecturesInstallIn64BitMode).
function RuntimesMissing: Boolean;
begin
  Result :=
    not FileExists(ExpandConstant('{sys}\concrt140.dll')) or
    not FileExists(ExpandConstant('{sys}\d3dx9_43.dll')) or
    not FileExists(ExpandConstant('{sys}\d3dx11_43.dll')) or
    not FileExists(ExpandConstant('{sys}\d3dcompiler_43.dll')) or
    not FileExists(ExpandConstant('{sys}\xinput1_3.dll')) or
    not FileExists(ExpandConstant('{sys}\xaudio2_7.dll'));
end;

// True when the file is signed by Microsoft; the same check as winruntimes.py.
// Only for downloads without a fixed hash (aka.ms serves the latest Visual C++
// redist) and for DXSETUP.exe, unpacked from the hash-checked DirectX package.
function SignedByMicrosoft(const FileName: String): Boolean;
var
  ResultCode: Integer;
  Command, Quoted: String;
begin
  // A ' in the path (a Windows account named O'Brien puts one in {tmp}) ends
  // the PowerShell string early: double it, as PowerShell's quoting wants.
  Quoted := FileName;
  StringChangeEx(Quoted, '''', '''''', True);
  Command :=
    '-NoProfile -NonInteractive -Command "$s = Get-AuthenticodeSignature -LiteralPath ''' + Quoted + '''; ' +
    'if ($s.Status -eq ''Valid'' -and $s.SignerCertificate.Subject -like ''*O=Microsoft Corporation,*'') { exit 0 } else { exit 1 }"';
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Command, '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

// Runs a Microsoft installer with administrator rights (one UAC prompt each).
function RunElevated(const FileName, Params: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := ShellExec('runas', FileName, Params, '', SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode)
    and ((ResultCode = 0) or (ResultCode = 1638) or (ResultCode = 3010) or (ResultCode = 1641));
end;

procedure InstallRuntimes;
var
  Page: TDownloadWizardPage;
  Vc, Dx, DxDir: String;
  Failed: String;
begin
  Vc := ExpandConstant('{tmp}\vc_redist.x64.exe');
  Dx := ExpandConstant('{tmp}\directx_Jun2010_redist.exe');
  DxDir := ExpandConstant('{tmp}\dxredist');
  Failed := '';

  Page := CreateDownloadPage('Game requirements', 'Downloading the Visual C++ and DirectX runtimes...', nil);
  Page.Clear;
  Page.Add('https://aka.ms/vs/17/release/vc_redist.x64.exe', 'vc_redist.x64.exe', '');
  // The offline June 2010 package: the small web installer no longer works.
  // It never changes, so Setup checks it against its SHA-256 while downloading
  // (the same value as winruntimes.py).
  Page.Add('https://download.microsoft.com/download/8/4/A/84A35BF1-DAFE-4AE8-82AF-AD2AE20B6B14/directx_Jun2010_redist.exe',
    'directx_Jun2010_redist.exe', '053f76dcbb28802e23341b6a787e3b0791c0fa5c8d4d011b1044172dbf89c73b');
  Page.Show;
  try
    try
      Page.Download;
    except
      Failed := 'The runtimes could not be downloaded (' + GetExceptionMessage + ').';
    end;
  finally
    Page.Hide;
  end;

  if Failed = '' then
  begin
    // Dx was already checked against its SHA-256 by Page.Download.
    if not SignedByMicrosoft(Vc) then
      Failed := 'A download was not signed by Microsoft, so it was not run.'
    else
    begin
      if not RunElevated(Vc, '/install /quiet /norestart') then
        Failed := 'The Visual C++ runtime did not install.'
      else if not RunElevated(Dx, '/Q "/T:' + DxDir + '"') or not FileExists(DxDir + '\DXSETUP.exe') then
        Failed := 'The DirectX runtime could not be unpacked.'
      else if not SignedByMicrosoft(DxDir + '\DXSETUP.exe') then
        Failed := 'The DirectX installer was not signed by Microsoft, so it was not run.'
      else if not RunElevated(DxDir + '\DXSETUP.exe', '/silent') then
        Failed := 'The DirectX runtime did not install.';
    end;
  end;

  if Failed <> '' then
    SuppressibleMsgBox(Failed + #13#10#13#10 + 'COMMANDER is installed. You can install the runtimes later from ' +
      'COMMANDER''s Install page (Dependencies).', mbInformation, MB_OK, IDOK);
end;

// ---------------------------------------------------------- self-update
// True when COMMANDER started this Setup to update itself (see [Run]).
function RelaunchAfterUpdate: Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;

// ------------------------------------------------------------ uninstall
// Exit codes of commander_gui/wincleanup.py.
const
  CLEANUP_STEAM_RUNNING = 3;
  CLEANUP_COUNT_UNKNOWN = 100;

var
  DeleteSettings, DeleteBackups: Boolean;
  SteamShortcutsLeft, DefenderLeft: Boolean;

// Runs a commander_gui.wincleanup step with the COMMANDER.exe being
// uninstalled (still on disk at usUninstall). -1 when it couldn't start.
function CleanupStep(const Args: String): Integer;
var
  ResultCode: Integer;
begin
  if Exec(ExpandConstant('{app}\{#AppExe}'), '-m commander_gui.wincleanup ' + Args, '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode) then
    Result := ResultCode
  else
    Result := -1;
  Log('wincleanup ' + Args + ': ' + IntToStr(Result));
end;

function SettingsDir: String;
begin
  Result := ExpandConstant('{userappdata}\stalker-gamma');
end;

function DataDir: String;
begin
  Result := ExpandConstant('{localappdata}\stalker-gamma-commander');
end;

procedure UndoWindowsChanges;
var
  Count: Integer;
begin
  if CleanupStep('windows-changes --app "' + ExpandConstant('{app}') + '"') = CLEANUP_STEAM_RUNNING then
    SteamShortcutsLeft := True;
  Count := CleanupStep('defender-count');
  // 0 none; -1 couldn't start; CLEANUP_COUNT_UNKNOWN or above: the list
  // can't be read, so the defender step would have nothing to remove.
  if (Count <= 0) or (Count >= CLEANUP_COUNT_UNKNOWN) then
    Exit;
  if UninstallSilent then
  begin
    // No UAC prompt out of nowhere: only when already elevated.
    DefenderLeft := CleanupStep('defender --only-if-admin') <> 0;
    Exit;
  end;
  if MsgBox('COMMANDER added your game folders to Microsoft Defender''s exclusions, so Defender doesn''t ' +
      'slow down installs and the game.' + #13#10#13#10 + 'Remove these exclusions now? Windows will ask for ' +
      'permission.', mbConfirmation, MB_YESNO) = IDYES then
    DefenderLeft := CleanupStep('defender') <> 0
  else
    DefenderLeft := True;
end;

procedure AskAboutUserData;
begin
  if UninstallSilent then
    Exit;
  if DirExists(SettingsDir) then
    DeleteSettings := MsgBox('Also delete COMMANDER''s settings and logs?' + #13#10#13#10 +
      'Keep them if you will install COMMANDER again: your profiles, folders and options come back as they were. ' +
      'Your game, saves and mods are not affected either way.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  if DirExists(DataDir + '\backups') then
    DeleteBackups := MsgBox('Also delete the backups COMMANDER made (' + DataDir + '\backups)?' + #13#10#13#10 +
      'They are copies of your game setup, mods and saves. They can''t be restored once deleted.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure ReportLeftovers;
var
  Text: String;
begin
  if UninstallSilent then
    Exit;
  Text := '';
  if SteamShortcutsLeft then
    Text := Text + '- The "STALKER COMMANDER" shortcut in Steam: Steam was open. In Steam, right-click it > ' +
      'Manage > Remove non-Steam game from your library.' + #13#10;
  if DefenderLeft then
    Text := Text + '- The Defender exclusions for your game folders: Windows Security > Virus & threat protection > ' +
      'Manage settings > Exclusions.' + #13#10;
  if Text <> '' then
    MsgBox('COMMANDER is uninstalled. These are still there, and can be removed by hand:' + #13#10#13#10 + Text,
      mbInformation, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    // Before Setup deletes COMMANDER.exe, which the cleanup runs with.
    UndoWindowsChanges;
    AskAboutUserData;
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    if DeleteSettings then
      DelTree(SettingsDir, True, True, True);
    if DeleteBackups then
      DelTree(DataDir + '\backups', True, True, True);
    if DeleteSettings then
    begin
      // Plugins and Steam icons, which are only of use to COMMANDER itself.
      DelTree(DataDir + '\plugins', True, True, True);
      DelTree(DataDir + '\icons', True, True, True);
      RemoveDir(DataDir);  // only when nothing else (kept backups) is left
    end;
    ReportLeftovers;
  end;
end;

// ---------------------------------------------------------------- events
procedure InitializeWizard;
begin
  // The dark style paints the header icon's own background #2B2B2B, a grey box
  // on the black header. Match the header so the icon blends in.
  WizardForm.WizardSmallBitmapImage.BackColor := $000000;
  WizardForm.MainPanel.Color := $000000;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('runtimes') then
    InstallRuntimes;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
    PlayFinishSound;
end;
