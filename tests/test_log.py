import io
import logging
import os
import unittest
from unittest import mock

from src.controller.api import configurar_log


class TestConfigurarLog(unittest.TestCase):
    def setUp(self):
        self.src = logging.getLogger("src")
        self.antes = (self.src.level, list(self.src.handlers), self.src.propagate)
        self.src.handlers = []
        self.src.setLevel(logging.NOTSET)
        self.src.propagate = True
        self.saida = io.StringIO()
        self.addCleanup(self._restaurar)

    def _restaurar(self):
        self.src.level, self.src.handlers, self.src.propagate = self.antes[0], self.antes[1], self.antes[2]

    def _configurar(self, **env):
        with mock.patch.dict(os.environ, env), mock.patch("sys.stderr", self.saida):
            configurar_log()

    def test_info_da_app_aparece_uma_vez(self):
        self._configurar()
        self._configurar()  # de novo: não duplica o handler
        logging.getLogger("src.service.agendador_coleta").info("coleta agendada ligada: 08:00")
        self.assertEqual(self.saida.getvalue().count("coleta agendada ligada: 08:00"), 1)
        self.assertIn("INFO: src.service.agendador_coleta:", self.saida.getvalue())
        # Só o `src`: o httpx em INFO logaria a URL do Apify, com o token.
        self.assertFalse(logging.getLogger("httpx").isEnabledFor(logging.INFO))

    def test_log_nivel_vale_e_invalido_cai_em_info(self):
        self._configurar(LOG_NIVEL="warning")
        self.assertEqual(self.src.level, logging.WARNING)
        self._configurar(LOG_NIVEL="barulhento")
        self.assertEqual(self.src.level, logging.INFO)


if __name__ == "__main__":
    unittest.main()
