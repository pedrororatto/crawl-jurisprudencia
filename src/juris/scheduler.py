"""Coleta incremental por tema.

Uso:
  python -m juris.scheduler --once                  # uma rodada completa agora
  python -m juris.scheduler --once --tema lgpd      # só um tema
  python -m juris.scheduler --once --max-paginas 3  # limitar páginas (teste)
  python -m juris.scheduler --once --desde 2026-01-01
  python -m juris.scheduler                         # daemon: roda todo dia às 03:00

Job por período (backfill de um mês, percorre TODAS as páginas retornadas):
  python -m juris.scheduler --once --desde 2026-06-01 --ate 2026-06-30
  python -m juris.scheduler --once --tema lgpd --desde 2026-06-01 --ate 2026-06-30

Backfill histórico automático (mês a mês, do mês atual até backfill_inicio):
  python -m juris.scheduler --backfill-passo   # um mês por execução, e sai
  python -m juris.scheduler                    # daemon: incremental diário às
                                               # 03:00 + um passo do backfill a
                                               # cada 10 minutos
"""

import argparse
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from juris.collectors.tjsp_cjsg import TjspCjsgCollector
from juris.config import settings
from juris.db.engine import SessionLocal
from juris.db.models import BackfillProgresso, Execucao
from juris.lock import adquirir_trava_unica
from juris.pipeline.ingest import ingerir
from juris.temas import Tema, carregar_temas

logger = logging.getLogger(__name__)


async def _obter_inicio(tribunal: str, tema_slug: str, desde: date | None) -> date:
    if desde:
        return desde
    async with SessionLocal() as session:
        row = await session.scalar(
            select(Execucao).where(
                Execucao.tribunal == tribunal, Execucao.tema_slug == tema_slug
            )
        )
    if row and row.watermark:
        return row.watermark - timedelta(days=settings.watermark_margem_dias)
    return settings.backfill_inicio


async def _salvar_watermark(
    tribunal: str, tema_slug: str, watermark: date | None, qtd: int
) -> None:
    async with SessionLocal() as session:
        row = await session.scalar(
            select(Execucao).where(
                Execucao.tribunal == tribunal, Execucao.tema_slug == tema_slug
            )
        )
        if row is None:
            row = Execucao(tribunal=tribunal, tema_slug=tema_slug)
            session.add(row)
        if watermark and (row.watermark is None or watermark > row.watermark):
            row.watermark = watermark
        row.ultima_execucao = datetime.now(timezone.utc)
        row.ultima_qtd = qtd
        await session.commit()


# Teto efetivamente "sem limite" para o job por período: o coletor já para na
# última página real (ceil(total / 20)).
TODAS_AS_PAGINAS = 1_000_000


async def coletar_tema(
    tema: Tema,
    max_paginas: int | None = None,
    desde: date | None = None,
    ate: date | None = None,
) -> int:
    collector = TjspCjsgCollector()
    inicio = await _obter_inicio(collector.tribunal, tema.slug, desde)
    fim = ate or date.today()
    if max_paginas is None:
        # Com período explícito (--desde/--ate) percorre tudo; na rodada
        # incremental vale o teto de configuração.
        max_paginas = TODAS_AS_PAGINAS if ate else settings.max_paginas_por_rodada
    logger.info(
        "Coletando tema %r (%s a %s, até %s páginas): %s",
        tema.slug, inicio, fim, max_paginas, tema.busca,
    )

    total = 0
    maior_publicacao: date | None = None
    async for pagina in collector.paginas(tema.busca, inicio, fim, max_paginas, tema.campo):
        async with SessionLocal() as session:
            novas, atualizadas = await ingerir(session, pagina.decisoes, tema.slug)
            await session.commit()
        total += len(pagina.decisoes)
        datas = [d.data_publicacao for d in pagina.decisoes if d.data_publicacao]
        if datas:
            maior = max(datas)
            maior_publicacao = max(maior_publicacao, maior) if maior_publicacao else maior
        logger.info(
            "Tema %r página %s/%s: %s novas, %s atualizadas",
            tema.slug, pagina.pagina, pagina.total_resultados, novas, atualizadas,
        )

    await _salvar_watermark(collector.tribunal, tema.slug, maior_publicacao, total)
    return total


async def rodada(
    tema_slug: str | None = None,
    max_paginas: int | None = None,
    desde: date | None = None,
    ate: date | None = None,
) -> int:
    """Executa a coleta dos temas e retorna o número de temas que falharam."""
    temas = carregar_temas(settings.temas_path)
    if tema_slug:
        temas = [t for t in temas if t.slug == tema_slug]
        if not temas:
            raise SystemExit(f"Tema {tema_slug!r} não existe em {settings.temas_path}")
    falhas = 0
    for tema in temas:
        try:
            qtd = await coletar_tema(tema, max_paginas, desde, ate)
            logger.info("Tema %r: %s decisões processadas", tema.slug, qtd)
        except Exception:
            falhas += 1
            logger.exception("Falha ao coletar tema %r; seguindo para o próximo", tema.slug)
    return falhas


