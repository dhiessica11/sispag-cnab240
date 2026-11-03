#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
email_rejeitados.py — envia por email a relacao de pagamentos REJEITADOS
========================================================================

Le todas as pastas de status geradas pelo classificar.py,
extrai cada pagamento rejeitado (favorecido, valor, motivo) dos arquivos de
retorno (CNAB 240 / segmentos A e J) e envia a relacao por email (tabela + CSV).

Recursos:
    - FILTRAR_MES_ATUAL : so rejeitados com data de pagamento no mes atual em diante.
    - EVITAR_REPETIDOS  : nao reenvia o que ja foi enviado (registro em _enviados.txt).
    - DRY_RUN           : gera CSV/previa sem enviar (nunca marca como enviado).

Configuracao por variaveis de ambiente (recomendado — evita segredo no codigo):
    SMTP_HOST      (ex: smtp.gmail.com)
    SMTP_PORT      (ex: 587)
    SMTP_USER      (email que envia)
    SMTP_PASS      (senha de app)
    SMTP_STARTTLS  (1/0)
    EMAIL_FROM     (remetente; default = SMTP_USER)
    EMAIL_TO       (destinatarios separados por virgula)
    CLASSIFICADOS  (pastas _classificados separadas por ';')

Ou edite os defaults na secao CONFIG abaixo.
"""

import os
import csv
import hashlib
import json
import smtplib
import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

# ============================================================
# CONFIG (env vars tem prioridade)
# ============================================================

DRY_RUN = os.getenv("DRY_RUN", "1") == "1"        # padrao: nao envia
ENVIAR_SE_VAZIO = False
FILTRAR_MES_ATUAL = True
EVITAR_REPETIDOS = True

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.example.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "usuario@example.com")
SMTP_PASS = os.getenv("SMTP_PASS", "")            # NUNCA versionar senha real
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "1") == "1"

EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
EMAIL_TO = [e.strip() for e in os.getenv("EMAIL_TO", "destino@example.com").split(",") if e.strip()]
ASSUNTO = "Pagamentos REJEITADOS - {data}"

# pastas _classificados (uma por unidade/empresa). Ex.: "C:/dados/UNID1/_classificados;C:/dados/UNID2/_classificados"
CLASSIFICADOS = [p.strip() for p in os.getenv("CLASSIFICADOS", "").split(";") if p.strip()]

PASTA_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_log_rejeitados")

TIPOS = ("PIX", "BOLETO", "IMPOSTOS", "SALARIO")
EXTENSOES = (".ret", ".RET", ".txt", ".TXT")

# CNAB 240 — segmento A do detalhe
POS_TIPO_REGISTRO = 7
POS_SEGMENTO = 13
POS_NOME = (43, 73)
POS_VALOR = (119, 134)
POS_DATA = (93, 101)
POS_OCORR = (230, 240)

CODIGOS_REJEITADO = {"CE", "NA", "SS", "RJ", "DV", "LC", "NR", "EX", "TA", "TI"}

MOTIVOS = {
    "CE": "Confirmacao com erro / divergencia",
    "NA": "Nao autorizado",
    "SS": "Saldo insuficiente",
    "RJ": "Rejeitado / recusado",
    "DV": "Dados divergentes",
    "LC": "Lancamento cancelado",
    "NR": "Nao realizado",
    "EX": "Expirado / vencido",
    "TA": "Titulo com problema",
    "TI": "Titulo invalido",
}


# ============================================================

def descreve_motivo(cod):
    return MOTIVOS.get(cod, f"codigo {cod}")


def rotulo(base):
    partes = base.replace("/", "\\").split("\\")
    return partes[-2] if len(partes) >= 2 else base


def extrair_rejeitados(caminho):
    itens = []
    with open(caminho, "r", encoding="latin-1", errors="replace") as f:
        for linha in f:
            linha = linha.rstrip("\r\n")
            if len(linha) < POS_OCORR[1]:
                continue
            segmento = linha[POS_SEGMENTO]
            if linha[POS_TIPO_REGISTRO] != "3" or segmento not in {"A", "J"}:
                continue
            campo = linha[POS_OCORR[0]:POS_OCORR[1]]
            codigos = [campo[i:i + 2].strip() for i in range(0, 10, 2)]
            rej = [c for c in codigos if c in CODIGOS_REJEITADO]
            if not rej:
                continue
            # J: nome 62-91, data do pagamento 145-152, valor 153-167.
            # J-52 e complemento cadastral, nao um pagamento.
            if segmento == "J" and linha[17:19] == "52":
                continue
            pos_nome, pos_valor, pos_data = (
                ((61, 91), (152, 167), (144, 152)) if segmento == "J"
                else (POS_NOME, POS_VALOR, POS_DATA)
            )
            nome = linha[slice(*pos_nome)].strip()
            valor_raw = linha[slice(*pos_valor)].strip()
            valor = int(valor_raw) / 100 if valor_raw.isdigit() else 0.0
            data_raw = linha[slice(*pos_data)].strip()
            data_fmt = (f"{data_raw[0:2]}/{data_raw[2:4]}/{data_raw[4:8]}"
                        if len(data_raw) == 8 and data_raw.isdigit() else data_raw)
            try:
                data_ord = datetime.date(int(data_raw[4:8]), int(data_raw[2:4]), int(data_raw[0:2]))
            except Exception:
                data_ord = None
            itens.append({
                "favorecido": nome,
                "valor": valor,
                "motivo": ", ".join(f"{c} - {descreve_motivo(c)}" for c in sorted(set(rej))),
                "data": data_fmt,
                "_data_ord": data_ord,
                "_seq": len(itens),
                "_registro_id": hashlib.sha256(linha.encode("latin-1")).hexdigest(),
            })
    return itens


def coletar():
    if not CLASSIFICADOS:
        print("AVISO: nenhuma pasta em CLASSIFICADOS. Defina a env var CLASSIFICADOS.")
        return []
    registros = []
    for base in CLASSIFICADOS:
        emp = rotulo(base)
        pastas = [(tipo, os.path.join(base, tipo, status))
                  for tipo in TIPOS
                  for status in ("REJEITADO", "LIQUIDADO", "AGENDADO", "REVISAR")]
        pastas.append(("REVISAR", os.path.join(base, "_REVISAR")))
        for tipo, pasta in pastas:
            if not os.path.isdir(pasta):
                continue
            for nome_arq in sorted(os.listdir(pasta)):
                if not nome_arq.endswith(EXTENSOES):
                    continue
                caminho = os.path.join(pasta, nome_arq)
                if not os.path.isfile(caminho):
                    continue
                for item in extrair_rejeitados(caminho):
                    item.update(empresa=emp, tipo=tipo, arquivo=nome_arq,
                                _origem=os.path.normcase(os.path.realpath(base)))
                    registros.append(item)
    if FILTRAR_MES_ATUAL:
        hoje = datetime.date.today()
        corte = datetime.date(hoje.year, hoje.month, 1)
        antes = len(registros)
        registros = [r for r in registros if r.get("_data_ord") and r["_data_ord"] >= corte]
        print(f"Filtro mes atual (>= {corte:%d/%m/%Y}): {len(registros)} de {antes}.")
    return registros


def gerar_csv(registros, caminho):
    campos = ["empresa", "tipo", "favorecido", "valor", "motivo", "data", "arquivo"]
    with open(caminho, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for r in registros:
            linha = dict(r)
            linha["valor"] = f"{r['valor']:.2f}".replace(".", ",")
            w.writerow(linha)


def moeda(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def montar_html(regs, total):
    linhas = "".join(
        f"<tr><td>{r['empresa']}</td><td>{r['tipo']}</td><td>{r['favorecido']}</td>"
        f"<td style='text-align:right'>{moeda(r['valor'])}</td><td>{r['motivo']}</td>"
        f"<td>{r['data']}</td><td>{r['arquivo']}</td></tr>" for r in regs)
    return f"""<html><body style="font-family:Arial,sans-serif;font-size:13px;color:#222">
