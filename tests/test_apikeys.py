import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from juris.apikeys import criar_chave, exigir_chave, hash_da_chave


async def test_chave_criada_nao_fica_em_claro_no_banco(session):
    chave, registro = await criar_chave(session, nome="squad", escopo="publico")
    assert chave.startswith("juris_")
    guardado = await session.scalar(
        text("SELECT key_hash FROM api_keys WHERE nome = 'squad'")
    )
    assert guardado == hash_da_chave(chave)
    assert chave not in guardado


@pytest.fixture
def app_de_teste(session):
    """App mínimo com uma rota pública e uma que exige escopo interno.

    A rota interna existe só aqui: nenhum endpoint de produção declara
    'interno' antes da onda 5, mas o mecanismo precisa ser testado.
    """
    from juris.api.deps import get_session

    app = FastAPI()

    @app.get("/publico", dependencies=[Depends(exigir_chave("publico"))])
    async def _publico():
        return {"ok": True}

    @app.get("/interno", dependencies=[Depends(exigir_chave("interno"))])
    async def _interno():
        return {"ok": True}

    async def _session_de_teste():
        yield session

    app.dependency_overrides[get_session] = _session_de_teste
    return app


async def _get(app, caminho: str, chave: str | None = None):
    headers = {"Authorization": f"Bearer {chave}"} if chave else {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://teste") as client:
        return await client.get(caminho, headers=headers)


async def test_sem_chave_recusa(session, app_de_teste):
    assert (await _get(app_de_teste, "/publico")).status_code == 401


async def test_chave_invalida_recusa_com_a_mesma_mensagem(session, app_de_teste):
    """401 genérico nos dois casos: distinguir facilitaria enumeração."""
    sem = await _get(app_de_teste, "/publico")
    errada = await _get(app_de_teste, "/publico", chave="juris_naoexiste")
    assert errada.status_code == 401
    assert errada.json() == sem.json()


async def test_chave_valida_aceita(session, app_de_teste):
    chave, _ = await criar_chave(session, nome="squad", escopo="publico")
    assert (await _get(app_de_teste, "/publico", chave=chave)).status_code == 200


async def test_chave_inativa_recusa(session, app_de_teste):
    """Mesmo corpo do 401 de chave ausente/inválida — ver o teste acima.

    A propriedade de mensagem idêntica precisa ficar fixada por asserção, não
    por identidade de objeto: cada recusa agora ergue uma HTTPException nova
    (ver `_recusa()` em apikeys.py), então só o conteúdo comparável importa.
    """
    chave, _ = await criar_chave(session, nome="squad", escopo="publico")
    await session.execute(text("UPDATE api_keys SET ativa = false WHERE nome = 'squad'"))
    sem = await _get(app_de_teste, "/publico")
    resposta = await _get(app_de_teste, "/publico", chave=chave)
    assert resposta.status_code == 401
    assert resposta.json() == sem.json()


async def test_escopo_insuficiente_recusa(session, app_de_teste):
    chave, _ = await criar_chave(session, nome="squad", escopo="publico")
    assert (await _get(app_de_teste, "/interno", chave=chave)).status_code == 403


async def test_escopo_interno_alcanca_o_publico(session, app_de_teste):
    chave, _ = await criar_chave(session, nome="escritorio", escopo="interno")
    assert (await _get(app_de_teste, "/publico", chave=chave)).status_code == 200


async def test_limite_diario_excedido_devolve_429(session, app_de_teste):
    chave, registro = await criar_chave(
        session, nome="squad", escopo="publico", limite_diario=1
    )
    assert (await _get(app_de_teste, "/publico", chave=chave)).status_code == 200
    assert (await _get(app_de_teste, "/publico", chave=chave)).status_code == 429


async def test_health_anonimo_nao_revela_o_acervo(session):
    from juris.api.deps import get_session
    from juris.api.main import app

    async def _session_de_teste():
        yield session

    app.dependency_overrides[get_session] = _session_de_teste
    try:
        resposta = await _get(app, "/v1/health")
        assert resposta.status_code == 200
        assert resposta.json() == {"status": "ok"}
    finally:
        app.dependency_overrides.clear()


async def test_health_com_chave_revogada_degrada_para_o_anonimo(session):
    """Chave revogada em /v1/health não deve virar erro nem vazar o acervo:
    degrada exatamente para o mesmo corpo do chamador anônimo."""
    from juris.api.deps import get_session
    from juris.api.main import app

    chave, _ = await criar_chave(session, nome="squad", escopo="publico")
    await session.execute(text("UPDATE api_keys SET ativa = false WHERE nome = 'squad'"))

    async def _session_de_teste():
        yield session

    app.dependency_overrides[get_session] = _session_de_teste
    try:
        resposta = await _get(app, "/v1/health", chave=chave)
        assert resposta.status_code == 200
        assert resposta.json() == {"status": "ok"}
    finally:
        app.dependency_overrides.clear()
