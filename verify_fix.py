from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto("http://localhost:8000/login")
    page.fill('input[name="password"]', "password")
    page.click('button[type="submit"]')
    page.wait_for_url("http://localhost:8000/")
    page.goto("http://localhost:8000/events")
    content = page.content()
    if "Internal Server Error" in content:
        print("Internal Server Error found on /events page")
    else:
        print("No Internal Server Error found on /events page")
    browser.close()
