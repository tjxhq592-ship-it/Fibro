; Fibro インストーラ (Inno Setup 6)
; 前提: PyInstaller で dist\Fibro\ をビルド済みであること
;   .venv\Scripts\pyinstaller --onedir --windowed --noconfirm --name Fibro main.py
; ビルド: Inno Setup Compiler でこのファイルを開いて Compile
;   （または iscc installer.iss）
; 出力: installer_output\FibroSetup-<version>.exe

#define AppName "Fibro"
#define AppVersion "1.0.0"
#define AppPublisher "hiros"
#define AppExeName "Fibro.exe"

[Setup]
AppId={{8F2B3C50-7E14-4D7C-9A14-FIBRO0000001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
; 管理者権限不要（ユーザー単位インストール）
PrivilegesRequired=lowest
DefaultDirName={userpf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=FibroSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作成"; \
    GroupDescription: "追加のショートカット:"; Flags: unchecked
; Win+E 統合（要 AutoHotkey v2。レジストリ変更なし・常駐スクリプト方式）
Name: "winekey"; Description: "Win+E で Fibro を起動する（AutoHotkey v2 が必要）"; \
    GroupDescription: "ホットキー:"; Flags: unchecked

[Files]
Source: "dist\Fibro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs
Source: "fibro_hotkey.ahk"; DestDir: "{app}"; Flags: ignoreversion
; 設定（favorites.json 等）は実行時に {app}\config に作られる

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon
; スタートアップに AHK スクリプトを登録（ログイン時に Win+E 常駐を開始）
Name: "{userstartup}\Fibro Hotkey"; Filename: "{app}\fibro_hotkey.ahk"; \
    Tasks: winekey

[Run]
; インストール直後にも常駐開始（AutoHotkey 未導入なら何もしない）
Filename: "{app}\fibro_hotkey.ahk"; Flags: shellexec nowait skipifsilent; \
    Tasks: winekey

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{#AppName} を起動"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; アンインストール時に設定も削除するか確認したい場合はコメントアウト
Type: filesandordirs; Name: "{app}\config"
