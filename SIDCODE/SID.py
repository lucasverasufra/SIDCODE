# SID.py — Duas páginas: 1) Código AA00AA; 2) Formulário por código
# Cronômetro inicia no PRIMEIRO ACESSO (não no start do servidor)
# Último envio é ACEITO e gravado mesmo após expirar os 40 min
# Bloqueio total de reenvio por código
# Meta: Alt+M (sempre ativo) | Banner de progresso na 2ª tela com Alt+P (padrão DESATIVADO)
# Requisitos: pip install flask

import string
import csv, re, unicodedata, os, sys
from difflib import SequenceMatcher # type: ignore
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, request, Response, redirect, url_for

APP = Flask(__name__)

# ---------------- Condição / Flags ----------------
COND = "B"              # Modo B ativo
SHOW_PROGRESS = False   # Banner de progresso vem DESATIVADO; Alt+P alterna
META_GOAL = None

# Contadores de formulários (sessão)
CORRECT_COUNT = 0       # formulários 100% corretos
ERROR_COUNT = 0         # formulários com pelo menos 1 erro

# ---------------- Sessão (timer inicia no primeiro acesso) ----------------
SESSION_DURATION_SECONDS = 40 * 60  # 40 min
SESSION_START_DT = None
SESSION_DEADLINE = None

# Arquivos de sessão
SESSION_ID = None
SESSION_CSV = None
SESSION_ERR_CSV = None
META_FILE = None
SUMMARY_FILE = None
FILES_INIT = False  # evita reinit
SUMMARY_WRITTEN = False  # garante que o CSV da sessão é escrito só uma vez

# Resumo global de erros da sessão
TOTAL_ERRORS = 0
ERROR_COUNTERS = {
    "extra/missing spaces": 0,
    "punctuation difference": 0,
    "accent difference": 0,
    "wrong character / mistype": 0,
    "incomplete text": 0,
    "wrong word order": 0,
    "incorrect": 0
}


def ensure_session_started():
    """
    Inicia a sessão no primeiro acesso útil e cria os arquivos da sessão com headers.
    """
    global SESSION_START_DT, SESSION_DEADLINE, SESSION_ID
    global SESSION_CSV, SESSION_ERR_CSV, META_FILE, SUMMARY_FILE, FILES_INIT

    if SESSION_START_DT is not None:
        return

    # Marca início agora
    SESSION_START_DT = datetime.now()
    SESSION_DEADLINE = SESSION_START_DT + timedelta(seconds=SESSION_DURATION_SECONDS)

    # Define IDs e paths de arquivos agora (timestamp da sessão)
    SESSION_ID = SESSION_START_DT.strftime("%Y%m%d-%H%M%S")
    SESSION_CSV = EXPORTS / f"sessao-{SESSION_ID}.csv"
    SESSION_ERR_CSV = EXPORTS / f"sessao-erros-{SESSION_ID}.csv"
    META_FILE = EXPORTS / f"meta-{SESSION_ID}.txt"
    SUMMARY_FILE = EXPORTS / f"sumario-{SESSION_ID}.txt"

    # Cria arquivos com headers (uma única vez)
    if not FILES_INIT:
        # CSV principal da sessão: RESUMO GERAL (1 linha)
        with SESSION_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "InicioSessao", "FimSessao", "DuracaoSessao(seg)",
                "FormulariosCorretos", "FormulariosComErro", "TotalErros",
                "Erros_Espacos", "Erros_Pontuacao", "Erros_Acento",
                "Erros_Digito", "Erros_Incompletos", "Erros_Ordem", "Erros_Divergentes"
            ])
        # CSV de erros: granular (uma linha por erro)
        with SESSION_ERR_CSV.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(ERR_HEADERS_PT)
        FILES_INIT = True


