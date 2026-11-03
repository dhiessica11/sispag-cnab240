import contextlib
import datetime
import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import classificar as c
import email_rejeitados as e


def detalhe(segmento='A', codigo='RJ', nome='FAVORECIDO', valor=12345, sequencia=1):
    linha = list(' ' * 240)
    linha[0:3] = '341'
    linha[3:7] = '0001'
    linha[7] = '3'
    linha[8:13] = f'{sequencia:05}'
    linha[13] = segmento
    if segmento == 'J':
        campos = [(61, 91, nome), (144, 152, datetime.date.today().strftime('%d%m%Y')),
                  (152, 167, f'{valor:015}')]
    else:
        campos = [(43, 73, nome), (93, 101, datetime.date.today().strftime('%d%m%Y')),
                  (119, 134, f'{valor:015}')]
    for inicio, fim, texto in campos:
        linha[inicio:fim] = texto.ljust(fim-inicio)
    linha[230:232] = codigo
    return ''.join(linha) + '\n'


class RetornosTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def arquivo(self, pasta, texto, nome='retorno.ret'):
        pasta.mkdir(parents=True, exist_ok=True)
        p = pasta / nome
        p.write_text(texto, encoding='latin-1')
        return p

    def test_resultado_misto_prioriza_rejeicao(self):
        self.assertEqual(c.categoria_status(['00', 'RJ'])[0], 'REJEITADO')
        self.assertEqual(c.categoria_status(['00'])[0], 'LIQUIDADO')

    def test_pipeline_misto(self):
        header = list(' ' * 240)
        header[7] = '1'
        header[9:11] = '20'
        header[11:13] = '45'
        self.arquivo(self.root, ''.join(header)+'\n'+detalhe(codigo='00')+detalhe())
        with contextlib.redirect_stdout(io.StringIO()):
            c.processar(str(self.root))
            with patch.object(e, 'CLASSIFICADOS', [str(self.root/'_classificados')]):
                regs = e.coletar()
        self.assertEqual(len(regs), 1)
        self.assertTrue((self.root/'_classificados/PIX/REJEITADO/retorno.ret').exists())

    def test_coleta_historico_fora_da_pasta_rejeitado(self):
        for status in ('LIQUIDADO', 'AGENDADO', 'REVISAR'):
            self.arquivo(self.root/'PIX'/status, detalhe())
        with patch.object(e, 'CLASSIFICADOS', [str(self.root)]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(len(e.coletar()), 3)

    def test_segmentos_a_e_j_usam_posicoes_proprias(self):
        p = self.arquivo(self.root, detalhe(nome='PIX', valor=700)+detalhe('J', nome='BOLETO', valor=98765))
        regs = e.extrair_rejeitados(p)
        self.assertEqual([(r['favorecido'], r['valor']) for r in regs], [('PIX', 7), ('BOLETO', 987.65)])
        self.assertTrue(all(r['_data_ord'] == datetime.date.today() for r in regs))

    def test_j52_nao_e_pagamento(self):
        linha = list(detalhe('J'))
        linha[17:19] = '52'
        p = self.arquivo(self.root, ''.join(linha))
        self.assertEqual(e.extrair_rejeitados(p), [])

    def test_copias_concorrentes_preservam_arquivos(self):
        origem = self.arquivo(self.root/'entrada', 'retorno original')
        destino = self.root/'destino'
        destino.mkdir()
        self.arquivo(destino, 'arquivo anterior')
        with ThreadPoolExecutor(max_workers=8) as pool:
            caminhos = list(pool.map(lambda _: c._transferir_seguro(str(origem), str(destino), True), range(20)))
        self.assertEqual(len(set(caminhos)), 20)
        self.assertEqual((destino/'retorno.ret').read_text(), 'arquivo anterior')
        self.assertTrue(all(Path(p).read_text() == 'retorno original' for p in caminhos))

    def test_falha_na_copia_preserva_origem_e_limpa_reserva(self):
        origem = self.arquivo(self.root/'entrada', 'original')
        destino = self.root/'destino'
        destino.mkdir()
        with patch.object(c.shutil, 'copy2', side_effect=OSError('falha')):
            with self.assertRaises(OSError):
                c._transferir_seguro(str(origem), str(destino), False)
        self.assertTrue(origem.exists())
        self.assertEqual(list(destino.iterdir()), [])

    def test_move_preserva_conteudo(self):
        origem = self.arquivo(self.root/'entrada', 'original')
        destino = self.root/'destino'
        destino.mkdir()
        p = c._transferir_seguro(str(origem), str(destino), False)
        self.assertFalse(origem.exists())
        self.assertEqual(Path(p).read_text(), 'original')

    def test_chaves_distinguem_registros_e_independem_do_nome(self):
        p = self.arquivo(self.root, detalhe()+detalhe(nome='OUTRO', sequencia=2))
        regs = e.extrair_rejeitados(p)
        for r in regs:
            r.update(_origem='/empresa', tipo='PIX', arquivo='retorno.ret')
        self.assertNotEqual(e.chave(regs[0]), e.chave(regs[1]))
        self.assertNotEqual(e.chave(regs[0]), e.chave(dict(regs[0], tipo='SALARIO')))
        self.assertNotEqual(e.chave(regs[0]), e.chave(dict(regs[0], _origem='/outra')))
        self.assertEqual(e.chave(regs[0]), e.chave(dict(regs[0], arquivo='retorno_1.ret')))

    def test_deduplicacao_no_mesmo_envio_e_no_proximo(self):
        self.arquivo(self.root/'PIX/REJEITADO', detalhe())
        self.arquivo(self.root/'PIX/LIQUIDADO', detalhe(), 'copia.ret')
        with patch.object(e, 'CLASSIFICADOS', [str(self.root)]), patch.object(e, 'PASTA_LOG', str(self.root/'logs')), patch.object(e, 'DRY_RUN', False), patch.object(e, 'enviar_email', return_value='teste') as enviar, contextlib.redirect_stdout(io.StringIO()):
            e.main()
            self.assertEqual(len(enviar.call_args.args[0]), 1)
            e.main()
            self.assertEqual(enviar.call_count, 1)


if __name__ == '__main__':
    unittest.main()
