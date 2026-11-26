Unicode True
!include "MUI2.nsh"
!include "x64.nsh"
Name "WAT G-01 工作台"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\WAT G01"
RequestExecutionLevel user
SetCompressor /SOLID lzma
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "此安装包需要 64 位 Windows。"
    Abort
  ${EndIf}
FunctionEnd

!define UNKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\WAT-G01"
Section
  SetOutPath "$INSTDIR"
  File /r "${STAGE}/payload/*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$SMPROGRAMS\WAT G-01 工作台.lnk" "$INSTDIR\python.exe" '-X utf8 -B "$INSTDIR\launch.py"'
  WriteRegStr HKCU "${UNKEY}" "DisplayName" "WAT G-01 工作台"
  WriteRegStr HKCU "${UNKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
SectionEnd

Section "Uninstall"
  Delete "$SMPROGRAMS\WAT G-01 工作台.lnk"
  !include "${STAGE}/uninstall.nsh"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNKEY}"
SectionEnd
