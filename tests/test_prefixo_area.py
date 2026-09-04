from pathlib import Path

from sqlalchemy import text

from juris.temas import carregar_temas


def test_todos_os_slugs_do_yaml_tem_prefixo_de_area():
    temas = carregar_temas(Path("config/temas.yaml"))
    assert temas, "temas.yaml não pode estar vazio"
    sem_prefixo = [t.slug for t in temas if not t.slug.startswith("digital-")]
    assert sem_prefixo == [], f"slugs sem prefixo de área: {sem_prefixo}"


async def test_migracao_de_slug_e_idempotente(session):
    """Reexecutar não pode produzir digital-digital-lgpd."""
    await session.execute(
        text(
            "INSERT INTO decisoes (tribunal, cd_acordao, temas) "
            "VALUES ('TJSP', '99999002', ARRAY['digital-lgpd'])"
        )
    )
    await session.execute(
        text(
            """
            UPDATE decisoes
               SET temas = ARRAY(
                     SELECT DISTINCT
                            CASE WHEN t LIKE 'digital-%' THEN t
                                 ELSE 'digital-' || t END
                       FROM unnest(temas) AS t ORDER BY 1)
             WHERE cd_acordao = '99999002'
            """
        )
    )
    temas = await session.scalar(
        text("SELECT temas FROM decisoes WHERE cd_acordao = '99999002'")
    )
    assert temas == ["digital-lgpd"]
