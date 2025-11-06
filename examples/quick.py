"""Quick example: fetch ticker using registry.get_exchange_adapter"""
import asyncio
from cuea.registry import get_exchange_adapter

async def main():
    adapter = get_exchange_adapter('binance')  # pass api_key/secret if needed
    try:
        ticker = await adapter.spot.fetch_ticker('BTC/USDT')
        print('ticker:', ticker)
    finally:
        await adapter.close()

if __name__ == '__main__':
    asyncio.run(main())
