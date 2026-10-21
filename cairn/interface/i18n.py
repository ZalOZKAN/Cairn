"""Interface language for Cairn's reports and dashboard.

Picked from, in order: the CAIRN_LANG environment variable, `ui.language`
in `.cairn/config.yaml`, the system locale, then English. Covers the review,
measurement and dashboard screens; older command help text is English only.

What Cairn writes for agents (AGENTS.md, session context, tool descriptions)
stays English on purpose: every model reads it reliably, and one wording
keeps the decision block diffable across a team that works in several
languages. Decisions themselves can be written in any language.
"""
from __future__ import annotations

import locale
import os

LANGUAGES = {"en": "English", "tr": "Türkçe", "fr": "Français", "es": "Español", "de": "Deutsch",
             "zh": "中文", "ja": "日本語"}

STRINGS: dict[str, dict[str, str]] = {
    "review.none": {
        "en": "No decisions are waiting for review.", "tr": "İncelemeyi bekleyen karar yok.",
        "fr": "Aucune décision en attente de revue.", "es": "No hay decisiones pendientes de revisión.",
        "de": "Keine Entscheidungen warten auf Prüfung.", "zh": "没有待审核的决定。", "ja": "レビュー待ちの決定はありません。"},
    "review.title": {
        "en": "Quarantined decisions (possible prompt injection)",
        "tr": "Karantinadaki kararlar (olası prompt injection)",
        "fr": "Décisions en quarantaine (injection de prompt possible)",
        "es": "Decisiones en cuarentena (posible inyección de prompt)",
        "de": "Entscheidungen in Quarantäne (mögliche Prompt-Injection)",
        "zh": "已隔离的决定（可能是提示词注入）", "ja": "隔離された決定（プロンプトインジェクションの可能性）"},
    "review.hint": {
        "en": "Approve with `cairn memory approve <id>`, or leave it: it stays inactive.",
        "tr": "Onaylamak için `cairn memory approve <id>`; dokunmazsan pasif kalır.",
        "fr": "Approuver avec `cairn memory approve <id>`, sinon elle reste inactive.",
        "es": "Apruébala con `cairn memory approve <id>`; si no, sigue inactiva.",
        "de": "Mit `cairn memory approve <id>` freigeben, sonst bleibt sie inaktiv.",
        "zh": "用 `cairn memory approve <id>` 批准；否则它保持停用。",
        "ja": "`cairn memory approve <id>` で承認。そのままなら無効のままです。"},
    "approve.ok": {
        "en": "Approved: the decision is active again.", "tr": "Onaylandı: karar yeniden etkin.",
        "fr": "Approuvée : la décision est de nouveau active.", "es": "Aprobada: la decisión vuelve a estar activa.",
        "de": "Freigegeben: Die Entscheidung ist wieder aktiv.", "zh": "已批准：该决定重新生效。",
        "ja": "承認しました：決定は再び有効です。"},
    "approve.missing": {
        "en": "Decision {id} is not quarantined.", "tr": "{id} numaralı karar karantinada değil.",
        "fr": "La décision {id} n'est pas en quarantaine.", "es": "La decisión {id} no está en cuarentena.",
        "de": "Entscheidung {id} ist nicht in Quarantäne.", "zh": "决定 {id} 未被隔离。",
        "ja": "決定 {id} は隔離されていません。"},
    "col.id": {"en": "ID", "tr": "ID", "fr": "ID", "es": "ID", "de": "ID", "zh": "ID", "ja": "ID"},
    "col.decision": {"en": "Decision", "tr": "Karar", "fr": "Décision", "es": "Decisión", "de": "Entscheidung",
                     "zh": "决定", "ja": "決定"},
    "col.reason": {"en": "Why held", "tr": "Neden tutuldu", "fr": "Motif", "es": "Motivo", "de": "Grund",
                   "zh": "原因", "ja": "理由"},
    "col.source": {"en": "Source", "tr": "Kaynak", "fr": "Source", "es": "Origen", "de": "Quelle",
                   "zh": "来源", "ja": "出所"},
    "missed.none": {
        "en": "No possible misses: every decision-like message was followed by record_decision.",
        "tr": "Kaçırılan yok gibi: karar benzeri her mesajdan sonra record_decision çağrıldı.",
        "fr": "Aucun oubli probable : chaque message de type décision a été suivi d'un record_decision.",
        "es": "Sin posibles omisiones: cada mensaje con una decisión fue seguido de record_decision.",
        "de": "Keine möglichen Lücken: Auf jede entscheidungsartige Nachricht folgte record_decision.",
        "zh": "没有疑似遗漏：每条像决定的消息后都调用了 record_decision。",
        "ja": "見落としの可能性なし：決定らしいメッセージの後には必ず record_decision がありました。"},
    "missed.title": {
        "en": "Possibly missed decisions", "tr": "Kaçırılmış olabilecek kararlar",
        "fr": "Décisions peut-être oubliées", "es": "Decisiones posiblemente omitidas",
        "de": "Möglicherweise verpasste Entscheidungen", "zh": "可能遗漏的决定", "ja": "見落とされた可能性のある決定"},
    "missed.summary": {
        "en": "{signals} decision-like messages, {covered} recorded, {missed} possibly missed (capture rate {rate}).",
        "tr": "{signals} karar benzeri mesaj, {covered} kaydedildi, {missed} kaçırılmış olabilir (yakalama oranı {rate}).",
        "fr": "{signals} messages de type décision, {covered} enregistrés, {missed} peut-être oubliés (taux {rate}).",
        "es": "{signals} mensajes con decisiones, {covered} registrados, {missed} posiblemente omitidos (tasa {rate}).",
        "de": "{signals} entscheidungsartige Nachrichten, {covered} erfasst, {missed} evtl. verpasst (Quote {rate}).",
        "zh": "{signals} 条像决定的消息，已记录 {covered} 条，可能遗漏 {missed} 条（捕获率 {rate}）。",
        "ja": "決定らしいメッセージ {signals} 件、記録 {covered} 件、見落とし候補 {missed} 件（捕捉率 {rate}）。"},
    "missed.hint": {
        "en": "A real decision here? Record it: cairn decide \"<decision>\" --why \"<reason>\".",
        "tr": "Gerçekten bir karar mı? Kaydet: cairn decide \"<karar>\" --why \"<neden>\".",
        "fr": "Une vraie décision ? Enregistrez-la : cairn decide \"<décision>\" --why \"<raison>\".",
        "es": "¿Una decisión real? Regístrala: cairn decide \"<decisión>\" --why \"<motivo>\".",
        "de": "Echte Entscheidung? Erfassen: cairn decide \"<Entscheidung>\" --why \"<Grund>\".",
        "zh": "确实是决定？记录它：cairn decide \"<决定>\" --why \"<原因>\"。",
        "ja": "本当に決定なら記録：cairn decide \"<決定>\" --why \"<理由>\"。"},
    "col.when": {"en": "When", "tr": "Ne zaman", "fr": "Quand", "es": "Cuándo", "de": "Wann", "zh": "时间", "ja": "日時"},
    "col.signal": {"en": "Signal", "tr": "İşaret", "fr": "Signal", "es": "Señal", "de": "Signal", "zh": "信号",
                   "ja": "シグナル"},
    "col.message": {"en": "Message", "tr": "Mesaj", "fr": "Message", "es": "Mensaje", "de": "Nachricht",
                    "zh": "消息", "ja": "メッセージ"},
    "usage.none": {
        "en": "No Claude Code transcripts found for this project yet.",
        "tr": "Bu proje için henüz Claude Code transkripti bulunamadı.",
        "fr": "Aucune transcription Claude Code trouvée pour ce projet.",
        "es": "Aún no hay transcripciones de Claude Code para este proyecto.",
        "de": "Noch keine Claude-Code-Transkripte für dieses Projekt gefunden.",
        "zh": "尚未找到此项目的 Claude Code 会话记录。", "ja": "このプロジェクトの Claude Code トランスクリプトはまだありません。"},
    "usage.title": {
        "en": "Token use per session, with and without Cairn", "tr": "Oturum başına token kullanımı, Cairn'li ve Cairn'siz",
        "fr": "Tokens par session, avec et sans Cairn", "es": "Tokens por sesión, con y sin Cairn",
        "de": "Tokens pro Sitzung, mit und ohne Cairn", "zh": "每个会话的 token 用量（使用与未使用 Cairn）",
        "ja": "セッションあたりのトークン（Cairn あり／なし）"},
    "col.group": {"en": "Sessions", "tr": "Oturumlar", "fr": "Sessions", "es": "Sesiones", "de": "Sitzungen",
                  "zh": "会话", "ja": "セッション"},
    "col.count": {"en": "Count", "tr": "Adet", "fr": "Nombre", "es": "Cantidad", "de": "Anzahl", "zh": "数量",
                  "ja": "件数"},
    "col.calls": {"en": "Model calls", "tr": "Model çağrısı", "fr": "Appels modèle", "es": "Llamadas al modelo",
                  "de": "Modellaufrufe", "zh": "模型调用", "ja": "モデル呼び出し"},
    "col.input": {"en": "Input", "tr": "Girdi", "fr": "Entrée", "es": "Entrada", "de": "Eingabe", "zh": "输入",
                  "ja": "入力"},
    "col.cache_read": {"en": "Cache read", "tr": "Önbellek okuma", "fr": "Cache lu", "es": "Caché leída",
                       "de": "Cache gelesen", "zh": "缓存读取", "ja": "キャッシュ読取"},
    "col.output": {"en": "Output", "tr": "Çıktı", "fr": "Sortie", "es": "Salida", "de": "Ausgabe", "zh": "输出",
                   "ja": "出力"},
    "col.billable": {"en": "Billable input*", "tr": "Faturalanan girdi*", "fr": "Entrée facturable*",
                     "es": "Entrada facturable*", "de": "Abrechenbare Eingabe*", "zh": "计费输入*", "ja": "課金対象入力*"},
    "group.with": {"en": "with Cairn", "tr": "Cairn'li", "fr": "avec Cairn", "es": "con Cairn", "de": "mit Cairn",
                   "zh": "使用 Cairn", "ja": "Cairn あり"},
    "group.without": {"en": "without Cairn", "tr": "Cairn'siz", "fr": "sans Cairn", "es": "sin Cairn",
                      "de": "ohne Cairn", "zh": "未使用 Cairn", "ja": "Cairn なし"},
    "usage.billable_note": {
        "en": "*input + 1.25 x cache writes + 0.1 x cache reads: how Anthropic bills input. Averages per session.",
        "tr": "*girdi + 1.25 x önbellek yazma + 0.1 x önbellek okuma: Anthropic girdiyi böyle faturalar. Oturum ortalaması.",
        "fr": "*entrée + 1,25 x écritures cache + 0,1 x lectures cache : facturation Anthropic. Moyenne par session.",
        "es": "*entrada + 1,25 x escrituras de caché + 0,1 x lecturas: así factura Anthropic. Media por sesión.",
        "de": "*Eingabe + 1,25 x Cache-Schreiben + 0,1 x Cache-Lesen: so rechnet Anthropic ab. Mittel je Sitzung.",
        "zh": "*输入 + 1.25 x 缓存写入 + 0.1 x 缓存读取：Anthropic 的输入计费方式。按会话平均。",
        "ja": "*入力 + 1.25 x キャッシュ書込 + 0.1 x キャッシュ読取：Anthropic の課金方法。セッション平均。"},
    "usage.caveat": {
        "en": "Compare like with like: sessions differ in task size. Run the same kind of work for a week each way.",
        "tr": "Benzer işleri karşılaştır: oturumların iş büyüklüğü farklı. Aynı tür işi birer hafta iki şekilde yap.",
        "fr": "Comparez des tâches similaires : la taille varie. Une semaine de chaque façon sur le même type de travail.",
        "es": "Compara tareas similares: el tamaño varía. Una semana de cada forma con el mismo tipo de trabajo.",
        "de": "Vergleichbares vergleichen: Aufgaben sind verschieden groß. Je eine Woche gleiche Arbeit pro Variante.",
        "zh": "请比较同类任务：会话的任务规模不同。两种方式各用一周做同类工作。",
        "ja": "同種の作業で比較してください。同じ種類の作業を各方式で1週間ずつ。"},
}