# Aciona o início da sessão somente em rotas que representam interação do participante
@APP.before_request
def _start_session_on_first_use():
    # Agora NÃO inicia o cronômetro automaticamente
    # O tempo só começa quando o código for enviado (POST /start)
    pass


# ---------------- Paths seguros (.py e .exe) ----------------
def get_base_dir():
    if getattr(sys, 'frozen', False):  # PyInstaller
        return Path(sys.executable).parent
    return Path(__file__).parent


BASE = get_base_dir()
EXPORTS = BASE / "exports"
EXPORTS.mkdir(parents=True, exist_ok=True)

GABARITO_CSV = BASE / "survey_2024.csv"   # precisa ter 'Participante' (AA00AA)

# ---------------- Constantes ----------------
CODE_FIELD = "Participante"
CODE_LABEL = "Código do participante (AA00AA)"
CODE_REGEX = re.compile(r"^[A-Za-z]{2}\d{2}[A-Za-z]{2}$")

PRETTY = {
    "Q1_PapelAtual": "1. Papel atual",
    "Q2_LinguagensMaisUsadas": "2. Linguagens mais usadas",
    "Q3_ComoAprendeu": "3. Como aprendeu a programar",
    "Q4_AmbientePreferido": "4. Ambiente de trabalho preferido",
    "Q5_FerramentasPlataformas": "5. Ferramentas e plataformas mais usadas",
    "Q6_UsoDeIA": "6. Uso de IA nos projetos",
    "Q7_Motivacao": "7. O que mais motiva ao programar",
    "Q8_Desafios": "8. Maiores desafios",
    "Q9_TecnologiasParaAprender": "9. Tecnologias que deseja aprender",
    "Q10_PorQueContinua": "10. Por que continua estudando/programando",
}
QUESTION_KEYS = list(PRETTY.keys())

# Cabeçalho do CSV de erros (não do resumo)
ERR_HEADERS_PT = [
    "Data/Hora do Envio", "Código", "Pergunta", "Resposta Dada",
    "Gabarito", "Tipo do Erro", "Início", "Envio", "Duração (segundos)"
]

# ---------------- Lista de códigos usados (persistente/robusto) ----------------
USED_CODES_FILE = EXPORTS / "codigos_utilizados.txt"
USED_CODES = set()


def load_used_codes_into_memory():
    USED_CODES.clear()
    if USED_CODES_FILE.exists():
        for line in USED_CODES_FILE.read_text(encoding="utf-8").splitlines():
            line = (line or "").strip().upper()
            if line:
                USED_CODES.add(line)


load_used_codes_into_memory()


def is_code_used(code: str) -> bool:
    code = (code or "").strip().upper()
    load_used_codes_into_memory()
    return code in USED_CODES


def mark_code_as_used(code: str):
    code = (code or "").strip().upper()
    if not code:
        return
    load_used_codes_into_memory()
    if code in USED_CODES:
        return
    with USED_CODES_FILE.open("a", encoding="utf-8") as f:
        f.write(code + "\n")
    USED_CODES.add(code)


# ---------------- Gabarito ----------------
GAB_ROWS, GAB_COLS = [], []


def load_gabarito():
    global GAB_ROWS, GAB_COLS
    if not GABARITO_CSV.exists():
        raise RuntimeError("Arquivo survey_2024.csv não encontrado.")
    with GABARITO_CSV.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        GAB_COLS = r.fieldnames or []
        GAB_ROWS = list(r)
    if CODE_FIELD not in GAB_COLS:
        raise RuntimeError(f"O CSV do gabarito precisa ter a coluna '{CODE_FIELD}'.")
    missing = [q for q in QUESTION_KEYS if q not in GAB_COLS]
    if missing:
        raise RuntimeError(f"Colunas faltando no CSV gabarito: {missing}")


def get_gabarito_row_by_code(code: str):
    code = (code or "").strip().upper()
    for row in GAB_ROWS:
        if (row.get(CODE_FIELD, "") or "").strip().upper() == code:
            return row
    return None


