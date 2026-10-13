"""Vaga do Remotar que já fechou no Gupy.

`fixtures/gupy_jobs.json` é o objeto `job` do `__NEXT_DATA__` de três páginas GRAVADAS DE VERDADE
em 2026-10-12, só com os campos simples (sem descrição e outros textos longos):
- `cancelada`: status `canceled`, prazos ainda no futuro;
- `encerrada`: status `closed`;
- `inscricoes_encerradas`: status `published`, mas `registerEndDate` 2026-10-05 já passou
  (a página mostra "encerrada" e o anúncio fica no ar até `expiresAt`).
Os HTML em volta do `__NEXT_DATA__` e os casos marcados são escritos à mão.
"""

import copy
import json
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from src.client.gupy import ErroGupy, GupyClient, encerramento_do_job, get_html, link_gupy
from src.client.remotar import RemotarClient

FIXTURES = Path(__file__).parent / "fixtures"
AGORA = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
JOBS = json.loads((FIXTURES / "gupy_jobs.json").read_text(encoding="utf-8"))
LINK = "https://grupotecnospeed.gupy.io/job/eyJqb2JJZCI6MTI2NDgxODQsInNvdXJjZSI6InJlbW90YXIifQ==?jobBoardSource=remotar"


def pagina(job: dict) -> str:
    # Escrito à mão: só a casca da página com o `__NEXT_DATA__`.
    dados = {"props": {"pageProps": {"job": job}}}
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(dados)}</script></body></html>'


class EncerramentoDoJobTest(unittest.TestCase):
    def test_status_fechado_ou_cancelado_fecha_agora(self):
        self.assertEqual(encerramento_do_job(JOBS["encerrada"], AGORA), AGORA)
        self.assertEqual(encerramento_do_job(JOBS["cancelada"], AGORA), AGORA)

    def test_publicada_com_inscricoes_encerradas_fecha_no_fim_do_registerEndDate(self):
        # 2026-10-05 em Brasília acaba às 03:00 UTC do dia 6.
        fim = encerramento_do_job(JOBS["inscricoes_encerradas"], AGORA)
        self.assertEqual(fim, datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc))
        self.assertLess(fim, AGORA)

    def test_publicada_com_prazos_futuros_vale_o_primeiro(self):
        job = dict(JOBS["inscricoes_encerradas"], registerEndDate="2026-10-20")  # escrito à mão
        self.assertEqual(encerramento_do_job(job, AGORA), datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc))
        job["registerEndDate"] = "2026-12-01"  # escrito à mão: o fim do anúncio (2026-11-02) vem antes
        self.assertEqual(encerramento_do_job(job, AGORA), datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc))

    def test_publicada_sem_prazo_ou_com_data_torta_nao_fecha(self):
        job = dict(JOBS["inscricoes_encerradas"], registerEndDate=None, expiresAt="não é data")  # escrito à mão
        self.assertIsNone(encerramento_do_job(job, AGORA))


class GupyClientTest(unittest.TestCase):
    def test_le_o_job_da_pagina(self):
        chamadas = []
        gupy = GupyClient(http=lambda url: chamadas.append(url) or pagina(JOBS["encerrada"]), relogio=lambda: AGORA)
        self.assertEqual(gupy.encerramento(LINK), AGORA)
        self.assertEqual(chamadas, [LINK])

    def test_pagina_que_sumiu_fecha_agora(self):
        self.assertEqual(GupyClient(http=lambda url: None, relogio=lambda: AGORA).encerramento(LINK), AGORA)

    def test_pagina_sem_next_data_ou_sem_job_e_erro(self):
        for html in ("<html>manutenção</html>", '<script id="__NEXT_DATA__">{"props": {}}</script>', pagina([])):
            with self.subTest(html=html), self.assertRaises(ErroGupy):
                GupyClient(http=lambda url: html).encerramento(LINK)

    def test_get_html_404_e_none_e_500_e_erro(self):
        def falha(codigo):
            return urllib.error.HTTPError(LINK, codigo, "x", {}, None)

        with mock.patch("urllib.request.urlopen", side_effect=falha(404)):
            self.assertIsNone(get_html(LINK))
        with mock.patch("urllib.request.urlopen", side_effect=falha(500)), self.assertRaises(ErroGupy):
            get_html(LINK)
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError()), self.assertRaises(ErroGupy):
            get_html(LINK)


class LinkGupyTest(unittest.TestCase):
    def test_aceita_so_pagina_de_vaga_no_gupy_por_https(self):
        self.assertEqual(link_gupy(LINK), LINK)
        self.assertIsNotNone(link_gupy("https://cogna.gupy.io/jobs/11985278?jobBoardSource=gupy_public_page"))
        for link in (
            LINK.replace("https://", "http://"),
            "https://grupotecnospeed.gupy.io.exemplo.com/job/abc",
            "https://gupy.io/job/abc",
            "https://grupotecnospeed.gupy.io/",
            "https://grupomoovenfornax.inhire.app/vagas/377ed750",
            None,
            123,
        ):
            with self.subTest(link=link):
                self.assertIsNone(link_gupy(link))


class RemotarComGupyTest(unittest.TestCase):
    """`remotar.json` é gravada de verdade: a 1ª vaga candidata pelo Gupy, a 2ª pelo InHire.
    O Remotar não diz nada sobre prazo nas duas."""

    def buscar(self, gupy):
        listagem = json.loads((FIXTURES / "remotar.json").read_text(encoding="utf-8"))
        return RemotarClient(http=lambda url: copy.deepcopy(listagem), relogio=lambda: AGORA, gupy=gupy).buscar("dados")

    def test_vaga_fechada_no_gupy_sai_expirada(self):
        consultados = []

        def gupy(link):
            consultados.append(link)
            return GupyClient(http=lambda url: pagina(JOBS["inscricoes_encerradas"]), relogio=lambda: AGORA).encerramento(link)

        vagas = self.buscar(gupy)
        self.assertEqual(consultados, ["https://compass.gupy.io/job/eyJqb2JJZCI6MTI3MTA4NDgsInNvdXJjZSI6InJlbW90YXIifQ==?jobBoardSource=remotar"])
        fim = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)
        self.assertEqual([v.expirada_em for v in vagas], [fim, None])
        self.assertEqual([v.tem_prazo for v in vagas], [True, None])
        self.assertEqual(vagas[0].detalhe.expirada_em, fim)

    def test_gupy_ilegivel_deixa_a_vaga_como_o_remotar_disse(self):
        def gupy(link):
            raise ErroGupy("HTTP 503")

        vagas = self.buscar(gupy)
        self.assertEqual([v.expirada_em for v in vagas], [None, None])
        self.assertEqual([v.tem_prazo for v in vagas], [None, None])

    def test_detalhar_tambem_olha_o_gupy(self):
        job = json.loads((FIXTURES / "remotar.json").read_text(encoding="utf-8"))["data"][0]
        remotar = RemotarClient(http_detalhe=lambda url: job, relogio=lambda: AGORA, gupy=lambda link: AGORA)
        self.assertEqual(remotar.detalhar("166022").expirada_em, AGORA)

    def test_a_app_liga_o_gupy_no_remotar(self):
        from src.controller.api import providers_padrao

        remotar = next(p for p in providers_padrao() if isinstance(p, RemotarClient))
        self.assertIsNotNone(remotar._gupy)


if __name__ == "__main__":
    unittest.main()
