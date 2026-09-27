; ============================================================================
; 原神风格抽卡模拟系统 —— 安装脚本
;
; 构建：ISCC.exe packaging\installer.iss
; 或直接跑 build_windows.py（会先 PyInstaller 再调本脚本）
;
; 设计要点：
;   * PrivilegesRequired=lowest —— 仅当前用户，全程不弹 UAC。
;     装到 %LOCALAPPDATA%\Programs 而不是 Program Files，卸载同样免管理员。
;   * 卸载时**询问**是否删除用户数据。数据库与上传素材在 %LOCALAPPDATA%\GenshinGacha，
;     那是用户的存档，默认保留，避免误删。
; ============================================================================

#define AppName "原神风格抽卡模拟系统"
#define AppShortName "原神抽卡模拟器"
#define AppVersion "1.0.0"
#define AppPublisher "GenshinGacha"
#define AppExeName "原神抽卡模拟器.exe"
; 用户数据目录，必须与 app/core/paths.py 里的 APP_DIR_NAME 保持一致
#define UserDataDirName "GenshinGacha"

[Setup]
AppId={{7C4E1A62-9F3B-4D58-A1E7-2B6C05D94F31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
; 仅当前用户：不弹 UAC，卸载也不需要管理员
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
DefaultDirName={localappdata}\Programs\{#AppShortName}
DefaultGroupName={#AppShortName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
; 打包产物体积约 53MB，这里给足余量
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
OutputDir=..\release
OutputBaseFilename={#AppShortName}-{#AppVersion}-安装包
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 关掉"是否发送使用统计"之类的联网行为（本安装包不联网）
DisableWelcomePage=no

[Languages]
Name: "chinese"; MessagesFile: "{#SourcePath}\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; PyInstaller 的 onedir 产物整体拷进去
Source: "{#SourcePath}\..\dist\{#AppShortName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 面向最终使用者的说明，随安装一起放好，方便随时查看
Source: "{#SourcePath}\使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{group}\卸载 {#AppShortName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppShortName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppShortName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只删程序目录。用户数据在 %LOCALAPPDATA%\{#UserDataDirName}，由下面的代码询问后再决定。
Type: filesandordirs; Name: "{app}"

[Code]
const
  UserDataDir = 'GenshinGacha';

function GetUserDataPath(): String;
begin
  Result := ExpandConstant('{localappdata}\' + UserDataDir);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataPath: String;
begin
  // 程序文件删完之后再问，这样用户看到的是"程序已移除，数据要不要留"
  if CurUninstallStep = usPostUninstall then
  begin
    DataPath := GetUserDataPath();
    if DirExists(DataPath) then
    begin
      // 静默卸载一律保留数据，不询问。
      // Inno 的 MsgBox 在 /SUPPRESSMSGBOXES 下会**直接返回按钮值而不显示**，
      // 也就是说无人值守卸载会在这里"自动同意"删库 —— 那是最糟的结果。
      // 只有用户在场、明确点了「是」，才真的删。
      if UninstallSilent() then
        Exit;

      if MsgBox('是否同时删除用户数据？' + #13#10 + #13#10 +
                '数据目录：' + DataPath + #13#10 +
                '里面是你的抽卡记录、账号、以及上传的素材。' + #13#10 + #13#10 +
                '选择「否」将保留这些数据，方便以后重新安装时继续使用。',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(DataPath, True, True, True);
      end;
    end;
  end;
end;
