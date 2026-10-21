; Inno Setup. Compilar:  iscc /DAppVersion=1.0.0 installer\PDFStudio.iss
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
[Setup]
AppId={{7A1D2B3C-5E4F-4A6B-9C8D-0F1E2D3C4B5A}
AppName=PDF Studio
AppVersion={#AppVersion}
AppPublisher=PDF Studio
DefaultDirName={autopf}\PDF Studio
DefaultGroupName=PDF Studio
PrivilegesRequired=lowest
OutputDir=..\dist-installer
OutputBaseFilename=PDFStudio-Setup-{#AppVersion}
SetupIconFile=..\assets\pdfstudio.ico
UninstallDisplayIcon={app}\PDF Studio.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el Escritorio"; Flags: checkedonce
Name: "openwith"; Description: "Añadir PDF Studio a «Abrir con» de los archivos PDF"; Flags: checkedonce
[Files]
Source: "..\dist\PDF Studio\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY.md"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{group}\PDF Studio"; Filename: "{app}\PDF Studio.exe"
Name: "{autodesktop}\PDF Studio"; Filename: "{app}\PDF Studio.exe"; Tasks: desktopicon
[Registry]
Root: HKCU; Subkey: "Software\Classes\Applications\PDF Studio.exe\shell\open\command"; ValueType: string; ValueData: """{app}\PDF Studio.exe"" ""%1"""; Tasks: openwith; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\PDF Studio.exe\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithList\PDF Studio.exe"; ValueType: none; Tasks: openwith; Flags: uninsdeletekey
[Run]
Filename: "{app}\PDF Studio.exe"; Description: "Abrir PDF Studio"; Flags: nowait postinstall skipifsilent
