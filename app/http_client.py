import os
import asyncio
import logging
from typing import Optional, Dict

import httpx

logger = logging.getLogger(__name__)

GLOBAL_CLIENT: Optional[httpx.AsyncClient] = None
_pending_close_clients = []

def _get_proxies() -> Optional[Dict[str, str]]:
    proxy = os.getenv("HTTP_PROXY", "").strip()
    if proxy:
        return {"http": proxy, "https": proxy}
    return None

def _create_client_config():
    proxies = _get_proxies()
    mounts = None
    if proxies:
        proxy_url = proxies.get("https") or proxies.get("http")
        if proxy_url:
            mounts = {
                "https://": httpx.AsyncHTTPTransport(proxy=proxy_url),
                "http://": httpx.AsyncHTTPTransport(proxy=proxy_url),
            }
    limits = httpx.Limits(
        max_keepalive_connections=200,
        max_connections=200,
        keepalive_expiry=1.0
    )
    timeout = httpx.Timeout(
        connect=2.0,
        read=300.0,
        write=2.0,
        pool=1.0
    )
    return mounts, timeout, limits

async def init_http_client():
    global GLOBAL_CLIENT
    mounts, timeout, limits = _create_client_config()
    GLOBAL_CLIENT = httpx.AsyncClient(mounts=mounts, timeout=timeout, limits=limits)

async def close_http_client():
    global GLOBAL_CLIENT
    if GLOBAL_CLIENT:
        await GLOBAL_CLIENT.aclose()
        GLOBAL_CLIENT = None

def get_http_client() -> Optional[httpx.AsyncClient]:
    return GLOBAL_CLIENT

async def recycle_http_client():
    global GLOBAL_CLIENT
    while True:
        try:
            await asyncio.sleep(60)
            logger.info("[连接回收] 开始回收全局HTTP客户端...")
            old_client = GLOBAL_CLIENT
            mounts, timeout, limits = _create_client_config()
            GLOBAL_CLIENT = httpx.AsyncClient(mounts=mounts, timeout=timeout, limits=limits)
            logger.info("[连接回收] 新客户端已创建，等待120秒后关闭旧客户端...")
            if old_client:
                _pending_close_clients.append(old_client)
                asyncio.create_task(_delayed_close_client(old_client))
            if len(_pending_close_clients) > 5:
                logger.warning(f"[连接回收] 待关闭客户端过多({len(_pending_close_clients)})")
        except Exception as e:
            logger.error(f"[连接回收] 回收失败: {e}")
            if GLOBAL_CLIENT is None:
                try:
                    await init_http_client()
                except Exception:
                    pass

async def _delayed_close_client(client: httpx.AsyncClient):
    try:
        await asyncio.sleep(120)
        logger.info("[连接回收] 开始关闭旧客户端...")
        try:
            await asyncio.wait_for(client.aclose(), timeout=2.0)
            logger.info("[连接回收] 旧客户端已成功关闭")
        except asyncio.TimeoutError:
            logger.warning("[连接回收] 旧客户端关闭超时")
        except Exception as e:
            logger.warning(f"[连接回收] 关闭旧客户端时出错: {e}")
        finally:
            try:
                _pending_close_clients.remove(client)
            except ValueError:
                pass
    except Exception as e:
        logger.error(f"[连接回收] 延迟关闭任务失败: {e}")
