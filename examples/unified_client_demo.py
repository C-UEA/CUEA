"""UnifiedClient demo: fetch ticker and (commented) place order"""
import asyncio
from cuea import UnifiedClient

async def demo():
    async with UnifiedClient('binance') as c:
        t = await c.fetch_ticker('BTC/USDT')
        print('ticker', t)
        # place a limit order (example)
        # order = await c.place_limit_order('BTC/USDT', 'buy', qty=Decimal('0.001'), price=Decimal('30000'), market_type='spot')
        # print('placed', order)

if __name__ == '__main__':
    asyncio.run(demo())
