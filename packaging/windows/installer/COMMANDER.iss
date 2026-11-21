; Inno Setup script for STALKER COMMANDER (Windows).
;
; Build from the repo root, after PyInstaller has produced dist\COMMANDER:
;
;   python packaging\windows\installer\make_images.py
;   ISCC packaging\windows\installer\COMMANDER.iss
;
; Output: dist\STALKER-COMMANDER-Setup-<version>.exe
;
; Override on the command line when needed (CI passes the real version):
;   ISCC /DSourceDir=C:\path\to\COMMANDER /DAppVersion=1.4.1 /DNumericVersion=1.4.1 COMMANDER.iss

#ifndef AppVersion
  #define AppVersion "1.4.0"
#endif
#ifndef NumericVersion
  ; VersionInfoVersion must be digits only (AppVersion may be "1.4.0-unstable2").
  #define NumericVersion "1.4.0"
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
VersionInfoVersion={#NumericVersion}

; Per user by default (no administrator prompt); the user can switch to all users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
CloseApplications=yes
RestartApplications=no

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
OutputBaseFilename=STALKER-COMMANDER-Setup-{#AppVersion}
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

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Played once, on the last page (see [Code]); never installed.
Source: "..\..\..\commander_gui\assets\pda.wav"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

; Uninstall removes only what Setup installed. Settings in %APPDATA%\stalker-gamma
; and the user's Anomaly / GAMMA folders are deliberately not touched.

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
function SignedByMicrosoft(const FileName: String): Boolean;
var
  ResultCode: Integer;
  Command: String;
begin
  Command :=
    '-NoProfile -NonInteractive -Command "$s = Get-AuthenticodeSignature -LiteralPath ''' + FileName + '''; ' +
    'if ($s.Status -eq ''Valid'' -and $s.SignerCertificate.Subject -like ''*Microsoft Corporation*'') { exit 0 } else { exit 1 }"';
  Result := Exec('powershell.exe', Command, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

// Runs a Microsoft installer with administrator rights (one UAC prompt each).
function RunElevated(const FileName, Params: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := ShellExec('runas', FileName, Params, '', SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode)
    and ((ResultCode = 0) or (ResultCode = 1638) or (ResultCode = 3010));
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
  Page.Add('https://download.microsoft.com/download/8/4/A/84A35BF1-DAFE-4AE8-82AF-AD2AE20B6B14/directx_Jun2010_redist.exe',
    'directx_Jun2010_redist.exe', '');
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
    if not SignedByMicrosoft(Vc) or not SignedByMicrosoft(Dx) then
      Failed := 'A download was not signed by Microsoft, so it was not run.'
    else
    begin
      if not RunElevated(Vc, '/install /quiet /norestart') then
        Failed := 'The Visual C++ runtime did not install.'
      else if not RunElevated(Dx, '/Q "/T:' + DxDir + '"') or not FileExists(DxDir + '\DXSETUP.exe') then
        Failed := 'The DirectX runtime could not be unpacked.'
      else if not RunElevated(DxDir + '\DXSETUP.exe', '/silent') then
        Failed := 'The DirectX runtime did not install.';
    end;
  end;

  if Failed <> '' then
    SuppressibleMsgBox(Failed + #13#10#13#10 + 'COMMANDER is installed. You can install the runtimes later from ' +
      'COMMANDER''s Install page (Dependencies).', mbInformation, MB_OK, IDOK);
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