load_gabarito()

# ---------------- Helpers antigos (alguns ainda usados) ----------------
def strip_spaces(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def lower_no_accents(s):
    s = strip_spaces(s).lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def no_punct(s):
    return re.sub(r"[^\w\s]", "", strip_spaces(s), flags=re.UNICODE)


def remove_spaces(s):
    return re.sub(r"\s+", "", (s or ""))


def remove_commas_semicolons(s):
    return re.sub(r"[;,]+", "", (s or ""))


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


# ---------------- Comparação ULTRA RÍGIDA + CLASSIFICAÇÃO ----------------
def normalize_accents(s):
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )


def classify_error(user, gold):
    """
    Classificação comportamental do erro.
    Agora o nível máximo se chama → ERRO DIVERGENTE.
    Representa resposta totalmente distinta, sem correspondência útil com o gabarito.
    """
    if user == "":
        return "missing"

    # Perfeito literal (modo ultra rígido)
    if user == gold:
        return "correct"

    u, g = user, gold

    # ==============================
    # 1) Erro por ESCRITA (forma)
    # ==============================

    # Só espaços diferentes
    if u.replace(" ", "") == g.replace(" ", ""):
        return "extra/missing spaces"

    # Sem pontuação igual, mas texto com mesmas letras/acentos
    u_p = ''.join(c for c in u if c in string.punctuation)
    g_p = ''.join(c for c in g if c in string.punctuation)
    if normalize_accents(u) == normalize_accents(g) and u_p != g_p:
        return "punctuation difference"

    # Mesmas letras sem acento → erro perceptivo
    if normalize_accents(u) == normalize_accents(g):
        return "accent difference"

    # Mesmo tamanho aproximado → erro de digitação
    if len(u) == len(g):
        return "wrong character / mistype"

    # ==============================
    # 2) Erro por conteúdo
    # ==============================

    if len(u) < len(g) and u in g:
        return "incomplete text"

    # Mesmas palavras, ordem trocada
    if sorted(u.split()) == sorted(g.split()):
        return "wrong word order"

    # ==============================
    # 3) Nível final
    # ==============================
    return "erro divergente"   # substitui o antigo “incorrigível”



def compare_levels(user_text, gold_text):
    if user_text == "":
        return "missing"
    if user_text == gold_text:  # ultra rígido literal
        return "correct"
    return "incorrect"


# ---------------- HTML utils ----------------
def esc(s):
    s = s or ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") \
        .replace('"', "&quot;").replace("'", "&#39;")


# ---------------- Templates ----------------
CSS_COMMON = (
    "<style>"
    "body{font-family:system-ui,Arial,sans-serif;max-width:920px;margin:24px auto;padding:0 12px;font-size:18px;line-height:1.6}"
    "h1{margin:0 0 12px 0}"
    ".card{border:1px solid #e5e7eb;border-radius:12px;padding:16px;margin:14px 0}"
    ".q-title{font-weight:700;margin-bottom:8px}"
    ".btn{padding:10px 16px;border-radius:10px;border:1px solid #111827;background:#111827;color:#fff;cursor:pointer;font-size:16px}"
    ".inp{width:100%;padding:10px;border-radius:8px;border:1px solid #d1d5db;background:white;font-size:17px}"
    ".banner{border-radius:10px;padding:10px 12px;margin:8px 0;font-weight:600;font-size:16px}"
    ".banner.ok{background:#dcfce7;border:1px solid #16a34a;color:#065f46}"
    ".banner.info{background:#e5e7eb;border:1px solid #374151;color:#111827}"
    ".meta-btn{position:fixed;right:-9999px;bottom:-9999px;opacity:0;}"
    "</style>"
)
CSS_NARROW = CSS_COMMON.replace("max-width:920px", "max-width:720px")

