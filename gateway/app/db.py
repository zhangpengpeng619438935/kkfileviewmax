import aiomysql
import redis.asyncio as aioredis

from . import config

_pool: aiomysql.Pool | None = None
_redis: aioredis.Redis | None = None


async def init_db():
    global _pool
    _pool = await aiomysql.create_pool(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT,
        user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
        db=config.MYSQL_DB, charset="utf8mb4",
        minsize=config.MYSQL_POOL_MIN, maxsize=config.MYSQL_POOL_MAX,
        autocommit=True, pool_recycle=1800,
    )


async def close_db():
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None


def pool() -> aiomysql.Pool:
    if _pool is None:
        raise RuntimeError("数据库未初始化")
    return _pool


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.Redis(
            host=config.REDIS_HOST, port=config.REDIS_PORT,
            password=config.REDIS_PASSWORD or None, db=config.REDIS_DB,
            decode_responses=True, socket_timeout=5, socket_connect_timeout=5,
        )
    return _redis


async def query(sql, args=None):
    async with pool().acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            return await cur.fetchall()


async def query_one(sql, args=None):
    rows = await query(sql, args)
    return rows[0] if rows else None


async def execute(sql, args=None):
    async with pool().acquire() as conn:
        async with conn.cursor() as cur:
            return await cur.execute(sql, args)


async def executemany(sql, seq):
    async with pool().acquire() as conn:
        async with conn.cursor() as cur:
            return await cur.executemany(sql, seq)


async def in_transaction(funcs):
    """按顺序在单个事务中执行一组 (sql, args)。"""
    async with pool().acquire() as conn:
        async with conn.cursor() as cur:
            await conn.begin()
            try:
                for sql, args in funcs:
                    await cur.execute(sql, args)
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
