# Correções de retornos CNAB 240

- Ocorrências de rejeição têm prioridade sobre liquidação na classificação do arquivo.
- A coleta procura rejeições em todos os status e em `_REVISAR`, alcançando também arquivos antigos com resultados mistos.
- O extrator lê segmentos A e J com posições próprias. No J, usa nome nas posições 62–91, data do pagamento em 145–152 e valor em 153–167 (posições contadas a partir de 1). Complementos J-52 não são pagamentos e são ignorados.
- Destinos são reservados com criação exclusiva e recebem sufixos numéricos quando necessário. A origem só é removida após a cópia terminar; falhas na cópia removem a reserva e preservam a origem.
- A chave v2 de deduplicação combina caminho canônico da empresa, tipo de pagamento e SHA-256 do registro CNAB completo. Renomear o arquivo não gera uma nova chave. Registros repetidos no mesmo envio também são eliminados.

## Atualização do controle de envios

Preserve `_log_rejeitados/_enviados.txt` para auditoria. As chaves antigas continuam no arquivo, mas não bloqueiam as chaves v2: um pagamento já comunicado pelo código antigo pode ser enviado novamente uma vez. Não é possível converter automaticamente as chaves antigas sem uma associação confiável aos registros originais.

Execute primeiro com `DRY_RUN=1` e revise o CSV. Nesse modo, o controle de envios não é atualizado. A primeira execução efetiva registra as chaves v2 após o envio. Mover as pastas das empresas altera sua identidade de origem e também exige revisão antes do envio.

O filtro existente de data continua considerando apenas o mês atual em diante. Os códigos de ocorrência continuam configuráveis conforme o manual do banco. Os testes usam dados sintéticos; valide um retorno real anonimizado do seu contrato antes de usar em produção.

## Verificação

```bash
python -m unittest -v
```

Os testes não acessam SMTP: o envio é substituído por um mock.
