"""
Helper: prints instructions for obtaining Laystars888 session cookies.

Run directly:  py -3.12 -m scraper.cookie_helper
"""


def print_instructions() -> None:
    print(
        """
=== Laystars888 Cookie Setup ===

The Laystars scraper needs cookies from a logged-in browser session.

To get your cookies:

  1. Open Chrome, navigate to https://www.laystars888.com and log in.
  2. Press F12 to open DevTools, go to the Network tab.
  3. Click any XHR request to www.laystars888.com (e.g. list-live-mapped).
  4. In the Headers tab, find "Cookie:" under Request Headers.
  5. Copy the entire cookie string (everything after "Cookie: ").
  6. Paste it into  backend/config.py  as:

       LAYSTARS_COOKIES = '<your cookie string here>'

  7. Run the test:  py -3.12 test_run.py
"""
    )


if __name__ == "__main__":
    print_instructions()