JS_NAV_FORM = (
    "<script>"
    "(function(){"
    "  var form = document.getElementById('formMain');"
    "  var btn  = document.getElementById('btnEnviar');"
    "  var inputs = Array.prototype.slice.call(form.querySelectorAll('input.inp'));"
    "  form.addEventListener('submit', function(e){ e.preventDefault(); });"
    "  btn.addEventListener('click', function(){ form.submit(); });"
    "  inputs.forEach(function(el, i){"
    "    el.addEventListener('keydown', function(e){"
    "      if (e.key === 'Enter') {"
    "        e.preventDefault();"
    "        var next = inputs[i+1];"
    "        if (next) { next.focus(); try{ next.select(); } catch(_){} }"
    "        else { form.submit(); }"
    "      }"
    "    });"
    "  });"
    "})();"
    "</script>"
)

JS_GATE = (
    "<script>"
    "(function(){"
    "  var form = document.getElementById('formCode');"
    "  var code = document.getElementById('__code');"
    "  code.addEventListener('keydown', function(e){ if (e.key === 'Enter') { form.requestSubmit(); }});"
    "})();"
    "</script>"
)


def js_meta_widget():
    return (
        "<button id='__meta' class='meta-btn' accesskey='m' title='meta' aria-hidden='true'>.</button>"
        "<script>"
        "(function(){"
        "  var b=document.getElementById('__meta');"
        "  function setM(){"
        "    var v=prompt('Definir META (número inteiro):');"
        "    if(v===null) return;"
        "    fetch('/set_meta',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},"
        "      body:'meta='+encodeURIComponent(v)})"
        "      .then(()=>alert('Meta registrada.'));"
        "  }"
        "  b.addEventListener('click', setM);"
        "  window.addEventListener('keydown',function(e){"
        "    var k=(e.key||'').toLowerCase();"
        "    var alt=e.altKey||e.metaKey, ctrl=e.ctrlKey||false;"
        "    if( (alt && k==='m') || (alt && ctrl && k==='m') ){ e.preventDefault(); setM(); }"
        "  });"
        "})();"
        "</script>"
    )


def js_progress_toggle():
    return (
        "<button id='__toggleProgress' class='meta-btn' accesskey='p' title='progress' aria-hidden='true'>.</button>"
        "<script>"
        "(function(){"
        "  function toggleProgress(){"
        "    fetch('/toggle_progress',{method:'POST'})"
        "      .then(r=>r.text())"
        "      .then(msg=>{ alert(msg); location.reload(); });"
        "  }"
        "  var b=document.getElementById('__toggleProgress');"
        "  b.addEventListener('click',toggleProgress);"
        "  window.addEventListener('keydown',function(e){"
        "    var k=(e.key||'').toLowerCase();"
        "    var alt=e.altKey||e.metaKey, ctrl=e.ctrlKey||false, shift=e.shiftKey||false;"
        "    if( (alt && k==='p') || (alt && ctrl && k==='p') || (alt && shift && k==='p') ){"
        "       e.preventDefault(); toggleProgress();"
        "    }"
        "  }, true);"
        "})();"
        "</script>"
    )

# ---------------- Helpers de progresso ----------------
def build_progress_banner():
    if COND != "B" or not SHOW_PROGRESS:
        return ""
    # Condição B: progresso baseado APENAS em acertos
    feitos = CORRECT_COUNT
    if META_GOAL is None:
        msg = f"Progresso: {feitos} feito(s)."
    else:
        remain = max(META_GOAL - feitos, 0)
        msg = f"Progresso: {feitos} feito(s) • faltam {remain} para a meta de {META_GOAL}."
    return "<div class='banner info'>" + esc(msg) + "</div>"


