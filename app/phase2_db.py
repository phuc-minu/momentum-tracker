import os
from contextlib import contextmanager
from typing import Any, Iterator


SCHEMA_SQL = """
create table if not exists symbols (
  symbol text primary key,
  name text,
  exchange text,
  sector text,
  active boolean not null default true,
  updated_at timestamptz not null default now()
);

alter table symbols add column if not exists first_listed_at timestamptz;
alter table symbols add column if not exists first_listed_price numeric;

create table if not exists daily_bars (
  symbol text not null references symbols(symbol) on delete cascade,
  bar_date date not null,
  open numeric not null,
  high numeric not null,
  low numeric not null,
  close numeric not null,
  volume bigint not null default 0,
  updated_at timestamptz not null default now(),
  primary key (symbol, bar_date)
);

create table if not exists refresh_runs (
  id bigserial primary key,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null default 'running',
  symbols_requested int not null default 0,
  symbols_scored int not null default 0,
  error text
);

create table if not exists refresh_logs (
  id bigserial primary key,
  run_id bigint references refresh_runs(id) on delete cascade,
  created_at timestamptz not null default now(),
  level text not null,
  source text not null,
  message text not null,
  details jsonb not null default '{}'::jsonb
);

create table if not exists momentum_scores (
  symbol text primary key references symbols(symbol) on delete cascade,
  run_id bigint references refresh_runs(id) on delete set null,
  score numeric not null,
  grade text not null,
  setup text not null,
  last_close numeric not null,
  factors jsonb not null,
  notes jsonb not null,
  scored_at timestamptz not null default now()
);

create table if not exists option_scores (
  symbol text primary key references symbols(symbol) on delete cascade,
  run_id bigint references refresh_runs(id) on delete set null,
  put_call_ratio numeric,
  put_call_source text not null default 'unavailable',
  call_volume bigint,
  call_open_interest bigint,
  call_activity_ratio numeric,
  call_activity text not null default 'unavailable',
  options_confirmation_score numeric,
  options_signal text not null default 'Options unavailable',
  preferred_contract jsonb,
  scored_at timestamptz not null default now()
);

create index if not exists idx_daily_bars_symbol_date on daily_bars(symbol, bar_date desc);
create index if not exists idx_momentum_scores_score on momentum_scores(score desc);
create index if not exists idx_option_scores_score on option_scores(options_confirmation_score desc nulls last);
create index if not exists idx_refresh_logs_run_created on refresh_logs(run_id, created_at desc);
"""


def database_url() -> str | None:
    return os.getenv("DATABASE_URL")


def require_database_url() -> str:
    value = database_url()
    if not value:
        raise RuntimeError("DATABASE_URL is required for Phase 2 PostgreSQL features")
    return value


def _psycopg():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:
        raise RuntimeError("Install psycopg[binary] to use Phase 2 PostgreSQL features") from error
    return psycopg, dict_row


@contextmanager
def connect() -> Iterator[Any]:
    url = require_database_url()
    psycopg, dict_row = _psycopg()
    with psycopg.connect(url, row_factory=dict_row) as connection:
        yield connection


def init_db(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(SCHEMA_SQL)
    connection.commit()


def create_refresh_run(connection, *, symbols_requested: int) -> int:
    with connection.cursor() as cursor:
        row = cursor.execute(
            "insert into refresh_runs (symbols_requested) values (%s) returning id",
            (symbols_requested,),
        ).fetchone()
    connection.commit()
    return int(row["id"])


def finish_refresh_run(connection, run_id: int, *, status: str, symbols_scored: int, error: str | None = None) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            update refresh_runs
            set finished_at = now(), status = %s, symbols_scored = %s, error = %s
            where id = %s
            """,
            (status, symbols_scored, error, run_id),
        )
    connection.commit()


def insert_refresh_log(
    connection,
    run_id: int,
    *,
    level: str,
    source: str,
    message: str,
    details: str = "{}",
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            insert into refresh_logs (run_id, level, source, message, details)
            values (%s, %s, %s, %s, %s::jsonb)
            """,
            (run_id, level, source, message, details),
        )
    connection.commit()


def upsert_symbols(connection, symbols: list[dict[str, Any]]) -> None:
    if not symbols:
        return
    rows = [
        (
            item["symbol"],
            item.get("name"),
            item.get("exchange"),
            item.get("sector", "Unclassified"),
        )
        for item in symbols
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            insert into symbols (symbol, name, exchange, sector, active)
            values (%s, %s, %s, %s, true)
            on conflict (symbol) do update set
              name = excluded.name,
              exchange = excluded.exchange,
              sector = excluded.sector,
              active = true,
              updated_at = now()
            """,
            rows,
        )
    connection.commit()


def upsert_daily_bars(connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    values = [
        (
            row["symbol"],
            row["bar_date"],
            row["open"],
            row["high"],
            row["low"],
            row["close"],
            row["volume"],
        )
        for row in rows
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            insert into daily_bars (symbol, bar_date, open, high, low, close, volume)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (symbol, bar_date) do update set
              open = excluded.open,
              high = excluded.high,
              low = excluded.low,
              close = excluded.close,
              volume = excluded.volume,
              updated_at = now()
            """,
            values,
        )
    connection.commit()


