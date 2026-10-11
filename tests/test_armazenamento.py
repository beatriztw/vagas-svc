import io
import tempfile
import unittest
from pathlib import Path

from src.client.armazenamento import Armazenamento, criar_cliente_s3, ler_segredo


class ClienteS3Falso:
    def __init__(self):
        self.objetos = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objetos[(Bucket, Key)] = (Body, ContentType)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objetos[(Bucket, Key)][0])}


class ArmazenamentoTest(unittest.TestCase):
    def test_envia_e_baixa_no_bucket_do_env(self):
        cliente = ClienteS3Falso()
        armazenamento = Armazenamento(cliente=cliente, env={"S3_BUCKET": "outro"})

        chave = armazenamento.enviar("originais/cv.pdf", b"%PDF", "application/pdf")

        self.assertEqual(chave, "originais/cv.pdf")
        self.assertEqual(cliente.objetos[("outro", "originais/cv.pdf")], (b"%PDF", "application/pdf"))
        self.assertEqual(armazenamento.baixar(chave), b"%PDF")

    def test_bucket_padrao_e_vagas(self):
        self.assertEqual(Armazenamento(cliente=ClienteS3Falso(), env={}).bucket, "vagas")

    def test_segredo_do_env_ou_do_arquivo(self):
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "segredo"
            arquivo.write_text("do-arquivo\n", encoding="utf-8")

            self.assertEqual(ler_segredo({"S3_SECRET_ACCESS_KEY_FILE": str(arquivo)}, "S3_SECRET_ACCESS_KEY"), "do-arquivo")
            self.assertEqual(
                ler_segredo({"S3_SECRET_ACCESS_KEY": "do-env", "S3_SECRET_ACCESS_KEY_FILE": str(arquivo)}, "S3_SECRET_ACCESS_KEY"),
                "do-env",
            )
            self.assertIsNone(ler_segredo({}, "S3_SECRET_ACCESS_KEY"))

    def test_cliente_boto3_usa_endpoint_regiao_e_path_style(self):
        cliente = criar_cliente_s3(
            {"S3_ENDPOINT": "http://garage:3900", "S3_ACCESS_KEY_ID": "id", "S3_SECRET_ACCESS_KEY": "segredo"}
        )

        self.assertEqual(cliente.meta.endpoint_url, "http://garage:3900")
        self.assertEqual(cliente.meta.region_name, "garage")
        self.assertEqual(cliente.meta.config.s3["addressing_style"], "path")
        credenciais = cliente._request_signer._credentials
        self.assertEqual((credenciais.access_key, credenciais.secret_key), ("id", "segredo"))
