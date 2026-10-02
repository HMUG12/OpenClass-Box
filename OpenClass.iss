; OpenClass-Box 安装包脚本（Inno Setup 6）—— A / B 端分区打包
;
; 用法：
;   iscc /DRole=A OpenClass.iss   → A 端（服务端）安装包
;   iscc /DRole=B OpenClass.iss   → B 端（本体）安装包
;
; 硬要求：两个端都随包携带 WebView2 运行时（{app}\WebView2Runtime）——
;         界面完全由 WebView2 渲染，缺它就是黑屏。A 端虽然精简了重型工具，
;         但运行组件一个都不能少。
;
; A 端：完整程序 + 界面运行组件（排除 OpenOffice / mpv / VLC / LibreOffice 等重型工具）
; B 端：完整工具箱（原本体，含随包 OpenOffice 与 mpv）

; 用数值参数便于命令行传递（ISPP 的 /D 会按表达式解析，字符串需转义易出错）：
;   iscc /DRoleNum=1 OpenClass.iss   → A 端
;   iscc /DRoleNum=2 OpenClass.iss   → B 端（默认）
; 关键：只在命令行未传参时才给默认值，否则脚本里的 #define 会覆盖 /D 参数
#ifndef RoleNum
  #define RoleNum "2"
#endif

#if RoleNum == "1"
  #pragma message "=== 编译目标：A 端（服务端）==="
  #define AppTitle "OpenClass-Box A 端（服务端）"
  #define OutputName "OpenClass-Box-A_Setup"
  #define InstallDir "OpenClass-Box-A"
  #define RoleParam "--role=a"
#else
  #pragma message "=== 编译目标：B 端（本体）==="
  #define AppTitle "OpenClass-Box B 端（本体）"
  #define OutputName "OpenClass-Box-B_Setup"
  #define InstallDir "OpenClass-Box-B"
  #define RoleParam "--role=b"
#endif

