#define MyAppName "Dimitry Hub"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "DimitryUwU"
#define MyAppExeName "DimitryHub.exe"

[Setup]
AppId={{81BC7CA0-720F-4ADF-AEF7-3E7C4CC65D3E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Dimitry Hub
DefaultGroupName=Dimitry Hub
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=release
OutputBaseFilename=Dimitry_Hub_Setup_x64
SetupIconFile=dimitry_hub.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany=DimitryUwU
VersionInfoDescription=Dimitry Hub Installer
VersionInfoProductName=Dimitry Hub
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
Source: "dist\DimitryHub.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\DimitryHubUpdater.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dimitry_hub.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "LEEME_PRIMERO.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Dimitry Hub"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\dimitry_hub.ico"
Name: "{autodesktop}\Dimitry Hub"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\dimitry_hub.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: checkedonce

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir Dimitry Hub"; Flags: nowait postinstall skipifsilent

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/D /C taskkill /F /T /IM DimitryHub.exe >nul 2>&1', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(700);
  Result := '';
end;