def fetch_recent_daily_bars(connection, symbols: list[str], *, countback: int) -> list[dict[str, Any]]:
    if not symbols:
        return []
    with connection.cursor() as cursor:
        rows = cursor.execute(
            """
            select symbol, bar_date, open, high, low, close, volume
            from (
              select
                symbol,
                bar_date,
                open,
                high,
                low,
                close,
                volume,
                row_number() over (partition by symbol order by bar_date desc) as row_number
              from daily_bars
              where symbol = any(%s)
            ) ranked
            where row_number <= %s
            order by symbol, bar_date
            """,
            (symbols, countback),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_momentum_scores(connection, run_id: int, scores: list[dict[str, Any]]) -> None:
    if not scores:
        return
    values = [
        (
            item["symbol"],
            run_id,
            item["score"],
            item["grade"],
            item["setup"],
            item["last_close"],
            item["factors"],
            item["notes"],
        )
        for item in scores
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            insert into momentum_scores (symbol, run_id, score, grade, setup, last_close, factors, notes)
            values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            on conflict (symbol) do update set
              run_id = excluded.run_id,
              score = excluded.score,
              grade = excluded.grade,
              setup = excluded.setup,
              last_close = excluded.last_close,
              factors = excluded.factors,
              notes = excluded.notes,
              scored_at = now()
            """,
            values,
        )
        cursor.execute(
            """
            update symbols s
            set first_listed_at = m.scored_at, first_listed_price = m.last_close
            from momentum_scores m
            where m.symbol = s.symbol
              and s.symbol = any(%s) and s.first_listed_at is null
            """,
            ([item["symbol"] for item in scores],),
        )
    connection.commit()


def upsert_option_scores(connection, run_id: int, scores: list[dict[str, Any]]) -> None:
    if not scores:
        return
    values = [
        (
            item["symbol"],
            run_id,
            item.get("put_call_ratio"),
            item.get("put_call_source", "unavailable"),
            item.get("call_volume"),
            item.get("call_open_interest"),
            item.get("call_activity_ratio"),
            item.get("call_activity", "unavailable"),
            item.get("options_confirmation_score"),
            item.get("options_signal", "Options unavailable"),
            item.get("preferred_contract"),
        )
        for item in scores
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            insert into option_scores (
              symbol, run_id, put_call_ratio, put_call_source, call_volume, call_open_interest,
              call_activity_ratio, call_activity, options_confirmation_score, options_signal,
              preferred_contract
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            on conflict (symbol) do update set
              run_id = excluded.run_id,
              put_call_ratio = excluded.put_call_ratio,
              put_call_source = excluded.put_call_source,
              call_volume = excluded.call_volume,
              call_open_interest = excluded.call_open_interest,
              call_activity_ratio = excluded.call_activity_ratio,
              call_activity = excluded.call_activity,
              options_confirmation_score = excluded.options_confirmation_score,
              options_signal = excluded.options_signal,
              preferred_contract = excluded.preferred_contract,
              scored_at = now()
            """,
            values,
        )
    connection.commit()


def prune_scores_for_run(connection, run_id: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "delete from option_scores where run_id is distinct from %s",
            (run_id,),
        )
        cursor.execute(
            "delete from momentum_scores where run_id is distinct from %s",
            (run_id,),
        )
    connection.commit()


def fetch_phase2_candidates(connection, *, limit: int, min_price: float = 10.0, exclude_earnings: bool = False) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        rows = cursor.execute(
            """
            select
              m.symbol,
              s.name,
              s.exchange,
              s.sector,
              m.score,
              m.grade,
              m.setup,
              m.last_close,
              m.factors,
              m.notes,
              m.scored_at,
              to_jsonb(s)->>'first_listed_at' as first_listed_at,
              (to_jsonb(s)->>'first_listed_price')::numeric as first_listed_price,
              o.options_confirmation_score,
              o.options_signal,
              o.put_call_source,
              o.call_volume,
              o.call_open_interest,
              o.call_activity_ratio,
              o.call_activity,
              o.put_call_ratio,
              o.preferred_contract
            from momentum_scores m
            join symbols s on s.symbol = m.symbol
            left join option_scores o on o.symbol = m.symbol
            where s.active = true
              and m.last_close > %s
              and (
                not %s
                or coalesce((m.factors->>'likely_earnings_related')::boolean, false) = false
              )
            order by m.score desc, (m.factors->>'return_1m')::numeric desc nulls last
            limit %s
            """,
            (min_price, exclude_earnings, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_latest_refresh_run(connection) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        row = cursor.execute(
            """
            select id, started_at, finished_at, status, symbols_requested, symbols_scored, error
            from refresh_runs
            order by id desc
            limit 1
            """
        ).fetchone()
    if not row:
        return None
    refresh_run = dict(row)
    refresh_run["logs"] = fetch_refresh_logs(connection, int(refresh_run["id"]))
    return refresh_run


def fetch_refresh_logs(connection, run_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    if not _table_exists(connection, "refresh_logs"):
        return []
    with connection.cursor() as cursor:
        rows = cursor.execute(
            """
            select id, run_id, created_at, level, source, message, details
            from refresh_logs
            where run_id = %s
            order by created_at desc
            limit %s
            """,
            (run_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def _table_exists(connection, table_name: str) -> bool:
    with connection.cursor() as cursor:
        row = cursor.execute("select to_regclass(%s) as table_name", (f"public.{table_name}",)).fetchone()
    return bool(row and row["table_name"])
