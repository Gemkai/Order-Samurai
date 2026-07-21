from playwright.sync_api import sync_playwright

errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.goto("http://localhost:5173/", wait_until="networkidle", timeout=40000)
    pg.wait_for_timeout(2000)
    pg.screenshot(path="_overview.png", full_page=True)
    pg.get_by_role("button", name="SWORD").first.click()
    pg.wait_for_timeout(1500)
    pg.screenshot(path="_sword.png", full_page=True)
    try:
        pg.get_by_text("Open CVEs", exact=True).first.click()  # open metric inspector modal
        pg.wait_for_timeout(900)
        pg.screenshot(path="_modal.png", full_page=True)
    except Exception as e:
        errors.append(f"modal: {e}")
    b.close()

print("CONSOLE/PAGE ERRORS:", errors[:25] if errors else "none")
