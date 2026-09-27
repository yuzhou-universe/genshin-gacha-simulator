; 简体中文语言文件（精简版）
;
; 说明：本机网络无法访问 GitHub，拿不到 Inno Setup 官方社区维护的完整中文语言包，
; 因此这里手写一份覆盖安装流程中用户实际会看到的条目。
; Inno 对未在此定义的条目会自动回退到默认语言（英文），不会报错。
;
; 编码：UTF-8（带 BOM），故 LanguageCodePage=0。

[LangOptions]
LanguageName=简体中文
LanguageID=$0804
LanguageCodePage=0
DialogFontName=Microsoft YaHei UI
DialogFontSize=9
WelcomeFontName=Microsoft YaHei UI
WelcomeFontSize=12
TitleFontName=Microsoft YaHei UI
TitleFontSize=29
CopyrightFontName=Microsoft YaHei UI
CopyrightFontSize=8

[Messages]
SetupAppTitle=安装程序
SetupWindowTitle=安装 - %1
UninstallAppTitle=卸载程序
UninstallAppFullTitle=卸载 %1
InformationTitle=提示
ConfirmTitle=确认
ErrorTitle=错误
ButtonBack=< 上一步(&B)
ButtonNext=下一步(&N) >
ButtonInstall=安装(&I)
ButtonOK=确定
ButtonCancel=取消
ButtonYes=是(&Y)
ButtonNo=否(&N)
ButtonFinish=完成(&F)
ButtonBrowse=浏览(&R)...
ButtonClose=关闭(&C)
WelcomeLabel1=欢迎使用 [name] 安装向导
WelcomeLabel2=即将在你的电脑上安装 [name/ver]。%n%n继续之前，建议先关闭其它正在运行的程序。
WizardSelectDir=选择安装位置
SelectDirDesc=要安装 [name] 到哪里？
SelectDirLabel3=安装程序会把 [name] 安装到下面的文件夹。
SelectDirBrowseLabel=点「下一步」继续。如果想换个位置，点「浏览」。
DiskSpaceGBLabel=至少需要 [gb] GB 可用磁盘空间。
DiskSpaceMBLabel=至少需要 [mb] MB 可用磁盘空间。
WizardSelectTasks=选择附加任务
SelectTasksDesc=还需要执行哪些附加任务？
SelectTasksLabel2=安装 [name] 时，请选择要执行的附加任务，然后点「下一步」：
CreateDesktopIcon=创建桌面快捷方式(&D)
CreateQuickLaunchIcon=创建快速启动栏快捷方式(&Q)
WizardReady=准备安装
ReadyLabel1=安装程序已准备好，即将开始安装 [name]。
ReadyLabel2a=点「安装」开始安装；想检查或修改设置，点「上一步」。
PreparingDesc=安装程序正在准备安装 [name]，请稍候。
InstallingLabel=正在安装 [name]，请稍候…
WizardInstalling=正在安装
WizardFinished=安装完成
FinishedLabel=安装完成。[name] 已装到你的电脑上。%n%n点「完成」关闭安装向导。
FinishedLabelNoIcons=[name] 已装到你的电脑上。%n%n点「完成」关闭安装向导。
FinishedRestartLabel=要完成 [name] 的安装，必须重启电脑。现在重启吗？
ClickFinish=点「完成」关闭安装向导。
RunEntryExec=运行 %1
RunEntryShellExec=打开 %1
WizardUninstalling=正在卸载
UninstallStatusLabel=正在从你的电脑上移除 %1，请稍候…
UninstalledAll=%1 已成功卸载。
UninstalledMost=%1 卸载完成。%n%n有少量文件未能删除，你可以手动删除。
UninstalledAndNeedsRestart=要完成 %1 的卸载，必须重启电脑。%n%n现在重启吗？
UninstallNotFound=文件 %1 不存在，无法卸载。
UninstallOpenError=无法打开文件 %1，无法卸载。
UninstallUnknowHeader=无法卸载
ConfirmUninstall=确定要完全移除 %1 及其全部组件吗？
UninstallOnlyOnWin64=本程序只能在 64 位 Windows 上卸载。
ErrorChangingAttr=更改下列文件的属性时出错，请确认没有其它程序正在使用它：%n%n%1
ErrorCopying=复制下列文件时出错：%n%n%1%n%n点「重试」再试一次，或点「跳过」忽略此文件。
ErrorCreatingDir=安装程序无法创建目录「%1」。
ErrorOpeningFile=无法打开文件：%1。
ErrorTooManyFiles=目标目录下的文件过多，安装程序无法完成安装。
ErrorFolderNotFound=找不到文件夹：%1。
ErrorInvalidPath=安装路径无效：%1。
ErrorInternal2=内部错误：%1
ErrorNeedAdmin=本安装程序需要管理员权限。
ErrorMissingFile=找不到要解压的文件：%n%n%1
ExitSetupTitle=退出安装
ExitSetupMessage=安装尚未完成。现在退出，程序将不会被安装。%n%n你可以稍后重新运行安装程序来完成安装。
ExitSetupMsgBox=确定要退出安装吗？
NotOnThisPlatform=本程序无法在 %1 上运行。
WinVersionTooLowError=本程序需要 %1 版本 %2 或更高。
