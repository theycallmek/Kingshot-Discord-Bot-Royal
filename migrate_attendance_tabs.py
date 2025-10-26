"""
Migration script to add tabbed interface to attendance.html
Adds Dashboard tab (default) and Upload tab
"""

import os
import re
from datetime import datetime

TEMPLATE_PATH = os.path.join("web", "templates", "attendance.html")

def backup_file():
    """Create backup of current file"""
    if not os.path.exists(TEMPLATE_PATH):
        print(f"[ERROR] File not found: {TEMPLATE_PATH}")
        return False

    backup_path = TEMPLATE_PATH.replace('.html', f'_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.html')

    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    with open(backup_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[OK] Backup created: {backup_path}")
    return True


def add_tab_styles(content):
    """Add CSS styles for tabs"""

    tab_styles = """
    /* Tab Navigation Styles */
    .tabs-container {
        max-width: 1400px;
        margin: 2rem auto;
    }

    .tabs-nav {
        display: flex;
        gap: 0.5rem;
        border-bottom: 2px solid var(--border-color);
        margin-bottom: 2rem;
    }

    .tab-button {
        padding: 1rem 2rem;
        background: none;
        border: none;
        border-bottom: 3px solid transparent;
        color: var(--text-color);
        cursor: pointer;
        transition: all 0.3s ease;
        font-size: 1.1rem;
        font-weight: 500;
    }

    .tab-button:hover {
        background-color: var(--surface-color);
    }

    .tab-button.active {
        color: var(--primary-color);
        border-bottom-color: var(--primary-color);
    }

    .tab-content {
        display: none;
    }

    .tab-content.active {
        display: block;
    }

    /* Dashboard Styles */
    .dashboard-grid {
        display: grid;
        gap: 1.5rem;
        margin-bottom: 2rem;
    }

    .dashboard-card {
        background-color: var(--surface-color);
        border-radius: 8px;
        padding: 1.5rem;
        border: 1px solid var(--border-color);
    }

    .dashboard-card h2 {
        margin-top: 0;
        margin-bottom: 1rem;
        color: var(--header-color);
        font-size: 1.3rem;
    }

    .dashboard-stats {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 1rem;
        margin-bottom: 2rem;
    }

    .dashboard-stat-card {
        background-color: var(--background-color);
        border-radius: 8px;
        padding: 1.5rem;
        border: 1px solid var(--border-color);
        text-align: center;
    }

    .dashboard-stat-value {
        font-size: 2.5rem;
        font-weight: bold;
        color: var(--primary-color);
        margin-bottom: 0.5rem;
    }

    .dashboard-stat-label {
        font-size: 0.9rem;
        color: var(--text-color);
        opacity: 0.8;
    }

    .verification-badge {
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.25rem 0.75rem;
        border-radius: 12px;
        font-size: 0.85rem;
        font-weight: 500;
    }

    .verification-high {
        background-color: rgba(40, 167, 69, 0.15);
        color: #28a745;
    }

    .verification-medium {
        background-color: rgba(255, 193, 7, 0.15);
        color: #ffc107;
    }

    .verification-low {
        background-color: rgba(220, 53, 69, 0.15);
        color: #dc3545;
    }

    .verification-bar {
        display: inline-block;
        width: 60px;
        height: 8px;
        background-color: var(--border-color);
        border-radius: 4px;
        overflow: hidden;
        margin-left: 0.5rem;
    }

    .verification-bar-fill {
        height: 100%;
        background: linear-gradient(90deg, var(--primary-color), #28a745);
        transition: width 0.3s ease;
    }

    body.light-mode .dashboard-card {
        background-color: #f5f5f5;
    }

    body.light-mode .dashboard-stat-card {
        background-color: #ffffff;
    }
</style>
"""

    # Insert before </style>
    content = content.replace('</style>', tab_styles)
    print("[OK] Added tab styles")
    return content


def wrap_with_tabs(content):
    """Wrap content with tabbed interface"""

    # Find the <h1> tag and content after it
    h1_pattern = r'(<h1>Event Attendance</h1>)'

    if not re.search(h1_pattern, content):
        print("[ERROR] Could not find <h1>Event Attendance</h1>")
        return content

    # Find where to start wrapping (after <h1>)
    h1_match = re.search(h1_pattern, content)
    h1_end = h1_match.end()

    # Find the upload-container div
    upload_container_start = content.find('<div class="upload-container">', h1_end)

    if upload_container_start == -1:
        print("[ERROR] Could not find upload-container div")
        return content

    # Extract the part before h1, the h1, and the part after upload-container
    before_h1 = content[:h1_match.start()]
    after_upload_start = content[upload_container_start:]

    # Find the end of upload-container (matching closing div before modal)
    # We'll look for the modal div as a marker
    modal_start = after_upload_start.find('<div class="modal" id="imageModal">')

    if modal_start == -1:
        print("[ERROR] Could not find modal div")
        return content

    upload_content = after_upload_start[:modal_start]
    after_content = after_upload_start[modal_start:]

    # Build new structure with tabs
    new_structure = f"""{before_h1}<div class="tabs-container">
    <h1>Event Attendance</h1>

    <div class="tabs-nav">
        <button class="tab-button active" data-tab="dashboard">
            📊 Dashboard
        </button>
        <button class="tab-button" data-tab="upload">
            📤 Upload Screenshots
        </button>
    </div>

    <!-- Dashboard Tab (Default View) -->
    <div class="tab-content active" id="dashboard-tab">
        <div class="dashboard-grid">
            <div class="dashboard-card">
                <h2>📈 Recent Events</h2>
                <p style="color: var(--text-color); opacity: 0.7;">Loading event data...</p>
                <div id="recentEventsContainer">
                    <!-- Will be populated by JavaScript -->
                </div>
            </div>

            <div class="dashboard-card">
                <h2>👥 Top Players</h2>
                <p style="color: var(--text-color); opacity: 0.7;">Loading player data...</p>
                <div id="topPlayersContainer">
                    <!-- Will be populated by JavaScript -->
                </div>
            </div>

            <div class="dashboard-card">
                <h2>✅ Data Verification Status</h2>
                <p style="color: var(--text-color); opacity: 0.7;">Loading verification data...</p>
                <div id="verificationContainer">
                    <!-- Will be populated by JavaScript -->
                </div>
            </div>
        </div>
    </div>

    <!-- Upload Tab -->
    <div class="tab-content" id="upload-tab">
{upload_content}    </div>
</div>

{after_content}"""

    print("[OK] Wrapped content with tabs")
    return new_structure


def add_tab_javascript(content):
    """Add JavaScript for tab switching"""

    # Find the closing </script> tag at the end
    script_end_pattern = r'(</script>\s*{% endblock %})'

    if not re.search(script_end_pattern, content):
        print("[WARNING] Could not find script closing tag pattern")
        return content

    tab_js = """
    // ========================================
    // Tab Switching Logic
    // ========================================
    document.querySelectorAll('.tab-button').forEach(button => {
        button.addEventListener('click', () => {
            const tabName = button.dataset.tab;

            // Update buttons
            document.querySelectorAll('.tab-button').forEach(btn => {
                btn.classList.remove('active');
            });
            button.classList.add('active');

            // Update content
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.remove('active');
            });
            document.getElementById(`${tabName}-tab`).classList.add('active');

            // Load dashboard data when switching to dashboard tab
            if (tabName === 'dashboard') {
                loadDashboardData();
            }
        });
    });

    // ========================================
    // Dashboard Data Loading
    // ========================================
    function loadDashboardData() {
        // Placeholder for dashboard data loading
        console.log('Loading dashboard data...');

        // TODO: Fetch actual data from API
        // For now, show placeholder content
        document.getElementById('recentEventsContainer').innerHTML = `
            <p style="color: var(--text-color); opacity: 0.6; text-align: center; padding: 2rem;">
                Dashboard data will be loaded here.<br>
                API endpoint to be implemented.
            </p>
        `;
    }

    // Load dashboard data on page load (since dashboard is default tab)
    document.addEventListener('DOMContentLoaded', () => {
        loadDashboardData();
    });

"""

    # Insert before the final </script>
    content = re.sub(
        r'(</script>\s*{% endblock %})',
        tab_js + r'\1',
        content
    )

    print("[OK] Added tab switching JavaScript")
    return content


def migrate_template():
    """Main migration function"""
    print("="*70)
    print("Attendance Template Migration - Adding Tabbed Interface")
    print("="*70)

    # Backup
    if not backup_file():
        return False

    # Read current file
    print("\nReading current template...")
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    print(f"Original file size: {len(content)} characters")

    # Apply transformations
    print("\nApplying transformations...")
    content = add_tab_styles(content)
    content = wrap_with_tabs(content)
    content = add_tab_javascript(content)

    # Write new file
    print("\nWriting modified template...")
    with open(TEMPLATE_PATH, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"New file size: {len(content)} characters")

    print("\n" + "="*70)
    print("MIGRATION COMPLETE!")
    print("="*70)
    print("\nChanges made:")
    print("  [OK] Added tab navigation styles")
    print("  [OK] Created Dashboard tab (default view)")
    print("  [OK] Moved upload functionality to Upload tab")
    print("  [OK] Added tab switching JavaScript")
    print("\nNext steps:")
    print("  1. Restart your uvicorn server")
    print("  2. Refresh the attendance page")
    print("  3. You should see Dashboard and Upload tabs")
    print("  4. Dashboard is the default view")
    print("="*70)

    return True


if __name__ == "__main__":
    try:
        migrate_template()
    except Exception as e:
        print(f"\n[ERROR] Migration failed: {e}")
        import traceback
        traceback.print_exc()
