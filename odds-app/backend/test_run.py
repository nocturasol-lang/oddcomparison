import asyncio
from scraper.novibet import NovibetScraper
from scraper.laystars import LaystarsScraper
from config import LAYSTARS_COOKIES


async def test():
    print("=== LAYSTARS ===")
    laystars = LaystarsScraper()
    await laystars.set_cookies(LAYSTARS_COOKIES)
    # Fallback event IDs for when discovery (list-live-mapped) is down
    laystars.event_ids = ["35320789"]
    result = await laystars.fetch()
    print(f"Laystars: {len(result.entries)} entries, success={result.success}")
    if result.entries:
        for e in result.entries[:5]:
            print(f"  {e.game_name} | {e.market} | {e.selection} | ls1={e.ls1} | available={e.lay_available}")
    else:
        print(f"Error: {result.error}")


asyncio.run(test())
