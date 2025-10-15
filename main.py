import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import sys
import os
import subprocess
import stat
import shutil
import hashlib

def is_container() -> bool:
    return os.path.exists("/.dockerenv") or os.path.exists("/var/run/secrets/kubernetes.io")

def is_ci_environment() -> bool:
    """Check if running in a CI environment"""
    ci_indicators = [
        'CI', 'CONTINUOUS_INTEGRATION', 'GITHUB_ACTIONS', 
        'JENKINS_URL', 'TRAVIS', 'CIRCLECI', 'GITLAB_CI'
    ]
    return any(os.getenv(indicator) for indicator in ci_indicators)

def remove_readonly(func, path, _):
    """Clear the readonly bit and reattempt the removal"""
    os.chmod(path, stat.S_IWRITE)
    func(path)

def safe_remove(path, is_dir=None):
    """
    Safely remove a file or directory.
    Clear the read-only bit on Windows.
    
    Args:
        path: Path to file or directory to remove
        is_dir: True for directory, False for file, None to auto-detect
        
    Returns:
        bool: True if successfully removed, False otherwise
    """
    if not os.path.exists(path):
        return True  # Already gone, consider it success
    
    if is_dir is None: # Auto-detect type if not specified
        is_dir = os.path.isdir(path)
    
    try:
        if is_dir:
            if sys.platform == "win32":
                shutil.rmtree(path, onexc=remove_readonly)
            else:
                shutil.rmtree(path)
        else:
            try:
                os.remove(path)
            except PermissionError:
                if sys.platform == "win32":
                    os.chmod(path, stat.S_IWRITE)
                    os.remove(path)
                else:
                    raise  # Re-raise on non-Windows platforms
        
        return True
        
    except PermissionError:
        print(f"Warning: Access Denied. Could not remove '{path}'.\nCheck permissions or if {'directory' if is_dir else 'file'} is in use.")
    except OSError as e:
        print(f"Warning: Could not remove '{path}': {e}")
    
    return False

def calculate_file_hash(filepath):
    """Calculate SHA256 hash of a file."""
    if not os.path.exists(filepath):
        return None
    
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return None

def check_and_install_requirements():
    """Check requirements and install missing ones from requirements.txt"""
    if not os.path.exists("requirements.txt"):
        print("No requirements.txt found")
        return False
        
    # Read requirements
    with open("requirements.txt", "r") as f:
        requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    
    print(f"Checking {len(requirements)} requirements...")
    
    missing_packages = []
    
    # Test each requirement
    for requirement in requirements:
        package_name = requirement.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].split("!=")[0]
        
        try:
            if package_name == "discord.py":
                import discord
            elif package_name == "aiohttp-socks":
                import aiohttp_socks
            elif package_name == "python-dotenv":
                import dotenv
            elif package_name == "python-bidi":
                import bidi
            elif package_name == "arabic-reshaper":
                import arabic_reshaper
            elif package_name.lower() == "pillow":
                import PIL
            elif package_name.lower() == "numpy":
                import numpy
            else:
                __import__(package_name)
                        
        except ImportError:
            print(f"✗ {package_name} - MISSING")
            missing_packages.append(requirement)
    
    if missing_packages: # Install missing packages
        print(f"Installing {len(missing_packages)} missing packages...")
        
        for package in missing_packages:
            try:
                cmd = [sys.executable, "-m", "pip", "install", package, "--no-cache-dir"]
                
                subprocess.check_call(cmd, timeout=1200, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"✓ {package} installed successfully")
                
            except Exception as e:
                print(f"✗ Failed to install {package}: {e}")
                return False
    
    print("✓ All requirements satisfied")
    return True

# Apply SSL context patch for better certificate handling
try:
    import ssl
    import certifi

    def _create_ssl_context_with_certifi():
        return ssl.create_default_context(cafile=certifi.where())
    
    original_create_default_https_context = getattr(ssl, "_create_default_https_context", None)

    if original_create_default_https_context is None or \
       original_create_default_https_context is ssl.create_default_context:
        ssl._create_default_https_context = _create_ssl_context_with_certifi
        print("✓ Applied SSL context patch using certifi for default HTTPS connections.")
    else: # Assume if it's already patched, it's for a good reason, just log it.
        print("SSL default HTTPS context seems to be already modified. Skipping certifi patch.")
except ImportError:
    print("Certifi library not found. SSL certificate verification might fail until it's installed.")
except Exception as e:
    print(f"Error applying SSL context patch: {e}")