def _mes_anterior(d: date) -> date:
    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)


def _fim_do_mes(d: date) -> date:
    proximo = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    return proximo - timedelta(days=1)


async def backfill_passo(tribunal: str = "TJSP") -> None:
    """Um passo do backfill histórico: coleta UM mês (todos os temas, todas as
    páginas) e retrocede o ponteiro. O daemon chama isto a cada 10 minutos até
    chegar em settings.backfill_inicio (jan/2020 por padrão)."""
    hoje = date.today()
    async with SessionLocal() as session:
        row = await session.scalar(
            select(BackfillProgresso).where(BackfillProgresso.tribunal == tribunal)
        )
        if row is None:
            row = BackfillProgresso(
                tribunal=tribunal, proximo_mes=date(hoje.year, hoje.month, 1)
            )
            session.add(row)
            await session.commit()
        proximo_mes = row.proximo_mes

    if proximo_mes < settings.backfill_inicio.replace(day=1):
        logger.info(
            "Backfill %s concluído (chegou a %s); nada a fazer",
            tribunal, settings.backfill_inicio,
        )
        return

    inicio, fim = proximo_mes, _fim_do_mes(proximo_mes)
    logger.info("Backfill %s: coletando %s a %s", tribunal, inicio, fim)
    falhas = await rodada(desde=inicio, ate=fim)
    if falhas:
        # Não avança o ponteiro: o próximo passo tenta o mesmo mês de novo.
        logger.error(
            "Backfill %s: %s tema(s) falharam no mês %s; será refeito no próximo passo",
            tribunal, falhas, proximo_mes,
        )
        return

    async with SessionLocal() as session:
        row = await session.scalar(
            select(BackfillProgresso).where(BackfillProgresso.tribunal == tribunal)
        )
        row.proximo_mes = _mes_anterior(proximo_mes)
        row.atualizado_em = datetime.now(timezone.utc)
        await session.commit()
    logger.info("Backfill %s: mês %s concluído; próximo: %s", tribunal, inicio, row.proximo_mes)


def _daemon() -> None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    async def _run() -> None:
        # Na subida, roda a incremental uma vez ANTES de agendar o resto, para
        # não abrir duas coletas paralelas contra o e-SAJ no boot.
        logger.info("Rodada incremental inicial...")
        await rodada()

        # O AsyncIOScheduler precisa ser iniciado com o event loop já rodando.
        scheduler = AsyncIOScheduler()
        scheduler.add_job(rodada, "cron", hour=3, minute=0)
        # Backfill histórico: um mês por passo, a cada 10 min, até
        # backfill_inicio. max_instances=1 garante que um passo lento não
        # acumule execuções; next_run_time dispara o primeiro passo já.
        scheduler.add_job(
            backfill_passo,
            "interval",
            minutes=10,
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(),
        )
        scheduler.start()
        logger.info(
            "Scheduler ativo: coleta incremental diária às 03:00 + backfill "
            "mensal a cada 10 min (primeiro passo já disparado)"
        )
        await asyncio.Event().wait()

    asyncio.run(_run())


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Trava primeiro, antes de qualquer outra coisa: nenhuma invocação do
    # scheduler (daemon, --once ou --backfill-passo) pode rodar em paralelo
    # com outra — foi rodar isso em duplicidade que causou o bloqueio no e-SAJ.
    adquirir_trava_unica()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="executa uma rodada e sai")
    parser.add_argument(
        "--backfill-passo",
        action="store_true",
        help="executa UM passo do backfill histórico (um mês) e sai",
    )
    parser.add_argument("--tema", help="slug de um único tema do temas.yaml")
    parser.add_argument("--max-paginas", type=int, help="limite de páginas por tema")
    parser.add_argument(
        "--desde", type=date.fromisoformat, help="ignora watermark e coleta desde esta data (ISO)"
    )
    parser.add_argument(
        "--ate",
        type=date.fromisoformat,
        help="fim do período (ISO); exige --desde e limita o intervalo a 1 mês, "
        "mas percorre todas as páginas retornadas",
    )
    args = parser.parse_args()

    if args.ate:
        if not args.desde:
            parser.error("--ate exige --desde")
        if args.ate < args.desde:
            parser.error("--ate não pode ser anterior a --desde")
        if (args.ate - args.desde).days > 31:
            parser.error("o período entre --desde e --ate é limitado a 1 mês (31 dias)")

    if args.backfill_passo:
        asyncio.run(backfill_passo())
    elif args.once:
        asyncio.run(rodada(args.tema, args.max_paginas, args.desde, args.ate))
    else:
        _daemon()


if __name__ == "__main__":
    main()