# ---------------- Sumário ----------------
def render_summary():
    global SUMMARY_WRITTEN

    # Persistência simples do sumário em TXT
    if SUMMARY_FILE:
        SUMMARY_FILE.write_text(
            f"Início: {SESSION_START_DT.isoformat(timespec='seconds') if SESSION_START_DT else '-'}\n"
            f"Fim (limite): {SESSION_DEADLINE.isoformat(timespec='seconds') if SESSION_DEADLINE else '-'}\n"
            f"Acertos (100%): {CORRECT_COUNT}\n"
            f"Com erro: {ERROR_COUNT}\n"
            f"Meta: {META_GOAL}\n"
            f"Total de erros (respostas): {TOTAL_ERRORS}\n",
            encoding="utf-8"
        )

    # Grava o RESUMO no CSV da sessão, apenas uma vez
    if not SUMMARY_WRITTEN and SESSION_CSV is not None and SESSION_START_DT is not None:
        dur = int((SESSION_DEADLINE - SESSION_START_DT).total_seconds()) if SESSION_DEADLINE else 0
        with SESSION_CSV.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                SESSION_START_DT.isoformat(timespec="seconds"),
                SESSION_DEADLINE.isoformat(timespec="seconds") if SESSION_DEADLINE else "-",
                dur,
                CORRECT_COUNT,
                ERROR_COUNT,
                TOTAL_ERRORS,
                ERROR_COUNTERS["extra/missing spaces"],
                ERROR_COUNTERS["punctuation difference"],
                ERROR_COUNTERS["accent difference"],
                ERROR_COUNTERS["wrong character / mistype"],
                ERROR_COUNTERS["incomplete text"],
                ERROR_COUNTERS["wrong word order"],
                ERROR_COUNTERS["incorrect"]
            ])
        SUMMARY_WRITTEN = True

    # HTML pro pesquisador
    html = []
    html.append("<!doctype html><html lang='pt-BR'><meta charset='utf-8'>")
    html.append("<title>SID — Sumário</title>")
    html.append(CSS_NARROW)
    html.append("<body><h1>Sumário</h1>")
    html.append("<div class='card'>")
    html.append(f"<div><b>Você acertou {CORRECT_COUNT} formulário(s)até aqui.</div>")
    return "".join(html)


# ---------------- Página 1: código ----------------
@APP.get("/")
def page_code():
    # se a sessão ainda não começou, ensure_session_started será chamado no before_request (no primeiro hit)
    if SESSION_DEADLINE and datetime.now() >= SESSION_DEADLINE:
        return Response(render_summary(), mimetype="text/html; charset=utf-8")
    return Response(render_code_gate(), mimetype="text/html; charset=utf-8")


def render_code_gate(msg=None, code_value=""):
    code_val = esc(code_value or "")
    html = []
    html.append("<!doctype html><html lang='pt-BR'><meta charset='utf-8'>")
    html.append("<title>SID — Digite seu código</title>")
    html.append(CSS_NARROW)
    html.append("<body><h1>Informe seu código</h1>")
    if msg:
        html.append("<div class='banner info' role='status' aria-live='polite'>" + esc(msg) + "</div>")
    html.append(
        "<form id='formCode' method='post' action='/start' autocomplete='off' novalidate>"
        "  <div class='card'>"
        f"    <label for='__code'><b>{esc(CODE_LABEL)}</b></label>"
        "    <input class='inp' type='text' name='__code' id='__code' autofocus "
        "           placeholder='Ex.: AB12CD' value='" + code_val + "' "
        "           autocomplete='off' autocapitalize='off' autocorrect='off' spellcheck='false'>"
        "  </div>"
        "  <button id='btnIr' class='btn' type='submit'>Continuar</button>"
        "</form>"
    )
    html.append(JS_GATE)
    html.append(js_meta_widget())
    html.append(js_progress_toggle())
    html.append("</body></html>")
    return "".join(html)


