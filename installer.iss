; MediaSense Inno Setup Script
#define MyAppName "MediaSense"
#ifndef MyAppVersion
; 命令行可用 /DMyAppVersion=x.y.z 覆盖（build_installer.py 会从 version.txt 传入）
#define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "MediaSense Contributors"
#define MyAppURL "https://github.com/selfish-sin/MediaSense"
#define MyAppExeName "MediaSense.exe"

[Setup]
AppId={{A3F7B2C1-9D4E-4A8F-B6C5-1E2D3F4A5B6C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=dist
OutputBaseFilename=MediaSense-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=LICENSE
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\MediaSense\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只删应用运行数据（DB/下载/转换临时目录）；{app}\models 里用户下载的 Whisper
; 模型刻意保留——体积大且可跨版本复用，避免重装后再走一遍下载
Type: filesandordirs; Name: "{app}\backend\data"
