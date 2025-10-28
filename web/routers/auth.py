"""
Authentication routes for the web application.

This module handles user login, logout, and session authentication,
providing a secure entry point to the web dashboard.
"""

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from web.core.config import templates, WEB_DASHBOARD_PASSWORD

router = APIRouter()

def is_authenticated(request: Request) -> bool:
    """
    Checks if a user is authenticated by verifying the session.

    Args:
        request: The incoming request object.

    Returns:
        True if the user is authenticated, False otherwise.
    """
    return "authenticated" in request.session

@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    """Serves the login page."""
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, password: str = Form(...)):
    """
    Handles the login form submission.

    Args:
        request: The incoming request object.
        password: The password submitted via the form.

    Returns:
        A redirect to the dashboard on successful login, or the login page
        with an error message on failure.
    """
    if password == WEB_DASHBOARD_PASSWORD:
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=303)
    else:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid password"})

@router.get("/logout")
async def logout(request: Request):
    """
    Logs the user out by clearing the session.

    Args:
        request: The incoming request object.

    Returns:
        A redirect to the login page.
    """
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