[Setup]
AppName={#AppTitle}
AppVersion=0.2.0
AppPublisher=HMUG12
AppComments=开源实用工具箱 · A/B 端
DefaultDirName={autopf}\{#InstallDir}
DefaultGroupName={#AppTitle}
OutputDir=installer
OutputBaseFilename={#OutputName}
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
SetupIconFile=openclass.ico
; 安装向导第一步展示的用户许可与免责协议（需勾选"我接受"才能继续）
LicenseFile=license.txt
PrivilegesRequired=lowest
UninstallDisplayName={#AppTitle}
CreateUninstallRegKey=yes
LanguageDetectionMethod=uilanguage
ShowLanguageDialog=auto

[Languages]
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
#if RoleNum == "1"
; A 端：程序本体 + WebView2Runtime（黑屏防线）；排除管理端用不到的重型工具
Source: "dist_build/OpenClass-Box/*"; DestDir: "{app}"; Excludes: "data\*,tools\openoffice\*,tools\mpv\*,tools\vlc\*,tools\libreoffice\*,tools\*.bak\*"; Flags: ignoreversion recursesubdirs createallsubdirs
#else
; B 端：完整工具箱
; 排除 data\*：那是打包机上跑过 exe 生成的运行时数据（配置 / 日志 / 局域网库），
; 不能带给用户，否则新装机器拿到的是打包者的设置
Source: "dist_build/OpenClass-Box/*"; DestDir: "{app}"; Excludes: "data\*,tools\*.bak\*"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif
; WebView2 运行时安装器：随包携带，万一内嵌运行时不可用还能在线补装
Source: "installer\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
; 签名信任证书（装到其他机器时导入后不再提示「未知发布者」）
Source: "dist_build/OpenClass-Box/OpenClass-Box.cer"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppTitle}"; Filename: "{app}\OpenClass-Box.exe"; Parameters: "{#RoleParam}"
Name: "{autodesktop}\{#AppTitle}"; Filename: "{app}\OpenClass-Box.exe"; Parameters: "{#RoleParam}"; Tasks: desktopicon
Name: "{group}\OpenClass-Box（默认模式）"; Filename: "{app}\OpenClass-Box.exe"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "额外任务:"
Name: "shellmenu"; Description: "把 OpenClass-Box 加入右键菜单与""打开方式""（文件将由工具箱内对应工具打开）"; GroupDescription: "系统集成:"
Name: "trustcert"; Description: "在本机信任 OpenClass-Box 签名证书（消除""未知发布者""安全提示）"; GroupDescription: "系统集成:"

[Registry]
; ── 右键菜单与「打开方式」集成（HKA 会按权限自动落到 HKCR 或 HKCU\Software\Classes）──
Root: HKA; Subkey: "*\shell\OpenClassBox"; ValueType: string; ValueName: ""; ValueData: "用 OpenClass-Box 打开"; Flags: uninsdeletekey; Tasks: shellmenu
Root: HKA; Subkey: "*\shell\OpenClassBox"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\openclass.ico"; Tasks: shellmenu
Root: HKA; Subkey: "*\shell\OpenClassBox\command"; ValueType: string; ValueName: ""; ValueData: """{app}\OpenClass-Box.exe"" ""%1"""; Tasks: shellmenu
Root: HKA; Subkey: "Applications\OpenClass-Box.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\OpenClass-Box.exe"" ""%1"""; Flags: uninsdeletekey; Tasks: shellmenu
Root: HKA; Subkey: "Applications\OpenClass-Box.exe\shell\open\command"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "OpenClass-Box"; Tasks: shellmenu
Root: HKA; Subkey: "*\OpenWithProgids"; ValueType: string; ValueName: "OpenClassBox.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: shellmenu
Root: HKA; Subkey: "OpenClassBox.Document"; ValueType: string; ValueName: ""; ValueData: "OpenClass-Box 文档"; Flags: uninsdeletekey; Tasks: shellmenu
Root: HKA; Subkey: "OpenClassBox.Document\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\openclass.ico"; Tasks: shellmenu
Root: HKA; Subkey: "OpenClassBox.Document\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\OpenClass-Box.exe"" ""%1"""; Tasks: shellmenu
Root: HKA; Subkey: ".zip\OpenWithProgids"; ValueType: string; ValueName: "OpenClassBox.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: shellmenu
Root: HKA; Subkey: ".7z\OpenWithProgids"; ValueType: string; ValueName: "OpenClassBox.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: shellmenu
Root: HKA; Subkey: ".rar\OpenWithProgids"; ValueType: string; ValueName: "OpenClassBox.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: shellmenu
Root: HKA; Subkey: ".pdf\OpenWithProgids"; ValueType: string; ValueName: "OpenClassBox.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: shellmenu
Root: HKA; Subkey: ".docx\OpenWithProgids"; ValueType: string; ValueName: "OpenClassBox.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: shellmenu
Root: HKA; Subkey: ".xlsx\OpenWithProgids"; ValueType: string; ValueName: "OpenClassBox.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: shellmenu
Root: HKA; Subkey: ".pptx\OpenWithProgids"; ValueType: string; ValueName: "OpenClassBox.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: shellmenu
Root: HKA; Subkey: ".mp4\OpenWithProgids"; ValueType: string; ValueName: "OpenClassBox.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: shellmenu
Root: HKA; Subkey: ".mkv\OpenWithProgids"; ValueType: string; ValueName: "OpenClassBox.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: shellmenu
Root: HKA; Subkey: ".mp3\OpenWithProgids"; ValueType: string; ValueName: "OpenClassBox.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: shellmenu

[Run]
; 目标机缺 WebView2 运行时（界面显示不出来的根因）时，自动静默安装
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "正在安装界面运行组件 WebView2…"; Check: NeedsWebView2
; 把签名证书导入当前用户的受信任根（仅勾选了该任务且证书存在时执行）
Filename: "{sys}\certutil.exe"; Parameters: "-user -addstore -f Root ""{app}\OpenClass-Box.cer"""; StatusMsg: "正在信任签名证书…"; Flags: runhidden; Check: TrustCertWanted
Filename: "{app}\OpenClass-Box.exe"; Parameters: "{#RoleParam}"; Description: "安装完成后启动 {#AppTitle}"; Flags: nowait postinstall

[Code]
function TrustCertWanted(): Boolean;
begin
  Result :=
    WizardIsTaskSelected('trustcert') and
    FileExists(ExpandConstant('{app}\OpenClass-Box.cer'));
end;

function NeedsWebView2(): Boolean;
begin
  // 已随包携带固定版本运行时（{app}\WebView2Runtime）时无需再装系统运行时的
  Result := not (
    FileExists(ExpandConstant('{app}\WebView2Runtime\msedgewebview2.exe')) or
    DirExists(ExpandConstant('{localappdata}\Microsoft\EdgeWebView\Application')) or
    DirExists(ExpandConstant('{pf32}\Microsoft\EdgeWebView\Application')) or
    DirExists(ExpandConstant('{pf64}\Microsoft\EdgeWebView\Application'))
  );
end;
