from playwright.sync_api import sync_playwright

def run(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    # Login
    page.goto("http://localhost:8000/login")
    page.fill("input[name='password']", "password")
    page.click("button[type='submit']")
    page.wait_for_url("http://localhost:8000/")

    # Desktop screenshots
    page.set_viewport_size({"width": 1920, "height": 1080})
    page.goto("http://localhost:8000/members")
    page.screenshot(path="jules-scratch/verification/members-desktop.png")
    page.goto("http://localhost:8000/logs")
    page.screenshot(path="jules-scratch/verification/logs-desktop.png")
    page.goto("http://localhost:8000/events")
    page.click("#theme-toggle-button")
    page.screenshot(path="jules-scratch/verification/events-desktop-light.png")

    # Mobile screenshots
    page.set_viewport_size({"width": 375, "height": 667})
    page.goto("http://localhost:8000/members")
    page.screenshot(path="jules-scratch/verification/members-mobile.png")
    page.goto("http://localhost:8000/logs")
    page.screenshot(path="jules-scratch/verification/logs-mobile.png")
    page.goto("http://localhost:8000/events")
    page.screenshot(path="jules-scratch/verification/events-mobile-light.png")
    page.click("#hamburger-menu")
    page.wait_for_selector("#sidebar.active")
    page.screenshot(path="jules-scratch/verification/mobile-sidebar.png")

    context.close()
    browser.close()

with sync_playwright() as playwright:
    run(playwright)
