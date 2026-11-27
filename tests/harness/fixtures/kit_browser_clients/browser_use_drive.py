"""The shop journey through browser-use's own browser and REPL, with no model: the cell an agent would write.

Usage: python browser_use_drive.py <DevTools address> <workspace>. Run in browser-use's own environment."""

import asyncio
import json
import sys
from pathlib import Path

from browser_use.browser import Browser
from browser_use.repl.session import Repl

CELL = """
def centre(selector):
    return js("(() => { const box = document.querySelector('%s').getBoundingClientRect();"
              " return [box.x + box.width / 2, box.y + box.height / 2]; })()" % selector)

new_tab('http://shop.test/journey')
wait_for_load()
click_at_xy(*centre('#buy'))
click_at_xy(*centre('#q'))
type_text('red shoes')
click_at_xy(*centre('#open'))
wait(2)
goto_url('http://www.example.com/deals')
wait(2)
print('done')
"""


async def main(address: str, workspace: str) -> int:
    browser = Browser(cdp_url=address)
    env = await browser.start()
    repl = Repl(Path(workspace), env=env)
    try:
        await repl.start()
        result = await repl.call("exec", CELL, timeout=90)
    finally:
        await repl.close()
        await browser.stop()
    print(json.dumps({key: result.get(key) for key in ("output", "error")}))
    return 0 if not result.get("error") and "done" in (result.get("output") or "") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2])))
