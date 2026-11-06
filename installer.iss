; Stable per-user installation identity. Never delete settings or user libraries.
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifdef QA
  #define Identity "JournalDownloader-Isolated-QA"
  #define DisplayName "期刊全文下载器 · 安装测试"
  #define MutexName "Local\JournalDownloader-Isolated-QA"
#else
  #define Identity "JournalDownloader-6EC76596-6DF5-4BEA-A645-E8D8084EC6D8"
  #define DisplayName "期刊全文下载器"
  #define MutexName "Local\JournalDownloader-6EC76596-6DF5-4BEA-A645-E8D8084EC6D8"
#endif

[Setup]
AppId={#Identity}
AppName={#DisplayName}
AppVersion={#AppVersion}
AppPublisher=yaluncoco
AppPublisherURL=https://github.com/yaluncoco/journal-downloader
AppSupportURL=https://github.com/yaluncoco/journal-downloader/issues
#ifdef QA
DefaultDirName={localappdata}\Programs\JournalDownloader-Isolated-QA
#else
DefaultDirName={localappdata}\Programs\JournalDownloader
#endif
DefaultGroupName={#DisplayName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
WizardSizePercent=110
WizardResizable=yes
DisableProgramGroupPage=yes
DisableDirPage=auto
UsePreviousAppDir=yes
UsePreviousTasks=yes
AppMutex={#MutexName}
SetupMutex={#Identity}-Setup
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\JournalDownloader.exe
SetupIconFile=app.ico
OutputDir={#OutputDir}
OutputBaseFilename=JournalDownloader-Setup
Compression=lzma2
SolidCompression=yes
VersionInfoVersion={#AppVersion}.0
VersionInfoDescription={#DisplayName}安装程序
InfoBeforeFile=INSTALLATION.txt

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[INI]
Filename: "{app}\installation.ini"; Section: "Install"; Key: "AppId"; String: "{#Identity}"; Flags: uninsdeleteentry uninsdeletesectionifempty
Filename: "{app}\installation.ini"; Section: "Install"; Key: "Version"; String: "{#AppVersion}"; Flags: uninsdeleteentry

[Icons]
#ifndef QA
Name: "{group}\期刊全文下载器"; Filename: "{app}\JournalDownloader.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\期刊全文下载器"; Filename: "{app}\JournalDownloader.exe"; WorkingDir: "{app}"; Tasks: desktopicon
#endif

[Run]
Filename: "{app}\JournalDownloader.exe"; Description: "启动期刊全文下载器"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only this installer-owned marker; never delete settings, credentials or papers.
Type: files; Name: "{app}\installation.ini"
