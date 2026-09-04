from fastapi import FastAPI

from juris.api.routes import router
from juris.logging_config import configurar_logging

# Subindo por `uvicorn juris.api.main:app` nada mais configura o logging: os
# loggers `juris.*` ficariam sem handler, cairiam no lastResort em WARNING e
# todo o INFO da aplicação sumiria.
configurar_logging()

app = FastAPI(
    title="Juris — API de Jurisprudência de Direito Digital",
    description=(
        "Decisões de segundo grau coletadas do TJSP (e-SAJ/CJSG) sobre temas de "
        "direito digital, com busca full-text em português e atualização diária."
    ),
    version="0.1.0",
)
app.include_router(router)