@APP.post("/start")
def start():
    global SESSION_START_DT
                #aqui passa a ser o gatilho real do início da sessão
    if SESSION_START_DT is None:
        ensure_session_started()
    if SESSION_DEADLINE and datetime.now() >= SESSION_DEADLINE:
        return Response(render_summary(), mimetype="text/html; charset=utf-8")

    code_input = (request.form.get("__code") or "").strip().upper()
    if strip_spaces(code_input) == "":
        return Response(
            render_code_gate("Informe o código do participante (AA00AA).", code_value=code_input),
            mimetype="text/html; charset=utf-8"
        )
    if not CODE_REGEX.match(code_input):
        return Response(
            render_code_gate("Código inválido. Use o padrão AA00AA (ex.: AB12CD).", code_value=code_input),
            mimetype="text/html; charset=utf-8"
        )
    if is_code_used(code_input):
        return Response(
            render_code_gate("Código já utilizado. Selecione um novo código.", code_value=code_input),
            mimetype="text/html; charset=utf-8"
        )
    if get_gabarito_row_by_code(code_input) is None:
        return Response(
            render_code_gate("Código não encontrado no gabarito.", code_value=code_input),
            mimetype="text/html; charset=utf-8"
        )
    started_at = datetime.now().isoformat(timespec="seconds")
    return redirect(url_for("page_form", code=code_input, started=started_at))


# ---------------- Página 2: formulário ----------------
@APP.get("/form")
def page_form():
    # Importante: se já está no formulário, deixamos concluir mesmo após expirar
    code = (request.args.get("code") or "").strip().upper()
    started_at = request.args.get("started") or datetime.now().isoformat(timespec="seconds")
    if not CODE_REGEX.match(code) or get_gabarito_row_by_code(code) is None:
        return redirect(url_for("page_code"))
    if is_code_used(code):
        return Response(
            render_code_gate("Código já utilizado. Selecione um novo código.", code_value=code),
            mimetype="text/html; charset=utf-8"
        )
    return Response(render_form(code, started_at), mimetype="text/html; charset=utf-8")


def render_form(code, started_at):
    started_at_val = esc(started_at)
    code_val = esc(code)
    html = []
    html.append("<!doctype html><html lang='pt-BR'><meta charset='utf-8'>")
    html.append("<title>SID — Transcrição (" + code_val + ")</title>")
    html.append(CSS_COMMON)
    html.append("<body>")
    html.append("<h1>Transcrição — Código " + code_val + "</h1>")
    html.append(build_progress_banner())  # só aparece em B + SHOW_PROGRESS

    html.append("<form id='formMain' method='post' action='/submit' autocomplete='off' novalidate>")
    for idx, q in enumerate(QUESTION_KEYS):
        label_text = PRETTY[q]
        autofocus = "autofocus" if idx == 0 else ""
        html.append(
            "<div class='card'>"
            "<div class='q-title'><b>" + esc(label_text) + "</b></div>"
            "<input class='inp' type='text' name='" + q + "' id='" + q + "' data-idx='" + str(idx) + "' " + autofocus + " "
            "placeholder='Digite a resposta' value='' "
            "autocomplete='off' autocapitalize='off' autocorrect='off' spellcheck='false'>"
            "</div>"
        )
    html.append("<input type='hidden' name='__code' value='" + code_val + "'>")
    html.append("<input type='hidden' name='started_at' value='" + started_at_val + "'>")
    html.append("<button id='btnEnviar' class='btn' type='button'>Enviar</button></form>")
    html.append(JS_NAV_FORM)
    html.append(js_meta_widget())
    html.append(js_progress_toggle())
    html.append("</body></html>")
    return "".join(html)


