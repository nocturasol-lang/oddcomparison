import asyncio
from scraper.novibet import NovibetScraper
from scraper.laystars import LaystarsScraper

async def test():
    print("=== NOVIBET ===")
    novibet = NovibetScraper()
    await novibet.initialize()
    result = await novibet.fetch()
    print(f"Novibet: {len(result.entries)} entries, success={result.success}")

    print("\n=== LAYSTARS ===")
    laystars = LaystarsScraper()
    result2 = await laystars.fetch()
    print(f"Laystars: {len(result2.entries)} entries, success={result2.success}")
    if result2.entries:
        for e in result2.entries[:3]:
            print(f"  {e.game_name} | {e.market} | ls1={e.ls1}")

    await novibet.cleanup()

asyncio.run(test())