<p>Rela&ccedil;&atilde;o de <b>pagamentos rejeitados</b> nos retornos bancarios.</p>
<p><b>Total: {len(regs)}</b> | Valor: <b>{moeda(total)}</b></p>
<table border="1" cellspacing="0" cellpadding="5" style="border-collapse:collapse;border-color:#ccc">
<thead style="background:#333;color:#fff"><tr><th>Empresa</th><th>Tipo</th><th>Favorecido</th>
<th>Valor</th><th>Motivo</th><th>Data</th><th>Arquivo</th></tr></thead>
<tbody>{linhas}</tbody></table>
<p style="color:#888;font-size:11px">Gerado em {datetime.datetime.now():%d/%m/%Y %H:%M}. CSV em anexo.</p>
</body></html>"""


def montar_texto(regs, total):
    ls = [f"PAGAMENTOS REJEITADOS - {len(regs)} item(ns) - Total {moeda(total)}", ""]
    ls += [f"[{r['empresa']}/{r['tipo']}] {r['favorecido']} | {moeda(r['valor'])} | {r['motivo']} | {r['data']} | {r['arquivo']}" for r in regs]
    return "\n".join(ls)


def enviar_email(regs, total, caminho_csv):
    assunto = ASSUNTO.format(data=datetime.datetime.now().strftime("%d/%m/%Y"))
    msg = MIMEMultipart("mixed")
    msg["Subject"], msg["From"], msg["To"] = assunto, EMAIL_FROM, ", ".join(EMAIL_TO)
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(montar_texto(regs, total), "plain", "utf-8"))
    alt.attach(MIMEText(montar_html(regs, total), "html", "utf-8"))
    msg.attach(alt)
    with open(caminho_csv, "rb") as f:
        anexo = MIMEApplication(f.read(), _subtype="csv")
    anexo.add_header("Content-Disposition", "attachment", filename=os.path.basename(caminho_csv))
    msg.attach(anexo)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        if SMTP_STARTTLS:
            s.starttls()
        if SMTP_USER:
            s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    return assunto


# --- controle de ja-enviados ---

def _ledger():
    return os.path.join(PASTA_LOG, "_enviados.txt")


def carregar_enviados():
    p = _ledger()
    if not os.path.isfile(p):
        return set()
    with open(p, "r", encoding="utf-8") as f:
        return set(l.strip() for l in f if l.strip())


def salvar_enviados(chaves):
    with open(_ledger(), "a", encoding="utf-8") as f:
        for c in chaves:
            f.write(c + "\n")


def chave(r):
    identidade = [r["_origem"], r["tipo"], r["_registro_id"]]
    return "v2:" + hashlib.sha256(
        json.dumps(identidade, ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def main():
    os.makedirs(PASTA_LOG, exist_ok=True)
    carimbo = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    caminho_csv = os.path.join(PASTA_LOG, f"rejeitados_{carimbo}.csv")

    registros = coletar()
    if EVITAR_REPETIDOS:
        enviados = carregar_enviados()
        if any(not c.startswith("v2:") for c in enviados):
            print("AVISO: ledger antigo detectado; registros serao reavaliados com chaves v2.")
        novos = []
        vistos = set(enviados)
        for r in registros:
            identificador = chave(r)
            if identificador not in vistos:
                novos.append(r)
                vistos.add(identificador)
        print(f"Ja enviados antes: {len(registros) - len(novos)} | novos: {len(novos)}")
    else:
        novos = registros

    total = sum(r["valor"] for r in novos)
    gerar_csv(novos, caminho_csv)
    print(f"Rejeitados NOVOS: {len(novos)} | total {moeda(total)}")
    print(f"CSV: {caminho_csv}")

    if not novos and not ENVIAR_SE_VAZIO:
        print("Nada novo a enviar.")
        return
    if DRY_RUN:
        print(">>> DRY_RUN: email NAO enviado (ledger nao atualizado). Previa:")
        print(montar_texto(novos, total))
        return

    assunto = enviar_email(novos, total, caminho_csv)
    if EVITAR_REPETIDOS and novos:
        salvar_enviados(chave(r) for r in novos)
    print(f"Email enviado: {assunto} -> {', '.join(EMAIL_TO)}")


if __name__ == "__main__":
    main()