if __name__ == "__main__":
    check_and_install_requirements()
    
    import discord
    from discord.ext import commands
    import sqlite3
    from colorama import Fore, Style, init
    
    # Colorama shortcuts
    F = Fore
    R = Style.RESET_ALL
    import requests
    import asyncio
    import shutil
    import zipfile
    from datetime import datetime

    # Migration function to detect old system and migrate to new if no version file exists
    def is_legacy_version():
        """Check if this is the old autoupdateinfo.txt based system"""
        return not os.path.exists("version")

    def migrate_from_legacy():
        """Migrate from old system to new GitHub release system"""
        print(Fore.YELLOW + "Detected legacy update system. Migrating to new GitHub release system..." + Style.RESET_ALL)
        
        current_version = "v1.0.0"
        
        # Create the new version file first
        with open("version", "w") as f:
            f.write(current_version)
        
        # Create a migration flag to indicate we should auto-update after restart
        with open(".migration_update", "w") as f:
            f.write("1")
        
        # Clean up old autoupdateinfo file
        if os.path.exists("autoupdateinfo.txt"):
            if safe_remove("autoupdateinfo.txt", is_dir=False):
                print("Removed legacy autoupdateinfo.txt")
            else:
                print("Warning: Could not remove autoupdateinfo.txt")
        
        print(Fore.GREEN + f"Migration completed. Now using GitHub release system (current version: {current_version})." + Style.RESET_ALL)

    # Configuration for update sources
    UPDATE_SOURCES = [
        {
            "name": "GitHub",
            "api_url": "https://api.github.com/repos/kingshot-project/Kingshot-Discord-Bot/releases/latest",
            "primary": True
        }
    ]

    def get_latest_release_info(beta_mode=False):
        """Get latest release info from GitHub"""
        for source in UPDATE_SOURCES:
            try:
                print(f"Checking for updates from {source['name']}...")
                
                if source['name'] == "GitHub":
                    if beta_mode:
                        # Get latest commit from main branch
                        repo_name = source['api_url'].split('/repos/')[1].split('/releases')[0]
                        branch_url = f"https://api.github.com/repos/{repo_name}/branches/main"
                        response = requests.get(branch_url, timeout=30)
                        if response.status_code == 200:
                            data = response.json()
                            commit_sha = data['commit']['sha'][:7]  # Short SHA
                            return {
                                "tag_name": f"beta-{commit_sha}",
                                "body": f"Latest development version from main branch (commit: {commit_sha})",
                                "download_url": f"https://github.com/{repo_name}/archive/refs/heads/main.zip",
                                "source": f"{source['name']} (Beta)"
                            }
                    else:
                        response = requests.get(source['api_url'], timeout=30)
                        if response.status_code == 200:
                            data = response.json()
                            # Using GitHub's automatic source archive
                            repo_name = source['api_url'].split('/repos/')[1].split('/releases')[0]
                            download_url = f"https://github.com/{repo_name}/archive/refs/tags/{data['tag_name']}.zip"
                            return {
                                "tag_name": data["tag_name"],
                                "body": data["body"],
                                "download_url": download_url,
                                "source": source['name']
                            }
                
            except requests.exceptions.RequestException as e:
                if hasattr(e, 'response') and e.response is not None:
                    if e.response.status_code == 404:
                        print(f"{source['name']} repository not found or no releases available")
                    elif e.response.status_code in [403, 429]:
                        print(f"{source['name']} access limited (rate limit or access denied)")
                    else:
                        print(f"{source['name']} returned HTTP {e.response.status_code}")
                else:
                    print(f"{source['name']} connection failed")
                continue
            except Exception as e:
                print(f"Failed to check {source['name']}: {e}")
                continue
        
        print("All update sources failed")
        return None

    def restart_bot(for_update=False):
        print(Fore.YELLOW + "\nRestarting bot..." + Style.RESET_ALL)
        python = sys.executable
        if for_update: # Add --autoupdate when restarting for an update
            args = [python] + sys.argv + ["--autoupdate"]
        else:
            args = [python] + sys.argv
        os.execl(python, *args)

    def setup_version_table():
        try:
            with sqlite3.connect('db/settings.sqlite') as conn:
                cursor = conn.cursor()
                cursor.execute('''CREATE TABLE IF NOT EXISTS versions (
                    file_name TEXT PRIMARY KEY,
                    version TEXT,
                    is_main INTEGER DEFAULT 0
                )''')
                conn.commit()
                print(F.GREEN + "Version table created successfully." + R)
        except Exception as e:
            print(F.RED + f"Error creating version table: {e}" + R)


    async def check_and_update_files():
        """Update system using GitHub releases with beta support"""
        beta_mode = "--beta" in sys.argv
        try: # Check if we need to migrate from legacy system
            if is_legacy_version():
                migrate_from_legacy()

            release_info = get_latest_release_info(beta_mode=beta_mode)
            
            if release_info:
                latest_tag = release_info["tag_name"]
                source_name = release_info["source"]
                
                if os.path.exists("version"):
                    with open("version", "r") as f:
                        current_version = f.read().strip()
                    if beta_mode:
                        print(F.YELLOW + f"Beta mode: Comparing latest commit from main branch" + R)
                else:
                    current_version = "v1.0.0"
                    if beta_mode:
                        print(F.YELLOW + f"Beta mode: Comparing latest commit from main branch" + R)

                print(F.CYAN + f"Current version: {current_version}" + R)
                            
                if current_version != latest_tag:
                    print(F.YELLOW + f"New version available: {latest_tag} (from {source_name})" + R)
                    print("Update Notes:")
                    print(release_info["body"])
                    print()
                    
                    update = False
                    
                    if is_container():
                        print(F.YELLOW + "Running in a container. Skipping update prompt." + R)
                        update = True
                    elif "--autoupdate" in sys.argv:
                        print(F.GREEN + "Auto-update enabled, proceeding with update..." + R)
                        update = True
                    else:
                        print("Note: You can use the --autoupdate argument to skip this prompt in the future.")
                        response = input("Do you want to update now? (y/n): ").lower()
                        update = response == 'y'
                    
                    if update:
                        # Backup database if it exists
                        if os.path.exists("db") and os.path.isdir("db"):
                            print(F.YELLOW + "Making backup of database..." + R)
                            
                            db_bak_path = "db.bak"
                            if os.path.exists(db_bak_path) and os.path.isdir(db_bak_path):
                                if not safe_remove(db_bak_path): # Create a timestamped backup to avoid upgrading without first having a backup
                                    db_bak_path = f"db.bak_{int(datetime.now().timestamp())}"
                                    print(F.YELLOW + f"WARNING: Couldn't remove db.bak folder. Making backup with timestamp instead." + R)

                            try:
                                shutil.copytree("db", db_bak_path)
                                print(F.GREEN + f"Backup completed: db → {db_bak_path}" + R)
                            except Exception as e:
                                print(F.RED + f"WARNING: Failed to create database backup: {e}" + R)
                                                
                        download_url = release_info["download_url"]
                        print(F.YELLOW + f"Downloading update from {source_name}..." + R)
                        safe_remove("package.zip", is_dir=False)
                        download_resp = requests.get(download_url, timeout=600)
                        
                        if download_resp.status_code == 200:
                            with open("package.zip", "wb") as f:
                                f.write(download_resp.content)
                            
                            if os.path.exists("update") and os.path.isdir("update"):
                                if not safe_remove("update"):
                                    print(F.RED + "WARNING: Could not remove previous update directory" + R)
                                    return
                                
                            try:
                                with zipfile.ZipFile("package.zip", 'r') as zip_ref:
                                    zip_ref.extractall("update")
                            except Exception as e:
                                print(F.RED + f"ERROR: Failed to extract update package: {e}" + R)
                                return
                                
                            safe_remove("package.zip", is_dir=False)
                            
                            # Find the extracted directory (GitHub archives create a subdirectory)
                            update_dir = "update"
                            extracted_items = os.listdir(update_dir)
                            if len(extracted_items) == 1 and os.path.isdir(os.path.join(update_dir, extracted_items[0])):
                                update_dir = os.path.join(update_dir, extracted_items[0])
                            
                            # Handle main.py update
                            main_py_path = os.path.join(update_dir, "main.py")
                            if os.path.exists(main_py_path):
                                safe_remove("main.py.bak", is_dir=False)
                                    
                                try:
                                    if os.path.exists("main.py"):
                                        os.rename("main.py", "main.py.bak")
                                except Exception as e:
                                    print(F.YELLOW + f"Could not backup main.py: {e}" + R)
                                    if os.path.exists("main.py"):
                                        if safe_remove("main.py", is_dir=False):
                                            print(F.YELLOW + "Removed current main.py" + R)
                                        else:
                                            print(F.RED + "Warning: Could not backup or remove current main.py" + R)
                                
                                try:
                                    shutil.copy2(main_py_path, "main.py")
                                except Exception as e:
                                    print(F.RED + f"ERROR: Could not install new main.py: {e}" + R)
                                    return
                            
                            # Copy other files
                            for root, _, files in os.walk(update_dir):
                                for file in files:
                                    if file == "main.py":
                                        continue
                                        
                                    src_path = os.path.join(root, file)
                                    rel_path = os.path.relpath(src_path, update_dir)
                                    dst_path = os.path.join(".", rel_path)
                                    
                                    # Skip certain files that shouldn't be overwritten
                                    if file in ["bot_token.txt", "version", "autoupdateinfo.txt"] or dst_path.startswith("db/") or dst_path.startswith("db\\"):
                                        continue
                                    
                                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)

                                    # Only backup important files
                                    should_backup = any([
                                        dst_path.startswith("cogs/"),
                                        file.endswith(".py")
                                    ])
                                    
                                    # Skip backing up standard project files
                                    if file in ["README.md", "requirements.txt"]:
                                        should_backup = False
                                    
                                    if os.path.exists(dst_path) and should_backup:
                                        backup_path = f"{dst_path}.bak"
                                        safe_remove(backup_path)
                                        try:
                                            os.rename(dst_path, backup_path)
                                        except Exception as e:
                                            print(F.YELLOW + f"Could not create backup of {dst_path}: {e}" + R)
                                    elif os.path.exists(dst_path): # For standard files, just overwrite without backup
                                        safe_remove(dst_path, is_dir=False)
                                            
                                    try:
                                        shutil.copy2(src_path, dst_path)
                                    except Exception as e:
                                        print(F.RED + f"Failed to copy {file} to {dst_path}: {e}" + R)
                            
                            if not safe_remove("update"):
                                print(F.RED + "WARNING: update folder could not be removed. You may want to remove it manually." + R)
                            
                            # Update version file
                            with open("version", "w") as f:
                                f.write(latest_tag)
                            
                            print(F.GREEN + f"Update completed successfully from {source_name}." + R)
                            restart_bot(for_update=True)
                        else:
                            print(F.RED + f"Failed to download the update from {source_name}. HTTP status: {download_resp.status_code}" + R)
                            return  
                else:
                    print(F.GREEN + "Bot is up to date!" + R)
            else:
                print(F.RED + "Failed to fetch latest release info" + R)
                
        except Exception as e:
            print(F.RED + f"Error during update check: {e}" + R)

    # Initialize colorama and setup
    init(autoreset=True)
    
    # Setup database folder and connections
    if not os.path.exists("db"):
        os.makedirs("db")
        print(F.GREEN + "db folder created" + R)

    setup_version_table()
    
    # Create version file if it doesn't exist
    if not os.path.exists("version"):
        with open("version", "w") as f:
            f.write("v1.0.0")
        print(F.GREEN + "Created version file (v1.0.0)" + R)

    # Check for mutually exclusive flags
    mutually_exclusive_flags = ["--autoupdate", "--no-update"]
    active_flags = [flag for flag in mutually_exclusive_flags if flag in sys.argv]
    
    if len(active_flags) > 1:
        print(F.RED + f"Error: {' and '.join(active_flags)} flags are mutually exclusive." + R)
        print("Use --autoupdate to automatically install updates without prompting.")
        print("Use --no-update to skip all update checks.")
        print("Use --beta to get latest development version from main branch.")
        sys.exit(1)
    
    # Run update check unless --no-update flag is present
    if "--no-update" not in sys.argv:
        asyncio.run(check_and_update_files())
    else:
        print(F.YELLOW + "Update check skipped due to --no-update flag." + R)
            
    import discord
    from discord.ext import commands

    class CustomBot(commands.Bot):
        async def on_error(self, event_name, *args, **kwargs):
            if event_name == "on_interaction":
                error = sys.exc_info()[1]
                if isinstance(error, discord.NotFound) and error.code == 10062:
                    return
            
            await super().on_error(event_name, *args, **kwargs)

        async def on_command_error(self, ctx, error):
            if isinstance(error, discord.NotFound) and error.code == 10062:
                return
            await super().on_command_error(ctx, error)

    intents = discord.Intents.default()
    intents.message_content = True

    bot = CustomBot(command_prefix="/", intents=intents)

    # Token handling
    token_file = "bot_token.txt"
    if not os.path.exists(token_file):
        bot_token = input("Enter the bot token: ")
        with open(token_file, "w") as f:
            f.write(bot_token)
    else:
        with open(token_file, "r") as f:
            bot_token = f.read().strip()

    # Database setup
    databases = {
        "conn_alliance": "db/alliance.sqlite",
        "conn_giftcode": "db/giftcode.sqlite",
        "conn_changes": "db/changes.sqlite",
        "conn_users": "db/users.sqlite",
        "conn_settings": "db/settings.sqlite",
    }

    connections = {name: sqlite3.connect(path) for name, path in databases.items()}
    print(F.GREEN + "Database connections have been successfully established." + R)

    def create_tables():
        with connections["conn_changes"] as conn_changes:
            conn_changes.execute("""CREATE TABLE IF NOT EXISTS nickname_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                fid INTEGER, 
                old_nickname TEXT, 
                new_nickname TEXT, 
                change_date TEXT
            )""")
            
            conn_changes.execute("""CREATE TABLE IF NOT EXISTS furnace_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                fid INTEGER, 
                old_furnace_lv INTEGER, 
                new_furnace_lv INTEGER, 
                change_date TEXT
            )""")

        with connections["conn_settings"] as conn_settings:
            conn_settings.execute("""CREATE TABLE IF NOT EXISTS botsettings (
                id INTEGER PRIMARY KEY, 
                channelid INTEGER, 
                giftcodestatus TEXT 
            )""")
            
            conn_settings.execute("""CREATE TABLE IF NOT EXISTS admin (
                id INTEGER PRIMARY KEY, 
                is_initial INTEGER
            )""")

        with connections["conn_users"] as conn_users:
            conn_users.execute("""CREATE TABLE IF NOT EXISTS users (
                fid INTEGER PRIMARY KEY, 
                nickname TEXT, 
                furnace_lv INTEGER DEFAULT 0, 
                kid INTEGER, 
                stove_lv_content TEXT, 
                alliance TEXT
            )""")

        with connections["conn_giftcode"] as conn_giftcode:
            conn_giftcode.execute("""CREATE TABLE IF NOT EXISTS gift_codes (
                giftcode TEXT PRIMARY KEY, 
                date TEXT
            )""")
            
            conn_giftcode.execute("""CREATE TABLE IF NOT EXISTS user_giftcodes (
                fid INTEGER, 
                giftcode TEXT, 
                status TEXT, 
                PRIMARY KEY (fid, giftcode),
                FOREIGN KEY (giftcode) REFERENCES gift_codes (giftcode)
            )""")

        with connections["conn_alliance"] as conn_alliance:
            conn_alliance.execute("""CREATE TABLE IF NOT EXISTS alliancesettings (
                alliance_id INTEGER PRIMARY KEY, 
                channel_id INTEGER, 
                interval INTEGER
            )""")
            
            conn_alliance.execute("""CREATE TABLE IF NOT EXISTS alliance_list (
                alliance_id INTEGER PRIMARY KEY, 
                name TEXT
            )""")

        print(F.GREEN + "All tables checked." + R)

    create_tables()

    async def load_cogs():
        cogs = ["control", "alliance", "alliance_member_operations", "bot_operations", "logsystem", "support_operations", "gift_operations", "changes", "w", "wel", "other_features", "bear_trap", "id_channel", "backup_operations", "bear_trap_editor", "attendance_report", "attendance", "minister_menu", "minister_schedule"]
        
        failed_cogs = []
        
        for cog in cogs:
            try:
                await bot.load_extension(f"cogs.{cog}")
            except Exception as e:
                print(f"✗ Failed to load cog {cog}: {e}")
                failed_cogs.append(cog)
        
        if failed_cogs:
            print(F.RED + f"\n⚠️  {len(failed_cogs)} cog(s) failed to load:" + R)
            for cog in failed_cogs:
                print(F.YELLOW + f"   • {cog}" + R)
            print(F.YELLOW + "\nThe bot will continue with reduced functionality." + R)
            print(F.YELLOW + "To fix missing or corrupted files, try using the --repair flag.\n" + R)

    @bot.event
    async def on_ready():
        try:
            print(f"{F.GREEN}Logged in as {F.CYAN}{bot.user}{R}")
            await bot.tree.sync()
        except Exception as e:
            print(f"Error syncing commands: {e}")

    async def main():
        await load_cogs()
        await bot.start(bot_token)

    if __name__ == "__main__":
        asyncio.run(main())
