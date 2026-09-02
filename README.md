# Classificador de Retornos Bancários (CNAB 240 / SISPAG)

Ferramenta em Python para automatizar o processamento de arquivos de **RETORNO**
bancário no padrão **CNAB 240** (testado com SISPAG do Itaú). Classifica cada
arquivo por tipo e status de pagamento e envia por email a relação de pagamentos
rejeitados.

Projeto sem dependências externas — usa apenas a biblioteca padrão do Python 3.

## O que faz

**1. Classificação (`classificar.py`)**

Lê os arquivos de retorno de uma pasta e move cada um para
`_classificados/<TIPO>/<STATUS>/`, onde:

- **TIPO**: `PIX`, `BOLETO`, `IMPOSTOS`, `SALARIO`
- **STATUS**: `LIQUIDADO`, `AGENDADO`, `REJEITADO`, `REVISAR`

Regras de negócio embutidas (ajustáveis no código):

- PIX QR Code (forma `47`) é tratado como `IMPOSTOS`.
- Quando um retorno traz **chave PIX (45) + QR Code (47)** liquidados, o original
  vai para `PIX/LIQUIDADO` e uma **cópia** é gerada em `IMPOSTOS/LIQUIDADO`.
- Cada execução gera um CSV de auditoria em `_classificados/_log/`.

**2. Relação de rejeitados por email (`email_rejeitados.py`)**

Varre as pastas `REJEITADO`, extrai cada pagamento recusado (favorecido, valor,
motivo) e envia um email com tabela HTML + CSV anexo.

- `FILTRAR_MES_ATUAL`: só considera pagamentos do mês corrente em diante.
- `EVITAR_REPETIDOS`: nunca reenvia um rejeitado já enviado (registro em
  `_log_rejeitados/_enviados.txt`).
- `DRY_RUN`: gera a prévia sem enviar nada.

## Uso

```bash
# classificar (simulação)
python classificar.py "C:/dados/UNIDADE1/RETORNO" --dry-run

# classificar de verdade (move os arquivos)
python classificar.py "C:/dados/UNIDADE1/RETORNO"

# enviar relação de rejeitados
python email_rejeitados.py
```

## Configuração

O `email_rejeitados.py` lê a configuração de **variáveis de ambiente** (ou de um
arquivo `.env` que você carrega no shell). Copie `.env.example` para `.env` e
preencha:

| Variável        | Descrição                                            |
|-----------------|------------------------------------------------------|
| `SMTP_HOST`     | Servidor SMTP (ex: `smtp.gmail.com`)                 |
| `SMTP_PORT`     | Porta (ex: `587`)                                    |
| `SMTP_USER`     | Conta que envia                                      |
| `SMTP_PASS`     | Senha de app (para Gmail/Workspace)                  |
| `SMTP_STARTTLS` | `1` para STARTTLS (porta 587)                        |
| `EMAIL_FROM`    | Remetente (default = `SMTP_USER`)                    |
| `EMAIL_TO`      | Destinatários separados por vírgula                  |
| `CLASSIFICADOS` | Pastas `_classificados` separadas por `;`            |
| `DRY_RUN`       | `1` = só simula, `0` = envia                         |

> **Segurança:** nunca versione o `.env` nem a senha real. O `.gitignore` já
> exclui `.env`, os logs e os dados classificados.

## Automação (Windows)

Um `.bat` chamando `python classificar.py <pasta>` para cada pasta, seguido de
`python email_rejeitados.py`, pode ser agendado no **Agendador de Tarefas** para
rodar sozinho (ex.: a cada 2 horas no horário comercial).

## Formato CNAB 240

O parser usa posições fixas do layout CNAB 240 (header de lote para tipo/forma de
pagamento; segmento A do detalhe para nome do favorecido, valor, data e códigos de
ocorrência). Os códigos de status e forma de pagamento podem variar por
banco/conta — ajuste os conjuntos no topo de cada script conforme o seu manual.

## Licença

MIT — veja [LICENSE](LICENSE).