# Dashboard labels, one list per key in LANGUAGES order (en, tr, fr, es, de, zh, ja).
_DASHBOARD_ROWS: dict[str, list[str]] = {
    "title": ["Cairn: Decision Memory", "Cairn: Karar Hafızası", "Cairn : mémoire des décisions",
              "Cairn: memoria de decisiones", "Cairn: Entscheidungsgedächtnis", "Cairn：决定记忆", "Cairn：決定メモリ"],
    "subtitle": ["Search here before re-arguing a call your team already made. Generated",
                 "Bir kararı yeniden tartışmadan önce burada ara. Oluşturulma:",
                 "Cherchez ici avant de rediscuter une décision déjà prise. Généré le",
                 "Busca aquí antes de volver a discutir una decisión ya tomada. Generado",
                 "Hier suchen, bevor eine getroffene Entscheidung neu diskutiert wird. Erstellt",
                 "重新讨论已做出的决定之前，先在这里搜索。生成于", "決定済みの事項を蒸し返す前にここで検索。生成日時"],
    "theme": ["Toggle Theme", "Tema", "Thème", "Tema", "Design", "切换主题", "テーマ切替"],
    "search": ["Search decisions...", "Kararlarda ara...", "Rechercher...", "Buscar decisiones...",
               "Entscheidungen suchen...", "搜索决定...", "決定を検索..."],
    "status": ["Status", "Durum", "Statut", "Estado", "Status", "状态", "状態"],
    "all": ["All", "Tümü", "Toutes", "Todas", "Alle", "全部", "すべて"],
    "active": ["Active", "Etkin", "Actives", "Activas", "Aktiv", "生效", "有効"],
    "invalidated": ["Invalidated", "Geçersiz", "Invalidées", "Invalidadas", "Ungültig", "已失效", "無効"],
    "recent": ["Last 14d", "Son 14 gün", "14 derniers jours", "Últimos 14 días", "Letzte 14 Tage", "近14天", "直近14日"],
    "needs_docs": ["Needs docs", "Gerekçe eksik", "À documenter", "Sin documentar", "Ohne Begründung",
                   "缺少理由", "要文書化"],
    "view": ["View", "Görünüm", "Vue", "Vista", "Ansicht", "视图", "表示"],
    "graph": ["Graph", "Grafik", "Graphe", "Grafo", "Graph", "图", "グラフ"],
    "cards": ["Cards", "Kartlar", "Cartes", "Tarjetas", "Karten", "卡片", "カード"],
    "map": ["Decision Map", "Karar Haritası", "Carte des décisions", "Mapa de decisiones", "Entscheidungskarte",
            "决定图", "決定マップ"],
    "decisions": ["Decisions", "Kararlar", "Décisions", "Decisiones", "Entscheidungen", "决定", "決定"],
    "activity": ["Activity (30 days, this project)", "Etkinlik (30 gün, bu proje)", "Activité (30 jours, ce projet)",
                 "Actividad (30 días, este proyecto)", "Aktivität (30 Tage, dieses Projekt)", "活动（30天，本项目）",
                 "アクティビティ（30日、このプロジェクト）"],
    "empty": ["No decisions recorded yet.", "Henüz kayıtlı karar yok.", "Aucune décision enregistrée.",
              "Aún no hay decisiones.", "Noch keine Entscheidungen.", "尚无决定。", "まだ決定はありません。"],
    "no_match": ["No decisions match the current search/filters.", "Aramaya uyan karar yok.",
                 "Aucune décision ne correspond.", "Ninguna decisión coincide.", "Keine passenden Entscheidungen.",
                 "没有匹配的决定。", "一致する決定はありません。"],
    "footer": ["Generated by Cairn. No server, no telemetry, just an HTML file.",
               "Cairn tarafından üretildi. Sunucu yok, telemetri yok, sadece bir HTML dosyası.",
               "Généré par Cairn. Pas de serveur, pas de télémétrie, juste un fichier HTML.",
               "Generado por Cairn. Sin servidor ni telemetría, solo un archivo HTML.",
               "Von Cairn erzeugt. Kein Server, keine Telemetrie, nur eine HTML-Datei.",
               "由 Cairn 生成。无服务器、无遥测，只是一个 HTML 文件。",
               "Cairn が生成。サーバーもテレメトリもなし、HTML ファイルだけです。"],
    "health": ["Health", "Sağlık", "Santé", "Salud", "Zustand", "健康状况", "ヘルス"],
    "quarantined": ["Quarantined (review with `cairn memory review`)",
                    "Karantinada (`cairn memory review` ile incele)",
                    "En quarantaine (`cairn memory review`)", "En cuarentena (`cairn memory review`)",
                    "In Quarantäne (`cairn memory review`)", "已隔离（用 `cairn memory review` 审核）",
                    "隔離中（`cairn memory review` で確認）"],
    "capture": ["Decision capture rate (`cairn memory missed`)", "Karar yakalama oranı (`cairn memory missed`)",
                "Taux de capture (`cairn memory missed`)", "Tasa de captura (`cairn memory missed`)",
                "Erfassungsquote (`cairn memory missed`)", "决定捕获率（`cairn memory missed`）",
                "決定の捕捉率（`cairn memory missed`）"],
    "missed": ["possibly missed", "kaçırılmış olabilir", "peut-être oubliées", "posiblemente omitidas",
               "evtl. verpasst", "可能遗漏", "見落とし候補"],
    "no_data": ["no data yet", "henüz veri yok", "pas encore de données", "sin datos aún", "noch keine Daten",
                "暂无数据", "まだデータなし"],
    "helped": ["Cairn stepped in", "Cairn devreye girdi", "Interventions de Cairn", "Cairn intervino",
               "Cairn hat eingegriffen", "Cairn 介入次数", "Cairn の介入"],
    "h_rejected": ["rejected ideas caught", "reddedilmiş fikir yakalandı", "idées rejetées repérées",
                   "ideas descartadas detectadas", "verworfene Ideen erkannt", "次拦下已否决方案", "件の却下案を検出"],
    "h_blocked": ["forbidden edits blocked", "yasak düzenleme engellendi", "modifications interdites bloquées",
                  "cambios prohibidos bloqueados", "verbotene Änderungen blockiert", "次阻止禁止的修改", "件の禁止編集をブロック"],
    "h_reminders": ["reminders before edits", "düzenleme öncesi hatırlatma", "rappels avant modification",
                    "recordatorios antes de editar", "Hinweise vor Änderungen", "次编辑前提醒", "件の編集前リマインド"],
    "h_recorded": ["decisions recorded", "karar kaydedildi", "décisions enregistrées", "decisiones registradas",
                   "Entscheidungen erfasst", "个决定已记录", "件の決定を記録"],
    "language": ["Language", "Dil", "Langue", "Idioma", "Sprache", "语言", "言語"],
}
DASHBOARD_STRINGS: dict[str, dict[str, str]] = {
    code: {key: row[i] for key, row in _DASHBOARD_ROWS.items()} for i, code in enumerate(LANGUAGES)
}


def current_language(config: dict | None = None) -> str:
    env = (os.environ.get("CAIRN_LANG") or "").strip().lower()[:2]
    if env in LANGUAGES:
        return env
    configured = str(((config or {}).get("ui") or {}).get("language") or "").strip().lower()[:2]
    if configured in LANGUAGES:
        return configured
    try:
        loc = (locale.getlocale()[0] or os.environ.get("LANG") or "").lower()
    except ValueError:
        loc = ""
    for code in LANGUAGES:
        if loc.startswith(code) or {"tr": "turkish", "fr": "french", "es": "spanish", "de": "german",
                                    "zh": "chinese", "ja": "japanese"}.get(code, "\0") in loc:
            return code
    return "en"


def t(key: str, lang: str | None = None, **values: object) -> str:
    entry = STRINGS.get(key, {})
    text = entry.get(lang or current_language()) or entry.get("en") or key
    return text.format(**values) if values else text
