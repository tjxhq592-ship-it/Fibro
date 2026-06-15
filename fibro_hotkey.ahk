; Fibro Win+E ホットキー (AutoHotkey v2)
; Win+E で標準エクスプローラーの代わりに Fibro を起動する。
; このスクリプトを終了すれば Win+E は即座に標準エクスプローラーに戻る。
; レジストリ・システム設定は一切変更しない。
;
; 必要環境: AutoHotkey v2 (https://www.autohotkey.com)
; 使い方: このファイルをダブルクリック（タスクトレイに常駐）

#Requires AutoHotkey v2.0
#SingleInstance Force

; Fibro.exe はこのスクリプトと同じフォルダにある前提
FibroExe := A_ScriptDir . "\Fibro.exe"

A_IconTip := "Fibro ホットキー (Win+E)"
TraySetIcon(FibroExe)

#e::
{
    if FileExist(FibroExe)
    {
        ; 常に起動を試みる。Fibro 側が単一インスタンス制御するため、
        ; 既に起動中なら新プロセスは立たず、既存ウィンドウに新規タブが
        ; 追加され最前面化される（二重起動時はタブ追加＋前面化）。
        Run(FibroExe)
    }
    else
        MsgBox("Fibro.exe が見つかりません:`n" . FibroExe)
}
