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
        ; 既に起動中ならウィンドウを前面へ、なければ起動
        if WinExist("ahk_exe Fibro.exe")
            WinActivate
        else
            Run(FibroExe)
    }
    else
        MsgBox("Fibro.exe が見つかりません:`n" . FibroExe)
}