# ---------------- Submit (aceita último envio mesmo após expirar) ----------------
@APP.post("/submit")
def submit():
    global CORRECT_COUNT, ERROR_COUNT, TOTAL_ERRORS, ERROR_COUNTERS

    now = datetime.now()
    ts = now.isoformat(timespec="seconds")
    code_input = (request.form.get("__code") or "").strip().upper()
    started_at = request.form.get("started_at") or ts

    # NÃO bloqueamos por tempo aqui: registro do envio em curso é garantido.
    session_expired = (SESSION_DEADLINE is not None) and (now >= SESSION_DEADLINE)

    if not CODE_REGEX.match(code_input):
        return redirect(url_for("page_code"))
    gab = get_gabarito_row_by_code(code_input)
    if gab is None:
        return redirect(url_for("page_code"))
    if is_code_used(code_input):
        return Response(
            render_code_gate("Código já utilizado. Selecione um novo código.", code_value=code_input),
            mimetype="text/html; charset=utf-8"
        )

    # Duração informativa
    try:
        started_dt = datetime.fromisoformat(started_at)
    except ValueError:
        started_dt = now
    session_seconds = int((now - started_dt).total_seconds())

    values = {q: (request.form.get(q) or "") for q in QUESTION_KEYS}

    # Verifica se TODAS as respostas batem 100% com o gabarito
    all_correct = True
    for q in QUESTION_KEYS:
        if compare_levels(values[q], gab.get(q, "")) != "correct":
            all_correct = False
            break

    if all_correct:
        CORRECT_COUNT += 1
        mark_code_as_used(code_input)

        # Se sessão expirou ou meta batida, mostra sumário (e grava resumo)
        if session_expired or (COND == "B" and META_GOAL is not None and CORRECT_COUNT >= META_GOAL):
            return Response(render_summary(), mimetype="text/html; charset=utf-8")

        msg = f"Envio correto! Código registrado. Você acertou {CORRECT_COUNT} formulário(s) até aqui. Digite o código."
        return Response(render_code_gate(msg), mimetype="text/html; charset=utf-8")

    else:
        # Formulário com erro
        ERROR_COUNT += 1

        # Registro detalhado — uma linha por pergunta com erro no CSV de erros
        with SESSION_ERR_CSV.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            for q in QUESTION_KEYS:
                gold = gab.get(q, "")
                user = values[q]
                etype = classify_error(user, gold)

                if etype != "correct":
                    TOTAL_ERRORS += 1
                    # missing cai no bucket "incorrect" se não tiver chave própria
                    if etype in ERROR_COUNTERS:
                        ERROR_COUNTERS[etype] += 1
                    else:
                        ERROR_COUNTERS["incorrect"] += 1

                    writer.writerow([
                        ts,              # data e hora do envio
                        code_input,      # código participante
                        q,               # pergunta
                        user,            # resposta escrita
                        gold,            # gabarito
                        etype,           # tipo de erro detectado
                        started_at,      # início
                        ts,              # fim
                        session_seconds  # tempo gasto
                    ])

        mark_code_as_used(code_input)

        if session_expired:
            return Response(render_summary(), mimetype="text/html; charset=utf-8")

        return Response(render_code_gate("Digite o código."), mimetype="text/html; charset=utf-8")


# ---------------- Meta (Alt+M) ----------------
@APP.post("/set_meta")
def set_meta():
    global META_GOAL
    meta = (request.form.get("meta") or "").strip()
    try:
        META_GOAL = int(meta)
    except ValueError:
        META_GOAL = None
    if META_FILE:
        META_FILE.write_text(f"{datetime.now().isoformat(timespec='seconds')} META={META_GOAL}\n", encoding="utf-8")
    return Response("ok", mimetype="text/plain")


# ---------------- Toggle Progress (Alt+P) ----------------
@APP.post("/toggle_progress")
def toggle_progress():
    global SHOW_PROGRESS
    SHOW_PROGRESS = not SHOW_PROGRESS
    estado = "ativado" if SHOW_PROGRESS else "desativado"
    return Response(f"Banner de progresso {estado}.", mimetype="text/plain")


# ---------------- Run ----------------
if __name__ == "__main__":
    APP.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)

# (imports opcionais, não usados no fluxo principal)
import threading, webbrowser, time
