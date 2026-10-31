#!/usr/bin/env python3
# ~/.hermes/scripts/chatwoot-historico.py
import json, re, subprocess, sys
from datetime import datetime, timezone, timedelta

MAX_MSGS = 10          # quantas mensagens anteriores incluir
MAX_CHARS = 400        # limite de caracteres por mensagem
BOT_USER_IDS = {2}     # IDs dos usuários do Chatwoot usados pelo bot (dono do API key da CLI)

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    TZ = timezone(timedelta(hours=-3))   # fallback sem tzdata

CANAIS = {
    "Channel::Whatsapp": "WhatsApp",
    "Channel::Instagram": "Instagram",
    "Channel::FacebookPage": "Facebook Messenger",
    "Channel::Telegram": "Telegram",
    "Channel::TwitterProfile": "X (Twitter)",
    "Channel::Email": "E-mail",
    "Channel::Sms": "SMS",
    "Channel::TwilioSms": "SMS",
    "Channel::WebWidget": "Chat do site",
    "Channel::Api": "API",
    "Channel::Line": "LINE",
    "Channel::Voice": "Voz",
}


def ignorar():
    print("[SILENT]")
    raise SystemExit(0)


def limpar(texto):
    """Neutraliza tags nossas e indenta continuações, para texto de terceiros
    não fechar blocos nem forjar linhas do histórico."""
    texto = re.sub(r"<\s*(/?)\s*(historico|nova_mensagem)\b", r"<\1_\2", texto or "", flags=re.I)
    return texto.replace("\r\n", "\n").replace("\n", "\n    ")


def limpar_nome(nome, padrao):
    """Nomes vão para atributos de tag: sem aspas, sem quebras de linha."""
    nome = limpar((nome or "").strip()).replace('"', "'").replace("\n", " ")
    return nome or padrao


def fmt_dt(dt):
    """Converte para America/Sao_Paulo: 2026-10-31 02:41:10 -03:00"""
    s = dt.astimezone(TZ).isoformat(sep=" ", timespec="seconds")
    return s[:19] + " " + s[19:]


def fmt_data(valor):
    """created_at pode vir como unix (int/float) ou string ISO."""
    try:
        if isinstance(valor, (int, float)):
            dt = datetime.fromtimestamp(valor, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return fmt_dt(dt)
    except Exception:
        return "data desconhecida"


def papel_msg(m):
    """Papel de uma mensagem vinda da CLI (message_type é inteiro)."""
    if m.get("private"):
        return "nota interna"
    return "contato" if m.get("message_type") == 0 else "atendimento"


def fmt_autor(m):
    papel = papel_msg(m)
    padrao = {"contato": "Contato", "atendimento": "Atendente", "nota interna": "Equipe"}[papel]
    nome = limpar_nome((m.get("sender") or {}).get("name"), padrao)
    return f"{nome} ({papel})"


def pessoa(d, nome_padrao):
    d = d or {}
    return {
        "id": str(d["id"]) if d.get("id") is not None else "[SEM_ID]",
        "name": limpar_nome(d.get("name"), nome_padrao),
    }

def visibilidade(m):
    """Marcador explícito de visibilidade para cada mensagem do histórico."""
    return "[NOTA INTERNA]" if m.get("private") else "[MENSAGEM]"

def canal_legivel(tipo):
    tipo = (tipo or "").strip()
    return CANAIS.get(tipo) or (tipo.split("::")[-1] or "desconhecido")


payload = json.load(sys.stdin)

# 1) quais eventos acordam o agente
if payload.get("event") != "message_created":
    ignorar()

privada = bool(payload.get("private"))
entrada = payload.get("message_type") in ("incoming", 0)
msg_sender = payload.get("sender") or {}

if privada:
    # nota interna: ignora as do próprio bot (evita loop)
    if msg_sender.get("id") in BOT_USER_IDS:
        ignorar()
elif not entrada:
    # resposta pública de atendente/bot: não aciona o agente
    ignorar()

if not (payload.get("content") or "").strip():
    ignorar()

conv = payload.get("conversation") or {}
conv_id = conv.get("id")
account_id = (payload.get("account") or {}).get("id")
if not conv_id:
    ignorar()

# 2) o contato é sempre o mesmo, independente de quem enviou a mensagem
contato_raw = (conv.get("meta") or {}).get("sender") or {}
if not contato_raw.get("id") and entrada and not privada:
    contato_raw = msg_sender

# 3) histórico via CLI do Chatwoot
cmd = ["chatwoot", "conv", str(conv_id), "messages", "-o", "json"]
if account_id:
    cmd += ["-a", str(account_id)]

linhas = []
try:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=True)
    msgs = json.loads(r.stdout).get("payload", [])
    atual = payload.get("id")
    msgs.sort(key=lambda m: m.get("created_at") or 0)
    for m in msgs:
        if m.get("id") == atual:
            continue                                   # não duplicar a atual
        if m.get("message_type") not in (0, 1):
            continue                                   # atividades e templates
        if (m.get("content_attributes") or {}).get("deleted"):
            continue                                   # mensagens apagadas
        texto = (m.get("content") or "").strip()
        if not texto:
            continue
        linhas.append(
            f"[{fmt_data(m.get('created_at'))}] {visibilidade(m)} "
            f"{fmt_autor(m)}: {limpar(texto[:MAX_CHARS])}"
        )
    linhas = linhas[-MAX_MSGS:]
except Exception as e:
    linhas = [f"(histórico indisponível: {type(e).__name__})"]

# 4) payload enxuto: só o que o prompt usa
autor = pessoa(msg_sender, "Contato" if not privada else "Equipe")

inbox_raw = payload.get("inbox") or {}
inbox = {
    "id": str(inbox_raw["id"]) if inbox_raw.get("id") is not None
          else str(conv.get("inbox_id") or "[SEM_ID]"),
    "name": limpar_nome(inbox_raw.get("name"), "desconhecida"),
    "canal": canal_legivel(conv.get("channel")),
}

saida = {
    "event": payload.get("event"),
    "event_type": payload.get("event_type") or payload.get("event"),
    "id": payload.get("id"),
    "content": limpar(payload.get("content") or ""),
    "conversation": {"id": conv_id},
    "conversation_id": conv_id,
    "account": {"id": account_id},
    "contato": pessoa(contato_raw, "Contato"),   # sempre o contato da conversa
    "sender": autor,                              # quem escreveu esta mensagem
    "autor": autor,
    "inbox": inbox,
    "mensagem_privada": "sim" if privada else "não",
    "tipo_mensagem": "nota interna (invisível ao contato)" if privada else "mensagem do contato",
    "mensagem_em": fmt_data(payload.get("created_at")),
    "agora": fmt_dt(datetime.now(timezone.utc)),
    "historico": "\n".join(linhas) or "(primeira mensagem da conversa)",
}
print(json.dumps(saida, ensure_ascii=False))