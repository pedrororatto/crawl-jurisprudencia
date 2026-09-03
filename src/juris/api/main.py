from fastapi import FastAPI

from juris.api.routes import router

app = FastAPI(
    title="Juris — API de Jurisprudência de Direito Digital",
    description=(
        "Decisões de segundo grau coletadas do TJSP (e-SAJ/CJSG) sobre temas de "
        "direito digital, com busca full-text em português e atualização diária."
    ),
    version="0.1.0",
)
app.include_router(router)
