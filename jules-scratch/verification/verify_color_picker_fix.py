
from playwright.sync_api import sync_playwright

def run(playwright):
    browser = playwright.chromium.launch()
    page = browser.new_page()

    # Log in
    page.goto("http://localhost:8000/login")
    page.fill('input[name="password"]', 'password')
    page.click('button[type="submit"]')
    page.wait_for_url("http://localhost:8000/")

    # Navigate to events page
    page.goto("http://localhost:8000/events")

    # Click the "Add Event" button
    page.click("#add-event-btn")

    # Wait for the modal to be visible
    page.wait_for_selector("#add-event-modal", state="visible")

    # Click the color input to open the picker
    page.click("#add_embed_color")

    # Take a screenshot of the modal with the color picker open in dark mode
    page.screenshot(path="jules-scratch/verification/verification-dark.png")

    # Close the modal
    page.click("#add-event-modal .cancel-button")

    # Toggle to light mode
    page.click("#theme-toggle-button")

    # Re-open the modal
    page.click("#add-event-btn")

    # Wait for the modal to be visible
    page.wait_for_selector("#add-event-modal", state="visible")

    # Click the color input to open the picker again
    page.click("#add_embed_color")

    # Take a screenshot of the modal with the color picker open in light mode
    page.screenshot(path="jules-scratch/verification/verification-light.png")

    browser.close()

with sync_playwright() as playwright:
    run(playwright)
