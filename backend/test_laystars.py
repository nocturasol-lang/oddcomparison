"""Quick test for Laystars888 scraper."""
import asyncio
from scraper.laystars import LaystarsScraper


async def test():
    scraper = LaystarsScraper()
    result = await scraper.fetch()
    print(f"Success: {result.success}, entries: {len(result.entries)}")
    if result.entries:
        for e in result.entries[:5]:
            print(f"  {e.game_name} | {e.market} | {e.selection} | lay={e.lay_odds} ls1={e.ls1} ls2={e.ls2} ls3={e.ls3} avail={e.lay_available}")
    if result.error:
        print(f"Error: {result.error}")


if __name__ == "__main__":
    asyncio.run(test())
