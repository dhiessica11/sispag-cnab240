#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
classificar.py — classificador de arquivos de RETORNO bancario (CNAB 240 / SISPAG Itau)
=======================================================================================

Separa arquivos de retorno em duas dimensoes e organiza em subpastas:

  TIPO   (header de lote): PIX / BOLETO / IMPOSTOS / SALARIO
  STATUS (detalhe seg. A): LIQUIDADO / AGENDADO / REJEITADO / REVISAR

Destino: <entrada>/_classificados/<TIPO>/<STATUS>/arquivo.ret

Uso:
    python classificar.py "C:/caminho/da/pasta/RETORNO"
    python classificar.py ./retornos --dry-run
    python classificar.py ./retornos --copiar        # nao move o original

Seguranca:
    - --dry-run: apenas simula, nao move nada.
    - --copiar : copia em vez de mover (original permanece).
    - Nunca apaga arquivos.

Este projeto e um exemplo generico. Ajuste os codigos de TIPO/STATUS conforme o
layout da sua conta/banco.
"""

import os
import csv
import shutil
import argparse
import datetime
from pathlib import Path

# ============================================================
# REGRAS DE CLASSIFICACAO
# ============================================================

TIPOS = ("PIX", "BOLETO", "IMPOSTOS", "SALARIO")
STATUS = ("LIQUIDADO", "AGENDADO", "REJEITADO", "REVISAR")
EXTENSOES = (".ret", ".RET", ".txt", ".TXT")

# Posicoes CNAB 240 (0-based)
POS_TIPO_REGISTRO = 7
POS_TIPO_PGTO = (9, 11)
POS_FORMA_PGTO = (11, 13)
POS_SEGMENTO = 13
POS_OCORR = (230, 240)
SEGMENTOS_PAGAMENTO = {"A", "J"}

FORMAS_IMPOSTOS = {"16", "17", "18", "19", "22", "91"}

CODIGOS_LIQUIDADO = {"00", "CP", "FC", "FD", "PD"}
CODIGOS_AGENDADO = {"BD", "BE", "AE", "LA", "IR", "EM"}
CODIGOS_REJEITADO = {"CE", "NA", "SS", "RJ", "DV", "LC", "NR", "EX", "TA", "TI"}


def categoria_tipo(tipo_pgto, forma_pgto):
    if tipo_pgto == "30":
        return "SALARIO"
    if tipo_pgto == "22" or forma_pgto in FORMAS_IMPOSTOS:
        return "IMPOSTOS"
    if forma_pgto == "45":
        return "PIX"
    if forma_pgto == "47":               # PIX QR Code
        return "IMPOSTOS"
    if forma_pgto in {"30", "31"}:
        return "BOLETO"
    return None


def categoria_status(ocorrencias):
    if not ocorrencias:
        return "REVISAR", "nenhuma ocorrencia lida"
    rej = sorted(set(c for c in ocorrencias if c in CODIGOS_REJEITADO))
    if rej:
        return "REJEITADO", f"rejeicao: {rej}"
    if any(c in CODIGOS_LIQUIDADO for c in ocorrencias):
        outros = sorted(set(c for c in ocorrencias if c not in CODIGOS_LIQUIDADO))
        if outros:
            return "LIQUIDADO", f"liquidado presente (tambem ha: {outros})"
        return "LIQUIDADO", f"todos efetuados: {sorted(set(ocorrencias))}"
    age = sorted(set(c for c in ocorrencias if c in CODIGOS_AGENDADO))
    if age:
        return "AGENDADO", f"agendado: {age}"
    return "REVISAR", f"codigo nao mapeado: {sorted(set(ocorrencias))}"


def classificar(pares, ocorrencias):
    """Retorna (tipo, status, motivo_tipo, motivo_status, copia_extra)."""
    status, motivo_s = categoria_status(ocorrencias)
    if not pares:
        return None, status, "nenhum header de lote", motivo_s, None

    cats_raw = [(categoria_tipo(t, f), t, f) for (t, f) in pares]
    cats = {c for (c, _, _) in cats_raw if c is not None}
    det = ", ".join(f"{t}/{f}" for t, f in pares)

    if len(cats) == 1:
        return cats.pop(), status, f"tipo/forma: {det}", motivo_s, None

    formas = {f for (_, _, f) in cats_raw}
    # chave PIX (45) + QR Code (47) liquidado -> PIX/LIQUIDADO + COPIA em IMPOSTOS/LIQUIDADO
    if "45" in formas and "47" in formas and status == "LIQUIDADO":
        return "PIX", "LIQUIDADO", f"chave PIX + QR Code (copia p/ IMPOSTOS): {det}", motivo_s, ("IMPOSTOS", "LIQUIDADO")
    # outro misto com 47 liquidado -> IMPOSTOS/LIQUIDADO
    if "47" in formas and status == "LIQUIDADO":
        return "IMPOSTOS", "LIQUIDADO", f"misto com QR Code liquidado: {det}", motivo_s, None

    primeiro = next((c for (c, _, _) in cats_raw if c is not None), None)
    return primeiro, "REVISAR", f"lotes mistos {sorted(cats)}: {det}", motivo_s, None


# ============================================================
# LEITURA / MOVIMENTACAO
# ============================================================

def ler_arquivo(caminho):
    pares, ocorrencias = [], []
    with open(caminho, "r", encoding="latin-1", errors="replace") as f:
        for linha in f:
            linha = linha.rstrip("\r\n")
            if len(linha) <= POS_TIPO_REGISTRO:
                continue
            tipo_reg = linha[POS_TIPO_REGISTRO]
            if tipo_reg == "1" and len(linha) >= POS_FORMA_PGTO[1]:
                t = linha[POS_TIPO_PGTO[0]:POS_TIPO_PGTO[1]].strip()
                fo = linha[POS_FORMA_PGTO[0]:POS_FORMA_PGTO[1]].strip()
                if t or fo:
                    pares.append((t, fo))
            elif (tipo_reg == "3" and len(linha) >= POS_OCORR[1]
                  and linha[POS_SEGMENTO] in SEGMENTOS_PAGAMENTO):
                campo = linha[POS_OCORR[0]:POS_OCORR[1]]
                for i in range(0, 10, 2):
                    cod = campo[i:i + 2].strip()
                    if cod:
                        ocorrencias.append(cod)
    return pares, ocorrencias


def garantir_pastas(base_saida, revisar_raiz, pasta_log):
    for t in TIPOS:
        for s in STATUS:
            Path(os.path.join(base_saida, t, s)).mkdir(parents=True, exist_ok=True)
    Path(revisar_raiz).mkdir(parents=True, exist_ok=True)
    Path(pasta_log).mkdir(parents=True, exist_ok=True)


def _destino_unico(pasta, origem):
    """Reserva atomicamente um destino; o chamador deve preencher ou remover."""
    nome, ext = os.path.splitext(os.path.basename(origem))
    tentativa = 0
    while True:
        sufixo = f"_{tentativa}" if tentativa else ""
        destino = os.path.join(pasta, f"{nome}{sufixo}{ext}")
        try:
            fd = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            tentativa += 1
            continue
        os.close(fd)
        return destino


def _transferir_seguro(origem, pasta, copiar):
    destino = _destino_unico(pasta, origem)
    try:
        shutil.copy2(origem, destino)
    except BaseException:
        os.unlink(destino)
        raise
    # So remove a origem depois de concluir a copia. Se a remocao falhar,
    # preserva ambas as copias para nao perder o retorno.
    if not copiar:
        os.unlink(origem)
    return destino


def transferir(origem, tipo, status, cfg):
    pasta = cfg["revisar_raiz"] if tipo is None else os.path.join(cfg["base_saida"], tipo, status)
    if cfg["dry_run"]:
        return os.path.join(pasta, os.path.basename(origem))
    return _transferir_seguro(origem, pasta, cfg["copiar"])


def transferir_copia(origem, tipo, status, cfg):
    pasta = os.path.join(cfg["base_saida"], tipo, status)
    if cfg["dry_run"]:
        return os.path.join(pasta, os.path.basename(origem))
    return _transferir_seguro(origem, pasta, True)


# ============================================================
# EXECUCAO
# ============================================================

def processar(entrada, dry_run=False, copiar=False):
    if not os.path.isdir(entrada):
        print(f"ERRO: pasta de entrada nao existe: {entrada}")
        return

    base_saida = os.path.join(entrada, "_classificados")
    cfg = {
        "base_saida": base_saida,
        "revisar_raiz": os.path.join(base_saida, "_REVISAR"),
        "pasta_log": os.path.join(base_saida, "_log"),
        "dry_run": dry_run,
        "copiar": copiar,
    }
    garantir_pastas(cfg["base_saida"], cfg["revisar_raiz"], cfg["pasta_log"])

    agora = datetime.datetime.now()
    carimbo = agora.strftime("%Y%m%d_%H%M%S")
    caminho_csv = os.path.join(cfg["pasta_log"], f"tipo_status_{carimbo}.csv")

    modo = "COPIA" if copiar else "MOVE"
    print(f"Inicio {agora:%d/%m/%Y %H:%M:%S} | dry_run={dry_run} | modo={modo}")
    print(f"Entrada: {entrada}")

    arquivos = sorted(f for f in os.listdir(entrada)
                      if f.endswith(EXTENSOES)
                      and os.path.isfile(os.path.join(entrada, f)))
    if not arquivos:
        print("Nenhum arquivo de retorno encontrado.")

    contagem, linhas_csv = {}, []
    for nome in arquivos:
        origem = os.path.join(entrada, nome)
        try:
            pares, ocorr = ler_arquivo(origem)
            tipo, status, mt, ms, copia = classificar(pares, ocorr)
        except Exception as e:
            tipo, status, mt, ms, copia, pares, ocorr = None, "REVISAR", f"erro: {e}", "", None, [], []

        destino = transferir(origem, tipo, status, cfg)
        chave = f"{tipo or '_REVISAR'}/{status}"
        contagem[chave] = contagem.get(chave, 0) + 1
        acao = "[SIMULADO]" if dry_run else f"[{modo}]"
        print(f"{acao} {nome} -> {chave}  ({mt} | {ms})")

        copia_destino = ""
        if copia is not None:
            fonte = destino if not dry_run else origem
            copia_destino = transferir_copia(fonte, copia[0], copia[1], cfg)
            chave_c = f"{copia[0]}/{copia[1]}"
            contagem[chave_c] = contagem.get(chave_c, 0) + 1
            print(f"           [COPIA] +copia -> {chave_c}")

        linhas_csv.append({
            "arquivo": nome, "tipo": tipo or "_REVISAR", "status": status,
            "motivo_tipo": mt, "motivo_status": ms,
            "lotes": ";".join(f"{t}/{f}" for t, f in pares),
            "ocorrencias": ";".join(ocorr),
            "destino": destino, "copia": copia_destino,
        })

    print("-" * 60)
    for k in sorted(contagem):
        print(f"  {k}: {contagem[k]}")

    with open(caminho_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["arquivo", "tipo", "status", "motivo_tipo",
                                          "motivo_status", "lotes", "ocorrencias",
                                          "destino", "copia"])
        w.writeheader()
        w.writerows(linhas_csv)
    print(f"CSV: {caminho_csv}")


def main():
    ap = argparse.ArgumentParser(description="Classifica arquivos de RETORNO CNAB 240 por TIPO e STATUS.")
    ap.add_argument("entrada", help="pasta com os arquivos de retorno (.ret/.txt)")
    ap.add_argument("--dry-run", action="store_true", help="apenas simula, nao move nada")
    ap.add_argument("--copiar", action="store_true", help="copia em vez de mover (original permanece)")
    args = ap.parse_args()
    processar(args.entrada, dry_run=args.dry_run, copiar=args.copiar)


if __name__ == "__main__":
    main()

