from contextlib import asynccontextmanager

from fastapi import FastAPI

from juris.api.routes import router
from juris.logging_config import configurar_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Subindo por `uvicorn juris.api.main:app` nada mais configura o logging:
    # os loggers `juris.*` ficariam sem handler, cairiam no lastResort em
    # WARNING e todo o INFO da aplicação sumiria.
    #
    # Aqui, e não no import do módulo: `configurar_logging` usa
    # `basicConfig(force=True)`, que remove e FECHA os handlers da raiz. Os
    # testes importam este módulo no meio da sessão de pytest, e no import
    # isso derrubaria os handlers de quem estivesse rodando.
    configurar_logging()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Juris — API de Jurisprudência de Direito Digital",
    description=(
        "Decisões de segundo grau coletadas do TJSP (e-SAJ/CJSG) sobre temas de "
        "direito digital, com busca full-text em português e atualização diária."
    ),
    version="0.1.0",
)
app.include_router(router)
