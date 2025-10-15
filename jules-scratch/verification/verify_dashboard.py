from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        # Go to the login page
        page.goto("http://localhost:8000/login")

        # Fill in the password and click login
        page.fill("input[name='password']", "password")
        page.click("button[type='submit']")

        # Go to the members page and take a screenshot
        page.goto("http://localhost:8000/members")
        page.screenshot(path="jules-scratch/verification/members.png")

        # Go to the gift codes page and take a screenshot
        page.goto("http://localhost:8000/giftcodes")
        page.screenshot(path="jules-scratch/verification/giftcodes.png")

        # Go to the logs page and take a screenshot
        page.goto("http://localhost:8000/logs")
        page.screenshot(path="jules-scratch/verification/logs.png")

        # Go to the events page and take a screenshot
        page.goto("http://localhost:8000/events")
        page.screenshot(path="jules-scratch/verification/events.png")

        browser.close()

if __name__ == "__main__":
    run()
