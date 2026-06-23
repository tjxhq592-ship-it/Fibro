"""Fibro UI 多言語対応モジュール。

使い方:
    from app.i18n import _
    label.setText(_("ok"))           # キーで取得
    text = _("sel_n").format(n=3)    # フォーマット付き

言語設定は ThemeManager.set("language", "ja"/"en") で永続化。
アプリ起動時に apply_language(lang) を呼ぶことで _ 関数が返す言語が切り替わる。
再起動なしの即時切り替えは行わない（PySide6 ウィジェットの再生成が必要なため）。
"""
from __future__ import annotations

_LANG: str = "ja"   # 現在の言語（モジュールレベルのシングルトン）

# ── 翻訳辞書 ──────────────────────────────────────────────────────────────
# キー → {"ja": 日本語, "en": English}
# f-string のプレースホルダは {変数名} 形式で書き、呼び出し側で .format(**kw) する。
_STRINGS: dict[str, dict[str, str]] = {

    # ── ウィンドウタイトル ──
    "app_title":          {"ja": "Fibro — ファイルブラウザー",   "en": "Fibro — File Browser"},

    # ── メニュー（将来のメニューバー用も含む） ──
    "menu_file":          {"ja": "ファイル",       "en": "File"},
    "menu_new":           {"ja": "新規作成",        "en": "New"},
    "menu_new_folder":    {"ja": "フォルダー\tCtrl+Shift+N",  "en": "Folder\tCtrl+Shift+N"},
    "menu_new_text":      {"ja": "テキストファイル",             "en": "Text File"},
    "menu_new_tab":       {"ja": "新規タブ\tCtrl+T",            "en": "New Tab\tCtrl+T"},
    "menu_close_tab":     {"ja": "タブを閉じる\tCtrl+W",        "en": "Close Tab\tCtrl+W"},
    "menu_exit":          {"ja": "終了",            "en": "Exit"},
    "menu_select":        {"ja": "選択",            "en": "Select"},
    "menu_select_all":    {"ja": "すべて選択\tCtrl+A",          "en": "Select All\tCtrl+A"},
    "menu_invert_sel":    {"ja": "選択を反転",      "en": "Invert Selection"},
    "menu_sel_pattern":   {"ja": "パターンで選択…", "en": "Select by Pattern…"},
    "menu_view":          {"ja": "表示",            "en": "View"},
    "menu_toggle_view":   {"ja": "詳細／サムネイル切替\tCtrl+Shift+T", "en": "Detail / Thumbnail\tCtrl+Shift+T"},
    "menu_dual_pane":     {"ja": "デュアルペイン\tF9",          "en": "Dual Pane\tF9"},
    "menu_switch_pane":   {"ja": "ペイン切替\tF6",              "en": "Switch Pane\tF6"},
    "menu_quick_preview": {"ja": "クイックプレビュー\tSpace",   "en": "Quick Preview\tSpace"},
    "menu_settings":      {"ja": "設定",            "en": "Settings"},
    "menu_toggle_theme":  {"ja": "テーマ切替（ダーク／ライト）", "en": "Toggle Theme (Dark/Light)"},
    "menu_set_initial":   {"ja": "初期ディレクトリを設定…",     "en": "Set Initial Directory…"},
    "menu_refresh_places": {"ja": "場所を更新（ドライブ/クラウド）", "en": "Refresh Places (Drives/Cloud)"},
    "menu_override_wine": {"ja": "標準フォルダのオーバーライド", "en": "Override Default Folder"},
    "menu_language":      {"ja": "言語",            "en": "Language"},
    "menu_lang_ja":       {"ja": "日本語",          "en": "日本語 (Japanese)"},
    "menu_lang_en":       {"ja": "English",         "en": "English"},
    "menu_help":          {"ja": "ヘルプ",          "en": "Help"},
    "menu_report_bug":    {"ja": "バグを報告…",     "en": "Report a Bug…"},
    "menu_about":         {"ja": "Fibro について…", "en": "About Fibro…"},

    # ── ツールバーボタンのツールチップ ──
    "tip_settings":       {"ja": "設定",            "en": "Settings"},
    "tip_shortcuts":      {"ja": "ショートカット一覧", "en": "Shortcuts"},
    "tip_help":           {"ja": "ヘルプ",          "en": "Help"},
    "shortcuts_title":    {"ja": "ショートカット一覧", "en": "Keyboard Shortcuts"},

    # ── タブ・ツールバー ──
    "new_tab_tooltip":    {"ja": "新しいタブ (Ctrl+T)",         "en": "New Tab (Ctrl+T)"},
    "tab_close_tooltip":  {"ja": "タブを閉じる",                 "en": "Close Tab"},
    "filter_placeholder": {"ja": "カレントフォルダ内を絞り込み…", "en": "Filter current folder…"},

    # ── サイドバーセクション見出し ──
    "sidebar_fav":        {"ja": "お気に入り",      "en": "Favorites"},
    "sidebar_history":    {"ja": "履歴",            "en": "History"},
    "sidebar_tree":       {"ja": "フォルダツリー",  "en": "Folder Tree"},
    "sidebar_places":     {"ja": "クラウド / ネットワーク", "en": "Cloud / Network"},
    "sidebar_recent":     {"ja": "最近使った",      "en": "Recently Used"},
    "sidebar_frequent":   {"ja": "よく使う",        "en": "Frequently Used"},

    # ── ステータスバー ──
    "sel_0":              {"ja": "選択: 0件",        "en": "Selected: 0"},
    "sel_n":              {"ja": "選択: {n}件",      "en": "Selected: {n}"},
    "sel_n_size":         {"ja": "選択: {n}件 / {size}", "en": "Selected: {n} / {size}"},
    "items_n":            {"ja": "{n:,} 項目",       "en": "{n:,} items"},
    "drive_free":         {"ja": "{drive} 空き {free} / {total}", "en": "{drive} Free {free} / {total}"},
    "view_mode_thumb":    {"ja": "表示: サムネイル", "en": "View: Thumbnails"},
    "view_mode_detail":   {"ja": "表示: 詳細",       "en": "View: Details"},
    "theme_dark":         {"ja": "テーマ: ダーク",   "en": "Theme: Dark"},
    "theme_light":        {"ja": "テーマ: ライト",   "en": "Theme: Light"},
    "win_e_on":           {"ja": "Win+E を Fibro に割り当てました", "en": "Win+E is now assigned to Fibro"},
    "win_e_off":          {"ja": "Win+E を標準エクスプローラーに戻しました", "en": "Win+E restored to Explorer"},
    "win_e_fail_on":      {"ja": "設定に失敗しました（権限などをご確認ください）", "en": "Failed to apply setting (check permissions)"},
    "win_e_fail_off":     {"ja": "解除に失敗しました", "en": "Failed to remove setting"},
    "initial_dir_set":    {"ja": "初期ディレクトリを {path} に設定しました", "en": "Initial directory set to {path}"},
    "rename_done":        {"ja": "リネームが完了しました（Ctrl+Zで取り消し）", "en": "Rename complete (Ctrl+Z to undo)"},
    "undo_rename_done":   {"ja": "リネーム {n}件を取り消しました", "en": "Undo rename: {n} items"},
    "undo_move_done":     {"ja": "{kind} {n}件を取り消しました",   "en": "Undo {kind}: {n} items"},
    "undo_none":          {"ja": "取り消せる操作はありません",      "en": "Nothing to undo"},
    "undo_failed":        {"ja": "Undo失敗",          "en": "Undo Failed"},
    "kind_move":          {"ja": "移動",              "en": "move"},
    "kind_copy":          {"ja": "コピー",            "en": "copy"},
    "single_rename_done": {"ja": "{old} → {new}（Ctrl+Zで取り消し）", "en": "{old} → {new} (Ctrl+Z to undo)"},
    "copied_n":           {"ja": "{n}件のパスをコピーしました",    "en": "Copied {n} path(s)"},
    "clipped_n":          {"ja": "{n}件を{verb}しました",          "en": "{n} item(s) {verb}"},
    "clip_copied":        {"ja": "コピー",            "en": "copied"},
    "clip_cut":           {"ja": "切り取り",          "en": "cut"},
    "pasted_n":           {"ja": "{n}件を貼り付けました",          "en": "Pasted {n} item(s)"},
    "moved_n":            {"ja": "{n}件を {dest} へ{verb}しました（Ctrl+Zで取り消し）",
                                                                    "en": "{n} item(s) {verb} to {dest} (Ctrl+Z to undo)"},
    "trashed_n":          {"ja": "{n}件をゴミ箱に移動しました",    "en": "Moved {n} item(s) to Trash"},
    "deleted_n":          {"ja": "{n}件を完全に削除しました",      "en": "Permanently deleted {n} item(s)"},
    "selected_n":         {"ja": "{n}件を選択しました",            "en": "Selected {n} item(s)"},
    "shell_menu_fail":    {"ja": "Windowsメニューを表示できませんでした", "en": "Could not show Windows menu"},
    "lang_restart":       {"ja": "言語を変更しました。次回起動時に反映されます。", "en": "Language changed. It will take effect on next launch."},

    # ── ダイアログ — 共通 ──
    "dlg_ok":             {"ja": "OK",              "en": "OK"},
    "dlg_cancel":         {"ja": "キャンセル",       "en": "Cancel"},
    "dlg_close":          {"ja": "閉じる",           "en": "Close"},

    # ── ダイアログ — 削除確認 ──
    "dlg_trash_title":    {"ja": "削除の確認",        "en": "Confirm Delete"},
    "dlg_trash_msg":      {"ja": "次の{n}件をゴミ箱に移動しますか?\n\n{names}", "en": "Move {n} item(s) to Trash?\n\n{names}"},
    "dlg_delete_title":   {"ja": "完全に削除",        "en": "Delete Permanently"},
    "dlg_delete_msg":     {"ja": "次の{n}件を完全に削除します。\nこの操作は元に戻せません（ゴミ箱に入りません）。\n\n{names}",
                           "en": "Permanently delete {n} item(s).\nThis cannot be undone.\n\n{names}"},
    "dlg_trash_fail":     {"ja": "削除失敗",          "en": "Delete Failed"},
    "dlg_delete_fail":    {"ja": "削除失敗",          "en": "Delete Failed"},
    "dlg_open_fail":      {"ja": "開けません",         "en": "Cannot Open"},
    "dlg_open_fail_msg":  {"ja": "ファイルを開けませんでした:\n{path}\n\n{err}", "en": "Could not open file:\n{path}\n\n{err}"},
    "dlg_paste_fail":     {"ja": "貼り付け失敗",       "en": "Paste Failed"},
    "dlg_drop_fail":      {"ja": "ドロップ失敗",       "en": "Drop Failed"},
    "dlg_create_fail":    {"ja": "作成失敗",           "en": "Create Failed"},
    "dlg_rename_fail":    {"ja": "リネーム失敗",       "en": "Rename Failed"},
    "dlg_rename_exists":  {"ja": "「{name}」は既に存在します。", "en": '"{name}" already exists.'},
    "dlg_rename_select":  {"ja": "一括リネーム",       "en": "Batch Rename"},
    "dlg_rename_select_msg": {"ja": "リネームするファイルを選択してください。", "en": "Please select files to rename."},
    "dlg_terminal_fail":  {"ja": "ターミナル",         "en": "Terminal"},
    "dlg_open_app_fail":  {"ja": "開く",               "en": "Open"},
    "dlg_terminal_fail_msg": {"ja": "起動できませんでした:\n{err}", "en": "Could not launch:\n{err}"},

    # ── ダイアログ — 新規作成 ──
    "dlg_new_folder_title":  {"ja": "新規フォルダー",        "en": "New Folder"},
    "dlg_new_folder_label":  {"ja": "フォルダー名:",         "en": "Folder name:"},
    "dlg_new_folder_default":{"ja": "新しいフォルダー",      "en": "New Folder"},
    "dlg_new_text_title":    {"ja": "新規テキストファイル",   "en": "New Text File"},
    "dlg_new_text_label":    {"ja": "ファイル名:",           "en": "File name:"},
    "dlg_new_text_default":  {"ja": "新しいテキスト.txt",    "en": "New Text.txt"},
    "dlg_new_tpl_title":     {"ja": "雛形から新規作成",      "en": "New from Template"},
    "dlg_new_tpl_label":     {"ja": "ファイル名:",           "en": "File name:"},
    "dlg_pattern_title":     {"ja": "パターンで選択",        "en": "Select by Pattern"},
    "dlg_pattern_label":     {"ja": "ワイルドカード（例: *.png）:", "en": "Wildcard (e.g. *.png):"},
    "dlg_initial_dir_title": {"ja": "初期ディレクトリを選択", "en": "Select Initial Directory"},

    # ── ダイアログ — About ──
    "dlg_about_title":    {"ja": "Fibro について",    "en": "About Fibro"},
    "dlg_about_body":     {"ja": "<b>Fibro — ファイルブラウザー</b><br>バージョン {ver}<br><br>Python {py}・PySide6 {pyside}<br>{sys}<br><br>バグ報告: <a href='{url}'>{url}</a>",
                           "en": "<b>Fibro — File Browser</b><br>Version {ver}<br><br>Python {py} · PySide6 {pyside}<br>{sys}<br><br>Report bugs: <a href='{url}'>{url}</a>"},

    # ── ダイアログ — WinE ──
    "dlg_wine_title":     {"ja": "Win+E オーバーライド", "en": "Win+E Override"},
    "dlg_wine_exe_only":  {"ja": "この機能はインストール版／exe 版（Fibro.exe）でのみ有効です。\nソースから実行中は利用できません。",
                           "en": "This feature requires the installed/exe version (Fibro.exe).\nNot available when running from source."},

    # ── 右クリックメニュー ──
    "ctx_open":           {"ja": "開く",              "en": "Open"},
    "ctx_open_with":      {"ja": "ここで開く",         "en": "Open Here"},
    "ctx_terminal":       {"ja": "ターミナル",         "en": "Terminal"},
    "ctx_explorer":       {"ja": "エクスプローラー",   "en": "Explorer"},
    "ctx_rename":         {"ja": "名前の変更\tF2",     "en": "Rename\tF2"},
    "ctx_batch_rename":   {"ja": "一括リネーム…\tCtrl+H", "en": "Batch Rename…\tCtrl+H"},
    "ctx_copy":           {"ja": "コピー\tCtrl+C",    "en": "Copy\tCtrl+C"},
    "ctx_cut":            {"ja": "切り取り\tCtrl+X",  "en": "Cut\tCtrl+X"},
    "ctx_paste":          {"ja": "貼り付け\tCtrl+V",  "en": "Paste\tCtrl+V"},
    "ctx_copy_path":      {"ja": "パスをコピー\tCtrl+Shift+C", "en": "Copy Path\tCtrl+Shift+C"},
    "ctx_trash":          {"ja": "削除（ゴミ箱へ）\tDelete", "en": "Delete (Trash)\tDelete"},
    "ctx_delete":         {"ja": "完全に削除\tShift+Delete",  "en": "Delete Permanently\tShift+Delete"},
    "ctx_add_fav":        {"ja": "お気に入りに追加",   "en": "Add to Favorites"},
    "ctx_properties":     {"ja": "プロパティ",         "en": "Properties"},
    "ctx_new":            {"ja": "新規作成",           "en": "New"},
    "ctx_here_copy":      {"ja": "ここにコピー（{name}）", "en": "Copy Here ({name})"},
    "ctx_here_move":      {"ja": "ここに移動（{name}）",   "en": "Move Here ({name})"},
    "ctx_cancel":         {"ja": "キャンセル",         "en": "Cancel"},

    # ── ファイル一覧 列見出し ──
    "col_name":           {"ja": "名前",              "en": "Name"},
    "col_size":           {"ja": "サイズ",            "en": "Size"},
    "col_type":           {"ja": "種類",              "en": "Type"},
    "col_modified":       {"ja": "更新日時",          "en": "Date Modified"},
    "col_n":              {"ja": "列{n}",             "en": "Col {n}"},

    # ── 単一リネームダイアログ ──
    "rename_title":       {"ja": "名前の変更",         "en": "Rename"},
    "rename_name_label":  {"ja": "名前:",              "en": "Name:"},
    "rename_ext_label":   {"ja": "拡張子:",            "en": "Extension:"},

    # ── 一括リネームダイアログ ──
    "brename_title":      {"ja": "一括リネーム — {n}件", "en": "Batch Rename — {n} items"},
    "brename_preset":     {"ja": "プリセット:",        "en": "Preset:"},
    "brename_load":       {"ja": "読み込み",           "en": "Load"},
    "brename_save":       {"ja": "保存…",             "en": "Save…"},
    "brename_delete":     {"ja": "削除",               "en": "Delete"},
    "brename_replace_ph": {"ja": "連番は ${n}",        "en": "Use ${n} for counter"},
    "brename_regex":      {"ja": "正規表現",           "en": "Regex"},
    "brename_target_name":{"ja": "名前",               "en": "Name"},
    "brename_target_ext": {"ja": "拡張子",             "en": "Extension"},
    "brename_target_both":{"ja": "両方",               "en": "Both"},
    "brename_case_keep":  {"ja": "そのまま",           "en": "Keep"},
    "brename_search_lbl": {"ja": "検索:",              "en": "Search:"},
    "brename_target_lbl": {"ja": "対象:",              "en": "Target:"},
    "brename_replace_lbl":{"ja": "置換:",              "en": "Replace:"},
    "brename_case_lbl":   {"ja": "大小:",              "en": "Case:"},
    "brename_counter_lbl":{"ja": "連番 ${n}:  開始",  "en": "Counter ${n}: Start"},
    "brename_digits_lbl": {"ja": "桁",                 "en": "digits"},
    "brename_step_lbl":   {"ja": "増分",               "en": "step"},
    "brename_col_current":{"ja": "現在名",             "en": "Current Name"},
    "brename_col_new":    {"ja": "新名",               "en": "New Name"},
    "brename_col_status": {"ja": "状態",               "en": "Status"},
    "brename_apply":      {"ja": "実行",               "en": "Apply"},
    "brename_summary":    {"ja": "変更: {change}件 / 全{total}件", "en": "Changes: {change} / {total}"},
    "brename_summary_err":{"ja": "  （エラー行はスキップされます）", "en": "  (rows with errors will be skipped)"},
    "brename_save_title": {"ja": "プリセットを保存",   "en": "Save Preset"},
    "brename_save_label": {"ja": "プリセット名:",      "en": "Preset name:"},
    "brename_fail_title": {"ja": "リネーム失敗",       "en": "Rename Failed"},
    "brename_fail_msg":   {"ja": "リネームに失敗しました（変更はロールバック済み）:\n{err}",
                           "en": "Rename failed (changes rolled back):\n{err}"},

    # ── リネームステータス ──
    "status_ok":          {"ja": "✓",                  "en": "✓"},
    "status_unchanged":   {"ja": "- 変更なし",          "en": "- Unchanged"},
    "status_resolved":    {"ja": "⚠ 衝突→連番",        "en": "⚠ Conflict→Numbered"},
    "status_conflict":    {"ja": "✗ 衝突",             "en": "✗ Conflict"},
    "status_invalid":     {"ja": "✗ 無効な名前",       "en": "✗ Invalid Name"},

    # ── 検索パネル ──
    "search_title":       {"ja": "検索",               "en": "Search"},
    "search_root":        {"ja": "検索場所: {path}",   "en": "Search in: {path}"},
    "search_keyword_ph":  {"ja": "検索キーワード…",    "en": "Search keyword…"},
    "search_btn":         {"ja": "検索",               "en": "Search"},
    "search_cancel_btn":  {"ja": "キャンセル",         "en": "Cancel"},
    "search_filename":    {"ja": "ファイル名",          "en": "Filename"},
    "search_text":        {"ja": "テキスト",           "en": "Text"},
    "search_case":        {"ja": "大小区別",           "en": "Case sensitive"},
    "search_recursive":   {"ja": "サブフォルダーも検索","en": "Search subfolders"},
    "search_index":       {"ja": "高速インデックス",   "en": "Fast index"},
    "search_index_tip":   {"ja": "ファイル名検索を SQLite FTS5 インデックスで高速化。\n初回と10分経過後は自動で再構築します。\n（ファイル名モードのみ・ワイルドカード/サブフォルダOFFとは併用不可）",
                           "en": "Accelerates filename search with SQLite FTS5 index.\nAuto-rebuilt on first use and after 10 minutes.\n(Filename mode only; incompatible with wildcard/subfolder search)"},
    "search_partial_tip": {"ja": "部分一致で検索。* や ? を含めると *.md / file_?.txt などのパターン照合になります。",
                           "en": "Partial match search. Use * or ? for pattern matching (e.g. *.md / file_?.txt)."},
    "search_running":     {"ja": "検索中…",            "en": "Searching…"},
    "search_running_n":   {"ja": "検索中… {n}件",      "en": "Searching… {n} found"},
    "search_done_n":      {"ja": "{n}件ヒット （{scanned}ファイル走査）",
                           "en": "{n} result(s) found ({scanned} files scanned)"},
    "search_done_skip":   {"ja": "{n}件ヒット （{scanned}ファイル走査、{skipped}件スキップ）",
                           "en": "{n} result(s) found ({scanned} scanned, {skipped} skipped)"},
    "search_cap_done":    {"ja": "一致{total}件中 上位{max}件を表示（多すぎます・絞り込んでください） （{scanned}ファイル走査）",
                           "en": "Showing top {max} of {total} matches — please refine ({scanned} files scanned)"},
    "search_cap_done_skip": {"ja": "一致{total}件中 上位{max}件を表示（多すぎます・絞り込んでください） （{scanned}ファイル走査、{skipped}件スキップ）",
                             "en": "Showing top {max} of {total} matches — please refine ({scanned} scanned, {skipped} skipped)"},
    "search_cap_running": {"ja": "検索中… 上位{max}件を表示中（一致が多すぎます・絞り込んでください）",
                           "en": "Searching… showing top {max} (too many matches — please refine)"},
    "search_building":    {"ja": "インデックス構築中…",  "en": "Building index…"},
    "search_updating":    {"ja": "インデックス更新中…",  "en": "Updating index…"},

    # ── プロパティダイアログ ──
    "prop_title":         {"ja": "プロパティ — {name}", "en": "Properties — {name}"},
    "prop_kind_folder":   {"ja": "フォルダ",            "en": "Folder"},
    "prop_kind_file":     {"ja": "ファイル ({ext})",    "en": "File ({ext})"},
    "prop_kind_noext":    {"ja": "ファイル (なし)",     "en": "File (no extension)"},
    "prop_row_name":      {"ja": "名前",                "en": "Name"},
    "prop_row_kind":      {"ja": "種類",                "en": "Type"},
    "prop_row_location":  {"ja": "場所",                "en": "Location"},
    "prop_row_size":      {"ja": "サイズ",              "en": "Size"},
    "prop_row_created":   {"ja": "作成日時",            "en": "Date Created"},
    "prop_row_modified":  {"ja": "更新日時",            "en": "Date Modified"},
    "prop_row_readonly":  {"ja": "読み取り専用",        "en": "Read-only"},
    "prop_yes":           {"ja": "はい",                "en": "Yes"},
    "prop_no":            {"ja": "いいえ",              "en": "No"},
    "prop_error":         {"ja": "エラー",              "en": "Error"},

    # ── テンプレート ──
    "tpl_label":          {"ja": "雛形: {name}",        "en": "Template: {name}"},

    # ── お気に入りサイドバー ──
    "fav_already_msg":    {"ja": "既に登録されています。", "en": "Already added."},
    "fav_label_display":  {"ja": "表示名:",            "en": "Display name:"},
    "fav_group_new_title":{"ja": "新規グループ",        "en": "New Group"},
    "fav_group_name_lbl": {"ja": "グループ名:",         "en": "Group name:"},
    "fav_group_new_default": {"ja": "新しいグループ",   "en": "New Group"},
    "fav_unreachable_mark":  {"ja": "(到達不可)",       "en": "(unreachable)"},
    "fav_unreachable_title": {"ja": "到達不可",         "en": "Unreachable"},
    "fav_unreachable_msg":   {"ja": "パスにアクセスできません:\n{path}\n\nネットワークドライブの接続や共有設定を確認してください。",
                              "en": "Cannot access path:\n{path}\n\nCheck the network drive connection or share settings."},
    "fav_ctx_new_group":  {"ja": "新規グループ…",       "en": "New Group…"},
    "fav_ctx_new_subgroup": {"ja": "このグループ内に新規グループ…", "en": "New Group in This Group…"},
    "fav_ctx_rename":     {"ja": "名前を変更…",         "en": "Rename…"},
    "fav_ctx_remove_group": {"ja": "削除（中身ごと）",  "en": "Delete (with contents)"},
    "fav_ctx_edit_note":  {"ja": "メモを編集…",         "en": "Edit Note…"},
    "fav_ctx_remove":     {"ja": "削除",                "en": "Delete"},
    "fav_rename_title":   {"ja": "名前を変更",          "en": "Rename"},
    "fav_note_title":     {"ja": "メモを編集",          "en": "Edit Note"},
    "fav_note_label":     {"ja": "メモ:",               "en": "Note:"},

    # ── ショートカット一覧（カテゴリ） ──
    "sc_cat_file":        {"ja": "ファイル操作",        "en": "File Operations"},
    "sc_cat_nav":         {"ja": "ナビゲーション",      "en": "Navigation"},
    "sc_cat_search":      {"ja": "検索・選択",          "en": "Search & Selection"},
    "sc_cat_view":        {"ja": "タブ・ペイン・表示",  "en": "Tabs, Panes & View"},

    # ── ショートカット一覧（機能名） ──
    "sc_copy":            {"ja": "コピー",              "en": "Copy"},
    "sc_cut":             {"ja": "切り取り",            "en": "Cut"},
    "sc_paste":           {"ja": "貼り付け",            "en": "Paste"},
    "sc_copy_path":       {"ja": "パスをコピー",        "en": "Copy Path"},
    "sc_trash":           {"ja": "削除（ゴミ箱）",      "en": "Delete (Trash)"},
    "sc_delete":          {"ja": "完全削除",            "en": "Delete Permanently"},
    "sc_rename":          {"ja": "単一リネーム",        "en": "Rename"},
    "sc_batch_rename":    {"ja": "一括リネーム",        "en": "Batch Rename"},
    "sc_new_folder":      {"ja": "新規フォルダー",      "en": "New Folder"},
    "sc_undo":            {"ja": "元に戻す",            "en": "Undo"},
    "sc_up":              {"ja": "上へ",                "en": "Up"},
    "sc_back":            {"ja": "戻る",                "en": "Back"},
    "sc_forward":         {"ja": "進む",                "en": "Forward"},
    "sc_path_input":      {"ja": "パス入力へ",          "en": "Focus Path Bar"},
    "sc_refresh":         {"ja": "更新",                "en": "Refresh"},
    "sc_search":          {"ja": "検索",                "en": "Search"},
    "sc_filter":          {"ja": "フィルタへ",          "en": "Focus Filter"},
    "sc_select_all":      {"ja": "すべて選択",          "en": "Select All"},
    "sc_new_tab":         {"ja": "新規タブ",            "en": "New Tab"},
    "sc_close_tab":       {"ja": "タブを閉じる",        "en": "Close Tab"},
    "sc_switch_tab":      {"ja": "タブ切替",            "en": "Switch Tab"},
    "sc_dual_pane":       {"ja": "デュアルペイン切替",  "en": "Toggle Dual Pane"},
    "sc_switch_pane":     {"ja": "ペイン切替",          "en": "Switch Pane"},
    "sc_toggle_view":     {"ja": "詳細／サムネイル切替", "en": "Detail / Thumbnail"},
    "sc_quick_preview":   {"ja": "クイックプレビュー",  "en": "Quick Preview"},
}


def apply_language(lang: str) -> None:
    """アプリ起動時に呼ぶ。lang は "ja" or "en"。"""
    global _LANG
    _LANG = lang if lang in ("ja", "en") else "ja"


def _(key: str) -> str:
    """翻訳文字列を返す。キーが無ければキー自身を返す（フォールバック安全）。"""
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    return entry.get(_LANG) or entry.get("ja") or key


def current_language() -> str:
    return _LANG
